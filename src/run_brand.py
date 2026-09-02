import os
import sys
import re
import json
import argparse
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Union, Callable, Any
from PIL import Image
import pandas as pd

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    is_empty_value,
    slugify,
    get_effective_value
)
from src.utils.validators import validate_row_deterministic
from src.utils.scraper import load_brand_defaults
from src.utils.llm_client import preflight_quota_check, AllLLMProvidersExhaustedError
import importlib
review_mod = importlib.import_module("src.3_review")
process_row_loop = review_mod.process_row_loop
load_config = review_mod.load_config
collect_mod = importlib.import_module("src.1_collect")
from src.utils.logger import setup_logger

logger = setup_logger("run_brand")


def generate_run_report(brand: str, brand_rows: list, output_dir: str = "dist", category: str = "powerbank") -> str:
    """
    Generates a comprehensive Markdown run report for human review:
    - Summary header: Ready / Blocked / Deferred / Warnings
    - Blocked and Deferred rows grouped with diagnostic fix logs
    - Full breakdown per product row with field sources, tiers, attempts, image metrics, and LLM Provider.
    """
    brand_slug = slugify(brand)
    category_slug = slugify(category)
    clean_output_dir = output_dir.rstrip("/\\")
    if category_slug not in clean_output_dir.lower():
        cat_output_dir = os.path.join(clean_output_dir, category_slug)
    else:
        cat_output_dir = clean_output_dir

    brand_output_dir = os.path.join(cat_output_dir, brand_slug) if brand_slug not in cat_output_dir else cat_output_dir
    os.makedirs(brand_output_dir, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_filename = f"RUN_REPORT_{brand_slug}_{timestamp_str}.md"
    report_path = os.path.join(brand_output_dir, report_filename)

    ready_rows = [r for r in brand_rows if r.get("Status") in ("Ready_For_Review", "Approved")]
    blocked_rows = [r for r in brand_rows if r.get("Status") == "Blocked"]
    skipped_rows = [r for r in brand_rows if r.get("Status") == "Skipped"]
    deferred_rows = [r for r in brand_rows if r.get("Status") == "Deferred"]
    warn_rows = [r for r in brand_rows if not is_empty_value(r.get("Flags")) and r.get("Status") not in ("Blocked", "Skipped", "Deferred")]

    report_lines = [
        f"# Pipeline Run Report: Brand '{brand}'",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Total Products:** {len(brand_rows)} | **Ready for Review:** {len(ready_rows)} | **Blocked (Needs Human):** {len(blocked_rows)} | **Skipped (Exhausted):** {len(skipped_rows)} | **Deferred:** {len(deferred_rows)} | **Warnings:** {len(warn_rows)}",
        "",
        "---",
        ""
    ]

    # ==================== BLOCKED ROWS SECTION (Grouped at top) ====================
    if blocked_rows:
        report_lines.append("## ⛔ BLOCKED ROWS (Requires Human Resolution)")
        report_lines.append("The following products failed due to active data contradictions (qualifier token mismatch, capacity conflict, duplicate image). Zero unverified data was written.")
        report_lines.append("")
        for b_row in blocked_rows:
            pid = b_row.get("Product_ID")
            model = b_row.get("Model_Name")
            flags = b_row.get("Flags") or "Contradiction detected"
            attempts = b_row.get("Attempts", 0)
            fix_log = b_row.get("Fix_Log") or "No attempts logged"

            report_lines.append(f"### 🔴 [{pid}] {brand} {model}")
            report_lines.append(f"- **Status:** `Blocked`")
            report_lines.append(f"- **Reason / Hard Flags:** {flags}")
            report_lines.append(f"- **Attempts Used:** {attempts} / 3")
            report_lines.append(f"- **Fix Log / Diagnostics:**")
            for entry in fix_log.split("\n"):
                if entry.strip():
                    report_lines.append(f"  - {entry.strip()}")
            report_lines.append("")
        report_lines.append("---")
        report_lines.append("")
    else:
        report_lines.append("## ✅ No Blocked Rows! No contradictory data detected.")
        report_lines.append("")

    # ==================== SKIPPED ROWS SECTION ====================
    if skipped_rows:
        report_lines.append("## ⏭️ SKIPPED ROWS (Data Exhausted / Insufficient Specs)")
        report_lines.append("The following products were skipped after exhausting all collection tiers (including Vision) and retry attempts. These rows do not stop the pipeline run.")
        report_lines.append("")
        for s_row in skipped_rows:
            pid = s_row.get("Product_ID")
            model = s_row.get("Model_Name")
            flags = s_row.get("Flags") or "All source tiers exhausted"
            attempts = s_row.get("Attempts", 0)
            fix_log = s_row.get("Fix_Log") or "No attempts logged"

            report_lines.append(f"### ⚪ [{pid}] {brand} {model}")
            report_lines.append(f"- **Status:** `Skipped`")
            report_lines.append(f"- **Reason / Flags:** {flags}")
            report_lines.append(f"- **Attempts Used:** {attempts} / 3")
            report_lines.append(f"- **What Was Tried & Fix Log:**")
            for entry in fix_log.split("\n"):
                if entry.strip():
                    report_lines.append(f"  - {entry.strip()}")
            report_lines.append("")
        report_lines.append("---")
        report_lines.append("")

    # ==================== DEFERRED ROWS SECTION ====================
    if deferred_rows:
        report_lines.append("## ⏳ DEFERRED ROWS (Infrastructure / Retry on Next Run)")
        report_lines.append("The following products could not complete processing due to temporary API or network limits. Collected specs and provenance are preserved, and these rows will be retried on the next run.")
        report_lines.append("")
        for d_row in deferred_rows:
            pid = d_row.get("Product_ID")
            model = d_row.get("Model_Name")
            flags = d_row.get("Flags") or "Deferred due to infrastructure failure"
            attempts = d_row.get("Attempts", 0)
            fix_log = d_row.get("Fix_Log") or "Deferred"

            report_lines.append(f"### 🟡 [{pid}] {brand} {model}")
            report_lines.append(f"- **Status:** `Deferred`")
            report_lines.append(f"- **Reason / Flags:** {flags}")
            report_lines.append(f"- **Attempts Used:** {attempts}")
            report_lines.append(f"- **Fix Log / Diagnostics:**")
            for entry in fix_log.split("\n"):
                if entry.strip():
                    report_lines.append(f"  - {entry.strip()}")
            report_lines.append("")
        report_lines.append("---")
        report_lines.append("")

    # ==================== ALL ROWS BREAKDOWN ====================
    report_lines.append("## 📋 Product Rows Breakdown")
    report_lines.append("")

    for row in brand_rows:
        pid = row.get("Product_ID")
        model = row.get("Model_Name")
        status = row.get("Status", "Pending")
        if status in ("Ready_For_Review", "Approved"):
            status_icon = "🟢"
        elif status == "Skipped":
            status_icon = "⚪"
        elif status == "Deferred":
            status_icon = "🟡"
        else:
            status_icon = "🔴"
        attempts = row.get("Attempts", 0)
        img_source = row.get("Image_Source", "None")
        img_tier = row.get("Image_Tier", "N/A")
        llm_provider = row.get("LLM_Provider") or "N/A"
        
        brand_slug = slugify(brand)
        model_slug = slugify(model)
        category_slug = slugify(str(row.get("Category") or category or "powerbank"))
        cat_img_path = f"images/{category_slug}/{brand_slug}/{model_slug}.png"
        if not is_empty_value(row.get("Override_Image_Path")):
            img_path = str(row.get("Override_Image_Path")).strip()
        elif os.path.exists(cat_img_path):
            img_path = cat_img_path
        else:
            img_path = f"images/{brand_slug}/{model_slug}.png"
        
        dims_str = "Missing"
        if os.path.exists(img_path) and os.path.isfile(img_path):
            try:
                with Image.open(img_path) as im:
                    dims_str = f"{im.width}x{im.height}px (format={im.format})"
            except Exception:
                dims_str = "Unreadable"

        report_lines.append(f"### {status_icon} [{pid}] {brand} {model}")
        report_lines.append(f"- **Status:** `{status}` | **Attempts:** {attempts} | **LLM Provider:** `{llm_provider}`")
        report_lines.append(f"- **Dealer Price (DP):** ₹{get_effective_value(row, 'MRP')}")
        report_lines.append(f"- **Image Source:** `{img_source}` (Tier {img_tier}) | **Dimensions:** `{dims_str}`")
        
        # Flags / Warnings
        flags = row.get("Flags")
        if not is_empty_value(flags):
            report_lines.append(f"- **Flags / Advisory:**")
            for fl in str(flags).split("\n"):
                if fl.strip():
                    report_lines.append(f"  - ⚠️ {fl.strip()}")

        # Field Provenance Table
        report_lines.append("")
        report_lines.append("| Field | Effective Value | Source URL / Provenance | Tier |")
        report_lines.append("|---|---|---|---|")
        
        fields_to_show = [
            ("Title", get_effective_value(row, "Title"), row.get("Source_Title") or row.get("Source_URL"), row.get("Tier_Title", 1)),
            ("Subtitle", get_effective_value(row, "Subtitle"), row.get("Source_Subtitle") or row.get("Source_URL"), row.get("Tier_Subtitle", 1)),
            ("Spec: Capacity", get_effective_value(row, "Spec_Capacity"), row.get("Source_Spec_Capacity") or row.get("Source_URL"), row.get("Tier_Spec_Capacity", 1)),
            ("Spec: Output", get_effective_value(row, "Spec_Output"), row.get("Source_Spec_Output") or row.get("Source_URL"), row.get("Tier_Spec_Output", 1)),
            ("Spec: Ports", get_effective_value(row, "Spec_Ports"), row.get("Source_Spec_Ports") or row.get("Source_URL"), row.get("Tier_Spec_Ports", 1)),
            ("Spec: Weight", get_effective_value(row, "Spec_Weight"), row.get("Source_Spec_Weight") or row.get("Source_URL"), row.get("Tier_Spec_Weight", 1)),
            ("Spec: Warranty", get_effective_value(row, "Spec_Warranty"), row.get("Source_Spec_Warranty") or row.get("Source_URL"), row.get("Tier_Spec_Warranty", 1)),
            ("Bullet 1", get_effective_value(row, "Bullet_1"), row.get("Source_Bullet_1") or row.get("Source_URL"), row.get("Tier_Bullet_1", 1)),
            ("Bullet 2", get_effective_value(row, "Bullet_2"), row.get("Source_Bullet_2") or row.get("Source_URL"), row.get("Tier_Bullet_2", 1)),
            ("Bullet 3", get_effective_value(row, "Bullet_3"), row.get("Source_Bullet_3") or row.get("Source_URL"), row.get("Tier_Bullet_3", 1)),
            ("Bullet 4", get_effective_value(row, "Bullet_4"), row.get("Source_Bullet_4") or row.get("Source_URL"), row.get("Tier_Bullet_4", 1)),
        ]

        for f_name, f_val, f_src, f_tier in fields_to_show:
            val_display = str(f_val).replace("|", "\\|") if not is_empty_value(f_val) else "*Empty*"
            src_display = str(f_src).replace("|", "\\|") if not is_empty_value(f_src) else "*None*"
            tier_display = str(f_tier) if not is_empty_value(f_tier) else "-"
            report_lines.append(f"| `{f_name}` | {val_display} | `{src_display}` | {tier_display} |")

        report_lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    logger.info(f"Generated run report: {report_path}")
    return report_path


def resolve_brand_url_collisions(
    df: pd.DataFrame,
    brand_name: str,
    config: dict,
    brand_defaults: dict,
    enable_semantic_audit: bool = False
) -> pd.DataFrame:
    """
    Rule 2 Collision Resolver:
    When two rows of the same brand resolve to the same Source_URL:
    1. Score both rows against the Source_URL using score_candidate_match.
    2. Keep the higher-scoring row (closer match).
    3. Clear the lower-scoring row and re-collect it with exclude_urls = {colliding_url}.
    4. Repeat until no duplicate Source_URLs exist within the brand.
    """
    from src.utils.scraper import score_candidate_match
    import importlib
    collect_mod = importlib.import_module("src.1_collect")

    brand_mask = df["Brand"].astype(str).str.strip().str.lower() == brand_name.strip().lower()
    
    max_passes = 5
    for pass_num in range(max_passes):
        url_map: Dict[str, List[int]] = {}
        for idx in df[brand_mask].index:
            src_url = df.loc[idx, "Source_URL"]
            status = df.loc[idx, "Status"]
            if not is_empty_value(src_url) and status not in ("Skipped", "Deferred"):
                clean_url = str(src_url).strip().rstrip("/").lower()
                url_map.setdefault(clean_url, []).append(idx)

        collisions = {u: idxs for u, idxs in url_map.items() if len(idxs) > 1}
        if not collisions:
            break

        logger.warning(f"[Collision Resolver] Found {len(collisions)} colliding URLs in brand '{brand_name}' (Pass {pass_num+1}): {list(collisions.keys())}")

        for coll_url, colliding_indices in collisions.items():
            scored_rows = []
            for idx in colliding_indices:
                r_dict = df.loc[idx].to_dict()
                m_name = r_dict.get("Model_Name", "")
                title = r_dict.get("Raw_Title") or r_dict.get("Override_Title") or m_name
                score, _, diag = score_candidate_match(m_name, title, coll_url, brand=brand_name)
                scored_rows.append((score, idx, r_dict))

            scored_rows.sort(key=lambda x: x[0], reverse=True)
            winner_score, winner_idx, winner_dict = scored_rows[0]
            logger.info(f"[Collision Resolver] Winner for '{coll_url}': [{winner_dict.get('Product_ID')}] '{winner_dict.get('Model_Name')}' (score={winner_score:.1f})")

            # Losers must be re-collected excluding coll_url
            for loser_score, loser_idx, loser_dict in scored_rows[1:]:
                loser_pid = loser_dict.get("Product_ID")
                loser_model = loser_dict.get("Model_Name")
                logger.info(f"[Collision Resolver] Re-collecting loser [{loser_pid}] '{loser_model}' (score={loser_score:.1f}) excluding '{coll_url}'...")
                
                # Clear raw fields for loser row
                raw_cols = [c for c in df.columns if c.startswith("Raw_") or c.startswith("Source_") or c.startswith("Tier_")]
                for rc in raw_cols:
                    df.at[loser_idx, rc] = None
                df.at[loser_idx, "Source_URL"] = None
                df.at[loser_idx, "Source_Audit"] = None
                df.at[loser_idx, "Image_URL"] = None
                df.at[loser_idx, "Image_Status"] = "missing"
                df.at[loser_idx, "Status"] = "Pending"
                df.at[loser_idx, "Attempts"] = 0
                df.at[loser_idx, "Flags"] = None

                # Re-run collection with exclude_urls
                all_rows = [row.to_dict() for _, row in df.iterrows()]
                cleared_row = df.loc[loser_idx].to_dict()
                
                collect_updates, success, c_log = collect_mod.collect_data_for_row(cleared_row, config, exclude_urls={coll_url})
                cleared_row.update(collect_updates)
                
                processed_loser = process_row_loop(
                    cleared_row, all_rows, config, brand_defaults, enable_semantic_audit=enable_semantic_audit, exclude_urls={coll_url}
                )
                for k, v in processed_loser.items():
                    df.at[loser_idx, k] = v

    return df


def clean_display_name(name: str, brand: str = "") -> str:
    """Returns the shortest clean Display_Name stripped of brand, SKU codes, capacities, and color suffixes."""
    cleaned = str(name or "").strip()
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}\s*', '', cleaned, flags=re.I).strip()
    # Strip SKU patterns
    cleaned = re.sub(r'\b(?:UPR|UPC|POR|PB|SC|PEB)[\w-]*\b', '', cleaned, flags=re.I).strip()
    # Strip trailing generic category words
    cleaned = re.sub(r'\s+(?:power\s*bank|powerbank|charger)\b', '', cleaned, flags=re.I).strip()
    # Strip capacity and wattage
    cleaned = re.sub(r'\s*\b(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b)\b', '', cleaned, flags=re.I).strip()
    # Strip trailing color words
    cleaned = re.sub(r'\s+(?:black|white|blue|grey|gray|green|pink|mocha|camo|silver)\b', '', cleaned, flags=re.I).strip()
    return cleaned or name


