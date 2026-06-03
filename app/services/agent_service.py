import re

from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.services.agreement_service import generate_sample_agreement_pdf
from app.services.deal_service import advance_deal_stage, create_registration, get_or_create_deal, parse_price_from_message
from app.services.history_service import get_recent_interactions, get_shortlisted_properties, save_interaction
from app.services.broker_agent import run_broker_agent
from app.services.llm_service import generate_reply_with_llm
from app.services.owner_listing_service import (
    extract_owner_listing_details,
    is_owner_listing_message,
    missing_owner_fields,
    save_owner_listing,
)
from app.services.photo_storage_service import public_url_for_object
from app.services.property_service import get_properties_by_ids, resolve_property_id
from app.services.web_property_service import fetch_property_photo


def _upsert_user(db: Session, phone_number: str) -> tuple[User, bool]:
    user = db.query(User).filter(User.phone_number == phone_number).first()
    if user:
        return user, False
    user = User(phone_number=phone_number, name="WhatsApp User", preferences={})
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


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

        broker_result = run_broker_agent(
            db,
            user,
            message,
            is_initial=is_new,
            history_context=_format_history_context(db, user.user_id),
        )
        return _save_assistant_response(
            db,
            user.user_id,
            broker_result.status,
            broker_result.reply,
            broker_result.metadata,
            broker_result.media,
        )

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
