import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import reject_qualifier_mismatch

print("=== VERIFYING BUG 1: Qualifier Token Check ===")

# Test 1: Roam+ 20000mAh Mini wired Powerbank vs Roam Plus -> MUST MATCH
valid_1, reason_1 = reject_qualifier_mismatch(
    target_model_name="Roam Plus",
    candidate_title="Roam+ 20000mAh Mini wired Powerbank with 20W Type C Output",
    brand="Stuffcool"
)
print(f"Test 1 (Roam+ vs Roam Plus): is_valid = {valid_1} (Reason: {reason_1})")
assert valid_1 == True, f"Failed Test 1: {reason_1}"

# Test 2: Giga Max 25000mAh vs Giga 20000 mAH -> MUST BE REJECTED
valid_2, reason_2 = reject_qualifier_mismatch(
    target_model_name="Giga 20000 mAH",
    candidate_title="Giga Max smallest 25000mAh powerbank with 100W Built-in Type-C Cable",
    brand="Stuffcool"
)
print(f"Test 2 (Giga Max vs Giga 20000 mAH): is_valid = {valid_2} (Reason: {reason_2})")
assert valid_2 == False, "Failed Test 2: Giga Max should have been rejected!"

# Test 3: Major Ultra vs Major 10000 mAH -> MUST BE REJECTED
valid_3, reason_3 = reject_qualifier_mismatch(
    target_model_name="Major 10000 mAH",
    candidate_title="Major Ultra 65W PD Super Fast Charging 20000mAh Powerbank",
    brand="Stuffcool"
)
print(f"Test 3 (Major Ultra vs Major 10000 mAH): is_valid = {valid_3} (Reason: {reason_3})")
assert valid_3 == False, "Failed Test 3: Major Ultra should have been rejected!"

# Test 4: Lucid Plus vs Lucid -> MUST BE REJECTED
valid_4, reason_4 = reject_qualifier_mismatch(
    target_model_name="Lucid",
    candidate_title="Lucid Plus 15W Magnetic Wireless 10000mAh Powerbank with stand",
    brand="Stuffcool"
)
print(f"Test 4 (Lucid Plus vs Lucid): is_valid = {valid_4} (Reason: {reason_4})")
assert valid_4 == False, "Failed Test 4: Lucid Plus should have been rejected!"

print("\n>>> ALL BUG 1 TESTS PASSED PERFECTLY! <<<")
