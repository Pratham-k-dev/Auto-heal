
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from psycopg.types.json import Jsonb

from app.database import get_connection

logger = logging.getLogger(__name__)
# Keeps startup-recovery tasks referenced until they finish.
_automation_tasks = set()

router = APIRouter(prefix="/incidents", tags=["incidents"])

ACTION_ALERTS = {
    "reset_injected_fault": {"HighErrorRate"},
    "rollback_release": {"DeploymentRegression"},
    "enable_payment_fallback": {
        "PaymentDependencySlow",
        "PaymentDependencyTimeouts",
    },
}


def record_event(conn, incident_id: int, event_type: str, details: dict):
    conn.execute(
        """
        INSERT INTO incident_events (incident_id, event_type, details)
        VALUES (%s, %s, %s)
        """,
        (incident_id, event_type, Jsonb(details)),
    )


def _expected_alert_for_incident(action: str, labels: Any) -> str:
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except json.JSONDecodeError as exc:
            raise ValueError("Incident labels are invalid JSON") from exc

    if not isinstance(labels, dict):
        raise ValueError("Incident labels are not an object")

    alert_name = labels.get("alertname")
    if (
        not isinstance(alert_name, str)
        or alert_name not in ACTION_ALERTS.get(action, set())
    ):
        raise ValueError(
            f"Alert {alert_name!r} is not valid for remediation {action!r}"
        )

    return alert_name


def _service_state_is_valid(
    action: str,
    health: Any,
    order_status_code: int,
    order_data: Any,
    pre_action_health: Any,
) -> bool:
    if not isinstance(health, dict) or health.get("status") != "ok":
        return False
    if order_status_code != 200 or not isinstance(order_data, dict):
        return False

    if action == "reset_injected_fault":
        if not isinstance(pre_action_health, dict):
            return False
        return (
            health.get("fault_mode") == "normal"
            and "release" in pre_action_health
            and health.get("release") == pre_action_health["release"]
        )

    if action == "rollback_release":
        return (
            health.get("fault_mode") == "normal"
            and health.get("release") == "release-stable"
        )

    if action == "enable_payment_fallback":
        if not isinstance(pre_action_health, dict):
            return False
        original_mode = pre_action_health.get("fault_mode")
        return (
            original_mode in {"dependency_slow", "dependency_timeout"}
            and health.get("payment_fallback_enabled") is True
            and health.get("fault_mode") == original_mode
            and "release" in pre_action_health
            and health.get("release") == pre_action_health["release"]
            and order_data.get("payment_dependency") == "fallback"
        )

    return False


def _action_result_is_valid(
    action: str,
    result: Any,
    pre_action_health: dict,
) -> bool:
    if not isinstance(result, dict):
        return False

    if action == "reset_injected_fault":
        return (
            result.get("fault_mode") == "normal"
            and "release" in pre_action_health
            and result.get("release") == pre_action_health["release"]
            and result.get("payment_fallback_enabled") is False
        )

    if action == "rollback_release":
        return (
            result.get("fault_mode") == "normal"
            and result.get("release") == "release-stable"
        )

    if action == "enable_payment_fallback":
        original_mode = pre_action_health.get("fault_mode")
        return (
            original_mode in {"dependency_slow", "dependency_timeout"}
            and result.get("payment_fallback_enabled") is True
            and result.get("fault_mode") == original_mode
            and "release" in pre_action_health
            and result.get("release") == pre_action_health["release"]
        )

    return False


