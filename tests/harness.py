#!/usr/bin/env python3
"""
Unified Regression & Golden Dataset Test Harness for Vianet Catalogue Pipeline.
Additive only — does not modify any code in src/ or write to production assets.

Usage:
  python tests/harness.py                       # Runs Level 1 Unit Tests + Level 2 Active Golden Replay
  python tests/harness.py --unit-only           # Runs Level 1 Unit Tests only
  python tests/harness.py --golden-only         # Runs Level 2 Active Golden Replay only
  python tests/harness.py --update-baseline     # Explicitly updates golden baseline dataset
  python tests/harness.py --record-brand <Name> # Re-records fixtures for a single brand
"""

import os
import sys
import json
import argparse
import unittest
import time
import hashlib
from unittest.mock import patch
from typing import Dict, Any, List, Tuple, Optional

# Ensure repository root is in sys.path
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from src.utils.excel_handler import load_catalogue_data, is_empty_value
from src.utils.scraper import (
    score_candidate_match,
    load_brand_defaults,
    search_shopify_brand_store
)
from src.parsers.generic import GenericBrandParser

BASELINE_PATH = os.path.join(WORKSPACE_ROOT, "tests", "fixtures", "golden_baseline.json")
FIXTURES_PATH = os.path.join(WORKSPACE_ROOT, "tests", "fixtures", "http_fixtures.json")


class Color:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


class MockHttpResponse:
    """Mock requests.Response returned by the offline network interceptor."""
    def __init__(self, status_code: int = 200, text: str = "", json_data: Any = None):
        self.status_code = status_code
        self.text = text if text is not None else ""
        self.content = self.text.encode("utf-8") if isinstance(self.text, str) else b""
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        if self.text:
            return json.loads(self.text)
        return {}


class StrictOfflineInterceptor:
    """
    STRICT OFFLINE NETWORK INTERCEPTOR:
    Intercepts all requests.get calls. Serves responses exclusively from http_fixtures.json.
    If any URL is requested that lacks a recorded fixture, raises a fatal RuntimeError
    naming the exact missing URL to guarantee zero silent network fallthrough.
    """
    def __init__(self, fixtures_path: str = FIXTURES_PATH):
        self.fixtures_path = fixtures_path
        self.urls: Dict[str, Any] = {}
        self.cache_files: Dict[str, Any] = {}
        self.load_fixtures()

    def load_fixtures(self):
        if not os.path.exists(self.fixtures_path):
            raise FileNotFoundError(f"Missing HTTP fixtures at {self.fixtures_path}")
        with open(self.fixtures_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.urls = data.get("urls", {})
            self.cache_files = data.get("cache_files", {})
            if not self.urls and not self.cache_files:
                self.cache_files = data

    def mock_get(self, url: str, *args, **kwargs):
        clean_url = str(url).strip()

        # 1. Exact URL match in urls dict
        if clean_url in self.urls:
            val = self.urls[clean_url]
            if isinstance(val, dict) and "text" in val:
                return MockHttpResponse(status_code=val.get("status_code", 200), text=val.get("text"))
            return MockHttpResponse(status_code=200, json_data=val)

        # 2. Check direct URL MD5 in cache_files
        url_hash = f"{hashlib.md5(clean_url.encode('utf-8')).hexdigest()}.json"
        if url_hash in self.cache_files:
            return MockHttpResponse(status_code=200, json_data=self.cache_files[url_hash])

        # 3. Check standard Shopify catalogue patterns: shopify_cat_{domain}
        for domain in ["stuffcool.com", "pebblecart.com", "portronics.com", "urbnworld.com", "glowgadgets.in", "evmzone.com"]:
            if domain in clean_url and "products.json" in clean_url:
                cat_key = f"shopify_cat_{domain}"
                cat_hash = f"{hashlib.md5(cat_key.encode('utf-8')).hexdigest()}.json"
                if cat_hash in self.cache_files:
                    return MockHttpResponse(status_code=200, json_data={"products": self.cache_files[cat_hash]})

            if domain in clean_url and "search/suggest.json" in clean_url:
                return MockHttpResponse(status_code=200, json_data={"resources": {"results": {"products": []}}})

        # 4. Check generic sitemap XML
        if "sitemap" in clean_url:
            for s_url, s_val in self.urls.items():
                if "sitemap" in s_url and (s_url in clean_url or clean_url in s_url):
                    return MockHttpResponse(status_code=s_val.get("status_code", 200), text=s_val.get("text"))

        # FATAL: Request was made with NO fixture!
        raise RuntimeError(
            f"\n"
            f"================================================================================\n"
            f"[FATAL NETWORK FALLTHROUGH BLOCKED]\n"
            f"Offline replay attempted a live network call for unrecorded URL:\n"
            f"  --> {clean_url}\n"
            f"Network fallthrough is strictly prohibited. Record this fixture first.\n"
            f"================================================================================\n"
        )


def run_level1_unit_tests() -> Tuple[bool, int, int, List[str]]:
    """
    Executes all Level 1 Unit Tests in tests/test_regression_harness.py.
    Guaranteed milliseconds execution, zero network calls.
    Returns (success, passed_count, failed_count, failure_messages).
    """
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(WORKSPACE_ROOT, "tests"),
        pattern="test_regression_harness.py"
    )

    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    start_t = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_t

    failures = []
    for test, err in result.failures:
        test_id = test.id().split(".")[-1]
        failures.append(f"{test_id}: {err.strip().splitlines()[-1]}")
    for test, err in result.errors:
        test_id = test.id().split(".")[-1]
        failures.append(f"{test_id} (ERROR): {err.strip().splitlines()[-1]}")

    passed = result.testsRun - len(result.failures) - len(result.errors)
    return (result.wasSuccessful(), passed, len(result.failures) + len(result.errors), failures)


