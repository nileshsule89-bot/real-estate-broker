import json
import time

import httpx

from app.config import settings


def is_llm_configured() -> bool:
    provider = settings.llm_provider.lower()
    if provider == "openrouter":
        return bool(settings.openrouter_api_key)
    if provider == "nvidia":
        return bool(settings.nvidia_api_key)
    return False


def _get_llm_config() -> tuple[str, str, str]:
    provider = settings.llm_provider.lower()
    if provider == "openrouter":
        return settings.openrouter_url, settings.openrouter_api_key, settings.openrouter_model
    if provider == "nvidia":
        return settings.nvidia_api_url, settings.nvidia_api_key, settings.nvidia_model
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")


def _chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    timeout: float,
    required: bool = False,
) -> str | None:
    if not is_llm_configured():
        if required:
            raise RuntimeError(f"{settings.llm_provider.title()} API key is required for assistant replies.")
        return None

    url, api_key, model = _get_llm_config()
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"].strip()
                if not content:
                    raise RuntimeError("LLM returned an empty response.")
                return content
        except httpx.HTTPStatusError as e:
            # Handle 429 (Too Many Requests) with exponential backoff
            if e.response.status_code == 429:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    time.sleep(wait_time)
                    continue
                # Final attempt failed, raise or return None
                if required:
                    raise RuntimeError(f"Rate limited after {max_retries} attempts. Please try again later.")
                return None
            # Other HTTP errors
            if required:
                raise
            return None
        except Exception:
            if required:
                raise
            return None

    return None


def extract_preferences_with_llm(message: str) -> dict:
    content = _chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "You extract real-estate intent into JSON. "
                    "Return only valid JSON with keys: location, budget, bhk, type. "
                    "type must be rent or buy. Omit unknown keys."
                ),
            },
            {"role": "user", "content": f"Message: {message}"},
        ],
        temperature=0,
        timeout=8,
    )
    if not content:
        return {}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}

    allowed = {"location", "budget", "bhk", "type"}
    cleaned = {k: v for k, v in parsed.items() if k in allowed and v not in ("", None, [])}
    if "type" in cleaned and cleaned["type"] not in {"rent", "buy"}:
        cleaned.pop("type")
    return cleaned


def generate_reply_with_llm(message: str, context: str, *, is_initial: bool = False) -> str:
    stage = "initial" if is_initial else "ongoing"
    return _chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "You are Nilesh, a real-estate broker assistant working across WhatsApp and web chat. "
                    "Every user-facing reply must be natural, polished, commercially aware, and easy to act on. "
                    "Never sound like a template or mention that you are using a model. "
                    "If this is the initial conversation, start by introducing yourself as Nilesh before anything else. "
                    "Then briefly say you can help with research, finalizing a property, sharing property photos, drafting sample rent or buy/sell agreements in PDF format, "
                    "registering legal agreements, and arranging walkthrough appointments for 3 to 5 selected properties. "
                    "If search results are available in the initial conversation, still lead with the introduction and capabilities before mentioning any properties. "
                    "When property ids are provided in context, preserve them exactly inside square brackets. "
                    "When a file path is provided in context, preserve it exactly. "
                    "When photo URLs are provided in context, mention that property photos are available. "
                    "Never invent listing facts that are not present in the context. "
                    "If the context includes a search result set, respond like an experienced broker who has screened the options already. "
                    "For search results, do four things in this order: "
                    "first, briefly restate the requirement in plain English; "
                    "second, give a one-line take on the overall fit of the options; "
                    "third, present the best options with crisp reasoning using the provided ids, price, location, and any snippet or budget-fit clues; "
                    "fourth, end with one practical next step such as shortlisting or refining the search. "
                    "Keep the tone warm and professional, and make the answer feel thoughtfully curated rather than dumped from a database."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Conversation stage: {stage}\n"
                    f"User message: {message}\n"
                    f"Context:\n{context}\n"
                    "Write the reply now. Use short paragraphs or compact bullets when presenting multiple properties."
                ),
            },
        ],
        temperature=0.4,
        timeout=12,
        required=True,
    ) or ""


def generate_clarification_with_llm(message: str, missing_fields: list[str], *, is_initial: bool = False) -> str:
    fields_text = ", ".join(missing_fields)
    return generate_reply_with_llm(
        message,
        (
            "The user has not given enough details to search properties yet. "
            f"Missing search inputs: {fields_text}. "
            "Ask one short, friendly question that gathers the missing details."
        ),
        is_initial=is_initial,
    )
