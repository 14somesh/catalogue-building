import os
import io
import sys
import time
import json
import glob
import shutil
import pdfplumber
import pypdfium2 as pdfium
import pandas as pd
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api import app
from src.jobs import get_job, enqueue_job, get_db_connection
from src.utils.excel_handler import load_catalogue_data, load_catalogue_data_readonly, save_catalogue_data

EXCEL_PATH = "data/catalogue_data.xlsx"
EXCEL_BACKUP = "data/catalogue_data.xlsx.phase3_backup"
CONFIG_PATH = "config.yaml"
CONFIG_BACKUP = "config.yaml.phase3_backup"


def backup_all():
    shutil.copy2(EXCEL_PATH, EXCEL_BACKUP)
    shutil.copy2(CONFIG_PATH, CONFIG_BACKUP)
    print(f"Backed up {EXCEL_PATH} and {CONFIG_PATH}")


def restore_all():
    if os.path.exists(EXCEL_BACKUP):
        shutil.copy2(EXCEL_BACKUP, EXCEL_PATH)
        os.remove(EXCEL_BACKUP)
    if os.path.exists(CONFIG_BACKUP):
        shutil.copy2(CONFIG_BACKUP, CONFIG_PATH)
        os.remove(CONFIG_BACKUP)
    # Clean up test override images
    if os.path.exists("images/overrides/pebble"):
        shutil.rmtree("images/overrides/pebble", ignore_errors=True)
    if os.path.exists("images/overrides/testbrand"):
        shutil.rmtree("images/overrides/testbrand", ignore_errors=True)
    print("Restored all Excel, config, and cleaned up test assets.")


def test_review_endpoint():
    print("\n" + "="*70)
    print("TEST 1: GET /brands/{brand}/review")
    print("="*70)
    with TestClient(app) as client:
        res = client.get("/brands/Pebble/review")
        assert res.status_code == 200, f"Failed: {res.text}"
        rows = res.json()
        print(f"  ✅ Retrieved {len(rows)} review rows for brand 'Pebble'")
        first_row = rows[0]
        print(f"  • Product_ID: {first_row.get('product_id')}")
        print(f"  • Model_Name: {first_row.get('model_name')}")
        print(f"  • Title: {first_row.get('title')}")
        print(f"  • Subtitle: {first_row.get('subtitle')}")
        print(f"  • Bullet 1: {first_row.get('bullet_1')}")
        print(f"  • DP: {first_row.get('dp')} | MRP: {first_row.get('mrp')}")
        print(f"  • Status: {first_row.get('status')}")
        print(f"  • Image URL: {first_row.get('image_url')}")
        print(f"  • Is Overridden: {first_row.get('is_overridden')}")
        # Verify no specs are present
        assert "specs" not in first_row, "Specs should NOT be present in review data!"
        assert "Spec_Capacity" not in first_row, "Raw specs should not be present!"
        print("  ✅ Verified no specs in review response.")


def test_edit_length_rejections():
    print("\n" + "="*70)
    print("TEST 2: Edit Length Rejection (PATCH /products/{product_id})")
    print("="*70)
    with TestClient(app) as client:
        # Title limit is 20 chars
        long_title = "This Title Is Far Too Long For The Card Panel"
        res_t = client.patch("/products/PB-PEB-001", json={"title": long_title})
        assert res_t.status_code == 400, f"Expected 400, got {res_t.status_code}"
        print(f"  ✅ Title overflow rejected: {res_t.json().get('detail')}")

        # Subtitle limit is 80 chars
        long_sub = "This is an extremely long subtitle that exceeds the maximum allowed eighty character limit on the product card."
        res_s = client.patch("/products/PB-PEB-001", json={"subtitle": long_sub})
        assert res_s.status_code == 400
        print(f"  ✅ Subtitle overflow rejected: {res_s.json().get('detail')}")

        # Bullet limit is 60 chars
        long_bullet = "This bullet point is way too verbose and will wrap onto four lines causing severe card overflow."
        res_b = client.patch("/products/PB-PEB-001", json={"bullet_1": long_bullet})
        assert res_b.status_code == 400
        print(f"  ✅ Bullet overflow rejected: {res_b.json().get('detail')}")


