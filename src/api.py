import os
import re
import io
import sys
import glob
import time
import uuid
import json
import yaml
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image
import pypdfium2 as pdfium

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
    get_active_job_for_brand,
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
from src.utils.validators import validate_row_deterministic
from src.run_brand import derive_failure_reason
from src.utils.logger import setup_logger
from src.title_measurer import get_title_measurer, stop_title_measurer

logger = setup_logger("api")

UPLOAD_DIR = "uploads"
BROCHURE_DIR = "brochures"
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB
ALLOWED_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".xlsx", ".xls", ".csv", ".txt"}
ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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
    stop_title_measurer()


app = FastAPI(
    title="Vianet Catalogue Engine API",
    version="3.0.0",
    description="API for autonomous catalogue ingestion, curation, live collection, approval editing, and PDF build pipeline.",
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
# Helper Functions
# ==============================================================================

def build_resolved_row_response(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Formats a product row for review and edit endpoints with full resolution chain."""
    prod = get_effective_product_dict(row_dict)
    pid = str(row_dict.get("Product_ID", "")).strip()

    title_val = prod.get("display_name")
    subtitle_val = prod.get("subtitle")
    bullets = prod.get("bullets", [])
    bullet_1 = bullets[0] if len(bullets) > 0 else None
    bullet_2 = bullets[1] if len(bullets) > 1 else None
    bullet_3 = bullets[2] if len(bullets) > 2 else None
    bullet_4 = bullets[3] if len(bullets) > 3 else None
    dp_val = prod.get("dp_raw") if prod.get("dp_raw") is not None else prod.get("dp")
    mrp_val = prod.get("mrp")

    img_full_path = prod.get("image_full_path") or ""
    clean_img_path = img_full_path.replace("\\", "/").lstrip("./")
    if clean_img_path.startswith("images/"):
        image_url = f"/{clean_img_path}"
    elif clean_img_path:
        image_url = f"/images/{clean_img_path}"
    else:
        image_url = None

    failure_reason = derive_failure_reason(row_dict)

    is_overridden = {
        "title": not is_empty_value(row_dict.get("Override_Title")),
        "subtitle": not is_empty_value(row_dict.get("Override_Subtitle")),
        "bullet_1": not is_empty_value(row_dict.get("Override_Bullet_1")),
        "bullet_2": not is_empty_value(row_dict.get("Override_Bullet_2")),
        "bullet_3": not is_empty_value(row_dict.get("Override_Bullet_3")),
        "bullet_4": not is_empty_value(row_dict.get("Override_Bullet_4")),
        "dp": not is_empty_value(row_dict.get("Override_DP")),
        "mrp": not is_empty_value(row_dict.get("Override_MRP")),
        "image": not is_empty_value(row_dict.get("Override_Image_Path"))
    }

    return {
        "product_id": pid,
        "model_name": row_dict.get("Model_Name"),
        "title": title_val,
        "subtitle": subtitle_val,
        "bullet_1": bullet_1,
        "bullet_2": bullet_2,
        "bullet_3": bullet_3,
        "bullet_4": bullet_4,
        "dp": dp_val,
        "mrp": mrp_val,
        "status": row_dict.get("Status", "Pending"),
        "source_url": row_dict.get("Source_URL"),
        "image_url": image_url,
        "image_status": row_dict.get("Image_Status", "missing"),
        "flags": row_dict.get("Flags"),
        "failure_reason": failure_reason,
        "is_overridden": is_overridden
    }


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
    Accepts multipart file upload (png, jpg, jpeg, webp, pdf, xlsx, xls, csv, txt).
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
    brand: Optional[str] = None
    category: Optional[str] = None


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
        "pasted_text": body.pasted_text.strip() if body.pasted_text else None,
        "brand": body.brand.strip() if body.brand and body.brand.strip() else None,
        "category": body.category.strip() if body.category and body.category.strip() else None
    }

    try:
        job_id = enqueue_job(job_type="ingest", brand=body.brand.strip() if body.brand else None, payload=payload)
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
    category: Optional[str] = "Powerbank"
    domain: Optional[str] = None
    platform: Optional[str] = None
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

    # Guard: Detect duplicate Display_Name across submitted rows
    seen_dns = set()
    dup_dns = set()
    for r in body.rows:
        dn = (r.display_name or r.model_name or "").strip().lower()
        if dn:
            if dn in seen_dns:
                dup_dns.add(r.display_name or r.model_name)
            else:
                seen_dns.add(dn)
    if dup_dns:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate Display_Name detected: {', '.join(sorted(dup_dns))}. Each product must have a unique Display_Name."
        )

    payload = {
        "brand_name": target_brand,
        "brand_code": body.brand_code,
        "category": (body.category or "Powerbank").strip(),
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
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(err),
                "brand": getattr(err, "brand", target_brand),
                "active_job_id": getattr(err, "active_job_id", None),
                "started_at": getattr(err, "started_at", None)
            }
        )
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
async def attach_brand_brochure(
    brand: str,
    category: Optional[str] = Form(None),
    file: UploadFile = File(...)
) -> Dict[str, Any]:
    """
    Accepts a PDF brochure upload for a brand and optional category.
    Saves to brochures/{category_slug}/{brand_slug}/{filename}.pdf and updates the Brochure_PDF column in Excel.
    """
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail=f"Brochure must be a PDF file. Got '{ext}'.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Brochure exceeds 25 MB limit.")

    brand_slug = slugify(clean_brand)
    cat_slug = slugify(clean_category) if clean_category else "powerbank"
    brand_brochure_dir = os.path.join(BROCHURE_DIR, cat_slug, brand_slug)
    os.makedirs(brand_brochure_dir, exist_ok=True)

    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
    dest_path = os.path.join(brand_brochure_dir, clean_name).replace("\\", "/")

    with open(dest_path, "wb") as f:
        f.write(contents)

    # Update Excel
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        try:
            os.remove(dest_path)
        except Exception:
            pass
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    rel_path = dest_path
    df.loc[mask, "Brochure_PDF"] = rel_path
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    logger.info(f"Attached brochure '{rel_path}' to {mask.sum()} rows for brand '{clean_brand}' (category='{clean_category}').")
    return {
        "brand": clean_brand,
        "category": clean_category,
        "brochure_path": rel_path,
        "filename": clean_name,
        "attached_count": int(mask.sum())
    }


@app.get("/brands/{brand}/brochure")
def get_brand_brochure(
    brand: str,
    category: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Returns the currently attached brochure path for a brand and optional category."""
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    brochure_vals = df.loc[mask, "Brochure_PDF"].dropna()
    current_path = str(brochure_vals.iloc[0]).strip() if not brochure_vals.empty else None
    return {
        "brand": clean_brand,
        "category": clean_category,
        "brochure_path": current_path if current_path and not is_empty_value(current_path) else None
    }


@app.delete("/brands/{brand}/brochure")
def delete_brand_brochure(
    brand: str,
    category: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Detaches and removes the brochure for a brand and optional category."""
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

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

    logger.info(f"Detached brochure for brand '{clean_brand}' (category='{clean_category}').")
    return {
        "brand": clean_brand,
        "category": clean_category,
        "brochure_path": None,
        "message": f"Brochure detached successfully from brand '{clean_brand}'."
    }


# ==============================================================================
# 5. Stage 3 Collection Endpoints (Live Progress & Retries)
# ==============================================================================

@app.post("/brands/{brand}/collect")
def start_brand_collection(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter"),
    semantic_audit: bool = Query(False, description="Enable optional post-collection LLM semantic audit")
) -> Dict[str, Any]:
    """
    Enqueues a 'collect' job for the specified brand and optional category. Respects Brand Lock.
    Writes live progress to the jobs store as each row is processed.
    Returns job_id immediately without blocking.
    """
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    try:
        job_id = enqueue_job(
            job_type="collect",
            brand=clean_brand,
            payload={"semantic_audit": semantic_audit, "category": clean_category}
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "brand": clean_brand,
            "category": clean_category,
            "stream_url": f"/jobs/{job_id}/stream"
        }
    except BrandLockedError as err:
        active_id = getattr(err, "active_job_id", None)
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(err),
                "brand": getattr(err, "brand", clean_brand),
                "active_job_id": active_id,
                "stream_url": f"/jobs/{active_id}/stream" if active_id else None,
                "started_at": getattr(err, "started_at", None)
            }
        )


@app.get("/brands/{brand}/active-job")
def get_brand_active_job(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter")
) -> Dict[str, Any]:
    """Returns any active (queued or running) job for the brand (and optional category) to enable stream reattachment."""
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    active_job = get_active_job_for_brand(clean_brand, category=clean_category)
    if active_job:
        return {
            "active": True,
            "job_id": active_job["id"],
            "job_type": active_job.get("job_type"),
            "status": active_job.get("status"),
            "brand": active_job.get("brand"),
            "category": active_job.get("category"),
            "created_at": active_job.get("created_at"),
            "stream_url": f"/jobs/{active_job['id']}/stream"
        }
    return {"active": False, "job": None}


class RetryBrandRequest(BaseModel):
    category: Optional[str] = Field(None, description="Optional category filter")
    product_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of specific Product_IDs to retry. If omitted, retries all Blocked, Skipped, and Deferred rows for the brand."
    )


@app.post("/brands/{brand}/retry")
def retry_failed_rows(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter"),
    body: Optional[RetryBrandRequest] = None
) -> Dict[str, Any]:
    """
    Enqueues a 'retry' job for failed (Blocked, Skipped, Deferred) rows of a brand.
    Uses re_run_product from scratch for each row with Attempts reset.
    Respects Brand Lock and reports the identical per-row result structure as collect.
    """
    clean_brand = brand.strip()
    target_category = (body.category if body and body.category else category)
    clean_category = target_category.strip() if target_category and target_category.strip() else None
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    payload = {
        "product_ids": body.product_ids if body and body.product_ids else None,
        "category": clean_category
    }
    try:
        job_id = enqueue_job(job_type="retry", brand=clean_brand, payload=payload)
        return {
            "job_id": job_id,
            "status": "queued",
            "brand": clean_brand,
            "category": clean_category,
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
# 6. Stage 4 Approval, Review & Edit Endpoints
# ==============================================================================

@app.get("/brands/{brand}/review")
def get_brand_review_data(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter")
) -> List[Dict[str, Any]]:
    """
    Returns everything the approval screen needs per row:
    Product_ID, Model_Name, resolved card title, subtitle, the 4 bullets, DP, MRP,
    Status, Source_URL, image URL servable from /images mount, Image_Status, Flags,
    and derived plain-language failure reason.
    Every field is passed through get_effective_value so overrides are reflected.
    Includes is_overridden dictionary indicating which fields were manually edited.
    Does NOT include specs (specs are not rendered on the card).
    """
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    brand_df = df[mask].sort_values(by="Product_ID", ascending=True)
    results = []
    for _, row in brand_df.iterrows():
        row_dict = row.to_dict()
        results.append(build_resolved_row_response(row_dict))
    return results


class TitleValidateRequest(BaseModel):
    title: str = Field(..., description="Title to measure against the card container")


@app.post("/validate/title")
@app.post("/products/validate-title")
def validate_title_endpoint(body: TitleValidateRequest) -> Dict[str, Any]:
    """Measures rendered title width in Chromium against 291px container."""
    measurer = get_title_measurer()
    return measurer.measure_title(body.title)


class EditProductRequest(BaseModel):
    title: Optional[Union[str, None]] = None
    subtitle: Optional[Union[str, None]] = None
    bullet_1: Optional[Union[str, None]] = None
    bullet_2: Optional[Union[str, None]] = None
    bullet_3: Optional[Union[str, None]] = None
    bullet_4: Optional[Union[str, None]] = None
    dp: Optional[Union[float, int, None]] = None
    mrp: Optional[Union[float, int, None]] = None


@app.patch("/products/{product_id}")
def edit_product_row(product_id: str, body: EditProductRequest) -> Dict[str, Any]:
    """
    Edits manual overrides for a row: title, subtitle, bullets 1-4, DP, MRP.
    Writes to Override_Title, Override_Subtitle, Override_Bullet_1..4, Override_DP, Override_MRP.
    Sending null for a field clears that override and falls back to the collected value.
    Enforces identical card layout limits:
      - title: measured rendered width against 291px container (reusing Rule 5)
      - subtitle: max 80 characters (max 2 lines)
      - bullet_1..4: max 60 characters (max 3 lines)
    Returns the row's resolved values after edit.
    """
    clean_pid = product_id.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    fields_set = body.model_fields_set

    # 1. Title (Real width measurement against 291px card container)
    if "title" in fields_set:
        if body.title is None or str(body.title).strip() == "":
            df.loc[mask, "Override_Title"] = None
        else:
            val = str(body.title).strip()
            measurer = get_title_measurer()
            m_res = measurer.measure_title(val)
            if m_res["overflow"]:
                diff_px = round(m_res["diff"])
                raise HTTPException(
                    status_code=400,
                    detail=f"Title will not fit the card: rendered width ({round(m_res['textWidth'])}px) exceeds the available container width ({round(m_res['containerWidth'])}px) by {diff_px}px. Please shorten the title to fit."
                )
            df.loc[mask, "Override_Title"] = val

    # 2. Subtitle (Limit: 80 characters)
    if "subtitle" in fields_set:
        if body.subtitle is None or str(body.subtitle).strip() == "":
            df.loc[mask, "Override_Subtitle"] = None
        else:
            val = str(body.subtitle).strip()
            if len(val) > 80:
                raise HTTPException(
                    status_code=400,
                    detail=f"Subtitle exceeds 80-character limit ({len(val)} characters given): '{val}'"
                )
            df.loc[mask, "Override_Subtitle"] = val

    # 3. Bullets 1..4 (Limit: 60 characters each)
    for b_idx in range(1, 5):
        key = f"bullet_{b_idx}"
        if key in fields_set:
            val = getattr(body, key)
            if val is None or str(val).strip() == "":
                df.loc[mask, f"Override_Bullet_{b_idx}"] = None
            else:
                b_str = str(val).strip()
                if len(b_str) > 60:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Bullet_{b_idx} exceeds 60-character wrap limit ({len(b_str)} characters given): '{b_str}'"
                    )
                df.loc[mask, f"Override_Bullet_{b_idx}"] = b_str

    # 4. DP (Dealer Price)
    if "dp" in fields_set:
        if body.dp is None:
            df.loc[mask, "Override_DP"] = None
        else:
            try:
                num = float(body.dp)
                if num < 0:
                    raise ValueError
                df.loc[mask, "Override_DP"] = num
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid DP value '{body.dp}'. Must be a non-negative number."
                )

    # 5. MRP (Maximum Retail Price)
    if "mrp" in fields_set:
        if body.mrp is None:
            df.loc[mask, "Override_MRP"] = None
        else:
            try:
                num = float(body.mrp)
                if num < 0:
                    raise ValueError
                df.loc[mask, "Override_MRP"] = num
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid MRP value '{body.mrp}'. Must be a non-negative number."
                )

    save_catalogue_data(df, "data/catalogue_data.xlsx")
    updated_row = df.loc[mask].iloc[0].to_dict()
    return build_resolved_row_response(updated_row)


@app.post("/products/{product_id}/image")
async def upload_product_image_override(product_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Accepts an uploaded image file for a product.
    Validates:
      - Valid image format
      - At least 1200x1200 resolution
      - Square aspect ratio (pads to 1:1 on white background if rectangular)
    Saves image to images/overrides/{brand_slug}/{model_slug}.png.
    Sets Override_Image_Path and Image_Status = 'ok' in catalogue data.
    """
    clean_pid = product_id.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMG_EXTS:
        allowed_str = ", ".join(sorted(ALLOWED_IMG_EXTS))
        raise HTTPException(
            status_code=400,
            detail=f"Image extension '{ext}' not allowed. Allowed image formats: {allowed_str}"
        )

    contents = await file.read()
    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()
        # Re-open for operations after verify
        img = Image.open(io.BytesIO(contents))
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Uploaded file is corrupted or not a valid image: {err}")

    w, h = img.size
    if w < 1200 or h < 1200:
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution ({w}x{h}px) is below the required 1200x1200px minimum. Upload a high-resolution image."
        )

    # Pad to 1:1 square on white background if rectangular
    max_dim = max(w, h)
    if w == h:
        final_img = img.convert("RGB")
    else:
        padded = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
        offset_x = (max_dim - w) // 2
        offset_y = (max_dim - h) // 2
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            final_img = padded
            final_img.paste(img.convert("RGBA"), (offset_x, offset_y), img.convert("RGBA"))
        else:
            final_img = padded
            final_img.paste(img.convert("RGB"), (offset_x, offset_y))

    brand_val = str(df.loc[mask, "Brand"].iloc[0]).strip()
    model_val = str(df.loc[mask, "Model_Name"].iloc[0]).strip()
    brand_slug = slugify(brand_val)
    model_slug = slugify(model_val)

    dest_dir = os.path.join("images", "overrides", brand_slug)
    os.makedirs(dest_dir, exist_ok=True)
    saved_filename = f"{model_slug}.png"
    dest_path = os.path.join(dest_dir, saved_filename).replace("\\", "/")

    final_img.save(dest_path, format="PNG")

    df.loc[mask, "Override_Image_Path"] = dest_path
    df.loc[mask, "Image_Status"] = "ok"
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    logger.info(f"Saved image override for {clean_pid} to {dest_path} ({max_dim}x{max_dim}px)")
    return {
        "product_id": clean_pid,
        "override_image_path": dest_path,
        "image_url": f"/{dest_path}",
        "width": max_dim,
        "height": max_dim,
        "message": f"Image override uploaded and validated successfully ({max_dim}x{max_dim}px)."
    }


@app.delete("/products/{product_id}/image")
def delete_product_image_override(product_id: str) -> Dict[str, Any]:
    """
    Drops the image override for a product and falls back to the collected image asset.
    """
    clean_pid = product_id.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    current_override = df.loc[mask, "Override_Image_Path"].iloc[0]
    df.loc[mask, "Override_Image_Path"] = None
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    # Optionally clean up override file from disk if it was in images/overrides/
    if current_override and not is_empty_value(current_override):
        override_file = str(current_override).strip()
        if "overrides" in override_file and os.path.exists(override_file):
            try:
                os.remove(override_file)
            except Exception:
                pass

    logger.info(f"Dropped image override for {clean_pid}.")
    return {
        "product_id": clean_pid,
        "override_image_path": None,
        "message": "Image override removed. Reverted to collected image."
    }


@app.post("/products/{product_id}/approve")
def approve_single_product(product_id: str) -> Dict[str, Any]:
    """
    Sets Status = 'Approved' for a single product.
    Allows approval even with unresolved hard flags (since the user is final authority),
    but clearly reports any active flags in the response.
    """
    clean_pid = product_id.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    row_dict = df.loc[mask].iloc[0].to_dict()
    row_dict["Status"] = "Approved"
    is_passed, hard_flags, warnings = validate_row_deterministic(row_dict)

    df.loc[mask, "Status"] = "Approved"
    save_catalogue_data(df, "data/catalogue_data.xlsx")

    msg = f"Product {clean_pid} approved successfully."
    if hard_flags:
        msg = f"Product {clean_pid} approved with unresolved hard flags: {', '.join(hard_flags)}"

    return {
        "product_id": clean_pid,
        "status": "Approved",
        "has_hard_flags": bool(hard_flags),
        "hard_flags": hard_flags,
        "warnings": warnings,
        "message": msg
    }


@app.post("/products/{product_id}/skip")
def skip_single_product(product_id: str) -> Dict[str, Any]:
    """Sets Status = 'Skipped' for a single product."""
    clean_pid = product_id.strip()
    df = load_catalogue_data("data/catalogue_data.xlsx")
    mask = df["Product_ID"].astype(str).str.lower() == clean_pid.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Product_ID '{clean_pid}' not found in catalogue data.")

    df.loc[mask, "Status"] = "Skipped"
    save_catalogue_data(df, "data/catalogue_data.xlsx")
    return {
        "product_id": clean_pid,
        "status": "Skipped",
        "message": f"Product {clean_pid} marked as Skipped."
    }


class BrandApproveRequest(BaseModel):
    category: Optional[str] = Field(None, description="Optional category filter")
    product_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of specific product_ids to approve. If omitted, approves all rows currently 'Ready_For_Review' for the brand."
    )


@app.post("/brands/{brand}/approve")
def approve_brand_products(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter"),
    body: Optional[BrandApproveRequest] = None
) -> Dict[str, Any]:
    """
    Approves products for a brand (and optional category).
    If product_ids is omitted: approves every row currently 'Ready_For_Review' for that brand/category.
    If product_ids is provided: approves the specified products.
    Reports which rows changed and which were left alone and why.
    """
    clean_brand = brand.strip()
    target_category = (body.category if body and body.category else category)
    clean_category = target_category.strip() if target_category and target_category.strip() else None
    df = load_catalogue_data("data/catalogue_data.xlsx")
    brand_mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        brand_mask = brand_mask & (df["Category"].astype(str).str.strip().str.lower() == clean_category.lower())
    if not brand_mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    specified_ids = set(str(p).strip().lower() for p in body.product_ids) if body and body.product_ids else None
    changed_rows = []
    unchanged_rows = []

    for idx in df[brand_mask].index:
        r_dict = df.loc[idx].to_dict()
        pid = str(r_dict.get("Product_ID", "")).strip()
        current_status = str(r_dict.get("Status", "Pending")).strip()

        should_approve = False
        skip_reason = None

        if specified_ids is not None:
            if pid.lower() in specified_ids:
                should_approve = True
            else:
                skip_reason = "Product_ID not included in approve request list"
        else:
            if current_status == "Ready_For_Review":
                should_approve = True
            else:
                skip_reason = f"Current status is '{current_status}', not 'Ready_For_Review'"

        if should_approve:
            df.at[idx, "Status"] = "Approved"
            test_dict = dict(r_dict)
            test_dict["Status"] = "Approved"
            _, hard_flags, _ = validate_row_deterministic(test_dict)
            changed_rows.append({
                "product_id": pid,
                "model_name": r_dict.get("Model_Name"),
                "from_status": current_status,
                "to_status": "Approved",
                "hard_flags": hard_flags
            })
        else:
            unchanged_rows.append({
                "product_id": pid,
                "model_name": r_dict.get("Model_Name"),
                "status": current_status,
                "reason": skip_reason
            })

    if changed_rows:
        save_catalogue_data(df, "data/catalogue_data.xlsx")

    return {
        "brand": clean_brand,
        "category": clean_category,
        "approved_count": len(changed_rows),
        "changed_rows": changed_rows,
        "unchanged_rows": unchanged_rows
    }


# ==============================================================================
# 7. Stage 5 Build & Outputs Endpoints
# ==============================================================================

class BuildRequest(BaseModel):
    brand: Optional[str] = Field(None, description="Optional brand name to compile single-brand catalogue")
    category: Optional[str] = Field(None, description="Optional category filter to compile category catalogue")
    brand_order: Optional[List[str]] = Field(None, description="Optional explicit brand order list for combined catalogue")


@app.post("/build")
def trigger_catalogue_build(body: Optional[BuildRequest] = None) -> Dict[str, Any]:
    """
    Enqueues a 'build' job using build_catalogue with progress tracking.
    If brand is provided: compiles single-brand PDF.
    If category is provided: compiles single-category PDF.
    If brand_order is provided: updates config.yaml so the order persists, then compiles combined PDF.
    If empty: compiles default combined PDF.
    Returns job_id immediately.
    """
    brand_target = body.brand.strip() if body and body.brand and body.brand.strip() else None
    category_target = body.category.strip() if body and body.category and body.category.strip() else None
    brand_order = body.brand_order if body and body.brand_order else None

    # Update config.yaml if brand_order is provided
    if brand_order:
        config_path = "config.yaml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["brand_order"] = brand_order
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)
            logger.info(f"Updated config.yaml brand_order: {brand_order}")
        except Exception as err:
            logger.error(f"Failed to update config.yaml brand_order: {err}")
            raise HTTPException(status_code=500, detail=f"Failed to update config.yaml brand_order: {err}")

    try:
        job_id = enqueue_job(
            job_type="build",
            brand=brand_target,
            category=category_target,
            payload={"brand": brand_target, "category": category_target, "brand_order": brand_order}
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "brand": brand_target,
            "category": category_target,
            "stream_url": f"/jobs/{job_id}/stream"
        }
    except BrandLockedError as err:
        active_id = getattr(err, "active_job_id", None)
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(err),
                "brand": getattr(err, "brand", brand_target or "combined"),
                "active_job_id": active_id,
                "stream_url": f"/jobs/{active_id}/stream" if active_id else None,
                "started_at": getattr(err, "started_at", None)
            }
        )


