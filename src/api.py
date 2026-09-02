import os
import re
import sys
import time
import uuid
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    set_main_event_loop,
    register_subscriber,
    unregister_subscriber,
    request_job_cancellation,
    is_job_cancellation_requested,
    DEFAULT_DB_PATH
)
from src.utils.excel_handler import (
    load_catalogue_data,
    load_catalogue_data_readonly,
    save_catalogue_data,
    get_effective_value,
    get_effective_product_dict,
    is_empty_value,
    slugify
)
from src.utils.logger import setup_logger

logger = setup_logger("api")

UPLOAD_DIR = "uploads"
BROCHURE_DIR = "brochures"
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB
ALLOWED_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".xlsx", ".xls", ".csv", ".txt"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes job store, registers event loop, performs crash recovery, and runs single worker thread."""
    logger.info("Starting up FastAPI catalogue API...")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(BROCHURE_DIR, exist_ok=True)
    os.makedirs("images", exist_ok=True)
    os.makedirs("dist", exist_ok=True)
    
    # Register running asyncio loop for thread-safe worker event broadcasting
    loop = asyncio.get_running_loop()
    set_main_event_loop(loop)
    
    init_db()
    start_worker()
    yield
    logger.info("Shutting down FastAPI catalogue API...")
    stop_worker()


app = FastAPI(
    title="Vianet Catalogue Engine API",
    version="2.0.0",
    description="API for autonomous catalogue ingestion, curation, live collection, and PDF rendering pipeline.",
    lifespan=lifespan
)

# Mount static directories
os.makedirs("images", exist_ok=True)
os.makedirs("dist", exist_ok=True)
os.makedirs(BROCHURE_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/dist", StaticFiles(directory="dist"), name="dist")
app.mount("/brochures", StaticFiles(directory=BROCHURE_DIR), name="brochures")


# ==============================================================================
# 0. Health & Jobs Endpoints
# ==============================================================================

@app.get("/health")
def health_check() -> Dict[str, str]:
    """Basic service health check."""
    return {"status": "ok"}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Returns the current status, progress, and results for a specific job.
    Serves as the polling fallback for clients unable to use SSE streams.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@app.get("/jobs")
def get_recent_jobs(limit: int = Query(50, ge=1, le=500)) -> List[Dict[str, Any]]:
    """Lists recent pipeline jobs."""
    return list_jobs(limit=limit)


@app.get("/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """
    Server-Sent Events (SSE) live progress stream for a job.
    Emits milestone events: {"stage": str, "product_id": str|None, "current": int, "total": int, "message": str}
    Emits a final terminal event when the job completes, then closes cleanly.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    async def event_generator():
        # 1. Check if job is already finished
        current_job = get_job(job_id)
        if current_job and current_job["status"] in ("done", "failed", "cancelled"):
            initial_evt = {
                "stage": "status",
                "status": current_job["status"],
                "product_id": None,
                "current": current_job.get("progress_current", 0),
                "total": current_job.get("progress_total", 0),
                "message": current_job.get("message", "")
            }
            yield f"data: {json.dumps(initial_evt)}\n\n"
            terminal_evt = {
                "stage": "terminal",
                "status": current_job["status"],
                "product_id": None,
                "current": current_job.get("progress_current", 0),
                "total": current_job.get("progress_total", 0),
                "message": current_job.get("message", "Job ended."),
                "result": current_job.get("result"),
                "error": current_job.get("error")
            }
            yield f"data: {json.dumps(terminal_evt)}\n\n"
            return

        # 2. Register subscriber queue for live events
        q = register_subscriber(job_id)

        # Emit initial current status
        if current_job:
            init_evt = {
                "stage": "status",
                "status": current_job["status"],
                "product_id": None,
                "current": current_job.get("progress_current", 0),
                "total": current_job.get("progress_total", 0),
                "message": current_job.get("message", "")
            }
            yield f"data: {json.dumps(init_evt)}\n\n"

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=2.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("stage") == "terminal":
                        break
                except asyncio.TimeoutError:
                    # Check database status on timeout
                    j = get_job(job_id)
                    if not j or j["status"] in ("done", "failed", "cancelled"):
                        term_evt = {
                            "stage": "terminal",
                            "status": j["status"] if j else "unknown",
                            "product_id": None,
                            "current": j.get("progress_current", 0) if j else 0,
                            "total": j.get("progress_total", 0) if j else 0,
                            "message": j.get("message", "") if j else "Job ended.",
                            "result": j.get("result") if j else None,
                            "error": j.get("error") if j else None
                        }
                        yield f"data: {json.dumps(term_evt)}\n\n"
                        break
                    # Send SSE keep-alive comment
                    yield ": keep-alive\n\n"
        finally:
            unregister_subscriber(job_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> Dict[str, Any]:
    """
    Cancels a queued job outright. For a running job, signals graceful cancellation:
    stops after the current row finishes rather than killing it mid-write.
    Completed rows keep their data; rows not reached stay Pending.
    """
    try:
        return request_job_cancellation(job_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


# ==============================================================================
# 1. Upload Staging Endpoint: POST /uploads
# ==============================================================================

@app.post("/uploads")
async def upload_staging_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Accepts multipart file upload (png, jpg, jpeg, pdf, xlsx, xls, csv, txt).
    Saves to uploads/ with a unique identifier.
    Rejects files over 25 MB or with disallowed extensions.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided in upload.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        allowed_str = ", ".join(sorted(ALLOWED_UPLOAD_EXTS))
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{ext}' is not allowed. Allowed extensions: {allowed_str}"
        )

    contents = await file.read()
    file_size = len(contents)
    if file_size > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File size ({round(file_size / (1024*1024), 2)} MB) exceeds maximum allowed limit of 25 MB."
        )

    upload_id = f"upl_{uuid.uuid4().hex[:12]}"
    clean_orig_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
    saved_filename = f"{upload_id}_{clean_orig_name}"
    saved_path = os.path.join(UPLOAD_DIR, saved_filename).replace("\\", "/")

    with open(saved_path, "wb") as f:
        f.write(contents)

    logger.info(f"File uploaded successfully: {saved_path} (size: {file_size} bytes)")
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "path": saved_path,
        "size": file_size
    }


