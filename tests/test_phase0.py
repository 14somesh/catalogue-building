import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.jobs import (
    init_db,
    enqueue_job,
    get_job,
    list_jobs,
    BrandLockedError,
    get_db_connection
)
from src.api import app
from starlette.testclient import TestClient


def test_brand_lock_and_different_brands():
    """Verifies that duplicate active jobs for the same brand are rejected, while different brands queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "jobs_test.db")
        init_db(db_path)

        # Enqueue job 1 for BrandA
        job1_id = enqueue_job(job_type="collect", brand="BrandA", db_path=db_path)
        assert job1_id.startswith("job_")
        job1 = get_job(job1_id, db_path=db_path)
        assert job1["status"] == "queued"
        assert job1["brand"] == "BrandA"

        # Enqueue second job for same brand -> must be rejected with BrandLockedError
        failed_with_expected_error = False
        try:
            enqueue_job(job_type="collect", brand="BrandA", db_path=db_path)
        except BrandLockedError as exc:
            failed_with_expected_error = True
            assert f"Brand 'BrandA' is locked by job '{job1_id}'" in str(exc)
        assert failed_with_expected_error, "Expected BrandLockedError was not raised!"

        # Enqueue job for a DIFFERENT brand -> must succeed
        job2_id = enqueue_job(job_type="collect", brand="BrandB", db_path=db_path)
        assert job2_id.startswith("job_")
        job2 = get_job(job2_id, db_path=db_path)
        assert job2["status"] == "queued"
        assert job2["brand"] == "BrandB"

        # Enqueue build job without brand -> must succeed
        job3_id = enqueue_job(job_type="build", brand=None, db_path=db_path)
        assert job3_id.startswith("job_")


def test_crash_recovery_marks_running_jobs_failed():
    """Verifies that upon startup/restart, any job left in 'running' is marked failed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "jobs_crash_test.db")
        init_db(db_path)

        # Simulate a job that was left running when worker died
        with get_db_connection(db_path) as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO jobs (id, job_type, brand, status, created_at, started_at)
                VALUES ('job_stale_123', 'collect', 'Pebble', 'running', ?, ?)
                """,
                (now_iso, now_iso)
            )
            conn.commit()

        stale_job_before = get_job("job_stale_123", db_path=db_path)
        assert stale_job_before["status"] == "running"

        # Restart / re-initialize DB
        init_db(db_path)

        stale_job_after = get_job("job_stale_123", db_path=db_path)
        assert stale_job_after["status"] == "failed"
        assert "Worker crashed or restarted while job was running" in stale_job_after["error"]
        assert stale_job_after["finished_at"] is not None


def test_fastapi_endpoints():
    """Verifies FastAPI health, jobs listing, and brand lock rejection via HTTP."""
    with TestClient(app) as client:
        # 1. Health check
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        # 2. Enqueue fake job for BrandX
        res_post1 = client.post("/jobs/enqueue?job_type=collect&brand=BrandX")
        assert res_post1.status_code == 200
        job_data1 = res_post1.json()
        job1_id = job_data1["job_id"]

        # 3. Enqueue second job for BrandX -> must get 409 Conflict with clear error
        res_post2 = client.post("/jobs/enqueue?job_type=collect&brand=BrandX")
        assert res_post2.status_code == 409
        assert f"Brand 'BrandX' is locked by job '{job1_id}'" in res_post2.json()["detail"]

        # 4. Enqueue job for BrandY -> must get 200 OK
        res_post3 = client.post("/jobs/enqueue?job_type=collect&brand=BrandY")
        assert res_post3.status_code == 200

        # 5. Fetch job status
        res_job = client.get(f"/jobs/{job1_id}")
        assert res_job.status_code == 200
        assert res_job.json()["id"] == job1_id

        # 6. List jobs
        res_list = client.get("/jobs")
        assert res_list.status_code == 200
        assert len(res_list.json()) >= 2


if __name__ == "__main__":
    print("Running test_brand_lock_and_different_brands...")
    test_brand_lock_and_different_brands()
    print("PASS: Brand lock and multiple brands verified.")

    print("Running test_crash_recovery_marks_running_jobs_failed...")
    test_crash_recovery_marks_running_jobs_failed()
    print("PASS: Crash recovery verified.")

    print("Running test_fastapi_endpoints...")
    test_fastapi_endpoints()
    print("PASS: FastAPI endpoints verified.")

    print("\nALL PHASE 0 VERIFICATION TESTS PASSED SUCCESSFULLY!")
