
import json
import math
import os
from typing import TypedDict

import httpx
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

from app.schemas import IncidentInput, RCAResult


PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL", "http://localhost:9090"
).rstrip("/")

MOCK_SERVICE_URL = os.getenv(
    "MOCK_SERVICE_URL", "http://localhost:8000"
).rstrip("/")

MODEL_NAME = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-120b"
)

llm = ChatGroq(model=MODEL_NAME, temperature=0)

MAX_EVIDENCE_ITEM_CHARS = 1800
MAX_FINAL_EVIDENCE_CHARS = 9000


class AgentState(TypedDict, total=False):
    incident: dict
    evidence: dict
    result: dict


def get_json(url: str, params: dict | None = None) -> dict:
    """Make a bounded-time, read-only GET request."""
    try:
        response = httpx.get(url, params=params, timeout=5.0)
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            return {"error": "Unexpected response format"}

        return payload
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": f"Request failed: {type(exc).__name__}"}


@tool
def query_prometheus(query: str) -> dict:
    """Run a read-only Prometheus instant query."""
    if not query.strip() or len(query) > 1000:
        return {"error": "Query must contain 1-1000 characters"}

    result = get_json(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
    )

    if result.get("status") != "success":
        return result if "error" in result else {
            "error": "Prometheus query was unsuccessful"
        }

    data = result.get("data")
    if not isinstance(data, dict):
        return {"error": "Unexpected Prometheus data format"}

    rows = data.get("result")
    if not isinstance(rows, list):
        return {"error": "Unexpected Prometheus result format"}

    return {"data": rows}


@tool
def get_service_health() -> dict:
    """Read service health, current fault mode, and release."""
    return get_json(f"{MOCK_SERVICE_URL}/health")


@tool
def get_recent_events(limit: int = 30) -> dict:
    """Read recent diagnostic events. Limit must be between 1 and 50."""
    if not 1 <= limit <= 50:
        return {"error": "limit must be between 1 and 50"}

    result = get_json(
        f"{MOCK_SERVICE_URL}/diagnostics/events",
        params={"limit": limit},
    )

    events = result.get("events")
    if isinstance(events, list):
        result["events"] = events[-limit:]

    return result


@tool
def get_recent_changes(limit: int = 20) -> dict:
    """Read recent simulated deployments and fault-mode changes."""
    if not 1 <= limit <= 50:
        return {"error": "limit must be between 1 and 50"}

    result = get_json(f"{MOCK_SERVICE_URL}/admin/changes")
    changes = result.get("changes")

    if isinstance(changes, list):
        result["changes"] = changes[:limit]

    return result


INVESTIGATION_TOOLS = [
    query_prometheus,
    get_service_health,
    get_recent_events,
    get_recent_changes,
]


def scalar_value(query_result: dict) -> float | None:
    """Extract one finite numeric value from a Prometheus result."""
    if not isinstance(query_result, dict) or "error" in query_result:
        return None

    rows = query_result.get("data", [])
    if not isinstance(rows, list) or not rows:
        return None

    try:
        value = float(rows[0]["value"][1])
        return value if math.isfinite(value) else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def compact(value, max_chars: int = MAX_EVIDENCE_ITEM_CHARS):
    """Serialize evidence without sending arbitrarily large results to Groq."""
    serialized = json.dumps(value, default=str)
    if len(serialized) > max_chars:
        return serialized[:max_chars] + "... [truncated]"
    return serialized


