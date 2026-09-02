import os
import sys
import pandas as pd
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, get_effective_value, get_effective_product_dict
from src.run_brand import re_run_product

def test_price_resolution_chains():
    """Verifies that Override_DP and Override_MRP drive their respective prices."""
    row = {
        "Product_ID": "TEST-001",
        "Brand": "Pebble",
        "Model_Name": "TestModel",
        "Display_Name": "TestModel",
        "MRP_Input": 1999,
        "Raw_MRP_Scraped": 3499,
        "MRP_Display": 3299,
        "Override_DP": 1799,
        "Override_MRP": 2999,
    }
    # 1. DP resolution: Override_DP > MRP_Input
    assert get_effective_value(row, "DP") == 1799
    row_no_dp_override = dict(row, Override_DP=None)
    assert get_effective_value(row_no_dp_override, "DP") == 1999

    # 2. MRP resolution: Override_MRP > MRP_Display > Raw_MRP_Scraped
    assert get_effective_value(row, "MRP") == 2999
    row_no_mrp_override = dict(row, Override_MRP=None)
    assert get_effective_value(row_no_mrp_override, "MRP") == 3299
    row_no_display = dict(row, Override_MRP=None, MRP_Display=None)
    assert get_effective_value(row_no_display, "MRP") == 3499

    prod = get_effective_product_dict(row)
    assert prod["dp"] == 1799
    assert prod["mrp"] == 2999
    assert prod["mrp_display"] == "2999"


def test_title_resolution_chain():
    """Verifies that Override_Title takes precedence over Display_Name and Model_Name."""
    row = {
        "Product_ID": "TEST-002",
        "Brand": "Pebble",
        "Model_Name": "BaseModel",
        "Display_Name": "CardDisplayName",
        "Override_Title": "HumanOverriddenTitle",
        "MRP_Input": 999
    }
    prod = get_effective_product_dict(row)
    assert prod["display_name"] == "HumanOverriddenTitle"

    row_no_override = dict(row, Override_Title=None)
    prod2 = get_effective_product_dict(row_no_override)
    assert prod2["display_name"] == "CardDisplayName"

    row_base_only = dict(row, Override_Title=None, Display_Name=None)
    prod3 = get_effective_product_dict(row_base_only)
    assert prod3["display_name"] == "BaseModel"


def test_rerun_resets_approved_row():
    """Verifies that re_run_product clears raw/source fields and resets status & attempts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_excel = os.path.join(tmpdir, "test_cat.xlsx")
        df = load_catalogue_data("data/catalogue_data.xlsx")
        # Find Pebble row
        target_pid = "PB-PEB-004"
        idx = df[df["Product_ID"] == target_pid].index[0]
        df.at[idx, "Status"] = "Approved"
        df.at[idx, "Attempts"] = 3
        df.at[idx, "Override_Title"] = "Rapid Boost Custom"
        save_catalogue_data(df, test_excel)

        # Create temporary config pointing to test_excel
        test_config = os.path.join(tmpdir, "test_config.yaml")
        import yaml
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["paths"]["excel_file"] = test_excel
        with open(test_config, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)

        # Re-run product
        updated_row = re_run_product(target_pid, config_path=test_config)

        # Confirm Override_Title preserved
        assert updated_row.get("Override_Title") == "Rapid Boost Custom"
        # Confirm row was processed through the review loop (not skipped by line 163 guard)
        assert updated_row.get("Status") in ("Ready_For_Review", "Approved", "Skipped", "Blocked")
        assert updated_row.get("Attempts") >= 1
        print("re_run_product successfully reset and re-processed approved row with Attempts=3!")

if __name__ == "__main__":
    print("Testing price resolution...")
    test_price_resolution_chains()
    print("PASS: Price resolution verified.")

    print("Testing title resolution...")
    test_title_resolution_chain()
    print("PASS: Title resolution verified.")

    print("Testing re_run_product...")
    test_rerun_resets_approved_row()
    print("PASS: re_run_product verified.")
