
import time
from collections import Counter, deque
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

app = FastAPI(title="AutoHeal Mock Production Service")

lock = Lock()

fault_mode = "normal"
release = "release-stable"
payment_fallback_enabled = False

request_counts = Counter()
dependency_request_counts = Counter()
dependency_duration_bucket_counts = Counter()
dependency_duration_sum = 0.0
dependency_duration_count = 0

events = deque(maxlen=200)
changes = deque(maxlen=50)

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def record_event(event_type, **fields):
    event = {
        "timestamp": timestamp(),
        "event_type": event_type,
        **fields,
    }
    with lock:
        events.append(event)


def record_change(change_type, **fields):
    change = {
        "timestamp": timestamp(),
        "change_type": change_type,
        **fields,
    }
    with lock:
        changes.appendleft(change)


@app.middleware("http")
async def collect_metrics(request, call_next):
    response = await call_next(request)

    with lock:
        request_counts[
            (request.url.path, response.status_code, release)
        ] += 1

    return response


def call_payment_dependency():
    """Simulate a downstream payment dependency."""
    global dependency_duration_sum, dependency_duration_count

    started = time.perf_counter()
    status = "200"
    error = None

    with lock:
        mode = fault_mode
        current_release = release

    try:
        if mode == "dependency_slow":
            time.sleep(1.2)

        elif mode == "dependency_timeout":
            time.sleep(0.1)
            status = "504"
            raise TimeoutError("Simulated payment dependency timeout")

        return {"status": "ok"}

    except TimeoutError as exc:
        error = str(exc)
        raise HTTPException(
            status_code=504,
            detail="Payment dependency timed out",
        ) from exc

    finally:
        duration = time.perf_counter() - started

        with lock:
            dependency_request_counts[status] += 1
            dependency_duration_sum += duration
            dependency_duration_count += 1

            for bucket in LATENCY_BUCKETS:
                if duration <= bucket:
                    dependency_duration_bucket_counts[bucket] += 1

        record_event(
            "dependency_request",
            dependency="payment",
            status=int(status),
            duration_seconds=round(duration, 4),
            error=error,
            release=current_release,
        )


@app.get("/health")
def health():
    with lock:
        current_mode = fault_mode
        current_release = release
        fallback_enabled = payment_fallback_enabled

    return {
        "status": "ok",
        "fault_mode": current_mode,
        "release": current_release,
        "payment_fallback_enabled": fallback_enabled,
    }


@app.get("/api/orders")
def get_orders():
    with lock:
        mode = fault_mode
        current_release = release
        fallback_enabled = payment_fallback_enabled

    if mode == "high_errors":
        record_event(
            "request_failure",
            endpoint="/api/orders",
            status=503,
            release=current_release,
            reason="Injected service failure",
        )
        raise HTTPException(
            status_code=503,
            detail="Injected service failure",
        )

    if mode == "high_latency":
        time.sleep(2)

    if mode == "deployment_regression":
        record_event(
            "request_failure",
            endpoint="/api/orders",
            status=503,
            release=current_release,
            reason="Simulated deployment regression",
        )
        raise HTTPException(
            status_code=503,
            detail="Simulated deployment regression",
        )

    if fallback_enabled and mode in {
        "dependency_slow",
        "dependency_timeout",
    }:
        payment = {"status": "fallback"}
        record_event(
            "payment_fallback_used",
            endpoint="/api/orders",
            release=current_release,
            reason="Used local payment fallback instead of dependency",
        )
    else:
        payment = call_payment_dependency()

    return {
        "orders": [{"id": 1, "status": "created"}],
        "payment_dependency": payment["status"],
        "release": current_release,
    }


@app.post("/admin/fault")
def set_fault(payload: dict):
    global fault_mode, release, payment_fallback_enabled

    mode = payload.get("mode")
    supported_modes = {
        "normal",
        "high_errors",
        "high_latency",
        "dependency_slow",
        "dependency_timeout",
        "deployment_regression",
    }

    if mode not in supported_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported fault mode. Supported modes: {sorted(supported_modes)}",
        )

    with lock:
        previous_mode = fault_mode
        previous_release = release

        fault_mode = mode
        payment_fallback_enabled = False
        release = (
            "release-regression"
            if mode == "deployment_regression"
            else "release-stable"
        )

        current_release = release

    record_change(
        "simulated_deployment"
        if mode == "deployment_regression"
        else "fault_mode_change",
        previous_mode=previous_mode,
        new_mode=mode,
        previous_release=previous_release,
        release=current_release,
    )

    record_event(
        "fault_mode_changed",
        previous_mode=previous_mode,
        new_mode=mode,
        release=current_release,
    )

    return {
        "fault_mode": mode,
        "release": current_release,
    }


