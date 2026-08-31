import os
import sys
import re
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

urls = [
    "https://www.stuffcool.com/products/odin-10-000mah-qi2-magsafe-powerbank-with-built-in-type-c-cable",
    "https://www.stuffcool.com/products/omni-pro-qi2-2-certified-25w-magsafe-powerbank-with-apple-watch-charger",
    "https://www.stuffcool.com/products/click-10000mah-15w-slim-magnetic-wireless-powerbank-with-natural-titanium-finish-for-iphone",
    "https://www.stuffcool.com/products/aura-10000mah-15w-magnetic-wireless-powerbank-with-gold-finish"
]

print("=== Inspecting Main Product Images on Shopify Store Pages ===")

for u in urls:
    r = requests.get(u, headers=DEFAULT_HEADERS, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    
    # 1. Check og:image
    og_img = soup.find("meta", property="og:image")
    og_src = og_img["content"] if og_img else None
    
    # 2. Check all gallery images
    all_imgs = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-master")
        if src and ("cdn/shop" in src or "cdn.shopify" in src):
            # Check exclusions
            is_video_poster = any(ign in src.lower() for ign in ["preview_images", "thumbnail", "video", "poster"])
            is_icon = any(ign in src.lower() for ign in ["icon", "logo", "badge", "payment", "flag", "star", "review"])
            if not is_video_poster and not is_icon:
                all_imgs.append(src)
                
    print(f"\nURL: {u.split('/')[-1]}")
    print(f"  • og:image: {og_src}")
    print(f"  • Filtered main product photos ({len(all_imgs)}):")
    for src in all_imgs[:3]:
        print(f"    - {src}")
