import os
import re
import io
import json
import logging
from typing import Dict, List, Optional, Set, Any, Tuple
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from src.parsers.base import BaseParser, ParserResult
from src.utils.scraper import (
    extract_model_name_portion,
    normalize_model_tokens,
    score_candidate_match,
    reject_qualifier_mismatch
)
from src.utils.llm_client import extract_specs_via_vision, VisionExtractedSpecsSchema
from src.utils.logger import setup_logger

logger = setup_logger("brochure_parser")


def find_brochure_pdfs(brand: str, brochure_override: Optional[str] = None) -> List[str]:
    """
    Finds all available brochure PDF paths for a given brand.
    Checks:
    1. Explicit brochure override path (from Brochure_PDF column)
    2. brochures/{brand_slug}/*.pdf and brochures/{brand}/*.pdf
    3. data/brochures/{brand_slug}.pdf and data/brochures/{brand}.pdf
    """
    found_paths = []
    seen_norm = set()
    brand_slug = brand.lower().replace(" ", "-")

    def _add_path(p: str):
        norm = os.path.normcase(os.path.abspath(p))
        if norm not in seen_norm and os.path.exists(p):
            seen_norm.add(norm)
            found_paths.append(p)

    if brochure_override:
        _add_path(brochure_override)

    # Check brochures/ directories
    for folder in [f"brochures/{brand_slug}", f"brochures/{brand}", f"brochures/{brand.lower()}"]:
        if os.path.exists(folder) and os.path.isdir(folder):
            for fname in os.listdir(folder):
                if fname.lower().endswith(".pdf"):
                    _add_path(os.path.join(folder, fname))

    # Check data/brochures/
    for folder in ["data/brochures", "data"]:
        for fname in [f"{brand_slug}.pdf", f"{brand.lower()}.pdf", f"{brand}.pdf"]:
            _add_path(os.path.join(folder, fname))

    return found_paths


def parse_specs_from_text(text: str) -> Dict[str, str]:
    """Extracts technical specifications from clean text block using deterministic heuristics."""
    specs = {}

    # Capacity
    cap_match = re.search(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:mAh|mah|MAH)", text)
    if cap_match:
        specs["capacity"] = f"{cap_match.group(1).replace(',', '')} mAh"

    # Output / Fast Charging
    out_match = re.search(r"(\d+(?:\.\d+)?\s*W(?:att)?|\d+W\s*(?:Fast Charging|Turbo|PD|QC|Wireless|MagSafe))", text, re.I)
    if out_match:
        specs["output"] = out_match.group(1).strip()

    # Ports
    port_parts = []
    if re.search(r"type[- ]?c|usb[- ]?c", text, re.I):
        port_parts.append("Type-C")
    if re.search(r"usb[- ]?a", text, re.I):
        port_parts.append("USB-A")
    if re.search(r"micro[- ]?usb", text, re.I):
        port_parts.append("Micro-USB")
    if re.search(r"lightning", text, re.I):
        port_parts.append("Lightning")
    if re.search(r"built[- ]in\s+cable", text, re.I):
        port_parts.append("Built-in Cable")
    if port_parts:
        specs["ports"] = ", ".join(dict.fromkeys(port_parts))

    # Weight
    weight_match = re.search(r"(\d+(?:\.\d+)?\s*(?:g|gm|grams|kg))\b", text, re.I)
    if weight_match:
        w_str = weight_match.group(1).strip()
        if not re.search(r"mah|w|v|a", w_str, re.I):
            specs["weight"] = w_str

    # Warranty
    warr_match = re.search(r"(\d+\s*(?:Months?|Years?)\s*(?:Warranty|Guarantee)?)", text, re.I)
    if warr_match:
        specs["warranty"] = warr_match.group(1).strip()

    return specs