# ==============================================================================
# 2. Ingest Endpoint: POST /ingest
# ==============================================================================

class IngestRequest(BaseModel):
    upload_id: Optional[str] = None
    pasted_text: Optional[str] = None


@app.post("/ingest")
def trigger_ingest_job(body: IngestRequest) -> Dict[str, Any]:
    """
    Enqueues an asynchronous 'ingest' job that extracts content and performs AI analysis.
    Returns job_id immediately without blocking.
    """
    target_path = None
    if body.upload_id:
        clean_uid = body.upload_id.strip()
        matches = [f for f in os.listdir(UPLOAD_DIR) if f.startswith(clean_uid)]
        if not matches:
            raise HTTPException(
                status_code=404,
                detail=f"Uploaded file with upload_id '{clean_uid}' was not found in uploads staging."
            )
        target_path = os.path.join(UPLOAD_DIR, matches[0]).replace("\\", "/")

    if not target_path and not (body.pasted_text and body.pasted_text.strip()):
        raise HTTPException(
            status_code=400,
            detail="Either 'upload_id' or non-empty 'pasted_text' must be provided in request body."
        )

    payload = {
        "file_path": target_path,
        "pasted_text": body.pasted_text.strip() if body.pasted_text else None
    }

    try:
        job_id = enqueue_job(job_type="ingest", brand=None, payload=payload)
        return {"job_id": job_id, "status": "queued"}
    except Exception as err:
        logger.error(f"Failed to enqueue ingest job: {err}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(err))


# ==============================================================================
# 3. Confirm Endpoint: POST /brands/{brand}/confirm
# ==============================================================================

class ProductRowInput(BaseModel):
    model_name: str
    display_name: Optional[str] = None
    dp: Optional[float] = None
    mrp: Optional[float] = None
    raw_text: Optional[str] = None
    notes: Optional[str] = None


class ConfirmBrandRequest(BaseModel):
    brand_name: Optional[str] = None
    brand_code: Optional[str] = None
    domain: Optional[str] = ""
    platform: Optional[str] = "shopify"
    column_mapping: Optional[Dict[str, str]] = None
    qualifier_tokens: Optional[List[Any]] = None
    dp_column_explanation: Optional[str] = None
    rows: List[ProductRowInput]


@app.post("/brands/{brand}/confirm")
def confirm_brand_onboarding(brand: str, body: ConfirmBrandRequest) -> Dict[str, Any]:
    """
    Enqueues a 'confirm' job in the job queue respecting the Brand Lock.
    Writes the accepted product rows to catalogue_data.xlsx and registers brand_defaults.yaml.
    Returns the created rows with their assigned Product_IDs.
    """
    target_brand = (body.brand_name or brand).strip()
    if not target_brand:
        raise HTTPException(status_code=400, detail="Brand name cannot be empty.")

    if not body.rows:
        raise HTTPException(status_code=400, detail="At least one product row must be submitted to confirm.")

    payload = {
        "brand_name": target_brand,
        "brand_code": body.brand_code,
        "domain": body.domain,
        "platform": body.platform,
        "column_mapping": body.column_mapping or {},
        "qualifier_tokens": body.qualifier_tokens or [],
        "dp_column_explanation": body.dp_column_explanation or "User confirmed pricing",
        "rows": [r.model_dump() for r in body.rows]
    }

    try:
        job_id = enqueue_job(job_type="confirm", brand=target_brand, payload=payload)
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    # Await job completion with a short timeout since writing rows takes ~0.2 seconds
    start_time = time.time()
    while time.time() - start_time < 10.0:
        job = get_job(job_id)
        if job and job["status"] == "done":
            res = job.get("result") or {}
            return {
                "job_id": job_id,
                "status": "done",
                "brand": target_brand,
                "count": res.get("count", 0),
                "created_rows": res.get("created_rows", [])
            }
        elif job and job["status"] == "failed":
            raise HTTPException(
                status_code=500,
                detail=job.get("error") or f"Confirm job '{job_id}' failed."
            )
        time.sleep(0.1)

    return {
        "job_id": job_id,
        "status": "running",
        "message": f"Brand confirmation for '{target_brand}' is processing in background."
    }


# ==============================================================================
# 4. Brochure Endpoints: POST, GET, DELETE /brands/{brand}/brochure
# ==============================================================================

@app.post("/brands/{brand}/brochure")
async def attach_brand_brochure(brand: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Accepts a PDF brochure upload for a brand.
    Saves to brochures/{brand_slug}/{filename}.pdf and updates the Brochure_PDF column in Excel.
    """
    clean_brand = brand.strip()
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail=f"Brochure must be a PDF file. Got '{ext}'.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Brochure exceeds 25 MB limit.")

    brand_slug = slugify(clean_brand)
    brand_brochure_dir = os.path.join(BROCHURE_DIR, brand_slug)
    os.makedirs(brand_brochure_dir, exist_ok=True)

    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
    dest_path = os.path.join(brand_brochure_dir, clean_name).replace("\\", "/")

    with open(dest_path, "wb") as f:
        f.write(contents)

    # Update Excel
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if not mask.any():
        try:
            os.remove(dest_path)
        except Exception:
            pass
        raise HTTPException(status_code=404, detail=f"Brand '{clean_brand}' not found in catalogue data.")

    rel_path = dest_path
    df.loc[mask, "Brochure_PDF"] = rel_path
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    logger.info(f"Attached brochure '{rel_path}' to {mask.sum()} rows for brand '{clean_brand}'.")
    return {
        "brand": clean_brand,
        "brochure_path": rel_path,
        "filename": clean_name,
        "attached_count": int(mask.sum())
    }


@app.get("/brands/{brand}/brochure")
def get_brand_brochure(brand: str) -> Dict[str, Any]:
    """Returns the currently attached brochure path for a brand."""
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == brand.strip().lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Brand '{brand}' not found in catalogue data.")

    brochure_vals = df.loc[mask, "Brochure_PDF"].dropna()
    current_path = str(brochure_vals.iloc[0]).strip() if not brochure_vals.empty else None
    return {
        "brand": brand.strip(),
        "brochure_path": current_path if current_path and not is_empty_value(current_path) else None
    }


@app.delete("/brands/{brand}/brochure")
def delete_brand_brochure(brand: str) -> Dict[str, Any]:
    """Detaches and removes the brochure for a brand."""
    clean_brand = brand.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Brand '{clean_brand}' not found in catalogue data.")

    brochure_vals = df.loc[mask, "Brochure_PDF"].dropna()
    if not brochure_vals.empty:
        old_path = str(brochure_vals.iloc[0]).strip()
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception as e:
                logger.warning(f"Failed to delete brochure file '{old_path}': {e}")

    df.loc[mask, "Brochure_PDF"] = None
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    logger.info(f"Detached brochure for brand '{clean_brand}'.")
    return {
        "brand": clean_brand,
        "brochure_path": None,
        "message": f"Brochure detached successfully from brand '{clean_brand}'."
    }


# ==============================================================================
# 5. Stage 3 Collection Endpoints (Live Progress & Retries)
# ==============================================================================

@app.post("/brands/{brand}/collect")
def start_brand_collection(
    brand: str,
    semantic_audit: bool = Query(False, description="Enable optional post-collection LLM semantic audit")
) -> Dict[str, Any]:
    """
    Enqueues a 'collect' job for the specified brand. Respects Brand Lock.
    Writes live progress to the jobs store as each row is processed.
    Returns job_id immediately without blocking.
    """
    clean_brand = brand.strip()
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Brand '{clean_brand}' not found in catalogue data.")

    try:
        job_id = enqueue_job(
            job_type="collect",
            brand=clean_brand,
            payload={"semantic_audit": semantic_audit}
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "brand": clean_brand,
            "stream_url": f"/jobs/{job_id}/stream"
        }
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))


class RetryBrandRequest(BaseModel):
    product_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of specific Product_IDs to retry. If omitted, retries all Blocked, Skipped, and Deferred rows for the brand."
    )


@app.post("/brands/{brand}/retry")
def retry_failed_rows(brand: str, body: Optional[RetryBrandRequest] = None) -> Dict[str, Any]:
    """
    Enqueues a 'retry' job for failed (Blocked, Skipped, Deferred) rows of a brand.
    Uses re_run_product from scratch for each row with Attempts reset.
    Respects Brand Lock and reports the identical per-row result structure as collect.
    """
    clean_brand = brand.strip()
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Brand '{clean_brand}' not found in catalogue data.")

    payload = {"product_ids": body.product_ids if body and body.product_ids else None}
    try:
        job_id = enqueue_job(job_type="retry", brand=clean_brand, payload=payload)
        return {
            "job_id": job_id,
            "status": "queued",
            "brand": clean_brand,
            "stream_url": f"/jobs/{job_id}/stream"
        }
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))


@app.post("/products/{product_id}/rerun")
def rerun_single_product(product_id: str) -> Dict[str, Any]:
    """
    Re-runs collection from scratch for a single product row via re_run_product.
    Enqueues through job queue with Brand Lock.
    Returns the updated row dict with failure reasons if failed.
    """
    clean_pid = product_id.strip()
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    brand = str(df.loc[mask, "Brand"].iloc[0]).strip()

    try:
        job_id = enqueue_job(
            job_type="rerun_product",
            brand=brand,
            payload={"product_id": clean_pid}
        )
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))

    # Await single-row completion for up to 20 seconds
    start_t = time.time()
    while time.time() - start_t < 20.0:
        job = get_job(job_id)
        if job and job["status"] == "done":
            res = job.get("result") or {}
            return {
                "job_id": job_id,
                "status": "done",
                "product_id": clean_pid,
                "brand": brand,
                "updated_row": res.get("updated_row")
            }
        elif job and job["status"] in ("failed", "cancelled"):
            raise HTTPException(
                status_code=500,
                detail=job.get("error") or f"Re-run job '{job_id}' failed."
            )
        time.sleep(0.1)

    return {
        "job_id": job_id,
        "status": "running",
        "product_id": clean_pid,
        "stream_url": f"/jobs/{job_id}/stream"
    }


class ManualSourceRequest(BaseModel):
    url: str = Field(..., description="Direct product URL to use as the starting point for collection")


@app.post("/products/{product_id}/source")
def provide_manual_source_url(product_id: str, body: ManualSourceRequest) -> Dict[str, Any]:
    """
    Sets Product_URL for a product row and re-runs collection using that URL as the starting point.
    Reports clearly whether collection succeeded or failed with the provided URL.
    """
    clean_pid = product_id.strip()
    clean_url = body.url.strip()
    if not clean_url or not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid URL. Must begin with http:// or https://")

    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    brand = str(df.loc[mask, "Brand"].iloc[0]).strip()

    try:
        job_id = enqueue_job(
            job_type="manual_source",
            brand=brand,
            payload={"product_id": clean_pid, "url": clean_url}
        )
    except BrandLockedError as err:
        raise HTTPException(status_code=409, detail=str(err))

    # Await single-row completion for up to 25 seconds
    start_t = time.time()
    while time.time() - start_t < 25.0:
        job = get_job(job_id)
        if job and job["status"] == "done":
            res = job.get("result") or {}
            return {
                "job_id": job_id,
                "status": "done",
                "product_id": clean_pid,
                "brand": brand,
                "manual_url": clean_url,
                "success": res.get("success", False),
                "message": res.get("message", ""),
                "updated_row": res.get("updated_row")
            }
        elif job and job["status"] in ("failed", "cancelled"):
            raise HTTPException(
                status_code=500,
                detail=job.get("error") or f"Manual source collection job '{job_id}' failed."
            )
        time.sleep(0.1)

    return {
        "job_id": job_id,
        "status": "running",
        "product_id": clean_pid,
        "manual_url": clean_url,
        "stream_url": f"/jobs/{job_id}/stream"
    }


# ==============================================================================
# 6. Read Endpoints: GET /brands & GET /brands/{brand}/rows
# ==============================================================================

@app.get("/brands")
def list_brands() -> List[Dict[str, Any]]:
    """
    Lists all brands in the catalogue with row counts grouped by status.
    Uses non-blocking read snapshot so it never locks or blocks jobs.
    """
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    if df.empty:
        return []

    unique_brands = df["Brand"].dropna().unique()
    brand_list = []

    for brand_name in unique_brands:
        brand_mask = df["Brand"].astype(str).str.lower() == str(brand_name).lower()
        b_df = df[brand_mask]

        status_counts = {
            "Approved": int((b_df["Status"] == "Approved").sum()),
            "Ready_For_Review": int((b_df["Status"] == "Ready_For_Review").sum()),
            "Pending": int((b_df["Status"] == "Pending").sum()),
            "Blocked": int((b_df["Status"] == "Blocked").sum()),
            "Skipped": int((b_df["Status"] == "Skipped").sum()),
            "Deferred": int((b_df["Status"] == "Deferred").sum())
        }

        brochure_vals = b_df["Brochure_PDF"].dropna()
        brochure_path = str(brochure_vals.iloc[0]).strip() if not brochure_vals.empty else None

        brand_list.append({
            "brand": str(brand_name),
            "total_rows": len(b_df),
            "status_counts": status_counts,
            "brochure_path": brochure_path if brochure_path and not is_empty_value(brochure_path) else None
        })

    return brand_list


@app.get("/brands/{brand}/rows")
def get_brand_rows(brand: str) -> List[Dict[str, Any]]:
    """
    Returns all product rows for a brand with resolved display fields needed by the UI:
    Product_ID, Model_Name, Display_Name, Status, DP, MRP, Source_URL, Image_Status, Flags.
    Uses non-blocking read-only snapshot.
    """
    clean_brand = brand.strip()
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Brand '{clean_brand}' not found in catalogue data.")

    from src.run_brand import derive_failure_reason

    brand_df = df[mask].sort_values(by="Product_ID", ascending=True)
    results = []

    for _, row in brand_df.iterrows():
        row_dict = row.to_dict()
        prod = get_effective_product_dict(row_dict)

        dp_val = prod.get("dp_raw") if prod.get("dp_raw") is not None else prod.get("dp")
        mrp_val = prod.get("mrp")
        failure_reason = derive_failure_reason(row_dict)

        results.append({
            "Product_ID": row_dict.get("Product_ID"),
            "Model_Name": row_dict.get("Model_Name"),
            "Display_Name": prod.get("display_name"),
            "Status": row_dict.get("Status", "Pending"),
            "DP": dp_val,
            "MRP": mrp_val,
            "Source_URL": row_dict.get("Source_URL"),
            "Image_Status": row_dict.get("Image_Status", "missing"),
            "Flags": row_dict.get("Flags"),
            "Failure_Reason": failure_reason,
            "Attempts": row_dict.get("Attempts", 0),
            "Category": row_dict.get("Category", "Powerbank"),
            "Brochure_PDF": row_dict.get("Brochure_PDF"),
            "Override_Title": row_dict.get("Override_Title"),
            "Override_DP": row_dict.get("Override_DP"),
            "Override_MRP": row_dict.get("Override_MRP"),
            "Override_Image_Path": row_dict.get("Override_Image_Path")
        })

    return results
