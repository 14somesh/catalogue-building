import os
import sys
from duckduckgo_search import DDGS

print("=== Testing duckduckgo_search (DDGS) for Retail Queries ===")

queries = [
    "Stuffcool Major 10000 mAh croma",
    "Stuffcool Giga 20000 mAh croma",
    "Stuffcool Lucid 5000 mAh croma",
    "Stuffcool 1# 10000 mAh powerbank",
    "Stuffcool Major 10000 mAh reliancedigital",
    "Stuffcool Major 10000 mAh tatacliq",
    "Stuffcool Major 10000 mAh flipkart"
]

ddgs = DDGS()
for q in queries:
    print(f"\n--- Query: '{q}' ---")
    try:
        results = list(ddgs.text(q, max_results=5, region="in-en"))
        print(f"Results returned: {len(results)}")
        for r in results:
            print(f"  • Title: {r.get('title')}\n    URL: {r.get('href')}\n    Body: {r.get('body')[:100]}...")
    except Exception as e:
        print(f"Error on '{q}': {e}")
