import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import fetch_and_parse_url

print("=== VERIFYING BUG 4: Tier 1 Shopify Image Extraction ===")

test_cases = [
    ("Odin", "https://www.stuffcool.com/products/odin-10-000mah-qi2-magsafe-powerbank-with-built-in-type-c-cable"),
    ("Omni pro", "https://www.stuffcool.com/products/omni-pro-qi2-2-certified-25w-magsafe-powerbank-with-apple-watch-charger"),
    ("Click 10", "https://www.stuffcool.com/products/click-10000mah-15w-slim-magnetic-wireless-powerbank-with-natural-titanium-finish-for-iphone"),
    ("1#", "https://www.stuffcool.com/products/1-22-5w-10000mah-powerbank-with-20w-pd-type-c-fast-charging")
]

for name, url in test_cases:
    print(f"\n--- Testing: {name} ---")
    res = fetch_and_parse_url(url, tier=1)
    assert res.success == True, f"Fetch failed for {name}"
    assert len(res.image_urls) > 0, f"No image URLs found for {name}"
    
    primary_img = res.image_urls[0]
    print(f"Primary Image URL: {primary_img}")
    
    # Assert NO video poster or thumbnail in primary image
    for bad_tok in ["preview_images", "thumbnail", "video", "poster"]:
        assert bad_tok not in primary_img.lower(), f"Failed: Primary image contains video/thumbnail token '{bad_tok}'"
    
    # Assert high-res width=2048
    assert "width=2048" in primary_img, "Failed: Primary image missing width=2048"
    print(f"✓ {name} successfully extracted clean high-res master image.")

print("\n>>> ALL BUG 4 VERIFICATIONS PASSED PERFECTLY! <<<")
