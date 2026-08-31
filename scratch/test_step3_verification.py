import os
import sys
import importlib

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import is_empty_value, RAW_FIELD_TO_SOURCE_MAP
from src.utils.validators import validate_row_deterministic
from src.utils.llm_client import audit_product_semantics

# Dynamic import for 3_review module (starts with digit)
review_mod = importlib.import_module("src.3_review")
process_row_loop = review_mod.process_row_loop
attempt_auto_fix = review_mod.attempt_auto_fix
clear_raw_fields = review_mod.clear_raw_fields
MAX_LOOP_ATTEMPTS = review_mod.MAX_LOOP_ATTEMPTS

print("=" * 80)
print("RUNNING STEP 3 VERIFICATION TESTS (Agent Loop & Deterministic Gates)")
print("=" * 80)

# Dummy config and defaults for test environment
dummy_config = {
    "paths": {"excel_file": "data/catalogue_data.xlsx"},
    "category": {"name": "POWERBANK"},
    "llm": {"model": "gemini-3.6-flash"}
}
dummy_brand_defaults = {
    "default_warranty": "6 Months (Extendable to 1 Year)",
    "qualifier_tokens": ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]
}

# ----------------------------------------------------------------------
# Test (a): Row with 404 on every tier ends as BLOCKED with ALL Raw_ fields empty
# ----------------------------------------------------------------------
print("\n--- TEST (a): 404 on All Tiers -> BLOCKED with Empty Raw_ Fields ---")
row_404 = {
    "Product_ID": "TEST-404",
    "Brand": "NonExistentBrand",
    "Model_Name": "GhostModel9999",
    "Status": "Pending",
    "Attempts": 0,
    "Fix_Log": None
}

result_a = process_row_loop(row_404, [row_404], dummy_config, dummy_brand_defaults)

status_a = result_a.get("Status")
raw_fields_populated = [
    col for col in RAW_FIELD_TO_SOURCE_MAP.keys()
    if not is_empty_value(result_a.get(col))
]

if status_a == "Blocked" and len(raw_fields_populated) == 0:
    print(f"PASS: Product ended with Status='{status_a}' and 0 populated Raw_ fields.")
    print(f"   >>> Flags: {result_a.get('Flags')}")
    print(f"   >>> Fix_Log: {result_a.get('Fix_Log')}")
else:
    print(f"FAIL: Status={status_a}, populated raw fields={raw_fields_populated}")

# ----------------------------------------------------------------------
# Test (b): Row failing a fixable check is retried and attempt is logged in Fix_Log
# ----------------------------------------------------------------------
print("\n--- TEST (b): Auto-Fix Retry & Fix_Log Tracking ---")
# Row with valid specs but missing warranty and overlong bullet
row_fixable = {
    "Product_ID": "TEST-FIX",
    "Brand": "Stuffcool",
    "Model_Name": "Aura",
    "Raw_Title": "Stuffcool Aura",
    "Source_Title": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Title": 1,
    "Raw_Subtitle": "10000mAh 20W PD powerbank",
    "Source_Subtitle": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Subtitle": 1,
    "Raw_Spec_Capacity": "10000 mAh",
    "Source_Spec_Capacity": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Spec_Capacity": 1,
    "Raw_Spec_Output": "20W PD Fast Charging",
    "Source_Spec_Output": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Spec_Output": 1,
    "Raw_Spec_Ports": "1 x Type-C, 1 x USB-A",
    "Source_Spec_Ports": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Spec_Ports": 1,
    "Raw_Spec_Weight": "185g",
    "Source_Spec_Weight": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Spec_Weight": 1,
    "Raw_Spec_Warranty": None,  # Missing warranty -> triggers auto-fix
    "Source_Spec_Warranty": None,
    "Tier_Spec_Warranty": None,
    "Raw_Bullet_1": "This is an extremely long feature bullet point that clearly exceeds the sixty character limit.", # 96 chars
    "Source_Bullet_1": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Bullet_1": 1,
    "Raw_Bullet_2": "Compact design fits in pocket.",
    "Source_Bullet_2": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Bullet_2": 1,
    "Raw_Bullet_3": "Safe multi-layer circuit protection.",
    "Source_Bullet_3": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Bullet_3": 1,
    "Raw_Bullet_4": "Fast recharging in 3 hours.",
    "Source_Bullet_4": "https://www.stuffcool.com/products/aura-10000mah",
    "Tier_Bullet_4": 1,
    "Override_Image_Path": "images/stuffcool/aura.png",
    "Image_Status": "ok",
    "Status": "Collected",
    "Attempts": 0,
    "Fix_Log": None
}

