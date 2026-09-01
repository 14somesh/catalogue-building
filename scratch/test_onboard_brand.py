import os
import sys
import unittest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.onboard_brand import (
    analyze_price_sheet,
    generate_onboarding_summary,
    register_brand_config,
    append_products_to_catalogue,
    BrandInferenceSchema,
    RawProductItem
)

class TestOnboardBrand(unittest.TestCase):

    def test_mock_inference_and_summary_generation(self):
        # Create a mock inference schema
        inference = BrandInferenceSchema(
            brand_name="Anker",
            brand_code="ANK",
            domain="anker.com",
            platform="shopify",
            qualifier_tokens=[
                {"token": "Plus", "rationale": "High capacity variant modifier (e.g. Anker 537 vs 537 Plus)"},
                {"token": "Pro", "rationale": "High wattage premium variant modifier"},
                {"token": "Mini", "rationale": "Ultra-compact form factor modifier"}
            ],
            dp_column_explanation="Read 'Dealer Net (INR)' as DP (₹1,850) and 'MSRP' as MRP (₹3,499)",
            products=[
                RawProductItem(
                    raw_text="ANK-537-BK Anker 537 Power Bank 24000mAh 65W Black",
                    model_name="537 Power Bank 24000mAh 65W",
                    display_name="537",
                    dp=3499.0,
                    mrp=5999.0,
                    notes="Stripped SKU prefix ANK-537-BK and color Black"
                ),
                RawProductItem(
                    raw_text="ANK-622-WH Anker 622 Magnetic Battery (MagGo) 5000mAh White",
                    model_name="622 Magnetic Battery MagGo 5000mAh",
                    display_name="622 MagGo",
                    dp=1850.0,
                    mrp=3499.0,
                    notes="Stripped SKU prefix and color White"
                )
            ]
        )

        summary_md = generate_onboarding_summary(inference)
        print("\n--- GENERATED SUMMARY MARKDOWN ---")
        print(summary_md)
        print("----------------------------------\n")

        self.assertIn("Anker", summary_md)
        self.assertIn("537", summary_md)
        self.assertIn("622 MagGo", summary_md)
        self.assertIn("PB-ANK-001", summary_md)
        self.assertIn("PB-ANK-002", summary_md)
        self.assertIn("High capacity variant modifier", summary_md)

if __name__ == "__main__":
    unittest.main()
