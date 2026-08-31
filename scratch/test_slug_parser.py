import os
import sys
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def parse_croma_url_and_content(url: str, html: str = "") -> dict:
    slug = url.split("/p/")[0].split("/")[-1].replace("-", " ")
    full_text = f"{slug}\n{html}"
    
    specs = {}
    cap = re.search(r'\b(5000|10000|15000|20000|25000|30000)\s*(?:mAh|mah)\b', full_text, re.I)
    if cap:
        specs["capacity"] = f"{cap.group(1)} mAh"
        
    watt = re.search(r'\b(\d+(?:[.\s]\d+)?)\s*w(?:att)?\b', full_text, re.I)
    if watt:
        w_val = watt.group(1).replace(" ", ".")
        specs["output"] = f"{w_val}W Fast Charging"
        
    ports = []
    if re.search(r'type-?c|usb-?c', full_text, re.I):
        ports.append("Type-C")
    if re.search(r'type-?a|usb-?a', full_text, re.I):
        ports.append("USB-A")
    if re.search(r'micro-?usb', full_text, re.I):
        ports.append("Micro-USB")
    if re.search(r'wireless|magsafe|qi2?', full_text, re.I):
        ports.append("Magnetic Wireless")
    if ports:
        specs["ports"] = ", ".join(ports)
        
    wt = re.search(r'\b(\d{2,3}(?:\.\d+)?)\s*(?:g|grams|gm)\b', full_text, re.I)
    if wt:
        specs["weight"] = f"{wt.group(1)}g"
        
    warr = re.search(r'\b(\d+)\s*(?:month|year)s?\s*(?:warranty)\b', full_text, re.I)
    if warr:
        specs["warranty"] = warr.group(0).title()
        
    return {
        "slug": slug,
        "specs": specs,
        "success": bool(specs.get("capacity") and specs.get("output"))
    }

urls = [
    "https://www.croma.com/stuffcool-major-10000-mah-22-5w-fast-charging-power-bank-2-type-a-and-1-type-c-and-micro-usb-ports-led-indicator-black-/p/303296",
    "https://www.croma.com/stuffcool-palm-smallest-10000-mah-22-5w-fast-charging-power-bank-1-type-a-and-1-type-c-ports-led-indicator-yellow-/p/303300",
    "https://www.croma.com/stuffcool-odin-10000-mah-35w-fast-charging-power-bank-2-type-c-qi2-magsafe-digital-display-silver-/p/317818"
]

print("=== Testing Croma Structured Parser ===")
for u in urls:
    res = parse_croma_url_and_content(u)
    print(f"\nURL: {u}")
    print(f"  Success: {res['success']}")
    print(f"  Specs: {res['specs']}")
