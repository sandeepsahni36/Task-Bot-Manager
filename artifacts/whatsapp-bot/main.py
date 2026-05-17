import os
import json
import logging
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, Request, Response, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import init_db, get_db, Task, WhatsAppMessage, Staff
from schemas import TaskOut, WhatsAppMessageOut, SendTestTaskResponse
from whatsapp import send_template_message
from hostfully import get_hostfully_config, fetch_properties, fetch_guests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="WhatsApp Task Reminder Bot",
    description="Send WhatsApp reminders to staff and track task completion.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Database initialised")


REPLY_MAP = {
    "1": "completed",
    "done": "completed",
    "2": "delayed",
    "delayed": "delayed",
    "3": "issue",
    "issue": "issue",
}


@app.get("/", summary="Health check")
def health_check():
    return {"status": "ok", "service": "WhatsApp Task Reminder Bot"}


@app.get("/webhooks/whatsapp", response_class=PlainTextResponse, summary="Meta webhook verification")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if not verify_token:
        logger.error("WHATSAPP_VERIFY_TOKEN is not configured")
        raise HTTPException(status_code=500, detail="Server misconfiguration: WHATSAPP_VERIFY_TOKEN not set")

    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        logger.info("Webhook verified successfully")
        return hub_challenge

    logger.warning(
        "Webhook verification failed: mode=%s token_match=%s",
        hub_mode,
        hub_verify_token == verify_token,
    )
    raise HTTPException(status_code=403, detail="Forbidden: verification failed")


@app.post("/webhooks/whatsapp", summary="Receive incoming WhatsApp messages")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        logger.warning("Failed to parse webhook payload as JSON")
        return {"status": "ignored"}

    logger.info("Webhook payload received: %s", json.dumps(body))

    try:
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") != "text":
                        continue

                    from_number = msg.get("from", "")
                    text = msg.get("text", {}).get("body", "").strip().lower()

                    raw_payload_str = json.dumps(body)
                    wa_msg = WhatsAppMessage(
                        staff_whatsapp_number=from_number,
                        direction="inbound",
                        message_text=text,
                        raw_payload=raw_payload_str,
                    )

                    new_status = REPLY_MAP.get(text)

                    if new_status:
                        task = (
                            db.query(Task)
                            .filter(
                                Task.staff_whatsapp_number == from_number,
                                Task.status == "open",
                            )
                            .order_by(Task.created_at.desc())
                            .first()
                        )
                        if task:
                            task.status = new_status
                            task.updated_at = datetime.now(timezone.utc)
                            wa_msg.task_id = task.id
                            db.add(task)
                            logger.info(
                                "Task %d updated to '%s' by %s",
                                task.id,
                                new_status,
                                from_number,
                            )
                        else:
                            logger.info(
                                "No open task found for %s to update", from_number
                            )
                    else:
                        logger.info(
                            "Unrecognised reply '%s' from %s — no status update",
                            text,
                            from_number,
                        )

                    db.add(wa_msg)

        db.commit()
    except Exception as exc:
        logger.exception("Error processing webhook: %s", exc)
        db.rollback()

    return {"status": "ok"}