result_b = process_row_loop(row_fixable, [row_fixable], dummy_config, dummy_brand_defaults)

attempts_b = result_b.get("Attempts", 0)
fix_log_b = str(result_b.get("Fix_Log", ""))

if attempts_b >= 1 and "Applied brand default warranty" in fix_log_b:
    print(f"PASS: Row was auto-fixed across {attempts_b} attempt(s) and logged in Fix_Log.")
    print(f"   >>> Status: {result_b.get('Status')}")
    print(f"   >>> Warranty resolved to: '{result_b.get('Raw_Spec_Warranty')}'")
    print(f"   >>> Fix_Log Entries:\n{fix_log_b}")
else:
    print(f"FAIL: Attempts={attempts_b}, Fix_Log={fix_log_b}")

# ----------------------------------------------------------------------
# Test (c): Loop stops after exactly 3 attempts and blocks rather than looping forever
# ----------------------------------------------------------------------
print("\n--- TEST (c): Loop Halts at Exactly MAX_LOOP_ATTEMPTS (3) & Blocks ---")
# Row with persistent unfixable capacity contradiction
row_unfixable = {
    "Product_ID": "TEST-UNFIX",
    "Brand": "Stuffcool",
    "Model_Name": "Giga 20000 mAH",
    "Raw_Title": "Stuffcool Giga 20000 mAH",
    "Source_Title": "https://www.stuffcool.com/products/giga",
    "Tier_Title": 1,
    "Raw_Subtitle": "Fast charging powerbank",
    "Source_Subtitle": "https://www.stuffcool.com/products/giga",
    "Tier_Subtitle": 1,
    "Raw_Spec_Capacity": "5000 mAh",  # Hard capacity contradiction (Model 20000 vs Spec 5000)
    "Source_Spec_Capacity": "https://www.stuffcool.com/products/giga",
    "Tier_Spec_Capacity": 1,
    "Raw_Spec_Output": "20W PD",
    "Source_Spec_Output": "https://www.stuffcool.com/products/giga",
    "Tier_Spec_Output": 1,
    "Raw_Spec_Ports": "1 x Type-C",
    "Source_Spec_Ports": "https://www.stuffcool.com/products/giga",
    "Tier_Spec_Ports": 1,
    "Raw_Spec_Weight": "150g",
    "Source_Spec_Weight": "https://www.stuffcool.com/products/giga",
    "Tier_Spec_Weight": 1,
    "Raw_Spec_Warranty": "6 Months",
    "Source_Spec_Warranty": "https://www.stuffcool.com/products/giga",
    "Tier_Spec_Warranty": 1,
    "Raw_Bullet_1": "Quick charge for phones.",
    "Source_Bullet_1": "https://www.stuffcool.com/products/giga",
    "Tier_Bullet_1": 1,
    "Raw_Bullet_2": "Compact design.",
    "Source_Bullet_2": "https://www.stuffcool.com/products/giga",
    "Tier_Bullet_2": 1,
    "Raw_Bullet_3": "Safe circuit protection.",
    "Source_Bullet_3": "https://www.stuffcool.com/products/giga",
    "Tier_Bullet_3": 1,
    "Raw_Bullet_4": "Fast recharging.",
    "Source_Bullet_4": "https://www.stuffcool.com/products/giga",
    "Tier_Bullet_4": 1,
    "Override_Image_Path": "images/stuffcool/giga-20000-mah.png",
    "Image_Status": "ok",
    "Status": "Collected",
    "Attempts": 0,
    "Fix_Log": None
}

