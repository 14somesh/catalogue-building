import re
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

from src.parsers.base import BaseBrandParser, ParserResult
from src.parsers.brochure import parse_specs_from_text
from src.utils.scraper import (
    DEFAULT_HEADERS,
    clean_html_text,
    extract_model_name_portion,
    normalize_model_tokens,
    score_candidate_match,
)

logger = logging.getLogger("generic_parser")


def detect_ecommerce_platform(domain_or_url: str) -> Tuple[str, Optional[str]]:
    """
    Detects the e-commerce platform of a website (Shopify, WooCommerce, Magento, or Custom).
    Returns (platform_name, diagnostic_detail).
    """
    url = domain_or_url if domain_or_url.startswith("http") else f"https://{domain_or_url}"
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=10, allow_redirects=True)
        headers_str = str(r.headers).lower()
        html = r.text.lower()

        # 1. Shopify Detection
        if "shopify" in headers_str or "cdn.shopify.com" in html or "myshopify.com" in html:
            return "shopify", "Detected via Shopify CDN / headers"

        # 2. WooCommerce Detection
        if "woocommerce" in html or "wp-content/plugins/woocommerce" in html or 'name="generator" content="woocommerce' in html:
            return "woocommerce", "Detected via WooCommerce meta / plugin scripts"

        # 3. Magento Detection
        if "mage/" in html or "magento" in html or "mage-init" in html or "text/x-magento-init" in html:
            return "magento", "Detected via Magento scripts / x-magento-init"

        # 4. BigCommerce
        if "cdn11.bigcommerce.com" in html or "bigcommerce" in html:
            return "bigcommerce", "Detected via BigCommerce CDN / assets"

        # Unknown / Custom
        logger.warning(f"[Platform Detection] WARNING: Could not identify e-commerce platform for '{domain_or_url}'. Operating in generic fallback mode (JSON-LD + Sitemap).")
        return "custom", "Platform could not be determined; using generic JSON-LD + Sitemap parser"

    except Exception as e:
        logger.warning(f"[Platform Detection] Probe failed for '{domain_or_url}': {e}. Defaulting to generic parser.")
        return "custom", f"Probe error: {e}"