@app.get("/config")
def get_catalogue_config() -> Dict[str, Any]:
    """Returns current config.yaml settings including brand_order."""
    config_path = "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return {
            "brand_order": cfg.get("brand_order", []),
            "category": cfg.get("category", {}),
            "validation_rules": cfg.get("validation_rules", {})
        }
    except Exception as e:
        logger.error(f"Failed to read config.yaml: {e}")
        return {"brand_order": [], "category": {}, "validation_rules": {}}


@app.get("/categories")
def list_categories() -> List[str]:
    """Returns available product categories from config.yaml."""
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cat_name = cfg.get("category", {}).get("name", "Power Bank")
        return [cat_name]
    except Exception:
        return ["Power Bank"]


@app.get("/builds")
def list_built_catalogues() -> List[Dict[str, Any]]:
    """
    Lists all built PDFs in dist/ with brand, page count, size, and timestamp,
    newest first, so the UI can offer the latest and history.
    """
    pdf_files = glob.glob("dist/**/*.pdf", recursive=True)
    results = []

    for p in pdf_files:
        clean_p = p.replace("\\", "/")
        try:
            st = os.stat(p)
            file_size = st.st_size
            mtime = st.st_mtime
            ts_iso = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        except Exception:
            continue

        # Extract brand label from path: dist/{category}/{brand}/*.pdf or dist/{category}/combined/*.pdf
        parts = clean_p.split("/")
        brand_label = "Combined"
        if len(parts) >= 4:
            sub = parts[-2]
            if sub.lower() == "combined":
                brand_label = "Combined"
            else:
                brand_label = sub.capitalize()

        page_count = 0
        try:
            pdf_doc = pdfium.PdfDocument(p)
            page_count = len(pdf_doc)
        except Exception:
            pass

        url = f"/{clean_p}"
        results.append({
            "filename": os.path.basename(clean_p),
            "path": clean_p,
            "url": url,
            "brand": brand_label,
            "page_count": page_count,
            "size": file_size,
            "timestamp": ts_iso,
            "_mtime": mtime
        })

    results.sort(key=lambda x: x["_mtime"], reverse=True)
    for r in results:
        del r["_mtime"]
    return results


