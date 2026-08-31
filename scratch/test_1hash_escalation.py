import os
import sys
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
collect_module = importlib.import_module("src.1_collect")
collect_data_for_row = collect_module.collect_data_for_row
load_config = collect_module.load_config

print("=== TESTING PB-SC-008 '1#' SPEC ESCALATION ===")
cfg = load_config("config_test.yaml")
row = {"Product_ID": "PB-SC-008", "Brand": "Stuffcool", "Model_Name": "1#", "MRP_Input": 949}

updates, success, msg = collect_data_for_row(row, cfg)
print(f"Success: {success}")
print(f"Message: {msg}")
print("Raw specs:")
for k, v in updates.items():
    if k.startswith("Raw_") or k.startswith("Source_") or k.startswith("Tier_"):
        print(f"  {k}: {v}")
