
import time
from collections import Counter
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

app = FastAPI(title="AutoHeal Mock Production Service")

lock = Lock()
fault_mode = "normal"
request_counts = Counter()


@app.middleware("http")
async def collect_metrics(request, call_next):
    response = await call_next(request)

    # Count requests by endpoint and response status.
    with lock:
        request_counts[(request.url.path, response.status_code)] += 1

    return response


@app.get("/health")
def health():
    return {"status": "ok", "fault_mode": fault_mode}


@app.get("/api/orders")
def get_orders():
    if fault_mode == "high_errors":
        raise HTTPException(
            status_code=503,
            detail="Injected service failure",
        )

    if fault_mode == "high_latency":
        time.sleep(2)

    return {"orders": [{"id": 1, "status": "created"}]}


@app.post("/admin/fault")
def set_fault(payload: dict):
    global fault_mode

    mode = payload.get("mode")
    if mode not in {"normal", "high_errors", "high_latency"}:
        raise HTTPException(status_code=400, detail="Unsupported fault mode")

    with lock:
        fault_mode = mode

    return {"fault_mode": fault_mode}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with lock:
        counts = list(request_counts.items())

    lines = [
        "# HELP mock_http_requests_total Total HTTP requests",
        "# TYPE mock_http_requests_total counter",
    ]

    for (path, status), count in counts:
        lines.append(
            f'mock_http_requests_total{{path="{path}",status="{status}"}} {count}'
        )

    return "\n".join(lines) + "\n"
