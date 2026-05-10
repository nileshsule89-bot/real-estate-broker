import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Shortlist, User
from app.schemas import ScheduleVisitInput, SearchPropertiesInput
from app.services.agent_assignment import assign_agent
from app.services.agreement_service import generate_sample_agreement_pdf
from app.services.deal_service import advance_deal_stage, create_registration, get_or_create_deal, parse_price_from_message
from app.services.history_service import get_recent_interactions, get_shortlisted_properties, save_interaction
from app.services.llm_service import extract_preferences_with_llm, generate_reply_with_llm
from app.services.memory_service import extract_preferences, has_minimum_requirements
from app.services.owner_listing_service import (
    extract_owner_listing_details,
    is_owner_listing_message,
    missing_owner_fields,
    save_owner_listing,
)
from app.services.photo_storage_service import public_url_for_object
from app.services.property_service import (
    get_properties_by_ids,
    resolve_property_id,
    search_properties,
    shortlist_property,
)
from app.services.scheduler_service import schedule_visit
from app.services.web_property_service import fetch_property_photo, persist_web_properties, search_real_properties


def _upsert_user(db: Session, phone_number: str) -> tuple[User, bool]:
    user = db.query(User).filter(User.phone_number == phone_number).first()
    if user:
        return user, False
    user = User(phone_number=phone_number, name="WhatsApp User", preferences={})
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


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
    lines.append("Respond like an experienced broker: summarize the user's ask, highlight the best-fit options, mention any useful caveats, and end with a clear next step.")
    lines.append("Invite the user to reply with shortlist <property_id> to save a property.")
    return "\n".join(lines)


def _format_history_context(db: Session, user_id: str) -> str:
    interactions = get_recent_interactions(db, user_id)
    shortlist = get_shortlisted_properties(db, user_id)

    lines = ["Recent interaction history:"]
    if interactions:
        for row in interactions:
            lines.append(f"- {row.role}: {row.message}")
    else:
        lines.append("- No prior interaction history.")

    lines.append("")
    lines.append("Current shortlisted properties:")
    if shortlist:
        for prop in shortlist:
            url = prop.metadata_json.get("url", "") if prop.metadata_json else ""
            lines.append(
                f"- [{prop.property_id[:8]}] {prop.title} | {prop.location} | INR {int(prop.price)} | {prop.bhk} BHK | {prop.type} | {url}"
            )
    else:
        lines.append("- No shortlisted properties yet.")

    return "\n".join(lines)


def _missing_fields(preferences: dict) -> list[str]:
    return [
        field
        for field in ["location", "budget", "bhk", "type"]
        if preferences.get(field) in ("", None, [])
    ]


def _infer_listing_type(message: str, preferences: dict) -> str | None:
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


def _selected_property_refs(message: str) -> list[str]:
    refs = re.findall(r"\b[a-f0-9]{8,64}\b", message.lower())
    unique: list[str] = []
    for ref in refs:
        if ref not in unique:
            unique.append(ref)
    return unique


def _detect_agreement_type(message: str) -> str | None:
    text = message.lower()
    if "registration" in text:
        return None
    if "agreement" not in text and "pdf" not in text:
        return None
    if "rent" in text or "lease" in text:
        return "rent"
    if "buy" in text or "sell" in text or "sale" in text:
        return "buy_sell"
    return "rent"


def _is_negotiation_or_finalization_request(message: str) -> bool:
    text = message.lower()
    return any(
        term in text
        for term in ["negot", "finaliz", "finalis", "close deal", "best price", "token amount"]
    )


def _is_registration_request(message: str) -> bool:
    text = message.lower()
    return "registration" in text or "register the property" in text


def _target_properties_for_progression(db: Session, user_id: str, message: str):
    requested_ids: list[str] = []
    for ref in _selected_property_refs(message):
        resolved_id = resolve_property_id(db, ref)
        if resolved_id and resolved_id not in requested_ids:
            requested_ids.append(resolved_id)

    if requested_ids:
        return get_properties_by_ids(db, requested_ids)

    shortlisted = get_shortlisted_properties(db, user_id)
    if shortlisted:
        return shortlisted[:1]
    return []


def _property_media(properties) -> list[dict]:
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


def _save_assistant_response(
    db: Session,
    user_id: str,
    status: str,
    reply: str,
    metadata: dict | None = None,
    media: list[dict] | None = None,
) -> dict:
    payload_metadata = {"status": status, **(metadata or {})}
    if media:
        payload_metadata["media"] = media
    save_interaction(db, user_id, "assistant", reply, payload_metadata)
    result = {"status": status, "reply": reply}
    if media:
        result["media"] = media
    return result