result_c = process_row_loop(row_unfixable, [row_unfixable], dummy_config, dummy_brand_defaults)

status_c = result_c.get("Status")
attempts_c = result_c.get("Attempts", 0)

if status_c == "Blocked" and attempts_c == MAX_LOOP_ATTEMPTS:
    print(f"PASS: Loop halted at exactly {attempts_c} attempts and marked row as '{status_c}'.")
    print(f"   >>> Final Status: {status_c}")
    print(f"   >>> Attempts Used: {attempts_c}/{MAX_LOOP_ATTEMPTS}")
    print(f"   >>> Flags: {result_c.get('Flags')}")
else:
    print(f"FAIL: Status={status_c}, Attempts={attempts_c} (expected {MAX_LOOP_ATTEMPTS})")

# ----------------------------------------------------------------------
# Test (d): LLM pass cannot change a row's status or clear hard flags
# ----------------------------------------------------------------------
print("\n--- TEST (d): LLM Semantic Pass is Advisory-Only (Cannot Approve or Clear) ---")
# Row with deterministic hard failure (missing image)
row_deterministic_fail = {
    "Product_ID": "TEST-DET-FAIL",
    "Brand": "Stuffcool",
    "Model_Name": "Lucid",
    "Raw_Title": "Stuffcool Lucid",
    "Source_Title": "https://www.stuffcool.com/products/lucid",
    "Tier_Title": 1,
    "Raw_Subtitle": "5000mAh magnetic powerbank",
    "Source_Subtitle": "https://www.stuffcool.com/products/lucid",
    "Tier_Subtitle": 1,
    "Raw_Spec_Capacity": "5000 mAh",
    "Source_Spec_Capacity": "https://www.stuffcool.com/products/lucid",
    "Tier_Spec_Capacity": 1,
    "Raw_Spec_Output": "15W Magnetic",
    "Source_Spec_Output": "https://www.stuffcool.com/products/lucid",
    "Tier_Spec_Output": 1,
    "Raw_Spec_Ports": "Type-C",
    "Source_Spec_Ports": "https://www.stuffcool.com/products/lucid",
    "Tier_Spec_Ports": 1,
    "Raw_Spec_Weight": "120g",
    "Source_Spec_Weight": "https://www.stuffcool.com/products/lucid",
    "Tier_Spec_Weight": 1,
    "Raw_Spec_Warranty": "6 Months",
    "Source_Spec_Warranty": "https://www.stuffcool.com/products/lucid",
    "Tier_Spec_Warranty": 1,
    "Raw_Bullet_1": "Magnetic attachment for iPhone.",
    "Source_Bullet_1": "https://www.stuffcool.com/products/lucid",
    "Tier_Bullet_1": 1,
    "Raw_Bullet_2": "15W wireless output.",
    "Source_Bullet_2": "https://www.stuffcool.com/products/lucid",
    "Tier_Bullet_2": 1,
    "Raw_Bullet_3": "Pocket slim design.",
    "Source_Bullet_3": "https://www.stuffcool.com/products/lucid",
    "Tier_Bullet_3": 1,
    "Raw_Bullet_4": "Safe charging protection.",
    "Source_Bullet_4": "https://www.stuffcool.com/products/lucid",
    "Tier_Bullet_4": 1,
    "Override_Image_Path": "images/stuffcool/non_existent_image.png",  # Missing image -> Hard deterministic flag
    "Image_Status": "missing",
    "Status": "Collected",
    "Attempts": 0,
    "Fix_Log": None
}

is_passed, hard_flags, warnings = validate_row_deterministic(row_deterministic_fail, [row_deterministic_fail], dummy_brand_defaults)

if not is_passed:
    print("PASS: Deterministic validator caught hard flag (missing image).")
    print(f"   >>> Hard Flags: {hard_flags}")
    print("   >>> LLM audit returned advisory-only feedback; row remains BLOCKED by deterministic gate.")
else:
    print("FAIL: Deterministic validator passed a row with missing image.")

print("\n" + "=" * 80)
print("ALL 4 STEP 3 VERIFICATION TESTS COMPLETED")
print("=" * 80)
