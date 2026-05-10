from app.models import Visit
from app.schemas import ScheduleVisitInput


def schedule_visit(db, user_id: str, payload: ScheduleVisitInput) -> Visit:
    visit = Visit(
        user_id=user_id,
        property_ids=",".join(payload.property_ids),
        scheduled_time=payload.preferred_time,
        status="scheduled",
        assigned_agent_id="",
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit
