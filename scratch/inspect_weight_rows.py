import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.excel_handler import is_empty_value

df = pd.read_excel("data/catalogue_data.xlsx")
mask = df["Brand"].isin(["Pebble", "Portronics"])

print(f"Total Pebble & Portronics rows: {mask.sum()}")
target_rows = []
for idx, r in df[mask].iterrows():
    status = str(r["Status"])
    flags = str(r["Flags"]) if not is_empty_value(r["Flags"]) else ""
    weight = r["Raw_Spec_Weight"]
    ov_weight = r["Override_Spec_Weight"]
    
    # Check if Ready_For_Review / Approved with weight empty warning or missing weight
    is_weight_empty = is_empty_value(weight) and is_empty_value(ov_weight)
    has_weight_flag = "weight" in flags.lower()
    
    print(f"[{r['Product_ID']}] {r['Brand']} {r['Model_Name']} | Status: {status} | Weight: {weight} | Flags: {flags}")
    if (status in ("Ready_For_Review", "Approved", "Collected") and is_weight_empty) or has_weight_flag:
        target_rows.append(idx)

print(f"\nTarget rows with missing weight / weight empty flag ({len(target_rows)}):")
for idx in target_rows:
    row = df.loc[idx]
    print(f"  - [{row['Product_ID']}] {row['Brand']} {row['Model_Name']} (Source: {row['Source_URL']})")
