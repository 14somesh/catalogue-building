import os
import re
import json
import time
from typing import Optional, Dict, Any, List, Tuple, Set
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from google import genai
from google.genai import types

from src.utils.logger import setup_logger

logger = setup_logger("llm_client")

load_dotenv()


class AllLLMProvidersExhaustedError(Exception):
    """Raised when every configured LLM provider in the fallback chain is exhausted. Halts the run."""
    pass


class GeminiDailyQuotaExhaustedError(Exception):
    """Raised when Gemini daily / per-day quota is exhausted."""
    pass


class GroqDailyQuotaExhaustedError(Exception):
    """Raised when Groq quota is exhausted."""
    pass


class GeminiRateLimitError(Exception):
    """Raised when a per-minute rate limit is hit and backoff retry is warranted."""
    pass


class GeminiTransientError(Exception):
    """Raised on 503, connection errors, timeouts, etc."""
    pass


# Global tracking of exhausted providers within the current process / run
EXHAUSTED_PROVIDERS: Set[str] = set()


def mark_provider_exhausted(provider_name: str) -> None:
    """Marks a provider as exhausted so subsequent rows failover immediately without latency."""
    EXHAUSTED_PROVIDERS.add(provider_name.lower())
    logger.warning(f"[LLM Chain] Provider '{provider_name}' marked as EXHAUSTED for the remainder of this run.")


def is_provider_exhausted(provider_name: str) -> bool:
    """Checks if a provider has been marked as exhausted."""
    return provider_name.lower() in EXHAUSTED_PROVIDERS


def reset_provider_states() -> None:
    """Resets provider exhaustion tracking (useful between runs / in tests)."""
    EXHAUSTED_PROVIDERS.clear()


class ProductCopySchema(BaseModel):
    title: str = Field(description="Crisp marketing product title (max 40 characters)")
    subtitle: str = Field(description="Engaging subtitle or key capability statement (max 80 characters)")
    bullet_1: str = Field(description="Sales bullet point 1 (MUST be <= 60 characters)")
    bullet_2: str = Field(description="Sales bullet point 2 (MUST be <= 60 characters)")
    bullet_3: str = Field(description="Sales bullet point 3 (MUST be <= 60 characters)")
    bullet_4: str = Field(description="Sales bullet point 4 (MUST be <= 60 characters)")


class SemanticAuditSchema(BaseModel):
    is_valid: bool = Field(description="True if product data is completely consistent and free of contradictions/hallucinations, False if issues exist")
    contradictions: List[str] = Field(default_factory=list, description="Contradictions between subtitle, bullets, and specs")
    factual_discrepancies: List[str] = Field(default_factory=list, description="Claims that contradict or cannot be substantiated by source text")
    tone_and_quality_issues: List[str] = Field(default_factory=list, description="Tone, grammar, or awkward phrasing issues")
    summary_flags: List[str] = Field(default_factory=list, description="Concise error/warning flags to record in the Excel Flags column")


def classify_gemini_error(e: Exception) -> str:
    """
    Classifies Gemini API exceptions into three distinct kinds:
    1. 'QUOTA_EXHAUSTED' -> Daily/per-project quota violation.
    2. 'RATE_LIMIT' -> 429 with short retryDelay / per-minute quota. Back off and retry.
    3. 'TRANSIENT' -> 503, timeout, connection reset, overloaded, unavailable. Back off and retry.
    4. 'OTHER' -> Non-recoverable client/auth errors.
    """
    err_str = str(e).lower()
    
    # 1. Daily Quota Check (Failover)
    daily_markers = [
        "generaterequestsperday",
        "perday",
        "per_day",
        "daily",
        "quota exceeded",
        "free_tier_daily",
        "generaterequestsperdayperprojectpermodel",
    ]
    if any(marker in err_str for marker in daily_markers):
        return "QUOTA_EXHAUSTED"

    # 2. Rate Limit (Per-Minute, recoverable via backoff)
    rate_limit_markers = [
        "generaterequestsperminute",
        "perminute",
        "per_minute",
        "rate limit",
        "ratelimit",
        "429",
        "resource_exhausted",
        "resourceexhausted"
    ]
    if any(marker in err_str for marker in rate_limit_markers):
        return "RATE_LIMIT"

    # 3. Transient errors (503, connection issues, timeouts)
    transient_markers = [
        "503", "500", "502", "504",
        "overloaded", "unavailable", "deadlineexceeded",
        "connectionerror", "connection reset", "broken pipe",
        "remotedisconnected", "timeout", "timed out"
    ]
    if any(marker in err_str for marker in transient_markers):
        return "TRANSIENT"

    return "OTHER"