def collect_evidence(state: AgentState) -> dict:
    """Collect evidence deterministically; no LLM tool-selection loop."""
    incident = state["incident"]
    labels = incident.get("labels") or {}

    if not isinstance(labels, dict):
        labels = {}

    nested_alert = incident.get("alert") or {}
    if not isinstance(nested_alert, dict):
        nested_alert = {}

    nested_labels = nested_alert.get("labels") or {}
    if not isinstance(nested_labels, dict):
        nested_labels = {}

    alert_name = (
        incident.get("alert_name")
        or incident.get("alertname")
        or labels.get("alertname")
        or nested_labels.get("alertname")
        or incident.get("name")
        or "Unknown"
    )

    # Only known alert names may drive PromQL label selectors.
    supported_alerts = {
        "HighErrorRate",
        "DeploymentRegression",
        "PaymentDependencySlow",
        "PaymentDependencyTimeouts",
    }

    if alert_name not in supported_alerts:
        alert_name = "Unknown"

    tool_results = []
    collection_errors = []

    def call_tool(tool_fn, arguments: dict, name: str):
        try:
            result = tool_fn.invoke(arguments)

            if not isinstance(result, dict):
                result = {"error": "Unexpected tool response format"}

            if result.get("error"):
                collection_errors.append(name)

            # Keep list-valued diagnostic context compact.
            for key in ("events", "changes"):
                value = result.get(key)
                if isinstance(value, list):
                    result[key] = value[-5:]

            tool_results.append({
                "tool": name,
                "result": compact(result),
            })
            return result

        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:150]}"
            collection_errors.append(name)
            tool_results.append({"tool": name, "error": error})
            return {"error": error}

    def run_query(name: str, query: str) -> dict:
        return call_tool(query_prometheus, {"query": query}, name)

    # Always collect core context, even when a metric query fails.
    health = call_tool(get_service_health, {}, "service_health")
    call_tool(
        get_recent_events, {"limit": 5}, "recent_events"
    )
    call_tool(
        get_recent_changes, {"limit": 5}, "recent_changes"
    )

    # Verify the alert is firing in Prometheus.
    firing_result = run_query(
        "alert_firing",
        f'ALERTS{{alertname="{alert_name}",alertstate="firing"}}',
    )

    firing_rows = firing_result.get("data", [])
    alert_query_succeeded = (
        isinstance(firing_result, dict)
        and "error" not in firing_result
        and isinstance(firing_rows, list)
    )
    alert_is_firing = (
        alert_query_succeeded and len(firing_rows) > 0
    )

    evidence = {
        "investigation_mode": "deterministic",
        "alert_name": alert_name,
        "alert_is_firing": alert_is_firing,
        "tool_results": tool_results,
    }

    eligible_action = "no_action"
    gate = None

    health_ok = (
        isinstance(health, dict)
        and "error" not in health
        and health.get("status") == "ok"
    )

    if alert_name == "HighErrorRate":
        error_ratio_result = run_query(
            "orders_error_ratio",
            'sum(rate(mock_http_requests_total'
            '{path="/api/orders",status=~"5.."}[30s])) '
            '/ sum(rate(mock_http_requests_total'
            '{path="/api/orders"}[30s]))',
        )
        request_rate_result = run_query(
            "orders_request_rate",
            'sum(rate(mock_http_requests_total'
            '{path="/api/orders"}[30s]))',
        )

        error_ratio = scalar_value(error_ratio_result)
        request_rate = scalar_value(request_rate_result)

        queries_ok = (
            error_ratio is not None and request_rate is not None
        )

        gate = {
            "alert_query_succeeded": alert_query_succeeded,
            "alert_is_firing": alert_is_firing,
            "error_ratio": error_ratio,
            "request_rate": request_rate,
            "health_ok": health_ok,
            "queries_succeeded": queries_ok,
        }

        if (
            queries_ok
            and alert_is_firing
            and error_ratio > 0.3
            and request_rate > 0
            and health_ok
        ):
            eligible_action = "reset_injected_fault"

    elif alert_name == "DeploymentRegression":
        # A counter can exist even when rate() has insufficient
        # samples. Use the raw counter as corroborating evidence;
        # the firing alert remains mandatory.
        error_counter_result = run_query(
            "regression_5xx_counter",
            'sum(mock_http_requests_total'
            '{path="/api/orders",status=~"5..",'
            'release="release-regression"})',
        )

        error_counter = scalar_value(error_counter_result)

        current_release = health.get("release") if health_ok else None
        release_matches = current_release == "release-regression"

        queries_ok = error_counter is not None

        gate = {
            "alert_query_succeeded": alert_query_succeeded,
            "alert_is_firing": alert_is_firing,
            "regression_5xx_counter": error_counter,
            "health_ok": health_ok,
            "current_release": current_release,
            "release_matches": release_matches,
            "queries_succeeded": queries_ok,
        }

        if (
            queries_ok
            and error_counter > 0
            and alert_is_firing
            and health_ok
            and release_matches
        ):
            eligible_action = "rollback_release"

    elif alert_name == "PaymentDependencySlow":
        run_query(
            "payment_dependency_average_latency",
            "sum(rate(mock_payment_dependency_duration_seconds_sum[30s])) "
            "/ sum(rate(mock_payment_dependency_duration_seconds_count[30s]))",
        )

    elif alert_name == "PaymentDependencyTimeouts":
        run_query(
            "payment_dependency_timeout_ratio",
            'sum(rate(mock_payment_dependency_requests_total'
            '{status="504"}[30s])) '
            '/ sum(rate(mock_payment_dependency_requests_total[30s]))',
        )

    evidence["investigation_tools_called"] = len(tool_results)
    evidence["evidence_collection_errors"] = collection_errors
    evidence["remediation_gate"] = gate
    evidence["remediation_eligible"] = (
        eligible_action != "no_action"
    )
    evidence["eligible_action"] = eligible_action
    evidence["eligibility_reason"] = (
        f"Deterministic checks passed for {eligible_action}; "
        "human approval is still required."
        if eligible_action != "no_action"
        else "No supported remediation gate passed. "
             "No remediation is eligible."
    )

    return {"evidence": evidence}


def analyze_incident(state: AgentState) -> dict:
    incident = state["incident"]
    evidence = state["evidence"]

    # Apply a final aggregate bound as well as per-result bounds.
    bounded_evidence = dict(evidence)
    bounded_evidence["tool_results"] = evidence.get("tool_results", [])
    serialized_evidence = compact(
        bounded_evidence, MAX_FINAL_EVIDENCE_CHARS
    )

    analysis_llm = llm.with_structured_output(RCAResult)

    prompt = f"""
You are analyzing a simulated production incident.

Use only the incident and investigation evidence below.
Separate observed facts from hypotheses. State uncertainty when
evidence is missing. Do not invent metrics, events, or changes.

INCIDENT:
{compact(incident, 2000)}

INVESTIGATION EVIDENCE:
{serialized_evidence}

ACTION SAFETY RULES:
- Allowed actions: reset_injected_fault, rollback_release, no_action.
- The only eligible action is the exact eligible_action in the evidence.
- Never recommend a different action.
- Never claim an action has already executed.
- requires_approval must be true.
- If evidence is insufficient, explain the evidence gaps.
"""

    parsed = analysis_llm.invoke(prompt)

    if not isinstance(parsed, RCAResult):
        parsed = RCAResult.model_validate(parsed)

    allowed_actions = {
        "reset_injected_fault",
        "rollback_release",
        "no_action",
    }
    eligible_action = evidence.get("eligible_action", "no_action")

    # Fail closed if evidence is malformed or an action is unexpected.
    if eligible_action not in allowed_actions:
        eligible_action = "no_action"

    if parsed.proposed_action != eligible_action:
        parsed.proposed_action = "no_action"
        parsed.action_reason = (
            "The proposed action did not match the deterministic "
            "eligibility decision."
        )
        parsed.evidence_gaps = list(parsed.evidence_gaps) + [
            "The proposed action was blocked by the eligibility guard."
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
