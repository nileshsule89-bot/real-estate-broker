import hashlib
import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Property, PropertySource, PropertyVerification


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def normalize_location(value: str) -> str:
    location = normalize_text(value).lower().strip(",")
    replacements = {
        "navi mumbai": "navi mumbai",
        "airoli, navi mumbai": "airoli",
    }
    return replacements.get(location, location)


def build_canonical_key(title: str, location: str, bhk: int, listing_type: str) -> str:
    cleaned_title = normalize_text(title).lower()
    cleaned_title = re.sub(r"[^a-z0-9\s]", " ", cleaned_title)
    cleaned_title = re.sub(r"\bfor (rent|sale|buy)\b", "", cleaned_title)
    cleaned_title = re.sub(r"\s+", " ", cleaned_title).strip()
    return "|".join(
        [
            cleaned_title,
            normalize_location(location),
            str(int(bhk)),
            listing_type.strip().lower(),
        ]
    )


def compute_freshness_score(
    last_seen_at: datetime | None,
    last_verified_at: datetime | None,
    verification_status: str,
) -> float:
    now = utcnow_naive()
    recency_days = max(0, (now - (last_seen_at or now)).days)
    verification_days = max(0, (now - (last_verified_at or now)).days) if last_verified_at else 21

    recency_score = max(0.2, 1 - (recency_days / 30))
    verification_score = max(0.2, 1 - (verification_days / 30))
    trust_bonus = 0.15 if verification_status == "verified" else 0.0

    return round(min(1.0, recency_score * 0.6 + verification_score * 0.4 + trust_bonus), 2)


def compute_fitness_score(property_row: Property) -> float:
    metadata = property_row.metadata_json or {}
    completeness = 0.4
    if property_row.title:
        completeness += 0.1
    if property_row.location:
        completeness += 0.1
    if property_row.price and property_row.price > 0:
        completeness += 0.1
    if property_row.bhk and property_row.bhk > 0:
        completeness += 0.1
    if metadata.get("area_sqft"):
        completeness += 0.05
    if metadata.get("image_url"):
        completeness += 0.05
    if metadata.get("amenities") or metadata.get("highlights"):
        completeness += 0.05

    verification_bonus = 0.15 if property_row.verification_status == "verified" else 0.0
    freshness_weight = (property_row.freshness_score or 0.3) * 0.35
    quality = min(1.0, completeness * 0.5 + freshness_weight + verification_bonus)
    return round(quality, 2)


def _merge_metadata(existing: dict | None, incoming: dict | None) -> dict:
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value not in ("", None, [], {}):
            merged[key] = value
    return merged


def clean_listing_payload(item: dict) -> dict:
    listing_type = str(item.get("type", "rent")).lower()
    listing_type = "buy" if listing_type in {"buy", "sale", "resale"} else "rent"
    location = normalize_location(str(item.get("location", "")))
    title = normalize_text(str(item.get("title", "Property listing")))
    source = normalize_text(str(item.get("source", "unknown"))) or "unknown"
    listing_url = normalize_text(str(item.get("url", "")))
    metadata = dict(item.get("metadata") or {})
    if item.get("snippet") and not metadata.get("snippet"):
        metadata["snippet"] = item["snippet"]
    if item.get("image_url") and not metadata.get("image_url"):
        metadata["image_url"] = item["image_url"]
    if listing_url and not metadata.get("url"):
        metadata["url"] = listing_url
    metadata["cleaned_at"] = utcnow_naive().isoformat()

    return {
        "listing_id": normalize_text(str(item.get("listing_id", ""))),
        "title": title,
        "location": location,
        "price": float(item.get("price", 0) or 0),
        "bhk": int(item.get("bhk", 1) or 1),
        "type": listing_type,
        "source": source,
        "source_type": normalize_text(str(item.get("source_type", "feed"))) or "feed",
        "url": listing_url,
        "owner_contact": normalize_text(str(item.get("owner_contact", listing_url or "NA"))) or "NA",
        "verification_status": normalize_text(str(item.get("verification_status", "unverified"))) or "unverified",
        "metadata": metadata,
    }