def extract_brochure_page_vision(
    pdf_path: str,
    page_num: int,
    brand: str,
    model_name: str,
    config: dict
) -> Optional[Dict[str, str]]:
    """
    VISION FALLBACK FOR BROCHURE:
    Renders the PDF page to a high-resolution image via pypdfium2 and sends it to Gemini Vision.
    Isolates the target product region on multi-product pages.
    """
    doc = None
    try:
        doc = pdfium.PdfDocument(pdf_path)
        if page_num < 1 or page_num > len(doc):
            return None
        page = doc[page_num - 1]
        # Render at scale 2.5 for crisp text & spec extraction
        pil_img = page.render(scale=2.5).to_pil()

        img_byte_arr = io.BytesIO()
        pil_img.save(img_byte_arr, format="PNG")
        png_bytes = img_byte_arr.getvalue()

        image_parts = [(png_bytes, "image/png")]
        page_source = f"brochure: {os.path.basename(pdf_path)}, page {page_num}"

        logger.info(f"[Tier 0 Vision] Rendering PDF page {page_num} of {os.path.basename(pdf_path)} for '{brand} {model_name}'...")
        return extract_specs_via_vision(
            image_bytes_list=image_parts,
            page_url=page_source,
            brand=brand,
            model_name=model_name,
            llm_config=config.get("llm", {})
        )
    except Exception as e:
        logger.warning(f"[Tier 0 Vision] Failed to render/extract brochure page {page_num}: {e}")
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def parse_brochure_for_model(
    brand: str,
    model_name: str,
    qualifier_tokens: List[str],
    config: dict,
    brochure_override: Optional[str] = None
) -> Optional[ParserResult]:
    """
    TIER 0: Brand Brochure PDF Extractor.
    1. Searches brochure PDFs for target product by Model_Name.
    2. Extracts text layer specs first.
    3. If text layer is thin / image-based, falls back to Vision Extraction on the rendered PDF page.
    4. Handles multi-product pages by isolating the target model region.
    5. Returns ParserResult with Tier 0 / Tier 0-vision provenance, or None if no brochure exists.
    """
    pdf_paths = find_brochure_pdfs(brand, brochure_override=brochure_override)
    if not pdf_paths:
        logger.debug(f"[Tier 0] No brochure PDFs found for brand '{brand}'. Silently skipping Tier 0.")
        return None

    model_portion = extract_model_name_portion(model_name)
    target_tokens = normalize_model_tokens(model_portion)

    for pdf_path in pdf_paths:
        pdf_filename = os.path.basename(pdf_path)
        logger.info(f"[Tier 0] Scanning brochure '{pdf_filename}' for '{brand} {model_name}'...")

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                for page_idx, page in enumerate(pdf.pages):
                    page_num = page_idx + 1
                    raw_text = page.extract_text() or ""
                    is_text_page = len(raw_text.strip()) > 30

                    if is_text_page:
                        # Check if model tokens appear on this page
                        page_tokens = normalize_model_tokens(raw_text)
                        if not target_tokens.issubset(page_tokens):
                            continue

                        # Validate qualifier tokens to prevent sibling mismatches
                        is_reject, reject_reason = reject_qualifier_mismatch(model_name, raw_text, qualifier_tokens)
                        if is_reject:
                            logger.warning(f"[Tier 0] Page {page_num} matched model but rejected due to qualifier conflict: {reject_reason}")
                            continue

                        logger.info(f"[Tier 0] Matched '{model_name}' in brochure '{pdf_filename}' on page {page_num}.")

                        # Multi-product handling: isolate section
                        model_section_text = raw_text
                        lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
                        model_line_idx = -1
                        for idx, line in enumerate(lines):
                            if target_tokens.issubset(normalize_model_tokens(line)):
                                model_line_idx = idx
                                break

                        if model_line_idx != -1:
                            section_lines = lines[model_line_idx:model_line_idx + 15]
                            model_section_text = "\n".join(section_lines)

                        text_specs = parse_specs_from_text(model_section_text)
                        source_label = f"brochure: {pdf_filename}, page {page_num}"

                        # If text layer provided >= 3 specs, use Text Tier 0
                        if len(text_specs) >= 3:
                            logger.info(f"[Tier 0] Text layer extraction successful on p.{page_num}: {text_specs}")
                            field_sources = {k: source_label for k in text_specs}
                            field_tiers = {k: 0 for k in text_specs}
                            return ParserResult(
                                success=True,
                                status_code=200,
                                url=source_label,
                                title=model_name,
                                description_text=model_section_text,
                                specs=text_specs,
                                field_sources=field_sources,
                                field_tiers=field_tiers,
                                tier=0
                            )

                        # If text layer was partial (< 3 specs), execute Vision Fallback on PDF page
                        logger.info(f"[Tier 0] Text layer yielded partial specs ({len(text_specs)}/5). Executing Tier 0 Vision Fallback...")
                        vision_specs = extract_brochure_page_vision(
                            pdf_path=pdf_path,
                            page_num=page_num,
                            brand=brand,
                            model_name=model_name,
                            config=config
                        )

                        combined_specs = dict(text_specs)
                        tier_label = 0
                        if vision_specs:
                            for k, v in vision_specs.items():
                                if v and k not in combined_specs:
                                    combined_specs[k] = v
                            tier_label = "0-vision"
                            logger.info(f"[Tier 0 Vision] Successfully extracted specs from brochure p.{page_num}: {combined_specs}")

                        if combined_specs:
                            field_sources = {k: source_label for k in combined_specs}
                            field_tiers = {k: tier_label for k in combined_specs}
                            return ParserResult(
                                success=True,
                                status_code=200,
                                url=source_label,
                                title=model_name,
                                description_text=model_section_text,
                                specs=combined_specs,
                                field_sources=field_sources,
                                field_tiers=field_tiers,
                                tier=tier_label
                            )
                    else:
                        # Scanned / Image-based PDF page (no text stream) -> Execute Vision Fallback directly
                        logger.info(f"[Tier 0] Scanned/image-based page {page_num} detected. Executing Tier 0 Vision Fallback...")
                        vision_specs = extract_brochure_page_vision(
                            pdf_path=pdf_path,
                            page_num=page_num,
                            brand=brand,
                            model_name=model_name,
                            config=config
                        )
                        if vision_specs:
                            source_label = f"brochure: {pdf_filename}, page {page_num}"
                            field_sources = {k: source_label for k in vision_specs}
                            field_tiers = {k: "0-vision" for k in vision_specs}
                            return ParserResult(
                                success=True,
                                status_code=200,
                                url=source_label,
                                title=model_name,
                                specs=vision_specs,
                                field_sources=field_sources,
                                field_tiers=field_tiers,
                                tier="0-vision"
                            )

        except Exception as e:
            logger.warning(f"[Tier 0] Error processing brochure '{pdf_filename}': {e}")
            continue

    return None
