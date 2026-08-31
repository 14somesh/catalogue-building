import re
from typing import Set, Dict, List
from bs4 import BeautifulSoup
from src.parsers.base import BaseParser, ParserResult


class RelianceParser(BaseParser):
    """
    Parser for Reliance Digital product listings.
    Extracts key specifications table and high-res product photos.
    """

    @property
    def name(self) -> str:
        return "reliance"

    @property
    def capabilities(self) -> Set[str]:
        return {"specs", "images"}

    def parse(self, url: str, html: str = "", status_code: int = 200, tier: int = 3) -> ParserResult:
        # Extract slug text from URL for structured spec extraction
        slug_text = url.split("/product/")[-1].split("?")[0].replace("-", " ") if "/product/" in url else ""
        
        soup = BeautifulSoup(html, "html.parser") if html else None
        title = ""
        spec_blocks = []
        json_ld_images = []
        json_ld_desc = ""

        if soup and status_code == 200:
            # 1. Try JSON-LD schema
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    import json
                    data = json.loads(script.get_text())
                    if isinstance(data, dict) and data.get("@type") == "Product":
                        title = data.get("name", "")
                        json_ld_desc = data.get("description", "")
                        img_field = data.get("image", [])
                        if isinstance(img_field, list):
                            json_ld_images.extend(img_field)
                        elif isinstance(img_field, str):
                            json_ld_images.append(img_field)
                except Exception:
                    pass

            if not title:
                title_el = soup.find("h1", class_=re.compile(r"pdp__title|product-title", re.I)) or soup.find("h1")
                title = title_el.get_text().strip() if title_el else ""
            
            for row in soup.find_all(["tr", "li", "div"], class_=re.compile(r"spec|details|pdp__feature", re.I)):
                spec_blocks.append(row.get_text(separator=" ", strip=True))

        if not title and slug_text:
            clean_slug = re.sub(r'\b(online|reliance|digital|buy|best price|prices|fast charging|power bank|powerbank)\b', '', slug_text, flags=re.I)
            title = " ".join(w.capitalize() for w in clean_slug.split()[:4]) + " Powerbank"

        full_text = f"{title}\n{slug_text}\n{json_ld_desc}\n" + "\n".join(spec_blocks)

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

        images = []
        for img_url in json_ld_images:
            if img_url and isinstance(img_url, str) and img_url not in images:
                images.append(img_url)

        if soup:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src and "reliancedigital" in src and not any(ic in src.lower() for ic in ["icon", "logo", "banner"]):
                    if src.startswith("//"):
                        src = f"https:{src}"
                    if src not in images:
                        images.append(src)

        field_sources = {
            "title": url if title else None,
            "subtitle": url if title else None,
            "capacity": url if specs.get("capacity") else None,
            "output": url if specs.get("output") else None,
            "ports": url if specs.get("ports") else None,
            "weight": url if specs.get("weight") else None,
            "warranty": url if specs.get("warranty") else None,
            "bullets": url if (specs.get("capacity") or specs.get("output")) else None
        }

        success = bool(specs.get("capacity") or specs.get("output"))
        return ParserResult(
            success=success,
            status_code=200 if success else status_code,
            url=url,
            title=title,
            description_text="\n".join(spec_blocks[:10]) or slug_text,
            specs=specs,
            image_urls=images,
            field_sources=field_sources,
            tier=tier,
            error=None if success else f"HTTP {status_code}"
        )
