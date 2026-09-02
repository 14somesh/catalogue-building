import time
import requests
import subprocess
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.jobs import get_db_connection

PORT = 8011
BASE_URL = f"http://127.0.0.1:{PORT}"

def main():
    print(f"Starting uvicorn server on port {PORT}...")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app", "--port", str(PORT), "--host", "127.0.0.1", "--log-level", "warning"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    try:
        # 1. Wait for server to become healthy
        connected = False
        for _ in range(30):
            try:
                r = requests.get(f"{BASE_URL}/health", timeout=1.0)
                if r.status_code == 200 and r.json() == {"status": "ok"}:
                    connected = True
                    break
            except Exception:
                time.sleep(0.2)
        
        assert connected, "Failed to connect to FastAPI server!"
        print("  ✅ Server is healthy: GET /health -> {'status': 'ok'}")

        # 2. Enqueue two fake jobs for the same brand -> confirm second is rejected with 409
        print("Testing brand lock on live API...")
        r1 = requests.post(f"{BASE_URL}/jobs/enqueue?job_type=collect&brand=BrandLockTest")
        assert r1.status_code == 200, f"Expected 200, got {r1.status_code}: {r1.text}"
        job1_id = r1.json()["job_id"]
        print(f"  ✅ Enqueued job 1: {job1_id}")

        r2 = requests.post(f"{BASE_URL}/jobs/enqueue?job_type=collect&brand=BrandLockTest")
        assert r2.status_code == 409, f"Expected 409 Conflict, got {r2.status_code}: {r2.text}"
        print(f"  ✅ Job 2 rejected as expected: {r2.json()['detail']}")

        # 3. Enqueue jobs for two different brands -> confirm both queue
        print("Testing queueing for different brands...")
        ra = requests.post(f"{BASE_URL}/jobs/enqueue?job_type=collect&brand=AlphaBrand")
        rb = requests.post(f"{BASE_URL}/jobs/enqueue?job_type=collect&brand=BetaBrand")
        assert ra.status_code == 200, f"Expected 200 for AlphaBrand, got {ra.status_code}"
        assert rb.status_code == 200, f"Expected 200 for BetaBrand, got {rb.status_code}"
        print(f"  ✅ Both AlphaBrand ({ra.json()['job_id']}) and BetaBrand ({rb.json()['job_id']}) enqueued successfully.")

        # 4. Fetch status and recent jobs list
        r_job = requests.get(f"{BASE_URL}/jobs/{job1_id}")
        assert r_job.status_code == 200
        print(f"  ✅ GET /jobs/{job1_id} -> status: {r_job.json()['status']}")

        r_jobs = requests.get(f"{BASE_URL}/jobs?limit=5")
        assert r_jobs.status_code == 200 and len(r_jobs.json()) >= 3
        print(f"  ✅ GET /jobs -> returned {len(r_jobs.json())} jobs.")

    finally:
        print("Shutting down live server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except Exception:
            server_process.kill()
        print("  ✅ Server terminated.")

    # 5. Kill worker mid-job, restart, confirm stale job is marked failed
    print("Testing crash recovery / stale job handling...")
    from src.jobs import init_db, get_job
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (id, job_type, brand, status, created_at, started_at)
            VALUES ('job_mid_kill_test', 'collect', 'KillTestBrand', 'running', datetime('now'), datetime('now'))
            """
        )
        conn.commit()

    job_before = get_job('job_mid_kill_test')
    assert job_before['status'] == 'running'
    print(f"  ✅ Simulated running job before restart: status={job_before['status']}")

    # Restart DB / worker
    init_db()

    job_after = get_job('job_mid_kill_test')
    assert job_after['status'] == 'failed'
    assert "Worker crashed or restarted" in job_after['error']
    print(f"  ✅ After restart: status={job_after['status']}, error='{job_after['error']}'")

    print("\nALL 5 LIVE VERIFICATION CRITERIA CONFIRMED AND PASSED!")

if __name__ == "__main__":
    main()
