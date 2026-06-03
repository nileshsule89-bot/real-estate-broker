"""LLM facade backed by the OpenAI Agents SDK (OpenRouter / NVIDIA compatible APIs)."""

from app.services.broker_agent import (
    extract_preferences_with_agent,
    generate_reply_with_agent,
    is_agent_configured,
)


def is_llm_configured() -> bool:
    return is_agent_configured()


def extract_preferences_with_llm(message: str) -> dict:
    return extract_preferences_with_agent(message)


def generate_reply_with_llm(message: str, context: str, *, is_initial: bool = False) -> str:
    return generate_reply_with_agent(message, context, is_initial=is_initial)


def generate_clarification_with_llm(message: str, missing_fields: list[str], *, is_initial: bool = False) -> str:
    fields_text = ", ".join(missing_fields)
    return generate_reply_with_agent(
        message,
        (
            "The user has not given enough details to search properties yet. "
            f"Missing search inputs: {fields_text}. "
            "Ask one short, friendly question that gathers the missing details."
        ),
        is_initial=is_initial,
    )