def test_edit_and_build_proof():
    print("\n" + "="*70)
    print("TEST 3: Edit Title, Bullet, MRP -> Build PDF -> Prove in PDF")
    print("="*70)
    target_pid = "PB-PEB-001"
    edit_payload = {
        "title": "Turbo Fuel",
        "bullet_1": "Custom Handcrafted 22.5W Fast Power.",
        "mrp": 3999
    }
    with TestClient(app) as client:
        # 1. Apply Edit
        res_patch = client.patch(f"/products/{target_pid}", json=edit_payload)
        assert res_patch.status_code == 200, f"Edit failed: {res_patch.text}"
        edited_row = res_patch.json()
        print(f"  ✅ Successfully edited {target_pid}:")
        print(f"     • Title: {edited_row.get('title')}")
        print(f"     • Bullet 1: {edited_row.get('bullet_1')}")
        print(f"     • MRP: {edited_row.get('mrp')}")
        print(f"     • Is Overridden: {edited_row.get('is_overridden')}")

        # 2. Build single-brand PDF for Pebble
        res_b = client.post("/build", json={"brand": "Pebble"})
        assert res_b.status_code == 200
        job_id = res_b.json()["job_id"]
        print(f"  ✅ Enqueued build job: {job_id}")

        start_t = time.time()
        job = None
        while time.time() - start_t < 40.0:
            j = client.get(f"/jobs/{job_id}").json()
            if j and j["status"] in ("done", "failed"):
                job = j
                break
            time.sleep(0.5)

        assert job is not None and job["status"] == "done", f"Build failed: {job}"
        pdf_path = job["result"]["pdf_path"]
        print(f"  ✅ PDF compiled successfully to: {pdf_path}")
        print(f"     • Page Count: {job['result']['page_count']}")
        print(f"     • File Size: {job['result']['file_size']} bytes")
        print(f"     • URL: {job['result']['url']}")

        # 3. Extract text with pypdfium2 and prove edited values appear in PDF
        pdf_doc = pdfium.PdfDocument(pdf_path)
        all_text = " ".join(page.get_textpage().get_text_range() for page in pdf_doc)
        normalized_text = " ".join(all_text.split()).lower()

        print("\n  🔍 Verifying edited values in PDF text:")
        assert "turbo fuel" in normalized_text, "Edited title 'Turbo Fuel' NOT found in PDF text!"
        print("     • Found Title: 'Turbo Fuel' in PDF!")

        assert "custom handcrafted 22.5w fast power" in normalized_text, "Edited bullet NOT found in PDF text!"
        print("     • Found Bullet: 'Custom Handcrafted 22.5W Fast Power.' in PDF!")

        assert "3,999" in normalized_text or "3999" in normalized_text, "Edited MRP 3,999 NOT found in PDF text!"
        print("     • Found MRP: '₹3,999' in PDF!")
        print("  ✅ Proven: API edits directly rendered in compiled PDF output.")


def test_clear_override_with_null():
    print("\n" + "="*70)
    print("TEST 4: Clear Overrides with null and Verify Fallback")
    print("="*70)
    target_pid = "PB-PEB-001"
    with TestClient(app) as client:
        # Clear title and mrp by sending null
        clear_payload = {
            "title": None,
            "mrp": None
        }
        res_clear = client.patch(f"/products/{target_pid}", json=clear_payload)
        assert res_clear.status_code == 200, f"Clear failed: {res_clear.text}"
        cleared_row = res_clear.json()
        print(f"  ✅ After clearing with null:")
        print(f"     • Title reverted to collected: {cleared_row.get('title')} (is_overridden: {cleared_row.get('is_overridden')['title']})")
        print(f"     • MRP reverted to collected: {cleared_row.get('mrp')} (is_overridden: {cleared_row.get('is_overridden')['mrp']})")
        assert cleared_row["is_overridden"]["title"] is False
        assert cleared_row["is_overridden"]["mrp"] is False
        assert cleared_row["title"] == "Fuel", f"Expected 'Fuel', got {cleared_row['title']}"
        assert cleared_row["mrp"] == 3499, f"Expected 3499, got {cleared_row['mrp']}"
        print("  ✅ Proved: Sending null cleared override and returned collected value.")


