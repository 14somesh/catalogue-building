import os
import sys
import json
import uuid
import time
import sqlite3
import threading
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable, Set, Union
from contextlib import contextmanager

from src.utils.logger import setup_logger

logger = setup_logger("jobs")

DEFAULT_DB_PATH = "data/jobs.db"
SUPPORTED_JOB_TYPES = {"ingest", "confirm", "collect", "build", "retry", "rerun_product", "manual_source"}


class BrandLockedError(Exception):
    """Raised when an operation cannot be enqueued because a job for the brand is already active."""
    pass


@contextmanager
def get_db_connection(db_path: str = DEFAULT_DB_PATH):
    """Yields a SQLite connection configured with WAL mode, closing it upon exit."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ==================== LIVE SSE EVENT BROADCASTING ====================

_job_subscribers: Dict[str, List[asyncio.Queue]] = {}
_subscribers_lock = threading.Lock()
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Registers the running asyncio event loop for thread-safe event emission."""
    global _main_loop
    _main_loop = loop


def register_subscriber(job_id: str) -> asyncio.Queue:
    """Registers an async queue to receive real-time progress events for a job."""
    q = asyncio.Queue()
    with _subscribers_lock:
        if job_id not in _job_subscribers:
            _job_subscribers[job_id] = []
        _job_subscribers[job_id].append(q)
    return q


def unregister_subscriber(job_id: str, q: asyncio.Queue) -> None:
    """Unregisters an async queue."""
    with _subscribers_lock:
        if job_id in _job_subscribers and q in _job_subscribers[job_id]:
            _job_subscribers[job_id].remove(q)
            if not _job_subscribers[job_id]:
                del _job_subscribers[job_id]


def broadcast_job_event(job_id: str, event: Dict[str, Any]) -> None:
    """Broadcasts a progress or terminal event to all connected SSE clients."""
    with _subscribers_lock:
        queues = list(_job_subscribers.get(job_id, []))
    if not queues:
        return

    for q in queues:
        if _main_loop and _main_loop.is_running():
            try:
                _main_loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass
        else:
            try:
                q.put_nowait(event)
            except Exception:
                pass


# ==================== CANCELLATION TRACKING ====================

_cancelled_jobs: Set[str] = set()
_cancelled_jobs_lock = threading.Lock()


def is_job_cancellation_requested(job_id: str) -> bool:
    """Checks if a cancellation request has been registered for a running job."""
    with _cancelled_jobs_lock:
        return job_id in _cancelled_jobs


