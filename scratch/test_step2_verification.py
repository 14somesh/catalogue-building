import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import validate_write_guard, DataLayerInvariantViolation
from src.utils.scraper import reject_qualifier_mismatch, is_boilerplate_bullet

print("=" * 80)
print("RUNNING STEP 2 VERIFICATION TESTS")
print("=" * 80)

# ----------------------------------------------------------------------
# Test (a): Write Guard raises when a Raw_ field is written with an empty Source_
# ----------------------------------------------------------------------
print("\n--- TEST (a): Write Guard Enforcement ---")
test_row_invalid = {
    "Product_ID": "TEST-001",
    "Brand": "Stuffcool",
    "Model_Name": "Phantom",
    "Raw_Title": "Stuffcool Phantom 20000mAh",
    "Source_Title": "",  # Empty Source_ field
    "Source_URL": None,  # Empty Source_URL
    "Raw_Bullet_1": "20W fast charging capability",
    "Source_Bullet_1": None,
}

try:
    validate_write_guard(test_row_invalid)
    print("❌ FAIL: Write Guard did not raise an exception on missing Source_!")
except DataLayerInvariantViolation as e:
    print(f"✅ PASS: Write Guard raised DataLayerInvariantViolation as expected:\n   >>> {e}")

# Also verify that Override_ columns are exempt
test_row_override = {
    "Product_ID": "TEST-002",
    "Brand": "Stuffcool",
    "Model_Name": "Lucid",
    "Raw_Title": None,
    "Source_Title": None,
    "Override_Title": "Stuffcool Lucid",  # Human override
    "Override_Bullet_1": "Smallest 5000mAh magnetic wireless powerbank.",
}
try:
    validate_write_guard(test_row_override)
    print("✅ PASS: Human Override_ fields are exempt from Write Guard.")
except Exception as e:
    print(f"❌ FAIL: Human Override_ fields triggered an error: {e}")

# ----------------------------------------------------------------------
# Test (b): Qualifier check rejects "Giga Max 25000mAh" for Model_Name "Giga 20000 mAH"
# ----------------------------------------------------------------------
print("\n--- TEST (b): Qualifier Token Mismatch Check ---")
target_model = "Giga 20000 mAH"
candidate_title = "Stuffcool Giga Max 65W 20000mAh Powerbank"
is_valid, reason = reject_qualifier_mismatch(target_model, candidate_title)

if not is_valid:
    print(f"✅ PASS: Qualifier check correctly rejected '{candidate_title}' for '{target_model}'.")
    print(f"   >>> Rejection Reason: {reason}")
else:
    print(f"❌ FAIL: Qualifier check accepted mismatched candidate '{candidate_title}'.")

# Also test a valid matching sibling
valid_candidate = "Stuffcool Giga 65W 20000mAh QC/PD Powerbank"
is_valid_clean, _ = reject_qualifier_mismatch(target_model, valid_candidate)
if is_valid_clean:
    print(f"✅ PASS: Qualifier check correctly accepted valid candidate '{valid_candidate}'.")
else:
    print(f"❌ FAIL: Qualifier check falsely rejected clean candidate '{valid_candidate}'.")

# ----------------------------------------------------------------------
# Test (c): Boilerplate detector rejects a bullet containing "leading Indian brand"
# ----------------------------------------------------------------------
print("\n--- TEST (c): Boilerplate Detector Check ---")
test_bullet_boilerplate = "Stuffcool is a leading Indian brand offering premium accessories."
is_bp, bp_phrase = is_boilerplate_bullet(test_bullet_boilerplate)

if is_bp:
    print(f"✅ PASS: Boilerplate detector correctly rejected bullet: '{test_bullet_boilerplate}'")
    print(f"   >>> Detected Boilerplate Phrase: '{bp_phrase}'")
else:
    print(f"❌ FAIL: Boilerplate detector missed phrase in '{test_bullet_boilerplate}'.")

# Test a clean spec-based bullet
clean_bullet = "20W Type-C PD port charges iPhone 50% in 30 mins."
is_bp_clean, _ = is_boilerplate_bullet(clean_bullet)
if not is_bp_clean:
    print(f"✅ PASS: Boilerplate detector accepted clean feature bullet: '{clean_bullet}'")
else:
    print(f"❌ FAIL: Boilerplate detector falsely flagged clean bullet '{clean_bullet}'.")

# ----------------------------------------------------------------------
# Test (d): Four Mandatory Matcher & Qualifier Regression Invariants
# ----------------------------------------------------------------------
print("\n--- TEST (d): Four Mandatory Matcher & Qualifier Regression Invariants ---")
from src.utils.scraper import score_candidate_match

qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

# 1. "Giga Max 25000mAh" must be REJECTED for Model_Name "Giga 20000 mAH"
score1, valid1, reason1 = score_candidate_match("Giga 20000 mAH", "Giga Max 25000mAh", "https://stuffcool.com/products/giga-max", brand="Stuffcool", qualifier_tokens=qualifiers)
if not valid1:
    print(f"✅ PASS Case 1: 'Giga Max 25000mAh' correctly REJECTED for 'Giga 20000 mAH'. (Reason: {reason1})")
else:
    print(f"❌ FAIL Case 1: 'Giga Max 25000mAh' was falsely ACCEPTED for 'Giga 20000 mAH'.")

# 2. "Roam+ 20000mAh Mini wired Powerbank" must MATCH Model_Name "Roam Plus"
score2, valid2, reason2 = score_candidate_match("Roam Plus", "Roam+ 20000mAh Mini wired Powerbank", "https://stuffcool.com/products/roam-plus", brand="Stuffcool", qualifier_tokens=qualifiers)
if valid2 and score2 > 0:
    print(f"✅ PASS Case 2: 'Roam+ 20000mAh Mini wired Powerbank' correctly MATCHED 'Roam Plus'. (Score: {score2:.1f})")
else:
    print(f"❌ FAIL Case 2: 'Roam+ 20000mAh Mini wired Powerbank' failed to match 'Roam Plus'. (Reason: {reason2})")

# 3. "Luxcell Wireless Mini 10K" must be REJECTED for Model_Name "Luxcell Mini"
score3, valid3, reason3 = score_candidate_match("Luxcell Mini", "Luxcell Wireless Mini 10K", "https://portronics.com/products/luxcell-wireless-mini", brand="Portronics", qualifier_tokens=qualifiers)
if not valid3:
    print(f"✅ PASS Case 3: 'Luxcell Wireless Mini 10K' correctly REJECTED for 'Luxcell Mini'. (Reason: {reason3})")
else:
    print(f"❌ FAIL Case 3: 'Luxcell Wireless Mini 10K' was falsely ACCEPTED for 'Luxcell Mini'.")

# 4. "Luxcell Wireless Mini 10K" must MATCH Model_Name "Luxcell Wireless Mini"
score4, valid4, reason4 = score_candidate_match("Luxcell Wireless Mini", "Luxcell Wireless Mini 10K", "https://portronics.com/products/luxcell-wireless-mini", brand="Portronics", qualifier_tokens=qualifiers)
if valid4 and score4 > 0:
    print(f"✅ PASS Case 4: 'Luxcell Wireless Mini 10K' correctly MATCHED 'Luxcell Wireless Mini'. (Score: {score4:.1f})")
else:
    print(f"❌ FAIL Case 4: 'Luxcell Wireless Mini 10K' failed to match 'Luxcell Wireless Mini'. (Reason: {reason4})")

print("\n" + "=" * 80)
print("ALL VERIFICATION TESTS COMPLETED")
print("=" * 80)
