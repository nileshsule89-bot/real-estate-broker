"""Broker tools: search (DB + web), shortlist, and schedule visit."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from agents import function_tool
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Shortlist, User
from app.schemas import ScheduleVisitInput, SearchPropertiesInput
from app.services.agent_assignment import assign_agent
from app.services.deal_service import get_or_create_deal
from app.services.memory_service import extract_preferences, has_minimum_requirements
from app.services.property_service import (
    get_properties_by_ids,
    resolve_property_id,
    search_properties as search_local_properties,
    shortlist_property as persist_shortlist,
)
from app.services.scheduler_service import schedule_visit
from app.services.web_property_service import fetch_property_photo, persist_web_properties, search_real_properties


@dataclass
class BrokerToolState:
    db: Session
    user: User
    preferences: dict
    is_initial: bool
    media: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    response_status: str = "ok"
    last_tool: str | None = None
    tool_context: str = ""
    assumption_note: str = ""


_broker_state: ContextVar[BrokerToolState | None] = ContextVar("_broker_state", default=None)


def set_broker_tool_state(state: BrokerToolState) -> None:
    _broker_state.set(state)


def get_broker_tool_state() -> BrokerToolState:
    state = _broker_state.get()
    if state is None:
        raise RuntimeError("Broker tool context is not set")
    return state


def clear_broker_tool_state() -> None:
    _broker_state.set(None)


def _budget_fit(price: float, budget: float) -> str:
    if budget <= 0:
        return "budget not available"
    ratio = price / budget
    if ratio <= 0.9:
        return "comfortably within budget"
    if ratio <= 1.0:
        return "right at budget"
    if ratio <= 1.1:
        return "slightly above budget"
    return "well above budget"


def _search_rationale(item: dict, preferences: dict) -> str:
    reasons: list[str] = []
    if item.get("location"):
        reasons.append(f"located in {item['location']}")
    if item.get("price"):
        reasons.append(_budget_fit(float(item["price"]), float(preferences.get("budget", 0) or 0)))
    if item.get("snippet"):
        reasons.append(item["snippet"])
    return "; ".join(reasons[:3])


def _property_media(properties) -> list[dict]:
    from app.services.photo_storage_service import public_url_for_object

    media: list[dict] = []
    for prop in properties:
        if not prop.metadata_json:
            continue
        image_url = prop.metadata_json.get("primary_cloud_photo_url")
        if not image_url and prop.metadata_json.get("primary_cloud_photo_path"):
            image_url = public_url_for_object(prop.metadata_json["primary_cloud_photo_path"])
        if not image_url:
            image_url = prop.metadata_json.get("image_url")
        if not image_url and prop.metadata_json.get("url"):
            image_url = fetch_property_photo(prop.metadata_json["url"])
            if image_url:
                prop.metadata_json = {**prop.metadata_json, "image_url": image_url}
        if image_url:
            media.append(
                {
                    "type": "image",
                    "url": image_url,
                    "caption": f"{prop.title} in {prop.location}",
                }
            )
    return media[:4]


def _format_search_context(results: list[dict]) -> str:
    if not results:
        return (
            "Search completed but there are no matching properties right now. "
            "Ask whether the user wants to broaden location, budget, or property type."
        )

    lines = ["Search results from live web listings:"]
    for idx, item in enumerate(results, start=1):
        photo_text = f" | photo {item['image_url']}" if item.get("image_url") else ""
        snippet_text = f" | snippet {item['snippet']}" if item.get("snippet") else ""
        fit_text = f" | budget_fit {item['budget_fit']}" if item.get("budget_fit") else ""
        rationale_text = f" | why {item['why_it_matches']}" if item.get("why_it_matches") else ""
        lines.append(
            f"{idx}. [{item['property_id'][:8]}] | {item['title']} | {item['location']} | INR {int(item['price'])} | {item['bhk']} BHK | {item['type']} | source {item['source']} | link {item['url']}{photo_text}{snippet_text}{fit_text}{rationale_text}"
        )
    lines.append(
        "Respond like an experienced broker: summarize the user's ask, highlight the best-fit options, "
        "mention any useful caveats, and end with a clear next step."
    )
    lines.append("Invite the user to reply with shortlist <property_id> to save a property.")
    return "\n".join(lines)


def _selected_property_refs(message: str) -> list[str]:
    refs = re.findall(r"\b[a-f0-9]{8,64}\b", message.lower())
    unique: list[str] = []
    for ref in refs:
        if ref not in unique:
            unique.append(ref)
    return unique


def _missing_fields(preferences: dict) -> list[str]:
    return [
        field_name
        for field_name in ["location", "budget", "bhk", "type"]
        if preferences.get(field_name) in ("", None, [])
    ]


def execute_property_search(
    db: Session,
    user: User,
    preferences: dict,
    *,
    assumption_note: str = "",
) -> dict:
    payload = SearchPropertiesInput(
        location=preferences["location"],
        budget=float(preferences["budget"]),
        bhk=int(preferences["bhk"]),
        type=preferences["type"],
    )
    try:
        live_listings = search_real_properties(preferences)
    except Exception:
        live_listings = []

    web_properties = persist_web_properties(db, live_listings) if live_listings else []

    if web_properties:
        result_items = [
            {
                "property_id": prop.property_id,
                "title": prop.title,
                "location": prop.location,
                "price": prop.price,
                "bhk": prop.bhk,
                "type": prop.type,
                "source": prop.source,
                "url": prop.metadata_json.get("url", "") if prop.metadata_json else "",
                "image_url": prop.metadata_json.get("image_url", "") if prop.metadata_json else "",
                "snippet": prop.metadata_json.get("snippet", "") if prop.metadata_json else "",
            }
            for prop in web_properties
        ]
        media = _property_media(web_properties)
    else:
        local_results = search_local_properties(db, payload, preferences)
        result_items = [
            {
                "property_id": item.property_id,
                "title": item.title,
                "location": item.location,
                "price": item.price,
                "bhk": item.bhk,
                "type": item.type,
                "source": "local",
                "url": "",
                "image_url": "",
                "snippet": "",
            }
            for item in local_results
        ]
        property_ids = [item.property_id for item in local_results]
        local_properties = get_properties_by_ids(db, property_ids)
        media = _property_media(local_properties)

    for item in result_items:
        item["budget_fit"] = _budget_fit(float(item["price"]), float(preferences["budget"]))
        item["why_it_matches"] = _search_rationale(item, preferences)

    property_grid = "\n".join(
        f"🏠 [{item['property_id'][:8]}] {item['title']} - ₹{item['price']:,} ({item['bhk']}BHK)"
        for item in result_items[:6]
    )
    tool_context = f"{_format_search_context(result_items)}{assumption_note}\n\nProperty Grid:\n{property_grid}"

    return {
        "status": "ok",
        "count": len(result_items),
        "properties": result_items,
        "media": media,
        "tool_context": tool_context,
    }


def execute_shortlist(db: Session, user_id: str, property_ref: str) -> dict:
    resolved_id = resolve_property_id(db, property_ref)
    if not resolved_id:
        return {
            "status": "error",
            "tool_context": (
                "The requested property id could not be resolved. "
                "Ask the user to pick a property id from the latest search results."
            ),
        }

    persist_shortlist(db, user_id, resolved_id)
    shortlisted = get_properties_by_ids(db, [resolved_id])
    media = _property_media(shortlisted)
    return {
        "status": "ok",
        "shortlisted_property_id": resolved_id,
        "media": media,
        "tool_context": (
            f"The property with id [{resolved_id[:8]}] has been added to the user's shortlist. "
            "Confirm that clearly and invite the user to shortlist more properties or book a walkthrough for 3 to 5 selected properties."
        ),
    }


def execute_schedule(
    db: Session,
    user: User,
    message: str,
    *,
    property_ids: list[str] | None = None,
    preferred_time: datetime | None = None,
) -> dict:
    shortlist_rows = db.query(Shortlist).filter(Shortlist.user_id == user.user_id).all()
    shortlisted_ids = [row.property_id for row in shortlist_rows if row.status == "shortlisted"]

    requested_ids: list[str] = []
    for ref in property_ids or _selected_property_refs(message):
        resolved_id = resolve_property_id(db, ref)
        if resolved_id and resolved_id not in requested_ids:
            requested_ids.append(resolved_id)

    selected_ids = requested_ids or shortlisted_ids
    if len(selected_ids) < 3 or len(selected_ids) > 5:
        return {
            "status": "error",
            "tool_context": (
                "The user wants to book a walkthrough appointment. "
                f"They currently have {len(selected_ids)} selected properties for the appointment. "
                "Explain that walkthrough booking requires 3 to 5 selected properties and ask them to shortlist or choose the right number."
            ),
        }

    selected_properties = get_properties_by_ids(db, selected_ids)
    schedule_time = preferred_time or (datetime.now(UTC) + timedelta(days=1))
    payload = ScheduleVisitInput(property_ids=selected_ids, preferred_time=schedule_time)
    visit = schedule_visit(db, user.user_id, payload)
    for property_id in selected_ids:
        get_or_create_deal(
            db,
            user_id=user.user_id,
            property_id=property_id,
            visit_id=visit.visit_id,
            stage="visit_scheduled",
            notes="Walkthrough scheduled",
        )
    field_agent = assign_agent(db, visit.visit_id)
    property_lines = "\n".join(
        f"- [{prop.property_id[:8]}] {prop.title} | {prop.location} | INR {int(prop.price)}"
        for prop in selected_properties
    )
    media = _property_media(selected_properties)

    if not field_agent:
        return {
            "status": "ok",
            "visit_id": visit.visit_id,
            "selected_property_ids": selected_ids,
            "media": media,
            "tool_context": (
                f"A walkthrough appointment for visit {visit.visit_id} has been created for {visit.scheduled_time.isoformat()} "
                f"for these properties:\n{property_lines}\n"
                "No field agent is currently available. Ask the user to wait for assignment."
            ),
        }

    return {
        "status": "ok",
        "visit_id": visit.visit_id,
        "selected_property_ids": selected_ids,
        "media": media,
        "tool_context": (
            f"Walkthrough appointment booked for {visit.scheduled_time.isoformat()} with visit id {visit.visit_id}. "
            f"Assigned agent: {field_agent.name}, phone {field_agent.phone}. "
            f"Selected properties:\n{property_lines}\n"
            f"Tell the user to contact {settings.field_agent_name} for the walkthrough of these selected properties."
        ),
    }


def _apply_tool_result(state: BrokerToolState, tool_name: str, result: dict) -> str:
    state.last_tool = tool_name
    state.response_status = result.get("status", "ok")
    state.tool_context = result.get("tool_context", "")
    if result.get("media"):
        state.media = result["media"]
    for key in ("shortlisted_property_id", "visit_id", "selected_property_ids", "count"):
        if key in result:
            state.metadata[key] = result[key]
    return json.dumps({k: v for k, v in result.items() if k not in ("media", "tool_context")}, default=str)


@function_tool(name_override="search_properties")
def search_property_listings(
    location: str,
    budget: float,
    bhk: int,
    listing_type: Literal["rent", "buy"],
) -> str:
    """Search local database and live web listings for matching properties."""
    state = get_broker_tool_state()
    preferences = {
        **state.preferences,
        "location": location,
        "budget": budget,
        "bhk": bhk,
        "type": listing_type,
    }
    state.user.preferences = preferences
    state.db.commit()
    state.preferences = preferences

    missing = _missing_fields(preferences)
    if missing:
        state.response_status = "clarification"
        state.tool_context = (
            f"The user has not provided enough information to search yet. Missing details: {', '.join(missing)}. "
            "Ask one short, friendly follow-up question."
        )
        return json.dumps({"status": "clarification", "missing": missing})

    result = execute_property_search(state.db, state.user, preferences)
    return _apply_tool_result(state, "search_properties", result)


@function_tool(name_override="shortlist_property")
def add_property_to_shortlist(property_id: str) -> str:
    """Add a property to the user's shortlist using the property id or 8-character prefix."""
    state = get_broker_tool_state()
    ref = property_id.strip()
    if not ref:
        state.response_status = "error"
        state.tool_context = (
            "The user used the shortlist command without a property id. "
            "Ask them to send shortlist <property_id>."
        )
        return json.dumps({"status": "error", "reason": "missing_property_id"})

    result = execute_shortlist(state.db, state.user.user_id, ref)
    return _apply_tool_result(state, "shortlist_property", result)


