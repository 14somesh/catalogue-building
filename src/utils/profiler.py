import os
import re
import json
import yaml
import logging
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Tuple, Set
from urllib.parse import urlparse, urljoin

from src.utils.logger import setup_logger
from src.utils.scraper import (
    DEFAULT_HEADERS,
    DEFAULT_QUALIFIER_TOKENS,
    discover_and_verify_brand_domain,
    load_brand_defaults,
    save_brand_domain_default,
)

logger = setup_logger("profiler")

# Standard stop words and non-variant tokens to exclude from qualifier derivation
EXCLUDED_QUALIFIER_WORDS = {
    "powerbank", "power", "bank", "charger", "portable", "battery",
    "mah", "cable", "watt", "watts", "volt", "volts",
    "black", "white", "blue", "grey", "gray", "green", "red", "purple", "metallic",
    "with", "and", "for", "the", "type", "inr", "rs", "series", "edition", "pack"
}


def derive_qualifier_tokens(
    model_names: List[str],
    default_tokens: Optional[List[str]] = None
) -> List[str]:
    """
    DETERMINISTIC QUALIFIER TOKEN DERIVATION:
    Tokenizes all model names for a brand from the price sheet.
    Identifies tokens that distinguish models sharing a common prefix/family.
    Example: 'Luxcell Mini' and 'Luxcell Wireless Mini' -> 'Wireless'
             'Enmag' and 'Enmag Ace' -> 'Ace'
    Merges with the baseline _default tokens.
    """
    baseline = list(default_tokens or DEFAULT_QUALIFIER_TOKENS)
    if not model_names:
        return baseline

    # Clean model names and tokenize
    token_lists = []
    for m in model_names:
        if not m or not isinstance(m, str):
            continue
        # Split marketing separators
        clean_m = re.split(r'\s*[-–—|/]\s*', m)[0]
        # Remove capacity / wattage tokens like 10000mAh, 65W
        clean_m = re.sub(r'\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:mah|w|v|a)\b', '', clean_m, flags=re.I)
        # Tokenize alphanumeric words
        words = [w for w in re.findall(r'[a-zA-Z0-9]+', clean_m) if len(w) > 1]
        if words:
            token_lists.append(words)

    derived_tokens = set()

    # Compare every pair of models to find family clusters
    for i in range(len(token_lists)):
        for j in range(i + 1, len(token_lists)):
            words1 = token_lists[i]
            words2 = token_lists[j]

            # Common words between the two models
            common = set(w.lower() for w in words1) & set(w.lower() for w in words2)
            # If they share at least one substantive token (family name, e.g. 'luxcell', 'enmag', 'aura')
            substantive_common = [c for c in common if c not in EXCLUDED_QUALIFIER_WORDS and not c.isdigit()]
            if substantive_common:
                # Distinguishing tokens
                diff1 = [w for w in words1 if w.lower() not in common]
                diff2 = [w for w in words2 if w.lower() not in common]

                for w in diff1 + diff2:
                    w_lower = w.lower()
                    if (
                        w_lower not in EXCLUDED_QUALIFIER_WORDS
                        and not w.isdigit()
                        and len(w) >= 2
                    ):
                        derived_tokens.add(w.capitalize())

    # Build final list: baseline tokens first, followed by new derived tokens
    final_tokens = []
    for t in baseline:
        if t not in final_tokens:
            final_tokens.append(t)
    for t in sorted(derived_tokens):
        if t not in final_tokens:
            final_tokens.append(t)

    logger.info(f"[Profiler] Derived qualifier tokens for {len(model_names)} models: {final_tokens}")
    return final_tokens


def detect_platform(domain: str, timeout: Tuple[float, float] = (3.0, 5.0)) -> Tuple[str, Dict[str, Any]]:
    """
    PROFILING STEP B: E-Commerce Platform Detection
    Probes response headers, cookies, scripts, meta tags, and CDN signatures.
    Returns (platform_name, diagnostics).
    """
    clean_domain = domain.strip().lower().replace("http://", "").replace("https://", "").replace("www.", "").rstrip("/")
    url = f"https://{clean_domain}"
    diag = {"domain": clean_domain, "checks": []}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    # 1. Fast Shopify products.json probe
    try:
        pjson_url = f"https://{clean_domain}/products.json?limit=1"
        pj_res = requests.get(pjson_url, headers=headers, timeout=timeout)
        if pj_res.status_code == 200 and "products" in pj_res.text:
            diag["checks"].append("Shopify products.json responded HTTP 200")
            return "shopify", diag
    except Exception as e:
        diag["checks"].append(f"products.json probe failed: {e}")

    # 2. Main homepage probe
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        headers_str = str(r.headers).lower()
        html = r.text.lower()

        # Check headers & cookies
        if "shopify" in headers_str or "_shopify_s" in headers_str:
            diag["checks"].append("Shopify header/cookie detected")
            return "shopify", diag

        # HTML fingerprints
        if "cdn.shopify.com" in html or "myshopify.com" in html or "shopify.theme" in html:
            diag["checks"].append("Shopify script/CDN fingerprint detected")
            return "shopify", diag

        if "wp-content/plugins/woocommerce" in html or "woocommerce" in html:
            diag["checks"].append("WooCommerce plugin/class fingerprint detected")
            return "woocommerce", diag

        if "x-magento-" in headers_str or "mage/" in html or "text/x-magento-init" in html:
            diag["checks"].append("Magento script/header detected")
            return "magento", diag

        if "cdn11.bigcommerce.com" in html or "bigcommerce" in html:
            diag["checks"].append("BigCommerce CDN detected")
            return "bigcommerce", diag

        # Fallback to custom
        diag["checks"].append("No known platform signatures matched; classified as custom/generic")
        return "custom", diag

    except Exception as e:
        diag["checks"].append(f"Homepage probe error: {e}")
        return "unknown", diag


