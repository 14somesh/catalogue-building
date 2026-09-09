import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.category_specs import (
    extract_category_specs,
    get_category_spec_definition,
    normalize_category_key
)
from src.utils.validators import validate_row_deterministic


class TestCategorySpecs(unittest.TestCase):
    def test_powerbank_spec_extraction(self):
        text = "Stuffcool Mega 10000mAh Powerbank with 30W Fast Charging Type C PD Output, 2 Ports, weight 210g"
        specs = extract_category_specs(text, "Powerbank")
        self.assertEqual(specs.get("capacity"), "10000mAh")
        self.assertIn("30W", specs.get("output", ""))
        self.assertIn("Type-C", specs.get("ports", ""))
        self.assertEqual(specs.get("weight"), "210g")

    def test_tws_spec_extraction(self):
        text = "Pebble STRIKER Buds Truly Wireless Earbuds with 40 Hours Playtime, Quad Mic ENC, 13mm Drivers, Bluetooth v5.3 and Type-C Fast Charging"
        specs = extract_category_specs(text, "TWS")
        self.assertIn("40 Hours", specs.get("playtime", ""))
        self.assertIn("13mm", specs.get("drivers", ""))
        self.assertIn("Quad Mic ENC", specs.get("noise_cancellation", ""))
        self.assertIn("5.3", specs.get("bluetooth", ""))

    def test_smartwatch_spec_extraction(self):
        text = "Pebble Cosmos Ultra 1.91 Inch HD Display Smartwatch, BT Calling, 7 Days Battery Life, IP68 Water Resistant"
        specs = extract_category_specs(text, "Smartwatch")
        self.assertIn("1.91", specs.get("display", ""))
        self.assertIn("Calling", specs.get("calling", ""))
        self.assertIn("7 Days", specs.get("battery", ""))
        self.assertIn("IP68", specs.get("water_resistance", ""))

    def test_validator_powerbank_requires_two_specs(self):
        # Powerbank with 0 specs should fail check e
        pb_row = {
            "Brand": "Pebble",
            "Category": "Powerbank",
            "Model_Name": "Pebble PB 10000mAh",
            "Raw_Title": "Pebble PB 10000mAh Powerbank",
            "Bullets": "• Great powerbank\n• Sleek finish",
            "Subtitle": "Portable Battery",
            "MRP": 1999,
            "Offer_Price": 999,
            "Spec_Capacity": "",
            "Spec_Output": "",
            "Spec_Ports": "",
            "Spec_Weight": ""
        }
        passed, hard_flags, warnings = validate_row_deterministic(pb_row)
        self.assertFalse(passed)
        self.assertTrue(any("Insufficient specifications" in f for f in hard_flags))

    def test_validator_tws_passes_with_bullets_and_specs(self):
        # TWS with bullets and audio specs passes cleanly
        tws_row = {
            "Brand": "Pebble",
            "Category": "TWS",
            "Model_Name": "Pebble STRIKER Buds",
            "Raw_Title": "Pebble STRIKER Buds Truly Wireless Earbuds",
            "Bullets": "• 40 Hours Playtime with Type-C fast charging\n• Quad Mic ENC for crystal clear calls\n• 13mm dynamic bass boost drivers",
            "Subtitle": "True Wireless Earbuds",
            "MRP": 2999,
            "Offer_Price": 1299,
            "Spec_Capacity": "40 Hours Playtime",
            "Spec_Output": "13mm Drivers",
            "Spec_Ports": "Quad Mic ENC",
            "Spec_Weight": "BT 5.3"
        }
        passed, hard_flags, warnings = validate_row_deterministic(tws_row)
        # Hard check c (capacity mismatch) and hard check e should NOT flag for TWS
        spec_or_cap_flags = [f for f in hard_flags if "Insufficient specifications" in f or "Capacity mismatch" in f]
        self.assertEqual(len(spec_or_cap_flags), 0)


if __name__ == "__main__":
    unittest.main()