async def _query_alert_firing(
    client: httpx.AsyncClient,
    prometheus_url: str,
    expected_alert: str,
) -> bool:
    response = await client.get(
        f"{prometheus_url}/api/v1/query",
        params={
            "query": (
                f'ALERTS{{alertname="{expected_alert}",'
                'alertstate="firing"}'
            )
        },
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("result") if isinstance(data, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or not isinstance(rows, list)
    ):
        raise RuntimeError("Prometheus query returned an invalid response")
    return bool(rows)


def _persist_remediation_result(
    incident_id: int,
    remediation_id: int,
    action: str,
    status: str,
    verification: dict,
    error: str | None,
    *,
    reconciled: bool = False,
) -> dict:
    with get_connection() as conn:
        updated = conn.execute(
            """
            UPDATE remediation_actions
            SET status = %s,
                completed_at = NOW(),
                verification = %s,
                error = %s
            WHERE id = %s
              AND incident_id = %s
              AND status = 'executing'
            RETURNING *
            """,
            (
                status,
                Jsonb(verification),
                error,
                remediation_id,
                incident_id,
            ),
        ).fetchone()

        if not updated:
            raise HTTPException(
                status_code=409,
                detail="Remediation status changed during verification",
            )

        details = {
            "remediation_id": remediation_id,
            "action": action,
            "verification": verification,
            "error": error,
        }
        if reconciled:
            details["reconciled"] = True
        record_event(conn, incident_id, "remediation_" + status, details)
        return updated




def process_alert(alert: dict[str, Any]):
    """Persist an alert and indicate whether a new episode needs automation."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}

    fingerprint = alert.get("fingerprint")
    if not fingerprint:
        fingerprint = (
            f"{labels.get('alertname', 'UnknownAlert')}:"
            f"{labels.get('instance', 'unknown')}:"
            f"{labels.get('job', 'unknown')}"
        )

    title = labels.get("alertname", "Unknown alert")
    severity = labels.get("severity", "warning").lower()
    starts_at = alert.get("startsAt")
    is_resolved = alert.get("status") == "resolved"

    should_automate = False

    try:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT * FROM incidents WHERE fingerprint = %s",
                (fingerprint,),
            ).fetchone()

            if existing:
                incident_id = existing["id"]
                previous_status = existing["status"]

                # Only a resolved -> firing transition starts a new episode.
                if is_resolved:
                    new_status = "resolved"
                elif previous_status == "resolved":
                    new_status = "open"
                    should_automate = True
                else:
                    new_status = previous_status

                conn.execute(
                    """
                    UPDATE incidents
                    SET title = %s,
                        severity = %s,
                        labels = %s,
                        annotations = %s,
                        status = %s,
                        last_seen_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        title,
                        severity,
                        Jsonb(labels),
                        Jsonb(annotations),
                        new_status,
                        incident_id,
                    ),
                )

                record_event(
                    conn,
                    incident_id,
                    "alert_updated",
                    {"alert_status": alert.get("status")},
                )

            else:
                row = conn.execute(
                    """
                    INSERT INTO incidents (
                        fingerprint, title, severity, status,
                        source, labels, annotations, starts_at
                    )
                    VALUES (%s, %s, %s, %s, 'alertmanager', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        fingerprint,
                        title,
                        severity,
                        "resolved" if is_resolved else "open",
                        Jsonb(labels),
                        Jsonb(annotations),
                        starts_at,
                    ),
                ).fetchone()

                incident_id = row["id"]
                should_automate = not is_resolved

                record_event(
                    conn,
                    incident_id,
                    "incident_created",
                    {
                        "alert_status": alert.get("status"),
                        "fingerprint": fingerprint,
                    },
                )

            # Create the job in the same DB transaction as the incident.
            # Repeat notifications for an already-open incident do not
            # reset a completed or failed job.
            if should_automate:
                conn.execute(
                    """
                    INSERT INTO incident_automation_jobs (
                        incident_id, status
                    )
                    VALUES (%s, 'pending')
                    ON CONFLICT (incident_id) DO UPDATE
                    SET status = 'pending',
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE incident_automation_jobs.status <> 'running'
                    """,
                    (incident_id,),
                )

                record_event(
                    conn,
                    incident_id,
                    "automation_queued",
                    {"status": "pending"},
                )

        return incident_id, should_automate

    except Exception as exc:
        logger.exception("Failed to persist alert")
        raise RuntimeError("Failed to persist alert") from exc


async def run_automatic_workflow(incident_id: int):
    """Run RCA and create an approval request, but never approve it."""
    # Atomic claim: concurrent or duplicate tasks cannot both start.
    with get_connection() as conn:
        job = conn.execute(
            """
            UPDATE incident_automation_jobs
            SET status = 'running',
                attempts = attempts + 1,
                last_error = NULL,
                updated_at = NOW()
            WHERE incident_id = %s
              AND status = 'pending'
            RETURNING incident_id, attempts
            """,
            (incident_id,),
        ).fetchone()

        if not job:
            return

        record_event(
            conn,
            incident_id,
            "automation_started",
            {"attempt": job["attempts"]},
        )

    try:
        # Reuse your existing validated RCA endpoint logic directly.
        analysis = await analyze_incident(incident_id)

        proposed_action = analysis.get("proposed_action")
        requires_approval = analysis.get("requires_approval")
        remediation_id = None

        if (
            proposed_action in {
                "reset_injected_fault",
                "rollback_release",
                "enable_payment_fallback",
            }
            and requires_approval is True
        ):
            remediation = request_remediation(incident_id)
            remediation_id = remediation["id"]

            if remediation["rca_analysis_id"] != analysis["analysis_id"]:
                raise RuntimeError(
                    "Remediation is linked to a different RCA analysis"
                )

            if remediation["action"] != proposed_action:
                raise RuntimeError(
                    "Remediation action does not match the latest RCA"
                )

        # "completed" means the investigation and decision finished.
        # It does NOT mean the remediation has been executed.
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE incident_automation_jobs
                SET status = 'completed',
                    last_error = NULL,
                    updated_at = NOW()
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            record_event(
                conn,
                incident_id,
                "automation_completed",
                {
                    "analysis_id": analysis["analysis_id"],
                    "proposed_action": proposed_action,
                    "requires_approval": requires_approval,
                    "remediation_id": remediation_id,
                },
            )

        logger.info(
            "Automation completed for incident %s", incident_id
        )

    except Exception as exc:
        error = (
            str(exc.detail)
            if isinstance(exc, HTTPException)
            else f"{type(exc).__name__}: {exc}"
        )

        logger.exception(
            "Automation failed for incident %s", incident_id
        )

        try:
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE incident_automation_jobs
                    SET status = 'failed',
                        last_error = %s,
                        updated_at = NOW()
                    WHERE incident_id = %s
                    """,
                    (error[:4000], incident_id),
                )

                record_event(
                    conn,
                    incident_id,
                    "automation_failed",
                    {"error": error[:1000]},
                )
        except Exception:
            logger.exception(
                "Could not persist automation failure for incident %s",
                incident_id,
            )


