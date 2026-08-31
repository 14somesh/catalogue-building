import os
import sys
from ddgs import DDGS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import reject_qualifier_mismatch

test_products = [
    ("Stuffcool", "Major 10000 mAH"),
    ("Stuffcool", "Giga 20000 mAH"),
    ("Stuffcool", "Lucid"),
    ("Stuffcool", "1#"),
    ("Stuffcool", "Click 20"),
    ("Stuffcool", "Roam Plus")
]

qualifier_tokens = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

print("=== Testing DDGS across missing products ===")
ddgs = DDGS()

for brand, model in test_products:
    query = f"{brand} {model} powerbank (croma OR reliance digital OR flipkart OR tatacliq)"
    print(f"\n==================== Product: '{brand} {model}' ====================")
    try:
        results = list(ddgs.text(query, max_results=8))
        print(f"Total results: {len(results)}")
        for r in results:
            title = r.get("title", "")
            url = r.get("href", "")
            body = r.get("body", "")
            is_valid, reason = reject_qualifier_mismatch(model, title, qualifier_tokens, brand=brand)
            valid_tag = "VALID" if is_valid else f"REJECTED ({reason})"
            print(f"  • [{valid_tag}] {title}")
            print(f"    URL: {url}")
            print(f"    Body: {body[:100]}...")
    except Exception as e:
        print(f"Error on {model}: {e}")
