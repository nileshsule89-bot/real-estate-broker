from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session
import gradio as gr

from app.config import settings
from app.database import ensure_database_schema, get_db
from app.models import Agent, Property
from app.services.agent_service import assignment_contact, handle_user_message
from app.services.whatsapp_service import send_whatsapp_image, send_whatsapp_message
from gradio_ui import create_demo

app = FastAPI(title=settings.app_name)


def seed_data_if_needed(db: Session) -> None:
    if db.query(Property).count() == 0:
        properties = [
            Property(
                title="Spacious 2BHK near station",
                location="thane",
                price=24000,
                bhk=2,
                type="rent",
                source="internal",
                owner_contact="9000000001",
                listing_url="",
                freshness_score=0.9,
                verification_status="verified",
                metadata_json={"amenities": ["parking", "lift", "security"]},
            ),
            Property(
                title="Premium 2BHK family apartment",
                location="thane west",
                price=26000,
                bhk=2,
                type="rent",
                source="aggregator",
                owner_contact="9000000002",
                listing_url="",
                freshness_score=0.85,
                verification_status="verified",
                metadata_json={"amenities": ["lift", "school_nearby"]},
            ),
            Property(
                title="Budget 2BHK in Kalyan",
                location="kalyan",
                price=21000,
                bhk=2,
                type="rent",
                source="internal",
                owner_contact="9000000003",
                listing_url="",
                freshness_score=0.7,
                verification_status="unverified",
                metadata_json={"amenities": ["security"]},
            ),
            Property(
                title="Modern 2BHK buy option",
                location="thane",
                price=7500000,
                bhk=2,
                type="buy",
                source="internal",
                owner_contact="9000000004",
                listing_url="",
                freshness_score=0.88,
                verification_status="verified",
                metadata_json={"amenities": ["parking", "gym"]},
            ),
        ]
        db.add_all(properties)

    if db.query(Agent).count() == 0:
        db.add(
            Agent(
                name=settings.field_agent_name,
                phone=settings.field_agent_phone,
                availability="available",
                location="thane",
            )
        )
    db.commit()


@app.on_event("startup")
def startup_event() -> None:
    ensure_database_schema()
    db = next(get_db())
    seed_data_if_needed(db)
    db.close()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "field_agent": assignment_contact()}


@app.get("/webhook/whatsapp")
def verify_webhook(
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def receive_whatsapp(payload: dict, db: Session = Depends(get_db)) -> dict:
    # Works with a simplified payload for local testing and can be extended
    # to full WhatsApp Cloud webhook payload shape.
    phone_number = payload.get("from", "unknown")
    message_text = payload.get("message", "") or payload.get("caption", "") or "Uploading property photos"
    image_urls = payload.get("image_urls", []) or []
    if not message_text and not image_urls:
        raise HTTPException(status_code=400, detail="Missing message text or images")

    result = handle_user_message(db, phone_number, message_text, image_urls=image_urls)
    delivery = {"text": await send_whatsapp_message(phone_number, result["reply"])}
    if result.get("media"):
        image_deliveries = []
        for item in result["media"]:
            if item.get("type") == "image" and item.get("url"):
                image_deliveries.append(
                    await send_whatsapp_image(phone_number, item["url"], item.get("caption", ""))
                )
        if image_deliveries:
            delivery["images"] = image_deliveries
    return {"reply": result, "delivery": delivery}


@app.post("/simulate-message")
def simulate_message(payload: dict, db: Session = Depends(get_db)) -> dict:
    phone_number = payload.get("from", "919999999999")
    message_text = payload.get("message", "") or "Uploading property photos"
    image_paths = payload.get("image_paths", []) or []
    image_urls = payload.get("image_urls", []) or []
    if not message_text and not image_paths and not image_urls:
        raise HTTPException(status_code=400, detail="Missing message text or images")
    return handle_user_message(db, phone_number, message_text, image_paths=image_paths, image_urls=image_urls)


app = gr.mount_gradio_app(app, create_demo(), path="/")
