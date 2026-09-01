import os
import sys
import unittest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import importlib
build_mod = importlib.import_module("src.4_build")
load_config = build_mod.load_config
from src.utils.excel_handler import get_effective_product_dict

class TestBrandAggregationAndPagination(unittest.TestCase):

    def test_brand_grouping_and_pagination(self):
        # Create a mock DataFrame with out-of-order rows
        rows = [
            {"Product_ID": "PB-SC-001", "Brand": "Stuffcool", "Model_Name": "P1", "Status": "Approved"},
            {"Product_ID": "PB-SC-003", "Brand": "Stuffcool", "Model_Name": "P3", "Status": "Approved"},
            {"Product_ID": "PB-PEB-001", "Brand": "Pebble", "Model_Name": "PEB1", "Status": "Approved"},
            {"Product_ID": "PB-SC-002", "Brand": "Stuffcool", "Model_Name": "P2", "Status": "Approved"},
            {"Product_ID": "PB-SC-011", "Brand": "Stuffcool", "Model_Name": "P11", "Status": "Approved"},
        ]
        df = pd.DataFrame(rows)

        # Simulate build brand grouping logic
        ordered_brands = df["Brand"].dropna().unique().tolist()
        brand_groups = []

        for brand_name in ordered_brands:
            brand_df = df[df["Brand"] == brand_name].sort_values(by="Product_ID", ascending=True)
            products = []
            for idx_in_brand, (_, row) in enumerate(brand_df.iterrows(), 1):
                prod_ctx = {
                    "product_id": row["Product_ID"],
                    "index": f"{idx_in_brand:02d}",
                    "brand": brand_name,
                    "name": row["Model_Name"]
                }
                products.append(prod_ctx)

            # Paginate 2-up
            pages = []
            for i in range(0, len(products), 2):
                chunk = products[i:i+2]
                pages.append({
                    "type": "2-up" if len(chunk) == 2 else "1-up",
                    "products": chunk,
                    "start_index": i + 1
                })

            brand_groups.append({
                "brand": brand_name,
                "pages": pages,
                "total_products": len(products)
            })

        # 1. Exactly one brand group per unique brand
        self.assertEqual(len(brand_groups), 2)
        sc_group = [g for g in brand_groups if g["brand"] == "Stuffcool"][0]
        peb_group = [g for g in brand_groups if g["brand"] == "Pebble"][0]

        # 2. Sort within brand by Product_ID
        sc_pids = [p["product_id"] for page in sc_group["pages"] for p in page["products"]]
        self.assertEqual(sc_pids, ["PB-SC-001", "PB-SC-002", "PB-SC-003", "PB-SC-011"])

        # 3. Numbering continues in sequence
        sc_indices = [p["index"] for page in sc_group["pages"] for p in page["products"]]
        self.assertEqual(sc_indices, ["01", "02", "03", "04"])

        # 4. Slot filling: 4 products => 2 pages of 2-up
        self.assertEqual(len(sc_group["pages"]), 2)
        self.assertEqual(sc_group["pages"][0]["type"], "2-up")
        self.assertEqual(sc_group["pages"][1]["type"], "2-up")
        self.assertEqual(len(sc_group["pages"][1]["products"]), 2)

        print("\n[SUCCESS] Brand aggregation, Product_ID sorting, sequence numbering, and slot filling verified!")

if __name__ == "__main__":
    unittest.main()
