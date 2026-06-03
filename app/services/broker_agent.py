"""OpenAI Agents SDK integration for broker replies, tools, and preference extraction."""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from agents import Agent, AsyncOpenAI, ModelSettings, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import RateLimitError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.services.broker_tools import (
    BROKER_TOOLS,
    BrokerToolState,
    clear_broker_tool_state,
    infer_listing_type,
    merge_user_preferences,
    run_deterministic_tool,
    set_broker_tool_state,
)
set_tracing_disabled(True)

BROKER_INSTRUCTIONS = (
    "You are Nilesh, a real-estate broker assistant working across WhatsApp and web chat. "
    "Every user-facing reply must be natural, polished, commercially aware, and easy to act on. "
    "Never sound like a template or mention that you are using a model. "
    "If the conversation stage is initial, start by introducing yourself as Nilesh before anything else. "
    "Then briefly say you can help with research, finalizing a property, sharing property photos, "
    "drafting sample rent or buy/sell agreements in PDF format, registering legal agreements, "
    "and arranging walkthrough appointments for 3 to 5 selected properties. "
    "If search results are available in the initial conversation, still lead with the introduction "
    "and capabilities before mentioning any properties. "
    "When property ids are provided in context, preserve them exactly inside square brackets. "
    "When a file path is provided in context, preserve it exactly. "
    "When photo URLs are provided in context, mention that property photos are available. "
    "Never invent listing facts that are not present in the context. "
    "If the context includes a search result set, respond like an experienced broker who has screened the options already. "
    "For search results, do four things in this order: "
    "first, briefly restate the requirement in plain English; "
    "second, give a one-line take on the overall fit of the options; "
    "third, present the best options with crisp reasoning using the provided ids, price, location, "
    "and any snippet or budget-fit clues; "
    "fourth, end with one practical next step such as shortlisting or refining the search. "
    "Keep the tone warm and professional, and make the answer feel thoughtfully curated rather than dumped from a database."
)

TOOL_BROKER_INSTRUCTIONS = (
    BROKER_INSTRUCTIONS
    + " You have tools to perform real actions. "
    "Use search_properties when the user wants to find homes and you have location, budget, BHK, and rent/buy type "
    "(or ask for missing fields). "
    "Use shortlist_property when the user wants to save a property id from search results. "
    "Use schedule_visit when the user wants a walkthrough or appointment for 3 to 5 property ids. "
    "Always call the appropriate tool before confirming an action. "
    "After a tool runs, summarize the outcome naturally for the user."
)


@dataclass
class BrokerAgentResult:
    status: str
    reply: str
    media: list[dict] | None = None
    metadata: dict | None = None


class PropertyPreferences(BaseModel):
    location: str | None = None
    budget: float | None = None
    bhk: int | None = None
    type: Literal["rent", "buy"] | None = Field(default=None, description="rent or buy")


def is_agent_configured() -> bool:
    provider = settings.llm_provider.lower()
    if provider == "openrouter":
        return bool(settings.openrouter_api_key)
    if provider == "nvidia":
        return bool(settings.nvidia_api_key)
    return False


def _provider_credentials() -> tuple[str, str, str]:
    provider = settings.llm_provider.lower()
    if provider == "openrouter":
        return settings.openrouter_url, settings.openrouter_api_key, settings.openrouter_model
    if provider == "nvidia":
        return settings.nvidia_api_url, settings.nvidia_api_key, settings.nvidia_model
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")


def _chat_completions_base_url(url: str) -> str:
    normalized = url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized[: -len("/chat/completions")]
    return normalized


@lru_cache(maxsize=1)
def _chat_model() -> OpenAIChatCompletionsModel:
    api_url, api_key, model_name = _provider_credentials()
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=_chat_completions_base_url(api_url),
        timeout=30.0,
    )
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)


@lru_cache(maxsize=1)
def _preference_agent() -> Agent:
    return Agent(
        name="PreferenceExtractor",
        instructions=(
            "Extract real-estate search intent from the user message. "
            "Return structured fields only; omit unknown values."
        ),
        model=_chat_model(),
        output_type=PropertyPreferences,
        model_settings=ModelSettings(temperature=0),
    )


@lru_cache(maxsize=1)
def _reply_agent() -> Agent:
    return Agent(
        name="NileshBroker",
        instructions=BROKER_INSTRUCTIONS,
        model=_chat_model(),
        model_settings=ModelSettings(temperature=0.4),
    )


