import re
from typing import Set, Dict, List
from bs4 import BeautifulSoup
from src.parsers.base import BaseParser, ParserResult


class ShopifyParser(BaseParser):
    """
    Parser for Shopify-based brand storefronts (e.g. Stuffcool).
    Extracts product title, clean description, collapsed accordion specs, and high-res images.
    """

    @property
    def name(self) -> str:
        return "shopify"

    @property
    def capabilities(self) -> Set[str]:
        return {"specs", "images"}

    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 1) -> ParserResult:
        if status_code in (401, 403, 429):
            return ParserResult(success=False, status_code=status_code, url=url, is_blocked=True, tier=tier, error=f"HTTP {status_code} Blocked")
        if status_code in (404, 410):
            return ParserResult(success=False, status_code=status_code, url=url, is_delisted=True, tier=tier, error=f"HTTP {status_code} Delisted")
        if status_code != 200:
            return ParserResult(success=False, status_code=status_code, url=url, tier=tier, error=f"HTTP {status_code}")

        soup = BeautifulSoup(html, "html.parser")

        # Soft 404 / Homepage Fallback check
        title_el = soup.find("title")
        page_title = title_el.get_text().strip() if title_el else ""
        if any(w in page_title.lower() for w in ["404", "not found", "page not found"]):
            return ParserResult(success=False, status_code=404, url=url, is_delisted=True, tier=tier, error="Soft 404 page title")

        # Strip noisy elements (header, footer, nav, scripts, reviews, recommendations)
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
            tag.decompose()
        for noisy_cls in ["site-footer", "footer", "site-header", "header", "announcement-bar", "related-products", "product-recommendations", "shopify-section-footer"]:
            for el in soup.find_all(class_=re.compile(noisy_cls, re.I)):
                el.decompose()

        # Product Title
        product_title = None
        h1 = soup.find("h1", class_=re.compile(r"product.*title|title", re.I)) or soup.find("h1")
        if h1:
            product_title = h1.get_text().strip()

        # Extract Accordion & Tab Specs (Details/Summary, .accordion, .specs, tables)
        spec_text_blocks = []
        for acc in soup.find_all(["details", "table", "div", "section"], class_=re.compile(r"accordion|spec|tab|tech|detail", re.I)):
            spec_text_blocks.append(acc.get_text(separator=" ", strip=True))

        # Main product description container
        desc_container = soup.find("div", class_=re.compile(r"product.*description|description|rte", re.I))
        desc_text = desc_container.get_text(separator="\n", strip=True) if desc_container else ""

        full_product_text = f"{product_title or ''}\n{desc_text}\n" + "\n".join(spec_text_blocks)

        # Parse structured specs
        specs = self._extract_specs(full_product_text)

        # Extract MRP / List Price
        mrp = None
        # 1. First priority: explicit HTML regular price / MRP text in price-list or product containers
        for s_tag in soup.find_all(["s", "del", "span", "div", "price-list"], class_=re.compile(r"price-item--regular|compare|mrp|regular-price|price-list", re.I)):
            txt = s_tag.get_text()
            m_mrp = re.search(r'MRP[:\s]+(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)', txt, re.I) or re.search(r'Regular price\s+(?:MRP[:\s]+)?(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)', txt, re.I)
            if m_mrp:
                val = float(m_mrp.group(1).replace(",", ""))
                if val > 10000:
                    val = val / 100.0
                if val > 100:
                    mrp = val
                    break

        # 2. Second priority: regex on html
        if not mrp:
            m = re.search(r'MRP[:\s]+(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)', html, re.I)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val > 10000:
                    val = val / 100.0
                if val > 100:
                    mrp = val

        # 3. Third priority: compare_at_price in script JSON (handle paise / cents)
        if not mrp:
            m = re.search(r'compare[_\s-]?at[_\s-]?price["\':\s]+([0-9,]+(?:\.\d+)?)', html, re.I)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val > 10000:
                    val = val / 100.0
                if val > 100:
                    mrp = val

        # Has specs verification: require at least capacity or wattage/output
        has_specs = bool(specs.get("capacity") or specs.get("output"))
        if not has_specs and not desc_text:
            return ParserResult(success=False, status_code=200, url=url, is_delisted=True, tier=tier, error="Page lacks technical specs")

        # Extract High-Res Product Images (Prioritize og:image, ignore video posters & thumbnails)
        image_urls = []
        
        # 1. Primary Master Image from og:image or twitter:image
        for meta_prop in ["og:image", "twitter:image"]:
            meta_tag = soup.find("meta", property=meta_prop) or soup.find("meta", attrs={"name": meta_prop})
            if meta_tag and meta_tag.get("content"):
                src = meta_tag["content"].strip()
                if ("cdn/shop" in src or "cdn.shopify" in src) and not any(ign in src.lower() for ign in ["preview_images", "thumbnail", "video", "poster", "icon", "logo", "badge"]):
                    if src.startswith("//"):
                        src = f"https:{src}"
                    elif src.startswith("http://"):
                        src = "https://" + src[7:]
                    src = re.sub(r"width=\d+", "width=2048", src) if "width=" in src else f"{src}&width=2048" if "?" in src else f"{src}?width=2048"
                    if src not in image_urls:
                        image_urls.append(src)

        # 2. Gallery product photos
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-master")
            if src and ("cdn/shop" in src or "cdn.shopify" in src):
                # Strict exclusion of video poster thumbnails and UI elements
                if any(ign in src.lower() for ign in ["preview_images", "thumbnail", "video", "poster", "icon", "logo", "badge", "payment", "flag", "star", "review"]):
                    continue

                if src.startswith("//"):
                    src = f"https:{src}"
                elif src.startswith("http://"):
                    src = "https://" + src[7:]

                if "width=" in src:
                    src = re.sub(r"width=\d+", "width=2048", src)
                else:
                    sep = "&" if "?" in src else "?"
                    src = f"{src}{sep}width=2048"

                if src not in image_urls:
                    image_urls.append(src)

        field_sources = {
            "title": url,
            "subtitle": url,
            "capacity": url if specs.get("capacity") else None,
            "output": url if specs.get("output") else None,
            "ports": url if specs.get("ports") else None,
            "weight": url if specs.get("weight") else None,
            "warranty": url if specs.get("warranty") else None,
            "bullets": url,
            "mrp": url if mrp else None
        }

        return ParserResult(
            success=True,
            status_code=200,
            url=url,
            title=product_title,
            description_text=desc_text[:2000],
            specs=specs,
            mrp=mrp,
            image_urls=image_urls,
            field_sources=field_sources,
            tier=tier
        )

    def _extract_specs(self, text: str) -> Dict[str, str]:
        specs = {}
        # Capacity
        cap = re.search(r'\b(5000|10000|10,000|15000|20000|20,000|25000|27000|30000)\s*(?:mAh|mah)\b', text, re.I)
        if cap:
            specs["capacity"] = f"{cap.group(1).replace(',', '')} mAh"

        # Output / Wattage
        watt = re.search(r'\b(\d+(?:\.\d+)?\s*W(?:att)?)\b', text, re.I)
        if watt:
            specs["output"] = f"{watt.group(1)} Fast Charging"

        # Ports
        ports = []
        if re.search(r'type-?c|usb-?c', text, re.I):
            ports.append("Type-C")
        if re.search(r'usb-?a|qc\s*3\.0', text, re.I):
            ports.append("USB-A")
        if re.search(r'wireless|magsafe|qi2?', text, re.I):
            ports.append("Magnetic Wireless")
        if ports:
            specs["ports"] = ", ".join(ports)

        # Weight
        wt = re.search(r'\b(\d{2,3}(?:\.\d+)?)\s*(?:g|grams|gm)\b', text, re.I)
        if wt:
            specs["weight"] = f"{wt.group(1)}g"

        # Warranty
        warr = re.search(r'\b(\d+)\s*(?:month|year)s?\s*(?:manufacturer\s*)?warranty\b', text, re.I)
        if warr:
            specs["warranty"] = warr.group(0).title()

        return specs
