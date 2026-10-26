
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import get_connection, init_db
from app.incidents import (
    reconcile_remediation,
    router as incidents_router,
)

logger = logging.getLogger(__name__)


async def remediation_watchdog():
    """Reconcile stale executions without rerunning their actions."""

    while True:
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, incident_id
                    FROM remediation_actions
                    WHERE status = 'executing'
                      AND approved_at IS NOT NULL
                      AND approved_at < NOW() - INTERVAL '3 minutes'
                    ORDER BY approved_at ASC
                    LIMIT 20
                    """
                ).fetchall()

            for row in rows:
                remediation_id = row["id"]
                incident_id = row["incident_id"]

                try:
                    result = await reconcile_remediation(
                        incident_id,
                        remediation_id,
                    )
                    logger.warning(
                        "Reconciled stale remediation %s for incident %s: %s",
                        remediation_id,
                        incident_id,
                        result["remediation"]["status"],
                    )
                except Exception:
                    logger.exception(
                        "Could not reconcile remediation %s for incident %s",
                        remediation_id,
                        incident_id,
                    )

        except Exception:
            logger.exception("Remediation watchdog encountered an error")

        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    watchdog_task = asyncio.create_task(remediation_watchdog())

    try:
        yield
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="AutoHeal Operations Backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(incidents_router)


@app.get("/health")
def health():
    return {"status": "ok"}
