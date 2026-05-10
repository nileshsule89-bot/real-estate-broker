from sqlalchemy.orm import Session

from app.models import Property, Shortlist
from app.schemas import RankingResult, SearchPropertiesInput


def _location_score(query_location: str, property_location: str) -> float:
    return 1.0 if query_location.lower() in property_location.lower() else 0.35


def _amenities_score(metadata: dict) -> float:
    amenities = metadata.get("amenities", []) if isinstance(metadata, dict) else []
    if not amenities:
        return 0.4
    preferred = {"parking", "lift", "security", "school_nearby"}
    overlap = len(set(a.lower() for a in amenities) & preferred)
    return min(1.0, 0.4 + overlap * 0.15)


def search_properties(db: Session, payload: SearchPropertiesInput, user_preferences: dict | None = None, top_n: int = 5) -> list[RankingResult]:
    rows = (
        db.query(Property)
        .filter(Property.type == payload.type)
        .filter(Property.bhk == payload.bhk)
        .all()
    )

    results: list[RankingResult] = []
    user_preferences = user_preferences or {}

    for prop in rows:
        budget_match = max(0.0, 1 - abs(prop.price - payload.budget) / max(payload.budget, 1))
        location_match = _location_score(payload.location, prop.location)
        amenity_match = _amenities_score(prop.metadata_json)
        preference_history_match = 1.0 if user_preferences.get("location") == payload.location else 0.6
        freshness_match = min(1.0, max(0.3, prop.freshness_score or 0.3))
        verification_match = 1.0 if prop.verification_status == "verified" else 0.5

        score = (
            budget_match * 0.25
            + location_match * 0.25
            + amenity_match * 0.15
            + preference_history_match * 0.15
            + freshness_match * 0.10
            + verification_match * 0.10
        ) * 100

        results.append(
            RankingResult(
                property_id=prop.property_id,
                title=prop.title,
                location=prop.location,
                price=prop.price,
                bhk=prop.bhk,
                type=prop.type,
                score=round(score, 2),
            )
        )

    results.sort(key=lambda item: item.score, reverse=True)
    return results[:top_n]


def shortlist_property(db: Session, user_id: str, property_id: str) -> Shortlist:
    row = (
        db.query(Shortlist)
        .filter(Shortlist.user_id == user_id)
        .filter(Shortlist.property_id == property_id)
        .first()
    )
    if row:
        row.status = "shortlisted"
        db.commit()
        db.refresh(row)
        return row

    row = Shortlist(user_id=user_id, property_id=property_id, status="shortlisted")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_property_id(db: Session, property_ref: str) -> str | None:
    ref = property_ref.strip()
    if not ref:
        return None

    exact = db.query(Property).filter(Property.property_id == ref).first()
    if exact:
        return exact.property_id

    matches = db.query(Property).filter(Property.property_id.startswith(ref)).all()
    if len(matches) == 1:
        return matches[0].property_id
    return None


def get_properties_by_ids(db: Session, property_ids: list[str]) -> list[Property]:
    if not property_ids:
        return []
    return db.query(Property).filter(Property.property_id.in_(property_ids)).all()
