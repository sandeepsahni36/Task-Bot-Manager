import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from supabase import Client

from schemas import (
    DamageCaseCreate,
    DamageCaseOut,
    DamageCaseDetail,
    DamagePhotoOut,
    DamageEventOut,
    QuoteBody,
    PhotoBody,
)
from supabase_client import get_supabase_dep
from whatsapp import send_text_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status display constants
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    "quote_pending":           "Quote Pending",
    "tenant_approval_pending": "Tenant Approval Pending",
    "gm_action_pending":       "GM Action Pending",
    "placement_proof_pending": "Placement Proof Pending",
    "accounts_refund_pending": "Accounts Refund Pending",
    "closed":                  "Closed",
    "cancelled":               "Cancelled",
}

STATUS_COLORS = {
    "quote_pending":           "#f59e0b",
    "tenant_approval_pending": "#3b82f6",
    "gm_action_pending":       "#8b5cf6",
    "placement_proof_pending": "#06b6d4",
    "accounts_refund_pending": "#f97316",
    "closed":                  "#22c55e",
    "cancelled":               "#6b7280",
}

router = APIRouter(prefix="/damage-cases", tags=["Damage Cases"])

# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    """Current UTC time as ISO string for Supabase inserts/updates."""
    return datetime.utcnow().isoformat()


def _due_at(hours: int) -> str:
    """ISO string for now + N hours (naive UTC, stored as TIMESTAMPTZ)."""
    return (datetime.utcnow() + timedelta(hours=hours)).isoformat()


