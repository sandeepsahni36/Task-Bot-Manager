import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import FastAPI, Request, Response, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse
from supabase import Client

from schemas import TaskOut, WhatsAppMessageOut, SendTestTaskResponse
from whatsapp import send_template_message, send_text_message
from hostfully import (
    get_hostfully_config,
    fetch_properties, fetch_guests, fetch_leads,
    fetch_all_leads, fetch_all_properties,
)
from damage_cases import router as damage_router, owner_router
from checkout_inspections import router as checkout_router, hostfully_checkout_router
from supabase_client import get_supabase_client, get_supabase_dep

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="WhatsApp Task Reminder Bot",
    description="Send WhatsApp reminders to staff and track task completion.",
    version="2.0.0",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai.stayeverluxe.com",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(damage_router)
app.include_router(owner_router)
app.include_router(checkout_router)
app.include_router(hostfully_checkout_router)


@app.on_event("startup")
def on_startup():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set — "
            "all database endpoints will return 500 until both secrets are configured. "
            "Add them in Replit Secrets and restart the server."
        )
    else:
        try:
            get_supabase_client()
            logger.info("Supabase client ready")
        except Exception as exc:
            logger.error("Failed to initialise Supabase client: %s", exc)


# ---------------------------------------------------------------------------
# Webhook reply maps
# ---------------------------------------------------------------------------

TASK_REPLY_MAP = {
    "1": "completed",
    "done": "completed",
    "2": "delayed",
    "delayed": "delayed",
    "3": "issue",
    "issue": "issue",
}

CHECKOUT_VERIFICATION_REPLIES = {"1", "done", "2", "late", "3", "issue"}
INSPECTION_REPLIES = {"1", "done", "no damage", "2", "damage", "damages"}


# ---------------------------------------------------------------------------
# Health & DB checks
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, summary="Homepage")
def homepage():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Holiday Homes Ops Bot</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f1f5f9; color: #1e293b; min-height: 100vh;
           display: flex; align-items: center; justify-content: center; }
    .card { background: #fff; border-radius: 12px; padding: 48px 40px;
            box-shadow: 0 4px 24px rgba(0,0,0,.08); max-width: 560px; width: 100%; }
    .badge { display: inline-block; background: #dcfce7; color: #16a34a;
             font-size: 12px; font-weight: 600; padding: 4px 12px;
             border-radius: 999px; margin-bottom: 20px; letter-spacing: .3px; }
    h1 { font-size: 26px; font-weight: 700; margin-bottom: 8px; }
    p  { color: #64748b; font-size: 14px; margin-bottom: 32px; line-height: 1.6; }
    ul { list-style: none; display: flex; flex-direction: column; gap: 10px; }
    a  { display: flex; align-items: center; gap: 10px; padding: 12px 16px;
         background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
         text-decoration: none; color: #1e293b; font-size: 14px; font-weight: 500;
         transition: background .15s; }
    a:hover { background: #f1f5f9; border-color: #cbd5e1; }
    .icon { font-size: 18px; width: 24px; text-align: center; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">&#x25cf; Running</div>
    <h1>Holiday Homes Ops Bot</h1>
    <p>WhatsApp task reminders and damage case management for Everluxe Real Estate And Holiday Homes — powered by FastAPI and Supabase.</p>
    <ul>
      <li><a href="/docs"><span class="icon">&#128196;</span>API Docs (Swagger UI)</a></li>
      <li><a href="/dashboard-view"><span class="icon">&#128202;</span>Operations Dashboard</a></li>
      <li><a href="/owner-summary"><span class="icon">&#128203;</span>Owner Summary (JSON)</a></li>
      <li><a href="/checkout-inspections/pending"><span class="icon">&#127968;</span>Pending Checkout Inspections (JSON)</a></li>
      <li><a href="/damage-cases/pending"><span class="icon">&#9203;</span>Pending Damage Cases (JSON)</a></li>
      <li><a href="/db/health"><span class="icon">&#10003;</span>Database Health Check</a></li>
      <li><a href="/debug/routes"><span class="icon">&#128269;</span>Debug: All Routes</a></li>
    </ul>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get(
    "/db/health",
    summary="Confirm Supabase connection is working",
)
def db_health():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set.",
                "hint": "Add both secrets in Replit Secrets and restart the server.",
            },
        )
    try:
        sb = get_supabase_client()
        sb.table("tasks").select("id").limit(1).execute()
        logger.info("DB health check passed")
        return {"ok": True, "supabase_url": url}
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": str(exc)},
        )


