from fastapi import FastAPI, HTTPException
import logging

from app.graph import run_analysis
from app.schemas import IncidentInput, RCAResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AutoHeal RCA Agent")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze", response_model=RCAResult)
def analyze(incident: IncidentInput):
    try:
        return run_analysis(incident)
    except Exception as exc:
        logger.exception(
            "RCA analysis failed (exception_type=%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Incident analysis failed: {type(exc).__name__}",
        ) from exc