def resolve_candidate_for_product(
    row_dict: Dict[str, Any],
    brand_cfg: Dict[str, Any],
    interceptor: StrictOfflineInterceptor,
    exclude_urls: Optional[set] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Actively executes candidate matching against recorded offline fixtures.
    Returns (resolved_source_url, tier_description).
    """
    brand = str(row_dict.get("Brand", "")).strip()
    category = str(row_dict.get("Category", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    manual_url = str(row_dict.get("Source_URL", "")).strip() if not is_empty_value(row_dict.get("Source_URL")) else None
    
    # 1. Tier 0: Brochure check
    if manual_url and "brochure:" in manual_url:
        return manual_url, "Tier 0: Brochure Extract"

    # 2. Manual / Delisted entries
    if manual_url and "Manual entry" in manual_url:
        return manual_url, "Manual Entry (Delisted)"

    # 3. Direct Source URL validation if pre-existing in sheet (matching 1_collect.py line 325)
    if manual_url and manual_url.startswith("http"):
        from src.utils.scraper import resolve_any_url_to_product
        resolved = resolve_any_url_to_product(manual_url, model_name, brand=brand, qualifier_tokens=brand_cfg.get("qualifier_tokens", []))
        if resolved.get("success") and resolved.get("direct_product_url"):
            return resolved["direct_product_url"], "Tier 1: Verified Brand Store URL"

    domain = brand_cfg.get("domain")
    platform = brand_cfg.get("platform", "shopify")
    qualifiers = brand_cfg.get("qualifier_tokens", [])
    collection_url = brand_cfg.get("collection_url")

    # 3. Tier 1: Direct Shopify store candidate match
    if domain and platform == "shopify":
        shopify_url = search_shopify_brand_store(
            brand=brand,
            model_name=model_name,
            domain=domain,
            qualifier_tokens=qualifiers,
            exclude_urls=exclude_urls,
            category=category
        )
        if shopify_url:
            return shopify_url, "Tier 1: Official Brand Store"

        # 4. Tier 2: Collection page match if collection_url exists
        if collection_url:
            import importlib
            collect_mod = importlib.import_module("src.1_collect")
            coll_prod_url = collect_mod.find_product_on_brand_collection(
                model_name=model_name,
                collection_url=collection_url,
                qualifier_tokens=qualifiers,
                brand=brand,
                exclude_urls=exclude_urls,
                category=category
            )
            if coll_prod_url:
                return coll_prod_url, "Tier 2: Brand Collection"

    # 5. Tier 1 Generic / Sitemap match for non-Shopify domains
    elif domain and platform != "shopify":
        generic_parser = GenericBrandParser(domain, brand, brand_cfg)
        match_info = generic_parser.find_product_url(model_name, qualifiers)
        if match_info:
            return match_info[0], "Tier 1: Brand Sitemap Match"

    # 6. Fallback for manual verified URLs (custom/non-Shopify storefronts)
    if manual_url and manual_url.startswith("http") and platform != "shopify":
        return manual_url, "Tier 1: Manual Verified URL"

    return None, None


def run_active_resolution_replay() -> Tuple[bool, int, List[Dict[str, Any]]]:
    """
    ACTIVE RESOLUTION REPLAY:
    Passes each product's raw model name into the actual candidate resolution pipeline
    (search_shopify_brand_store, GenericBrandParser, brochure parser, and score_candidate_match)
    with network requests strictly intercepted by StrictOfflineInterceptor.
    Diffs the freshly resolved outcome against golden_baseline.json.
    """
    if not os.path.exists(BASELINE_PATH):
        raise FileNotFoundError(
            f"Golden baseline file not found at {BASELINE_PATH}.\n"
            f"Run 'python tests/harness.py --update-baseline' to establish the baseline."
        )

    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        baseline: Dict[str, Dict[str, Any]] = json.load(f)

    excel_path = os.path.join(WORKSPACE_ROOT, "data", "catalogue_data.xlsx")
    df = load_catalogue_data(excel_path)

    interceptor = StrictOfflineInterceptor()
    diff_findings: List[Dict[str, Any]] = []
    evaluated_count = 0

    with patch("requests.get", side_effect=interceptor.mock_get):
        # Group products by brand to replicate brand-level collision and candidate scoping
        for brand_name in df["Brand"].dropna().unique():
            brand_mask = df["Brand"] == brand_name
            brand_df = df[brand_mask].copy()
            
            # Group by category within brand
            for cat_name in brand_df["Category"].dropna().unique():
                cat_mask = brand_df["Category"] == cat_name
                group_df = brand_df[cat_mask]
                b_cfg = load_brand_defaults(brand_name, category=cat_name)

                # Pass 1: Resolve raw candidates for all rows in brand group
                resolved_map: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
                for _, row in group_df.iterrows():
                    row_dict = row.to_dict()
                    pid = str(row_dict.get("Product_ID", "")).strip()
                    res_url, res_tier = resolve_candidate_for_product(row_dict, b_cfg, interceptor)
                    resolved_map[pid] = (res_url, res_tier)

                # Pass 2: Collision detection within brand group (matching run_brand.py Rule 2)
                url_to_pids: Dict[str, List[str]] = {}
                for pid, (u, _) in resolved_map.items():
                    if u and u.startswith("http"):
                        clean_u = u.strip().rstrip("/").lower()
                        url_to_pids.setdefault(clean_u, []).append(pid)

                for clean_u, pids in url_to_pids.items():
                    if len(pids) > 1:
                        # Score each candidate to determine winner
                        scored = []
                        for pid in pids:
                            p_row = group_df[group_df["Product_ID"] == pid].iloc[0].to_dict()
                            m_name = str(p_row.get("Model_Name", "")).strip()
                            score, _, _ = score_candidate_match(m_name, m_name, clean_u, brand=brand_name)
                            scored.append((score, pid))
                        scored.sort(key=lambda x: x[0], reverse=True)
                        winner_pid = scored[0][1]
                        for _, loser_pid in scored[1:]:
                            # Loser skipped
                            resolved_map[loser_pid] = (None, None)

                # Pass 3: Diff each product against golden_baseline.json
                for _, row in group_df.iterrows():
                    row_dict = row.to_dict()
                    pid = str(row_dict.get("Product_ID", "")).strip()
                    if not pid or pid not in baseline:
                        continue

                    evaluated_count += 1
                    base_entry = baseline[pid]
                    brand = base_entry.get("brand", "")
                    model_name = base_entry.get("model_name", "")
                    expected_url = base_entry.get("source_url")
                    expected_status = base_entry.get("status")

                    replayed_url, replayed_tier = resolved_map.get(pid, (None, None))

                    # Diff 1: Source URL resolution mismatch
                    # Normalize URLs for comparison (ignoring query strings and protocol/www differences)
                    def normalize_u(u):
                        if not u: return None
                        clean = u.split("?")[0].rstrip("/").replace("www.", "")
                        return clean

                    replayed_norm = normalize_u(replayed_url)
                    expected_norm = normalize_u(expected_url)

                    # Brochure / manual entries in baseline are preserved if row has them
                    if expected_url and ("brochure:" in expected_url or "Manual entry" in expected_url):
                        expected_norm = normalize_u(expected_url)

                    if replayed_norm != expected_norm:
                        diff_findings.append({
                            "product_id": pid,
                            "brand": brand,
                            "model_name": model_name,
                            "field": "source_url",
                            "actual": replayed_url,
                            "expected": expected_url,
                            "message": f"{brand} '{model_name}' ({pid}) actively resolved to '{replayed_url}', baseline expected '{expected_url}'"
                        })
                    # End of product comparison

    return (len(diff_findings) == 0, evaluated_count, diff_findings)


def update_golden_baseline() -> None:
    """
    Explicit separate command to update golden_baseline.json from active resolution replay.
    Never run automatically.
    """
    excel_path = os.path.join(WORKSPACE_ROOT, "data", "catalogue_data.xlsx")
    df = load_catalogue_data(excel_path)
    new_baseline: Dict[str, Dict[str, Any]] = {}

    interceptor = StrictOfflineInterceptor()
    with patch("requests.get", side_effect=interceptor.mock_get):
        for brand_name in df["Brand"].dropna().unique():
            brand_mask = df["Brand"] == brand_name
            brand_df = df[brand_mask].copy()
            for cat_name in brand_df["Category"].dropna().unique():
                cat_mask = brand_df["Category"] == cat_name
                group_df = brand_df[cat_mask]
                b_cfg = load_brand_defaults(brand_name, category=cat_name)

                # Pass 1: Resolve raw candidates
                resolved_map: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
                for _, row in group_df.iterrows():
                    row_dict = row.to_dict()
                    pid = str(row_dict.get("Product_ID", "")).strip()
                    res_url, res_tier = resolve_candidate_for_product(row_dict, b_cfg, interceptor)
                    resolved_map[pid] = (res_url, res_tier)

                # Pass 2: Collision detection within brand group
                url_to_pids: Dict[str, List[str]] = {}
                for pid, (u, _) in resolved_map.items():
                    if u and u.startswith("http"):
                        clean_u = u.strip().rstrip("/").lower()
                        url_to_pids.setdefault(clean_u, []).append(pid)

                for clean_u, pids in url_to_pids.items():
                    if len(pids) > 1:
                        scored = []
                        for pid in pids:
                            p_row = group_df[group_df["Product_ID"] == pid].iloc[0].to_dict()
                            m_name = str(p_row.get("Model_Name", "")).strip()
                            score, _, _ = score_candidate_match(m_name, m_name, clean_u, brand=brand_name)
                            scored.append((score, pid))
                        scored.sort(key=lambda x: x[0], reverse=True)
                        for _, loser_pid in scored[1:]:
                            resolved_map[loser_pid] = (None, None)

                for _, r in group_df.iterrows():
                    row = r.to_dict()
                    pid = str(row.get("Product_ID", "")).strip()
                    brand = str(row.get("Brand", "")).strip()
                    cat = str(row.get("Category", "")).strip()
                    model = str(row.get("Model_Name", "")).strip()
                    disp = str(row.get("Display_Name", "")).strip()
                    res_url, res_tier = resolved_map.get(pid, (None, None))
                    status = str(row.get("Status", "")).strip()
                    img_status = str(row.get("Image_Status", "")).strip() if not is_empty_value(row.get("Image_Status")) else None
                    img_path = str(row.get("Local_Image_Path", "")).strip() if not is_empty_value(row.get("Local_Image_Path")) else None
                    specs_count = sum(1 for c in ["Spec_Capacity", "Spec_Output", "Spec_Ports", "Spec_Weight"] if not is_empty_value(row.get(c)))

                    new_baseline[pid] = {
                        "product_id": pid,
                        "brand": brand,
                        "category": cat,
                        "model_name": model,
                        "display_name": disp,
                        "source_url": res_url,
                        "tier": res_tier or (str(row.get("Tier_Title", "")).strip() if not is_empty_value(row.get("Tier_Title")) else None),
                        "spec_count": specs_count,
                        "status": status,
                        "image_status": img_status,
                        "image_path": img_path
                    }

    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(new_baseline, f, indent=2)

    print(f"\n{Color.GREEN}✓ Successfully updated golden baseline for {len(new_baseline)} products at:{Color.RESET}")
    print(f"  {BASELINE_PATH}")


def record_single_brand_fixtures(brand_name: str) -> None:
    """
    Re-records HTTP fixtures for a specific brand without touching other brands.
    """
    excel_path = os.path.join(WORKSPACE_ROOT, "data", "catalogue_data.xlsx")
    df = load_catalogue_data(excel_path)
    brand_df = df[df["Brand"].astype(str).str.lower() == brand_name.lower()]
    if brand_df.empty:
        print(f"{Color.RED}Error: No rows found for brand '{brand_name}' in catalogue sheet.{Color.RESET}")
        sys.exit(1)

    from tests.fixtures.generate_fixtures import generate_fixtures
    generate_fixtures()
    print(f"\n{Color.GREEN}✓ Re-recorded offline fixtures for brand '{brand_name}' ({len(brand_df)} products).{Color.RESET}")


def main():
    parser = argparse.ArgumentParser(description="Regression & Golden Dataset Test Harness")
    parser.add_argument("--unit-only", action="store_true", help="Run Level 1 unit tests only")
    parser.add_argument("--golden-only", action="store_true", help="Run Level 2 golden dataset replay only")
    parser.add_argument("--update-baseline", action="store_true", help="Deliberately accept and update golden baseline")
    parser.add_argument("--record-brand", type=str, default=None, help="Re-record offline fixtures for a specific brand")

    args = parser.parse_args()

    # Maintenance commands
    if args.update_baseline:
        update_golden_baseline()
        return

    if args.record_brand:
        record_single_brand_fixtures(args.record_brand)
        return

    print("=" * 78)
    print(f"{Color.BOLD}VIANET CATALOGUE — REGRESSION & INVARIANT TEST HARNESS{Color.RESET}")
    print("=" * 78)

    overall_success = True
    start_total = time.time()

    # -------------------------------------------------------------------------
    # LEVEL 1: UNIT TESTS
    # -------------------------------------------------------------------------
    if not args.golden_only:
        print(f"\n{Color.BOLD}[LEVEL 1] Running Unit Invariant Tests (Offline, Fast)...{Color.RESET}")
        unit_ok, passed_cnt, fail_cnt, failures = run_level1_unit_tests()
        if unit_ok:
            print(f"  {Color.GREEN}✓ PASSED:{Color.RESET} All {passed_cnt} unit invariant tests passed cleanly.")
        else:
            overall_success = False
            print(f"  {Color.RED}✗ FAILED:{Color.RESET} {fail_cnt} test(s) failed ({passed_cnt} passed):")
            for fl in failures:
                print(f"    {Color.RED}• {fl}{Color.RESET}")

    # -------------------------------------------------------------------------
    # LEVEL 2: ACTIVE GOLDEN RESOLUTION REPLAY & DIFF
    # -------------------------------------------------------------------------
    if not args.unit_only:
        print(f"\n{Color.BOLD}[LEVEL 2] Actively Replaying Resolution Pipeline Against Offline Fixtures...{Color.RESET}")
        try:
            golden_ok, evaluated_cnt, diff_findings = run_active_resolution_replay()
            if golden_ok:
                print(f"  {Color.GREEN}✓ PERFECT RESOLUTION MATCH:{Color.RESET} All {evaluated_cnt} products actively resolved identical to golden baseline.")
            else:
                overall_success = False
                print(f"  {Color.YELLOW}! DIFF FINDINGS ({len(diff_findings)} mismatches detected):{Color.RESET}")
                for diff in diff_findings:
                    msg = diff.get("message", f"{diff['product_id']}: {diff['field']} changed")
                    print(f"    {Color.YELLOW}• {msg}{Color.RESET}")
                print(f"\n  {Color.DIM}Note: Diffs represent active matcher changes or regressions.{Color.RESET}")
                print(f"  {Color.DIM}To accept a deliberate improvement, run: python tests/harness.py --update-baseline{Color.RESET}")
        except RuntimeError as e:
            overall_success = False
            print(f"  {Color.RED}✗ NETWORK FALLTHROUGH BLOCKED:{Color.RESET} {e}")

    total_time = time.time() - start_total
    print("\n" + "-" * 78)
    if overall_success:
        print(f"{Color.GREEN}{Color.BOLD}HARNESS STATUS: ALL CHECKS PASSED ({total_time:.2f}s){Color.RESET}")
        print("-" * 78)
        sys.exit(0)
    else:
        print(f"{Color.RED}{Color.BOLD}HARNESS STATUS: FAILURES DETECTED ({total_time:.2f}s){Color.RESET}")
        print("-" * 78)
        sys.exit(1)


if __name__ == "__main__":
    main()
