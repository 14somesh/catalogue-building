import os
import sys
import unittest
import importlib
from typing import Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.validators import validate_row_deterministic
review_mod = importlib.import_module("src.3_review")
is_contradiction_flag = review_mod.is_contradiction_flag
process_row_loop = review_mod.process_row_loop

from src.run_brand import generate_run_report
collect_mod = importlib.import_module("src.1_collect")
execute_vision_fallback_for_page = collect_mod.execute_vision_fallback_for_page

print("=== RUNNING SELF-CORRECTION CLOSURE VERIFICATION SUITE ===")

class TestSelfCorrectionClosure(unittest.TestCase):

    def test_partial_data_acceptance_3_of_5(self):
        """Test that 3 of 5 specs passes validation as Ready_For_Review with warnings."""
        row = {
            "Product_ID": "TEST-001",
            "Brand": "Stuffcool",
            "Model_Name": "Alpha 10000",
            "Raw_Title": "Stuffcool Alpha 10000mAh Powerbank",
            "Source_Title": "https://stuffcool.com/alpha",
            "Raw_Subtitle": "Fast charging powerbank.",
            "Source_Subtitle": "https://stuffcool.com/alpha",
            "Raw_Spec_Capacity": "10000 mAh",
            "Source_Spec_Capacity": "https://stuffcool.com/alpha",
            "Raw_Spec_Output": "20W Fast Charging",
            "Source_Spec_Output": "https://stuffcool.com/alpha",
            "Raw_Spec_Ports": "Type-C, USB-A",
            "Source_Spec_Ports": "https://stuffcool.com/alpha",
            "Raw_Spec_Weight": None,   # Missing (1/5 missing)
            "Raw_Spec_Warranty": None, # Missing (2/5 missing) -> 3/5 populated!
            "Raw_Bullet_1": "10000mAh high-density battery cell.",
            "Source_Bullet_1": "https://stuffcool.com/alpha",
            "Raw_Bullet_2": "20W Type-C fast charging output.",
            "Source_Bullet_2": "https://stuffcool.com/alpha",
            "Raw_Bullet_3": "Dual port charging capabilities.",
            "Source_Bullet_3": "https://stuffcool.com/alpha",
            "Raw_Bullet_4": "Compact design for easy travel.",
            "Source_Bullet_4": "https://stuffcool.com/alpha",
            "Override_Image_Path": "images/stuffcool/alpha.png"
        }
        
        # Create a dummy image file so image validation passes
        os.makedirs("images/stuffcool", exist_ok=True)
        from PIL import Image
        img = Image.new("RGB", (800, 800), color=(255, 255, 255))
        img.save("images/stuffcool/alpha.png")

        is_passed, hard_flags, warnings = validate_row_deterministic(row)
        print(f"\n[3 of 5 Specs Test] is_passed={is_passed}, hard_flags={hard_flags}, warnings={warnings}")
        self.assertTrue(is_passed, f"Expected 3/5 specs to pass, but failed with hard flags: {hard_flags}")
        self.assertTrue(any("Partial specifications" in w for w in warnings), "Expected partial specifications warning")

    def test_insufficient_data_rejection_fewer_than_3(self):
        """Test that fewer than 3 of 5 specs fails validation."""
        row = {
            "Product_ID": "TEST-002",
            "Brand": "Stuffcool",
            "Model_Name": "Beta 10000",
            "Raw_Title": "Stuffcool Beta 10000mAh Powerbank",
            "Source_Title": "https://stuffcool.com/beta",
            "Raw_Spec_Capacity": "10000 mAh",
            "Source_Spec_Capacity": "https://stuffcool.com/beta",
            "Raw_Spec_Output": None,   # 1/5
            "Raw_Spec_Ports": None,    # 2/5
            "Raw_Spec_Weight": None,   # 3/5
            "Raw_Spec_Warranty": None, # 4/5 -> only 1/5 populated!
            "Raw_Bullet_1": "10000mAh high-density battery cell.",
            "Source_Bullet_1": "https://stuffcool.com/beta",
            "Raw_Bullet_2": "20W Type-C fast charging output.",
            "Source_Bullet_2": "https://stuffcool.com/beta",
            "Raw_Bullet_3": "Dual port charging capabilities.",
            "Source_Bullet_3": "https://stuffcool.com/beta",
            "Raw_Bullet_4": "Compact design for easy travel.",
            "Source_Bullet_4": "https://stuffcool.com/beta",
            "Override_Image_Path": "images/stuffcool/alpha.png"
        }
        is_passed, hard_flags, warnings = validate_row_deterministic(row)
        print(f"\n[1 of 5 Specs Test] is_passed={is_passed}, hard_flags={hard_flags}")
        self.assertFalse(is_passed)
        self.assertTrue(any("Insufficient specifications" in hf for hf in hard_flags))

    def test_contradiction_vs_exhaustion_categorization(self):
        """Test that is_contradiction_flag accurately separates Contradictions from Exhaustions."""
        # Contradictions -> Must be BLOCKED
        self.assertTrue(is_contradiction_flag("Qualifier token mismatch: candidate contains 'Max'"))
        self.assertTrue(is_contradiction_flag("Capacity mismatch: Model specifies 10000 but collected is 20000"))
        self.assertTrue(is_contradiction_flag("Duplicate image asset: identical file hash"))
        self.assertTrue(is_contradiction_flag("Image filename mismatch: expected 'foo.png'"))
        self.assertTrue(is_contradiction_flag("FATAL BUG: Field 'Raw_Title' populated without verified source"))
        
        # Exhaustions -> Must be SKIPPED
        self.assertFalse(is_contradiction_flag("All spec tiers exhausted without finding technical specifications"))
        self.assertFalse(is_contradiction_flag("Insufficient specifications: 3 of 5 required specs are empty"))
        self.assertFalse(is_contradiction_flag("Image missing on disk at 'images/foo.png'"))

    def test_run_report_includes_skipped_rows(self):
        """Test that generate_run_report produces a dedicated SKIPPED ROWS section."""
        sample_rows = [
            {
                "Product_ID": "PB-SC-001",
                "Brand": "Stuffcool",
                "Model_Name": "Aura",
                "Status": "Ready_For_Review",
                "Raw_Title": "Stuffcool Aura",
                "Source_URL": "https://stuffcool.com/aura",
                "Attempts": 1
            },
            {
                "Product_ID": "PB-SC-002",
                "Brand": "Stuffcool",
                "Model_Name": "Ghost SKU",
                "Status": "Skipped",
                "Flags": "Skipped: All spec tiers exhausted (including Vision) without finding technical specifications",
                "Attempts": 3,
                "Fix_Log": "Attempt 1: Tier 1-4 exhausted\nAttempt 2: Vision fallback tried, 0 specs\nAttempt 3: Exhausted"
            },
            {
                "Product_ID": "PB-SC-003",
                "Brand": "Stuffcool",
                "Model_Name": "Conflict SKU",
                "Status": "Blocked",
                "Flags": "Hard Block: Capacity mismatch: Model specifies 10000 but collected is 20000",
                "Attempts": 3,
                "Fix_Log": "Attempt 1: Sibling mismatch"
            }
        ]
        
        report_path = generate_run_report("Stuffcool", sample_rows, output_dir="dist/test")
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        print("\n[Run Report Test]")
        print("Generated report header snippet:")
        print("\n".join(content.split("\n")[:25]))
        
        self.assertIn("## ⏭️ SKIPPED ROWS (Data Exhausted / Insufficient Specs)", content)
        self.assertIn("## ⛔ BLOCKED ROWS (Requires Human Resolution)", content)
        self.assertIn("### ⚪ [PB-SC-002] Stuffcool Ghost SKU", content)
        self.assertIn("### 🔴 [PB-SC-003] Stuffcool Conflict SKU", content)
        self.assertIn("Total Products:** 3 | **Ready for Review:** 1 | **Blocked (Needs Human):** 1 | **Skipped (Exhausted):** 1", content)

if __name__ == "__main__":
    unittest.main()
