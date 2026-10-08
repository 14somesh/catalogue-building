import re
import json
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from src.parsers.base import BaseParser, ParserResult
from src.parsers.brochure import parse_specs_from_text
from src.utils.category_specs import extract_category_specs, normalize_category_key

logger = logging.getLogger("generic_parser")


def clean_html_text(html: str) -> str:
    """Strips noisy tags and returns clean text from HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def detect_ecommerce_platform(domain: str, timeout: int = 6) -> Tuple[str, str]:
    """
    Lightweight live probe of a brand domain's storefront platform, used by the onboarding summary.
    Returns (platform, detail) where platform is one of shopify/woocommerce/magento/bigcommerce/custom.
    Never raises: network failures are reported as ("custom", "error: ...").
    """
    import requests
    clean = str(domain or "").strip().replace("https://", "").replace("http://", "").strip("/")
    if not clean:
        return "custom", "error: empty domain"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        r = requests.get(f"https://{clean}/products.json?limit=1", headers=headers, timeout=timeout)
        if r.status_code == 200 and "products" in (r.text or "")[:200]:
            return "shopify", "products.json reachable"
        r = requests.get(f"https://{clean}/", headers=headers, timeout=timeout)
        if r.status_code in (401, 403, 429):
            return "custom", f"HTTP {r.status_code} — request blocked"
        body = (r.text or "").lower()
        if "cdn.shopify.com" in body or "shopify.theme" in body:
            return "shopify", f"HTTP {r.status_code}, Shopify markers in homepage"
        if "woocommerce" in body or "wp-content" in body:
            return "woocommerce", f"HTTP {r.status_code}, WooCommerce markers in homepage"
        if "mage/" in body or "magento" in body:
            return "magento", f"HTTP {r.status_code}, Magento markers in homepage"
        if "bigcommerce" in body:
            return "bigcommerce", f"HTTP {r.status_code}, BigCommerce markers in homepage"
        return "custom", f"HTTP {r.status_code}, no known platform markers"
    except Exception as e:
        return "custom", f"error: {type(e).__name__}"


class GenericParser(BaseParser):
    """
    Generic brand parser supporting WooCommerce, Magento, BigCommerce, and custom storefronts.
    Extracts product title, specifications, and maximum-resolution product images
    using JSON-LD schemas, microdata, structured spec tables, and semantic HTML fallbacks.
    """

    @property
    def name(self) -> str:
        return "generic"

    @property
    def capabilities(self) -> Set[str]:
        return {"specs", "images"}

    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 1, category: Optional[str] = None) -> ParserResult:
        if status_code in (401, 403, 429):
            return ParserResult(success=False, status_code=status_code, url=url, is_blocked=True, tier=tier, error=f"HTTP {status_code} Blocked")
        if status_code in (404, 410):
            return ParserResult(success=False, status_code=status_code, url=url, is_delisted=True, tier=tier, error=f"HTTP {status_code} Delisted")
        if status_code != 200:
            return ParserResult(success=False, status_code=status_code, url=url, tier=tier, error=f"HTTP {status_code}")

        soup = BeautifulSoup(html, "html.parser")

        # Soft 404 check
        title_el = soup.find("title")
        page_title = title_el.get_text().strip() if title_el else ""
        if any(w in page_title.lower() for w in ["404", "not found", "page not found", "error 404"]):
            return ParserResult(success=False, status_code=404, url=url, is_delisted=True, tier=tier, error="Soft 404 page title")

        # 1. Product Title
        product_title = None
        h1 = (
            soup.find("h1", class_=re.compile(r"product.*title|title|entry-title", re.I)) or
            soup.find("h1")
        )
        if h1:
            product_title = h1.get_text().strip()
        elif soup.find("meta", property="og:title"):
            product_title = soup.find("meta", property="og:title").get("content", "").strip()

        # 2. Extract Specifications & Images from JSON-LD
        specs: Dict[str, str] = {}
        mrp: Optional[float] = None
        candidate_images: List[str] = []
        json_ld_desc = ""

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                items = data if isinstance(data, list) else [data]
                if isinstance(data, dict) and "@graph" in data:
                    items.extend(data["@graph"])

                for item in items:
                    if isinstance(item, dict) and item.get("@type") in ("Product", "IndividualProduct"):
                        if not product_title and item.get("name"):
                            product_title = str(item["name"]).strip()

                        # Description
                        json_ld_desc = item.get("description") or ""

                        # Structured attributes
                        for prop in item.get("additionalProperty", []):
                            if isinstance(prop, dict):
                                n = str(prop.get("name", "")).lower()
                                v = str(prop.get("value", "")).strip()
                                if "capacity" in n and "capacity" not in specs:
                                    specs["capacity"] = v
                                elif "output" in n and "output" not in specs:
                                    specs["output"] = v
                                elif "port" in n and "ports" not in specs:
                                    specs["ports"] = v
                                elif "weight" in n and "weight" not in specs:
                                    specs["weight"] = v
                                elif "warranty" in n and "warranty" not in specs:
                                    specs["warranty"] = v

                        # Price / MRP
                        offers = item.get("offers")
                        if isinstance(offers, dict):
                            p_val = offers.get("price") or offers.get("highPrice")
                            if p_val:
                                try:
                                    mrp = float(str(p_val).replace(",", ""))
                                except ValueError:
                                    pass
                        elif isinstance(offers, list) and len(offers) > 0:
                            p_val = offers[0].get("price")
                            if p_val:
                                try:
                                    mrp = float(str(p_val).replace(",", ""))
                                except ValueError:
                                    pass

                        # Images
                        img_field = item.get("image")
                        if isinstance(img_field, list):
                            for u in img_field:
                                if isinstance(u, str) and u.startswith("http"):
                                    candidate_images.append(u)
                                elif isinstance(u, dict) and u.get("url"):
                                    candidate_images.append(u["url"])
                        elif isinstance(img_field, str) and img_field.startswith("http"):
                            candidate_images.append(img_field)
                        elif isinstance(img_field, dict) and img_field.get("url"):
                            candidate_images.append(img_field["url"])
            except Exception:
                continue

        # Strip noisy elements before HTML text extraction
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
            tag.decompose()
        for noisy_cls in ["site-footer", "footer", "site-header", "header", "related-products", "product-recommendations"]:
            for el in soup.find_all(class_=re.compile(noisy_cls, re.I)):
                el.decompose()

        # 3. HTML Description & Spec Table Extraction
        spec_text_blocks = []
        for table_el in soup.find_all(["table", "dl", "div", "section"], class_=re.compile(r"woocommerce-product-attributes|product-attributes|specification|tech-spec|spec|attribute|detail", re.I)):
            spec_text_blocks.append(clean_html_text(str(table_el)))

        desc_container = soup.find(["div", "section"], class_=re.compile(r"woocommerce-product-details__short-description|product-description|description|entry-content", re.I))
        desc_text = clean_html_text(str(desc_container)) if desc_container else ""

        full_text = f"{product_title or ''}\n{desc_text}\n{json_ld_desc}\n" + "\n".join(spec_text_blocks)
        if normalize_category_key(category) == "powerbank":
            parsed_specs = parse_specs_from_text(full_text)
        else:
            parsed_specs = extract_category_specs(full_text, category=category)
        for k, v in parsed_specs.items():
            if k not in specs or not specs[k]:
                specs[k] = v

        # Fallback MRP from HTML price tags if not found in JSON-LD
        if not mrp:
            price_el = soup.find(["span", "div", "p"], class_=re.compile(r"woocommerce-Price-amount|regular-price|mrp|price", re.I))
            if price_el:
                price_match = re.search(r'[\d,]+(?:\.\d{2})?', price_el.get_text())
                if price_match:
                    try:
                        mrp = float(price_match.group(0).replace(",", ""))
                    except ValueError:
                        pass

        # 4. High-Resolution HTML Image Extraction
        for img in soup.find_all("img"):
            high_res_url = (
                img.get("data-zoom-image") or
                img.get("data-large-img") or
                img.get("data-high-res") or
                img.get("data-master") or
                img.get("data-src") or
                img.get("src")
            )
            if high_res_url:
                full_u = urljoin(url, high_res_url)
                if (
                    full_u not in candidate_images
                    and not any(ign in full_u.lower() for ign in ["logo", "icon", "badge", "payment", "arrow", "rating", "avatar", "sprite"])
                    and full_u.startswith("http")
                ):
                    candidate_images.append(full_u)

            # Parse srcset for largest resolution image
            srcset = img.get("srcset")
            if srcset:
                parts = [p.strip().split() for p in srcset.split(",") if p.strip()]
                if parts:
                    def get_width(item):
                        if len(item) > 1 and item[1].endswith("w"):
                            try:
                                return int(item[1][:-1])
                            except ValueError:
                                return 0
                        return 0
                    sorted_srcs = sorted(parts, key=get_width, reverse=True)
                    best_src = sorted_srcs[0][0]
                    full_u = urljoin(url, best_src)
                    if (
                        full_u not in candidate_images
                        and not any(ign in full_u.lower() for ign in ["logo", "icon", "badge"])
                        and full_u.startswith("http")
                    ):
                        candidate_images.append(full_u)

        # OpenGraph image fallback
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            full_u = urljoin(url, og_img["content"])
            if full_u not in candidate_images and full_u.startswith("http"):
                candidate_images.append(full_u)

        field_sources = {k: url for k in specs}
        field_tiers = {k: tier for k in specs}

        return ParserResult(
            success=len(specs) >= 2 or len(candidate_images) > 0,
            status_code=200,
            url=url,
            title=product_title,
            description_text=desc_text,
            specs=specs,
            mrp=mrp,
            image_urls=candidate_images,
            field_sources=field_sources,
            field_tiers=field_tiers,
            tier=tier
        )


import xml.etree.ElementTree as ET
import requests
from typing import Tuple


class GenericBrandParser:
    """
    Brand-level search helper for generic storefronts (WooCommerce, Magento, custom).
    Discovers product URLs from XML Sitemaps.
    """

    def __init__(self, brand_domain: str, brand_name: str, config: Optional[dict] = None):
        self.brand_domain = brand_domain.replace("https://", "").replace("http://", "").strip("/")
        self.brand_name = brand_name
        self.config = config or {}
        self.sitemap_urls_cache: Optional[List[str]] = None

    def _discover_sitemap_urls(self) -> List[str]:
        """Discovers product URLs from standard XML sitemaps."""
        if self.sitemap_urls_cache is not None:
            return self.sitemap_urls_cache

        sitemap_candidates = [
            f"https://{self.brand_domain}/sitemap_products_1.xml",
            f"https://{self.brand_domain}/product-sitemap.xml",
            f"https://{self.brand_domain}/sitemap-products.xml",
            f"https://{self.brand_domain}/sitemap.xml",
        ]

        discovered = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        }
        for sm_url in sitemap_candidates:
            try:
                r = requests.get(sm_url, headers=headers, timeout=8)
                if r.status_code == 200 and ("<urlset" in r.text or "<sitemapindex" in r.text):
                    root = ET.fromstring(r.content)
                    # Check for sub-sitemaps
                    for sitemap_tag in root.findall("{*}sitemap"):
                        loc = sitemap_tag.find("{*}loc")
                        if loc is not None and loc.text and "product" in loc.text.lower():
                            sub_r = requests.get(loc.text, headers=headers, timeout=8)
                            if sub_r.status_code == 200:
                                sub_root = ET.fromstring(sub_r.content)
                                for u in sub_root.findall("{*}url"):
                                    l = u.find("{*}loc")
                                    if l is not None and l.text:
                                        discovered.append(l.text.strip())

                    # Check for direct URL elements
                    for u in root.findall("{*}url"):
                        loc = u.find("{*}loc")
                        if loc is not None and loc.text:
                            url_text = loc.text.strip()
                            if any(k in url_text.lower() for k in ["/product/", "/p/", "-power-bank", "-powerbank", "/item/", "/evm-products/"]):
                                discovered.append(url_text)

                    if discovered:
                        logger.info(f"[Generic Sitemap] Discovered {len(discovered)} product URLs from {sm_url}")
                        break
            except Exception as e:
                logger.debug(f"[Generic Sitemap] Sitemap check failed for {sm_url}: {e}")

        self.sitemap_urls_cache = discovered
        return discovered

    def find_product_url(
        self,
        model_name: str,
        qualifier_tokens: Optional[List[str]] = None,
        category: Optional[str] = None,
        exclude_urls: Optional[Set[str]] = None,
        out_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Tuple[str, str, float]]:
        """
        Searches discovered sitemap URLs for target product model.
        Falls back to TinyFish Search if sitemaps yield no matching product URL.
        Returns (url, matched_title, score).
        """
        from src.utils.scraper import score_candidate_match, record_candidate
        exclude = {u.strip().rstrip("/").lower() for u in (exclude_urls or set()) if u}
        urls = [u for u in self._discover_sitemap_urls() if u.strip().rstrip("/").lower() not in exclude]
        best_match = None
        best_score = -1.0

        if urls:
            for url in urls:
                slug = url.split("/")[-1].split("?")[0].replace("-", " ").replace(".html", "")
                score, is_valid, reason = score_candidate_match(
                    model_name, slug, url, brand=self.brand_name, qualifier_tokens=qualifier_tokens,
                    category=category, source_is_brand_site=True
                )
                record_candidate(out_candidates, url, slug, score, is_valid, reason, "brand-sitemap", model_name, self.brand_name)
                if is_valid and score > best_score:
                    best_score = score
                    best_match = (url, slug, score)

        # Fallback: Query TinyFish Search API for candidate product URLs on this domain
        if not best_match:
            from src.utils.tinyfish import is_tinyfish_configured, tinyfish_search
            if is_tinyfish_configured():
                queries = [
                    f"{self.brand_name} {model_name} site:{self.brand_domain}",
                    f"{self.brand_name} {model_name} {self.brand_domain}"
                ]
                for q in queries:
                    results = tinyfish_search(q, limit=6)
                    for it in results:
                        u = it.get("url", "")
                        t = it.get("title", "")
                        if not u or self.brand_domain not in u.lower() or u.strip().rstrip("/").lower() in exclude:
                            continue
                        score, is_valid, reason = score_candidate_match(
                            model_name, t, u, brand=self.brand_name, qualifier_tokens=qualifier_tokens,
                            category=category, source_is_brand_site=True
                        )
                        record_candidate(out_candidates, u, t, score, is_valid, reason, "brand-site-web-search", model_name, self.brand_name)
                        if is_valid and score > best_score:
                            best_score = score
                            best_match = (u, t, score)
                    if best_match:
                        break

        return best_match

