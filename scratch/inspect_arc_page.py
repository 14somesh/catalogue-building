import os
import re
import sys
import json
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.scraper import DEFAULT_HEADERS, clean_html_text
from src.parsers.brochure import parse_specs_from_text
from src.utils.llm_client import extract_specs_via_vision

def inspect_arc():
    url = "https://www.urbnworld.com/products/urbn-10000-mah-arc-magtag-power-bank"
    print("=" * 80)
    print(f"INVESTIGATING ARC PRODUCT PAGE: {url}")
    print("=" * 80)
    
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
    print(f"HTTP Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Failed fetching page: {r.status_code}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    
    # 1. Inspect HTML Text Layer
    print("\n--- 1. HTML TEXT & SPECIFICATION CONTENT ---")
    body_text = clean_html_text(r.text)
    print(f"Total clean text length: {len(body_text)} chars")
    
    # Extract any specs from text
    text_specs = parse_specs_from_text(body_text)
    print(f"Deterministic Specs from HTML: {text_specs}")
    
    # Check Shopify JSON-LD / Product JSON
    scripts = soup.find_all("script", type="application/ld+json")
    for s in scripts:
        try:
            d = json.loads(s.string or "{}")
            if isinstance(d, dict) and d.get("@type") == "Product":
                print("JSON-LD Product Description:", d.get("description", "")[:200])
        except Exception:
            pass

    # 2. Inspect Images on the Page
    print("\n--- 2. ALL IMAGES FOUND ON PAGE ---")
    candidate_img_urls = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-master")
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif not src.startswith("http"):
                src = f"https://www.urbnworld.com{src}"
            
            src_lower = src.lower()
            if not any(ign in src_lower for ign in ["logo", "wa-logo", "free_shipping", "warranty.gif", "secure_checkout", "loox", "icon", "star", "badge", "width=80", "width=250"]):
                if src not in candidate_img_urls:
                    candidate_img_urls.append(src)

    print(f"Total candidate images found: {len(candidate_img_urls)}")
    for i, u in enumerate(candidate_img_urls):
        print(f"  [{i+1}] {u}")

    # 3. Download Images & Check Sizes
    print("\n--- 3. DOWNLOADING IMAGES FOR VISION EXTRACTION ---")
    downloaded_images = []
    for u in candidate_img_urls[:8]:
        base_u = u.split("?")[0]
        dl_url = f"{base_u}?width=1200" if "cdn/shop" in u else u
        try:
            ir = requests.get(dl_url, headers=DEFAULT_HEADERS, timeout=10)
            if ir.status_code == 200 and len(ir.content) > 5000:
                mime = "image/jpeg" if any(ext in dl_url.lower() for ext in ["jpg", "jpeg"]) else "image/png"
                downloaded_images.append((ir.content, mime, dl_url, len(ir.content)))
                print(f"  ✅ Downloaded {len(ir.content):>7} bytes | {dl_url}")
            else:
                print(f"  ⚠️ Skipped small/empty: {ir.status_code} ({len(ir.content)} bytes) | {dl_url}")
        except Exception as e:
            print(f"  ❌ Download error on {dl_url}: {e}")

    # Sort by size descending and select top 4
    top_images = sorted(downloaded_images, key=lambda x: x[3], reverse=True)[:4]
    print(f"\nTop {len(top_images)} largest images selected for Vision:")
    for rank, (data, mime, u, size) in enumerate(top_images):
        print(f"  Rank {rank+1}: {size:>7} bytes ({mime}) -> {u}")

    # 4. Execute Hardened Vision Extraction
    print("\n--- 4. RUNNING HARDENED VISION EXTRACTION ---")
    vision_input = [(d[0], d[1]) for d in top_images]
    config = {"llm": {"provider": "gemini", "model": "gemini-3.5-flash"}}
    
    extracted = extract_specs_via_vision(
        vision_input, url, "Urbn", "Arc MagSafe 10000mAh", llm_config=config["llm"]
    )
    print(f"\nExtracted Specs via Hardened Vision: {extracted}")

if __name__ == "__main__":
    inspect_arc()
