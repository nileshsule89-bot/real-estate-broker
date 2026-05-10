import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    Agent,
    Deal,
    Interaction,
    Property,
    PropertySource,
    PropertyVerification,
    Registration,
    Shortlist,
    User,
    Visit,
)
from app.services.inventory_service import refresh_inventory_batch


@pytest.fixture(autouse=True)
def stub_external_services(monkeypatch) -> None:
    def fake_extract_preferences_with_llm(message: str) -> dict:
        return {}

    def fake_generate_reply_with_llm(message: str, context: str, *, is_initial: bool = False) -> str:
        intro = ""
        if is_initial:
            intro = (
                "Hi, I'm Nilesh. I help with property research, finalizing homes, sharing property photos, "
                "drafting sample rent or buy/sell agreement PDFs, and arranging walkthroughs.\n"
            )

        if "agreement PDF has been created at" in context:
            pdf_path = re.search(r"created at (.+?)\. Tell", context, re.DOTALL).group(1).strip()
            return intro + f"Your sample agreement PDF is ready at {pdf_path}. I can also help you review and register the final agreement."

        if "Registration has been initiated" in context:
            registration_id = re.search(r"registration_id['\"]?: ['\"]?([a-f0-9-]+)", context)
            return intro + "Your registration workflow has been scheduled, and I can help coordinate the remaining paperwork."

        if "A deal record has been opened" in context:
            return intro + "I have started negotiation and finalization tracking for this property. Ask me for the agreement draft when you want to move ahead."

        if "owner trying to upload a property listing" in context:
            return intro + "Please share the missing property details so I can upload your owner listing."

        if "owner's property listing has been saved" in context:
            property_id = re.search(r"\[([a-f0-9]{8})\]", context).group(1)
            return intro + f"Your owner listing has been captured under property id {property_id} and is pending verification."

        if "Walkthrough appointment booked" in context:
            visit_id = re.search(r"visit id ([a-f0-9-]+)", context).group(1)
            return intro + f"Your walkthrough appointment is booked under visit {visit_id}. Please contact Nilesh Sule for the walkthrough."

        if "requires 3 to 5 selected properties" in context:
            return intro + "Please choose 3 to 5 shortlisted properties for the walkthrough appointment."

        if "added to the user's shortlist" in context:
            property_id = re.search(r"\[([a-f0-9]{8})\]", context).group(1)
            return intro + f"Property {property_id} has been added to your shortlist, and I can share more photos if you want."

        if "could not be resolved" in context:
            return intro + "Please use a property id from the latest search results."

        if "Search results from live web listings:" in context:
            lines = [intro + "You are looking for a curated set of matching property options, and I found a few promising live listings."]
            lines.append("Overall, these look like reasonable fits for your search and include photos where available.")
            for property_id, title, location, price, bhk, source, link in re.findall(
                r"\[([a-f0-9]{8})\] \| (.*?) \| (.*?) \| INR (\d+) \| (\d+) BHK \| .*? \| source (.*?) \| link (https?://\S+)",
                context,
            ):
                lines.append(f"[{property_id}] {title} in {location} for INR {price} | {bhk} BHK | source {source} | {link}")
            if "Assumption used for this search: interpreted the request as a rent search" in context:
                lines.append("I treated this as a rental search because your message included a BHK and a monthly-looking budget.")
            lines.append("Reply with shortlist <property_id> to save the one you like, or tell me if you want tighter filtering.")
            return "\n".join(lines)

        return intro + "Please share your location, budget, BHK, and whether you want to rent or buy."

    def fake_search_real_properties(preferences: dict) -> list[dict]:
        return [
            {
                "listing_id": "web11111",
                "title": "2 BHK apartment in Thane West",
                "location": preferences["location"],
                "price": 24000,
                "bhk": int(preferences["bhk"]),
                "type": preferences["type"],
                "source": "housing.com",
                "url": "https://example.com/property-1",
                "snippet": "Spacious 2 BHK with parking",
                "image_url": "https://example.com/photo-1.jpg",
            },
            {
                "listing_id": "web22222",
                "title": "Modern 2 BHK near station",
                "location": preferences["location"],
                "price": 25500,
                "bhk": int(preferences["bhk"]),
                "type": preferences["type"],
                "source": "99acres.com",
                "url": "https://example.com/property-2",
                "snippet": "Family-friendly tower listing",
                "image_url": "https://example.com/photo-2.jpg",
            },
            {
                "listing_id": "web33333",
                "title": "Budget 2 BHK with balcony",
                "location": preferences["location"],
                "price": 23000,
                "bhk": int(preferences["bhk"]),
                "type": preferences["type"],
                "source": "magicbricks.com",
                "url": "https://example.com/property-3",
                "snippet": "Close to schools and market",
                "image_url": "https://example.com/photo-3.jpg",
            },
        ]

    monkeypatch.setattr("app.services.agent_service.extract_preferences_with_llm", fake_extract_preferences_with_llm)
    monkeypatch.setattr("app.services.agent_service.generate_reply_with_llm", fake_generate_reply_with_llm)
    monkeypatch.setattr("app.services.agent_service.search_real_properties", fake_search_real_properties)


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert "field_agent" in payload


