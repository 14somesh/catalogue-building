import os
import sys
import json
import uuid
import time
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable

from src.utils.logger import setup_logger

from contextlib import contextmanager

logger = setup_logger("jobs")

DEFAULT_DB_PATH = "data/jobs.db"


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
    if job_type not in ("ingest", "confirm", "collect", "build"):
        raise ValueError(f"Unsupported job_type: '{job_type}'. Must be ingest, confirm, collect, or build.")

    norm_brand = brand.strip() if brand and brand.strip() else None

    with get_db_connection(db_path) as conn:
        # Atomic lock check using IMMEDIATE transaction
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

            # Store payload in payload_json
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
    """Fetches a job record by ID."""
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
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Updates progress fields for a running job."""
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


# ==================== SINGLE BACKGROUND WORKER ====================

_worker_thread: Optional[threading.Thread] = None
_worker_stop_event = threading.Event()
_worker_event = threading.Event()


def _execute_claimed_job(job: sqlite3.Row, db_path: str) -> None:
    """Executes a single claimed job to completion."""
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
                progress_callback=progress_cb
            )
            if isinstance(result, dict) and result.get("halt_reason"):
                raise RuntimeError(f"Collection halted: {result['halt_reason']}")

        elif job_type == "build":
            import importlib
            build_mod = importlib.import_module("src.4_build")
            out_pdf = build_mod.build_catalogue(
                brand=brand,
                progress_callback=progress_cb
            )
            result = {"pdf_path": out_pdf, "brand": brand}

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
                raw_content, fmt_type = extract_text_from_file(file_path, llm_config)
            elif pasted_text:
                raw_content = pasted_text
                fmt_type = "pasted_text"
            else:
                raise ValueError("Ingest job requires either 'file_path' or 'pasted_text'.")

            progress_cb({"stage": "analyze_sheet", "message": "Analyzing price sheet structure with AI..."})
            inference = analyze_price_sheet(raw_content, category_name=category_name, llm_config=llm_config)

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
        logger.info(f"Job '{job_id}' completed successfully.")

    except Exception as e:
        logger.error(f"Job '{job_id}' failed: {e}", exc_info=True)
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection(db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (str(e), now_iso, job_id)
            )
            conn.commit()


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
            # Wait for next enqueue event or timeout
            _worker_event.wait(timeout=1.0)
            _worker_event.clear()

    logger.info("Single Job Worker thread exiting.")


def start_worker(db_path: str = DEFAULT_DB_PATH) -> None:
    """Starts the single background worker thread if not already running."""
    global _worker_thread
    init_db(db_path)
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_stop_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            daemon=True,
            name="CatalogueSingleWorker"
        )
        _worker_thread.start()
        logger.info("Started background catalogue job worker.")


def stop_worker(timeout: float = 5.0) -> None:
    """Signals the worker to stop cleanly."""
    global _worker_thread
    _worker_stop_event.set()
    _worker_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=timeout)
        logger.info("Stopped background catalogue job worker.")
    _worker_thread = None