@app.post("/send-test-task", response_model=SendTestTaskResponse, summary="Create a test task and send WhatsApp reminder")
async def send_test_task(db: Session = Depends(get_db)):
    to_number = os.getenv("TEST_WHATSAPP_TO")
    if not to_number:
        raise HTTPException(status_code=500, detail="TEST_WHATSAPP_TO is not set")

    staff_name = "Test Staff"
    property_name = "Sunset Villa"
    task_description = "Clean and prepare all bedrooms before guest check-in"
    due_time = "14:00 today"

    task = Task(
        staff_whatsapp_number=to_number,
        property_name=property_name,
        task_description=task_description,
        due_time=due_time,
        status="open",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("Test task created: id=%d for %s", task.id, to_number)

    try:
        wa_response = await send_template_message(
            to=to_number,
            staff_name=staff_name,
            property_name=property_name,
            task_description=task_description,
            due_time=due_time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    outbound_msg = WhatsAppMessage(
        task_id=task.id,
        staff_whatsapp_number=to_number,
        direction="outbound",
        message_text=f"Template: hello_world | {staff_name} | {property_name} | {task_description} | {due_time}",
        raw_payload=json.dumps(wa_response),
    )
    db.add(outbound_msg)
    db.commit()

    return SendTestTaskResponse(
        task_id=task.id,
        whatsapp_response=wa_response,
        message=f"Test task created and WhatsApp reminder sent to {to_number}",
    )


@app.get("/tasks", response_model=List[TaskOut], summary="List all tasks (newest first)")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.created_at.desc()).all()
    return tasks


@app.post("/tasks/clear-test", summary="Delete all test tasks for Sunset Villa")
def clear_test_tasks(db: Session = Depends(get_db)):
    deleted = (
        db.query(Task)
        .filter(Task.property_name == "Sunset Villa")
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Cleared %d test task(s) for Sunset Villa", deleted)
    return {"deleted": deleted, "message": f"Deleted {deleted} test task(s) with property_name='Sunset Villa'"}


@app.post("/tasks/{task_id}/close", response_model=TaskOut, summary="Mark a task as closed/cancelled")
def close_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    task.status = "closed"
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    logger.info("Task %d marked as closed", task_id)
    return task


@app.delete("/tasks/{task_id}", summary="Delete a task by ID")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    db.delete(task)
    db.commit()
    logger.info("Task %d deleted", task_id)
    return {"deleted": task_id, "message": f"Task {task_id} deleted"}


# ---------------------------------------------------------------------------
# Hostfully endpoints
# ---------------------------------------------------------------------------

@app.get("/hostfully/test", summary="Test Hostfully API connectivity")
async def hostfully_test():
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    status_code, data = await fetch_properties(api_key, agency_uid, base_url)
    success = status_code == 200

    property_names = []
    if success:
        items = data if isinstance(data, list) else data.get("properties", data.get("results", []))
        for p in items[:3]:
            name = p.get("name") or p.get("title") or p.get("propertyName")
            if name:
                property_names.append(name)

    logger.info("Hostfully connectivity test: status=%d success=%s", status_code, success)
    return {
        "success": success,
        "status_code": status_code,
        "first_3_properties": property_names,
    }


@app.get("/hostfully/properties", summary="List all Hostfully properties")
async def hostfully_properties():
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    status_code, data = await fetch_properties(api_key, agency_uid, base_url)
    if status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Hostfully API returned {status_code}: {data}",
        )

    items = data if isinstance(data, list) else data.get("properties", data.get("results", []))

    results = []
    for p in items:
        results.append({
            "uid": p.get("uid") or p.get("id"),
            "name": p.get("name") or p.get("title") or p.get("propertyName"),
            "address": p.get("address") or p.get("addressLine1"),
            "city": p.get("city"),
            "active": p.get("active") or p.get("status"),
        })

    logger.info("Hostfully properties fetched: %d properties", len(results))
    return {"count": len(results), "properties": results}


@app.get("/hostfully/guests", summary="List first 10 Hostfully guests")
async def hostfully_guests():
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    status_code, data = await fetch_guests(api_key, agency_uid, base_url)
    if status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Hostfully API returned {status_code}: {data}",
        )

    items = data if isinstance(data, list) else data.get("guests", data.get("results", []))

    results = []
    for g in items[:10]:
        results.append({
            "uid": g.get("uid") or g.get("id"),
            "firstName": g.get("firstName") or g.get("first_name"),
            "lastName": g.get("lastName") or g.get("last_name"),
            "email": g.get("email"),
            "phone": g.get("phoneNumber") or g.get("phone"),
        })

    logger.info("Hostfully guests fetched: returning %d of %d", len(results), len(items))
    return {"count": len(results), "guests": results}
