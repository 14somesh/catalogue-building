import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.jobs import (
    init_db,
    start_worker,
    stop_worker,
    get_job,
    list_jobs,
    enqueue_job,
    BrandLockedError,
    DEFAULT_DB_PATH
)
from src.utils.logger import setup_logger

logger = setup_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes job store, performs crash recovery, and runs single worker thread."""
    logger.info("Starting up FastAPI catalogue API...")
    init_db()
    start_worker()
    yield
    logger.info("Shutting down FastAPI catalogue API...")
    stop_worker()


app = FastAPI(
    title="Vianet Catalogue Engine API",
    version="0.1.0",
    description="Phase 0 backend foundation for autonomous catalogue generation pipeline.",
    lifespan=lifespan
)

# Mount static directories for image packshots and compiled PDF deliverables
os.makedirs("images", exist_ok=True)
os.makedirs("dist", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/dist", StaticFiles(directory="dist"), name="dist")


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Basic service health check."""
    return {"status": "ok"}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> Dict[str, Any]:
    """Returns the current status, progress, and results for a specific job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@app.get("/jobs")
def get_recent_jobs(limit: int = Query(50, ge=1, le=500)) -> List[Dict[str, Any]]:
    """Lists recent pipeline jobs."""
    return list_jobs(limit=limit)


# Internal enqueue endpoint for Phase 0 verification and future Phase 1 pipeline integration
@app.post("/jobs/enqueue")
def enqueue_pipeline_job(
    job_type: str,
    brand: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Enqueues a pipeline job with Brand Lock enforcement."""
    try:
        job_id = enqueue_job(job_type=job_type, brand=brand, payload=payload)
        return {"job_id": job_id, "status": "queued"}
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
