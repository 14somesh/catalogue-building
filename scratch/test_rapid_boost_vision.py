import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import Optional

load_dotenv()

class VisionExtractedSpecsSchema(BaseModel):
    capacity: Optional[str] = Field(default=None, description="Battery capacity e.g. '10000 mAh' or '20000 mAh'")
    output: Optional[str] = Field(default=None, description="Max power output e.g. '65W Fast Charging' or '22.5W Fast Charging'")
    ports: Optional[str] = Field(default=None, description="Input/output port configuration e.g. 'Type-C, USB-A'")
    weight: Optional[str] = Field(default=None, description="Weight of the product e.g. '195g' or '350g'")
    warranty: Optional[str] = Field(default=None, description="Warranty term e.g. '6 Months' or '1 Year'")

url = "https://www.pebblecart.com/products/rapid-boost-65"
headers = {"User-Agent": "Mozilla/5.0"}
r = requests.get(url, headers=headers)
soup = BeautifulSoup(r.text, "html.parser")

candidate_urls = []
for img in soup.find_all("img"):
    src = img.get("src") or img.get("data-src")
    if src:
        if src.startswith("//"):
            src = f"https:{src}"
        elif not src.startswith("http"):
            src = urljoin(url, src)
        
        # Filter out icons, badges, small gifs
        src_lower = src.lower()
        if any(ign in src_lower for ign in ["logo", "wa-logo", "free_shipping", "warranty.gif", "secure_checkout", "loox", "icon", "star", "badge", "width=250", "width=80", "height="]):
            continue
        if src not in candidate_urls:
            candidate_urls.append(src)

print(f"Candidate images found ({len(candidate_urls)}):")
for u in candidate_urls:
    print(f"  - {u}")

# Let's download the candidate images
image_parts = []
for u in candidate_urls:
    try:
        # Request with high width or remove width constraint
        base_u = u.split("?")[0]
        dl_url = f"{base_u}?width=1200"
        ir = requests.get(dl_url, headers=headers, timeout=10)
        if ir.status_code == 200 and len(ir.content) > 10000:
            mime = "image/jpeg" if any(x in dl_url.lower() for x in ["jpg", "jpeg"]) else "image/png"
            image_parts.append((types.Part.from_bytes(data=ir.content, mime_type=mime), dl_url, len(ir.content)))
            print(f"Downloaded: {dl_url} ({len(ir.content)} bytes)")
    except Exception as e:
        print(f"Download failed: {e}")

print(f"\nTotal downloaded for Vision: {len(image_parts)}")

# Send to Gemini Vision (up to 8 images)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
prompt = (
    "You are an expert technical product specification extractor.\n"
    "Examine these product infographic, gallery, and spec-sheet images for 'Pebble Rapid Boost 65'.\n"
    "Extract the exact technical specifications into the JSON schema:\n"
    "- capacity: battery capacity in mAh (e.g. '10000 mAh', '20000 mAh')\n"
    "- output: maximum output power / fast charging wattage (e.g. '65W Fast Charging', '65W Turbo')\n"
    "- ports: port types and configuration (e.g. 'Type-C, USB-A', 'Type-C Input/Output', 'Built-in Type-C')\n"
    "- weight: product weight in grams (e.g. '350g')\n"
    "- warranty: warranty duration (e.g. '6 Months', '1 Year')\n\n"
    "STRICT CONSTRAINTS:\n"
    "1. ZERO HALLUCINATION: Only extract specs that are clearly visible or stated in the images.\n"
    "2. If a spec is not visible or not mentioned, return null for that field.\n"
    "3. Return valid JSON adhering to VisionExtractedSpecsSchema."
)

parts = [prompt] + [p[0] for p in image_parts[:10]]
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents=parts,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=VisionExtractedSpecsSchema,
        temperature=0.1
    )
)
print("\n--- VISION EXTRACTION RESULT ---")
print(response.text)
