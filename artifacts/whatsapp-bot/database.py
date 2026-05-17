import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

SQLITE_URL = "sqlite:///./whatsapp_bot.db"

engine = create_engine(
    SQLITE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    whatsapp_number = Column(String, unique=True, nullable=False, index=True)
    role = Column(String, nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    staff_whatsapp_number = Column(String, nullable=False, index=True)
    property_name = Column(String, nullable=False)
    task_description = Column(Text, nullable=False)
    due_time = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=True)
    staff_whatsapp_number = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    message_text = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DamageCase(Base):
    __tablename__ = "damage_cases"

    id = Column(Integer, primary_key=True, index=True)
    hostfully_property_uid = Column(String, nullable=True, index=True)
    hostfully_guest_uid = Column(String, nullable=True)
    unit_name = Column(String, nullable=False)
    guest_name = Column(String, nullable=False)
    guest_phone = Column(String, nullable=True)
    guest_email = Column(String, nullable=True)
    damage_description = Column(Text, nullable=False)
    deposit_amount = Column(Float, nullable=False, default=0.0)
    damage_amount = Column(Float, nullable=True, default=0.0)
    other_charges = Column(Float, nullable=True, default=0.0)
    refund_amount = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="quote_pending")
    waiting_on = Column(String, nullable=True)
    reported_by_number = Column(String, nullable=True)
    gm_number = Column(String, nullable=False)
    ops_supervisor_number = Column(String, nullable=False)
    reservations_number = Column(String, nullable=False)
    accounts_number = Column(String, nullable=False)
    photo_proof_received = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    due_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)


class DamageEvent(Base):
    __tablename__ = "damage_events"

    id = Column(Integer, primary_key=True, index=True)
    damage_case_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    whatsapp_number = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DamagePhoto(Base):
    __tablename__ = "damage_photos"

    id = Column(Integer, primary_key=True, index=True)
    damage_case_id = Column(Integer, nullable=False, index=True)
    photo_url_or_media_id = Column(String, nullable=False)
    photo_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
