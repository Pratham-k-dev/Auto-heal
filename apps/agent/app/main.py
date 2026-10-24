
from fastapi import FastAPI, HTTPException

from app.graph import run_analysis
from app.schemas import IncidentInput, RCAResult

app = FastAPI(title="AutoHeal RCA Agent")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze", response_model=RCAResult)
def analyze(incident: IncidentInput):
    try:
        return run_analysis(incident)
    except Exception as exc:
        # Do not expose API keys, prompts, or provider internals to callers.
        raise HTTPException(
            status_code=502,
            detail=f"Incident analysis failed: {type(exc).__name__}",
        ) from exc
