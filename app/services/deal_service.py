import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Deal, Property, Registration


def parse_price_from_message(message: str) -> float | None:
    match = re.search(r"(?:rs\.?|inr)?\s*([0-9][0-9,]{3,})", message.lower())
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def get_or_create_deal(
    db: Session,
    *,
    user_id: str,
    property_id: str,
    visit_id: str = "",
    stage: str = "discovery",
    notes: str = "",
) -> Deal:
    deal = (
        db.query(Deal)
        .filter(Deal.user_id == user_id)
        .filter(Deal.property_id == property_id)
        .filter(Deal.status == "open")
        .first()
    )
    if deal:
        if visit_id and not deal.visit_id:
            deal.visit_id = visit_id
        if stage and deal.stage != stage:
            deal.stage = stage
        if notes:
            deal.notes = notes
        deal.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(deal)
        return deal

    deal = Deal(
        user_id=user_id,
        property_id=property_id,
        visit_id=visit_id,
        stage=stage,
        status="open",
        notes=notes,
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return deal


def advance_deal_stage(
    db: Session,
    deal_id: str,
    *,
    stage: str,
    offered_price: float | None = None,
    final_price: float | None = None,
    agreement_path: str | None = None,
    notes: str | None = None,
) -> Deal:
    deal = db.query(Deal).filter(Deal.deal_id == deal_id).first()
    if not deal:
        raise ValueError(f"Unknown deal_id: {deal_id}")

    deal.stage = stage
    if offered_price is not None:
        deal.offered_price = offered_price
    if final_price is not None:
        deal.final_price = final_price
    if agreement_path is not None:
        deal.agreement_path = agreement_path
    if notes is not None:
        deal.notes = notes
    if stage == "closed":
        deal.status = "closed"
    deal.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(deal)
    return deal


def create_registration(
    db: Session,
    *,
    deal_id: str,
    agreement_type: str,
    agreement_path: str,
    office_location: str = "Thane sub-registrar office",
) -> Registration:
    registration = (
        db.query(Registration)
        .filter(Registration.deal_id == deal_id)
        .filter(Registration.status.in_(["initiated", "scheduled"]))
        .first()
    )
    scheduled_date = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2)
    if registration:
        registration.agreement_type = agreement_type
        registration.agreement_path = agreement_path
        registration.office_location = office_location
        registration.scheduled_date = registration.scheduled_date or scheduled_date
        registration.status = "scheduled"
        db.commit()
        db.refresh(registration)
        return registration

    registration = Registration(
        deal_id=deal_id,
        status="scheduled",
        agreement_type=agreement_type,
        agreement_path=agreement_path,
        office_location=office_location,
        scheduled_date=scheduled_date,
    )
    db.add(registration)
    db.commit()
    db.refresh(registration)
    return registration


def property_summary(property_row: Property) -> str:
    return f"{property_row.title} in {property_row.location} for INR {int(property_row.price)}"
