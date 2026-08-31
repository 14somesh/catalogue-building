import re
from typing import Set, Dict, List
from bs4 import BeautifulSoup
from src.parsers.base import BaseParser, ParserResult


class TataCliqParser(BaseParser):
    """
    Parser for Tata CLiQ retail product listings.
    """

    @property
    def name(self) -> str:
        return "tatacliq"

    @property
    def capabilities(self) -> Set[str]:
        return {"specs", "images"}

    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 3) -> ParserResult:
        if status_code in (401, 403, 429):
            return ParserResult(success=False, status_code=status_code, url=url, is_blocked=True, tier=tier, error=f"HTTP {status_code} Blocked")
        if status_code in (404, 410):
            return ParserResult(success=False, status_code=status_code, url=url, is_delisted=True, tier=tier, error=f"HTTP {status_code} Delisted")
        if status_code != 200:
            return ParserResult(success=False, status_code=status_code, url=url, tier=tier, error=f"HTTP {status_code}")

        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.find("h1") or soup.find("div", class_=re.compile(r"ProductDescriptionPage__title", re.I))
        title = title_el.get_text().strip() if title_el else ""

        spec_blocks = []
        for row in soup.find_all(["tr", "li", "div"], class_=re.compile(r"spec|feature|description", re.I)):
            spec_blocks.append(row.get_text(separator=" ", strip=True))

        full_text = f"{title}\n" + "\n".join(spec_blocks)

        specs = {}
        cap = re.search(r'\b(5000|10000|15000|20000|25000|30000)\s*(?:mAh|mah)\b', full_text, re.I)
        if cap:
            specs["capacity"] = f"{cap.group(1)} mAh"
        watt = re.search(r'\b(\d+(?:\.\d+)?\s*W(?:att)?)\b', full_text, re.I)
        if watt:
            specs["output"] = f"{watt.group(1)} Fast Charging"
        ports = []
        if re.search(r'type-?c|usb-?c', full_text, re.I):
            ports.append("Type-C")
        if re.search(r'usb-?a', full_text, re.I):
            ports.append("USB-A")
        if ports:
            specs["ports"] = ", ".join(ports)
        wt = re.search(r'\b(\d{2,3}(?:\.\d+)?)\s*(?:g|grams|gm)\b', full_text, re.I)
        if wt:
            specs["weight"] = f"{wt.group(1)}g"
        warr = re.search(r'\b(\d+)\s*(?:month|year)s?\s*(?:warranty)\b', full_text, re.I)
        if warr:
            specs["warranty"] = warr.group(0).title()

        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "tatacliq" in src and not any(ic in src.lower() for ic in ["icon", "logo", "banner"]):
                if src.startswith("//"):
                    src = f"https:{src}"
                if src not in images:
                    images.append(src)

        field_sources = {
            "title": url,
            "subtitle": url,
            "capacity": url if specs.get("capacity") else None,
            "output": url if specs.get("output") else None,
            "ports": url if specs.get("ports") else None,
            "weight": url if specs.get("weight") else None,
            "warranty": url if specs.get("warranty") else None,
            "bullets": url
        }

        return ParserResult(
            success=bool(specs.get("capacity") or specs.get("output")),
            status_code=200,
            url=url,
            title=title,
            description_text="\n".join(spec_blocks[:10]),
            specs=specs,
            image_urls=images,
            field_sources=field_sources,
            tier=tier
        )
