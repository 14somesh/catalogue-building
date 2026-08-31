import os
import shutil
import pandas as pd
import yaml

# 1. Create empty scratch image directory images/test_stuffcool/
test_img_dir = "images/test_stuffcool"
if os.path.exists(test_img_dir):
    shutil.rmtree(test_img_dir)
os.makedirs(test_img_dir, exist_ok=True)
print(f"Created clean empty image directory: {test_img_dir} (files: {len(os.listdir(test_img_dir))})")

# 2. Copy and prepare data/test_run.xlsx
src_excel = "data/catalogue_data.xlsx"
dest_excel = "data/test_run.xlsx"

df = pd.read_excel(src_excel, sheet_name="CatalogueData")

# Columns to keep populated
keep_cols = ["Product_ID", "Brand", "Model_Name", "MRP_Input"]

for col in df.columns:
    if col not in keep_cols:
        df[col] = None

# Set Status to Pending on all 10
df["Status"] = "Pending"

with pd.ExcelWriter(dest_excel, engine="openpyxl", mode="w") as writer:
    df.to_excel(writer, sheet_name="CatalogueData", index=False)

print(f"Created scratch Excel file: {dest_excel} with {len(df)} products.")
print(df[keep_cols + ["Status"]])

# 3. Create config_test.yaml
with open("config.yaml", "r", encoding="utf-8") as f:
    config_data = yaml.safe_load(f)

config_data["paths"]["excel_file"] = "data/test_run.xlsx"
config_data["paths"]["images_dir"] = "images/test_stuffcool"

with open("config_test.yaml", "w", encoding="utf-8") as f:
    yaml.dump(config_data, f, default_flow_style=False)

print("Created config_test.yaml pointing to scratch dataset and empty image directory.")
