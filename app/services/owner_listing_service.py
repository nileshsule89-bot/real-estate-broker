import re

from sqlalchemy.orm import Session

from app.models import User
from app.services.photo_storage_service import upload_property_photo_from_file, upload_property_photo_from_url
from app.services.inventory_service import refresh_inventory_batch


OWNER_FIELDS = ["location", "price", "bhk", "type"]


def is_owner_listing_message(message: str, previous_draft: dict | None = None) -> bool:
    text = message.lower()
    if previous_draft:
        return True
    return any(
        phrase in text
        for phrase in [
            "list my property",
            "post my property",
            "upload my property",
            "i am an owner",
            "i'm an owner",
            "owner here",
            "my flat is available",
            "my apartment is available",
            "my property is available",
        ]
    )


def extract_owner_listing_details(message: str, previous: dict | None = None) -> dict:
    draft = dict(previous or {})
    text = message.lower()

    location_match = re.search(r"(?:in|at|near)\s+([a-z0-9\-\s,]{3,})", text)
    if location_match and "location" not in draft:
        location = re.split(r"(?:for|rent|sale|buy|under|at|price|bhk)", location_match.group(1), maxsplit=1)[0]
        draft["location"] = location.strip(" ,.-")

    bhk_match = re.search(r"(\d+)\s*bhk", text)
    if bhk_match:
        draft["bhk"] = int(bhk_match.group(1))

    price_match = re.search(r"(?:rent|price|asking|budget|for|at)\s*(?:rs\.?|inr)?\s*([0-9][0-9,]{3,})", text)
    if price_match:
        draft["price"] = float(price_match.group(1).replace(",", ""))

    if "rent" in text:
        draft["type"] = "rent"
    elif any(term in text for term in ["sale", "sell", "buy"]):
        draft["type"] = "buy"

    if "title" not in draft and draft.get("bhk") and draft.get("location") and draft.get("type"):
        action = "Rent" if draft["type"] == "rent" else "Sale"
        draft["title"] = f"{draft['bhk']} BHK Owner Listing for {action} in {draft['location'].title()}"

    return draft


def missing_owner_fields(draft: dict) -> list[str]:
    return [field for field in OWNER_FIELDS if draft.get(field) in ("", None, [])]


def save_owner_listing(
    db: Session,
    user: User,
    draft: dict,
    *,
    image_paths: list[str] | None = None,
    image_urls: list[str] | None = None,
):
    listing = {
        "listing_id": f"owner-{user.user_id[:8]}-{draft['type']}-{draft['bhk']}",
        "title": draft["title"],
        "location": draft["location"],
        "price": draft["price"],
        "bhk": draft["bhk"],
        "type": draft["type"],
        "source": "owner_upload",
        "source_type": "owner_upload",
        "url": "",
        "owner_contact": user.phone_number,
        "verification_status": "pending_owner_verification",
        "metadata": {
            "submitted_via": "chatbot",
            "owner_name": user.name,
            "owner_phone": user.phone_number,
            "notes": draft.get("notes", ""),
        },
    }
    rows = refresh_inventory_batch(db, [listing], location=draft["location"], mark_missing=False)
    property_row = rows[0]
    uploaded_photo_urls: list[str] = []
    for path in image_paths or []:
        public_url = upload_property_photo_from_file(db, property_row, path)
        if public_url:
            uploaded_photo_urls.append(public_url)
    for url in image_urls or []:
        public_url = upload_property_photo_from_url(db, property_row, url)
        if public_url:
            uploaded_photo_urls.append(public_url)
    return property_row, uploaded_photo_urls