def handle_user_message(
    db: Session,
    phone_number: str,
    message: str,
    *,
    image_paths: list[str] | None = None,
    image_urls: list[str] | None = None,
) -> dict:
    try:
        user, is_new = _upsert_user(db, phone_number)
        save_interaction(db, user.user_id, "user", message)
        text = message.strip().lower()
        owner_draft = dict(user.preferences.get("owner_listing_draft", {}) if user.preferences else {})

        if is_owner_listing_message(message, owner_draft):
            updated_draft = extract_owner_listing_details(message, owner_draft)
            missing_fields = missing_owner_fields(updated_draft)
            user.preferences = {**(user.preferences or {}), "owner_listing_draft": updated_draft}
            db.commit()
            if missing_fields:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        "The user is an owner trying to upload a property listing by chat. "
                        f"Current draft details: {updated_draft}. Missing fields: {', '.join(missing_fields)}. "
                        "Ask one short follow-up question to collect the missing details."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "clarification", reply)

            property_row, uploaded_photo_urls = save_owner_listing(
                db,
                user,
                updated_draft,
                image_paths=image_paths,
                image_urls=image_urls,
            )
            cleaned_preferences = dict(user.preferences or {})
            cleaned_preferences.pop("owner_listing_draft", None)
            user.preferences = cleaned_preferences
            db.commit()
            media = [
                {"type": "image", "url": url, "caption": f"{property_row.title} photo"}
                for url in uploaded_photo_urls
            ]
            photo_note = (
                f" {len(uploaded_photo_urls)} property photo(s) were uploaded to cloud storage."
                if uploaded_photo_urls
                else " No property photo was uploaded with this listing yet."
            )
            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"The owner's property listing has been saved with id [{property_row.property_id[:8]}], title {property_row.title}, "
                    f"location {property_row.location}, price INR {int(property_row.price)}, and {property_row.bhk} BHK. "
                    f"Tell the owner the listing is captured, marked pending verification, and can now be matched to seekers.{photo_note}"
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"uploaded_property_id": property_row.property_id},
                media or None,
            )

        agreement_type = _detect_agreement_type(message)
        if agreement_type:
            shortlisted_properties = get_shortlisted_properties(db, user.user_id)
            if not shortlisted_properties:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        "The user asked for an agreement draft before shortlisting any property. "
                        "Ask them to shortlist at least one property first."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            primary_property = shortlisted_properties[0]
            deal = get_or_create_deal(
                db,
                user_id=user.user_id,
                property_id=primary_property.property_id,
                stage="agreement_drafting",
                notes="Agreement draft requested by user",
            )
            pdf_path = generate_sample_agreement_pdf(user, agreement_type, shortlisted_properties)
            advance_deal_stage(
                db,
                deal.deal_id,
                stage="agreement_drafted",
                agreement_path=pdf_path,
                final_price=primary_property.price,
                notes="Sample agreement drafted",
            )
            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"A sample {agreement_type.replace('_', '/')} agreement PDF has been created at {pdf_path}. "
                    f"Tell the user the PDF is ready and that {settings.field_agent_name} can help review and register the final agreement."
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"pdf_path": pdf_path, "agreement_type": agreement_type, "deal_id": deal.deal_id},
            )

        if _is_registration_request(message):
            target_properties = _target_properties_for_progression(db, user.user_id, message)
            if not target_properties:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        "The user asked about registration without any shortlisted property. "
                        "Ask them to shortlist a property first."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            property_row = target_properties[0]
            deal = get_or_create_deal(
                db,
                user_id=user.user_id,
                property_id=property_row.property_id,
                stage="registration",
                notes="Registration requested by user",
            )
            if not deal.agreement_path:
                agreement_type = "buy_sell" if property_row.type == "buy" else "rent"
                agreement_path = generate_sample_agreement_pdf(user, agreement_type, [property_row])
                deal = advance_deal_stage(
                    db,
                    deal.deal_id,
                    stage="agreement_drafted",
                    agreement_path=agreement_path,
                    final_price=property_row.price,
                    notes="Agreement drafted automatically before registration",
                )
            registration = create_registration(
                db,
                deal_id=deal.deal_id,
                agreement_type="buy_sell" if property_row.type == "buy" else "rent",
                agreement_path=deal.agreement_path,
            )
            advance_deal_stage(
                db,
                deal.deal_id,
                stage="registration",
                notes=f"Registration scheduled for {registration.scheduled_date.isoformat()}",
            )
            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"Registration has been initiated for {property_row.title}. "
                    f"Agreement copy: {registration.agreement_path}. "
                    f"Scheduled date: {registration.scheduled_date.isoformat()}. "
                    f"Office: {registration.office_location}. "
                    f"Tell the user {settings.field_agent_name} can coordinate the remaining paperwork."
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"deal_id": deal.deal_id, "registration_id": registration.registration_id},
            )

        if text.startswith("shortlist"):
            parts = message.strip().split()
            if len(parts) < 2:
                reply = generate_reply_with_llm(
                    message,
                    f"{_format_history_context(db, user.user_id)}\n\nThe user used the shortlist command without a property id. Ask them to send shortlist <property_id>.",
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            resolved_id = resolve_property_id(db, parts[1])
            if not resolved_id:
                reply = generate_reply_with_llm(
                    message,
                    f"{_format_history_context(db, user.user_id)}\n\nThe requested property id could not be resolved. Ask the user to pick a property id from the latest search results.",
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            shortlist_property(db, user.user_id, resolved_id)
            shortlisted = get_properties_by_ids(db, [resolved_id])
            media = _property_media(shortlisted)
            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"The property with id [{resolved_id[:8]}] has been added to the user's shortlist. "
                    "Confirm that clearly and invite the user to shortlist more properties or book a walkthrough for 3 to 5 selected properties."
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"shortlisted_property_id": resolved_id},
                media,
            )

        if text.startswith("schedule") or "appointment" in text or "walkthrough" in text:
            shortlist_rows = db.query(Shortlist).filter(Shortlist.user_id == user.user_id).all()
            shortlisted_ids = [row.property_id for row in shortlist_rows if row.status == "shortlisted"]

            requested_ids = []
            for ref in _selected_property_refs(message):
                resolved_id = resolve_property_id(db, ref)
                if resolved_id and resolved_id not in requested_ids:
                    requested_ids.append(resolved_id)

            selected_ids = requested_ids or shortlisted_ids
            if len(selected_ids) < 3 or len(selected_ids) > 5:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        "The user wants to book a walkthrough appointment. "
                        f"They currently have {len(selected_ids)} selected properties for the appointment. "
                        "Explain that walkthrough booking requires 3 to 5 selected properties and ask them to shortlist or choose the right number."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            selected_properties = get_properties_by_ids(db, selected_ids)
            payload = ScheduleVisitInput(property_ids=selected_ids, preferred_time=datetime.now(UTC) + timedelta(days=1))
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
            agent = assign_agent(db, visit.visit_id)
            property_lines = "\n".join(
                f"- [{prop.property_id[:8]}] {prop.title} | {prop.location} | INR {int(prop.price)}"
                for prop in selected_properties
            )
            media = _property_media(selected_properties)
            if not agent:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        f"A walkthrough appointment for visit {visit.visit_id} has been created for {visit.scheduled_time.isoformat()} "
                        f"for these properties:\n{property_lines}\n"
                        "No field agent is currently available. Ask the user to wait for assignment."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(
                    db,
                    user.user_id,
                    "ok",
                    reply,
                    {"visit_id": visit.visit_id, "selected_property_ids": selected_ids},
                    media,
                )

            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"Walkthrough appointment booked for {visit.scheduled_time.isoformat()} with visit id {visit.visit_id}. "
                    f"Assigned agent: {agent.name}, phone {agent.phone}. "
                    f"Selected properties:\n{property_lines}\n"
                    f"Tell the user to contact {settings.field_agent_name} for the walkthrough of these selected properties."
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"visit_id": visit.visit_id, "selected_property_ids": selected_ids},
                media,
            )

        if _is_negotiation_or_finalization_request(message):
            target_properties = _target_properties_for_progression(db, user.user_id, message)
            if not target_properties:
                reply = generate_reply_with_llm(
                    message,
                    (
                        f"{_format_history_context(db, user.user_id)}\n\n"
                        "The user wants to negotiate or finalize a property but none is shortlisted yet. "
                        "Ask them to shortlist the target property first."
                    ),
                    is_initial=is_new,
                )
                return _save_assistant_response(db, user.user_id, "error", reply)

            property_row = target_properties[0]
            offered_price = parse_price_from_message(message)
            deal = get_or_create_deal(
                db,
                user_id=user.user_id,
                property_id=property_row.property_id,
                stage="negotiation",
                notes="Negotiation started",
            )
            deal = advance_deal_stage(
                db,
                deal.deal_id,
                stage="finalization",
                offered_price=offered_price,
                final_price=property_row.price,
                notes="User asked to negotiate/finalize this property",
            )
            offered_price_text = f" Their stated offer is INR {int(offered_price)}." if offered_price else ""
            reply = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"A deal record has been opened for {property_row.title} in {property_row.location} at the listed price INR {int(property_row.price)}."
                    f"{offered_price_text} "
                    "Tell the user the property is now in finalization and negotiation tracking, and invite them to ask for the agreement draft when ready."
                ),
                is_initial=is_new,
            )
            return _save_assistant_response(
                db,
                user.user_id,
                "ok",
                reply,
                {"deal_id": deal.deal_id, "property_id": property_row.property_id},
            )

        merged_preferences = extract_preferences(message, user.preferences)
        llm_prefs = extract_preferences_with_llm(message)
        if llm_prefs:
            merged_preferences.update(llm_prefs)
        inferred_type = _infer_listing_type(message, merged_preferences)
        if inferred_type and not merged_preferences.get("type"):
            merged_preferences["type"] = inferred_type
        user.preferences = merged_preferences
        db.commit()

        if has_minimum_requirements(merged_preferences, ["location", "budget", "bhk", "type"]):
            payload = SearchPropertiesInput(
                location=merged_preferences["location"],
                budget=float(merged_preferences["budget"]),
                bhk=int(merged_preferences["bhk"]),
                type=merged_preferences["type"],
            )
            try:
                live_listings = search_real_properties(merged_preferences)
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
                # Use actual Property objects for media extraction
                media = _property_media(web_properties)
            else:
                local_results = search_properties(db, payload, merged_preferences)
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
                # Get actual Property objects for media extraction
                property_ids = [item.property_id for item in local_results]
                local_properties = get_properties_by_ids(db, property_ids)
                media = _property_media(local_properties)

            for item in result_items:
                item["budget_fit"] = _budget_fit(float(item["price"]), float(merged_preferences["budget"]))
                item["why_it_matches"] = _search_rationale(item, merged_preferences)

            assumption_context = ""
            if inferred_type and "type" not in llm_prefs and "type" not in extract_preferences(message, {}):
                assumption_context = (
                    f"\n\nAssumption used for this search: interpreted the request as a {inferred_type} search "
                    "because the user gave a BHK and budget but did not explicitly say rent or buy."
                )
            
            # Create a grid-like property summary
            property_grid = "\n".join([
                f"🏠 [{item['property_id'][:8]}] {item['title']} - ₹{item['price']:,} ({item['bhk']}BHK)"
                for item in result_items[:6]  # Show up to 6 properties in grid
            ])
            
            try:
                reply = generate_reply_with_llm(
                    message,
                    f"{_format_history_context(db, user.user_id)}\n\n{_format_search_context(result_items)}{assumption_context}\n\nProperty Grid:\n{property_grid}",
                    is_initial=is_new,
                )
            except Exception as llm_error:
                # If LLM fails (e.g., rate limit), provide fallback but still return media
                if "429" in str(llm_error) or "Too Many Requests" in str(llm_error):
                    reply = f"Found {len(result_items)} properties matching your criteria. Here are the top options:\n\n{property_grid}"
                else:
                    reply = f"Found {len(result_items)} properties matching your criteria. Please review the property details.\n\n{property_grid}"
            
            return _save_assistant_response(db, user.user_id, "ok", reply, media=media)

        try:
            clarification = generate_reply_with_llm(
                message,
                (
                    f"{_format_history_context(db, user.user_id)}\n\n"
                    f"The user has not provided enough information to search yet. Missing details: {', '.join(_missing_fields(merged_preferences))}. "
                    "Ask one short, friendly follow-up question."
                ),
                is_initial=is_new,
            )
        except Exception:
            # Fallback clarification message if LLM fails
            clarification = f"I need a bit more information. Could you please provide the missing details: {', '.join(_missing_fields(merged_preferences))}?"
        return _save_assistant_response(db, user.user_id, "clarification", clarification.strip())

    except Exception as exc:
        import traceback

        traceback.print_exc()
        try:
            user = db.query(User).filter(User.phone_number == phone_number).first()
            user_id = user.user_id if user else ""
            
            # Provide fallback response based on error type
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                reply = "I'm temporarily receiving too many requests. Please try again in a moment. Your request is important!"
            elif "Rate limited" in str(exc):
                reply = "The service is rate-limited. Please try again shortly."
            else:
                # Try to get LLM error message, but don't fail if LLM is down
                try:
                    reply = generate_reply_with_llm(
                        message,
                        (
                            "A backend error happened while processing the user's request. "
                            f"Error details: {exc}. Apologize briefly and ask the user to try again."
                        ),
                    )
                    if not reply:
                        reply = "Sorry, an error occurred processing your request. Please try again."
                except Exception:
                    reply = "Sorry, an error occurred processing your request. Please try again."
            
            if user_id:
                return _save_assistant_response(db, user_id, "error", reply, {"error": str(exc)})
            return {"status": "error", "reply": reply}
        except Exception:
            raise


def assignment_contact() -> str:
    return f"{settings.field_agent_name} ({settings.field_agent_phone})"