def request_job_cancellation(job_id: str, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """
    Cancels a queued job outright, or requests graceful cancellation for a running job
    after its current row completes execution.
    """
    with get_db_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, status, brand, job_type FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Job '{job_id}' not found.")

        current_status = row["status"]
        if current_status in ("done", "failed", "cancelled"):
            return {
                "job_id": job_id,
                "status": current_status,
                "message": f"Job '{job_id}' has already finished with status '{current_status}'."
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        if current_status == "queued":
            conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    error = 'Job cancelled by user before execution started.',
                    finished_at = ?
                WHERE id = ?
                """,
                (now_iso, job_id)
            )
            conn.commit()
            broadcast_job_event(job_id, {
                "stage": "terminal",
                "status": "cancelled",
                "product_id": None,
                "current": 0,
                "total": 0,
                "message": "Job cancelled by user before execution started."
            })
            logger.info(f"Queued job '{job_id}' was cancelled before start.")
            return {
                "job_id": job_id,
                "status": "cancelled",
                "message": "Queued job was cancelled immediately."
            }

        elif current_status == "running":
            with _cancelled_jobs_lock:
                _cancelled_jobs.add(job_id)
            conn.execute(
                """
                UPDATE jobs
                SET message = 'Cancellation requested by user. Halting after current row finishes.'
                WHERE id = ?
                """,
                (job_id,)
            )
            conn.commit()
            broadcast_job_event(job_id, {
                "stage": "cancel_requested",
                "status": "running",
                "product_id": None,
                "current": 0,
                "total": 0,
                "message": "Cancellation requested. Worker will halt gracefully after current row."
            })
            logger.info(f"Cancellation requested for running job '{job_id}'.")
            return {
                "job_id": job_id,
                "status": "cancel_requested",
                "message": "Cancellation requested. Worker will halt after the current row completes."
            }

    return {"job_id": job_id, "status": "unknown", "message": "Unable to determine cancellation state."}


# ==================== DATABASE INITIALIZATION ====================

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes the SQLite schema and performs startup crash recovery:
    any job left in 'running' is marked 'failed'.
    """
    with get_db_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                brand TEXT,
                status TEXT NOT NULL,
                progress_current INTEGER DEFAULT 0,
                progress_total INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                result_json TEXT,
                error TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_brand ON jobs(brand);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);")

        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN technical_details TEXT;")
        except sqlite3.OperationalError:
            pass

        # Startup crash recovery: mark stale running jobs as failed
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = 'Worker crashed or restarted while job was running',
                finished_at = ?
            WHERE status = 'running'
            """,
            (now_iso,)
        )
        if cur.rowcount > 0:
            logger.warning(f"Crash recovery: marked {cur.rowcount} stale 'running' job(s) as failed.")
        conn.commit()


def enqueue_job(
    job_type: str,
    brand: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH
) -> str:
    """
    Enqueues a job in the SQLite store with Brand Lock enforcement.
    Rejects the job if another job is already queued or running for the same brand.
    """
    if job_type not in SUPPORTED_JOB_TYPES:
        types_str = ", ".join(sorted(SUPPORTED_JOB_TYPES))
        raise ValueError(f"Unsupported job_type: '{job_type}'. Allowed types: {types_str}")

    norm_brand = brand.strip() if brand and brand.strip() else None

    with get_db_connection(db_path) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if norm_brand:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, status FROM jobs
                    WHERE LOWER(brand) = LOWER(?) AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (norm_brand,)
                )
                conflict = cur.fetchone()
                if conflict:
                    conn.execute("ROLLBACK")
                    raise BrandLockedError(
                        f"Brand '{norm_brand}' is locked by job '{conflict['id']}' (status: {conflict['status']})"
                    )

            now_iso = datetime.now(timezone.utc).isoformat()
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uid}"

            payload_str = json.dumps(payload) if payload else None

            conn.execute(
                """
                INSERT INTO jobs (id, job_type, brand, status, progress_current, progress_total, message, payload_json, created_at)
                VALUES (?, ?, ?, 'queued', 0, 0, '', ?, ?)
                """,
                (job_id, job_type, norm_brand, payload_str, now_iso)
            )
            conn.execute("COMMIT")
            logger.info(f"Enqueued job '{job_id}' (type={job_type}, brand={norm_brand})")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    # Signal worker
    _worker_event.set()
    return job_id


def get_job(job_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Returns a single job dictionary by ID, parsing result_json if present."""
    with get_db_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json"):
            try:
                d["result"] = json.loads(d["result_json"])
            except Exception:
                d["result"] = d["result_json"]
        else:
            d["result"] = None
        return d


def list_jobs(limit: int = 50, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Lists recent jobs ordered by creation timestamp descending."""
    with get_db_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("result_json"):
                try:
                    d["result"] = json.loads(d["result_json"])
                except Exception:
                    d["result"] = d["result_json"]
            else:
                d["result"] = None
            results.append(d)
        return results


def update_job_progress(
    job_id: str,
    current: int,
    total: int,
    message: str,
    stage: str = "progress",
    product_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Updates progress fields for a running job and broadcasts live SSE event."""
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET progress_current = ?,
                progress_total = ?,
                message = ?
            WHERE id = ?
            """,
            (current, total, message, job_id)
        )
        conn.commit()

    broadcast_job_event(job_id, {
        "stage": stage,
        "product_id": product_id,
        "current": current,
        "total": total,
        "message": message
    })


# ==================== SINGLE BACKGROUND WORKER ====================

_worker_thread: Optional[threading.Thread] = None
_worker_stop_event = threading.Event()
_worker_event = threading.Event()


def _execute_claimed_job(job: sqlite3.Row, db_path: str) -> None:
    """Executes a single claimed job to completion with live progress and cancellation checking."""
    job_id = job["id"]
    job_type = job["job_type"]
    brand = job["brand"]

    def progress_cb(event: Dict[str, Any]) -> None:
        try:
            update_job_progress(
                job_id=job_id,
                current=event.get("current", 0),
                total=event.get("total", 0),
                message=event.get("message", ""),
                stage=event.get("stage", "progress"),
                product_id=event.get("product_id"),
                db_path=db_path
            )
        except Exception as err:
            logger.warning(f"Failed to update progress for job '{job_id}': {err}")

    logger.info(f"Worker started executing job '{job_id}' (type={job_type}, brand={brand})")
    try:
        if job_type == "collect":
            from src.run_brand import run_brand
            if not brand:
                raise ValueError("Job type 'collect' requires a brand name.")

            result = run_brand(
                brand_name=brand,
                return_summary=True,
                progress_callback=progress_cb,
                check_cancellation=lambda: is_job_cancellation_requested(job_id)
            )

            if isinstance(result, dict) and result.get("cancelled"):
                now_iso = datetime.now(timezone.utc).isoformat()
                with get_db_connection(db_path) as conn:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'cancelled',
                            result_json = ?,
                            error = ?,
                            finished_at = ?
                        WHERE id = ?
                        """,
                        (json.dumps(result), result.get("halt_reason", "Job cancelled by user."), now_iso, job_id)
                    )
                    conn.commit()
                with _cancelled_jobs_lock:
                    _cancelled_jobs.discard(job_id)
                broadcast_job_event(job_id, {
                    "stage": "terminal",
                    "status": "cancelled",
                    "product_id": None,
                    "current": result.get("status_counts", {}).get("total_count", 0),
                    "total": result.get("status_counts", {}).get("total_count", 0),
                    "message": result.get("halt_reason", "Job cancelled by user."),
                    "result": result
                })
                logger.info(f"Job '{job_id}' gracefully cancelled.")
                return

            if isinstance(result, dict) and result.get("halt_reason"):
                raise RuntimeError(f"Collection halted: {result['halt_reason']}")

        elif job_type == "retry":
            from src.run_brand import re_run_product, derive_failure_reason
            from src.utils.excel_handler import load_catalogue_data_readonly

            if not brand:
                raise ValueError("Job type 'retry' requires a brand name.")

            payload = {}
            if "payload_json" in job.keys() and job["payload_json"]:
                try:
                    payload = json.loads(job["payload_json"])
                except Exception:
                    pass

            specified_pids = payload.get("product_ids") or []
            df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
            brand_mask = df["Brand"].astype(str).str.lower() == brand.lower()

            if specified_pids:
                spec_set = set(str(p).strip().lower() for p in specified_pids)
                target_df = df[brand_mask & df["Product_ID"].astype(str).str.lower().isin(spec_set)]
            else:
                target_df = df[brand_mask & df["Status"].isin(["Blocked", "Skipped", "Deferred"])]

            target_pids = target_df["Product_ID"].tolist()
            total = len(target_pids)
            retried_rows = []
            cancelled_early = False
            start_time = time.time()

            logger.info(f"Job '{job_id}' retrying {total} failed rows for brand '{brand}': {target_pids}")

            for idx, pid in enumerate(target_pids, 1):
                if is_job_cancellation_requested(job_id):
                    cancelled_early = True
                    break

                progress_cb({
                    "stage": "row_start",
                    "product_id": pid,
                    "current": idx,
                    "total": total,
                    "message": f"Retrying product {idx}/{total}: {pid}"
                })

                try:
                    updated_row = re_run_product(
                        product_id=pid,
                        progress_callback=lambda evt: progress_cb({
                            "stage": evt.get("stage", "row_progress"),
                            "product_id": pid,
                            "current": idx,
                            "total": total,
                            "message": f"[{pid}] {evt.get('message', '')}"
                        })
                    )
                    tier_val = updated_row.get("Tier_Title") or updated_row.get("Tier_Spec_Capacity") or 1
                    retried_rows.append({
                        "Product_ID": updated_row.get("Product_ID"),
                        "Model_Name": updated_row.get("Model_Name"),
                        "Display_Name": updated_row.get("Display_Name"),
                        "Status": updated_row.get("Status"),
                        "Source_URL": updated_row.get("Source_URL"),
                        "Tier": tier_val,
                        "Image_Status": updated_row.get("Image_Status", "missing"),
                        "Attempts": updated_row.get("Attempts", 0),
                        "Fix_Log": updated_row.get("Fix_Log"),
                        "Flags": updated_row.get("Flags"),
                        "Failure_Reason": derive_failure_reason(updated_row)
                    })
                except Exception as row_err:
                    logger.error(f"Failed to retry product '{pid}': {row_err}")

                progress_cb({
                    "stage": "row_done",
                    "product_id": pid,
                    "current": idx,
                    "total": total,
                    "message": f"Completed retry {idx}/{total}: {pid}"
                })

                if is_job_cancellation_requested(job_id):
                    cancelled_early = True
                    break

            runtime = round(time.time() - start_time, 2)
            counts = {
                "ready_count": sum(1 for r in retried_rows if r.get("Status") == "Ready_For_Review"),
                "approved_count": sum(1 for r in retried_rows if r.get("Status") == "Approved"),
                "blocked_count": sum(1 for r in retried_rows if r.get("Status") == "Blocked"),
                "skipped_count": sum(1 for r in retried_rows if r.get("Status") == "Skipped"),
                "deferred_count": sum(1 for r in retried_rows if r.get("Status") == "Deferred"),
                "total_count": len(retried_rows)
            }

            result = {
                "brand": brand,
                "runtime": runtime,
                "status_counts": counts,
                "rows": retried_rows,
                "retried_count": len(retried_rows),
                "total_targeted": total
            }

            if cancelled_early:
                now_iso = datetime.now(timezone.utc).isoformat()
                with get_db_connection(db_path) as conn:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'cancelled',
                            result_json = ?,
                            error = ?,
                            finished_at = ?
                        WHERE id = ?
                        """,
                        (json.dumps(result), "Job cancelled by user during retry execution.", now_iso, job_id)
                    )
                    conn.commit()
                with _cancelled_jobs_lock:
                    _cancelled_jobs.discard(job_id)
                broadcast_job_event(job_id, {
                    "stage": "terminal",
                    "status": "cancelled",
                    "product_id": None,
                    "current": len(retried_rows),
                    "total": total,
                    "message": "Retry job cancelled by user.",
                    "result": result
                })
                return

        elif job_type == "rerun_product":
            from src.run_brand import re_run_product, derive_failure_reason
            payload = {}
            if "payload_json" in job.keys() and job["payload_json"]:
                try:
                    payload = json.loads(job["payload_json"])
                except Exception:
                    pass

            pid = payload.get("product_id")
            if not pid:
                raise ValueError("Job type 'rerun_product' requires 'product_id'.")

            progress_cb({
                "stage": "row_start",
                "product_id": pid,
                "current": 1,
                "total": 1,
                "message": f"Starting single product re-run for {pid}"
            })

            updated_row = re_run_product(
                product_id=pid,
                progress_callback=lambda evt: progress_cb({
                    "stage": evt.get("stage", "row_progress"),
                    "product_id": pid,
                    "current": 1,
                    "total": 1,
                    "message": evt.get("message", "")
                })
            )

            tier_val = updated_row.get("Tier_Title") or updated_row.get("Tier_Spec_Capacity") or 1
            row_summary = {
                "Product_ID": updated_row.get("Product_ID"),
                "Model_Name": updated_row.get("Model_Name"),
                "Display_Name": updated_row.get("Display_Name"),
                "Status": updated_row.get("Status"),
                "Source_URL": updated_row.get("Source_URL"),
                "Tier": tier_val,
                "Image_Status": updated_row.get("Image_Status", "missing"),
                "Attempts": updated_row.get("Attempts", 0),
                "Fix_Log": updated_row.get("Fix_Log"),
                "Flags": updated_row.get("Flags"),
                "Failure_Reason": derive_failure_reason(updated_row)
            }

            result = {
                "product_id": pid,
                "brand": brand,
                "updated_row": row_summary
            }

        elif job_type == "manual_source":
            from src.run_brand import re_run_product, derive_failure_reason
            from src.utils.excel_handler import load_catalogue_data, save_catalogue_data

            payload = {}
            if "payload_json" in job.keys() and job["payload_json"]:
                try:
                    payload = json.loads(job["payload_json"])
                except Exception:
                    pass

            pid = payload.get("product_id")
            source_url = payload.get("url")
            if not pid or not source_url:
                raise ValueError("Job type 'manual_source' requires 'product_id' and 'url'.")

            progress_cb({
                "stage": "manual_source_set",
                "product_id": pid,
                "current": 1,
                "total": 1,
                "message": f"Setting Product_URL for {pid} to: {source_url}"
            })

            excel_path = "data/catalogue_data.xlsx"
            df = load_catalogue_data(excel_path)
            mask = df["Product_ID"].astype(str).str.lower() == pid.strip().lower()
            if not mask.any():
                raise ValueError(f"Product_ID '{pid}' not found in {excel_path}")
            df.loc[mask, "Product_URL"] = source_url
            save_catalogue_data(df, excel_path)

            progress_cb({
                "stage": "row_start",
                "product_id": pid,
                "current": 1,
                "total": 1,
                "message": f"Re-running product {pid} using provided manual URL"
            })

            updated_row = re_run_product(
                product_id=pid,
                progress_callback=lambda evt: progress_cb({
                    "stage": evt.get("stage", "row_progress"),
                    "product_id": pid,
                    "current": 1,
                    "total": 1,
                    "message": evt.get("message", "")
                })
            )

            tier_val = updated_row.get("Tier_Title") or updated_row.get("Tier_Spec_Capacity") or 1
            fail_reason = derive_failure_reason(updated_row)
            is_success = updated_row.get("Status") in ("Ready_For_Review", "Approved")

            row_summary = {
                "Product_ID": updated_row.get("Product_ID"),
                "Model_Name": updated_row.get("Model_Name"),
                "Display_Name": updated_row.get("Display_Name"),
                "Status": updated_row.get("Status"),
                "Source_URL": updated_row.get("Source_URL"),
                "Product_URL": source_url,
                "Tier": tier_val,
                "Image_Status": updated_row.get("Image_Status", "missing"),
                "Attempts": updated_row.get("Attempts", 0),
                "Fix_Log": updated_row.get("Fix_Log"),
                "Flags": updated_row.get("Flags"),
                "Failure_Reason": fail_reason
            }

            if is_success:
                msg = f"Collection succeeded with manual URL: {source_url}"
            else:
                msg = f"Collection attempted with manual URL '{source_url}', but failed: {fail_reason or updated_row.get('Flags')}"

            result = {
                "product_id": pid,
                "brand": brand,
                "manual_url": source_url,
                "success": is_success,
                "message": msg,
                "updated_row": row_summary
            }

        elif job_type == "build":
            import importlib
            import pypdfium2 as pdfium
            from src.utils.excel_handler import load_catalogue_data_readonly

            build_mod = importlib.import_module("src.4_build")
            out_pdf = build_mod.build_catalogue(
                brand=brand,
                progress_callback=progress_cb
            )
            clean_pdf_path = out_pdf.replace("\\", "/")
            url = f"/{clean_pdf_path}" if clean_pdf_path.startswith("dist/") else f"/dist/{os.path.basename(clean_pdf_path)}"
            file_size = os.path.getsize(out_pdf) if os.path.exists(out_pdf) else 0

            # Count pages using pypdfium2
            page_count = 0
            if os.path.exists(out_pdf):
                try:
                    pdf_doc = pdfium.PdfDocument(out_pdf)
                    page_count = len(pdf_doc)
                except Exception as err:
                    logger.warning(f"Failed to read page count for {out_pdf}: {err}")

            df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
            approved_mask = df["Status"] == "Approved"
            if brand:
                brand_mask = df["Brand"].astype(str).str.lower() == brand.lower()
                target_df = df[approved_mask & brand_mask]
                brand_count = 1 if not target_df.empty else 0
            else:
                target_df = df[approved_mask]
                brand_count = int(target_df["Brand"].nunique())

            product_count = len(target_df)

            result = {
                "pdf_path": clean_pdf_path,
                "url": url,
                "page_count": page_count,
                "product_count": product_count,
                "brand_count": brand_count,
                "file_size": file_size,
                "brand": brand
            }

        elif job_type == "ingest":
            from src.onboard_brand import extract_text_from_file, analyze_price_sheet, generate_onboarding_summary
            from src.run_brand import load_config
            cfg = load_config("config.yaml")
            llm_config = cfg.get("llm", {})
            category_name = cfg.get("category", {}).get("name", "powerbank")

            payload = {}
            if "payload_json" in job.keys() and job["payload_json"]:
                payload = json.loads(job["payload_json"])
            elif job["message"] and job["message"].startswith("{"):
                try:
                    payload = json.loads(job["message"])
                except Exception:
                    pass

            file_path = payload.get("file_path")
            pasted_text = payload.get("pasted_text")

            if file_path:
                progress_cb({"stage": "extract_text", "message": f"Extracting content from {os.path.basename(file_path)}..."})
                raw_content, fmt_type = extract_text_from_file(file_path, llm_config, progress_cb=progress_cb)
            elif pasted_text:
                raw_content = pasted_text
                fmt_type = "pasted_text"
            else:
                raise ValueError("Ingest job requires either 'file_path' or 'pasted_text'.")

            progress_cb({"stage": "analyze_sheet", "message": "Analyzing price sheet structure with AI..."})
            inference = analyze_price_sheet(raw_content, category_name=category_name, llm_config=llm_config, progress_cb=progress_cb)

            # Check duplicates
            seen = {}
            duplicates = []
            for idx, p in enumerate(inference.products, 1):
                key = (inference.brand_name.lower().strip(), p.model_name.lower().strip(), p.dp)
                if key in seen:
                    duplicates.append({
                        "row_index": idx,
                        "model_name": p.model_name,
                        "dp": p.dp,
                        "duplicates_row_index": seen[key],
                        "message": f"Row {idx} ('{p.model_name}', DP {p.dp}) duplicates Row {seen[key]}"
                    })
                else:
                    seen[key] = idx

            col_map_dict = {}
            if hasattr(inference, "column_mapping") and inference.column_mapping:
                for item in inference.column_mapping:
                    if hasattr(item, "column_name"):
                        col_map_dict[item.column_name] = item.role
                    elif isinstance(item, dict):
                        col_map_dict[item.get("column_name", "")] = item.get("role", "")

            qual_list = [
                q.model_dump() if hasattr(q, "model_dump") else (q if isinstance(q, dict) else {"token": str(q), "rationale": ""})
                for q in inference.qualifier_tokens
            ]

            result = {
                "brand_name": inference.brand_name,
                "brand_code": inference.brand_code,
                "domain": inference.domain,
                "platform": inference.platform,
                "dp_column_explanation": inference.dp_column_explanation,
                "column_mapping": col_map_dict,
                "qualifier_tokens": qual_list,
                "products": [p.model_dump() for p in inference.products],
                "duplicates": duplicates,
                "summary_markdown": generate_onboarding_summary(inference)
            }

        elif job_type == "confirm":
            from src.onboard_brand import (
                BrandInferenceSchema,
                RawProductItem,
                register_brand_config,
                append_products_to_catalogue
            )
            payload = {}
            if "payload_json" in job.keys() and job["payload_json"]:
                payload = json.loads(job["payload_json"])
            elif job["message"] and job["message"].startswith("{"):
                try:
                    payload = json.loads(job["message"])
                except Exception:
                    pass

            brand_name = payload.get("brand_name") or brand
            if not brand_name:
                raise ValueError("Confirm job requires brand_name.")

            brand_code = payload.get("brand_code")
            if not brand_code:
                brand_code = "".join([w[0] for w in brand_name.split() if w])[:4].upper()
                if len(brand_code) < 2:
                    brand_code = brand_name[:3].upper()

            domain = payload.get("domain", "")
            platform = payload.get("platform", "shopify")
            qualifier_tokens = payload.get("qualifier_tokens", [])
            column_mapping = payload.get("column_mapping", {})
            raw_rows = payload.get("rows", [])
            if not raw_rows:
                raise ValueError(f"No accepted product rows provided to confirm for brand '{brand_name}'.")

            progress_cb({"stage": "confirm", "message": f"Processing {len(raw_rows)} rows for brand '{brand_name}'..."})

            products = [
                RawProductItem(
                    raw_text=r.get("raw_text") or r.get("model_name", ""),
                    model_name=r.get("model_name", ""),
                    display_name=r.get("display_name") or r.get("model_name", ""),
                    dp=r.get("dp"),
                    mrp=r.get("mrp"),
                    notes=r.get("notes")
                )
                for r in raw_rows
            ]

            from src.onboard_brand import QualifierTokenItem, ColumnMappingItem
            qual_objs = []
            for q in qualifier_tokens:
                if isinstance(q, str):
                    qual_objs.append(QualifierTokenItem(token=q, rationale=""))
                elif isinstance(q, dict):
                    qual_objs.append(QualifierTokenItem(token=q.get("token", ""), rationale=q.get("rationale", "")))
                elif hasattr(q, "token"):
                    qual_objs.append(q)

            col_objs = []
            if isinstance(column_mapping, dict):
                for c_name, c_role in column_mapping.items():
                    col_objs.append(ColumnMappingItem(column_name=str(c_name), role=str(c_role)))
            elif isinstance(column_mapping, list):
                for c in column_mapping:
                    if isinstance(c, dict):
                        col_objs.append(ColumnMappingItem(column_name=c.get("column_name", ""), role=c.get("role", "")))
                    elif hasattr(c, "column_name"):
                        col_objs.append(c)

            inference = BrandInferenceSchema(
                brand_name=brand_name,
                brand_code=brand_code,
                domain=domain,
                platform=platform,
                qualifier_tokens=qual_objs,
                dp_column_explanation=payload.get("dp_column_explanation", "User confirmed pricing"),
                column_mapping=col_objs,
                products=products
            )

            progress_cb({"stage": "register_config", "message": f"Updating brand_defaults.yaml for '{brand_name}'..."})
            register_brand_config(inference)

            progress_cb({"stage": "append_excel", "message": f"Writing {len(products)} rows to catalogue_data.xlsx..."})
            count, created_rows = append_products_to_catalogue(inference)

            result = {
                "brand": brand_name,
                "count": count,
                "created_rows": created_rows
            }

        else:
            raise ValueError(f"Unknown job_type '{job_type}'")

        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection(db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'done',
                    result_json = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (json.dumps(result), now_iso, job_id)
            )
            conn.commit()

        with _cancelled_jobs_lock:
            _cancelled_jobs.discard(job_id)

        broadcast_job_event(job_id, {
            "stage": "terminal",
            "status": "done",
            "product_id": None,
            "current": 1,
            "total": 1,
            "message": "Job completed successfully.",
            "result": result
        })
        logger.info(f"Job '{job_id}' completed successfully.")

    except Exception as e:
        logger.error(f"Job '{job_id}' failed: {e}", exc_info=True)
        user_msg, tech_details = translate_job_error(e)
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection(db_path) as conn:
            try:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error = ?,
                        technical_details = ?,
                        finished_at = ?
                    WHERE id = ?
                    """,
                    (user_msg, tech_details, now_iso, job_id)
                )
            except sqlite3.OperationalError:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error = ?,
                        finished_at = ?
                    WHERE id = ?
                    """,
                    (user_msg, now_iso, job_id)
                )
            conn.commit()

        with _cancelled_jobs_lock:
            _cancelled_jobs.discard(job_id)

        broadcast_job_event(job_id, {
            "stage": "terminal",
            "status": "failed",
            "product_id": None,
            "current": 0,
            "total": 0,
            "message": user_msg,
            "error": user_msg,
            "technical_details": tech_details
        })


def translate_job_error(e: Exception) -> Tuple[str, str]:
    """
    Translates raw provider exceptions into user-friendly messages.
    Preserves raw stack and payloads in technical_details.
    """
    err_str = str(e)
    err_lower = err_str.lower()

    if hasattr(e, "user_message") and getattr(e, "user_message"):
        user_msg = getattr(e, "user_message")
        tech = getattr(e, "technical_details", err_str) or err_str
        return user_msg, tech

    # 1. 503 / Unavailable / High Demand
    if any(m in err_lower for m in ["503", "unavailable", "high demand", "overloaded", "spikes in demand"]):
        return "The AI service is unavailable. Try again in a few minutes.", err_str

    # 2. 429 / Quota / Rate limit
    if any(m in err_lower for m in ["429", "quota", "resource_exhausted", "rate limit", "ratelimit"]):
        return "The AI service request quota was reached. Please try again later.", err_str

    # 3. Timeout / Network
    if any(m in err_lower for m in ["timeout", "timed out", "deadlineexceeded", "connectionerror", "connection reset"]):
        return "The request timed out while contacting the AI service. Please try again.", err_str

    # 4. JSON / dict payloads from providers
    first_line = err_str.splitlines()[0] if err_str else "Unknown error occurred"
    if "{" in first_line and ("'error'" in first_line or '"error"' in first_line):
        return "The AI service encountered an error processing this file. Please try again.", err_str

    return first_line[:140], err_str


def _worker_loop(db_path: str) -> None:
    """Worker loop that claims jobs atomically one-at-a-time."""
    logger.info("Single Job Worker thread started.")
    while not _worker_stop_event.is_set():
        claimed_job = None
        try:
            with get_db_connection(db_path) as conn:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                        (now_iso, row["id"])
                    )
                    conn.execute("COMMIT")
                    claimed_job = row
                else:
                    conn.execute("COMMIT")
        except Exception as e:
            logger.error(f"Worker database claim error: {e}")
            time.sleep(1.0)
            continue

        if claimed_job:
            _execute_claimed_job(claimed_job, db_path)
        else:
            _worker_event.wait(timeout=1.0)
            _worker_event.clear()

    logger.info("Single Job Worker thread exiting.")


def start_worker(db_path: str = DEFAULT_DB_PATH) -> None:
    """Starts the single background worker thread if not already running."""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_stop_event.clear()
        _worker_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            name="CatalogueJobWorker",
            daemon=True
        )
        _worker_thread.start()
        logger.info("Started background catalogue job worker.")


def stop_worker() -> None:
    """Signals worker to stop and joins the worker thread."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        _worker_stop_event.set()
        _worker_event.set()
        _worker_thread.join(timeout=5.0)
        logger.info("Stopped background catalogue job worker.")
    _worker_thread = None
