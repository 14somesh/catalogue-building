import os
import sys
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.scraper import load_brand_defaults
from src.utils.excel_handler import load_catalogue_data_readonly
from src.onboard_brand import derive_category_prefix
from src.jobs import init_db, enqueue_job, get_active_job_for_brand, BrandLockedError, get_db_connection
from src.parsers.brochure import find_brochure_pdfs
from fastapi.testclient import TestClient
from src.api import app

class TestCategoryDimension(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM jobs WHERE brand = 'Pebble' AND status IN ('queued', 'running')")
            conn.commit()

    def test_1_brand_defaults_pebble(self):
        pb_pb = load_brand_defaults("Pebble", "Powerbank")
        self.assertEqual(pb_pb.get("domain"), "pebblecart.com")
        self.assertIn("Max", pb_pb.get("qualifier_tokens", []))
        self.assertIn("Mini", pb_pb.get("qualifier_tokens", []))
        self.assertIn("powerbanks", pb_pb.get("collection_url", ""))

        pb_tws = load_brand_defaults("Pebble", "TWS")
        self.assertEqual(pb_tws.get("domain"), "pebblecart.com")
        self.assertIn("headphones", pb_tws.get("collection_url", ""))

    def test_2_existing_catalogue_rows(self):
        df = load_catalogue_data_readonly("data/catalogue_data.xlsx")
        pebble_rows = df[df["Brand"].astype(str).str.lower() == "pebble"]
        self.assertEqual(len(pebble_rows), 10)

        pb_rows = df[(df["Brand"].astype(str).str.lower() == "pebble") & (df["Category"].astype(str).str.lower() == "powerbank")]
        self.assertEqual(len(pb_rows), 7)
        self.assertEqual(pb_rows["Product_ID"].tolist(), [f"PB-PEB-{i:03d}" for i in range(1, 8)])

        tws_rows = df[(df["Brand"].astype(str).str.lower() == "pebble") & (df["Category"].astype(str).str.lower() == "tws")]
        self.assertEqual(len(tws_rows), 3)

    def test_3_category_prefixes(self):
        self.assertEqual(derive_category_prefix("Powerbank"), "PB")
        self.assertEqual(derive_category_prefix("Powerbanks"), "PB")
        self.assertEqual(derive_category_prefix("TWS"), "TWS")
        self.assertEqual(derive_category_prefix("Smartwatch"), "SW")

    def test_4_brochure_resolution(self):
        urbn_pb = find_brochure_pdfs("Urbn", "Powerbank")
        self.assertTrue(len(urbn_pb) > 0)
        self.assertTrue(any("powerbank" in p.replace("\\", "/").lower() for p in urbn_pb))

    def test_5_api_category_scoping(self):
        client = TestClient(app)
        # Test GET /brands/Pebble/rows?category=Powerbank
        resp_pb = client.get("/brands/Pebble/rows?category=Powerbank")
        self.assertEqual(resp_pb.status_code, 200)
        rows_pb = resp_pb.json()
        self.assertEqual(len(rows_pb), 7)
        for r in rows_pb:
            self.assertEqual(r.get("category"), "Powerbank")

        # Test GET /brands/Pebble/rows with empty category returns all 10
        resp_all = client.get("/brands/Pebble/rows")
        self.assertEqual(resp_all.status_code, 200)
        self.assertEqual(len(resp_all.json()), 10)

        # Test GET /brands/Pebble/rows?category=TWS returns 3 TWS rows
        resp_tws = client.get("/brands/Pebble/rows?category=TWS")
        self.assertEqual(resp_tws.status_code, 200)
        self.assertEqual(len(resp_tws.json()), 3)

    def test_6_concurrent_brand_category_locking(self):
        # Enqueue Pebble Powerbank job
        job1_id = enqueue_job("collect", "Pebble", category="Powerbank", payload={})
        # Enqueue Pebble TWS job - should succeed without BrandLockedError because category is different!
        job2_id = enqueue_job("collect", "Pebble", category="TWS", payload={})
        self.assertIsNotNone(job1_id)
        self.assertIsNotNone(job2_id)

        # Enqueue another Pebble Powerbank job - should fail with BrandLockedError
        with self.assertRaises(BrandLockedError):
            enqueue_job("collect", "Pebble", category="Powerbank", payload={})

    def test_7_list_brands_grouping_and_filtering(self):
        client = TestClient(app)
        # Test GET /brands returns Pebble entries for each category
        resp = client.get("/brands")
        self.assertEqual(resp.status_code, 200)
        brands = resp.json()
        pebble_entries = [b for b in brands if b["brand"].lower() == "pebble"]
        self.assertEqual(len(pebble_entries), 2)

        pb_entry = next(b for b in pebble_entries if b["category"] == "Powerbank")
        self.assertEqual(pb_entry["total_rows"], 7)
        tws_entry = next(b for b in pebble_entries if b["category"] == "TWS")
        self.assertEqual(tws_entry["total_rows"], 3)

        # Test GET /brands?category=Powerbank returns Powerbank entries
        resp_pb = client.get("/brands?category=Powerbank")
        self.assertEqual(resp_pb.status_code, 200)
        pb_brands = resp_pb.json()
        self.assertTrue(any(b["brand"].lower() == "pebble" for b in pb_brands))

        # Test GET /brands?category=TWS returns TWS entry
        resp_tws = client.get("/brands?category=TWS")
        self.assertEqual(resp_tws.status_code, 200)
        self.assertTrue(any(b["brand"].lower() == "pebble" for b in resp_tws.json()))


if __name__ == "__main__":
    unittest.main()
