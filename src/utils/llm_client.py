import os
import json
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.utils.logger import setup_logger

logger = setup_logger("llm_client")

load_dotenv()


class ProductExtractionSchema(BaseModel):
    title: str = Field(description="Short, crisp marketing product title (max 40 characters)")
    subtitle: str = Field(description="Engaging subtitle or key capability statement (max 80 characters)")
    mrp_scraped: Optional[float] = Field(description="Scraped official MRP in INR if found in text, else null")
    spec_capacity: str = Field(description="Battery capacity e.g. '10,000 mAh', '20,000 mAh'")
    spec_output: str = Field(description="Charging output e.g. '22.5W Fast Charging', '65W Power Delivery'")
    spec_ports: str = Field(description="Ports description e.g. '2 x USB-A, 1 x Type-C (PD)'")
    spec_weight: str = Field(description="Weight e.g. '225g', '380g' (estimate if omitted)")
    spec_warranty: str = Field(description="Warranty e.g. '1 Year Manufacturer Warranty'")
    bullet_1: str = Field(description="Sales bullet point 1 (MUST be <= 60 characters)")
    bullet_2: str = Field(description="Sales bullet point 2 (MUST be <= 60 characters)")
    bullet_3: str = Field(description="Sales bullet point 3 (MUST be <= 60 characters)")
    bullet_4: str = Field(description="Sales bullet point 4 (MUST be <= 60 characters)")
    image_url: Optional[str] = Field(description="Direct URL to high-resolution product image if found")


class SemanticAuditSchema(BaseModel):
    is_valid: bool = Field(description="True if product data is completely consistent and free of contradictions/hallucinations, False if issues exist")
    contradictions: List[str] = Field(default_factory=list, description="Contradictions between subtitle, bullets, and specs (e.g. Subtitle claims 20000mAh while Spec_Capacity is 10000mAh)")
    factual_discrepancies: List[str] = Field(default_factory=list, description="Claims that contradict or cannot be substantiated by source text")
    tone_and_quality_issues: List[str] = Field(default_factory=list, description="Tone, grammar, or awkward phrasing issues")
    summary_flags: List[str] = Field(default_factory=list, description="Concise error/warning flags to record in the Excel Flags column")


