from pydantic import BaseModel
from typing import Optional
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
