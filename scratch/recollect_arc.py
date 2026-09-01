import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, check_file_lock, is_empty_value
from src.run_brand import process_row_loop, load_config
import importlib
collect_mod = importlib.import_module("src.1_collect")

def recollect_pb_urb_007():
    excel_path = "data/catalogue_data.xlsx"
    config = load_config("config.yaml")
    brand_defaults = config.get("brands", {})
    
    check_file_lock(excel_path)
    df = load_catalogue_data(excel_path)
    
    # Locate PB-URB-007
    idx_list = df[df["Product_ID"] == "PB-URB-007"].index
    if len(idx_list) == 0:
        print("PB-URB-007 not found in catalogue data!")
        return

    idx = idx_list[0]
    print(f"Current Status of PB-URB-007 before re-collection: {df.loc[idx, 'Status']}")
    
    # Clear raw fields for PB-URB-007
    raw_cols = [c for c in df.columns if c.startswith("Raw_") or c.startswith("Source_") or c.startswith("Tier_")]
    for rc in raw_cols:
        df.at[idx, rc] = None
    df.at[idx, "Source_URL"] = None
    df.at[idx, "Source_Audit"] = None
    df.at[idx, "Image_URL"] = None
    df.at[idx, "Image_Status"] = "missing"
    df.at[idx, "Status"] = "Pending"
    df.at[idx, "Attempts"] = 0
    df.at[idx, "Flags"] = None

    row_dict = df.loc[idx].to_dict()
    all_rows = [r.to_dict() for _, r in df.iterrows()]
    
    print("\n--- RUNNING TIER ESCALATION COLLECTION FOR PB-URB-007 ---")
    collect_updates, success, c_log = collect_mod.collect_data_for_row(row_dict, config)
    row_dict.update(collect_updates)
    print("Collection Updates:", collect_updates)
    
    print("\n--- RUNNING PROCESSING, IMAGE SOURCING & VALIDATION LOOP ---")
    processed_row = process_row_loop(
        row_dict, all_rows, config, brand_defaults, enable_semantic_audit=False
    )
    
    for k, v in processed_row.items():
        df.at[idx, k] = v
        
    save_catalogue_data(df, excel_path)
    print(f"\n✅ PB-URB-007 Re-collection Complete!")
    print(f"Final Status: {df.loc[idx, 'Status']}")
    print(f"Raw Title: {df.loc[idx, 'Raw_Title']}")
    print(f"Capacity: {df.loc[idx, 'Raw_Spec_Capacity']}")
    print(f"Output: {df.loc[idx, 'Raw_Spec_Output']}")
    print(f"Ports: {df.loc[idx, 'Raw_Spec_Ports']}")
    print(f"Weight: {df.loc[idx, 'Raw_Spec_Weight']}")
    print(f"Warranty: {df.loc[idx, 'Raw_Spec_Warranty']}")
    print(f"Image Status: {df.loc[idx, 'Image_Status']} ({df.loc[idx, 'Image_File']})")
    print(f"Source Audit: {df.loc[idx, 'Source_Audit']}")

if __name__ == "__main__":
    recollect_pb_urb_007()