@function_tool(name_override="schedule_visit")
def book_property_visit(
    property_ids: list[str],
    preferred_time: str | None = None,
) -> str:
    """Book a walkthrough for 3 to 5 properties. property_ids can be full ids or 8-character prefixes."""
    state = get_broker_tool_state()
    schedule_time: datetime | None = None
    if preferred_time:
        schedule_time = datetime.fromisoformat(preferred_time.replace("Z", "+00:00"))
        if schedule_time.tzinfo is None:
            schedule_time = schedule_time.replace(tzinfo=UTC)

    result = execute_schedule(
        state.db,
        state.user,
        "",
        property_ids=property_ids,
        preferred_time=schedule_time,
    )
    return _apply_tool_result(state, "schedule_visit", result)


BROKER_TOOLS = [search_property_listings, add_property_to_shortlist, book_property_visit]


def merge_user_preferences(db: Session, user: User, message: str, llm_prefs: dict | None = None) -> dict:
    merged = extract_preferences(message, user.preferences)
    if llm_prefs:
        merged.update(llm_prefs)
    return merged


def infer_listing_type(message: str, preferences: dict) -> str | None:
    text = message.lower()
    if preferences.get("type"):
        return preferences["type"]
    if any(term in text for term in ["buy", "sale", "sell", "purchase", "own"]):
        return "buy"
    if any(term in text for term in ["rent", "rental", "lease", "tenant"]):
        return "rent"

    budget = preferences.get("budget")
    bhk = preferences.get("bhk")
    location = preferences.get("location")
    if budget and bhk and location and float(budget) <= 200000:
        return "rent"
    return None


