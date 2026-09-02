import os
import re
import sys
import yaml
import json
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, is_empty_value, slugify
from src.utils.logger import setup_logger

logger = setup_logger("onboard")


class RawProductItem(BaseModel):
    raw_text: str = Field(description="Raw line or cell text describing the product")
    model_name: str = Field(description="Cleaned model name with capacity/specs (e.g. 'Mega 20000mAh')")
    display_name: str = Field(description="Shortest clean display name only (e.g. 'Mega', 'Roam Plus')")
    dp: Optional[float] = Field(None, description="Dealer price (DP) in INR")
    mrp: Optional[float] = Field(None, description="Maximum retail price (MRP) in INR")
    notes: Optional[str] = Field(None, description="Brief note on what was cleaned (e.g. 'Stripped color Black')")


class BrandInferenceSchema(BaseModel):
    brand_name: str = Field(description="Inferred brand name (e.g. 'Stuffcool', 'Pebble', 'Portronics', 'Anker')")
    brand_code: str = Field(description="2-4 character uppercase brand code for Product_ID (e.g. 'SC', 'PEB', 'POR', 'ANK')")
    domain: str = Field(description="Official brand website domain (e.g. 'anker.com', 'ambraneindia.com')")
    platform: str = Field(description="Website platform: 'shopify' or 'custom'")
    qualifier_tokens: List[Dict[str, str]] = Field(
        description="List of variant qualifier tokens appropriate for this brand with rationale. Tokens must be variant modifiers (Plus, Pro, Max, Mini) and NOT product model names.",
    )
    dp_column_explanation: str = Field(description="Explanation of how DP and MRP columns were identified")
    products: List[RawProductItem] = Field(description="List of extracted product rows")


def extract_text_from_file(file_path: str, llm_config: dict) -> Tuple[str, str]:
    """
    Detects file format and extracts text content.
    Returns (extracted_text, format_type).
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in [".xlsx", ".xls", ".csv"]:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
        csv_text = df.to_csv(index=False)
        return csv_text, "table"

    elif ext == ".pdf":
        import pdfplumber
        text_lines = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_lines.append(t)
        combined_text = "\n".join(text_lines)
        if len(combined_text.strip()) > 50:
            return combined_text, "pdf_text"
        else:
            # Fallback to rendering first few pages to Gemini Vision
            import pypdfium2 as pdfium
            import io
            doc = pdfium.PdfDocument(file_path)
            images = []
            for i in range(min(5, len(doc))):
                pil_img = doc[i].render(scale=2.0).to_pil()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                images.append((buf.getvalue(), "image/png"))
            doc.close()
            return _ocr_images_via_gemini(images, llm_config), "pdf_vision"

    elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
        with open(file_path, "rb") as f:
            img_bytes = f.read()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return _ocr_images_via_gemini([(img_bytes, mime)], llm_config), "image_vision"

    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read(), "text"

    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _ocr_images_via_gemini(image_parts: List[Tuple[bytes, str]], llm_config: dict) -> str:
    """Uses Gemini Vision to OCR price sheet images/pages."""
    try:
        from google import genai
        from google.genai import types
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=api_key)

        prompt = (
            "You are an expert tabular OCR engine. Extract all rows, headers, and product pricing information "
            "from the given dealer price sheet images. Preserve product codes, names, capacities, DP, and MRP. "
            "Output the result as a clean markdown table or structured CSV text."
        )

        contents = [prompt]
        for b_data, m_type in image_parts:
            contents.append(types.Part.from_bytes(data=b_data, mime_type=m_type))

        response = client.models.generate_content(
            model=llm_config.get("model", "gemini-3.6-flash"),
            contents=contents
        )
        return response.text or ""
    except Exception as e:
        logger.error(f"Error during Gemini OCR: {e}")
        return ""


def analyze_price_sheet(raw_content: str, category_name: str, llm_config: dict) -> BrandInferenceSchema:
    """
    Uses structured LLM output to parse raw sheet content and perform autonomous inference.
    """
    from google import genai
    from google.genai import types
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    prompt = f"""
Analyze this dealer price sheet for a brand in the '{category_name}' category.

Execute these tasks autonomously:
1. Infer the Brand Name (e.g. 'Stuffcool', 'Pebble', 'Portronics', 'Anker', 'Ambrane').
2. Assign a concise 2-4 uppercase brand code for Product_ID generation (e.g. 'SC', 'PEB', 'POR', 'ANK').
3. Find the brand's official consumer website domain (e.g. 'stuffcool.com', 'anker.com') and e-commerce platform ('shopify' or 'custom').
4. Disambiguate price columns:
   - If two prices exist: the lower price is Dealer Price (DP), the higher price is Maximum Retail Price (MRP).
   - If one price exists: it is Dealer Price (DP).
5. Extract product rows:
   - Strip internal SKU codes, brand prefixes, and color variant suffixes (e.g. 'Black', 'White') to produce `model_name`.
   - Generate `display_name`: the SHORTEST clean product title for the catalogue card (e.g. 'Mega', 'Major', 'Roam Plus', 'Power Shutter' — NOT 'Mega 20000mAh Powerbank').
6. Select Brand-Specific Qualifier Tokens:
   - Analyze the catalog's naming patterns to identify true variant/modifier suffixes (e.g. 'Plus', 'Pro', 'Max', 'Mini', 'Ultra', 'Lite', 'Go').
   - Provide a short rationale for each token explaining why it is a variant modifier.
   - DO NOT include actual product model names (e.g. 'Fuel', 'Boost', 'Nova', 'Electra', 'Giga', 'Major').