# ==============================================================================
# 8. Read Endpoints: GET /brands & GET /brands/{brand}/rows
# ==============================================================================

@app.get("/brands")
def list_brands(
    category: Optional[str] = Query(None, description="Optional category filter")
) -> List[Dict[str, Any]]:
    """
    Lists all brands in the catalogue with row counts grouped by status, per category.
    If category is provided, filters to that category.
    Uses non-blocking read snapshot so it never locks or blocks jobs.
    """
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    if df.empty:
        return []

    clean_category = category.strip() if category and category.strip() else None
    if clean_category:
        df = df[df["Category"].astype(str).str.strip().str.lower() == clean_category.lower()]
        if df.empty:
            return []

    if "Category" not in df.columns:
        df["Category"] = "Powerbank"

    # Group by (Brand, Category) preserving original appearance order
    brand_list = []
    for (brand_name, cat_name), b_df in df.groupby(["Brand", "Category"], sort=False):
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
            "category": str(cat_name),
            "total_rows": len(b_df),
            "status_counts": status_counts,
            "brochure_path": brochure_path if brochure_path and not is_empty_value(brochure_path) else None
        })

    return brand_list


@app.get("/categories")
def list_categories() -> List[str]:
    """
    Lists distinct categories currently present in the catalogue data.
    """
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    if df.empty or "Category" not in df.columns:
        return ["Powerbank"]
    cats = sorted(list(set(str(c).strip() for c in df["Category"].dropna() if str(c).strip())))
    return cats if cats else ["Powerbank"]


