import os
import sys
import yaml
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional, Set

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    is_empty_value,
    get_effective_product_dict,
    RAW_FIELD_TO_SOURCE_MAP
)
from src.utils.validators import validate_row_deterministic
from src.utils.scraper import load_brand_defaults, is_boilerplate_bullet
from src.utils.llm_client import draft_bullets_and_subtitle, audit_product_semantics
import importlib
collect_mod = importlib.import_module("src.1_collect")
collect_data_for_row = collect_mod.collect_data_for_row

images_mod = importlib.import_module("src.2_images")
execute_image_tier_escalation = images_mod.execute_image_tier_escalation
resolve_product_image_path = images_mod.resolve_product_image_path
from src.utils.logger import setup_logger

logger = setup_logger("review_loop")

MAX_LOOP_ATTEMPTS = 3


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clear_raw_fields(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clears all Raw_ and Source_ fields on a blocked row.
    Guarantees zero invented data or unverified fields are stored.
    """
    cleared = dict(row_dict)
    for raw_col, source_col in RAW_FIELD_TO_SOURCE_MAP.items():
        cleared[raw_col] = None
        cleared[source_col] = None
        cleared[f"Tier_{raw_col.replace('Raw_', '')}"] = None
    cleared["Source_URL"] = None
    cleared["Source_Audit"] = None
    cleared["Image_URL"] = None
    return cleared


def attempt_auto_fix(
    row_dict: Dict[str, Any],
    hard_flags: List[str],
    config: dict,
    brand_defaults: dict,
    attempt_num: int
) -> Tuple[Dict[str, Any], bool, str]:
    """
    Evaluates hard flags and executes targeted auto-fix strategies:
    - sibling mismatch / capacity mismatch -> re-collect via collection page
    - boilerplate bullets -> discard bullets and redraft from clean specs
    - bullet over 60 chars -> deterministic word-boundary truncation (no extra LLM call)
    - missing warranty -> apply brand default warranty
    - image low-res -> escalate image tier
    Returns (updated_row_dict, fix_attempted, log_entry).
    """
    updated = dict(row_dict)
    pid = updated.get("Product_ID", "UNKNOWN")
    brand = updated.get("Brand", "")
    model_name = updated.get("Model_Name", "")

    # Auto-Fix 1: Bullet over 60 chars -> Deterministic word-boundary truncation (cuts LLM calls)
    if any("exceeds 60-character" in f for f in hard_flags):
        for b_idx in range(1, 5):
            b_val = str(updated.get(f"Raw_Bullet_{b_idx}") or "").strip()
            if len(b_val) > 60:
                truncated = b_val[:60].rsplit(" ", 1)[0] if " " in b_val[:60] else b_val[:60]
                updated[f"Raw_Bullet_{b_idx}"] = truncated
        log = f"Attempt {attempt_num}: Truncated bullets to satisfy <=60 char limit"
        return updated, True, log

    # Auto-Fix 2: Boilerplate or warranty bullets -> Discard invalid bullets without padding
    if any("Boilerplate detected" in f or "Disallowed warranty claim" in f for f in hard_flags):
        for b_idx in range(1, 5):
            b_val = str(updated.get(f"Raw_Bullet_{b_idx}") or "").strip()
            if b_val:
                is_bp = is_boilerplate_bullet(b_val)[0]
                is_warr = bool(re.search(r'\b(warrant|guarantee)\b', b_val, re.I))
                if is_bp or is_warr:
                    updated[f"Raw_Bullet_{b_idx}"] = None
                    updated[f"Source_Bullet_{b_idx}"] = None
                    updated[f"Tier_Bullet_{b_idx}"] = None
        log = f"Attempt {attempt_num}: Dropped invalid/warranty bullets (retaining verified bullets)"
        return updated, True, log

    # Auto-Fix 4: Sibling / Capacity Mismatch -> Re-search via collection page
    if any("mismatch" in f.lower() for f in hard_flags):
        # Force re-collection
        collect_updates, success, c_log = collect_mod.collect_data_for_row(updated, config)
        if success:
            updated.update(collect_updates)
            log = f"Attempt {attempt_num}: Re-collected via tier escalation: {c_log}"
        else:
            log = f"Attempt {attempt_num}: Re-collection attempted but tiers failed: {c_log}"
        return updated, True, log

    return updated, False, f"Attempt {attempt_num}: No automated fix available for flags: {hard_flags}"


def is_contradiction_flag(flag: str) -> bool:
    """
    Returns True if a hard flag represents an active contradiction or wrong product match
    that strictly requires human judgment and must be BLOCKED.
    Returns False if it is pure data exhaustion / insufficient specs, which should be SKIPPED.
    """
    f_lower = flag.lower()
    contradiction_keywords = [
        "qualifier token mismatch",
        "capacity mismatch",
        "duplicate image asset",
        "image filename mismatch",
        "fatal bug",
        "corrupted image"
    ]
    return any(ck in f_lower for ck in contradiction_keywords)


def process_row_loop(
    row_dict: Dict[str, Any],
    all_rows: List[Dict[str, Any]],
    config: dict,
    brand_defaults: dict,
    enable_semantic_audit: bool = False,
    exclude_urls: Optional[Set[str]] = None,
    progress_callback: Optional[Any] = None,
    current_index: int = 1,
    total_count: int = 1
) -> Dict[str, Any]:
    """
    Executes the autonomous loop for a single row:
    1_collect -> 2_images -> 3_validate -> auto-fix / retry (up to MAX_LOOP_ATTEMPTS).
    """
    current_row = dict(row_dict)
    pid = current_row.get("Product_ID", "Unknown")

    # If row is already Approved or Skipped, preserve it and do not re-process
    if current_row.get("Status") in ("Approved", "Skipped"):
        return current_row

    attempts_val = current_row.get("Attempts")
    try:
        initial_attempts = int(attempts_val) if not is_empty_value(attempts_val) else 0
    except Exception:
        initial_attempts = 0
    attempts = initial_attempts
    fix_logs: List[str] = [str(current_row.get("Fix_Log"))] if not is_empty_value(current_row.get("Fix_Log")) else []
    hard_flags: List[str] = [str(current_row.get("Flags"))] if not is_empty_value(current_row.get("Flags")) else ["Initial validation required"]

    while attempts < MAX_LOOP_ATTEMPTS:
        attempts += 1
        logger.info(f"[{pid}] Loop execution attempt {attempts}/{MAX_LOOP_ATTEMPTS}...")
        if progress_callback:
            progress_callback({
                "stage": "row_loop",
                "product_id": pid,
                "current": current_index,
                "total": total_count,
                "message": f"[{pid}] Loop attempt {attempts}/{MAX_LOOP_ATTEMPTS} started"
            })

        # Step 1: Collect (if not already collected)
        if is_empty_value(current_row.get("Raw_Title")) and is_empty_value(current_row.get("Override_Title")):
            if progress_callback:
                progress_callback({
                    "stage": "collect",
                    "product_id": pid,
                    "current": current_index,
                    "total": total_count,
                    "message": f"[{pid}] Executing tiered collection"
                })
            collect_updates, success, c_log = collect_mod.collect_data_for_row(current_row, config, exclude_urls=exclude_urls)
            current_row.update(collect_updates)
            fix_logs.append(f"Attempt {attempts}: {c_log}")

            # Check if collection was deferred due to infrastructure/LLM failure
            if current_row.get("Status") == "Deferred":
                logger.warning(f"[{pid}] Row deferred due to infrastructure failure during collection.")
                current_row["Attempts"] = initial_attempts
                current_row["Fix_Log"] = "\n".join(fix_logs)
                return current_row

            if not success:
                hard_flags = [str(collect_updates.get("Flags") or "All spec tiers exhausted")]
                break

        # Step 2: Image Resolution
        if progress_callback:
            progress_callback({
                "stage": "image",
                "product_id": pid,
                "current": current_index,
                "total": total_count,
                "message": f"[{pid}] Resolving and auditing image"
            })
        images_dir = config.get("paths", {}).get("images_dir", "images")
        img_path = resolve_product_image_path(current_row, images_dir=images_dir)
        img_url = current_row.get("Image_URL")
        img_url = str(img_url).strip() if not is_empty_value(img_url) else None

        prod_url = current_row.get("Source_URL") or current_row.get("Product_URL")
        prod_url = str(prod_url).strip() if not is_empty_value(prod_url) else None

        mp_url = current_row.get("Marketplace_URL")
        mp_url = str(mp_url).strip() if not is_empty_value(mp_url) else None

        img_status, img_source, img_tier = execute_image_tier_escalation(
            pid, current_row.get("Brand", ""), current_row.get("Model_Name", ""),
            img_url, mp_url, img_path, product_page_url=prod_url
        )
        current_row["Image_Status"] = img_status
        if img_source:
            current_row["Image_Source"] = img_source
        if img_tier:
            current_row["Image_Tier"] = img_tier

        # Step 3: Deterministic Validation (Hard & Warn checks)
        is_passed, hard_flags, warnings = validate_row_deterministic(current_row, all_rows, brand_defaults)
        if progress_callback:
            progress_callback({
                "stage": "validate",
                "product_id": pid,
                "current": current_index,
                "total": total_count,
                "message": f"[{pid}] Deterministic validation: passed={is_passed}"
            })

        if is_passed:
            # Step 4: LLM Semantic Advisory Pass (WARN-ONLY, optional behind flag, off by default)
            if enable_semantic_audit:
                prod_payload = get_effective_product_dict(current_row)
                sem_flags, _ = audit_product_semantics(prod_payload, llm_config=config.get("llm", {}))
                for sf in sem_flags:
                    warnings.append(f"[Advisory] {sf}")

            logger.info(f"[{pid}] Passed validation on attempt {attempts}.")
            current_row["Status"] = "Ready_For_Review"
            current_row["Flags"] = "\n".join(warnings) if warnings else None
            current_row["Attempts"] = attempts
            current_row["Fix_Log"] = "\n".join(fix_logs)
            return current_row

        # Step 5: Attempt Auto-Fix
        logger.warning(f"[{pid}] Validation failed with hard flags: {hard_flags}")
        if progress_callback:
            progress_callback({
                "stage": "auto_fix",
                "product_id": pid,
                "current": current_index,
                "total": total_count,
                "message": f"[{pid}] Attempting auto-fix for: {', '.join(hard_flags)[:60]}"
            })
        updated_row, fix_attempted, fix_log = attempt_auto_fix(
            current_row, hard_flags, config, brand_defaults, attempts
        )
        fix_logs.append(fix_log)
        current_row = updated_row

        if current_row.get("Status") == "Deferred":
            logger.warning(f"[{pid}] Row deferred during auto-fix.")
            current_row["Attempts"] = initial_attempts
            current_row["Fix_Log"] = "\n".join(fix_logs)
            return current_row

        if not fix_attempted:
            logger.warning(f"[{pid}] Unfixable hard flags encountered. Halting loop.")
            break

    # If row is marked Deferred, do NOT clear fields and do NOT block
    if current_row.get("Status") == "Deferred":
        logger.warning(f"[{pid}] Product row is Deferred (infrastructure). Preserving raw fields.")
        current_row["Attempts"] = initial_attempts
        current_row["Fix_Log"] = "\n".join(fix_logs)
        return current_row

    # Distinguish Contradiction (Blocked) vs Data Exhaustion (Skipped)
    has_contradiction = any(is_contradiction_flag(f) for f in hard_flags)

    if has_contradiction:
        logger.error(f"[{pid}] BLOCKED after {attempts} attempts due to contradiction / mismatch: {hard_flags}. Clearing raw fields.")
        blocked_row = clear_raw_fields(current_row)
        blocked_row["Status"] = "Blocked"
        blocked_row["Flags"] = "Hard Block: " + ", ".join(hard_flags)
        blocked_row["Attempts"] = attempts
        blocked_row["Fix_Log"] = "\n".join(fix_logs)
        return blocked_row
    else:
        logger.warning(f"[{pid}] SKIPPED after {attempts} attempts due to data exhaustion / insufficient specs: {hard_flags}.")
        skipped_row = clear_raw_fields(current_row)
        skipped_row["Status"] = "Skipped"
        skipped_row["Flags"] = "Skipped: " + ", ".join(hard_flags)
        skipped_row["Attempts"] = attempts
        skipped_row["Fix_Log"] = "\n".join(fix_logs)
        return skipped_row


def run_review_loop(
    config_path: str = "config.yaml",
    target_pids: Optional[list] = None,
    enable_semantic_audit: bool = False
) -> pd.DataFrame:
    """
    Main loop controller across catalogue dataset.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    check_file_lock(excel_path)
    
    df = load_catalogue_data(excel_path)
    logger.info(f"Running agent review loop on {len(df)} products (semantic audit={enable_semantic_audit})...")

    all_rows = [row.to_dict() for _, row in df.iterrows()]
    updated_rows = []

    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        pid = row_dict.get("Product_ID")
        brand = row_dict.get("Brand", "")
        cat_val = str(row_dict.get("Category", "")).strip() or None
        brand_defaults = load_brand_defaults(brand, category=cat_val)

        if target_pids and pid not in target_pids:
            updated_rows.append(row_dict)
            continue

        processed_row = process_row_loop(
            row_dict, all_rows, config, brand_defaults, enable_semantic_audit=enable_semantic_audit
        )
        updated_rows.append(processed_row)

    updated_df = pd.DataFrame(updated_rows)
    save_catalogue_data(updated_df, excel_path)
    logger.info(f"Review loop complete. Saved updated records to {excel_path}.")
    return updated_df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Loop Controller")
    parser.add_argument("--pids", nargs="+", help="Specific Product_IDs to process")
    parser.add_argument("--semantic-audit", action="store_true", default=False, help="Enable optional LLM semantic audit pass")
    args = parser.parse_args()
    
    run_review_loop(target_pids=args.pids, enable_semantic_audit=args.semantic_audit)
