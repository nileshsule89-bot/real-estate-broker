from sqlalchemy.orm import Session

from app.models import Interaction, Property, Shortlist


def save_interaction(
    db: Session,
    user_id: str,
    role: str,
    message: str,
    metadata: dict | None = None,
) -> Interaction:
    row = Interaction(
        user_id=user_id,
        role=role,
        message=message,
        metadata_json=metadata or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_recent_interactions(db: Session, user_id: str, limit: int = 12) -> list[Interaction]:
    rows = (
        db.query(Interaction)
        .filter(Interaction.user_id == user_id)
        .order_by(Interaction.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def get_shortlisted_properties(db: Session, user_id: str) -> list[Property]:
    return (
        db.query(Property)
        .join(Shortlist, Shortlist.property_id == Property.property_id)
        .filter(Shortlist.user_id == user_id)
        .filter(Shortlist.status == "shortlisted")
        .all()
    )
