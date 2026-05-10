from app.models import Agent, Visit


def assign_agent(db, visit_id: str) -> Agent | None:
    visit = db.query(Visit).filter(Visit.visit_id == visit_id).first()
    if not visit:
        return None

    agent = db.query(Agent).filter(Agent.availability == "available").first()
    if not agent:
        return None

    visit.assigned_agent_id = agent.agent_id
    visit.status = "assigned"
    agent.availability = "busy"
    db.commit()
    return agent