@router.post("/webhook/alertmanager")
async def alertmanager_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        raise HTTPException(
            status_code=400,
            detail="Missing alerts array",
        )

    # Ensure the state table exists before processing webhook events.
    ensure_automation_table()

    results = []

    try:
        for alert in alerts:
            incident_id, should_automate = process_alert(alert)

            if should_automate:
                background_tasks.add_task(
                    run_automatic_workflow,
                    incident_id,
                )

            results.append({
                "incident_id": incident_id,
                "automation_queued": should_automate,
            })

    except Exception:
        logger.exception("Alertmanager webhook ingestion failed")
        raise HTTPException(
            status_code=503,
            detail="Alert ingestion failed; retry later",
        )

    return {"received": len(results), "incidents": results}
@router.get("")
def list_incidents():
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM incidents
            ORDER BY updated_at DESC
            LIMIT 100
            """
        ).fetchall()


@router.get("/{incident_id}")
def get_incident(incident_id: int):
    with get_connection() as conn:
        incident = conn.execute(
            "SELECT * FROM incidents WHERE id = %s",
            (incident_id,),
        ).fetchone()

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        events = conn.execute(
            """
            SELECT * FROM incident_events
            WHERE incident_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (incident_id,),
        ).fetchall()
        automation = conn.execute(
    """
    SELECT incident_id, status, attempts, last_error,
           created_at, updated_at
    FROM incident_automation_jobs
    WHERE incident_id = %s
    """,
    (incident_id,),
).fetchone()
        return {"incident": incident, "events": events, "automation": automation}


@router.post("/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE incidents
            SET status = 'acknowledged', updated_at = NOW()
            WHERE id = %s AND status = 'open'
            RETURNING id
            """,
            (incident_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=409,
                detail="Incident not found or not open",
            )

        record_event(conn, incident_id, "acknowledged", {})

    return {"incident_id": incident_id, "status": "acknowledged"}


@router.post("/{incident_id}/analyze")
async def analyze_incident(incident_id: int):
    # Fetch the incident from our database.
    with get_connection() as conn:
        incident = conn.execute(
            """
            SELECT id, title, severity, status, labels, annotations
            FROM incidents
            WHERE id = %s
            """,
            (incident_id,),
        ).fetchone()

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    # Call the separate LangGraph agent service.
    agent_url = os.getenv(
        "AGENT_URL",
        "http://localhost:8002",
    ).rstrip("/")

    payload = {
        "incident_id": incident["id"],
        "title": incident["title"],
        "severity": incident["severity"],
        "status": incident["status"],
        "labels": incident["labels"],
        "annotations": incident["annotations"],
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{agent_url}/analyze",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="RCA agent timed out",
        )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="RCA agent request failed",
        )
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="RCA agent returned invalid JSON",
        )

    # Validate the fields we need before persisting the result.
    try:
        root_cause = result["root_cause"]
        confidence = float(result["confidence"])
        supporting_evidence = result["supporting_evidence"]
        evidence_gaps = result["evidence_gaps"]
        proposed_action = result["proposed_action"]
        action_reason = result["action_reason"]
        requires_approval = result["requires_approval"]

        if not isinstance(root_cause, str) or not root_cause.strip():
            raise ValueError("Invalid root cause")
        if not 0 <= confidence <= 1:
            raise ValueError("Invalid confidence")
        if not isinstance(supporting_evidence, list):
            raise ValueError("Invalid supporting evidence")
        if not isinstance(evidence_gaps, list):
            raise ValueError("Invalid evidence gaps")
        if proposed_action not in {
            "reset_injected_fault",
            "rollback_release",
            "enable_payment_fallback",
            "no_action",
        }:
            raise ValueError("Invalid proposed action")
        if not isinstance(action_reason, str):
            raise ValueError("Invalid action reason")
        if not isinstance(requires_approval, bool):
            raise ValueError("Invalid approval flag")

    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="RCA agent returned an invalid result",
        )

    # Persist the analysis and audit event together.
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO rca_analyses (
                incident_id,
                root_cause,
                confidence,
                supporting_evidence,
                evidence_gaps,
                proposed_action,
                action_reason,
                requires_approval
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (
                incident_id,
                root_cause,
                confidence,
                Jsonb(supporting_evidence),
                Jsonb(evidence_gaps),
                proposed_action,
                action_reason,
                requires_approval,
            ),
        ).fetchone()

        record_event(
            conn,
            incident_id,
            "rca_completed",
            {
                "analysis_id": row["id"],
                "confidence": confidence,
                "proposed_action": proposed_action,
                "requires_approval": requires_approval,
            },
        )

    return {
        "analysis_id": row["id"],
        "incident_id": incident_id,
        **result,
        "created_at": row["created_at"],
    }


@router.get("/{incident_id}/rca")
def get_incident_rca(incident_id: int):
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM incidents WHERE id = %s",
            (incident_id,),
        ).fetchone()

        if not exists:
            raise HTTPException(
                status_code=404,
                detail="Incident not found",
            )

        analyses = conn.execute(
            """
            SELECT *
            FROM rca_analyses
            WHERE incident_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (incident_id,),
        ).fetchall()

    return {
        "incident_id": incident_id,
        "analyses": analyses,
    }


@router.post("/{incident_id}/remediations")
def request_remediation(incident_id: int):
    """Create an approval request linked to the latest eligible RCA."""
    allowed_actions = {
        "reset_injected_fault",
        "rollback_release",
        "enable_payment_fallback",
    }

    with get_connection() as conn:
        analysis = conn.execute(
            """
            SELECT *
            FROM rca_analyses
            WHERE incident_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (incident_id,),
        ).fetchone()

        if not analysis:
            raise HTTPException(
                status_code=409,
                detail="Run RCA before requesting remediation",
            )

        action = analysis["proposed_action"]

        if action not in allowed_actions:
            raise HTTPException(
                status_code=409,
                detail="Latest RCA did not propose an executable action",
            )

        if not analysis["requires_approval"]:
            raise HTTPException(
                status_code=409,
                detail="Remediation requires explicit approval",
            )

        existing = conn.execute(
            """
            SELECT id, status, action, rca_analysis_id
            FROM remediation_actions
            WHERE incident_id = %s
              AND status IN ('pending', 'executing')
            ORDER BY id DESC
            """,
            (incident_id,),
        ).fetchall()

        for item in existing:
            same_request = (
                item["action"] == action
                and item["rca_analysis_id"] == analysis["id"]
            )

            if same_request and item["status"] == "pending":
                return {
                    **dict(item),
                    "incident_id": incident_id,
                }

            if item["status"] == "executing":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "A remediation is already executing",
                        "remediation_id": item["id"],
                        "status": item["status"],
                    },
                )

            if not same_request:
                reason = (
                    "Rejected: superseded by a newer RCA or a different "
                    "proposed action. No remediation was executed."
                )

                conn.execute(
                    """
                    UPDATE remediation_actions
                    SET status = 'rejected',
                        error = %s,
                        completed_at = NOW()
                    WHERE id = %s
                      AND status = 'pending'
                    """,
                    (reason, item["id"]),
                )

                record_event(
                    conn,
                    incident_id,
                    "remediation_rejected",
                    {
                        "remediation_id": item["id"],
                        "reason": reason,
                        "superseded_by_analysis_id": analysis["id"],
                    },
                )

        row = conn.execute(
            """
            INSERT INTO remediation_actions (
                incident_id, rca_analysis_id, action
            )
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (incident_id, analysis["id"], action),
        ).fetchone()

        record_event(
            conn,
            incident_id,
            "remediation_requested",
            {
                "remediation_id": row["id"],
                "analysis_id": analysis["id"],
                "action": action,
            },
        )

        return row




@router.post(
    "/{incident_id}/remediations/{remediation_id}/approve"
)
async def approve_remediation(
    incident_id: int,
    remediation_id: int,
):
    """Approve, execute and verify an allowlisted remediation."""

    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE remediation_actions
            SET status = 'executing',
                approved_at = NOW()
            WHERE id = %s
              AND incident_id = %s
              AND status = 'pending'
              AND action IN (
                  'reset_injected_fault',
                  'rollback_release',
                  'enable_payment_fallback'
              )
            RETURNING *
            """,
            (remediation_id, incident_id),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Remediation not found, not pending, "
                    "or already processed"
                ),
            )

        action = row["action"]

        record_event(
            conn,
            incident_id,
            "remediation_approved",
            {
                "remediation_id": remediation_id,
                "action": action,
            },
        )

    verification = {"action": action}
    failure = None
    expected_alert = None
    pre_action_health = None

    service_url = os.getenv(
        "MOCK_SERVICE_URL",
        "http://mock-service:8000",
    ).rstrip("/")

    prometheus_url = os.getenv(
        "PROMETHEUS_URL",
        "http://prometheus:9090",
    ).rstrip("/")

    try:
        with get_connection() as conn:
            incident = conn.execute(
                "SELECT labels FROM incidents WHERE id = %s",
                (incident_id,),
            ).fetchone()
        if not incident:
            raise ValueError("Incident not found for remediation")
        expected_alert = _expected_alert_for_incident(
            action,
            incident["labels"],
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            pre_action_response = await client.get(
                f"{service_url}/health"
            )
            pre_action_response.raise_for_status()
            pre_action_health = pre_action_response.json()
            if (
                not isinstance(pre_action_health, dict)
                or pre_action_health.get("status") != "ok"
            ):
                raise RuntimeError(
                    "Mock service health was invalid before remediation"
                )

            verification["expected_alert"] = expected_alert
            verification["pre_action_health"] = pre_action_health
            with get_connection() as conn:
                saved = conn.execute(
                    """
                    UPDATE remediation_actions
                    SET verification = %s
                    WHERE id = %s
                      AND incident_id = %s
                      AND status = 'executing'
                    RETURNING id
                    """,
                    (
                        Jsonb(verification),
                        remediation_id,
                        incident_id,
                    ),
                ).fetchone()
            if not saved:
                raise RuntimeError(
                    "Could not persist pre-remediation health snapshot"
                )

            action_response = await client.post(
                f"{service_url}/admin/remediation",
                json={"action": action},
            )
            action_response.raise_for_status()
            action_result = action_response.json()
            verification["service_state"] = action_result
            if not _action_result_is_valid(
                action,
                action_result,
                pre_action_health,
            ):
                raise RuntimeError(
                    f"Mock service did not confirm action {action}"
                )

            recovery_checks = []
            alert_checks = []
            verification["recovery_checks"] = recovery_checks
            verification["alert_firing_checks"] = alert_checks
            for index in range(10):
                health_response = await client.get(
                    f"{service_url}/health"
                )
                health_response.raise_for_status()
                health = health_response.json()
                order_response = await client.get(
                    f"{service_url}/api/orders"
                )
                try:
                    order_data = order_response.json()
                except ValueError as exc:
                    raise RuntimeError(
                        "Orders endpoint returned invalid JSON"
                    ) from exc

                service_ok = _service_state_is_valid(
                    action,
                    health,
                    order_response.status_code,
                    order_data,
                    pre_action_health,
                )
                recovery_checks.append({
                    "health": health,
                    "order_status_code": order_response.status_code,
                    "orders": order_data,
                    "passed": service_ok,
                })
                alert_checks.append(
                    await _query_alert_firing(
                        client,
                        prometheus_url,
                        expected_alert,
                    )
                )
                if index < 9:
                    await asyncio.sleep(5)

            service_stable = (
                len(recovery_checks) == 10
                and all(check["passed"] for check in recovery_checks)
            )
            alert_cleared = (
                len(alert_checks) == 10
                and all(firing is False for firing in alert_checks)
            )
            verified = service_stable and alert_cleared
            verification.update({
                "service_stable": service_stable,
                "alert_cleared": alert_cleared,
                "verified": verified,
            })

            if not verified:
                failure = (
                    "Recovery verification failed: service checks or "
                    "relevant alert-clear checks did not pass"
                )

    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        failure = (
            f"Remediation execution or verification failed: "
            f"{type(exc).__name__}: {exc}"
        )
        verification["verified"] = False
        verification["error"] = failure

    final_status = "succeeded" if failure is None else "failed"
    _persist_remediation_result(
        incident_id,
        remediation_id,
        action,
        final_status,
        verification,
        failure,
    )

    return {
        "incident_id": incident_id,
        "remediation_id": remediation_id,
        "action": action,
        "status": final_status,
        "verification": verification,
        "error": failure,
    }

@router.get("/{incident_id}/remediations")
def get_incident_remediations(incident_id: int):
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM incidents WHERE id = %s",
            (incident_id,),
        ).fetchone()

        if not exists:
            raise HTTPException(
                status_code=404,
                detail="Incident not found",
            )

        rows = conn.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE incident_id = %s
            ORDER BY id DESC
            """,
            (incident_id,),
        ).fetchall()

    return {
        "incident_id": incident_id,
        "remediations": rows,
    }

def ensure_automation_table():
    """Create the persistent workflow-state table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_automation_jobs (
                incident_id BIGINT PRIMARY KEY
                    REFERENCES incidents(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (
                        status IN (
                            'pending', 'running', 'completed', 'failed'
                        )
                    ),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _track_automation_task(task):
    _automation_tasks.add(task)
    task.add_done_callback(_automation_tasks.discard)


@router.on_event("startup")
async def recover_automation_jobs():
    """Requeue jobs left unfinished by a backend restart."""
    try:
        ensure_automation_table()

        with get_connection() as conn:
            # This is intended for the project's single backend instance.
            conn.execute(
                """
                UPDATE incident_automation_jobs
                SET status = 'pending',
                    updated_at = NOW()
                WHERE status = 'running'
                """
            )

            pending = conn.execute(
                """
                SELECT incident_id
                FROM incident_automation_jobs
                WHERE status = 'pending'
                ORDER BY created_at
                """
            ).fetchall()

        for row in pending:
            task = asyncio.create_task(
                run_automatic_workflow(row["incident_id"])
            )
            _track_automation_task(task)

        logger.info(
            "Recovered %s pending automation job(s)", len(pending)
        )

    except Exception:
        logger.exception("Could not recover automation jobs")


@router.post("/{incident_id}/automation/retry")
def retry_automation(
    incident_id: int,
    background_tasks: BackgroundTasks,
):
    """Retry a failed investigation without creating a new incident."""
    ensure_automation_table()

    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE incident_automation_jobs
            SET status = 'pending',
                last_error = NULL,
                updated_at = NOW()
            WHERE incident_id = %s
              AND status = 'failed'
            RETURNING incident_id
            """,
            (incident_id,),
        ).fetchone()

        if not row:
            exists = conn.execute(
                "SELECT id FROM incidents WHERE id = %s",
                (incident_id,),
            ).fetchone()

            if not exists:
                raise HTTPException(
                    status_code=404,
                    detail="Incident not found",
                )

            raise HTTPException(
                status_code=409,
                detail="Automation job is not in failed state",
            )

        record_event(
            conn,
            incident_id,
            "automation_retry_requested",
            {},
        )

    background_tasks.add_task(
        run_automatic_workflow,
        incident_id,
    )

    return {
        "incident_id": incident_id,
        "automation_status": "pending",
    }



async def reconcile_remediation(
    incident_id: int,
    remediation_id: int,
):
    """Reconcile a stuck execution using fresh recovery evidence."""

    service_url = os.getenv(
        "MOCK_SERVICE_URL",
        "http://mock-service:8000",
    ).rstrip("/")

    prometheus_url = os.getenv(
        "PROMETHEUS_URL",
        "http://prometheus:9090",
    ).rstrip("/")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT remediation_actions.*, incidents.labels AS incident_labels
            FROM remediation_actions
            JOIN incidents
              ON incidents.id = remediation_actions.incident_id
            WHERE remediation_actions.id = %s
              AND remediation_actions.incident_id = %s
              AND remediation_actions.status = 'executing'
              AND remediation_actions.action IN (
                  'reset_injected_fault',
                  'rollback_release',
                  'enable_payment_fallback'
              )
            """,
            (remediation_id, incident_id),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=409,
                detail="No executing remediation found to reconcile",
            )

        action = row["action"]
        previous_verification = row["verification"]

    if isinstance(previous_verification, str):
        try:
            previous_verification = json.loads(previous_verification)
        except json.JSONDecodeError:
            previous_verification = None

    verification = (
        dict(previous_verification)
        if isinstance(previous_verification, dict)
        else {"action": action}
    )
    verification["reconciled"] = True
    failure = None

    try:
        expected_alert = _expected_alert_for_incident(
            action,
            row["incident_labels"],
        )
        pre_action_health = verification.get("pre_action_health")
        if (
            action in {"reset_injected_fault", "enable_payment_fallback"}
            and not isinstance(pre_action_health, dict)
        ):
            raise ValueError(
                "Pre-remediation health snapshot is missing or invalid"
            )
        verification["expected_alert"] = expected_alert

        async with httpx.AsyncClient(timeout=10.0) as client:
            health_response = await client.get(
                f"{service_url}/health"
            )
            health_response.raise_for_status()
            health = health_response.json()

            orders_response = await client.get(
                f"{service_url}/api/orders"
            )
            try:
                orders = orders_response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Orders endpoint returned invalid JSON"
                ) from exc

            service_ok = _service_state_is_valid(
                action,
                health,
                orders_response.status_code,
                orders,
                pre_action_health,
            )
            alert_firing = await _query_alert_firing(
                client,
                prometheus_url,
                expected_alert,
            )
            alert_cleared = not alert_firing
            verified = service_ok and alert_cleared

            verification.update({
                "health": health,
                "order_status_code": orders_response.status_code,
                "orders": orders,
                "service_stable": service_ok,
                "alert_firing": alert_firing,
                "alert_cleared": alert_cleared,
                "verified": verified,
            })

            if not verified:
                failure = (
                    "Reconciliation failed: current service recovery "
                    "or relevant alert-clear checks did not pass"
                )

    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        failure = (
            f"Reconciliation verification failed: "
            f"{type(exc).__name__}: {exc}"
        )
        verification["verified"] = False
        verification["error"] = failure

    final_status = "succeeded" if failure is None else "failed"
    updated = _persist_remediation_result(
        incident_id,
        remediation_id,
        action,
        final_status,
        verification,
        failure,
        reconciled=True,
    )

    return {
        "remediation": updated,
        "verification": verification,
    }
