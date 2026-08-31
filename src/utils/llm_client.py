import os
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


def _build_copy_prompts(brand: str, model_name: str, product_description_block: str, specs: Dict[str, str]) -> Tuple[str, str]:
    """Builds identical structured copy system and user prompts across all providers."""
    system_prompt = (
        "You are an expert technical product copywriter for a corporate gifting catalogue. "
        "Your single task is to write 4 concise, high-impact sales bullet points and an engaging subtitle "
        "based strictly on the provided verified product description and specifications.\n\n"
        "STRICT CONSTRAINTS:\n"
        "1. ZERO HALLUCINATION: Only make claims directly supported by the provided text. Never invent features.\n"
        "2. BULLET LENGTH LIMIT: Every bullet point (bullet_1 to bullet_4) MUST BE AT MOST 60 CHARACTERS.\n"
        "3. SUBTITLE LIMIT: Subtitle must be at most 80 characters.\n"
        "4. NO GENERIC BOILERPLATE: Avoid phrases like 'free shipping', 'leading brand', 'reliable and durable', 'homegrown'.\n"
        "5. OUTPUT FORMAT: Output valid JSON strictly conforming to this schema:\n"
        "{\n"
        '  "title": "Crisp title <= 40 chars",\n'
        '  "subtitle": "Engaging subtitle <= 80 chars",\n'
        '  "bullet_1": "Sales bullet <= 60 chars",\n'
        '  "bullet_2": "Sales bullet <= 60 chars",\n'
        '  "bullet_3": "Sales bullet <= 60 chars",\n'
        '  "bullet_4": "Sales bullet <= 60 chars"\n'
        "}"
    )

    user_prompt = (
        f"Brand: {brand}\n"
        f"Model: {model_name}\n"
        f"Verified Specs: {json.dumps(specs)}\n\n"
        f"--- VERIFIED PRODUCT DESCRIPTION BLOCK ---\n"
        f"{product_description_block[:1500]}\n"
        f"------------------------------------------\n\n"
        "Draft the title, subtitle, and 4 sales bullets in the required JSON schema."
    )
    return system_prompt, user_prompt


def _enforce_bullet_length(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """Enforces the strict <= 60 characters constraint on all 4 bullets via word-boundary trimming."""
    for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
        val = str(parsed_data.get(b_key, "")).strip()
        if len(val) > 60:
            logger.warning(f"{b_key} exceeded 60 chars ({len(val)} chars). Trimming...")
            parsed_data[b_key] = val[:60].rsplit(" ", 1)[0]
    return parsed_data


def _draft_copy_gemini(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "gemini-3.6-flash",
    temperature: float = 0.2,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Executes copy drafting on Gemini with retry on transient/rate-limit errors."""
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
            return _enforce_bullet_length(parsed_data)

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
    """Executes copy drafting on Groq with retry on transient/rate-limit errors."""
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
            return _enforce_bullet_length(parsed_data)

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