def execute_automatic_llm_post_run_review(
    df: pd.DataFrame,
    brand_name: str,
    config: dict,
    brand_defaults: dict,
    max_review_reruns: int = 2
) -> pd.DataFrame:
    """
    STEP 5.5: AUTOMATIC POST-RUN LLM REVIEW
    Reviews every Ready_For_Review row and inspects Skipped rows for potential recovery:
      1. Spec contradictions against subtitle/bullets (e.g. wattage/capacity mismatches).
      2. Model name capacity consistency.
      3. Copy mismatch (describing wrong product).
      4. Display_Name cleanliness (strips accidental brand prefixes, internal SKU codes, color suffixes).
      5. Recoverable Skipped rows.
    
    If review is not satisfied on a Ready_For_Review row:
      - Automatically triggers re-collection (max 2 review reruns per row).
      - Logs reason in Fix_Log.
    
    HARD INVARIANT:
      - The LLM reviewer CANNOT set Status = "Approved".
      - The LLM reviewer CANNOT clear any deterministic hard flags.
    """
    from src.utils.llm_client import audit_row_post_run
    
    brand_mask = df["Brand"].astype(str).str.strip().str.lower() == brand_name.strip().lower()
    brand_indices = df[brand_mask].index

    logger.info(f"[Step 5.5] Starting Automatic Post-Run LLM Review for brand '{brand_name}' ({len(brand_indices)} rows)...")

    for idx in brand_indices:
        row = df.loc[idx].to_dict()
        pid = row.get("Product_ID")
        status = row.get("Status")
        model_name = row.get("Model_Name")
        current_disp = row.get("Display_Name")

        # 1. Clean Display_Name if it carries brand prefixes, SKU codes, or color suffixes
        clean_disp = clean_display_name(current_disp or model_name, brand=brand_name)
        if clean_disp != current_disp and clean_disp:
            logger.info(f"[{pid}] Cleaned Display_Name from '{current_disp}' to '{clean_disp}'.")
            df.at[idx, "Display_Name"] = clean_disp
            row["Display_Name"] = clean_disp
            current_fix = str(row.get("Fix_Log") or "")
            df.at[idx, "Fix_Log"] = (current_fix + f"\nCleaned Display_Name to '{clean_disp}'").strip()

        # 2. Only perform semantic review on Ready_For_Review rows
        if status == "Ready_For_Review":
            payload = {
                "brand": brand_name,
                "product_id": pid,
                "model_name": model_name,
                "display_name": row.get("Display_Name"),
                "title": row.get("Raw_Title") or row.get("Override_Title"),
                "subtitle": row.get("Raw_Subtitle") or row.get("Override_Subtitle"),
                "bullets": [
                    row.get("Raw_Bullet_1") or row.get("Override_Bullet_1"),
                    row.get("Raw_Bullet_2") or row.get("Override_Bullet_2"),
                    row.get("Raw_Bullet_3") or row.get("Override_Bullet_3"),
                    row.get("Raw_Bullet_4") or row.get("Override_Bullet_4"),
                ],
                "specs": {
                    "capacity": row.get("Raw_Spec_Capacity") or row.get("Override_Spec_Capacity"),
                    "output": row.get("Raw_Spec_Output") or row.get("Override_Spec_Output"),
                    "ports": row.get("Raw_Spec_Ports") or row.get("Override_Spec_Ports"),
                    "weight": row.get("Raw_Spec_Weight") or row.get("Override_Spec_Weight"),
                    "warranty": row.get("Raw_Spec_Warranty") or row.get("Override_Spec_Warranty"),
                }
            }

            is_satisfied, contradictions, action = audit_row_post_run(payload, llm_config=config.get("llm", {}))

            if not is_satisfied and contradictions:
                logger.warning(f"[Step 5.5 Review Flag] [{pid}] '{model_name}' review not satisfied: {contradictions}")
                
                # Check review-triggered rerun count
                fix_log_text = str(row.get("Fix_Log") or "")
                review_reruns = fix_log_text.count("[Review Re-run]")

                if review_reruns < max_review_reruns:
                    logger.info(f"[{pid}] Triggering automatic review re-collection (attempt {review_reruns + 1}/{max_review_reruns})...")
                    
                    # Clear raw fields
                    raw_cols = [c for c in df.columns if c.startswith("Raw_") or c.startswith("Source_") or c.startswith("Tier_")]
                    for rc in raw_cols:
                        df.at[idx, rc] = None
                    df.at[idx, "Source_URL"] = None
                    df.at[idx, "Source_Audit"] = None
                    df.at[idx, "Image_URL"] = None
                    df.at[idx, "Image_Status"] = "missing"
                    df.at[idx, "Status"] = "Pending"
                    df.at[idx, "Flags"] = None

                    cleared_row = df.loc[idx].to_dict()
                    all_rows = [r.to_dict() for _, r in df.iterrows()]
                    
                    # Re-collect row excluding previously problematic source URL if applicable
                    prev_source = row.get("Source_URL")
                    exclude_urls = {prev_source} if prev_source and action == "recollect" else set()
                    
                    collect_updates, success, c_log = collect_mod.collect_data_for_row(cleared_row, config, exclude_urls=exclude_urls)
                    cleared_row.update(collect_updates)
                    
                    processed = process_row_loop(cleared_row, all_rows, config, brand_defaults, enable_semantic_audit=False, exclude_urls=exclude_urls)
                    
                    # Record review rerun in Fix_Log
                    new_fix_log = (str(processed.get("Fix_Log") or "") + f"\n[Review Re-run {review_reruns + 1}]: Re-collected due to review critique: {', '.join(contradictions)}").strip()
                    processed["Fix_Log"] = new_fix_log
                    
                    for k, v in processed.items():
                        df.at[idx, k] = v
                else:
                    logger.error(f"[{pid}] Exhausted max review re-runs ({max_review_reruns}). Blocking row.")
                    df.at[idx, "Status"] = "Blocked"
                    df.at[idx, "Flags"] = f"Blocked by Post-Run Review: {', '.join(contradictions)}"
            else:
                logger.info(f"[{pid}] Passed automatic post-run review.")

        elif status == "Skipped":
            logger.info(f"[{pid}] Skipped row confirmed unrecoverable.")

    return df