def detect_tool_intent(message: str, preferences: dict) -> str | None:
    text = message.strip().lower()
    if text.startswith("shortlist") or re.search(r"\bshortlist\b", text):
        return "shortlist_property"
    if text.startswith("schedule") or any(term in text for term in ("appointment", "walkthrough", "book walkthrough")):
        return "schedule_visit"
    if has_minimum_requirements(preferences, ["location", "budget", "bhk", "type"]):
        return "search_properties"
    if any(term in text for term in ("bhk", "rent", "buy", "property", "properties", "flat", "apartment", "house")):
        return "search_properties"
    return None


def run_deterministic_tool(message: str, preferences: dict) -> dict | None:
    intent = detect_tool_intent(message, preferences)
    state = get_broker_tool_state()

    if intent == "shortlist_property":
        parts = message.strip().split()
        ref = parts[1] if len(parts) >= 2 else ""
        if not ref:
            for candidate in _selected_property_refs(message):
                ref = candidate
                break
        result = execute_shortlist(state.db, state.user.user_id, ref)
        _apply_tool_result(state, intent, result)
        return result

    if intent == "schedule_visit":
        result = execute_schedule(state.db, state.user, message)
        _apply_tool_result(state, intent, result)
        return result

    if intent == "search_properties":
        missing = _missing_fields(preferences)
        if missing:
            state.response_status = "clarification"
            state.tool_context = (
                f"The user has not provided enough information to search yet. Missing details: {', '.join(missing)}. "
                "Ask one short, friendly follow-up question."
            )
            return {"status": "clarification", "missing": missing}

        assumption_note = state.assumption_note
        result = execute_property_search(state.db, state.user, preferences, assumption_note=assumption_note)
        _apply_tool_result(state, "search_properties", result)
        return result

    return None
