
import os
from typing import TypedDict

import httpx
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

from app.schemas import IncidentInput, RCAResult


PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "http://localhost:9090",
)

MODEL_NAME = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b",
)
llm = ChatGroq(
    model=MODEL_NAME,
    temperature=0,
).with_structured_output(RCAResult)

class AgentState(TypedDict, total=False):
    incident: dict
    evidence: dict
    result: dict


def query_prometheus(query: str) -> dict:
    """Execute a read-only instant query against Prometheus."""
    try:
        response = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "success":
            return {"error": "Prometheus query was unsuccessful"}

        return {"data": payload["data"]["result"]}

    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"error": f"Prometheus query failed: {type(exc).__name__}"}


def scalar_value(query_result: dict) -> float | None:
    """Extract one numeric value from a successful instant query."""
    if "error" in query_result:
        return None

    rows = query_result.get("data", [])
    if not rows:
        return None

    try:
        value = float(rows[0]["value"][1])
        return value if value == value and abs(value) != float("inf") else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def collect_evidence(state: AgentState) -> dict:
    incident = state["incident"]

    error_ratio_query = (
        'sum(rate(mock_http_requests_total'
        '{path="/api/orders",status=~"5.."}[30s]))'
        ' / clamp_min(sum(rate(mock_http_requests_total'
        '{path="/api/orders"}[30s])), 0.001)'
    )

    request_rate_query = (
        'sum(rate(mock_http_requests_total'
        '{path="/api/orders"}[30s]))'
    )

    error_rate = query_prometheus(error_ratio_query)
    request_rate = query_prometheus(request_rate_query)
    firing_alert = query_prometheus(
        'ALERTS{alertname="HighErrorRate",alertstate="firing"}'
    )
    failed_requests = query_prometheus(
        'mock_http_requests_total'
        '{path="/api/orders",status="503"}'
    )

    ratio = scalar_value(error_rate)
    requests_per_second = scalar_value(request_rate)
    alert_is_firing = (
        "error" not in firing_alert
        and bool(firing_alert.get("data"))
    )

    # Deterministic safety gate: historical counters alone are insufficient.
    query_results = [
        error_rate,
        request_rate,
        firing_alert,
    ]
    queries_succeeded = all("error" not in item for item in query_results)

    remediation_eligible = (
        queries_succeeded
        and alert_is_firing
        and ratio is not None
        and ratio > 0.3
        and requests_per_second is not None
        and requests_per_second > 0
    )

    evidence = {
        "error_rate_ratio_query": error_rate,
        "request_rate_query": request_rate,
        "high_error_alert": firing_alert,
        "http_503_counter": failed_requests,
        "alert_annotations": incident.get("annotations", {}),
        "alert_labels": incident.get("labels", {}),
        "current_error_rate_ratio": ratio,
        "current_request_rate_per_second": requests_per_second,
        "high_error_alert_firing": alert_is_firing,
        "remediation_eligible": remediation_eligible,
    }

    return {"evidence": evidence}


def analyze_incident(state: AgentState) -> dict:
    incident = state["incident"]
    evidence = state["evidence"]

    prompt = f"""
Analyze this incident using only the supplied evidence.

INCIDENT:
{incident}

PROMETHEUS EVIDENCE:
{evidence}

Rules:
- Separate measured facts from hypotheses.
- Historical counters prove past failures, not a current fault.
- Missing metrics are unknown, not zero.
- Never invent logs, deployments, dependencies, or events.
- Explain evidence gaps explicitly.
- Recommend set_fault_normal only when remediation_eligible is true.
- If current evidence is missing or remediation_eligible is false,
  recommend no_action.
- Never claim an action has already executed.
- Never propose arbitrary shell commands or code.
- Confidence must reflect the quality of current evidence.
- requires_approval must always be true.
"""

    result = llm.invoke(prompt)

    if isinstance(result, RCAResult):
        parsed = result
    else:
        parsed = RCAResult.model_validate(result)

    # The model cannot override this deterministic safety gate.
    if (
        not evidence["remediation_eligible"]
        and parsed.proposed_action == "set_fault_normal"
    ):
        parsed.proposed_action = "no_action"
        parsed.action_reason = (
            "Remediation blocked: current Prometheus evidence does not "
            "confirm a firing HighErrorRate alert, an error ratio above "
            "0.3, and a positive request rate. Historical counters alone "
            "are insufficient."
        )
        parsed.evidence_gaps = list(parsed.evidence_gaps) + [
            "Deterministic remediation eligibility check failed."
        ]

    parsed.incident_id = incident["incident_id"]
    parsed.requires_approval = True

    return {"result": parsed.model_dump()}


builder = StateGraph(AgentState)
builder.add_node("collect_evidence", collect_evidence)
builder.add_node("analyze_incident", analyze_incident)

builder.add_edge(START, "collect_evidence")
builder.add_edge("collect_evidence", "analyze_incident")
builder.add_edge("analyze_incident", END)

graph = builder.compile()


def run_analysis(incident: IncidentInput) -> RCAResult:
    output = graph.invoke({"incident": incident.model_dump()})
    return RCAResult.model_validate(output["result"])