def upsert_inventory_property(
    db: Session,
    *,
    title: str,
    location: str,
    price: float,
    bhk: int,
    listing_type: str,
    source_name: str,
    source_type: str,
    owner_contact: str,
    listing_url: str,
    metadata: dict | None = None,
    external_listing_id: str = "",
    verification_status: str = "unverified",
) -> Property:
    metadata = metadata or {}
    canonical_key = build_canonical_key(title, location, bhk, listing_type)
    now = utcnow_naive()

    property_row = db.query(Property).filter(Property.canonical_key == canonical_key).first()
    if not property_row and listing_url:
        property_row = db.query(Property).filter(Property.listing_url == listing_url).first()

    if property_row:
        property_row.title = title
        property_row.location = normalize_location(location)
        property_row.price = price
        property_row.bhk = bhk
        property_row.type = listing_type
        property_row.source = source_name
        property_row.owner_contact = owner_contact
        property_row.listing_url = listing_url
        property_row.status = "active"
        property_row.last_seen_at = now
        property_row.metadata_json = _merge_metadata(property_row.metadata_json, metadata)
        if property_row.verification_status != "verified":
            property_row.verification_status = verification_status
        if metadata.get("photo_storage_folder") and not property_row.photo_storage_folder:
            property_row.photo_storage_folder = metadata["photo_storage_folder"]
    else:
        property_row = Property(
            title=title,
            location=normalize_location(location),
            price=price,
            bhk=bhk,
            type=listing_type,
            source=source_name,
            owner_contact=owner_contact,
            status="active",
            freshness_score=0.5,
            fitness_score=0.5,
            verification_status=verification_status,
            listing_url=listing_url,
            photo_storage_folder=str(metadata.get("photo_storage_folder", "")),
            canonical_key=canonical_key,
            last_seen_at=now,
            metadata_json=metadata,
        )
        db.add(property_row)
        db.flush()

    if not property_row.photo_storage_folder:
        property_row.photo_storage_folder = f"properties/{property_row.property_id}"
    property_row.canonical_key = canonical_key
    property_row.freshness_score = compute_freshness_score(
        property_row.last_seen_at,
        property_row.last_verified_at,
        property_row.verification_status,
    )
    property_row.fitness_score = compute_fitness_score(property_row)

    fingerprint_source = external_listing_id or listing_url or canonical_key
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    source_row = (
        db.query(PropertySource)
        .filter(PropertySource.property_id == property_row.property_id)
        .filter(PropertySource.fingerprint == fingerprint)
        .first()
    )
    if source_row:
        source_row.payload_json = _merge_metadata(source_row.payload_json, metadata)
        source_row.source_url = listing_url
        source_row.ingested_at = now
        source_row.source_name = source_name
        source_row.source_type = source_type
    else:
        db.add(
            PropertySource(
                property_id=property_row.property_id,
                source_name=source_name,
                source_type=source_type,
                external_listing_id=external_listing_id,
                source_url=listing_url,
                fingerprint=fingerprint,
                payload_json=metadata,
                ingested_at=now,
            )
        )

    db.commit()
    db.refresh(property_row)
    return property_row


def record_property_verification(
    db: Session,
    property_id: str,
    *,
    status: str = "verified",
    notes: str = "",
    verified_by: str = "system",
) -> Property:
    property_row = db.query(Property).filter(Property.property_id == property_id).first()
    if not property_row:
        raise ValueError(f"Unknown property_id: {property_id}")

    verified_at = utcnow_naive()
    property_row.verification_status = status
    property_row.last_verified_at = verified_at
    property_row.status = "active" if status == "verified" else "needs_review"
    property_row.freshness_score = compute_freshness_score(
        property_row.last_seen_at,
        verified_at,
        status,
    )
    property_row.fitness_score = compute_fitness_score(property_row)
    db.add(
        PropertyVerification(
            property_id=property_id,
            status=status,
            notes=notes,
            verified_by=verified_by,
            verified_at=verified_at,
        )
    )
    db.commit()
    db.refresh(property_row)
    return property_row


def mark_missing_listings_inactive(
    db: Session,
    *,
    location: str,
    source_name: str,
    active_listing_urls: set[str],
) -> int:
    normalized_location = normalize_location(location)
    rows = (
        db.query(Property)
        .filter(Property.source == source_name)
        .filter(Property.location.contains(normalized_location))
        .all()
    )
    updated = 0
    for row in rows:
        if row.listing_url and row.listing_url not in active_listing_urls:
            row.status = "inactive"
            row.fitness_score = min(row.fitness_score or 0.5, 0.25)
            updated += 1
    if updated:
        db.commit()
    return updated


def refresh_inventory_batch(db: Session, listings: list[dict], *, location: str, mark_missing: bool = False) -> list[Property]:
    cleaned: list[dict] = []
    seen: dict[str, dict] = {}
    for item in listings:
        row = clean_listing_payload(item)
        key = row["url"] or build_canonical_key(row["title"], row["location"], row["bhk"], row["type"])
        current = seen.get(key)
        if not current or (row["verification_status"] == "verified" and current["verification_status"] != "verified"):
            seen[key] = row

    cleaned = list(seen.values())
    rows: list[Property] = []
    urls_by_source: dict[str, set[str]] = {}
    for item in cleaned:
        property_row = upsert_inventory_property(
            db,
            title=item["title"],
            location=item["location"],
            price=item["price"],
            bhk=item["bhk"],
            listing_type=item["type"],
            source_name=item["source"],
            source_type=item["source_type"],
            owner_contact=item["owner_contact"],
            listing_url=item["url"],
            metadata=item["metadata"],
            external_listing_id=item["listing_id"],
            verification_status=item["verification_status"],
        )
        if item["verification_status"] == "verified":
            record_property_verification(
                db,
                property_row.property_id,
                status="verified",
                notes=f"Verified during inventory refresh for {location}",
                verified_by="inventory_refresh",
            )
        rows.append(property_row)
        if item["url"]:
            urls_by_source.setdefault(item["source"], set()).add(item["url"])

    if mark_missing:
        for source_name, urls in urls_by_source.items():
            mark_missing_listings_inactive(db, location=location, source_name=source_name, active_listing_urls=urls)
    return rows