# ---------------------------------------------------------------------------
# Debug endpoints
# ---------------------------------------------------------------------------

@app.get("/debug/routes", summary="List all registered routes and methods")
def debug_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            routes.append({
                "path": route.path,
                "methods": sorted(route.methods),
                "name": getattr(route, "name", None),
            })
    routes.sort(key=lambda r: r["path"])
    return {"route_count": len(routes), "routes": routes}


@app.get("/storage/health", summary="Check Supabase Storage bucket access")
async def storage_health():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return {
            "ok": False,
            "message": "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set.",
        }
    try:
        import httpx
        storage_url = f"{url.rstrip('/')}/storage/v1/bucket"
        headers = {"Authorization": f"Bearer {key}", "apikey": key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(storage_url, headers=headers)
        if resp.status_code == 200:
            buckets = [b.get("name") for b in resp.json() if isinstance(b, dict)]
            has_photos = "damage-photos" in buckets
            return {
                "ok": True,
                "buckets": buckets,
                "damage_photos_bucket_exists": has_photos,
                "message": "Storage accessible" if has_photos else "damage-photos bucket not found — create it in Supabase Storage.",
            }
        return {
            "ok": False,
            "status_code": resp.status_code,
            "message": f"Storage API returned {resp.status_code}",
        }
    except Exception as exc:
        logger.error("Storage health check failed: %s", exc)
        return {"ok": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# WhatsApp webhook
# ---------------------------------------------------------------------------

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
async def receive_webhook(request: Request, sb: Client = Depends(get_supabase_dep)):
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
                        logger.info("Non-text message type '%s' — skipped", msg.get("type"))
                        continue

                    from_number = msg.get("from", "")
                    text = msg.get("text", {}).get("body", "").strip().lower()
                    raw_payload_str = json.dumps(body)
                    now_str = datetime.utcnow().isoformat()
                    task_id = None

                    # ----------------------------------------------------------
                    # Priority 1: Check for an open checkout inspection assigned
                    # to this number and awaiting a reply.
                    # ----------------------------------------------------------
                    checkout_resp = (
                        sb.table("checkout_inspections")
                        .select("*")
                        .eq("assigned_ops_number", from_number)
                        .in_("status", [
                            "checkout_verification_pending",
                            "late_checkout",
                            "inspection_pending",
                        ])
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )

                    if checkout_resp.data:
                        insp = checkout_resp.data[0]
                        insp_id = insp["id"]
                        logger.info(
                            "Routing reply '%s' from %s to checkout inspection %d (status=%s)",
                            text, from_number, insp_id, insp["status"],
                        )

                        if insp["status"] == "inspection_pending":
                            # Reply 1 = no damage → closed, Reply 2 = damage found
                            if text in ("1", "done", "no damage"):
                                sb.table("checkout_inspections").update({
                                    "status": "no_damage_reported",
                                    "updated_at": now_str,
                                }).eq("id", insp_id).execute()
                                sb.table("checkout_inspections").update({
                                    "status": "closed",
                                    "updated_at": now_str,
                                    "last_message_sent_at": now_str,
                                }).eq("id", insp_id).execute()
                                try:
                                    await send_text_message(
                                        from_number,
                                        f"No damage recorded for {insp['unit_name']}. Checkout inspection closed.",
                                    )
                                except Exception as exc:
                                    logger.warning("Could not send no-damage reply: %s", exc)
                                logger.info("Inspection %d → closed (no damage) via webhook", insp_id)

                            elif text in ("2", "damage", "damages"):
                                sb.table("checkout_inspections").update({
                                    "status": "damage_reported",
                                    "updated_at": now_str,
                                    "last_message_sent_at": now_str,
                                }).eq("id", insp_id).execute()
                                try:
                                    await send_text_message(
                                        from_number,
                                        f"*Damage noted* — Unit: {insp['unit_name']}\n"
                                        f"Please create a damage case via the dashboard or:\n"
                                        f"POST /checkout-inspections/{insp_id}/damage-found",
                                    )
                                except Exception as exc:
                                    logger.warning("Could not send damage reply: %s", exc)
                                logger.info("Inspection %d → damage_reported via webhook", insp_id)
                            else:
                                logger.info(
                                    "Unrecognised reply '%s' for inspection_pending %d — expected 1 or 2",
                                    text, insp_id,
                                )

                        else:
                            # checkout_verification_pending or late_checkout
                            # Reply 1 = checked out → inspection_pending
                            # Reply 2 = still inside → late_checkout
                            # Reply 3 = issue
                            if text in ("1", "done"):
                                sb.table("checkout_inspections").update({
                                    "status": "inspection_pending",
                                    "actual_checkout_at": now_str,
                                    "updated_at": now_str,
                                    "last_message_sent_at": now_str,
                                }).eq("id", insp_id).execute()
                                try:
                                    await send_text_message(
                                        from_number,
                                        f"*Checkout Confirmed — Please Inspect*\n"
                                        f"Unit: {insp['unit_name']}\n"
                                        f"Guest: {insp.get('guest_name') or '—'} has checked out.\n\n"
                                        f"Please inspect the unit and reply:\n"
                                        f"1 No damage found\n"
                                        f"2 Damage found",
                                    )
                                except Exception as exc:
                                    logger.warning("Could not send inspection prompt: %s", exc)
                                logger.info("Inspection %d → inspection_pending via webhook", insp_id)

                            elif text in ("2", "late"):
                                followup_at = (datetime.utcnow() + timedelta(hours=2)).isoformat()
                                sb.table("checkout_inspections").update({
                                    "status": "late_checkout",
                                    "late_checkout_followup_at": followup_at,
                                    "updated_at": now_str,
                                    "last_message_sent_at": now_str,
                                }).eq("id", insp_id).execute()
                                try:
                                    await send_text_message(
                                        from_number,
                                        f"*Late Checkout* — Unit: {insp['unit_name']}\n"
                                        f"Guest: {insp.get('guest_name') or '—'}\n"
                                        f"Follow-up reminder set for 2 hours.",
                                    )
                                except Exception as exc:
                                    logger.warning("Could not send late checkout reply: %s", exc)
                                logger.info("Inspection %d → late_checkout via webhook", insp_id)

                            elif text in ("3", "issue"):
                                sb.table("checkout_inspections").update({
                                    "status": "issue",
                                    "updated_at": now_str,
                                    "last_message_sent_at": now_str,
                                }).eq("id", insp_id).execute()
                                owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")
                                if owner_number and owner_number != from_number:
                                    try:
                                        await send_text_message(
                                            owner_number,
                                            f"*Checkout Issue*\n"
                                            f"Unit: {insp['unit_name']}\n"
                                            f"Guest: {insp.get('guest_name') or '—'}\n"
                                            f"Operations has reported an issue. Please follow up.",
                                        )
                                    except Exception as exc:
                                        logger.warning("Could not notify owner for checkout issue: %s", exc)
                                logger.info("Inspection %d → issue via webhook", insp_id)
                            else:
                                logger.info(
                                    "Unrecognised checkout reply '%s' from %s for inspection %d",
                                    text, from_number, insp_id,
                                )

                        # Log the inbound message linked to no task_id (it's a checkout inspection reply)
                        sb.table("whatsapp_messages").insert({
                            "task_id": None,
                            "staff_whatsapp_number": from_number,
                            "direction": "inbound",
                            "message_text": text,
                            "raw_payload": raw_payload_str,
                            "created_at": now_str,
                        }).execute()
                        continue  # Do not fall through to task routing

                    # ----------------------------------------------------------
                    # Priority 2: Task reply routing (existing behaviour)
                    # ----------------------------------------------------------
                    new_status = TASK_REPLY_MAP.get(text)

                    if new_status:
                        task_resp = (
                            sb.table("tasks")
                            .select("*")
                            .eq("staff_whatsapp_number", from_number)
                            .eq("status", "open")
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if task_resp.data:
                            task = task_resp.data[0]
                            task_id = task["id"]
                            sb.table("tasks").update({
                                "status": new_status,
                                "updated_at": now_str,
                            }).eq("id", task_id).execute()
                            logger.info(
                                "Task %d updated to '%s' by %s",
                                task_id, new_status, from_number,
                            )
                        else:
                            logger.info("No open task found for %s to update", from_number)
                    else:
                        logger.info(
                            "Unrecognised reply '%s' from %s — no checkout inspection or task to update",
                            text, from_number,
                        )

                    sb.table("whatsapp_messages").insert({
                        "task_id": task_id,
                        "staff_whatsapp_number": from_number,
                        "direction": "inbound",
                        "message_text": text,
                        "raw_payload": raw_payload_str,
                        "created_at": now_str,
                    }).execute()

    except Exception as exc:
        logger.exception("Error processing webhook: %s", exc)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------

@app.post("/send-test-task", response_model=SendTestTaskResponse, summary="Create a test task and send WhatsApp reminder")
async def send_test_task(sb: Client = Depends(get_supabase_dep)):
    to_number = os.getenv("TEST_WHATSAPP_TO")
    if not to_number:
        raise HTTPException(status_code=500, detail="TEST_WHATSAPP_TO is not set")

    staff_name = "Test Staff"
    property_name = "Sunset Villa"
    task_description = "Clean and prepare all bedrooms before guest check-in"
    due_time = "14:00 today"
    now = datetime.utcnow().isoformat()

    task_resp = sb.table("tasks").insert({
        "staff_whatsapp_number": to_number,
        "property_name": property_name,
        "task_description": task_description,
        "due_time": due_time,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }).execute()
    task = task_resp.data[0]
    logger.info("Test task created: id=%d for %s", task["id"], to_number)

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

    sb.table("whatsapp_messages").insert({
        "task_id": task["id"],
        "staff_whatsapp_number": to_number,
        "direction": "outbound",
        "message_text": f"Template: hello_world | {staff_name} | {property_name} | {task_description} | {due_time}",
        "raw_payload": json.dumps(wa_response),
        "created_at": datetime.utcnow().isoformat(),
    }).execute()

    return SendTestTaskResponse(
        task_id=task["id"],
        whatsapp_response=wa_response,
        message=f"Test task created and WhatsApp reminder sent to {to_number}",
    )


@app.get("/tasks", response_model=List[TaskOut], summary="List all tasks (newest first)")
def list_tasks(sb: Client = Depends(get_supabase_dep)):
    resp = sb.table("tasks").select("*").order("created_at", desc=True).execute()
    return [TaskOut.model_validate(r) for r in resp.data]


@app.post("/tasks/clear-test", summary="Delete all test tasks for Sunset Villa")
def clear_test_tasks(sb: Client = Depends(get_supabase_dep)):
    resp = (
        sb.table("tasks")
        .delete()
        .eq("property_name", "Sunset Villa")
        .execute()
    )
    deleted = len(resp.data) if resp.data else 0
    logger.info("Cleared %d test task(s) for Sunset Villa", deleted)
    return {"deleted": deleted, "message": f"Deleted {deleted} test task(s) with property_name='Sunset Villa'"}


@app.post("/tasks/{task_id}/close", response_model=TaskOut, summary="Mark a task as closed/cancelled")
def close_task(task_id: int, sb: Client = Depends(get_supabase_dep)):
    check = sb.table("tasks").select("id").eq("id", task_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    resp = sb.table("tasks").update({
        "status": "closed",
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", task_id).execute()
    logger.info("Task %d marked as closed", task_id)
    return TaskOut.model_validate(resp.data[0])


@app.delete("/tasks/{task_id}", summary="Delete a task by ID")
def delete_task(task_id: int, sb: Client = Depends(get_supabase_dep)):
    check = sb.table("tasks").select("id").eq("id", task_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    sb.table("tasks").delete().eq("id", task_id).execute()
    logger.info("Task %d deleted", task_id)
    return {"deleted": task_id, "message": f"Task {task_id} deleted"}


# ---------------------------------------------------------------------------
# Hostfully endpoints
# ---------------------------------------------------------------------------

@app.get("/hostfully/test", summary="Test Hostfully API connectivity (properties)")
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


@app.get("/hostfully/leads-test", summary="Test Hostfully leads/reservations endpoint (all pages, cursor paginated)")
async def hostfully_leads_test():
    """
    Fetches ALL leads via _limit/_nextCursor cursor pagination.
    Returns totals, cursor field names, per-record summaries, and raw first record.
    Never exposes the API key or agencyUid.
    """
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    try:
        all_leads, pages_fetched, last_raw = await fetch_all_leads(api_key, agency_uid, base_url)
    except Exception as exc:
        logger.error("Hostfully leads-test fetch failed: %s", exc)
        return {"success": False, "error": f"Request failed: {exc}"}

    # Surface the raw top-level keys and paging info for diagnostics
    top_level_keys = list(last_raw.keys()) if isinstance(last_raw, dict) else []
    paging_raw = last_raw.get("_paging") if isinstance(last_raw, dict) else None
    metadata_raw = last_raw.get("_metadata") if isinstance(last_raw, dict) else None
    cursor_fields_found = [k for k in (paging_raw or {}) if "cursor" in k.lower()]

    def _summarise(lead: dict) -> dict:
        uid = lead.get("uid") or lead.get("id")
        prop = lead.get("propertyUid") or lead.get("propertyId")
        checkin = (
            lead.get("checkInZonedDateTime") or lead.get("checkInLocalDateTime")
            or lead.get("checkInDate") or lead.get("checkinDate") or lead.get("startDate")
        )
        checkout = (
            lead.get("checkOutZonedDateTime") or lead.get("checkOutLocalDateTime")
            or lead.get("checkOutDate") or lead.get("checkoutDate") or lead.get("endDate")
        )
        gi = lead.get("guestInformation") or {}
        first = gi.get("firstName") or lead.get("guestFirstName") or lead.get("firstName") or ""
        last = gi.get("lastName") or lead.get("guestLastName") or lead.get("lastName") or ""
        guest = gi.get("fullName") or f"{first} {last}".strip() or lead.get("guestName") or "—"
        return {
            "uid": uid,
            "property_uid": prop,
            "guest": guest,
            "check_in": checkin,
            "check_out": checkout,
            "status": lead.get("status") or "—",
        }

    # Checkouts on 2026-05-21 Dubai time (UTC+4) — date starts with that string
    TARGET_DATE = "2026-05-21"
    may_21_checkouts = []
    for lead in all_leads:
        if (lead.get("status") or "").upper() in ("CANCELLED", "DECLINED", "EXPIRED"):
            continue
        co = (
            lead.get("checkOutZonedDateTime") or lead.get("checkOutLocalDateTime")
            or lead.get("checkOutDate") or ""
        )
        if co.startswith(TARGET_DATE):
            may_21_checkouts.append(_summarise(lead))

    # Redact sensitive fields from first raw record
    raw_first: dict = {}
    if all_leads:
        raw_first = dict(all_leads[0])
        for k in ("agencyUid", "externalBookingId"):
            if k in raw_first:
                raw_first[k] = "<redacted>"

    field_names = list(all_leads[0].keys()) if all_leads else []

    logger.info("Hostfully leads-test: total=%d pages=%d", len(all_leads), pages_fetched)
    return {
        "success": True,
        "total_records_fetched": len(all_leads),
        "pages_fetched": pages_fetched,
        "records_per_page": 100,
        "response_top_level_keys": top_level_keys,
        "paging_info": paging_raw,
        "metadata_info": metadata_raw,
        "cursor_fields_found": cursor_fields_found,
        "first_3_summaries": [_summarise(l) for l in all_leads[:3]],
        "may_21_checkouts": may_21_checkouts,
        "available_field_names": field_names,
        "first_record_raw": raw_first,
    }


@app.get("/hostfully/properties", summary="List all Hostfully properties (cursor paginated)")
async def hostfully_properties():
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    try:
        all_props, pages_fetched, _last_raw = await fetch_all_properties(api_key, agency_uid, base_url)
    except Exception as exc:
        logger.error("Hostfully properties fetch failed: %s", exc)
        return {"success": False, "error": f"Request failed: {exc}"}

    results = []
    for p in all_props:
        results.append({
            "uid": p.get("uid") or p.get("id"),
            "name": p.get("name") or p.get("title") or p.get("propertyName"),
            "address": p.get("address") or p.get("addressLine1"),
            "city": p.get("city"),
            "active": p.get("active") or p.get("status"),
        })

    note = None
    if len(results) <= 20:
        note = (
            f"Hostfully returned only {len(results)} properties with this API key/agency UID. "
            "This may be a property-level permission restriction — check active/published status in Hostfully."
        )

    logger.info("Hostfully properties fetched: %d in %d pages", len(results), pages_fetched)
    return {
        "success": True,
        "count": len(results),
        "pages_fetched": pages_fetched,
        "note": note,
        "properties": results,
    }


@app.get("/hostfully/access-diagnostics", summary="Full Hostfully API access diagnostics")
async def hostfully_access_diagnostics():
    """
    Runs paginated fetches for both properties and leads, then returns a
    comprehensive diagnostic report — cursor fields, top-level keys, totals,
    duplicate-page detection, and sample records. Useful for verifying API
    key permissions and understanding the response envelope.
    """
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    safe_agency = agency_uid[:8] + "…" if agency_uid else "?"

    # --- Properties ---
    props_error = None
    all_props: list = []
    props_pages = 0
    props_top_keys: list = []
    props_paging: dict = {}
    props_cursor_fields: list = []
    props_duplicate_detected = False

    try:
        all_props, props_pages, props_last_raw = await fetch_all_properties(api_key, agency_uid, base_url)
        props_top_keys = list(props_last_raw.keys()) if isinstance(props_last_raw, dict) else []
        props_paging = props_last_raw.get("_paging") if isinstance(props_last_raw, dict) else {}
        props_cursor_fields = [k for k in (props_paging or {}) if "cursor" in k.lower()]
    except Exception as exc:
        props_error = str(exc)

    # --- Leads ---
    leads_error = None
    all_leads: list = []
    leads_pages = 0
    leads_top_keys: list = []
    leads_paging: dict = {}
    leads_cursor_fields: list = []
    leads_duplicate_detected = False

    try:
        all_leads, leads_pages, leads_last_raw = await fetch_all_leads(api_key, agency_uid, base_url)
        leads_top_keys = list(leads_last_raw.keys()) if isinstance(leads_last_raw, dict) else []
        leads_paging = leads_last_raw.get("_paging") if isinstance(leads_last_raw, dict) else {}
        leads_cursor_fields = [k for k in (leads_paging or {}) if "cursor" in k.lower()]
        # Duplicate detection: if any uid appears more than once across all pages
        uid_counts: dict = {}
        for lead in all_leads:
            uid = lead.get("uid") or lead.get("id")
            if uid:
                uid_counts[uid] = uid_counts.get(uid, 0) + 1
        leads_duplicate_detected = any(v > 1 for v in uid_counts.values())
    except Exception as exc:
        leads_error = str(exc)

    # Duplicate detection for properties
    prop_uid_counts: dict = {}
    for p in all_props:
        uid = p.get("uid") or p.get("id")
        if uid:
            prop_uid_counts[uid] = prop_uid_counts.get(uid, 0) + 1
    props_duplicate_detected = any(v > 1 for v in prop_uid_counts.values())

    def _lead_summary(lead: dict) -> dict:
        gi = lead.get("guestInformation") or {}
        first = gi.get("firstName") or ""
        last = gi.get("lastName") or ""
        return {
            "uid": lead.get("uid"),
            "property_uid": lead.get("propertyUid"),
            "guest": f"{first} {last}".strip() or "—",
            "check_out": lead.get("checkOutZonedDateTime") or lead.get("checkOutLocalDateTime"),
            "status": lead.get("status"),
        }

    props_note = None
    if len(all_props) <= 20 and not props_error:
        props_note = (
            f"Hostfully returned only {len(all_props)} properties with this API key/agency UID. "
            "This may be a property-level permission restriction — check active/published status in Hostfully."
        )

    logger.info(
        "access-diagnostics: props=%d props_pages=%d leads=%d leads_pages=%d",
        len(all_props), props_pages, len(all_leads), leads_pages,
    )
    return {
        "success": True,
        "agency_uid_prefix": safe_agency,
        "base_url": base_url,
        "raw_urls_called": {
            "properties": f"{base_url}/properties?agencyUid=<hidden>&_limit=100",
            "leads": f"{base_url}/leads?agencyUid=<hidden>&_limit=100",
        },
        "properties": {
            "count": len(all_props),
            "pages_fetched": props_pages,
            "duplicate_pages_detected": props_duplicate_detected,
            "response_top_level_keys": props_top_keys,
            "paging_info": props_paging,
            "cursor_fields_found": props_cursor_fields,
            "first_5_names": [
                (p.get("name") or p.get("title") or p.get("propertyName") or "?")
                for p in all_props[:5]
            ],
            "note": props_note,
            "error": props_error,
        },
        "leads": {
            "count": len(all_leads),
            "pages_fetched": leads_pages,
            "duplicate_pages_detected": leads_duplicate_detected,
            "response_top_level_keys": leads_top_keys,
            "paging_info": leads_paging,
            "cursor_fields_found": leads_cursor_fields,
            "first_5_summaries": [_lead_summary(l) for l in all_leads[:5]],
            "error": leads_error,
        },
    }


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
