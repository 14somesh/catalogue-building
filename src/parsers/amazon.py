import re
import json
from typing import Set, Dict, List
from bs4 import BeautifulSoup
from src.parsers.base import BaseParser, ParserResult


class AmazonParser(BaseParser):
    """
    Parser for Amazon India product listings.
    RESTRICTED TO IMAGE EXTRACTION ONLY.
    Never callable for specs per ARCHITECTURE.md.
    """

    @property
    def name(self) -> str:
        return "amazon"

    @property
    def capabilities(self) -> Set[str]:
        return {"images"}

    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 3) -> ParserResult:
        """
        Parses Amazon page HTML for master product images ONLY.
        Raises ValueError if invoked for specs.
        """
        if status_code in (401, 403, 429):
            return ParserResult(success=False, status_code=status_code, url=url, is_blocked=True, tier=tier, error=f"HTTP {status_code} Amazon Bot Block")
        if status_code in (404, 410):
            return ParserResult(success=False, status_code=status_code, url=url, is_delisted=True, tier=tier, error=f"HTTP {status_code} Delisted")

        soup = BeautifulSoup(html, "html.parser")
        
        # Check bot challenge / captcha
        if "captcha" in html.lower() or "api-services-support@amazon.com" in html.lower():
            return ParserResult(success=False, status_code=status_code, url=url, is_blocked=True, tier=tier, error="Amazon Captcha / Bot Challenge")

        image_urls = []
        
        # 1. Landing image data-old-hires or dynamic image dictionary
        img_el = soup.find("img", id="landingImage") or soup.find("img", attrs={"data-old-hires": True})
        if img_el:
            hires = img_el.get("data-old-hires")
            if hires:
                image_urls.append(hires)
            if img_el.get("data-a-dynamic-image"):
                try:
                    dyn = json.loads(img_el["data-a-dynamic-image"])
                    if dyn:
                        largest = max(dyn.keys(), key=lambda k: dyn[k][0] * dyn[k][1])
                        if largest not in image_urls:
                            image_urls.append(largest)
                except Exception:
                    pass

        # 2. Extract from colorImages JS block
        m = re.search(r'"hiRes"\s*:\s*"(https://[^"]+)"', html)
        if m and m.group(1) not in image_urls:
            image_urls.append(m.group(1))

        # Upgrade Amazon image URLs to 1500px master
        upgraded_images = []
        for u in image_urls:
            upgraded = re.sub(r'\._[A-Z0-9_,]+_\.', '._SL1500_.', u)
            if upgraded not in upgraded_images:
                upgraded_images.append(upgraded)

        return ParserResult(
            success=len(upgraded_images) > 0,
            status_code=status_code,
            url=url,
            specs={},  # Amazon parser NEVER provides specs
            image_urls=upgraded_images,
            field_sources={},
            tier=tier
        )

    def extract_specs(self, *args, **kwargs):
        """Strict safety guard: Amazon is prohibited from supplying specifications."""
        raise ValueError("[SECURITY / INVARIANT VIOLATION] AmazonParser is restricted to IMAGE EXTRACTION ONLY and cannot be called for specs.")