PRICE SHEET CONTENT:
{raw_content}
"""

    response = client.models.generate_content(
        model=llm_config.get("model", "gemini-3.6-flash"),
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BrandInferenceSchema,
            temperature=0.1
        )
    )

    result_json = json.loads(response.text)
    return BrandInferenceSchema(**result_json)


def generate_onboarding_summary(inference: BrandInferenceSchema) -> str:
    """Generates the formatted inference summary markdown table with platform & bot-block probe."""
    from src.parsers.generic import detect_ecommerce_platform
    platform_detected, platform_detail = detect_ecommerce_platform(inference.domain)

    lines = []
    lines.append(f"### 📋 Onboarding Inference Summary for Brand '{inference.brand_name}'")
    lines.append(f"- **Brand Inferred:** `{inference.brand_name}` (Brand Code: `{inference.brand_code}`)")
    lines.append(f"- **Price Mapping:** {inference.dp_column_explanation}")
    lines.append(f"- **Official Domain & Platform:** `{inference.domain}` (Inferred: `{inference.platform}`, Live Probe: `{platform_detected}` — {platform_detail})")
    
    if platform_detected == "custom":
        lines.append(f"- **Platform Status:** ⚠️ E-commerce platform is non-standard / custom. Generic JSON-LD + Sitemap scraper will be active.")
    if "403" in platform_detail or "error" in platform_detail.lower():
        lines.append(f"- **Bot Challenge Status:** 🚨 Brand domain is protected or blocking requests ({platform_detail}). Brand will fall straight through to Retail Tier 3.")

    lines.append(f"- **Selected Qualifier Tokens & Justification:**")
    for q in inference.qualifier_tokens:
        token_name = q.get("token") or list(q.keys())[0]
        rationale = q.get("rationale") or list(q.values())[0]
        lines.append(f"  - `{token_name}`: {rationale}")
    
    # Check duplicates
    seen = {}
    duplicates = []
    for idx, p in enumerate(inference.products, 1):
        key = (inference.brand_name.lower(), p.model_name.lower(), p.dp)
        if key in seen:
            duplicates.append(f"Row {idx} ('{p.model_name}') duplicates Row {seen[key]}")
        else:
            seen[key] = idx

    lines.append(f"- **Duplicates Identified:** {', '.join(duplicates) if duplicates else 'None'}\n")
    
    lines.append("| Product_ID | Model_Name | Display_Name | DP (₹) | MRP (₹) | Inferred Notes |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    
    for idx, p in enumerate(inference.products, 1):
        pid = f"PB-{inference.brand_code}-{idx:03d}"
        dp_str = f"{int(p.dp):,}" if p.dp is not None else "TBD"
        mrp_str = f"{int(p.mrp):,}" if p.mrp is not None else "TBD"
        notes_str = p.notes or "-"
        lines.append(f"| `{pid}` | {p.model_name} | **{p.display_name}** | ₹{dp_str} | ₹{mrp_str} | {notes_str} |")

    return "\n".join(lines)


def register_brand_config(inference: BrandInferenceSchema, config_path: str = "config/brand_defaults.yaml"):
    """Registers the new brand defaults in brand_defaults.yaml."""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            brand_cfg = yaml.safe_load(f) or {}
    else:
        brand_cfg = {}

    brand_slug = inference.brand_name.lower().replace(" ", "_")
    token_list = []
    for q in inference.qualifier_tokens:
        tok = q.get("token") if isinstance(q, dict) and "token" in q else (list(q.keys())[0] if isinstance(q, dict) else str(q))
        if tok not in token_list:
            token_list.append(tok)

    brand_cfg[brand_slug] = {
        "brand": inference.brand_name,
        "domain": inference.domain,
        "platform": inference.platform,
        "qualifier_tokens": token_list
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(brand_cfg, f, sort_keys=False, default_flow_style=False)
    logger.info(f"Registered '{inference.brand_name}' defaults in {config_path}")


def append_products_to_catalogue(
    inference: BrandInferenceSchema,
    excel_path: str = "data/catalogue_data.xlsx"
) -> Tuple[int, List[Dict[str, Any]]]:
    """Appends the newly onboarded product rows to the master Excel catalogue."""
    df = load_catalogue_data(excel_path)
    
    # Determine the starting sequence number using max + 1 across existing rows for this brand
    existing_brand_mask = df["Brand"].astype(str).str.lower() == inference.brand_name.lower()
    brand_df = df[existing_brand_mask]

    existing_seqs = []
    for raw_pid in brand_df["Product_ID"].dropna():
        match = re.search(r"(\d+)$", str(raw_pid).strip())
        if match:
            try:
                existing_seqs.append(int(match.group(1)))
            except ValueError:
                continue

    start_seq = max(existing_seqs) + 1 if existing_seqs else 1

    all_existing_pids = set(df["Product_ID"].dropna().astype(str).str.strip())

    new_rows = []
    for offset, p in enumerate(inference.products):
        seq = start_seq + offset
        pid = f"PB-{inference.brand_code}-{seq:03d}"
        
        # Guard: Fail-fast if generated Product_ID already exists anywhere in the sheet
        if pid in all_existing_pids:
            raise ValueError(f"Product_ID collision detected: '{pid}' already exists in {excel_path}")
        
        row_dict = {
            "Product_ID": pid,
            "Brand": inference.brand_name,
            "Model_Name": p.model_name,
            "Display_Name": p.display_name,
            "MRP_Input": p.dp,
            "MRP_Display": p.mrp,
            "Status": "Pending",
            "Attempts": 0
        }
        new_rows.append(row_dict)

    new_df = pd.DataFrame(new_rows)
    combined_df = pd.concat([df, new_df], ignore_index=True)
    save_catalogue_data(combined_df, excel_path)
    logger.info(f"Appended {len(new_rows)} rows for brand '{inference.brand_name}' to {excel_path}")
    return len(new_rows), new_rows
