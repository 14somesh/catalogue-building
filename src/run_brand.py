import os
import sys
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
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
from src.utils.logger import setup_logger

logger = setup_logger("run_brand")


def generate_run_report(brand: str, brand_rows: list, output_dir: str = "dist") -> str:
    """
    Generates a comprehensive Markdown run report for human review:
    - Summary header: Ready / Blocked / Deferred / Warnings
    - Blocked and Deferred rows grouped with diagnostic fix logs
    - Full breakdown per product row with field sources, tiers, attempts, image metrics, and LLM Provider.
    """
    brand_slug = slugify(brand)
    brand_output_dir = os.path.join(output_dir, brand_slug) if brand_slug not in output_dir else output_dir
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
        img_path = str(row.get("Override_Image_Path") or f"images/{brand_slug}/{model_slug}.png")
        
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


def run_brand(brand_name: str, config_path: str = "config.yaml", enable_semantic_audit: bool = False) -> str:
    """
    Main unattended orchestrator for a specific brand:
    multi-provider pre-flight check -> load -> collect -> images -> validate -> fix loop -> report.
    """
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
    pending_count = sum(
        1 for idx in brand_indices
        if is_empty_value(df.loc[idx, "Raw_Title"]) and is_empty_value(df.loc[idx, "Override_Title"])
    )
    calls_per_row = 2 if enable_semantic_audit else 1
    estimated_calls = pending_count * calls_per_row

    logger.info(f"[Pre-flight] Brand '{brand_name}': {len(brand_indices)} total products, {pending_count} pending collection.")
    logger.info(f"[Pre-flight] Estimated LLM calls needed: {estimated_calls} ({calls_per_row} per pending product).")

    if pending_count > 0:
        try:
            preflight_quota_check(llm_config=config.get("llm", {}))
        except AllLLMProvidersExhaustedError as e:
            logger.error(f"[Pre-flight HALT] {e}")
            print(f"\n[HALT] {e}\n")
            return ""

    logger.info(f"Starting autonomous pipeline run for brand '{brand_name}' ({brand_mask.sum()} products, semantic audit={enable_semantic_audit})...")
    brand_defaults = load_brand_defaults(brand_name)
    all_rows = [row.to_dict() for _, row in df.iterrows()]

    for idx in df[brand_mask].index:
        row_dict = df.loc[idx].to_dict()
        try:
            processed_row = process_row_loop(
                row_dict, all_rows, config, brand_defaults, enable_semantic_audit=enable_semantic_audit
            )
            for k, v in processed_row.items():
                df.at[idx, k] = v
        except AllLLMProvidersExhaustedError as e:
            logger.error(f"[Pipeline HALT] {e}")
            print(f"\n[HALT] {e}\n")
            return ""

    # Resolve any URL collisions across the brand (Rule 2)
    df = resolve_brand_url_collisions(df, brand_name, config, brand_defaults, enable_semantic_audit=enable_semantic_audit)

    save_catalogue_data(df, excel_path)
    
    brand_rows = [df.loc[idx].to_dict() for idx in df[brand_mask].index]
    report_path = generate_run_report(brand_name, brand_rows)
    
    ready_count = sum(1 for r in brand_rows if r.get("Status") in ("Ready_For_Review", "Approved"))
    blocked_count = sum(1 for r in brand_rows if r.get("Status") == "Blocked")
    skipped_count = sum(1 for r in brand_rows if r.get("Status") == "Skipped")
    deferred_count = sum(1 for r in brand_rows if r.get("Status") == "Deferred")

    logger.info(f"Brand run complete for '{brand_name}': {ready_count} Ready for Review | {blocked_count} Blocked | {skipped_count} Skipped | {deferred_count} Deferred. Review report at: {report_path}")
    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Brand Pipeline Runner")
    parser.add_argument("--brand", required=True, help="Brand name to process (e.g. Stuffcool)")
    parser.add_argument("--config", default="config.yaml", help="Path to configuration YAML file")
    parser.add_argument("--semantic-audit", action="store_true", default=False, help="Enable optional LLM semantic audit pass")
    args = parser.parse_args()
    
    run_brand(args.brand, config_path=args.config, enable_semantic_audit=args.semantic_audit)