@app.get("/admin/changes")
def recent_changes():
    with lock:
        return {"changes": list(changes)}


@app.get("/diagnostics/events")
def recent_events(limit: int = 50):
    if not 1 <= limit <= 200:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 200",
        )

    with lock:
        result = list(events)[-limit:]

    return {"events": result}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with lock:
        request_snapshot = list(request_counts.items())
        dependency_snapshot = list(dependency_request_counts.items())
        bucket_snapshot = dict(dependency_duration_bucket_counts)
        duration_sum = dependency_duration_sum
        duration_count = dependency_duration_count

    lines = [
        "# HELP mock_http_requests_total Total HTTP requests",
        "# TYPE mock_http_requests_total counter",
    ]

    for (path, status, request_release), count in request_snapshot:
        lines.append(
            f'mock_http_requests_total{{path="{path}",'
            f'status="{status}",release="{request_release}"}} {count}'
        )

    lines.extend([
        "# HELP mock_payment_dependency_requests_total "
        "Total simulated payment dependency requests",
        "# TYPE mock_payment_dependency_requests_total counter",
    ])

    for status, count in dependency_snapshot:
        lines.append(
            f'mock_payment_dependency_requests_total'
            f'{{status="{status}"}} {count}'
        )

    lines.extend([
        "# HELP mock_payment_dependency_duration_seconds "
        "Simulated payment dependency request duration",
        "# TYPE mock_payment_dependency_duration_seconds histogram",
    ])

    for bucket in LATENCY_BUCKETS:
        lines.append(
            "mock_payment_dependency_duration_seconds_bucket"
            f'{{le="{bucket}"}} {bucket_snapshot.get(bucket, 0)}'
        )

    lines.append(
        "mock_payment_dependency_duration_seconds_bucket"
        f'{{le="+Inf"}} {duration_count}'
    )
    lines.append(
        "mock_payment_dependency_duration_seconds_sum "
        f"{duration_sum:.6f}"
    )
    lines.append(
        "mock_payment_dependency_duration_seconds_count "
        f"{duration_count}"
    )

    return "\n".join(lines) + "\n"


@app.post("/admin/remediation")
def execute_remediation(payload: dict):
    """Execute one explicitly allowlisted simulated remediation."""
    global fault_mode, release, payment_fallback_enabled

    action = payload.get("action")

    if action not in {
        "reset_injected_fault",
        "rollback_release",
        "enable_payment_fallback",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported remediation action",
        )

    with lock:
        previous_mode = fault_mode
        previous_release = release

        if action == "rollback_release":
            if release != "release-regression":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Rollback unavailable: current release is not "
                        "release-regression"
                    ),
                )

            fault_mode = "normal"
            release = "release-stable"

        elif action == "reset_injected_fault":
            fault_mode = "normal"
            payment_fallback_enabled = False

        elif action == "enable_payment_fallback":
            if fault_mode not in {
                "dependency_slow",
                "dependency_timeout",
            }:
                raise HTTPException(
                    status_code=409,
                    detail="Payment fallback requires an active dependency fault",
                )
            payment_fallback_enabled = True

        current_mode = fault_mode
        current_release = release
        fallback_enabled = payment_fallback_enabled

    if action == "enable_payment_fallback":
        record_change(
            "payment_fallback_enabled",
            action=action,
            fault_mode=current_mode,
            release=current_release,
        )
    elif action == "rollback_release":
        record_change(
            "simulated_rollback",
            action=action,
            previous_mode=previous_mode,
            new_mode=current_mode,
            previous_release=previous_release,
            release=current_release,
        )
    else:
        record_change(
            "fault_mode_change",
            action=action,
            previous_mode=previous_mode,
            new_mode=current_mode,
            previous_release=previous_release,
            release=current_release,
        )

    record_event(
        "remediation_executed",
        action=action,
        previous_mode=previous_mode,
        new_mode=current_mode,
        previous_release=previous_release,
        release=current_release,
        payment_fallback_enabled=fallback_enabled,
    )

    return {
        "action": action,
        "fault_mode": current_mode,
        "release": current_release,
        "payment_fallback_enabled": fallback_enabled,
        "simulated": True,
    }