def _tool_broker_agent() -> Agent:
    return Agent(
        name="NileshBrokerWithTools",
        instructions=TOOL_BROKER_INSTRUCTIONS,
        model=_chat_model(),
        tools=BROKER_TOOLS,
        model_settings=ModelSettings(temperature=0.4),
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    message = str(exc).lower()
    return "429" in message or "rate limit" in message


def _run_reply_agent(user_input: str, *, required: bool = False) -> str | None:
    if not is_agent_configured():
        if required:
            raise RuntimeError(f"{settings.llm_provider.title()} API key is required for assistant replies.")
        return None

    max_retries = 3
    retry_delay = 1.0
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            result = Runner.run_sync(_reply_agent(), user_input, max_turns=3)
            content = (result.final_output or "").strip()
            if not content:
                raise RuntimeError("Agent returned an empty response.")
            return content
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_error(exc) and attempt < max_retries - 1:
                time.sleep(retry_delay * (2**attempt))
                continue
            if required:
                if _is_rate_limit_error(exc):
                    raise RuntimeError("Rate limited after 3 attempts. Please try again later.") from exc
                raise
            return None

    if required and last_error:
        raise last_error
    return None


def _run_preference_agent(message: str) -> PropertyPreferences | None:
    if not is_agent_configured():
        return None

    max_retries = 3
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            result = Runner.run_sync(
                _preference_agent(),
                f"Message: {message}",
                max_turns=2,
            )
            output = result.final_output
            if isinstance(output, PropertyPreferences):
                return output
            if output is not None:
                return PropertyPreferences.model_validate(output)
            return None
        except Exception as exc:
            if _is_rate_limit_error(exc) and attempt < max_retries - 1:
                time.sleep(retry_delay * (2**attempt))
                continue
            return None

    return None


def extract_preferences_with_agent(message: str) -> dict:
    parsed = _run_preference_agent(message)
    if not parsed:
        return {}

    raw = parsed.model_dump(exclude_none=True)
    allowed = {"location", "budget", "bhk", "type"}
    cleaned = {k: v for k, v in raw.items() if k in allowed and v not in ("", None, [])}
    if "type" in cleaned and cleaned["type"] not in {"rent", "buy"}:
        cleaned.pop("type")
    return cleaned


def generate_reply_with_agent(message: str, context: str, *, is_initial: bool = False) -> str:
    stage = "initial" if is_initial else "ongoing"
    user_input = (
        f"Conversation stage: {stage}\n"
        f"User message: {message}\n"
        f"Context:\n{context}\n"
        "Write the reply now. Use short paragraphs or compact bullets when presenting multiple properties."
    )
    return _run_reply_agent(user_input, required=True) or ""


def _run_tool_broker_agent(user_input: str) -> str:
    max_retries = 3
    retry_delay = 1.0
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            result = Runner.run_sync(_tool_broker_agent(), user_input, max_turns=8)
            content = (result.final_output or "").strip()
            if not content:
                raise RuntimeError("Agent returned an empty response.")
            return content
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_error(exc) and attempt < max_retries - 1:
                time.sleep(retry_delay * (2**attempt))
                continue
            raise

    if last_error:
        raise last_error
    return ""


def _finalize_from_tool_state(
    message: str,
    history_context: str,
    state: BrokerToolState,
    *,
    is_initial: bool,
) -> BrokerAgentResult:
    if not state.tool_context:
        try:
            reply = generate_reply_with_agent(
                message,
                (
                    f"{history_context}\n\n"
                    "The user sent a property-related message but no tool applied. "
                    "Ask what they want: search, shortlist, or schedule a walkthrough."
                ),
                is_initial=is_initial,
            )
        except Exception:
            reply = "Tell me your location, budget, BHK, and rent or buy preference, or say shortlist or schedule."
        return BrokerAgentResult(status="clarification", reply=reply.strip())

    try:
        reply = generate_reply_with_agent(
            message,
            f"{history_context}\n\n{state.tool_context}",
            is_initial=is_initial,
        )
    except Exception:
        reply = state.tool_context

    return BrokerAgentResult(
        status=state.response_status,
        reply=reply.strip(),
        media=state.media or None,
        metadata=state.metadata or None,
    )


def run_broker_agent(
    db: Session,
    user: User,
    message: str,
    *,
    is_initial: bool,
    history_context: str,
) -> BrokerAgentResult:
    llm_prefs = extract_preferences_with_agent(message)
    preferences = merge_user_preferences(db, user, message, llm_prefs)
    had_explicit_type = bool(preferences.get("type"))
    inferred = infer_listing_type(message, preferences)
    assumption_note = ""
    if inferred and not had_explicit_type:
        preferences["type"] = inferred
        assumption_note = (
            f"\n\nAssumption used for this search: interpreted the request as a {inferred} search "
            "because the user gave a BHK and budget but did not explicitly say rent or buy."
        )
    user.preferences = preferences
    db.commit()

    state = BrokerToolState(
        db=db,
        user=user,
        preferences=preferences,
        is_initial=is_initial,
        assumption_note=assumption_note,
    )
    set_broker_tool_state(state)

    try:
        if is_agent_configured():
            stage = "initial" if is_initial else "ongoing"
            user_input = (
                f"Conversation stage: {stage}\n"
                f"User message: {message}\n"
                f"Saved preferences: {preferences}\n"
                f"History:\n{history_context}\n"
                "Choose and call the correct tool when needed, then reply to the user."
            )
            try:
                reply = _run_tool_broker_agent(user_input)
                status = state.response_status if state.last_tool else "clarification"
                return BrokerAgentResult(
                    status=status,
                    reply=reply,
                    media=state.media or None,
                    metadata=state.metadata or None,
                )
            except Exception:
                run_deterministic_tool(message, preferences)
                return _finalize_from_tool_state(
                    message,
                    history_context,
                    state,
                    is_initial=is_initial,
                )

        run_deterministic_tool(message, preferences)
        return _finalize_from_tool_state(
            message,
            history_context,
            state,
            is_initial=is_initial,
        )
    finally:
        clear_broker_tool_state()