def test_image_upload_and_delete():
    print("\n" + "="*70)
    print("TEST 5: Image Upload (1200x1200), Rejection (400x400), and Delete")
    print("="*70)
    target_pid = "PB-PEB-001"
    with TestClient(app) as client:
        # 1. Test rejection of low-resolution image (400x400)
        low_res_img = Image.new("RGB", (400, 400), (255, 255, 255))
        buf_low = io.BytesIO()
        low_res_img.save(buf_low, format="PNG")
        buf_low.seek(0)

        res_low = client.post(
            f"/products/{target_pid}/image",
            files={"file": ("small.png", buf_low, "image/png")}
        )
        assert res_low.status_code == 400, f"Expected 400 for low res, got {res_low.status_code}"
        print(f"  ✅ 400x400 image rejected: {res_low.json().get('detail')}")

        # 2. Test valid high-resolution rectangular image (1400x1200) padded to square
        rect_img = Image.new("RGB", (1400, 1200), (200, 100, 50))
        buf_rect = io.BytesIO()
        rect_img.save(buf_rect, format="PNG")
        buf_rect.seek(0)

        res_upload = client.post(
            f"/products/{target_pid}/image",
            files={"file": ("highres.png", buf_rect, "image/png")}
        )
        assert res_upload.status_code == 200, f"Upload failed: {res_upload.text}"
        data = res_upload.json()
        print(f"  ✅ High-res image uploaded: path={data.get('override_image_path')}, dims={data.get('width')}x{data.get('height')}px")
        assert data["width"] == 1400 and data["height"] == 1400, "Image was not padded to 1:1 square!"
        assert os.path.exists(data["override_image_path"])

        # Check review endpoint shows overridden image
        rev = client.get("/brands/Pebble/review").json()
        row = [r for r in rev if r["product_id"] == target_pid][0]
        assert row["is_overridden"]["image"] is True
        print(f"  ✅ Review endpoint confirms image override: {row['image_url']}")

        # 3. Test DELETE image override
        res_del = client.delete(f"/products/{target_pid}/image")
        assert res_del.status_code == 200
        print(f"  ✅ Image override deleted: {res_del.json().get('message')}")

        rev_after = client.get("/brands/Pebble/review").json()
        row_after = [r for r in rev_after if r["product_id"] == target_pid][0]
        assert row_after["is_overridden"]["image"] is False
        print(f"  ✅ Image reverted back to: {row_after['image_url']}")