class GenericBrandParser(BaseBrandParser):
    """
    Generic brand parser supporting WooCommerce, Magento, BigCommerce, and custom web stores.
    Discovers products via XML Sitemaps and extracts specs and high-res images via JSON-LD + HTML.
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
            f"https://www.google.com/sitemap.xml",
        ]

        discovered = []
        for sm_url in sitemap_candidates:
            try:
                r = requests.get(sm_url, headers=DEFAULT_HEADERS, timeout=8)
                if r.status_code == 200 and ("<urlset" in r.text or "<sitemapindex" in r.text):
                    # Parse XML
                    root = ET.fromstring(r.content)
                    # Check for sub-sitemaps
                    for sitemap_tag in root.findall("{*}sitemap"):
                        loc = sitemap_tag.find("{*}loc")
                        if loc is not None and loc.text and "product" in loc.text.lower():
                            sub_r = requests.get(loc.text, headers=DEFAULT_HEADERS, timeout=8)
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
                            if any(k in url_text.lower() for k in ["/product/", "/p/", "-power-bank", "-powerbank", "/item/"]):
                                discovered.append(url_text)

                    if discovered:
                        logger.info(f"[Generic Sitemap] Discovered {len(discovered)} product URLs from {sm_url}")
                        break
            except Exception as e:
                logger.debug(f"[Generic Sitemap] Sitemap check failed for {sm_url}: {e}")

        self.sitemap_urls_cache = discovered
        return discovered

    def find_product_url(self, model_name: str, qualifier_tokens: Optional[List[str]] = None) -> Optional[Tuple[str, str, float]]:
        """
        Searches discovered sitemap URLs for target product model.
        Returns (url, matched_title, score).
        """
        urls = self._discover_sitemap_urls()
        if not urls:
            return None

        best_match = None
        best_score = -1.0

        for url in urls:
            slug = url.split("/")[-1].split("?")[0].replace("-", " ")
            score, is_valid, reason = score_candidate_match(
                model_name, slug, url, brand=self.brand_name, qualifier_tokens=qualifier_tokens
            )
            if is_valid and score > best_score:
                best_score = score
                best_match = (url, slug, score)

        return best_match

    def parse_product_page(self, url: str, target_model_name: str) -> ParserResult:
        """
        Parses a generic e-commerce product page via JSON-LD + HTML fallback.
        Extracts specifications and maximum-resolution product images.
        """
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)
            if r.status_code != 200:
                return ParserResult(success=False, raw_specs={}, error_message=f"HTTP {r.status_code}")

            soup = BeautifulSoup(r.text, "html.parser")
            raw_specs = {}
            candidate_images = []

            # 1. Parse JSON-LD Product Schemas
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "{}")
                    items = data if isinstance(data, list) else [data]
                    if isinstance(data, dict) and "@graph" in data:
                        items.extend(data["@graph"])

                    for item in items:
                        if isinstance(item, dict) and item.get("@type") in ("Product", "IndividualProduct"):
                            # Specs from JSON-LD
                            desc = item.get("description") or ""
                            specs_from_desc = parse_specs_from_text(desc)
                            raw_specs.update(specs_from_desc)

                            # Structured attributes
                            for prop in item.get("additionalProperty", []):
                                if isinstance(prop, dict):
                                    name = str(prop.get("name", "")).lower()
                                    val = str(prop.get("value", "")).strip()
                                    if "capacity" in name and "capacity" not in raw_specs:
                                        raw_specs["capacity"] = val
                                    elif "output" in name and "output" not in raw_specs:
                                        raw_specs["output"] = val
                                    elif "port" in name and "ports" not in raw_specs:
                                        raw_specs["ports"] = val
                                    elif "weight" in name and "weight" not in raw_specs:
                                        raw_specs["weight"] = val

                            # High-res Images from JSON-LD
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

            # 2. HTML Text / Spec Table Fallback
            if len(raw_specs) < 3:
                # Look for WooCommerce or standard specification tables
                spec_tables = soup.find_all(["table", "dl", "div"], class_=re.compile(r"spec|attribute|tech|detail", re.I))
                spec_text = ""
                for st in spec_tables:
                    spec_text += "\n" + clean_html_text(str(st))
                if not spec_text:
                    spec_text = clean_html_text(r.text)
                
                table_specs = parse_specs_from_text(spec_text)
                for k, v in table_specs.items():
                    if k not in raw_specs or not raw_specs[k]:
                        raw_specs[k] = v

            # 3. High-Resolution HTML Image Extraction
            # Check for data-zoom-image, data-large-img, og:image, srcset
            for img in soup.find_all("img"):
                high_res_url = (
                    img.get("data-zoom-image") or
                    img.get("data-large-img") or
                    img.get("data-high-res") or
                    img.get("data-master") or
                    img.get("data-src")
                )
                if high_res_url:
                    full_u = urljoin(url, high_res_url)
                    if full_u not in candidate_images and not any(ign in full_u.lower() for ign in ["logo", "icon", "badge"]):
                        candidate_images.append(full_u)

                # Parse srcset for largest resolution descriptor
                srcset = img.get("srcset")
                if srcset:
                    parts = [p.strip().split() for p in srcset.split(",") if p.strip()]
                    if parts:
                        # Sort by descriptor width
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
                        if full_u not in candidate_images and not any(ign in full_u.lower() for ign in ["logo", "icon", "badge"]):
                            candidate_images.append(full_u)

            # og:image fallback
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                full_u = urljoin(url, og_img["content"])
                if full_u not in candidate_images:
                    candidate_images.append(full_u)

            image_url = candidate_images[0] if candidate_images else None

            # Apply brand default warranty if missing
            default_warranty = self.config.get("default_warranty")
            if "warranty" not in raw_specs and default_warranty:
                raw_specs["warranty"] = default_warranty

            field_sources = {k: url for k in raw_specs}
            field_tiers = {k: 1 for k in raw_specs}

            return ParserResult(
                success=len(raw_specs) >= 2,
                raw_specs=raw_specs,
                field_sources=field_sources,
                field_tiers=field_tiers,
                image_url=image_url,
                image_source="brand-product-page-generic" if image_url else None,
                image_tier=1 if image_url else None,
                all_images=candidate_images
            )

        except Exception as e:
            logger.error(f"[Generic Parser] Failed parsing {url}: {e}")
            return ParserResult(success=False, raw_specs={}, error_message=str(e))