def get_gemini_client() -> genai.Client:
    """Initializes and returns the Google GenAI client."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment or .env file.")
    return genai.Client(api_key=api_key)


def extract_product_data(brand: str, model_name: str, raw_content: str, model: str = "gemini-3.6-flash", temperature: float = 0.2) -> Optional[Dict[str, Any]]:
    """
    Uses Gemini LLM to parse raw web/brochure text into structured specifications and 4 bullet points.
    """
    client = get_gemini_client()

    system_prompt = (
        "You are an expert technical product copywriter for a corporate gifting catalogue. "
        "Your job is to extract verified technical specifications and create 4 punchy, high-converting bullet points "
        "for electronics products (specifically powerbanks and charging accessories).\n\n"
        "STRICT EXTRACTION & FIDELITY CONSTRAINTS:\n"
        "1. ZERO HALLUCINATION RULE: Every single spec and bullet point MUST be directly derived from and substantiated by the provided source text. Never assume, guess, or invent plausible features (e.g. do NOT claim digital displays, kickstands, wireless charging, or specific wattages unless explicitly stated in the source).\n"
        "2. If a specific spec (such as Weight or Warranty) is not stated in the source text, provide 'N/A' or the closest factual detail rather than guessing.\n"
        "3. Every single bullet point (bullet_1, bullet_2, bullet_3, bullet_4) MUST BE AT MOST 60 CHARACTERS LONG. Count characters carefully.\n"
        "4. Title must be concise and max 40 characters.\n"
        "5. Subtitle must be engaging and max 80 characters.\n"
        "6. Output valid JSON adhering exactly to the provided schema."
    )

    user_prompt = (
        f"Brand: {brand}\n"
        f"Model: {model_name}\n\n"
        f"--- RAW PRODUCT SOURCE CONTENT ---\n"
        f"{raw_content}\n"
        f"----------------------------------\n\n"
        "Extract the product specs and generate the 4 marketing bullet points (each <= 60 chars) according to the schema."
    )

    logger.info(f"Calling Gemini ({model}) for {brand} {model_name}...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=[system_prompt, user_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ProductExtractionSchema,
                temperature=temperature
            )
        )

        parsed_data = json.loads(response.text)
        
        # Post-validation check on bullet lengths
        for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
            if len(parsed_data.get(b_key, "")) > 60:
                logger.warning(f"{b_key} exceeded 60 chars ({len(parsed_data[b_key])} chars): '{parsed_data[b_key]}'. Truncating/trimming...")
                parsed_data[b_key] = parsed_data[b_key][:60].rsplit(" ", 1)[0]

        logger.info(f"Successfully extracted product data for {brand} {model_name}")
        return parsed_data
    except Exception as e:
        logger.error(f"Error calling Gemini API for {brand} {model_name}: {e}")
        return None


def audit_product_semantics(product_payload: Dict[str, Any], source_text: Optional[str] = None, model: str = "gemini-3.6-flash") -> Tuple[List[str], bool]:
    """
    Performs LLM Semantic Audit:
    1. Cross-checks Subtitle vs Specs (e.g. 20000mAh in subtitle vs 10000mAh in spec).
    2. Cross-checks Bullets vs Specs / Source Text for contradictions.
    3. Evaluates corporate gifting tone, clarity, and grammar.
    Returns (summary_flags_list, is_clean).
    """
    client = get_gemini_client()

    system_prompt = (
        "You are a strict Quality Assurance & Technical Auditor for an executive corporate gifting electronics catalogue.\n"
        "Your task is to conduct a semantic audit on a product's final copy and specifications.\n\n"
        "AUDIT RULES:\n"
        "1. CONTRADICTIONS: Check if the Subtitle contradicts any Specs (e.g., claiming 20000mAh in the subtitle but 10,000mAh in Spec_Capacity; or claiming 65W in subtitle but 20W in Spec_Output).\n"
        "2. INTERNAL CONSISTENCY: Check if any bullet point contradicts the specs or other bullets.\n"
        "3. SOURCE FIDELITY: If source text is provided, verify whether the claims in the title/subtitle/bullets are faithful to the source.\n"
        "4. TONE & GRAMMAR: Check for broken English, informal slang, placeholder text ('TBD', 'N/A', 'Lorem Ipsum'), or jarring inconsistencies.\n"
        "5. If everything is accurate, consistent, and well-written, return is_valid=True and empty summary_flags.\n"
        "6. If issues exist, return is_valid=False and list each issue concisely in summary_flags."
    )

    product_summary_str = json.dumps(product_payload, indent=2)
    user_prompt = f"Product Data Under Review:\n{product_summary_str}\n\n"
    if source_text:
        user_prompt += f"Original Scraped Source Text:\n{source_text[:1500]}\n\n"
    user_prompt += "Perform the semantic audit and return the results according to SemanticAuditSchema."

    logger.info(f"Running LLM semantic audit for {product_payload.get('brand')} {product_payload.get('model_name')}...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=[system_prompt, user_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SemanticAuditSchema,
                temperature=0.1
            )
        )

        audit_result = json.loads(response.text)
        flags = audit_result.get("summary_flags", [])
        
        # Merge any specific contradiction items if not already in summary_flags
        for item in audit_result.get("contradictions", []):
            if item not in flags:
                flags.append(f"Semantic contradiction: {item}")
        for item in audit_result.get("factual_discrepancies", []):
            if item not in flags:
                flags.append(f"Factual discrepancy: {item}")

        is_clean = audit_result.get("is_valid", False) and len(flags) == 0
        logger.info(f"Semantic audit result: clean={is_clean}, flags={flags}")
        return flags, is_clean
    except Exception as e:
        logger.error(f"Error during LLM semantic audit: {e}")
        return [f"Semantic audit error: {str(e)}"], False
