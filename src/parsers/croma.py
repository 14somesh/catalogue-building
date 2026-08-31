import re
from typing import Set, Dict, List
from bs4 import BeautifulSoup
from src.parsers.base import BaseParser, ParserResult


class CromaParser(BaseParser):
    """
    Parser for Croma retail product listings.
    Extracts structured key specs table and high-res product assets.
    """

    @property
    def name(self) -> str:
        return "croma"

    @property
    def capabilities(self) -> Set[str]:
        return {"specs", "images"}

    def parse(self, url: str, html: str = "", status_code: int = 200, tier: int = 3) -> ParserResult:
        # Extract slug text from URL for structured spec extraction
        slug_text = url.split("/p/")[0].split("/")[-1].replace("-", " ") if "/p/" in url else ""
        
        soup = BeautifulSoup(html, "html.parser") if html else None
        
        # Product title
        title = ""
        spec_text_blocks = []
        if soup and status_code == 200 and "Access Denied" not in html:
            title_el = soup.find("h1", class_=re.compile(r"pd-title|product-title", re.I)) or soup.find("h1")
            title = title_el.get_text().strip() if title_el else ""
            for row in soup.find_all(["tr", "li", "div"], class_=re.compile(r"spec|feature", re.I)):
                spec_text_blocks.append(row.get_text(separator=" ", strip=True))

        if not title and slug_text:
            # Reconstruct title from slug
            clean_slug = re.sub(r'\b(online|croma|buy|best price|prices|fast charging|power bank|powerbank)\b', '', slug_text, flags=re.I)
            title = " ".join(w.capitalize() for w in clean_slug.split()[:4]) + " Powerbank"

        full_text = f"{title}\n{slug_text}\n" + "\n".join(spec_text_blocks)
        
        # Specs regex extraction
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

        # Extract MRP
        mrp = None
        if soup:
            mrp_el = soup.find(class_=re.compile(r"pdp__mrp|mrpPrice|old-price|was-price", re.I))
            if mrp_el:
                txt = mrp_el.get_text()
                nums = re.findall(r'([0-9,]+(?:\.\d+)?)', txt)
                if nums:
                    val = float(nums[0].replace(",", ""))
                    if val > 100:
                        mrp = val
        if not mrp and html:
            m = re.search(r'MRP[:\s]+(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)', html, re.I)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val > 100:
                    mrp = val

        # Image extraction
        images = []
        if soup:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src and "croma" in src and not any(ic in src.lower() for ic in ["icon", "logo", "banner"]):
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
            "bullets": url if (specs.get("capacity") or specs.get("output")) else None,
            "mrp": url if mrp else None
        }

        success = bool(specs.get("capacity") or specs.get("output"))
        return ParserResult(
            success=success,
            status_code=200 if success else status_code,
            url=url,
            title=title,
            description_text="\n".join(spec_text_blocks[:10]) or slug_text,
            specs=specs,
            mrp=mrp,
            image_urls=images,
            field_sources=field_sources,
            tier=tier,
            error=None if success else f"HTTP {status_code}"
        )
