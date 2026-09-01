import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data

def append_urbn():
    excel_path = "data/catalogue_data.xlsx"
    df = load_catalogue_data(excel_path)
    
    # Check if already present
    if (df["Brand"].astype(str).str.lower() == "urbn").any():
        print("Urbn rows already exist in catalogue_data.xlsx. Cleaning old Urbn rows before re-appending.")
        df = df[df["Brand"].astype(str).str.lower() != "urbn"]

    products = [
        {"Product_ID": "PB-URB-001", "Brand": "Urbn", "Model_Name": "Nano 10000mAh 20W", "Display_Name": "Nano", "MRP_Input": 1020, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-002", "Brand": "Urbn", "Model_Name": "Atom Link MagSafe 10000mAh 20W", "Display_Name": "Atom Link", "MRP_Input": 2125, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-003", "Brand": "Urbn", "Model_Name": "Frost MagSafe 10000mAh", "Display_Name": "Frost", "MRP_Input": 1615, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-004", "Brand": "Urbn", "Model_Name": "Flux MagSafe Qi2 10000mAh", "Display_Name": "Flux", "MRP_Input": 2550, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-005", "Brand": "Urbn", "Model_Name": "Slide Stand MagSafe 10000mAh", "Display_Name": "Slide Stand", "MRP_Input": 1530, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-006", "Brand": "Urbn", "Model_Name": "Curve MagSafe 10000mAh", "Display_Name": "Curve", "MRP_Input": 1530, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-007", "Brand": "Urbn", "Model_Name": "Arc MagSafe 10000mAh", "Display_Name": "Arc", "MRP_Input": 1275, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-008", "Brand": "Urbn", "Model_Name": "Mini-Volt 10000mAh 35W", "Display_Name": "Mini-Volt", "MRP_Input": 1760, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-009", "Brand": "Urbn", "Model_Name": "Aero Mag Tag 10000mAh", "Display_Name": "Aero Mag Tag", "MRP_Input": 1680, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-010", "Brand": "Urbn", "Model_Name": "Slate MagSafe Qi2 10000mAh", "Display_Name": "Slate", "MRP_Input": 2125, "Status": "Pending", "Attempts": 0},
        {"Product_ID": "PB-URB-011", "Brand": "Urbn", "Model_Name": "Ultra-Volt 20000mAh 65W", "Display_Name": "Ultra-Volt", "MRP_Input": 2800, "Status": "Pending", "Attempts": 0},
    ]

    new_df = pd.DataFrame(products)
    combined = pd.concat([df, new_df], ignore_index=True)
    save_catalogue_data(combined, excel_path)
    print(f"Successfully appended {len(products)} Urbn rows to {excel_path}. Total rows: {len(combined)}")

if __name__ == "__main__":
    append_urbn()
