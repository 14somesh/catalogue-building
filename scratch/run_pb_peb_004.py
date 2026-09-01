import os
import sys
import yaml
import pandas as pd
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, is_empty_value

collect_mod = importlib.import_module("src.1_collect")
collect_data_for_row = collect_mod.collect_data_for_row
load_config = collect_mod.load_config

def run_single_row():
    config = load_config("config.yaml")
    excel_path = config.get("paths", {}).get("excel_path", "data/catalogue_data.xlsx")
    df = load_catalogue_data(excel_path)
    
    target_idx = df[df["Product_ID"] == "PB-PEB-004"].index
    if len(target_idx) == 0:
        print("Error: PB-PEB-004 not found in catalogue_data.xlsx")
        return
    
    idx = target_idx[0]
    row_dict = df.loc[idx].to_dict()
    print(f"Target row found: {row_dict.get('Product_ID')} - {row_dict.get('Brand')} {row_dict.get('Model_Name')}")
    
    # Blank out Raw_, Source_, Tier_ for collection
    for col in df.columns:
        if col.startswith("Raw_") or col.startswith("Source_") or col.startswith("Tier_"):
            row_dict[col] = None
    row_dict["Source_URL"] = None
    row_dict["Source_Audit"] = None
    
    print("\n--- Running collect_data_for_row for PB-PEB-004 ---")
    updates, success, log_msg = collect_data_for_row(row_dict, config)
    print(f"Success: {success}")
    print(f"Log: {log_msg}")
    
    print("\n--- Extracted Updates ---")
    for k, v in sorted(updates.items()):
        print(f"  {k}: {v}")
        
    # Apply updates to row_dict and df
    for k, v in updates.items():
        if k in df.columns:
            df.at[idx, k] = v
        else:
            print(f"Warning: column '{k}' not in df columns")
            
    # Save master Excel
    save_catalogue_data(df, excel_path)
    print("\nSuccessfully updated and saved data/catalogue_data.xlsx for PB-PEB-004.")

if __name__ == "__main__":
    run_single_row()
