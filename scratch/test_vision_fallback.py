import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import Optional, Dict

load_dotenv()

class VisionExtractedSpecsSchema(BaseModel):
    capacity: Optional[str] = Field(default=None, description="Battery capacity e.g. '10000 mAh' or '20000 mAh'")
    output: Optional[str] = Field(default=None, description="Max power output e.g. '22.5W Fast Charging' or '15W Wireless'")
    ports: Optional[str] = Field(default=None, description="Input/output port configuration e.g. 'Type-C, USB-A'")
    weight: Optional[str] = Field(default=None, description="Weight of the product e.g. '195g' or '220g'")
    warranty: Optional[str] = Field(default=None, description="Warranty term e.g. '6 Months' or '1 Year'")

def test_vision_extraction():
    url = "https://www.stuffcool.com/products/odin-10-000mah-qi2-magsafe-powerbank-with-built-in-type-c-cable"
    print(f"Fetching page: {url}")
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    
    # Collect candidate image URLs from product page
    candidate_img_urls = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and "cdn/shop" in src:
            if src.startswith("//"):
                src = f"https:{src}"
            if not any(ign in src.lower() for ign in ["preview_images", "icon", "logo", "badge", "payment", "flag"]):
                if src not in candidate_img_urls:
                    candidate_img_urls.append(src)
    
    print(f"Found {len(candidate_img_urls)} candidate images. Downloading top 3...")
    image_parts = []
    for img_url in candidate_img_urls[:3]:
        try:
            ir = requests.get(img_url, headers=headers, timeout=10)
            if ir.status_code == 200 and len(ir.content) > 10000:
                mime = "image/jpeg" if "jpg" in img_url or "jpeg" in img_url else "image/png"
                image_parts.append(types.Part.from_bytes(data=ir.content, mime_type=mime))
                print(f"  Downloaded: {img_url[:80]}... ({len(ir.content)} bytes)")
        except Exception as e:
            print(f"  Download failed for {img_url}: {e}")
            
    if not image_parts:
        print("No images downloaded.")
        return

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = (
        "You are an expert technical product specification extractor.\n"
        "Examine these product infographic and spec-sheet images for 'Stuffcool Odin'.\n"
        "Extract the exact technical specifications into the JSON schema:\n"
        "- capacity: battery capacity in mAh (e.g. '10000 mAh')\n"
        "- output: maximum output power / fast charging wattage (e.g. '22.5W Fast Charging', '15W Wireless')\n"
        "- ports: port types (e.g. 'Type-C, USB-A', 'Type-C, Lightning')\n"
        "- weight: product weight (e.g. '195g')\n"
        "- warranty: warranty duration (e.g. '6 Months', '1 Year')\n"
        "STRICT: Only extract what is clearly visible. If not visible, return null."
    )
    
    contents = [prompt] + image_parts
    print("\nCalling Gemini Vision API with gemini-3.6-flash...")
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VisionExtractedSpecsSchema,
            temperature=0.1
        )
    )
    print("\nVision Extracted Result:")
    print(response.text)

if __name__ == "__main__":
    test_vision_extraction()