def test_intro_live_results_and_media_flow() -> None:
    user = "919123456789"
    db = SessionLocal()
    db.query(Agent).update({"availability": "available"})
    db.query(Shortlist).delete()
    db.query(Visit).delete()
    db.query(Registration).delete()
    db.query(Deal).delete()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        search_response = client.post(
            "/simulate-message",
            json={"from": user, "message": "Looking for 2 BHK for rent in Thane under 25000"},
        )
        assert search_response.status_code == 200
        payload = search_response.json()
        reply = payload["reply"]
        assert reply.startswith("Hi, I'm Nilesh.")
        assert "curated set of matching property options" in reply
        assert len(payload["media"]) == 3
        assert payload["media"][0]["type"] == "image"
        assert "photo-1.jpg" in payload["media"][0]["url"]

        property_ids = re.findall(r"\[([a-f0-9]{8})\]", reply)
        assert len(property_ids) >= 3

        shortlist_response = client.post(
            "/simulate-message",
            json={"from": user, "message": f"shortlist {property_ids[0]}"},
        )
        assert shortlist_response.status_code == 200
        assert "added to your shortlist" in shortlist_response.json()["reply"].lower()
        assert shortlist_response.json()["media"][0]["type"] == "image"


def test_infers_rent_for_bhk_plus_budget_query() -> None:
    user = "919777777777"
    db = SessionLocal()
    db.query(Agent).update({"availability": "available"})
    db.query(Shortlist).delete()
    db.query(Visit).delete()
    db.query(Registration).delete()
    db.query(Deal).delete()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        response = client.post(
            "/simulate-message",
            json={"from": user, "message": "show me properties from powai, 1bhk for 50000"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert "rental search" in payload["reply"].lower()
        assert len(re.findall(r"\[([a-f0-9]{8})\]", payload["reply"])) >= 3


def test_walkthrough_and_agreement_flow() -> None:
    user = "919888888888"
    db = SessionLocal()
    db.query(Agent).update({"availability": "available"})
    db.query(Shortlist).delete()
    db.query(Visit).delete()
    db.query(Registration).delete()
    db.query(Deal).delete()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        search_response = client.post(
            "/simulate-message",
            json={"from": user, "message": "Looking for 2 BHK for rent in Thane under 26000"},
        )
        property_ids = re.findall(r"\[([a-f0-9]{8})\]", search_response.json()["reply"])

        for property_id in property_ids[:3]:
            client.post("/simulate-message", json={"from": user, "message": f"shortlist {property_id}"})

        walkthrough_response = client.post(
            "/simulate-message",
            json={"from": user, "message": f"book walkthrough for {property_ids[0]} {property_ids[1]} {property_ids[2]}"},
        )
        assert walkthrough_response.status_code == 200
        assert "walkthrough appointment is booked" in walkthrough_response.json()["reply"].lower()
        assert len(walkthrough_response.json()["media"]) >= 3

        agreement_response = client.post(
            "/simulate-message",
            json={"from": user, "message": "draft a sample rent agreement pdf"},
        )
        assert agreement_response.status_code == 200
        reply = agreement_response.json()["reply"]
        pdf_path = re.search(r"ready at (.+?)\. I can", reply).group(1)
        assert Path(pdf_path).exists()
        assert Path(pdf_path).suffix == ".pdf"

    db = SessionLocal()
    assert db.query(Interaction).count() >= 10
    visit = db.query(Visit).first()
    assert visit is not None
    assert len(visit.property_ids.split(",")) == 3
    db.close()


def test_live_inventory_creates_source_and_verification_records() -> None:
    user = "919666666666"
    db = SessionLocal()
    db.query(Registration).delete()
    db.query(Deal).delete()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Shortlist).delete()
    db.query(Visit).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        response = client.post(
            "/simulate-message",
            json={"from": user, "message": "Need 2 bhk on rent in thane west under 26000"},
        )
        assert response.status_code == 200

    db = SessionLocal()
    web_properties = db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).all()
    assert len(web_properties) == 3
    assert all(prop.freshness_score >= 0.8 for prop in web_properties)
    assert all(prop.verification_status == "verified" for prop in web_properties)
    assert all(prop.canonical_key for prop in web_properties)
    assert db.query(PropertySource).count() == 3
    assert db.query(PropertyVerification).count() == 3
    db.close()


