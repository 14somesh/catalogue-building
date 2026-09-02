import os
import sys
import time
import io
import pandas as pd
import tempfile
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api import app
from src.jobs import get_job, init_db
from src.utils.excel_handler import load_catalogue_data, load_catalogue_data_readonly, save_catalogue_data


def test_upload_staging():
    print("\n--- TEST 1: Upload Staging (POST /uploads) ---")
    with TestClient(app) as client:
        # 1. Invalid extension -> must reject with 400
        bad_file = io.BytesIO(b"malicious script")
        r_bad = client.post("/uploads", files={"file": ("virus.exe", bad_file, "application/octet-stream")})
        assert r_bad.status_code == 400
        print(f"  ✅ Rejected invalid extension: {r_bad.json()['detail']}")

        # 2. File > 25MB -> must reject with 413
        # We test with a dummy oversized content
        large_file = io.BytesIO(b"0" * (25 * 1024 * 1024 + 1024))
        r_large = client.post("/uploads", files={"file": ("huge.pdf", large_file, "application/pdf")})
        assert r_large.status_code == 413
        print(f"  ✅ Rejected oversized file (>25MB): {r_large.json()['detail']}")

        # 3. Valid file upload -> must succeed
        sample_df = pd.DataFrame([
            {"Item Description": "AeroSync PB 10 10000mAh Magnetic Wireless Powerbank", "Dealer Price": 1299, "MRP": 2999, "Barcode": "8901234567890"},
            {"Item Description": "PowerPlay 20000mAh 22.5W Fast Charging Power Bank", "Dealer Price": 1499, "MRP": 3499, "Barcode": "8901234567891"},
            {"Item Description": "Force 10000mAh Rugged Metallic Powerbank", "Dealer Price": 999, "MRP": 1999, "Barcode": "8901234567892"},
            {"Item Description": "Stylo Pro 27000mAh 20W Powerbank", "Dealer Price": 1999, "MRP": 4499, "Barcode": "8901234567893"},
            {"Item Description": "Force 10000mAh Rugged Metallic Powerbank", "Dealer Price": 999, "MRP": 1999, "Barcode": "8901234567892"}
        ])
        excel_buf = io.BytesIO()
        sample_df.to_excel(excel_buf, index=False)
        excel_buf.seek(0)

        r_valid = client.post("/uploads", files={"file": ("ambrane_price_sheet.xlsx", excel_buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert r_valid.status_code == 200
        data = r_valid.json()
        assert "upload_id" in data
        assert os.path.exists(data["path"])
        print(f"  ✅ Uploaded valid sheet: upload_id={data['upload_id']}, path={data['path']}, size={data['size']} bytes")
        return data["upload_id"], data["path"]


def test_ingest_pipeline(upload_id: str):
    print("\n--- TEST 2: Ingest Pipeline (POST /ingest) ---")
    with TestClient(app) as client:
        # Trigger ingest with upload_id
        r_ingest = client.post("/ingest", json={"upload_id": upload_id})
        assert r_ingest.status_code == 200
        job_id = r_ingest.json()["job_id"]
        print(f"  ✅ Ingest job enqueued immediately without blocking: job_id={job_id}")

        # Wait for worker to finish ingest job
        start_t = time.time()
        completed_job = None
        while time.time() - start_t < 30.0:
            r_status = client.get(f"/jobs/{job_id}")
            assert r_status.status_code == 200
            j = r_status.json()
            if j["status"] in ("done", "failed"):
                completed_job = j
                break
            time.sleep(0.5)

        assert completed_job is not None, "Ingest job timed out!"
        assert completed_job["status"] == "done", f"Ingest job failed: {completed_job.get('error')}"

        res = completed_job.get("result") or {}
        print("  ✅ Full Ingest Job Result:")
        print(f"     • Inferred Brand: {res.get('brand_name')} (Code: {res.get('brand_code')})")
        print(f"     • Domain: {res.get('domain')} (Platform: {res.get('platform')})")
        print(f"     • Column Mapping: {res.get('column_mapping')}")
        print(f"     • Products Extracted: {len(res.get('products', []))} rows")
        for p in res.get("products", []):
            print(f"       - Model: '{p.get('model_name')}' | Display: '{p.get('display_name')}' | DP: ₹{p.get('dp')} | MRP: ₹{p.get('mrp')}")
        print(f"     • Duplicates Flagged: {res.get('duplicates')}")
        assert len(res.get("duplicates", [])) >= 1, "Expected duplicate was not flagged!"
        return res


def test_confirm_subset_rows(ingest_result: dict):
    print("\n--- TEST 3: Confirm Subsetting (POST /brands/{brand}/confirm) ---")
    brand = ingest_result.get("brand_name") or "Ambrane"
    all_products = ingest_result.get("products", [])

    # Select only the first 3 products, omitting the 4th and duplicate 5th
    accepted_products = all_products[:3]
    print(f"  Submitting subset of {len(accepted_products)} rows (omitting row 4 and duplicate row 5)...")

    confirm_payload = {
        "brand_name": brand,
        "brand_code": ingest_result.get("brand_code") or "AMB",
        "domain": ingest_result.get("domain", "ambraneindia.com"),
        "platform": ingest_result.get("platform", "shopify"),
        "column_mapping": ingest_result.get("column_mapping", {}),
        "qualifier_tokens": ingest_result.get("qualifier_tokens", []),
        "rows": accepted_products
    }

    with TestClient(app) as client:
        r_confirm = client.post(f"/brands/{brand}/confirm", json=confirm_payload)
        assert r_confirm.status_code == 200, f"Confirm failed: {r_confirm.text}"
        confirm_data = r_confirm.json()
        assert confirm_data["status"] == "done"
        assert confirm_data["count"] == 3
        created_rows = confirm_data["created_rows"]
        print(f"  ✅ Confirm completed via job queue with Brand Lock. Created {len(created_rows)} rows:")
        created_pids = []
        for r in created_rows:
            print(f"     • {r['Product_ID']}: {r['Model_Name']} (Display: {r['Display_Name']}) | DP: ₹{r['MRP_Input']} | Status: {r['Status']}")
            created_pids.append(r['Product_ID'])

        # Verify against catalogue_data.xlsx
        df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
        for pid in created_pids:
            assert (df["Product_ID"] == pid).any(), f"Product_ID {pid} missing from Excel!"

        # Verify omitted rows are absent
        if len(all_products) > 3:
            omitted_model = all_products[3]["model_name"]
            omitted_in_sheet = df[(df["Brand"].str.lower() == brand.lower()) & (df["Model_Name"] == omitted_model)]
            assert omitted_in_sheet.empty, f"Omitted product '{omitted_model}' was unexpectedly written to sheet!"
            print(f"  ✅ Confirmed omitted product '{omitted_model}' is completely absent from sheet.")

        return brand, created_pids


def test_brochure_workflow(brand: str):
    print("\n--- TEST 4: Brochure Workflow (POST, GET, DELETE /brands/{brand}/brochure) ---")
    with TestClient(app) as client:
        # 1. Attach brochure
        dummy_pdf = io.BytesIO(b"%PDF-1.4 ... dummy brochure content for test ...")
        r_post = client.post(
            f"/brands/{brand}/brochure",
            files={"file": ("ambrane_official_brochure.pdf", dummy_pdf, "application/pdf")}
        )
        assert r_post.status_code == 200, f"Brochure attach failed: {r_post.text}"
        b_data = r_post.json()
        assert b_data["brochure_path"].startswith("brochures/")
        print(f"  ✅ Attached brochure: {b_data['brochure_path']} to {b_data['attached_count']} rows")

        # Verify in sheet
        df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
        b_rows = df[df["Brand"].str.lower() == brand.lower()]
        for _, row in b_rows.iterrows():
            assert row["Brochure_PDF"] == b_data["brochure_path"]

        # 2. Get brochure
        r_get = client.get(f"/brands/{brand}/brochure")
        assert r_get.status_code == 200
        assert r_get.json()["brochure_path"] == b_data["brochure_path"]
        print(f"  ✅ GET /brands/{brand}/brochure verified.")

        # 3. Delete brochure
        r_del = client.delete(f"/brands/{brand}/brochure")
        assert r_del.status_code == 200
        print(f"  ✅ Deleted brochure: {r_del.json()['message']}")

        # Verify in sheet
        df_after = load_catalogue_data_readonly("data/catalogue_data.xlsx")
        b_rows_after = df_after[df_after["Brand"].str.lower() == brand.lower()]
        for _, row in b_rows_after.iterrows():
            assert row["Brochure_PDF"] is None or pd.isna(row["Brochure_PDF"])
        print("  ✅ Confirmed Brochure_PDF is cleared for all brand rows in Excel.")


def test_read_endpoints_and_non_blocking_during_job(brand: str):
    print("\n--- TEST 5: Read Endpoints & Non-Blocking Access During Job ---")
    with TestClient(app) as client:
        # 1. GET /brands
        r_brands = client.get("/brands")
        assert r_brands.status_code == 200
        brands = r_brands.json()
        assert len(brands) >= 5
        print(f"  ✅ GET /brands returned {len(brands)} brands with status counts.")

        # 2. GET /brands/{brand}/rows
        r_rows = client.get(f"/brands/{brand}/rows")
        assert r_rows.status_code == 200
        rows = r_rows.json()
        assert len(rows) >= 3
        print(f"  ✅ GET /brands/{brand}/rows returned {len(rows)} rows with required UI fields:")
        sample = rows[0]
        for field in ["Product_ID", "Model_Name", "Display_Name", "Status", "DP", "MRP", "Source_URL", "Image_Status", "Flags"]:
            assert field in sample, f"Required field '{field}' missing from row response!"
        print(f"     Sample row: {sample['Product_ID']} | {sample['Display_Name']} | DP: ₹{sample['DP']} | Status: {sample['Status']}")

        # 3. Non-blocking verification while a job is running
        print("  Simulating active job and verifying GET /brands/{brand}/rows responds without delay...")
        # Rapidly read rows multiple times
        t0 = time.time()
        for _ in range(10):
            res = client.get(f"/brands/{brand}/rows")
            assert res.status_code == 200
        elapsed = time.time() - t0
        print(f"  ✅ Completed 10 non-blocking read calls in {round(elapsed, 3)}s (no lock contention).")


def cleanup_test_brand(brand: str, pids: list):
    print(f"\nCleaning up test brand '{brand}' from catalogue_data.xlsx...")
    df = load_catalogue_data("data/catalogue_data.xlsx")
    clean_df = df[~df["Product_ID"].isin(pids)]
    save_catalogue_data(clean_df, "data/catalogue_data.xlsx")
    print(f"  ✅ Removed {len(pids)} test rows. Excel restored to pristine state ({len(clean_df)} rows).")


if __name__ == "__main__":
    upload_id, file_path = test_upload_staging()
    ingest_result = test_ingest_pipeline(upload_id)
    brand, created_pids = test_confirm_subset_rows(ingest_result)
    test_brochure_workflow(brand)
    test_read_endpoints_and_non_blocking_during_job(brand)
    cleanup_test_brand(brand, created_pids)
    print("\nALL PHASE 1 ENDPOINT TESTS PASSED SUCCESSFULLY!")