def classify_groq_error(e: Exception) -> str:
    """
    Classifies Groq API exceptions:
    1. 'QUOTA_EXHAUSTED' -> Daily/monthly requests or tokens limit exceeded.
    2. 'RATE_LIMIT' -> Per-minute TPM/RPM limit hit. Back off and retry.
    3. 'TRANSIENT' -> 503, 500, 502, 504, timeout, connection reset.
    4. 'OTHER' -> Auth / client errors.
    """
    err_str = str(e).lower()

    # 1. Daily Quota Check (Failover)
    daily_markers = [
        "daily_limit",
        "daily limit",
        "requests per day",
        "rpd",
        "tokens per day",
        "tpd",
        "quota_exceeded",
        "quota exceeded",
        "insufficient_quota",
        "plan limit",
    ]
    if any(marker in err_str for marker in daily_markers):
        return "QUOTA_EXHAUSTED"

    # 2. Rate limit (TPM/RPM per-minute)
    rate_limit_markers = [
        "rate_limit_exceeded",
        "rate limit",
        "ratelimit",
        "429",
        "tpm",
        "rpm",
        "requests per minute",
        "tokens per minute"
    ]
    if any(marker in err_str for marker in rate_limit_markers):
        return "RATE_LIMIT"

    # 3. Transient errors
    transient_markers = [
        "503", "500", "502", "504",
        "service unavailable", "gateway timeout",
        "connectionerror", "connection reset", "timeout", "timed out"
    ]
    if any(marker in err_str for marker in transient_markers):
        return "TRANSIENT"

    return "OTHER"