def test_negotiation_and_registration_flow() -> None:
    user = "919555555555"
    db = SessionLocal()
    db.query(Registration).delete()
    db.query(Deal).delete()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Shortlist).delete()
    db.query(Visit).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "99acres.com", "magicbricks.com"])).delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        search_response = client.post(
            "/simulate-message",
            json={"from": user, "message": "Looking for 2 BHK for rent in Thane under 26000"},
        )
        property_ids = re.findall(r"\[([a-f0-9]{8})\]", search_response.json()["reply"])
        client.post("/simulate-message", json={"from": user, "message": f"shortlist {property_ids[0]}"})

        negotiation_response = client.post(
            "/simulate-message",
            json={"from": user, "message": f"please negotiate best price for {property_ids[0]} around 23000"},
        )
        assert negotiation_response.status_code == 200
        assert "negotiation and finalization tracking" in negotiation_response.json()["reply"].lower()

        registration_response = client.post(
            "/simulate-message",
            json={"from": user, "message": f"start registration for {property_ids[0]}"},
        )
        assert registration_response.status_code == 200
        assert "registration workflow has been scheduled" in registration_response.json()["reply"].lower()

    db = SessionLocal()
    deal = db.query(Deal).first()
    assert deal is not None
    assert deal.stage == "registration"
    assert deal.offered_price == 23000
    assert deal.agreement_path
    assert Path(deal.agreement_path).exists()
    registration = db.query(Registration).first()
    assert registration is not None
    assert registration.status == "scheduled"
    assert registration.agreement_path == deal.agreement_path
    db.close()


def test_refresh_inventory_batch_dedupes_and_updates_scores() -> None:
    db = SessionLocal()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Property).filter(Property.source.in_(["housing.com", "www.magicbricks.com", "www.nobroker.in"])).delete()
    db.commit()

    rows = refresh_inventory_batch(
        db,
        [
            {
                "listing_id": "dup-1",
                "title": "2 BHK Apartment for Rent in Airoli Navi Mumbai",
                "location": "Airoli",
                "price": 45000,
                "bhk": 2,
                "type": "rent",
                "source": "www.magicbricks.com",
                "source_type": "site_scrape",
                "url": "https://example.com/airoli-rent-1",
                "verification_status": "verified",
                "metadata": {"area_sqft": 950, "image_url": "https://example.com/p1.jpg"},
            },
            {
                "listing_id": "dup-2",
                "title": "2 BHK Apartment for Rent in Airoli Navi Mumbai",
                "location": "airoli",
                "price": 45000,
                "bhk": 2,
                "type": "rent",
                "source": "www.nobroker.in",
                "source_type": "search_fallback",
                "url": "https://example.com/airoli-rent-1",
                "verification_status": "unverified",
                "metadata": {"snippet": "duplicate of same listing"},
            },
        ],
        location="Airoli",
        mark_missing=False,
    )
    assert len(rows) == 1
    assert rows[0].freshness_score >= 0.8
    assert rows[0].fitness_score >= 0.8
    db.close()


def test_owner_can_upload_property_via_chatbot() -> None:
    user = "919444444444"
    db = SessionLocal()
    db.query(PropertyVerification).delete()
    db.query(PropertySource).delete()
    db.query(Interaction).delete()
    db.query(User).filter(User.phone_number == user).delete()
    db.query(Property).filter(Property.source == "owner_upload").delete()
    db.commit()
    db.close()

    with TestClient(app) as client:
        first = client.post(
            "/simulate-message",
            json={"from": user, "message": "I am an owner and want to list my property in Airoli"},
        )
        assert first.status_code == 200
        assert "missing property details" in first.json()["reply"].lower()

        second = client.post(
            "/simulate-message",
            json={"from": user, "message": "It is a 2 BHK for rent at 42000"},
        )
        assert second.status_code == 200
        assert "pending verification" in second.json()["reply"].lower()

    db = SessionLocal()
    owner_property = db.query(Property).filter(Property.source == "owner_upload").first()
    assert owner_property is not None
    assert owner_property.location == "airoli"
    assert owner_property.price == 42000
    assert owner_property.bhk == 2
    assert owner_property.type == "rent"
    assert owner_property.verification_status == "pending_owner_verification"
    db.close()


def test_refresh_batch_preserves_photo_storage_folder_metadata() -> None:
    db = SessionLocal()
    db.query(Property).filter(Property.source == "test-photo-source").delete()
    db.commit()

    rows = refresh_inventory_batch(
        db,
        [
            {
                "listing_id": "photo-folder-1",
                "title": "2 BHK Test Property",
                "location": "Airoli",
                "price": 41000,
                "bhk": 2,
                "type": "rent",
                "source": "test-photo-source",
                "source_type": "feed",
                "url": "https://example.com/property-photo-folder",
                "verification_status": "verified",
                "metadata": {
                    "photo_storage_folder": "properties/test-folder",
                    "image_url": "https://example.com/test.jpg",
                },
            }
        ],
        location="Airoli",
        mark_missing=False,
    )
    assert len(rows) == 1
    assert rows[0].photo_storage_folder == "properties/test-folder"
    db.close()
