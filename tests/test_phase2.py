import os
import sys
import time
import json
import shutil
import pandas as pd
from typing import List, Dict, Any
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api import app
from src.jobs import get_job, init_db, enqueue_job, DEFAULT_DB_PATH
from src.utils.excel_handler import load_catalogue_data, load_catalogue_data_readonly, save_catalogue_data

EXCEL_PATH = "data/catalogue_data.xlsx"
BACKUP_PATH = "data/catalogue_data.xlsx.phase2_backup"


def backup_catalogue():
    shutil.copy2(EXCEL_PATH, BACKUP_PATH)
    print(f"Backed up {EXCEL_PATH} -> {BACKUP_PATH}")


def restore_catalogue():
    if os.path.exists(BACKUP_PATH):
        shutil.copy2(BACKUP_PATH, EXCEL_PATH)
        os.remove(BACKUP_PATH)
        print(f"Restored {EXCEL_PATH} from backup.")


def test_collect_with_sse_stream():
    print("\n" + "="*70)
    print("TEST 1: Real Collect with SSE Event Sequence and Final Result")
    print("="*70)

    # Add a test brand with a quick test SKU into catalogue_data.xlsx
    df = load_catalogue_data(EXCEL_PATH)
    test_row = {
        "Product_ID": "PB-TST-001",
        "Category": "Powerbank",
        "Brand": "TestCollectBrand",
        "Model_Name": "Fuel 10000mAh",
        "Display_Name": "Fuel",
        "Status": "Pending",
        "MRP_Input": 1999,
        "Attempts": 0
    }
    df = pd.concat([df, pd.DataFrame([test_row])], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        # Trigger collection
        res = client.post("/brands/TestCollectBrand/collect")
        assert res.status_code == 200, f"Collect failed: {res.text}"
        data = res.json()
        job_id = data["job_id"]
        print(f"  ✅ Enqueued collect job: {job_id} (brand: TestCollectBrand)")

        # Connect to SSE stream
        print("  📡 Connecting to SSE stream: /jobs/{job_id}/stream ...")
        events_received = []
        terminal_event = None

        with client.stream("GET", f"/jobs/{job_id}/stream") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload_raw = line[6:].strip()
                    evt = json.loads(payload_raw)
                    events_received.append(evt)
                    stage = evt.get("stage")
                    msg = evt.get("message", "")
                    cur = evt.get("current", 0)
                    tot = evt.get("total", 0)
                    pid = evt.get("product_id")
                    print(f"     [SSE Event] stage={stage:<15} | pid={str(pid):<12} | progress={cur}/{tot} | {msg[:60]}")
                    if stage == "terminal":
                        terminal_event = evt
                        break

        assert terminal_event is not None, "Did not receive terminal SSE event!"
        print(f"\n  ✅ Terminal SSE Event received with status: {terminal_event.get('status')}")
        res_payload = terminal_event.get("result") or {}
        print("  ✅ Full Job Result Structure:")
        print(f"     • Brand: {res_payload.get('brand')}")
        print(f"     • Runtime: {res_payload.get('runtime')}s")
        print(f"     • Status Counts: {res_payload.get('status_counts')}")
        print("     • Per-Row Items:")
        for r in res_payload.get("rows", []):
            print(f"       - {r.get('Product_ID')}: Model='{r.get('Model_Name')}' | Status={r.get('Status')} | Tier={r.get('Tier')} | Img={r.get('Image_Status')} | Failure_Reason={r.get('Failure_Reason')}")
            for req_key in ["Product_ID", "Model_Name", "Display_Name", "Status", "Source_URL", "Tier", "Image_Status", "Attempts", "Flags", "Fix_Log"]:
                assert req_key in r, f"Required key '{req_key}' missing from row result!"

        # Verify GET /jobs/{job_id} matches
        res_poll = client.get(f"/jobs/{job_id}")
        assert res_poll.status_code == 200
        assert res_poll.json()["status"] == terminal_event["status"]
        print("  ✅ GET /jobs/{job_id} polling fallback verified.")


def test_cancel_running_collect_mid_run():
    print("\n" + "="*70)
    print("TEST 2: Cancel a Running Collect Mid-Run")
    print("="*70)

    # 1. First verify outright cancellation of a queued job:
    from src.jobs import get_db_connection, request_job_cancellation
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO jobs (id, job_type, brand, status, created_at) VALUES ('job_queued_test_1', 'collect', 'QueueBrand', 'queued', '2026-09-02T12:00:00')")
        conn.commit()
    cancel_q_res = request_job_cancellation("job_queued_test_1")
    assert cancel_q_res["status"] == "cancelled"
    assert get_job("job_queued_test_1")["status"] == "cancelled"
    print("  ✅ Verified outright cancellation of queued job before execution.")

    # 2. Next verify graceful mid-run cancellation of a running job:
    df = load_catalogue_data(EXCEL_PATH)
    rows = [
        {"Product_ID": f"PB-CAN-00{i}", "Category": "Powerbank", "Brand": "CancelBrand", "Model_Name": f"Slow Model {i}", "Status": "Pending", "MRP_Input": 1000 + i*500, "Attempts": 0}
        for i in range(1, 4)
    ]
    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        res = client.post("/brands/CancelBrand/collect")
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        print(f"  ✅ Enqueued collect job: {job_id} (brand: CancelBrand)")

        # Poll until the worker claims the job and it is running
        for _ in range(50):
            j = get_job(job_id)
            if j and j["status"] == "running":
                break
            time.sleep(0.05)

        print("  Job is running. Sending POST /jobs/{job_id}/cancel ...")
        cancel_res = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_res.status_code == 200
        print(f"  Cancel response: {cancel_res.json()}")

        # Wait for worker to finish current row and halt
        start_t = time.time()
        final_job = None
        while time.time() - start_t < 15.0:
            j = get_job(job_id)
            if j and j["status"] in ("cancelled", "done", "failed"):
                final_job = j
                break
            time.sleep(0.2)

        assert final_job is not None
        assert final_job["status"] == "cancelled", f"Expected job status 'cancelled', got {final_job['status']}"
        print(f"  ✅ Job status confirmed as 'cancelled': {final_job.get('error') or final_job.get('message')}")

        # Check catalogue_data.xlsx
        df_after = load_catalogue_data_readonly(EXCEL_PATH)
        row1 = df_after[df_after["Product_ID"] == "PB-CAN-001"].iloc[0]
        row2 = df_after[df_after["Product_ID"] == "PB-CAN-002"].iloc[0]
        row3 = df_after[df_after["Product_ID"] == "PB-CAN-003"].iloc[0]

        print(f"  • Row 1 ({row1['Product_ID']}): Status='{row1['Status']}', Attempts={row1['Attempts']} (completed & kept data)")
        print(f"  • Row 2 ({row2['Product_ID']}): Status='{row2['Status']}', Attempts={row2['Attempts']} (stayed Pending)")
        print(f"  • Row 3 ({row3['Product_ID']}): Status='{row3['Status']}', Attempts={row3['Attempts']} (stayed Pending)")

        assert row1["Status"] != "Pending", "Row 1 should have been processed!"
        assert row2["Status"] == "Pending", "Row 2 should have stayed Pending!"
        assert row3["Status"] == "Pending", "Row 3 should have stayed Pending!"
        print("  ✅ Proved: completed rows kept data, remaining rows stayed Pending.")


def test_retry_skipped_row():
    print("\n" + "="*70)
    print("TEST 3: Retry a Skipped Row (Attempts reset to 0, collected from scratch)")
    print("="*70)

    # Add a Skipped row with Attempts = 3
    df = load_catalogue_data(EXCEL_PATH)
    skipped_row = {
        "Product_ID": "PB-RTR-001",
        "Category": "Powerbank",
        "Brand": "RetryBrand",
        "Model_Name": "AeroPower 10000mAh",
        "Status": "Skipped",
        "Attempts": 3,
        "Flags": "Skipped: All spec tiers exhausted",
        "Fix_Log": "Exhausted all sources"
    }
    df = pd.concat([df, pd.DataFrame([skipped_row])], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        # Trigger retry for RetryBrand
        res = client.post("/brands/RetryBrand/retry", json={"product_ids": ["PB-RTR-001"]})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        print(f"  ✅ Enqueued retry job: {job_id} for PB-RTR-001")

        # Wait for completion
        start_t = time.time()
        job = None
        while time.time() - start_t < 25.0:
            j = client.get(f"/jobs/{job_id}").json()
            if j["status"] in ("done", "failed"):
                job = j
                break
            time.sleep(0.5)

        assert job is not None and job["status"] == "done", f"Retry job failed: {job}"
        retried_row = job["result"]["rows"][0]
        print(f"  ✅ Retry completed. Returned row: PID={retried_row['Product_ID']}, Status={retried_row['Status']}, Failure_Reason={retried_row.get('Failure_Reason')}")
        
        # Verify in Excel that Attempts was reset from 3 and processed
        df_after = load_catalogue_data_readonly(EXCEL_PATH)
        row_after = df_after[df_after["Product_ID"] == "PB-RTR-001"].iloc[0]
        print(f"  Excel row after retry: Status='{row_after['Status']}', Attempts={row_after['Attempts']}")
        assert row_after["Attempts"] in (0, 1), f"Expected Attempts reset to 0 or 1, got {row_after['Attempts']}"
        print("  ✅ Proved: Attempts was reset and row was collected from scratch.")


def test_override_title_preservation():
    print("\n" + "="*70)
    print("TEST 4: Override_Title Preservation on Re-run")
    print("="*70)

    df = load_catalogue_data(EXCEL_PATH)
    override_row = {
        "Product_ID": "PB-OVR-001",
        "Category": "Powerbank",
        "Brand": "OverrideBrand",
        "Model_Name": "Turbo 20000mAh",
        "Display_Name": "Turbo",
        "Override_Title": "Artisan Custom Handcrafted Title",
        "Override_DP": 1599,
        "Status": "Pending",
        "Attempts": 0
    }
    df = pd.concat([df, pd.DataFrame([override_row])], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        # Call single row rerun: POST /products/PB-OVR-001/rerun
        res = client.post("/products/PB-OVR-001/rerun")
        assert res.status_code == 200, f"Rerun failed: {res.text}"
        data = res.json()
        print(f"  ✅ Re-run response: status={data.get('status')}, product_id={data.get('product_id')}")

        # Check catalogue_data.xlsx
        df_after = load_catalogue_data_readonly(EXCEL_PATH)
        row_after = df_after[df_after["Product_ID"] == "PB-OVR-001"].iloc[0]
        print(f"  Row after rerun: Override_Title = '{row_after['Override_Title']}', Override_DP = {row_after['Override_DP']}")
        assert row_after["Override_Title"] == "Artisan Custom Handcrafted Title", "Override_Title was overwritten!"
        assert row_after["Override_DP"] == 1599, "Override_DP was overwritten!"
        print("  ✅ Proved: All Override_* fields survived re-run 100% intact.")


def test_manual_source_url():
    print("\n" + "="*70)
    print("TEST 5: Manual URL (POST /products/{product_id}/source)")
    print("="*70)

    df = load_catalogue_data(EXCEL_PATH)
    manual_row = {
        "Product_ID": "PB-MAN-001",
        "Category": "Powerbank",
        "Brand": "Stuffcool",
        "Model_Name": "Click 10000mAh",
        "Status": "Skipped",
        "Attempts": 0
    }
    df = pd.concat([df, pd.DataFrame([manual_row])], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        manual_url = "https://www.stuffcool.com/products/click-10"
        res = client.post("/products/PB-MAN-001/source", json={"url": manual_url})
        assert res.status_code == 200, f"Manual source endpoint failed: {res.text}"
        data = res.json()
        print(f"  ✅ Manual source response: status={data.get('status')}, success={data.get('success')}")
        print(f"     Message: {data.get('message')}")
        updated_row = data.get("updated_row") or {}
        print(f"     Updated row: Status='{updated_row.get('Status')}', Source_URL='{updated_row.get('Source_URL')}'")

        # Verify Product_URL was set in Excel
        df_after = load_catalogue_data_readonly(EXCEL_PATH)
        row_after = df_after[df_after["Product_ID"] == "PB-MAN-001"].iloc[0]
        assert row_after["Product_URL"] == manual_url, f"Product_URL was not set in sheet! Got {row_after['Product_URL']}"
        print(f"  ✅ Verified Product_URL was permanently saved to sheet: {row_after['Product_URL']}")


if __name__ == "__main__":
    backup_catalogue()
    try:
        test_collect_with_sse_stream()
        test_cancel_running_collect_mid_run()
        test_retry_skipped_row()
        test_override_title_preservation()
        test_manual_source_url()
        print("\n" + "="*70)
        print("ALL PHASE 2 VERIFICATION TESTS PASSED SUCCESSFULLY!")
        print("="*70)
    finally:
        restore_catalogue()