def test_approve_and_skip():
    print("\n" + "="*70)
    print("TEST 6: Single Product Approve/Skip and Brand Bulk Approve")
    print("="*70)
    df = load_catalogue_data(EXCEL_PATH)
    # Put a test row in Ready_For_Review
    test_sku = {
        "Product_ID": "PB-APP-001",
        "Category": "Powerbank",
        "Brand": "ApproveBrand",
        "Model_Name": "Wave 10",
        "Display_Name": "Wave",
        "Status": "Ready_For_Review",
        "MRP_Input": 999,
        "Attempts": 1
    }
    df = pd.concat([df, pd.DataFrame([test_sku])], ignore_index=True)
    save_catalogue_data(df, EXCEL_PATH)

    with TestClient(app) as client:
        # 1. Approve single product
        res_app = client.post("/products/PB-APP-001/approve")
        assert res_app.status_code == 200
        print(f"  ✅ Single product approve: {res_app.json()}")
        assert res_app.json()["status"] == "Approved"

        # 2. Skip single product
        res_skp = client.post("/products/PB-APP-001/skip")
        assert res_skp.status_code == 200
        print(f"  ✅ Single product skip: {res_skp.json()}")
        assert res_skp.json()["status"] == "Skipped"

        # 3. Bulk brand approve with no product_ids (should approve all Ready_For_Review rows)
        # Put 2 rows in Ready_For_Review and 1 in Skipped
        df2 = load_catalogue_data(EXCEL_PATH)
        m = df2["Product_ID"] == "PB-APP-001"
        df2.loc[m, "Status"] = "Ready_For_Review"
        test_sku2 = {
            "Product_ID": "PB-APP-002",
            "Category": "Powerbank",
            "Brand": "ApproveBrand",
            "Model_Name": "Wave 20",
            "Display_Name": "Wave 20",
            "Status": "Skipped",
            "MRP_Input": 1499,
            "Attempts": 1
        }
        df2 = pd.concat([df2, pd.DataFrame([test_sku2])], ignore_index=True)
        save_catalogue_data(df2, EXCEL_PATH)

        res_b_app = client.post("/brands/ApproveBrand/approve")
        assert res_b_app.status_code == 200
        data_b = res_b_app.json()
        print(f"  ✅ Bulk brand approve: approved {data_b['approved_count']} row(s)")
        print(f"     • Changed: {[r['product_id'] for r in data_b['changed_rows']]}")
        print(f"     • Unchanged: {[(r['product_id'], r['reason']) for r in data_b['unchanged_rows']]}")
        assert any(r["product_id"] == "PB-APP-001" for r in data_b["changed_rows"])
        assert any(r["product_id"] == "PB-APP-002" for r in data_b["unchanged_rows"])

    # Clean up test rows so combined build isn't contaminated
    df_clean = load_catalogue_data(EXCEL_PATH)
    df_clean = df_clean[~df_clean["Product_ID"].isin(["PB-APP-001", "PB-APP-002"])]
    save_catalogue_data(df_clean, EXCEL_PATH)
    print("  ✅ Cleaned up temporary test rows for ApproveBrand.")


def test_builds_and_outputs():
    print("\n" + "="*70)
    print("TEST 7: GET /builds and Combined Build with brand_order update")
    print("="*70)
    with TestClient(app) as client:
        # Enqueue combined build with custom brand_order
        custom_order = ["Stuffcool", "Pebble", "Portronics", "Urbn", "EVM"]
        res_build = client.post("/build", json={"brand_order": custom_order})
        assert res_build.status_code == 200
        job_id = res_build.json()["job_id"]
        print(f"  ✅ Enqueued combined build: {job_id}")

        # Wait for build to complete
        start_t = time.time()
        job = None
        while time.time() - start_t < 45.0:
            j = client.get(f"/jobs/{job_id}").json()
            if j and j["status"] in ("done", "failed"):
                job = j
                break
            time.sleep(1.0)

        assert job is not None and job["status"] == "done", f"Combined build failed: {job}"
        print(f"  ✅ Combined build done! Result: {job['result']}")
        assert job["result"]["brand_count"] == 5
        assert job["result"]["product_count"] == 40
        assert job["result"]["page_count"] == 27

        # Test GET /builds endpoint
        res_builds = client.get("/builds")
        assert res_builds.status_code == 200
        builds = res_builds.json()
        print(f"  ✅ GET /builds returned {len(builds)} built PDFs:")
        for b in builds[:3]:
            print(f"     • {b['filename']} | Brand: {b['brand']} | Pages: {b['page_count']} | Size: {b['size']} bytes | Date: {b['timestamp']}")
        assert len(builds) > 0


if __name__ == "__main__":
    backup_all()
    try:
        test_review_endpoint()
        test_edit_length_rejections()
        test_edit_and_build_proof()
        test_clear_override_with_null()
        test_image_upload_and_delete()
        test_approve_and_skip()
        test_builds_and_outputs()
        print("\n" + "="*70)
        print("ALL PHASE 3 VERIFICATION TESTS PASSED SUCCESSFULLY!")
        print("="*70)
    finally:
        restore_all()
