import os
import sys
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
collect_module = importlib.import_module("src.1_collect")
collect_data_for_row = collect_module.collect_data_for_row
load_config = collect_module.load_config

print("=== COMPREHENSIVE VERIFICATION: ALL 10 PRODUCTS DATA COLLECTION ===")
cfg = load_config("config_test.yaml")

test_rows = [
    {"Product_ID": "PB-SC-001", "Brand": "Stuffcool", "Model_Name": "Aura", "MRP_Input": 2099},
    {"Product_ID": "PB-SC-002", "Brand": "Stuffcool", "Model_Name": "Click 10", "MRP_Input": 1699},
    {"Product_ID": "PB-SC-003", "Brand": "Stuffcool", "Model_Name": "Giga 20000 mAH", "MRP_Input": 2899},
    {"Product_ID": "PB-SC-004", "Brand": "Stuffcool", "Model_Name": "Lucid", "MRP_Input": 1699},
    {"Product_ID": "PB-SC-005", "Brand": "Stuffcool", "Model_Name": "Major 10000 mAH", "MRP_Input": 849},
    {"Product_ID": "PB-SC-006", "Brand": "Stuffcool", "Model_Name": "Roam Plus", "MRP_Input": 1499},
    {"Product_ID": "PB-SC-007", "Brand": "Stuffcool", "Model_Name": "Odin", "MRP_Input": 3199},
    {"Product_ID": "PB-SC-008", "Brand": "Stuffcool", "Model_Name": "1#", "MRP_Input": 949},
    {"Product_ID": "PB-SC-009", "Brand": "Stuffcool", "Model_Name": "Click 20", "MRP_Input": 2375},
    {"Product_ID": "PB-SC-010", "Brand": "Stuffcool", "Model_Name": "Omni pro", "MRP_Input": 3799},
]

results = []
for row in test_rows:
    pid = row["Product_ID"]
    mname = row["Model_Name"]
    print(f"\n[{pid}] Collecting: {mname}...")
    updates, success, msg = collect_data_for_row(row, cfg)
    status = "SUCCESS" if success else "BLOCKED"
    tier_found = updates.get("Tier_Spec_Capacity") or updates.get("Tier_Title") or "None"
    source_found = updates.get("Source_Spec_Capacity") or updates.get("Source_Title") or "None"
    title = updates.get("Raw_Title", "")
    cap = updates.get("Raw_Spec_Capacity", "")
    out = updates.get("Raw_Spec_Output", "")
    print(f"  Result: [{status}] Tier: {tier_found} | Cap: {cap} | Out: {out}")
    print(f"  Source: {source_found}")
    results.append({
        "pid": pid,
        "name": mname,
        "success": success,
        "tier": tier_found,
        "source": source_found,
        "cap": cap,
        "out": out
    })

print("\n" + "=" * 110)
print("FINAL COLLECTION SUMMARY TABLE:")
print("=" * 110)
for r in results:
    stat = "PASS" if r["success"] else "FAIL"
    print(f"{r['pid']:<10} | {r['name']:<18} | {stat:<5} | Tier: {str(r['tier']):<4} | Cap: {str(r['cap']):<10} | Out: {str(r['out']):<20} | Source: {r['source']}")