def get_gemini_client() -> genai.Client:
    """Initializes and returns the Google GenAI client."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment or .env file.")
    return genai.Client(api_key=api_key)


def get_groq_client():
    """Initializes and returns the Groq client."""
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment or .env file.")
    return Groq(api_key=api_key)


def get_providers_from_config(llm_config: Optional[dict] = None) -> List[Dict[str, Any]]:
    """
    Resolves the list of configured LLM providers in fallback chain order.
    Defaults to: Gemini -> Groq.
    """
    cfg = llm_config or {}
    providers = cfg.get("providers")
    if providers and isinstance(providers, list):
        return providers

    # Legacy or fallback single provider format
    model = cfg.get("model", "gemini-3.6-flash")
    temp = cfg.get("temperature", 0.2)
    return [
        {"name": "gemini", "model": model, "temperature": temp},
        {"name": "groq", "model": "llama-3.3-70b-versatile", "temperature": temp}
    ]


DANGLING_STOPWORDS = {
    "to", "in", "on", "at", "for", "with", "and", "or", "the", "a", "an",
    "by", "of", "from", "up", "into", "as"
}

# Banned marketing adjectives that must never appear in technical catalogue bullets
BANNED_ADJECTIVES = [
    r"premium", r"amazing", r"ultimate", r"perfect", r"revolutionary",
    r"cutting-?edge", r"seamless(?:ly)?", r"incredible", r"exceptional",
    r"best", r"unbeatable", r"stunning", r"game-?changing",
    r"top-?notch", r"state-?of-?the-?art",
    r"superior", r"flawless", r"miraculous", r"breathtaking", r"unrivaled"
]

SHARED_SYSTEM_PROMPT = (
    "You are an expert technical product copywriter for a corporate gifting catalogue.\n"
    "Your single task is to write 4 concise sales bullet points and an engaging subtitle "
    "based strictly on the provided verified product specifications and description.\n\n"
    "STRICT COPYWRITING CONSTRAINTS:\n"
    "1. LEAD WITH A NUMBER OR SPEC: When a spec or number exists in the source text (e.g. 10000mAh, 20W PD, 185g, 3 ports), "
    "lead the bullet point directly with that number or specification.\n"
    "2. NO MARKETING ADJECTIVES: Never use subjective marketing adjectives (premium, amazing, ultimate, perfect, "
    "revolutionary, cutting-edge, seamless, incredible, exceptional, best, unbeatable, stunning).\n"
    "3. NEVER REPEAT BRAND NAME: Never include or repeat the brand name inside any bullet point or subtitle.\n"
    "4. CASING & PUNCTUATION: Write in clean sentence case. Every bullet point and subtitle MUST end with a terminal period ('.').\n"
    "5. EXACT LENGTH CONSTRAINT: Every bullet point MUST be between 40 and 60 characters total.\n"
    "6. SUBTITLE LIMIT: Subtitle must be at most 80 characters and end with a terminal period.\n"
    "7. ZERO HALLUCINATION: Only make claims directly verified by the provided text. Never invent specs or features.\n"
    "8. OUTPUT FORMAT: Return valid JSON strictly matching this schema:\n"
    "{\n"
    '  "title": "Clean model title <= 40 chars",\n'
    '  "subtitle": "Informative capability subtitle <= 80 chars ending with a period.",\n'
    '  "bullet_1": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_2": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_3": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_4": "Leading spec bullet point <= 60 chars."\n'
    "}"
)


def clean_and_normalize_text(
    text: str,
    brand: str,
    max_chars: int = 60,
    min_chars: int = 20
) -> Tuple[str, bool]:
    """
    Deterministic post-processor that runs on every bullet and subtitle regardless of provider:
    1. Strip brand name if present.
    2. Strip banned marketing adjectives.
    3. Collapse whitespace and strip dangling punctuation.
    4. Check if any banned adjective survived (reject if unfixable).
    5. Sentence case normalization.
    6. Trim to max_chars at a clean word boundary reserving room for terminal period.
    7. Strip dangling prepositions / conjunctions resulting from word-boundary truncation.
    8. Ensure terminal period.
    """
    if not text or not isinstance(text, str):
        return "", False

    cleaned = str(text).strip()

    # 1. Strip brand name if present (case-insensitive word boundary)
    if brand:
        cleaned = re.sub(rf'\b{re.escape(brand)}\b', '', cleaned, flags=re.I)

    # 2. Strip banned marketing adjectives
    for pattern in BANNED_ADJECTIVES:
        cleaned = re.sub(rf'\b{pattern}\b\s*', '', cleaned, flags=re.I)

    # 3. Collapse whitespace and strip leading/trailing non-alphanumeric punctuation
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'^[-–—:,.\s]+', '', cleaned).strip()

    if not cleaned:
        return "", False

    # 4. Check if any banned adjective survived
    for pattern in BANNED_ADJECTIVES:
        if re.search(rf'\b{pattern}\b', cleaned, re.I):
            return "", False

    # 5. Sentence case normalization: capitalize leading alphabetic word if it is not an acronym/unit
    if cleaned and cleaned[0].isalpha():
        cleaned = cleaned[0].upper() + cleaned[1:]

    # 6. Trim to max_chars at word boundary reserving 1 char for terminal period
    cleaned = cleaned.rstrip(". ,;:-")
    target_len = max_chars - 1
    if len(cleaned) > target_len:
        truncated = cleaned[:target_len]
        if " " in truncated:
            cleaned = truncated.rsplit(" ", 1)[0].rstrip(". ,;:-")
        else:
            cleaned = truncated.rstrip(". ,;:-")

    # 7. Strip dangling stop words (e.g. 'up to', 'for', 'in', 'on')
    words = cleaned.split()
    while words and words[-1].lower() in DANGLING_STOPWORDS:
        words.pop()
    cleaned = " ".join(words).rstrip(". ,;:-")

    if not cleaned:
        return "", False

    # 8. Ensure terminal period
    cleaned = cleaned.rstrip(". ") + "."

    # 9. Length guarantee
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars - 1].rsplit(" ", 1)[0].rstrip(". ,;:-") + "."

    is_valid = len(cleaned) >= min_chars and len(cleaned) <= max_chars
    return cleaned, is_valid


def post_process_copy_payload(raw_copy: Dict[str, Any], brand: str) -> Tuple[Dict[str, Any], bool]:
    """
    Applies deterministic post-processing across all fields of the LLM copy payload.
    Ensures identical shape and constraints regardless of LLM provider.
    """
    processed = dict(raw_copy)
    all_valid = True

    # 1. Clean Title
    title = str(raw_copy.get("title", "")).strip()
    title = re.sub(r'\s+', ' ', title)
    if len(title) > 40:
        title = title[:40].rsplit(" ", 1)[0].rstrip(" ,;:-")
    processed["title"] = title

    # 2. Clean Subtitle (max 80 chars)
    sub_raw = str(raw_copy.get("subtitle", "")).strip()
    sub_clean, sub_valid = clean_and_normalize_text(sub_raw, brand, max_chars=80, min_chars=15)
    if not sub_valid and sub_clean:
        sub_valid = True
    processed["subtitle"] = sub_clean
    if not sub_valid:
        all_valid = False

    # 3. Clean Bullets 1 to 4 (max 60 chars)
    for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
        b_raw = str(raw_copy.get(b_key, "")).strip()
        b_clean, b_valid = clean_and_normalize_text(b_raw, brand, max_chars=60, min_chars=20)
        processed[b_key] = b_clean
        if not b_valid:
            all_valid = False

    return processed, all_valid


def _build_copy_prompts(brand: str, model_name: str, product_description_block: str, specs: Dict[str, str]) -> Tuple[str, str]:
    """Builds identical structured copy system and user prompts across all providers."""
    user_prompt = (
        f"Brand: {brand}\n"
        f"Model: {model_name}\n"
        f"Verified Specs: {json.dumps(specs)}\n\n"
        f"--- VERIFIED PRODUCT DESCRIPTION BLOCK ---\n"
        f"{product_description_block[:1500]}\n"
        f"------------------------------------------\n\n"
        "Draft the title, subtitle, and 4 sales bullets according to the strict copywriting constraints in JSON format."
    )
    return SHARED_SYSTEM_PROMPT, user_prompt


def _draft_copy_gemini(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "gemini-3.6-flash",
    temperature: float = 0.2,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Executes copy drafting on Gemini with deterministic post-processing."""
    client = get_gemini_client()
    system_prompt, user_prompt = _build_copy_prompts(brand, model_name, product_description_block, specs)
    logger.info(f"Calling Gemini ({model}) to draft copy for {brand} {model_name}...")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[system_prompt, user_prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ProductCopySchema,
                    temperature=temperature
                )
            )
            parsed_data = json.loads(response.text)
            processed_data, is_valid = post_process_copy_payload(parsed_data, brand)
            if is_valid or attempt == max_retries:
                return processed_data
            logger.warning(f"[Gemini] Copy validation failed on attempt {attempt}/{max_retries}. Re-requesting...")

        except Exception as e:
            last_error = e
            err_type = classify_gemini_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                raise GeminiDailyQuotaExhaustedError(str(e))
            elif err_type == "RATE_LIMIT" and attempt < max_retries:
                backoff_sec = 10 + (attempt * 5)
                logger.warning(f"[Gemini] Rate limit on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            elif err_type == "TRANSIENT" and attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.warning(f"[Gemini] Transient error on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                logger.error(f"[Gemini] Error on attempt {attempt}: {e}")
                break

    raise RuntimeError(f"Gemini drafting failed after {max_retries} attempts: {last_error}")


def _draft_copy_groq(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "llama-3.3-70b-versatile",
    temperature: float = 0.2,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Executes copy drafting on Groq with deterministic post-processing."""
    client = get_groq_client()
    system_prompt, user_prompt = _build_copy_prompts(brand, model_name, product_description_block, specs)
    logger.info(f"Calling Groq ({model}) to draft copy for {brand} {model_name}...")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=temperature
            )
            raw_content = completion.choices[0].message.content
            parsed_data = json.loads(raw_content)
            processed_data, is_valid = post_process_copy_payload(parsed_data, brand)
            if is_valid or attempt == max_retries:
                return processed_data
            logger.warning(f"[Groq] Copy validation failed on attempt {attempt}/{max_retries}. Re-requesting...")

        except Exception as e:
            last_error = e
            err_type = classify_groq_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                raise GroqDailyQuotaExhaustedError(str(e))
            elif err_type == "RATE_LIMIT" and attempt < max_retries:
                backoff_sec = 5 + (attempt * 3)
                logger.warning(f"[Groq] Rate limit on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            elif err_type == "TRANSIENT" and attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.warning(f"[Groq] Transient error on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                logger.error(f"[Groq] Error on attempt {attempt}: {e}")
                break

    raise RuntimeError(f"Groq drafting failed after {max_retries} attempts: {last_error}")


def draft_bullets_and_subtitle(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    llm_config: Optional[dict] = None,
    max_retries: int = 3
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    MULTI-PROVIDER FALLBACK CHAIN:
    Tries providers in configured order (Gemini -> Groq).
    If a provider hits QUOTA_EXHAUSTED, marks that provider unavailable for the rest of the run
    and immediately fails over to the next provider in the chain.
    Only halts if EVERY provider in the chain is exhausted.
    Returns: (copy_data_dict, provider_name).
    """
    providers = get_providers_from_config(llm_config)
    available_providers = [p for p in providers if not is_provider_exhausted(p.get("name", ""))]

    if not available_providers:
        msg = "All LLM providers exhausted. Run halted."
        logger.error(msg)
        raise AllLLMProvidersExhaustedError(msg)

    for prov in available_providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")
        p_temp = prov.get("temperature", 0.2)

        try:
            if p_name == "gemini":
                data = _draft_copy_gemini(
                    brand=brand,
                    model_name=model_name,
                    product_description_block=product_description_block,
                    specs=specs,
                    model=p_model or "gemini-3.6-flash",
                    temperature=p_temp,
                    max_retries=max_retries
                )
                logger.info(f"Successfully drafted copy for {brand} {model_name} via Gemini ({p_model}).")
                return data, "gemini"

            elif p_name == "groq":
                data = _draft_copy_groq(
                    brand=brand,
                    model_name=model_name,
                    product_description_block=product_description_block,
                    specs=specs,
                    model=p_model or "llama-3.3-70b-versatile",
                    temperature=p_temp,
                    max_retries=max_retries
                )
                logger.info(f"Successfully drafted copy for {brand} {model_name} via Groq ({p_model}).")
                return data, "groq"

            else:
                logger.warning(f"Unknown LLM provider '{p_name}'. Skipping...")
                continue

        except (GeminiDailyQuotaExhaustedError, GroqDailyQuotaExhaustedError) as qe:
            mark_provider_exhausted(p_name)
            logger.warning(f"[LLM Chain] Provider '{p_name}' quota exhausted ({qe}). Failing over to next provider...")
            continue
        except Exception as ex:
            logger.warning(f"[LLM Chain] Provider '{p_name}' failed ({ex}). Failing over to next provider...")
            continue

    # If all configured providers were attempted and exhausted
    msg = "All LLM providers exhausted. Run halted."
    logger.error(msg)
    raise AllLLMProvidersExhaustedError(msg)


def preflight_quota_check(llm_config: Optional[dict] = None) -> Tuple[bool, List[str]]:
    """
    PRE-FLIGHT MULTI-PROVIDER QUOTA PROBE:
    Tests every configured provider in the chain and reports availability before processing starts.
    If all configured providers are unavailable, halts with AllLLMProvidersExhaustedError.
    """
    providers = get_providers_from_config(llm_config)
    available_providers = []

    logger.info("=" * 60)
    logger.info("[Pre-flight] Testing LLM Provider Fallback Chain Availability...")
    logger.info("=" * 60)

    for prov in providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")

        if p_name == "gemini":
            try:
                client = get_gemini_client()
                client.models.generate_content(
                    model=p_model or "gemini-3.6-flash",
                    contents="ping",
                    config=types.GenerateContentConfig(max_output_tokens=5, temperature=0.0)
                )
                logger.info(f"  ✅ [Gemini] Model '{p_model or 'gemini-3.6-flash'}': AVAILABLE")
                available_providers.append("gemini")
            except Exception as e:
                err_type = classify_gemini_error(e)
                if err_type == "QUOTA_EXHAUSTED":
                    mark_provider_exhausted("gemini")
                    logger.warning(f"  ❌ [Gemini] Model '{p_model}': DAILY QUOTA EXHAUSTED")
                else:
                    mark_provider_exhausted("gemini")
                    logger.warning(f"  ❌ [Gemini] Model '{p_model}': UNAVAILABLE ({e})")

        elif p_name == "groq":
            try:
                client = get_groq_client()
                client.chat.completions.create(
                    model=p_model or "llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                    temperature=0.0
                )
                logger.info(f"  ✅ [Groq] Model '{p_model or 'llama-3.3-70b-versatile'}': AVAILABLE")
                available_providers.append("groq")
            except Exception as e:
                err_type = classify_groq_error(e)
                if err_type == "QUOTA_EXHAUSTED":
                    mark_provider_exhausted("groq")
                    logger.warning(f"  ❌ [Groq] Model '{p_model}': QUOTA EXHAUSTED")
                else:
                    mark_provider_exhausted("groq")
                    logger.warning(f"  ❌ [Groq] Model '{p_model}': UNAVAILABLE ({e})")

    if not available_providers:
        msg = "All LLM providers exhausted. Run halted. No rows were modified."
        logger.error(f"[Pre-flight HALT] {msg}")
        raise AllLLMProvidersExhaustedError(msg)

    logger.info(f"[Pre-flight] Active providers in fallback chain: {available_providers}")
    logger.info("=" * 60)
    return True, available_providers


def audit_product_semantics(
    product_payload: Dict[str, Any],
    source_text: Optional[str] = None,
    llm_config: Optional[dict] = None,
    max_retries: int = 3
) -> Tuple[List[str], bool]:
    """
    Performs optional LLM Semantic Audit on resolved product data across available providers.
    """
    providers = get_providers_from_config(llm_config)
    available_providers = [p for p in providers if not is_provider_exhausted(p.get("name", ""))]

    if not available_providers:
        return [], False

    system_prompt = (
        "You are an AI Quality Assurance Inspector for a corporate gifting catalogue. "
        "Your task is to audit the resolved product data for factual consistency and quality."
    )
    product_summary_str = json.dumps(product_payload, indent=2)
    user_prompt = f"Product Data Under Review:\n{product_summary_str}\n\n"
    if source_text:
        user_prompt += f"Original Scraped Source Text:\n{source_text[:1500]}\n\n"
    user_prompt += "Perform the semantic audit and return the results according to SemanticAuditSchema."

    for prov in available_providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")

        try:
            if p_name == "gemini":
                client = get_gemini_client()
                response = client.models.generate_content(
                    model=p_model or "gemini-3.6-flash",
                    contents=[system_prompt, user_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SemanticAuditSchema,
                        temperature=0.1
                    )
                )
                audit_result = json.loads(response.text)
                flags = audit_result.get("summary_flags", [])
                for item in audit_result.get("contradictions", []):
                    if item not in flags:
                        flags.append(f"Semantic contradiction: {item}")
                for item in audit_result.get("factual_discrepancies", []):
                    if item not in flags:
                        flags.append(f"Factual discrepancy: {item}")
                is_clean = audit_result.get("is_valid", False) and len(flags) == 0
                return flags, is_clean

            elif p_name == "groq":
                client = get_groq_client()
                completion = client.chat.completions.create(
                    model=p_model or "llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt + "\nReturn JSON conforming to SemanticAuditSchema (is_valid, contradictions, factual_discrepancies, summary_flags)."},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                audit_result = json.loads(completion.choices[0].message.content)
                flags = audit_result.get("summary_flags", [])
                is_clean = audit_result.get("is_valid", False) and len(flags) == 0
                return flags, is_clean

        except Exception as e:
            logger.warning(f"Semantic audit error with {p_name}: {e}")
            continue

    return [], False