def discover_collection_url(
    domain: str,
    platform: str,
    category: str = "Powerbank",
    timeout: Tuple[float, float] = (3.0, 5.0)
) -> Optional[str]:
    """
    PROFILING STEP C: Collection URL Discovery
    Probes standard paths for the detected platform, then falls back to XML sitemap URL analysis.
    """
    clean_domain = domain.strip().lower().replace("http://", "").replace("https://", "").replace("www.", "").rstrip("/")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }

    # Standard candidate paths
    cat_slug = category.lower().replace(" ", "-")
    standard_paths = [
        f"/collections/{cat_slug}",
        f"/collections/{cat_slug}s",
        f"/collections/power-bank",
        f"/collections/powerbanks",
        f"/collections/power-banks",
        f"/collections/all",
        f"/category/{cat_slug}",
        f"/shop/{cat_slug}",
        f"/products"
    ]

    for path in standard_paths:
        test_url = f"https://{clean_domain}{path}"
        try:
            r = requests.head(test_url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                final_url = r.url
                logger.info(f"[Profiler] Found collection URL via probe: {final_url} (probed: {test_url})")
                return final_url
        except Exception:
            continue

    # Fallback: Parse XML Sitemaps to detect shared product collection pattern
    sitemap_candidates = [
        f"https://{clean_domain}/sitemap.xml",
        f"https://{clean_domain}/sitemap_products_1.xml",
        f"https://{clean_domain}/product-sitemap.xml"
    ]

    for sm_url in sitemap_candidates:
        try:
            r = requests.get(sm_url, headers=headers, timeout=timeout)
            if r.status_code == 200 and ("<urlset" in r.text or "<sitemapindex" in r.text):
                root = ET.fromstring(r.content)
                # Check for category or product URLs in sitemap
                urls = []
                for loc in root.findall(".//{*}loc"):
                    if loc.text:
                        u = loc.text.strip()
                        if any(k in u.lower() for k in [f"/{cat_slug}", "powerbank", "power-bank", "/products/"]):
                            urls.append(u)

                # Look for a category/collection page in the URLs
                for u in urls:
                    if any(k in u.lower() for k in ["/collection", "/category", "/categories", "/evm-products/"]) and not re.search(r'\.(jpg|png|webp|gif)$', u, re.I):
                        logger.info(f"[Profiler] Found collection URL via sitemap: {u}")
                        return u
        except Exception:
            continue

    return None



def record_waf_block(
    brand: str,
    domain: str,
    vendor: Optional[str] = None,
    reason: str = "HTTP 403 Bot Challenge",
    platform: str = "custom",
    config_path: str = "config/brand_defaults.yaml"
) -> None:
    """
    RUNTIME WAF LEARNING:
    Records runtime bot protection / WAF block against brand in brand_defaults.yaml.
    Future runs check waf_blocked: true and escalate directly to retail.
    """
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    if "brands" not in cfg:
        cfg["brands"] = {}

    if brand not in cfg["brands"]:
        cfg["brands"][brand] = {}

    brand_entry = cfg["brands"][brand]
    brand_entry["domain"] = domain
    brand_entry["platform"] = platform
    brand_entry["waf_blocked"] = True
    if vendor:
        brand_entry["waf_vendor"] = vendor
    brand_entry["waf_reason"] = reason
    brand_entry["waf_blocked_at"] = now_iso

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)

    logger.warning(f"[WAF Learning] Persisted WAF block for brand '{brand}' ({domain}): {vendor or 'WAF'} - {reason} at {now_iso}")


