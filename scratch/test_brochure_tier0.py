import os
import sys
import unittest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parsers.brochure import (
    find_brochure_pdfs,
    parse_brochure_for_model,
    parse_specs_from_text
)

class TestBrochureTier0(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs("brochures/testbrand", exist_ok=True)
        # Create a test PDF image with two products in a grid
        img = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        # Product 1: TestBrand Power 10
        draw.rectangle([50, 50, 1150, 750], outline=(200, 200, 200), width=2)
        draw.text((80, 80), "TestBrand Power 10 Powerbank", fill=(0, 0, 0))
        draw.text((80, 120), "10000 mAh High Capacity Battery", fill=(0, 0, 0))
        draw.text((80, 160), "22.5W Fast Charging Output", fill=(0, 0, 0))
        draw.text((80, 200), "Ports: Dual USB-A and Type-C", fill=(0, 0, 0))
        draw.text((80, 240), "Weight: 195g | 1 Year Warranty", fill=(0, 0, 0))
        
        # Product 2: TestBrand Turbo 20
        draw.rectangle([50, 850, 1150, 1550], outline=(200, 200, 200), width=2)
        draw.text((80, 880), "TestBrand Turbo 20 Powerbank", fill=(0, 0, 0))
        draw.text((80, 920), "20000 mAh Massive Battery", fill=(0, 0, 0))
        draw.text((80, 960), "65W Turbo PD Output", fill=(0, 0, 0))
        draw.text((80, 1000), "Ports: Built-in Type-C Cable", fill=(0, 0, 0))
        draw.text((80, 1040), "Weight: 380g | 6 Months Warranty", fill=(0, 0, 0))

        cls.pdf_path = "brochures/testbrand/catalog_2026.pdf"
        img.save(cls.pdf_path, "PDF", resolution=150.0)

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        if os.path.exists(cls.pdf_path):
            try:
                os.remove(cls.pdf_path)
            except Exception:
                pass
        if os.path.exists("brochures/testbrand"):
            try:
                os.rmdir("brochures/testbrand")
            except Exception:
                pass

    def test_silent_skip_when_no_brochure(self):
        """Test that brands without brochures return None silently."""
        res = parse_brochure_for_model(
            brand="NonExistentBrand",
            model_name="Phantom 100",
            qualifier_tokens=["Pro", "Max"],
            config={}
        )
        self.assertIsNone(res, "Expected None when no brochure exists")

    def test_find_brochure_pdfs(self):
        """Test finding brochure PDFs in brand directory."""
        pdfs = find_brochure_pdfs("TestBrand")
        self.assertEqual(len(pdfs), 1)
        self.assertIn("catalog_2026.pdf", pdfs[0])

    def test_brochure_vision_extraction_on_multi_product_page(self):
        """Test that Gemini Vision extracts the correct product's specs from the multi-product brochure page."""
        config = {"llm": {"provider": "gemini", "model": "gemini-3.6-flash"}}
        
        # Test extracting Product 1: Power 10
        res1 = parse_brochure_for_model(
            brand="TestBrand",
            model_name="Power 10",
            qualifier_tokens=["Turbo", "Pro", "Max"],
            config=config
        )
        self.assertIsNotNone(res1)
        print("\n[Brochure Tier 0 - Power 10 Result]")
        print(f"  URL/Source: {res1.url}")
        print(f"  Tier: {res1.tier}")
        print(f"  Specs: {res1.specs}")
        print(f"  Field Sources: {res1.field_sources}")
        print(f"  Field Tiers: {res1.field_tiers}")
        
        self.assertIn("10000", str(res1.specs.get("capacity")))
        self.assertIn("22.5W", str(res1.specs.get("output")))
        self.assertEqual(res1.field_tiers.get("capacity"), "0-vision")

        # Test extracting Product 2: Turbo 20
        res2 = parse_brochure_for_model(
            brand="TestBrand",
            model_name="Turbo 20",
            qualifier_tokens=["Pro", "Max"],
            config=config
        )
        self.assertIsNotNone(res2)
        print("\n[Brochure Tier 0 - Turbo 20 Result]")
        print(f"  URL/Source: {res2.url}")
        print(f"  Tier: {res2.tier}")
        print(f"  Specs: {res2.specs}")
        
        self.assertIn("20000", str(res2.specs.get("capacity")))
        self.assertIn("65W", str(res2.specs.get("output")))
        self.assertEqual(res2.field_tiers.get("capacity"), "0-vision")

if __name__ == "__main__":
    unittest.main()