@app.get("/brands/{brand}/rows")
def get_brand_rows(
    brand: str,
    category: Optional[str] = Query(None, description="Optional category filter")
) -> List[Dict[str, Any]]:
    """
    Returns all product rows for a brand with resolved display fields needed by the UI:
    Product_ID, Model_Name, Display_Name, Status, DP, MRP, Source_URL, Image_Status, Flags.
    Uses non-blocking read-only snapshot.
    """
    clean_brand = brand.strip()
    clean_category = category.strip() if category and category.strip() else None
    df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
    mask = df["Brand"].astype(str).str.lower() == clean_brand.lower()
    if clean_category:
        mask = mask & (df["Category"].astype(str).str.lower() == clean_category.lower())
    if not mask.any():
        detail_msg = f"Brand '{clean_brand}'" + (f" (category '{clean_category}')" if clean_category else "") + " not found in catalogue data."
        raise HTTPException(status_code=404, detail=detail_msg)

    brand_df = df[mask].sort_values(by="Product_ID", ascending=True)
    results = []

    for _, row in brand_df.iterrows():
        row_dict = row.to_dict()
        prod = get_effective_product_dict(row_dict)

        dp_val = prod.get("dp_raw") if prod.get("dp_raw") is not None else prod.get("dp")
        mrp_val = prod.get("mrp")
        failure_reason = derive_failure_reason(row_dict)

        results.append({
            "product_id": row_dict.get("Product_ID"),
            "model_name": row_dict.get("Model_Name"),
            "display_name": prod.get("display_name"),
            "status": row_dict.get("Status", "Pending"),
            "dp": dp_val,
            "mrp": mrp_val,
            "source_url": row_dict.get("Source_URL"),
            "image_status": row_dict.get("Image_Status", "missing"),
            "flags": row_dict.get("Flags"),
            "failure_reason": failure_reason,
            "attempts": row_dict.get("Attempts", 0),
            "category": row_dict.get("Category", "Powerbank"),
            "brochure_pdf": row_dict.get("Brochure_PDF"),
            "override_title": row_dict.get("Override_Title"),
            "override_dp": row_dict.get("Override_DP"),
            "override_mrp": row_dict.get("Override_MRP"),
            "override_image_path": row_dict.get("Override_Image_Path")
        })

    return results