def _parse_dt(val) -> datetime | None:
    """Convert a Supabase timestamp string or datetime object to naive UTC datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo is not None else val
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
        except ValueError:
            return None
    return None

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_case_or_404(sb: Client, case_id: int) -> dict:
    resp = sb.table("damage_cases").select("*").eq("id", case_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail=f"Damage case {case_id} not found")
    return resp.data[0]


def _require_status(case: dict, required: str, extra_check: str = None):
    if case["status"] != required:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid workflow step — case is not in the required status.",
                "current_status": case["status"],
                "required_status": required,
            },
        )
    if extra_check:
        raise HTTPException(status_code=400, detail={"error": extra_check})


def _add_event(
    sb: Client,
    case_id: int,
    event_type: str,
    message: str,
    whatsapp_number: str = None,
):
    sb.table("damage_events").insert({
        "damage_case_id": case_id,
        "event_type": event_type,
        "message": message,
        "whatsapp_number": whatsapp_number,
        "created_at": _now_utc(),
    }).execute()


def _update_case(sb: Client, case_id: int, fields: dict) -> dict:
    """Apply a partial update and return the updated row."""
    fields["updated_at"] = _now_utc()
    resp = sb.table("damage_cases").update(fields).eq("id", case_id).execute()
    return resp.data[0]

# ---------------------------------------------------------------------------
# Routes — must register fixed paths before /{case_id}
# ---------------------------------------------------------------------------

@router.post("", response_model=DamageCaseOut, summary="Create a new damage case")
async def create_damage_case(
    body: DamageCaseCreate,
    sb: Client = Depends(get_supabase_dep),
):
    if not body.unit_name and not body.hostfully_property_uid:
        raise HTTPException(
            status_code=400,
            detail="Provide either unit_name or hostfully_property_uid",
        )

    now = _now_utc()
    case_data = {
        "hostfully_property_uid": body.hostfully_property_uid,
        "hostfully_guest_uid": body.hostfully_guest_uid,
        "unit_name": body.unit_name or body.hostfully_property_uid,
        "guest_name": body.guest_name,
        "guest_phone": body.guest_phone,
        "guest_email": body.guest_email,
        "damage_description": body.damage_description,
        "deposit_amount": body.deposit_amount,
        "damage_amount": 0.0,
        "other_charges": 0.0,
        "refund_amount": None,
        "status": "quote_pending",
        "waiting_on": "GM",
        "reported_by_number": body.reported_by_number,
        "gm_number": body.gm_number,
        "ops_supervisor_number": body.ops_supervisor_number,
        "reservations_number": body.reservations_number,
        "accounts_number": body.accounts_number,
        "photo_proof_received": False,
        "due_at": _due_at(24),
        "created_at": now,
        "updated_at": now,
    }
    resp = sb.table("damage_cases").insert(case_data).execute()
    case = resp.data[0]

    msg = (
        f"*New Damage Case #{case['id']}*\n"
        f"Unit: {case['unit_name']}\n"
        f"Guest: {case['guest_name']}\n"
        f"Damage: {case['damage_description']}\n"
        f"Deposit: AED {case['deposit_amount']:.2f}\n\n"
        f"Please provide a damage quote (repair/replacement cost) as soon as possible."
    )
    await send_text_message(case["gm_number"], msg)
    _add_event(sb, case["id"], "case_created", msg, case["gm_number"])
    logger.info("Damage case %d created, GM notified at %s", case["id"], case["gm_number"])

    return DamageCaseOut.model_validate(case)


@router.get("", response_model=List[DamageCaseOut], summary="List all damage cases (newest first)")
def list_damage_cases(sb: Client = Depends(get_supabase_dep)):
    resp = sb.table("damage_cases").select("*").order("created_at", desc=True).execute()
    return [DamageCaseOut.model_validate(r) for r in resp.data]


@router.get("/pending", response_model=List[DamageCaseOut], summary="List pending (non-closed) damage cases")
def list_pending_cases(sb: Client = Depends(get_supabase_dep)):
    resp = (
        sb.table("damage_cases")
        .select("*")
        .neq("status", "closed")
        .neq("status", "cancelled")
        .order("created_at", desc=True)
        .execute()
    )
    return [DamageCaseOut.model_validate(r) for r in resp.data]


@router.post(
    "/check-overdue",
    summary="Notify GM + Ops Supervisor + Owner for every overdue gm_action_pending case",
    description=(
        "Finds all damage cases in status `gm_action_pending` whose `due_at` has passed "
        "and sends a WhatsApp escalation to the GM, Ops Supervisor, and the Owner "
        "(OWNER_WHATSAPP_NUMBER). Each recipient receives at most one escalation per case "
        "every 6 hours — safe to call hourly without spamming. Each notification is recorded "
        "as a `DamageEvent` (type `overdue_escalation`) so the audit trail is complete."
    ),
)
async def check_overdue(sb: Client = Depends(get_supabase_dep)):
    owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")
    now_naive = datetime.utcnow()
    throttle_window = timedelta(hours=6)

    all_gm = sb.table("damage_cases").select("*").eq("status", "gm_action_pending").execute().data
    overdue_cases = [
        c for c in all_gm
        if c.get("due_at") and _parse_dt(c["due_at"]) is not None
        and _parse_dt(c["due_at"]) < now_naive
    ]

    notified = []
    errors = []
    skipped_due_to_throttle = 0

    for case in overdue_cases:
        due = _parse_dt(case["due_at"])
        hours_overdue = round((now_naive - due).total_seconds() / 3600, 1)

        msg = (
            f"*⚠ Overdue Damage Case #{case['id']}*\n"
            f"Unit: {case['unit_name']} | Guest: {case['guest_name']}\n"
            f"Damage: {case['damage_description']}\n"
            f"Damage amount: AED {(case.get('damage_amount') or 0):.2f}\n"
            f"Overdue by: {hours_overdue}h\n\n"
            f"This case is awaiting GM action (purchase + placement of replacement item). "
            f"Please action this immediately."
        )

        recipients = [
            ("gm", case["gm_number"]),
            ("ops_supervisor", case["ops_supervisor_number"]),
        ]
        if owner_number:
            recipients.append(("owner", owner_number))

        case_notified = []
        for role, number in recipients:
            last_resp = (
                sb.table("damage_events")
                .select("created_at")
                .eq("damage_case_id", case["id"])
                .eq("event_type", "overdue_escalation")
                .eq("whatsapp_number", number)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if last_resp.data:
                last_sent = _parse_dt(last_resp.data[0]["created_at"])
                if last_sent and (now_naive - last_sent) < throttle_window:
                    hours_since = round((now_naive - last_sent).total_seconds() / 3600, 1)
                    logger.info(
                        "Case %d throttle: skipping %s (%s) — last sent %.1fh ago",
                        case["id"], number, role, hours_since,
                    )
                    skipped_due_to_throttle += 1
                    continue

            try:
                await send_text_message(number, msg)
                _add_event(sb, case["id"], "overdue_escalation", msg, number)
                case_notified.append({"role": role, "number": number})
                logger.info(
                    "Case %d overdue escalation sent to %s (%s)", case["id"], number, role,
                )
            except Exception as exc:
                err = f"Case {case['id']} → {role} ({number}): {exc}"
                errors.append(err)
                logger.error("Overdue escalation failed: %s", err)

        if case_notified:
            notified.append({
                "case_id": case["id"],
                "unit_name": case["unit_name"],
                "guest_name": case["guest_name"],
                "hours_overdue": hours_overdue,
                "notified": case_notified,
            })

    logger.info(
        "check-overdue: %d notification(s) sent, %d throttled, %d error(s)",
        sum(len(n["notified"]) for n in notified), skipped_due_to_throttle, len(errors),
    )
    return {
        "checked_status": "gm_action_pending",
        "overdue_count": len(overdue_cases),
        "notified": notified,
        "skipped_due_to_throttle": skipped_due_to_throttle,
        "errors": errors,
    }


@router.get("/{case_id}", response_model=DamageCaseDetail, summary="Get one damage case with events and photos")
def get_damage_case(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    events = (
        sb.table("damage_events")
        .select("*")
        .eq("damage_case_id", case_id)
        .order("created_at")
        .execute()
        .data
    )
    photos = (
        sb.table("damage_photos")
        .select("*")
        .eq("damage_case_id", case_id)
        .order("created_at")
        .execute()
        .data
    )
    return DamageCaseDetail.model_validate({**case, "events": events, "photos": photos})


@router.post("/{case_id}/quote", response_model=DamageCaseOut, summary="Submit damage quote")
async def submit_quote(
    case_id: int,
    body: QuoteBody,
    sb: Client = Depends(get_supabase_dep),
):
    case = _get_case_or_404(sb, case_id)
    _require_status(case, "quote_pending")

    refund = case["deposit_amount"] - body.damage_amount - body.other_charges
    updated = _update_case(sb, case_id, {
        "damage_amount": body.damage_amount,
        "other_charges": body.other_charges,
        "refund_amount": refund,
        "status": "tenant_approval_pending",
        "waiting_on": "Reservations/Ops",
        "due_at": _due_at(24),
    })

    notes_line = f"\nNotes: {body.notes}" if body.notes else ""
    msg = (
        f"*Damage Case #{case_id} – Quote Received*\n"
        f"Unit: {updated['unit_name']} | Guest: {updated['guest_name']}\n"
        f"Damage: AED {updated['damage_amount']:.2f}\n"
        f"Other charges: AED {updated['other_charges']:.2f}\n"
        f"Refund to guest: AED {updated['refund_amount']:.2f}{notes_line}\n\n"
        f"Please share these charges with the tenant and confirm their approval."
    )
    await send_text_message(updated["reservations_number"], msg)
    _add_event(sb, case_id, "quote_submitted", msg, updated["reservations_number"])
    logger.info("Case %d quote submitted, reservations notified", case_id)
    return DamageCaseOut.model_validate(updated)


@router.post("/{case_id}/tenant-approved", response_model=DamageCaseOut, summary="Mark tenant as approved")
async def tenant_approved(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    _require_status(case, "tenant_approval_pending")
    if case.get("refund_amount") is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Cannot mark tenant as approved — refund_amount is not set. Submit a quote first.",
                "current_status": case["status"],
                "required_status": "tenant_approval_pending",
            },
        )

    updated = _update_case(sb, case_id, {
        "status": "gm_action_pending",
        "waiting_on": "GM + Ops Supervisor",
        "due_at": _due_at(46),
    })

    msg = (
        f"*Damage Case #{case_id} – Tenant Approved*\n"
        f"Unit: {updated['unit_name']} | Guest: {updated['guest_name']}\n"
        f"Damage amount: AED {(updated.get('damage_amount') or 0):.2f}\n\n"
        f"Tenant has approved the charges. Please purchase and place the replacement item, "
        f"then send photo proof once placed."
    )
    await send_text_message(updated["gm_number"], msg)
    await send_text_message(updated["ops_supervisor_number"], msg)
    _add_event(sb, case_id, "tenant_approved", msg, updated["gm_number"])
    _add_event(sb, case_id, "tenant_approved", msg, updated["ops_supervisor_number"])
    logger.info("Case %d tenant approved, GM and Ops notified", case_id)
    return DamageCaseOut.model_validate(updated)


@router.post("/{case_id}/gm-purchased", response_model=DamageCaseOut, summary="GM confirms item purchased")
async def gm_purchased(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    _require_status(case, "gm_action_pending")

    updated = _update_case(sb, case_id, {
        "status": "placement_proof_pending",
        "waiting_on": "GM",
        "due_at": _due_at(24),
    })

    msg = (
        f"*Damage Case #{case_id} – Item Purchased*\n"
        f"Unit: {updated['unit_name']}\n\n"
        f"Item has been purchased. Please place the item in the unit and send a photo as proof of placement."
    )
    await send_text_message(updated["gm_number"], msg)
    _add_event(sb, case_id, "gm_purchased", msg, updated["gm_number"])
    logger.info("Case %d GM purchased, awaiting placement proof", case_id)
    return DamageCaseOut.model_validate(updated)


@router.post("/{case_id}/photo", response_model=DamagePhotoOut, summary="Upload photo proof")
async def add_photo(case_id: int, body: PhotoBody, sb: Client = Depends(get_supabase_dep)):
    _get_case_or_404(sb, case_id)

    photo_resp = sb.table("damage_photos").insert({
        "damage_case_id": case_id,
        "photo_url_or_media_id": body.photo_url_or_media_id,
        "photo_type": body.photo_type,
        "created_at": _now_utc(),
    }).execute()
    photo = photo_resp.data[0]

    _update_case(sb, case_id, {"photo_proof_received": True})
    _add_event(
        sb, case_id, "photo_uploaded",
        f"Photo uploaded: type={body.photo_type} ref={body.photo_url_or_media_id}",
    )
    logger.info("Case %d photo uploaded: type=%s", case_id, body.photo_type)
    return DamagePhotoOut.model_validate(photo)


@router.post(
    "/{case_id}/replacement-placed",
    response_model=DamageCaseOut,
    summary="Confirm replacement placed — notifies Accounts",
)
async def replacement_placed(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    _require_status(case, "placement_proof_pending")
    if not case.get("photo_proof_received"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Photo proof is required before sending to accounts.",
                "current_status": case["status"],
                "required_status": "placement_proof_pending",
            },
        )

    updated = _update_case(sb, case_id, {
        "status": "accounts_refund_pending",
        "waiting_on": "Accounts",
        "due_at": _due_at(24),
    })

    refund = updated.get("refund_amount") or 0.0
    msg = (
        f"*Damage Case #{case_id} – Ready for Refund Processing*\n"
        f"Unit: {updated['unit_name']}\n"
        f"Guest: {updated['guest_name']}"
        + (f" | {updated['guest_email']}" if updated.get("guest_email") else "")
        + (f" | {updated['guest_phone']}" if updated.get("guest_phone") else "") + "\n"
        f"Deposit held: AED {updated['deposit_amount']:.2f}\n"
        f"Damage charges: AED {(updated.get('damage_amount') or 0):.2f}\n"
        f"Other charges: AED {(updated.get('other_charges') or 0):.2f}\n"
        f"*Refund to guest: AED {refund:.2f}*\n\n"
        f"Replacement item has been placed and photo proof received. "
        f"Please process the refund to the guest."
    )
    await send_text_message(updated["accounts_number"], msg)
    _add_event(sb, case_id, "sent_to_accounts", msg, updated["accounts_number"])
    logger.info("Case %d sent to accounts for refund", case_id)
    return DamageCaseOut.model_validate(updated)


@router.post(
    "/{case_id}/refund-completed",
    response_model=DamageCaseOut,
    summary="Mark refund as completed — closes case",
)
def refund_completed(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    _require_status(case, "accounts_refund_pending")

    updated = _update_case(sb, case_id, {
        "status": "closed",
        "waiting_on": None,
        "due_at": None,
        "closed_at": _now_utc(),
    })
    _add_event(sb, case_id, "case_closed", "Refund completed. Case closed.")
    logger.info("Case %d closed", case_id)
    return DamageCaseOut.model_validate(updated)


@router.post("/{case_id}/cancel", response_model=DamageCaseOut, summary="Cancel a damage case")
def cancel_case(case_id: int, sb: Client = Depends(get_supabase_dep)):
    case = _get_case_or_404(sb, case_id)
    if case["status"] in ("closed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Case is already {case['status']}.",
        )

    updated = _update_case(sb, case_id, {
        "status": "cancelled",
        "waiting_on": None,
        "due_at": None,
    })
    _add_event(sb, case_id, "case_cancelled", "Case cancelled.")
    logger.info("Case %d cancelled", case_id)
    return DamageCaseOut.model_validate(updated)


# ---------------------------------------------------------------------------
# Owner summary & dashboard
# ---------------------------------------------------------------------------

owner_router = APIRouter(tags=["Owner Dashboard"])


def _build_summary(sb: Client) -> dict:
    now_naive = datetime.utcnow()
    today_start = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)

    all_pending = (
        sb.table("damage_cases")
        .select("*")
        .neq("status", "closed")
        .neq("status", "cancelled")
        .execute()
        .data
    )

    overdue = [
        c for c in all_pending
        if c.get("due_at") and _parse_dt(c["due_at"]) and _parse_dt(c["due_at"]) < now_naive
    ]

    def waiting(label):
        return len([c for c in all_pending if c.get("waiting_on") and label.lower() in c["waiting_on"].lower()])

    closed_today = len(
        sb.table("damage_cases")
        .select("id")
        .eq("status", "closed")
        .gte("closed_at", today_start.isoformat() + "+00:00")
        .execute()
        .data
    )

    oldest_open = (
        sb.table("damage_cases")
        .select("*")
        .neq("status", "closed")
        .neq("status", "cancelled")
        .order("created_at")
        .limit(10)
        .execute()
        .data
    )

    return {
        "total_pending": len(all_pending),
        "overdue": len(overdue),
        "waiting_on_gm": waiting("GM"),
        "waiting_on_ops_supervisor": waiting("Ops"),
        "waiting_on_reservations": waiting("Reservations"),
        "waiting_on_accounts": waiting("Accounts"),
        "missing_photo_proof": len([c for c in all_pending if not c.get("photo_proof_received")]),
        "closed_today": closed_today,
        "top_10_oldest_open": [
            {
                "id": c["id"],
                "unit_name": c["unit_name"],
                "guest_name": c["guest_name"],
                "status": c["status"],
                "waiting_on": c.get("waiting_on"),
                "age_days": (now_naive - _parse_dt(c["created_at"])).days
                if c.get("created_at") and _parse_dt(c["created_at"]) else None,
            }
            for c in oldest_open
        ],
    }


@owner_router.get("/owner-summary", summary="High-level summary for the owner")
def owner_summary(sb: Client = Depends(get_supabase_dep)):
    return _build_summary(sb)


@owner_router.post(
    "/send-owner-summary",
    summary="Send owner summary via WhatsApp",
    description=(
        "Builds the same data as GET /owner-summary and sends it as a WhatsApp message "
        "to the number stored in the OWNER_WHATSAPP_NUMBER secret."
    ),
)
async def send_owner_summary(sb: Client = Depends(get_supabase_dep)):
    owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")
    if not owner_number:
        raise HTTPException(status_code=500, detail="OWNER_WHATSAPP_NUMBER is not set")

    s = _build_summary(sb)

    oldest_lines = ""
    for i, c in enumerate(s["top_10_oldest_open"][:5], start=1):
        age = f"{c['age_days']}d" if c["age_days"] is not None else "—"
        oldest_lines += (
            f"  {i}. #{c['id']} {c['unit_name']} – {c['guest_name']} "
            f"({STATUS_LABELS.get(c['status'], c['status'])}, "
            f"waiting: {c['waiting_on'] or '—'}, age: {age})\n"
        )

    msg = (
        f"*Damage Cases Summary*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total pending: {s['total_pending']}\n"
        f"⏰ Overdue: {s['overdue']}\n"
        f"👤 Waiting on GM: {s['waiting_on_gm']}\n"
        f"🔧 Waiting on Ops: {s['waiting_on_ops_supervisor']}\n"
        f"🏠 Waiting on Reservations: {s['waiting_on_reservations']}\n"
        f"💰 Waiting on Accounts: {s['waiting_on_accounts']}\n"
        f"📷 Missing photo proof: {s['missing_photo_proof']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"*Top 5 Oldest Open Cases:*\n"
        f"{oldest_lines if oldest_lines else '  None'}"
    )

    wa_result = await send_text_message(owner_number, msg)
    logger.info("Owner summary sent to %s", owner_number)
    return {"sent_to": owner_number, "summary": s, "whatsapp_result": wa_result}


@owner_router.get("/dashboard-view", response_class=HTMLResponse, summary="HTML dashboard grouped by status")
def dashboard_view(sb: Client = Depends(get_supabase_dep)):
    rows_raw = sb.table("damage_cases").select("*").order("created_at", desc=True).execute().data
    cases = [SimpleNamespace(**r) for r in rows_raw]
    now = datetime.utcnow()

    grouped: dict[str, list] = {s: [] for s in STATUS_LABELS}
    for c in cases:
        grouped.setdefault(c.status, []).append(c)

    def age(c):
        dt = _parse_dt(c.created_at)
        if not dt:
            return "—"
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d"
        return f"{delta.seconds // 3600}h"

    def fmt_money(v):
        return f"AED {v:.2f}" if v is not None else "—"

    def photo_badge(v):
        return '<span style="color:#22c55e">✓</span>' if v else '<span style="color:#ef4444">✗</span>'

    def fmt_due(c):
        if c.status in ("closed", "cancelled") or not c.due_at:
            return "—"
        due = _parse_dt(c.due_at)
        if not due:
            return "—"
        overdue = due < now
        diff = abs(due - now)
        total_mins = int(diff.total_seconds() // 60)
        if total_mins < 60:
            label = f"{total_mins}m"
        elif total_mins < 1440:
            label = f"{total_mins // 60}h {total_mins % 60}m"
        else:
            label = f"{diff.days}d {(total_mins % 1440) // 60}h"
        if overdue:
            return f'<span style="color:#ef4444;font-weight:600">⚠ {label} ago</span>'
        return f'<span style="color:#16a34a">{label} left</span>'

    overdue_count = sum(
        1 for c in cases
        if c.status not in ("closed", "cancelled")
        and c.due_at
        and _parse_dt(c.due_at) is not None
        and _parse_dt(c.due_at) < now
    )

    rows_html = ""
    for status_key, label in STATUS_LABELS.items():
        group = grouped.get(status_key, [])
        color = STATUS_COLORS.get(status_key, "#6b7280")
        rows_html += f"""
        <tr>
          <td colspan="15" style="background:{color};color:#fff;font-weight:600;
              padding:8px 12px;font-size:13px;letter-spacing:.5px">
            {label} &nbsp;({len(group)})
          </td>
        </tr>"""
        if not group:
            rows_html += """
        <tr><td colspan="15" style="color:#9ca3af;padding:8px 12px;font-style:italic">No cases</td></tr>"""
        for c in group:
            updated_dt = _parse_dt(c.updated_at)
            updated_str = updated_dt.strftime("%d %b %H:%M") if updated_dt else "—"
            rows_html += f"""
        <tr>
          <td>#{c.id}</td>
          <td>{c.unit_name or "—"}</td>
          <td>{c.guest_name}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="{c.damage_description}">{c.damage_description[:60]}{"…" if len(c.damage_description) > 60 else ""}</td>
          <td><span style="background:{color};color:#fff;padding:2px 8px;border-radius:999px;
              font-size:11px;white-space:nowrap">{label}</span></td>
          <td>{c.waiting_on or "—"}</td>
          <td>{fmt_money(c.deposit_amount)}</td>
          <td>{fmt_money(c.damage_amount)}</td>
          <td>{fmt_money(c.other_charges)}</td>
          <td>{fmt_money(c.refund_amount)}</td>
          <td style="text-align:center">{photo_badge(c.photo_proof_received)}</td>
          <td>{age(c)}</td>
          <td style="white-space:nowrap">{fmt_due(c)}</td>
          <td>{updated_str}</td>
          <td>
            <a href="/damage-cases/{c.id}" style="color:#3b82f6;font-size:12px">View</a>
          </td>
        </tr>"""

    total = len(cases)
    pending = len([c for c in cases if c.status not in ("closed", "cancelled")])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Damage Cases Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#f1f5f9;color:#1e293b;font-size:14px}}
  header{{background:#0f172a;color:#fff;padding:16px 24px;
          display:flex;justify-content:space-between;align-items:center}}
  header h1{{font-size:18px;font-weight:700}}
  .stats{{display:flex;gap:12px;padding:16px 24px;flex-wrap:wrap}}
  .stat{{background:#fff;border-radius:8px;padding:12px 20px;
         box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:120px}}
  .stat .num{{font-size:28px;font-weight:700;line-height:1}}
  .stat .lbl{{font-size:11px;color:#64748b;margin-top:4px;text-transform:uppercase;
              letter-spacing:.5px}}
  .wrap{{padding:0 24px 32px}}
  table{{width:100%;border-collapse:collapse;background:#fff;
         border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  th{{background:#1e293b;color:#fff;padding:10px 12px;text-align:left;
      font-size:11px;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}}
  td{{padding:8px 12px;border-bottom:1px solid #f1f5f9;vertical-align:top}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#f8fafc}}
  .refresh{{font-size:12px;color:#94a3b8}}
</style>
</head>
<body>
<header>
  <h1>Damage Cases Dashboard</h1>
  <span class="refresh">Last updated: {now.strftime("%d %b %Y %H:%M")} UTC
    &nbsp;·&nbsp; <a href="/dashboard-view" style="color:#94a3b8">Refresh</a>
  </span>
</header>
<div class="stats">
  <div class="stat"><div class="num">{total}</div><div class="lbl">Total Cases</div></div>
  <div class="stat"><div class="num" style="color:#f59e0b">{pending}</div><div class="lbl">Pending</div></div>
  <div class="stat"><div class="num" style="color:#22c55e">{len([c for c in cases if c.status == "closed"])}</div>
       <div class="lbl">Closed</div></div>
  <div class="stat"><div class="num" style="color:#ef4444">{len([c for c in cases if c.status not in ("closed", "cancelled") and not c.photo_proof_received])}</div>
       <div class="lbl">Missing Photo</div></div>
  <div class="stat"><div class="num" style="color:#dc2626">{overdue_count}</div>
       <div class="lbl">Overdue</div></div>
</div>
<div class="wrap">
<table>
<thead>
  <tr>
    <th>ID</th><th>Unit</th><th>Guest</th><th>Damage</th><th>Status</th>
    <th>Waiting On</th><th>Deposit</th><th>Damage Amt</th><th>Other Chg</th>
    <th>Refund</th><th>Photo</th><th>Age</th><th>Due In / Overdue</th><th>Updated</th><th></th>
  </tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)
