from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class StaffOut(BaseModel):
    id: int
    name: str
    whatsapp_number: str
    role: str

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: int
    staff_whatsapp_number: str
    property_name: str
    task_description: str
    due_time: str
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class WhatsAppMessageOut(BaseModel):
    id: int
    task_id: Optional[int]
    staff_whatsapp_number: str
    direction: str
    message_text: Optional[str]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class SendTestTaskResponse(BaseModel):
    task_id: int
    whatsapp_response: dict
    message: str


# ---------------------------------------------------------------------------
# Damage case schemas
# ---------------------------------------------------------------------------

class DamageCaseCreate(BaseModel):
    hostfully_property_uid: Optional[str] = None
    hostfully_guest_uid: Optional[str] = None
    unit_name: Optional[str] = None
    guest_name: str
    guest_phone: Optional[str] = None
    guest_email: Optional[str] = None
    damage_description: str
    deposit_amount: float
    gm_number: str
    ops_supervisor_number: str
    reservations_number: str
    accounts_number: str
    reported_by_number: Optional[str] = None
    due_at: Optional[datetime] = None


class QuoteBody(BaseModel):
    damage_amount: float
    other_charges: float = 0.0
    notes: Optional[str] = None


class PhotoBody(BaseModel):
    photo_url_or_media_id: str
    photo_type: str = "placement_proof"


class DamageEventOut(BaseModel):
    id: int
    damage_case_id: int
    event_type: str
    message: Optional[str]
    whatsapp_number: Optional[str]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class DamagePhotoOut(BaseModel):
    id: int
    damage_case_id: int
    photo_url_or_media_id: str
    photo_type: str
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class DamageCaseOut(BaseModel):
    id: int
    hostfully_property_uid: Optional[str]
    hostfully_guest_uid: Optional[str]
    unit_name: str
    guest_name: str
    guest_phone: Optional[str]
    guest_email: Optional[str]
    damage_description: str
    deposit_amount: float
    damage_amount: Optional[float]
    other_charges: Optional[float]
    refund_amount: Optional[float]
    status: str
    waiting_on: Optional[str]
    reported_by_number: Optional[str]
    gm_number: str
    ops_supervisor_number: str
    reservations_number: str
    accounts_number: str
    photo_proof_received: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    due_at: Optional[datetime]
    closed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class DamageCaseDetail(DamageCaseOut):
    events: List[DamageEventOut] = []
    photos: List[DamagePhotoOut] = []