def profile_brand(
    brand: str,
    model_names: List[str],
    category: Optional[str] = None,
    existing_domain: Optional[str] = None,
    config_path: str = "config/brand_defaults.yaml"
) -> Dict[str, Any]:
    """
    UNIFIED BRAND PROFILER STEP
    Runs once per brand at confirm time (after row approval, before collection).
    Learns:
      a) Domain (via discover_and_verify_brand_domain)
      b) Platform (Shopify, WooCommerce, Magento, Custom)
      c) Collection URL (Probed following redirects, then Sitemap)
      d) Qualifier tokens (Union of existing hand-configured and derived tokens)
    Writes to brand_defaults.yaml and returns structured diagnostic report.
    """
    clean_brand = brand.strip()
    if not category or not str(category).strip():
        raise ValueError("Category is required for profiling brand defaults. Writing category-scoped keys at the brand root is prohibited.")
    clean_category = str(category).strip()

    report = {
        "brand": clean_brand,
        "category": clean_category,
        "domain": None,
        "platform": None,
        "collection_url": None,
        "qualifier_tokens": [],
        "waf_blocked": False,
        "waf_vendor": None,
        "waf_reason": None,
        "found": [],
        "not_found": []
    }

    # Load existing config for this brand and category
    existing_cfg = load_brand_defaults(clean_brand, category=clean_category, config_path=config_path)

    # Check if brand is known to be WAF blocked
    if existing_cfg.get("waf_blocked"):
        report["waf_blocked"] = True
        report["waf_vendor"] = existing_cfg.get("waf_vendor")
        report["waf_reason"] = existing_cfg.get("waf_reason")
        report["domain"] = existing_cfg.get("domain")
        report["found"].append(f"WAF block recorded ({existing_cfg.get('waf_vendor', 'WAF')}: {existing_cfg.get('waf_reason', 'Bot Challenge')})")

    # a) Domain
    domain = existing_domain or existing_cfg.get("domain")
    if not domain and not report["waf_blocked"]:
        disc = discover_and_verify_brand_domain(clean_brand, model_names, category=clean_category, config_path=config_path)
        domain = disc.get("domain")
        if disc.get("waf_blocked"):
            report["waf_blocked"] = True
            report["waf_vendor"] = disc.get("waf_vendor")
            report["waf_reason"] = disc.get("waf_reason")
            report["found"].append(f"WAF block detected during discovery ({disc.get('waf_vendor', 'WAF')}: {disc.get('waf_reason')})")

    if domain:
        report["domain"] = domain
        report["found"].append(f"Domain: {domain}")
    else:
        report["not_found"].append("Domain: Not found / no official storefront verified")

    # If domain exists and not WAF blocked, discover platform, collection URL
    platform = "shopify"
    collection_url = None

    if domain and not report["waf_blocked"]:
        # b) Platform Detection
        detected_plat, plat_diag = detect_platform(domain)
        platform = detected_plat if detected_plat != "unknown" else "custom"
        report["platform"] = platform
        report["found"].append(f"Platform: {platform}")

        # c) Collection URL
        collection_url = discover_collection_url(domain, platform, category=clean_category)
        if collection_url:
            report["collection_url"] = collection_url
            report["found"].append(f"Collection URL: {collection_url}")
        else:
            report["not_found"].append("Collection URL: Not found in standard paths or sitemaps")

    # d) Qualifier tokens (Union of existing hand-configured tokens and derived tokens)
    derived_tokens = derive_qualifier_tokens(model_names)
    existing_tokens = existing_cfg.get("qualifier_tokens", [])
    combined_tokens = list(dict.fromkeys(existing_tokens + derived_tokens))
    report["qualifier_tokens"] = combined_tokens
    report["found"].append(f"Qualifier tokens ({len(combined_tokens)} tokens): {', '.join(combined_tokens)}")

    # Persist learned metadata to config/brand_defaults.yaml strictly partitioned by category
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    if "brands" not in cfg:
        cfg["brands"] = {}

    brand_entry = cfg["brands"].get(clean_brand, {})
    if domain:
        brand_entry["domain"] = domain
    if platform:
        brand_entry["platform"] = platform

    if "categories" not in brand_entry or not isinstance(brand_entry["categories"], dict):
        brand_entry["categories"] = {}

    cat_entry = brand_entry["categories"].get(clean_category, {})
    if collection_url:
        cat_entry["collection_url"] = collection_url
    if combined_tokens:
        cat_entry["qualifier_tokens"] = combined_tokens

    brand_entry["categories"][clean_category] = cat_entry

    # Guard: Never allow category-scoped keys or obsolete keys at the brand root
    brand_entry.pop("collection_url", None)
    brand_entry.pop("qualifier_tokens", None)
    brand_entry.pop("image_tier_order", None)
    brand_entry.pop("default_warranty", None)

    cfg["brands"][clean_brand] = brand_entry

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)

    logger.info(f"[Profiler] Successfully completed brand profiling for '{clean_brand}' [{clean_category}] and saved to {config_path}")
    return report
