import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="Customer")
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class Property(Base):
    __tablename__ = "properties"

    property_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(120), index=True)
    price: Mapped[float] = mapped_column(Float)
    bhk: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(120), default="internal")
    owner_contact: Mapped[str] = mapped_column(String(40), default="NA")
    status: Mapped[str] = mapped_column(String(32), default="active")
    freshness_score: Mapped[float] = mapped_column(Float, default=0.5)
    fitness_score: Mapped[float] = mapped_column(Float, default=0.5)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    listing_url: Mapped[str] = mapped_column(String(500), default="")
    photo_storage_folder: Mapped[str] = mapped_column(String(300), default="")
    canonical_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Shortlist(Base):
    __tablename__ = "shortlists"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    property_id: Mapped[str] = mapped_column(String(64), ForeignKey("properties.property_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="shortlisted")


class Visit(Base):
    __tablename__ = "visits"

    visit_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    property_ids: Mapped[str] = mapped_column(Text)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    assigned_agent_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.agent_id"))


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(40))
    availability: Mapped[str] = mapped_column(String(32), default="available")
    location: Mapped[str] = mapped_column(String(120), default="thane")


class Interaction(Base):
    __tablename__ = "interactions"

    interaction_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PropertySource(Base):
    __tablename__ = "property_sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    property_id: Mapped[str] = mapped_column(String(64), ForeignKey("properties.property_id"), index=True)
    source_name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(32), default="feed")
    external_listing_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PropertyVerification(Base):
    __tablename__ = "property_verifications"

    verification_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    property_id: Mapped[str] = mapped_column(String(64), ForeignKey("properties.property_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="verified")
    notes: Mapped[str] = mapped_column(Text, default="")
    verified_by: Mapped[str] = mapped_column(String(120), default="system")
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Deal(Base):
    __tablename__ = "deals"

    deal_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    property_id: Mapped[str] = mapped_column(String(64), ForeignKey("properties.property_id"), index=True)
    visit_id: Mapped[str] = mapped_column(String(64), ForeignKey("visits.visit_id"), default="")
    stage: Mapped[str] = mapped_column(String(32), default="discovery")
    status: Mapped[str] = mapped_column(String(32), default="open")
    offered_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    final_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    agreement_path: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Registration(Base):
    __tablename__ = "registrations"

    registration_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id: Mapped[str] = mapped_column(String(64), ForeignKey("deals.deal_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="initiated")
    agreement_type: Mapped[str] = mapped_column(String(32), default="rent")
    agreement_path: Mapped[str] = mapped_column(String(500), default="")
    office_location: Mapped[str] = mapped_column(String(200), default="To be confirmed")
    scheduled_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