def format_final_presentation_table(df: pd.DataFrame, brand_name: str, category: str = "powerbank") -> str:
    """Formats the single final review table and prompts the two build questions."""
    brand_mask = df["Brand"].astype(str).str.strip().str.lower() == brand_name.strip().lower()
    brand_df = df[brand_mask]

    def _get_field(row, base_name):
        ov = row.get(f"Override_{base_name}")
        if not is_empty_value(ov):
            return str(ov).strip()
        rw = row.get(f"Raw_{base_name}")
        if not is_empty_value(rw):
            return str(rw).strip()
        return "-"

    lines = []
    lines.append(f"\n### 📊 Autonomous Review Table for Brand '{brand_name}'")
    lines.append("| Product_ID | Model_Name | Display_Name | Status | Capacity | Output | MRP (₹) | Image Status | Flags / Notes |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for _, r in brand_df.iterrows():
        pid = str(r.get("Product_ID") or "")
        model = str(r.get("Model_Name") or "")
        disp = str(r.get("Display_Name") or "")
        status = str(r.get("Status") or "")
        cap = _get_field(r, "Spec_Capacity")
        out = _get_field(r, "Spec_Output")
        
        mrp_ov = r.get("Override_MRP_Display")
        mrp_sc = r.get("Raw_MRP_Scraped")
        mrp_in = r.get("MRP_Input")
        mrp_val = mrp_ov if not is_empty_value(mrp_ov) else mrp_sc if not is_empty_value(mrp_sc) else mrp_in
        mrp_str = "-"
        if not is_empty_value(mrp_val):
            try:
                mrp_str = f"₹{int(float(mrp_val)):,}"
            except Exception:
                mrp_str = f"₹{mrp_val}"
                
        img_st = str(r.get("Image_Status") or "-")
        if is_empty_value(img_st):
            img_st = "-"
        flags_val = r.get("Flags")
        flags = str(flags_val).replace("\n", " ") if not is_empty_value(flags_val) else "Clean"
        if len(flags) > 40:
            flags = flags[:37] + "..."

        lines.append(f"| `{pid}` | {model} | **{disp}** | `{status}` | {cap} | {out} | {mrp_str} | `{img_st}` | {flags} |")

    category_slug = slugify(category)
    lines.append("\n---\n")
    lines.append("### 🚀 Ready for Human Sign-Off")
    lines.append("1. **Approve these rows?**")
    lines.append(f"2. **Standalone PDF in `dist/{category_slug}/{brand_name.lower()}/`, or append to the combined PDF and where in `brand_order`?**")

    return "\n".join(lines)


def run_brand(
    brand_name: str,
    config_path: str = "config.yaml",
    enable_semantic_audit: bool = False,
    return_summary: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Union[str, Dict[str, Any]]:
    """
    Main unattended orchestrator for a specific brand:
    multi-provider pre-flight check -> load -> collect -> images -> validate -> fix loop -> report.
    """
    start_time = time.time()
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    check_file_lock(excel_path)

    df = load_catalogue_data(excel_path)
    brand_mask = df["Brand"].astype(str).str.strip().str.lower() == brand_name.strip().lower()
    
    if not brand_mask.any():
        logger.error(f"No products found for brand '{brand_name}' in {excel_path}")
        raise ValueError(f"Brand '{brand_name}' not found in catalogue data.")

    # ==================== PRE-FLIGHT DUPLICATE DETECTION ====================
    brand_df = df[brand_mask]
    dup_mask = brand_df.duplicated(subset=["Model_Name", "MRP_Input"], keep=False)
    if dup_mask.any():
        dup_rows = brand_df[dup_mask]
        dup_groups = dup_rows.groupby(["Model_Name", "MRP_Input"])
        for (m_name, dp_val), grp in dup_groups:
            pids = grp["Product_ID"].tolist()
            logger.warning(
                f"[Pre-flight Duplicate Alert] Duplicate SKUs detected for Brand '{brand_name}': "
                f"Model '{m_name}' with DP ₹{dp_val} appears in multiple rows: {pids}."
            )

    # ==================== PRE-FLIGHT MULTI-PROVIDER QUOTA CHECK ====================
    brand_indices = df[brand_mask].index
    total_brand_rows = len(brand_indices)
    pending_count = sum(
        1 for idx in brand_indices
        if is_empty_value(df.loc[idx, "Raw_Title"]) and is_empty_value(df.loc[idx, "Override_Title"])
    )
    calls_per_row = 2 if enable_semantic_audit else 1
    estimated_calls = pending_count * calls_per_row

    logger.info(f"[Pre-flight] Brand '{brand_name}': {total_brand_rows} total products, {pending_count} pending collection.")
    logger.info(f"[Pre-flight] Estimated LLM calls needed: {estimated_calls} ({calls_per_row} per pending product).")

    if progress_callback:
        progress_callback({
            "stage": "preflight",
            "product_id": None,
            "current": 0,
            "total": total_brand_rows,
            "message": f"Pre-flight quota and duplicate check for {brand_name} ({pending_count} pending products)"
        })

    if pending_count > 0:
        try:
            preflight_quota_check(llm_config=config.get("llm", {}))
        except AllLLMProvidersExhaustedError as e:
            logger.error(f"[Pre-flight HALT] {e}")
            if not return_summary:
                print(f"\n[HALT] {e}\n")
                return ""
            return {
                "brand": brand_name,
                "report_path": "",
                "runtime": round(time.time() - start_time, 2),
                "status_counts": {},
                "rows": [],
                "table_markdown": "",
                "halt_reason": str(e)
            }

    logger.info(f"Starting autonomous pipeline run for brand '{brand_name}' ({brand_mask.sum()} products, semantic audit={enable_semantic_audit})...")
    brand_defaults = load_brand_defaults(brand_name)
    all_rows = [row.to_dict() for _, row in df.iterrows()]

    for i, idx in enumerate(brand_indices, 1):
        row_dict = df.loc[idx].to_dict()
        pid = row_dict.get("Product_ID")
        model = row_dict.get("Model_Name")
        if progress_callback:
            progress_callback({
                "stage": "row_start",
                "product_id": pid,
                "current": i,
                "total": total_brand_rows,
                "message": f"Processing product {i}/{total_brand_rows}: {model} ({pid})"
            })
        try:
            processed_row = process_row_loop(
                row_dict, all_rows, config, brand_defaults,
                enable_semantic_audit=enable_semantic_audit,
                progress_callback=progress_callback,
                current_index=i,
                total_count=total_brand_rows
            )
            for k, v in processed_row.items():
                df.at[idx, k] = v
            if progress_callback:
                progress_callback({
                    "stage": "row_done",
                    "product_id": pid,
                    "current": i,
                    "total": total_brand_rows,
                    "message": f"Finished product {i}/{total_brand_rows}: {pid} -> {processed_row.get('Status')}"
                })
        except AllLLMProvidersExhaustedError as e:
            logger.error(f"[Pipeline HALT] {e}")
            if not return_summary:
                print(f"\n[HALT] {e}\n")
                return ""
            return {
                "brand": brand_name,
                "report_path": "",
                "runtime": round(time.time() - start_time, 2),
                "status_counts": {},
                "rows": [],
                "table_markdown": "",
                "halt_reason": str(e)
            }

    # Resolve any URL collisions across the brand (Rule 2)
    if progress_callback:
        progress_callback({
            "stage": "collision_check",
            "product_id": None,
            "current": total_brand_rows,
            "total": total_brand_rows,
            "message": "Resolving URL collisions within brand"
        })
    df = resolve_brand_url_collisions(df, brand_name, config, brand_defaults, enable_semantic_audit=enable_semantic_audit)

    # ==================== STEP 5.5: AUTOMATIC POST-RUN LLM REVIEW ====================
    if progress_callback:
        progress_callback({
            "stage": "post_review",
            "product_id": None,
            "current": total_brand_rows,
            "total": total_brand_rows,
            "message": "Executing automatic post-run LLM review"
        })
    df = execute_automatic_llm_post_run_review(df, brand_name, config, brand_defaults)

    save_catalogue_data(df, excel_path)
    
    category_name = config.get("category", {}).get("name", "powerbank")
    brand_rows = [df.loc[idx].to_dict() for idx in df[brand_mask].index]
    report_path = generate_run_report(
        brand_name, brand_rows, output_dir=config.get("paths", {}).get("output_dir", "dist"), category=category_name
    )
    
    ready_count = sum(1 for r in brand_rows if r.get("Status") == "Ready_For_Review")
    approved_count = sum(1 for r in brand_rows if r.get("Status") == "Approved")
    blocked_count = sum(1 for r in brand_rows if r.get("Status") == "Blocked")
    skipped_count = sum(1 for r in brand_rows if r.get("Status") == "Skipped")
    deferred_count = sum(1 for r in brand_rows if r.get("Status") == "Deferred")

    logger.info(f"Brand run complete for '{brand_name}': {ready_count} Ready for Review | {approved_count} Approved | {blocked_count} Blocked | {skipped_count} Skipped | {deferred_count} Deferred. Review report at: {report_path}")

    # ==================== STEP 5.5: FINAL PRESENTATION & QUESTION PROMPT ====================
    table_output = format_final_presentation_table(df, brand_name, category=category_name)
    
    if progress_callback:
        progress_callback({
            "stage": "complete",
            "product_id": None,
            "current": total_brand_rows,
            "total": total_brand_rows,
            "message": f"Brand run complete for '{brand_name}': {ready_count} Ready, {approved_count} Approved, {blocked_count} Blocked, {skipped_count} Skipped"
        })

    if not return_summary:
        print(table_output)
        return report_path

    per_row_results = []
    for r in brand_rows:
        tier_val = r.get("Tier_Title") or r.get("Tier_Spec_Capacity") or 1
        per_row_results.append({
            "Product_ID": r.get("Product_ID"),
            "Model_Name": r.get("Model_Name"),
            "Display_Name": r.get("Display_Name"),
            "Status": r.get("Status"),
            "Source_URL": r.get("Source_URL"),
            "Tier": tier_val,
            "Attempts": r.get("Attempts"),
            "Fix_Log": r.get("Fix_Log"),
            "Flags": r.get("Flags"),
        })

    status_counts = {
        "ready_count": ready_count,
        "approved_count": approved_count,
        "blocked_count": blocked_count,
        "skipped_count": skipped_count,
        "deferred_count": deferred_count,
        "total_count": len(brand_rows)
    }

    return {
        "brand": brand_name,
        "report_path": report_path,
        "runtime": round(time.time() - start_time, 2),
        "status_counts": status_counts,
        "rows": per_row_results,
        "table_markdown": table_output,
        "halt_reason": None
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Brand Pipeline Runner")
    parser.add_argument("--brand", required=True, help="Brand name to process (e.g. Stuffcool)")
    parser.add_argument("--config", default="config.yaml", help="Path to configuration YAML file")
    parser.add_argument("--semantic-audit", action="store_true", default=False, help="Enable optional LLM semantic audit pass")
    args = parser.parse_args()
    
    run_brand(args.brand, config_path=args.config, enable_semantic_audit=args.semantic_audit)
