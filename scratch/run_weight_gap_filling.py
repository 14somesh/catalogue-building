import os
import sys
import yaml
import pandas as pd
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, is_empty_value
from src.utils.validators import validate_row_deterministic
collect_mod = importlib.import_module("src.1_collect")
execute_vision_fallback_for_page = collect_mod.execute_vision_fallback_for_page
load_config = collect_mod.load_config

def main():
    config = load_config("config.yaml")
    excel_path = config.get("paths", {}).get("excel_path", "data/catalogue_data.xlsx")
    df = load_catalogue_data(excel_path)
    
    mask = df["Brand"].isin(["Pebble", "Portronics"])
    all_rows = [row.to_dict() for _, row in df.iterrows()]
    
    target_indices = []
    for idx in df[mask].index:
        r = df.loc[idx]
        status = str(r.get("Status", ""))
        flags = str(r.get("Flags", "")) if not is_empty_value(r.get("Flags")) else ""
        weight = r.get("Raw_Spec_Weight")
        ov_weight = r.get("Override_Spec_Weight")
        
        is_weight_empty = is_empty_value(weight) and is_empty_value(ov_weight)
        has_weight_flag = "weight" in flags.lower()
        
        if (status in ("Ready_For_Review", "Approved", "Collected") and is_weight_empty) or has_weight_flag:
            target_indices.append(idx)
            
    print(f"Found {len(target_indices)} target rows to process for Vision gap-filling:")
    for idx in target_indices:
        r = df.loc[idx]
        print(f"  [{r['Product_ID']}] {r['Brand']} {r['Model_Name']} (Weight: {r.get('Raw_Spec_Weight')})")
        
    gained_weight = []
    gained_other_specs = []
    no_change = []
    
    for idx in target_indices:
        row_dict = df.loc[idx].to_dict()
        pid = row_dict.get("Product_ID")
        brand = row_dict.get("Brand")
        model = row_dict.get("Model_Name")
        source_url = row_dict.get("Source_URL") or row_dict.get("Product_URL")
        
        print(f"\n==========================================")
        print(f"Processing [{pid}] {brand} {model}")
        print(f"Source URL: {source_url}")
        
        if not source_url or not str(source_url).startswith("http"):
            print(f"  No valid source URL found for {pid}. Skipping.")
            no_change.append(pid)
            continue
            
        initial_weight = row_dict.get("Raw_Spec_Weight")
        
        # Execute Vision Fallback on the source page
        vision_res = execute_vision_fallback_for_page(
            url=str(source_url).strip(),
            brand=brand,
            model_name=model,
            base_tier=1,
            config=config
        )
        
        if not vision_res or not vision_res.specs:
            print(f"  [Vision] No specs extracted from infographic images on {source_url}.")
            no_change.append(pid)
            continue
            
        print(f"  [Vision Extracted Specs]: {vision_res.specs}")
        
        spec_mapping = {
            "capacity": "Raw_Spec_Capacity",
            "output": "Raw_Spec_Output",
            "ports": "Raw_Spec_Ports",
            "weight": "Raw_Spec_Weight",
            "warranty": "Raw_Spec_Warranty"
        }
        
        row_updated = False
        weight_added = False
        other_added = []
        
        # Gap-filling only: do NOT overwrite any populated field
        for spec_k, col_name in spec_mapping.items():
            current_val = row_dict.get(col_name)
            vis_val = vision_res.specs.get(spec_k)
            
            if is_empty_value(current_val) and vis_val and not is_empty_value(vis_val):
                clean_vis = str(vis_val).strip()
                row_dict[col_name] = clean_vis
                row_dict[col_name.replace("Raw_", "Source_")] = source_url
                row_dict[col_name.replace("Raw_", "Tier_")] = "1-vision"
                row_updated = True
                print(f"  -> FILLED GAP {col_name}: '{clean_vis}' (Tier: 1-vision)")
                
                if spec_k == "weight":
                    weight_added = True
                else:
                    other_added.append(f"{spec_k}: '{clean_vis}'")
                    
        if weight_added:
            gained_weight.append({
                "pid": pid,
                "brand": brand,
                "model": model,
                "weight": row_dict.get("Raw_Spec_Weight"),
                "source": source_url
            })
        elif other_added:
            gained_other_specs.append({
                "pid": pid,
                "brand": brand,
                "model": model,
                "details": other_added
            })
        else:
            no_change.append(pid)
            
        if row_updated:
            # Re-run deterministic validation to update Flags
            is_passed, hard_flags, warnings = validate_row_deterministic(row_dict, all_rows)
            if is_passed:
                row_dict["Status"] = "Ready_For_Review" if row_dict.get("Status") != "Approved" else "Approved"
                row_dict["Flags"] = "\n".join(warnings) if warnings else None
            else:
                row_dict["Flags"] = "\n".join(hard_flags)
                
            # Update DataFrame
            for k, v in row_dict.items():
                df.at[idx, k] = v
                
    # Save master Excel
    save_catalogue_data(df, excel_path)
    print("\n==========================================")
    print("FINISHED VISION GAP-FILLING RUN")
    print(f"Total processed: {len(target_indices)}")
    print(f"Rows that gained Weight ({len(gained_weight)}):")
    for g in gained_weight:
        print(f"  - [{g['pid']}] {g['brand']} {g['model']}: Weight = {g['weight']} (Source: {g['source']})")
    print(f"Rows that gained other specs ({len(gained_other_specs)}):")
    for g in gained_other_specs:
        print(f"  - [{g['pid']}] {g['brand']} {g['model']}: {g['details']}")
    print(f"Rows with no change ({len(no_change)}): {no_change}")

if __name__ == "__main__":
    main()
