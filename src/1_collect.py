import os
import sys
import yaml
import pandas as pd
from typing import Dict, Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    is_empty_value
)
from src.utils.scraper import scrape_web_page, extract_text_from_pdf, search_product_web
from src.utils.llm_client import extract_product_data
from src.utils.logger import setup_logger

logger = setup_logger("collect")


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_product_data_for_row(row_dict: Dict[str, Any], config: dict) -> Dict[str, Any]:
    """
    Collects raw product specs and marketing bullets for a single row following the Priority Hierarchy:
    1. Local Brochure PDF
    2. Manual Product URL override
    3. Manual Marketplace URL override
    4. Autonomous Web Search (Official brand site -> Marketplace fallback)
    """
    brand = str(row_dict.get("Brand", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    product_id = str(row_dict.get("Product_ID", f"{brand}_{model_name}")).strip()

    logger.info(f"Starting data collection for [{product_id}] {brand} - {model_name}...")

    raw_text = ""
    source_url_used = ""
    source_audit_note = ""
    discovered_image_url = None

    # Priority 1: Brochure PDF
    brochure_pdf = row_dict.get("Brochure_PDF")
    if not is_empty_value(brochure_pdf) and os.path.exists(str(brochure_pdf).strip()):
        pdf_path = str(brochure_pdf).strip()
        logger.info(f"[{product_id}] Priority 1: Using local brochure PDF at {pdf_path}")
        pdf_res = extract_text_from_pdf(pdf_path)
        if pdf_res.get("text"):
            raw_text = pdf_res["text"]
            source_audit_note = f"specs: local-brochure ({os.path.basename(pdf_path)})"

    # Priority 2: Manual Product URL Override
    if not raw_text:
        product_url = row_dict.get("Product_URL")
        if not is_empty_value(product_url) and str(product_url).strip().startswith("http"):
            url = str(product_url).strip()
            logger.info(f"[{product_id}] Priority 2: Using manual Product_URL: {url}")
            page_res = scrape_web_page(url)
            if page_res.get("text"):
                raw_text = page_res["text"]
                source_url_used = url
                discovered_image_url = page_res.get("image_url")
                source_audit_note = f"specs: manual-product-url ({url})"

    # Priority 3: Manual Marketplace URL Override
    if not raw_text:
        marketplace_url = row_dict.get("Marketplace_URL")
        if not is_empty_value(marketplace_url) and str(marketplace_url).strip().startswith("http"):
            url = str(marketplace_url).strip()
            logger.info(f"[{product_id}] Priority 3: Using manual Marketplace_URL: {url}")
            page_res = scrape_web_page(url)
            if page_res.get("text"):
                raw_text = page_res["text"]
                source_url_used = url
                discovered_image_url = page_res.get("image_url")
                source_audit_note = f"specs: manual-marketplace-url ({url})"

    # Priority 4: Autonomous Web Search
    if not raw_text:
        logger.info(f"[{product_id}] Priority 4: Executing autonomous web search for {brand} {model_name}...")
        discovered_url, source_type = search_product_web(brand, model_name, config)
        if discovered_url:
            logger.info(f"[{product_id}] Discovered URL via search: {discovered_url} ({source_type})")
            page_res = scrape_web_page(discovered_url)
            if page_res.get("text"):
                raw_text = page_res["text"]
                source_url_used = discovered_url
                discovered_image_url = page_res.get("image_url")
                source_audit_note = f"specs: {source_type} ({discovered_url})"
            else:
                logger.warning(f"[{product_id}] Failed to extract readable text from {discovered_url}")
        else:
            logger.warning(f"[{product_id}] Autonomous search found no valid product pages.")

    if not raw_text:
        logger.error(f"[{product_id}] No content could be gathered from brochure or web.")
        return {
            "success": False,
            "error": "No content found from any source"
        }

    # Extract structured product data via Gemini LLM
    llm_cfg = config.get("llm", {})
    llm_model = llm_cfg.get("model", "gemini-2.5-flash")
    llm_temp = llm_cfg.get("temperature", 0.2)

    extracted = extract_product_data(brand, model_name, raw_text, model=llm_model, temperature=llm_temp)
    if not extracted:
        return {
            "success": False,
            "error": "LLM extraction failed"
        }

    # Format Source_Audit note
    mrp_input_val = row_dict.get("MRP_Input")
    if not is_empty_value(mrp_input_val):
        final_source_audit = f"{source_audit_note} | mrp: input-sheet"
    else:
        final_source_audit = f"{source_audit_note} | mrp: scraped-unverified"

    return {
        "success": True,
        "source_url": source_url_used,
        "source_audit": final_source_audit,
        "image_url": discovered_image_url or extracted.get("image_url"),
        "raw_title": extracted.get("title", ""),
        "raw_subtitle": extracted.get("subtitle", ""),
        "raw_mrp_scraped": extracted.get("mrp_scraped"),
        "raw_spec_capacity": extracted.get("spec_capacity", ""),
        "raw_spec_output": extracted.get("spec_output", ""),
        "raw_spec_ports": extracted.get("spec_ports", ""),
        "raw_spec_weight": extracted.get("spec_weight", ""),
        "raw_spec_warranty": extracted.get("spec_warranty", ""),
        "raw_bullet_1": extracted.get("bullet_1", ""),
        "raw_bullet_2": extracted.get("bullet_2", ""),
        "raw_bullet_3": extracted.get("bullet_3", ""),
        "raw_bullet_4": extracted.get("bullet_4", ""),
    }


def run_collection(config_path: str = "config.yaml", force_all: bool = False) -> pd.DataFrame:
    """
    Main collector script runner: iterates through Excel sheet and collects specs for rows needing collection.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")

    check_file_lock(excel_path)
    df = load_catalogue_data(excel_path)
    logger.info(f"Running data collection on {len(df)} products in {excel_path}...")

    collected_count = 0
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        product_id = row_dict.get("Product_ID", f"Row_{idx+1}")
        status = str(row_dict.get("Status", "")).strip()

        # Check if collection is needed
        needs_collection = force_all or status in ["Pending", ""] or is_empty_value(row_dict.get("Raw_Title"))
        if not needs_collection:
            logger.info(f"[{product_id}] Skipping (already status '{status}')")
            continue

        res = collect_product_data_for_row(row_dict, config)
        if res.get("success"):
            df.at[idx, "Source_URL"] = res.get("source_url", "")
            df.at[idx, "Source_Audit"] = res.get("source_audit", "")
            if res.get("image_url") and is_empty_value(row_dict.get("Image_URL")):
                df.at[idx, "Image_URL"] = res.get("image_url", "")

            df.at[idx, "Raw_Title"] = res.get("raw_title", "")
            df.at[idx, "Raw_Subtitle"] = res.get("raw_subtitle", "")
            if res.get("raw_mrp_scraped") is not None:
                df.at[idx, "Raw_MRP_Scraped"] = res.get("raw_mrp_scraped")

            df.at[idx, "Raw_Spec_Capacity"] = res.get("raw_spec_capacity", "")
            df.at[idx, "Raw_Spec_Output"] = res.get("raw_spec_output", "")
            df.at[idx, "Raw_Spec_Ports"] = res.get("raw_spec_ports", "")
            df.at[idx, "Raw_Spec_Weight"] = res.get("raw_spec_weight", "")
            df.at[idx, "Raw_Spec_Warranty"] = res.get("raw_spec_warranty", "")
            df.at[idx, "Raw_Bullet_1"] = res.get("raw_bullet_1", "")
            df.at[idx, "Raw_Bullet_2"] = res.get("raw_bullet_2", "")
            df.at[idx, "Raw_Bullet_3"] = res.get("raw_bullet_3", "")
            df.at[idx, "Raw_Bullet_4"] = res.get("raw_bullet_4", "")
            df.at[idx, "Status"] = "Collected"
            collected_count += 1
            logger.info(f"[{product_id}] Successfully populated Raw_* specs and Source_URL.")
        else:
            logger.warning(f"[{product_id}] Collection failed: {res.get('error')}")

    save_catalogue_data(df, excel_path)
    logger.info(f"Data collection complete. Updated {collected_count} products.")
    return df


if __name__ == "__main__":
    run_collection()
