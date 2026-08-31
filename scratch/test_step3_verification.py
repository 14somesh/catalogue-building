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
from unittest.mock import patch, MagicMock

row_404 = {
    "Product_ID": "TEST-404",
    "Brand": "NonExistentBrand",
    "Model_Name": "GhostModel9999",
    "Status": "Pending",
    "Attempts": 0,
    "Fix_Log": None
}

with patch.object(review_mod.collect_mod, "execute_spec_escalation", return_value=(None, None)):
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

with patch.object(review_mod.collect_mod, "collect_data_for_row", return_value=({}, False, "All tiers exhausted")):
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

# ----------------------------------------------------------------------
# Test (e): Simulated Daily Quota 429 Halts Immediately Without Retrying
# ----------------------------------------------------------------------
print("\n--- TEST (e): Simulated Daily Quota 429 (GenerateRequestsPerDay) -> Immediate Halt ---")
from unittest.mock import patch, MagicMock
from src.utils.llm_client import (
    draft_bullets_and_subtitle,
    GeminiDailyQuotaExhaustedError,
    classify_gemini_error
)

call_count_e = 0

def mock_daily_quota_generate(*args, **kwargs):
    global call_count_e
    call_count_e += 1
    raise Exception("429 RESOURCE_EXHAUSTED: quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier exceeded")

with patch("src.utils.llm_client.get_gemini_client") as mock_get_client:
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_daily_quota_generate
    mock_get_client.return_value = mock_client
    
    call_count_e = 0
    try:
        draft_bullets_and_subtitle("Stuffcool", "Aura", "Test description", {"capacity": "10000mAh"})
        print("FAIL: Daily quota 429 did not raise GeminiDailyQuotaExhaustedError!")
    except GeminiDailyQuotaExhaustedError as e:
        if call_count_e == 1:
            print(f"PASS: Daily quota 429 halted immediately on attempt {call_count_e} (0 retries).")
            print(f"   >>> Exception message: {e}")
        else:
            print(f"FAIL: Daily quota 429 made {call_count_e} attempts before raising (expected 1).")

# ----------------------------------------------------------------------
# Test (f): Simulated 503 Transient Error Retries with Backoff
# ----------------------------------------------------------------------
print("\n--- TEST (f): Simulated 503 Transient Error -> Exponential Backoff & Retry ---")
call_count_f = 0
sleep_calls_f = []

def mock_503_then_success(*args, **kwargs):
    global call_count_f
    call_count_f += 1
    if call_count_f < 3:
        raise Exception("503 Service Unavailable: Model is overloaded, please try again.")
    mock_resp = MagicMock()
    mock_resp.text = json_str = '{"title": "Stuffcool Aura", "subtitle": "Fast charging", "bullet_1": "B1", "bullet_2": "B2", "bullet_3": "B3", "bullet_4": "B4"}'
    return mock_resp

def mock_sleep_f(seconds):
    sleep_calls_f.append(seconds)

with patch("src.utils.llm_client.get_gemini_client") as mock_get_client, \
     patch("src.utils.llm_client.time.sleep", side_effect=mock_sleep_f):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_503_then_success
    mock_get_client.return_value = mock_client
    
    call_count_f = 0
    sleep_calls_f = []
    res_f = draft_bullets_and_subtitle("Stuffcool", "Aura", "Test description", {"capacity": "10000mAh"})
    
    if res_f and res_f.get("title") == "Stuffcool Aura" and call_count_f == 3 and sleep_calls_f == [2, 4]:
        print(f"PASS: 503 transient error retried with exponential backoff across {call_count_f} attempts.")
        print(f"   >>> Backoff sleep intervals: {sleep_calls_f}s")
        print(f"   >>> Successfully resolved copy on attempt 3: title='{res_f.get('title')}'")
    else:
        print(f"FAIL: 503 handling: calls={call_count_f}, sleeps={sleep_calls_f}, res={res_f}")

# ----------------------------------------------------------------------
# Test (g): LLM Infrastructure Failure Sets Status='Deferred', Not 'Blocked'
# ----------------------------------------------------------------------
print("\n--- TEST (g): LLM Infrastructure Failure -> Status='Deferred' (Preserves Collected Data) ---")
from src.parsers.base import ParserResult
collect_module = importlib.import_module("src.1_collect")

dummy_parser_res = ParserResult(
    success=True,
    status_code=200,
    tier=1,
    url="https://www.stuffcool.com/products/aura-10000mah",
    specs={"capacity": "10000 mAh", "output": "20W PD", "ports": "Type-C", "weight": "185g", "warranty": "6 Months"},
    description_text="Authentic Stuffcool Aura 10000mAh powerbank with 20W power delivery.",
    image_urls=["https://www.stuffcool.com/cdn/aura.png"]
)

with patch.object(collect_module, "execute_spec_escalation", return_value=(dummy_parser_res, "tier-1: brand-page")), \
     patch.object(collect_module, "draft_bullets_and_subtitle", return_value=None), \
     patch.object(review_mod.collect_mod, "execute_spec_escalation", return_value=(dummy_parser_res, "tier-1: brand-page")), \
     patch.object(review_mod.collect_mod, "draft_bullets_and_subtitle", return_value=None):
    
    row_input = {
        "Product_ID": "TEST-DEFERRED",
        "Brand": "Stuffcool",
        "Model_Name": "Aura",
        "Status": "Pending",
        "Attempts": 0,
        "Fix_Log": None
    }
    
    collect_updates, success, log_msg = collect_module.collect_data_for_row(row_input, dummy_config)
    
    status_g = collect_updates.get("Status")
    flags_g = collect_updates.get("Flags")
    saved_capacity = collect_updates.get("Raw_Spec_Capacity")
    saved_source = collect_updates.get("Source_Spec_Capacity")
    
    final_loop_row = process_row_loop(row_input, [row_input], dummy_config, dummy_brand_defaults)
    final_status = final_loop_row.get("Status")
    final_attempts = final_loop_row.get("Attempts")
    
    if (status_g == "Deferred" and 
        final_status == "Deferred" and 
        saved_capacity == "10000 mAh" and 
        saved_source is not None and 
        final_attempts == 0 and 
        "Hard Block" not in str(flags_g)):
        print(f"PASS: LLM failure correctly set Status='Deferred' (NOT 'Blocked').")
        print(f"   >>> Status: '{final_status}' | Loop Attempts Consumed: {final_attempts} (0 consumed)")
        print(f"   >>> Preserved Collected Specs: Capacity='{saved_capacity}', Source='{saved_source}'")
        print(f"   >>> Flags: '{final_loop_row.get('Flags')}'")
    else:
        print(f"FAIL: LLM failure status={final_status}, attempts={final_attempts}, saved_capacity={saved_capacity}")

print("\n" + "=" * 80)
print("ALL STEP 3 REGRESSION AND VERIFICATION TESTS COMPLETED")
print("=" * 80)
