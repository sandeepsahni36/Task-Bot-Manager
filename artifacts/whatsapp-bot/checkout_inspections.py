import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import Client

from hostfully import get_hostfully_config, fetch_bookings
from supabase_client import get_supabase_dep
from whatsapp import send_text_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status display constants
# ---------------------------------------------------------------------------

CHECKOUT_STATUS_LABELS = {
    "checkout_verification_pending": "Checkout Verification Pending",
    "rebooked_extension_detected":   "Rebooked / Extension Detected",
    "late_checkout":                 "Late Checkout",
    "inspection_pending":            "Inspection Pending",
    "no_damage_reported":            "No Damage Reported",
    "damage_reported":               "Damage Reported",
    "issue":                         "Issue",
    "closed":                        "Closed",
}

CHECKOUT_STATUS_COLORS = {
    "checkout_verification_pending": "#f59e0b",
    "rebooked_extension_detected":   "#3b82f6",
    "late_checkout":                 "#f97316",
    "inspection_pending":            "#8b5cf6",
    "no_damage_reported":            "#22c55e",
    "damage_reported":               "#ef4444",
    "issue":                         "#dc2626",
    "closed":                        "#6b7280",
}

ACTIVE_BOOKING_STATUSES = {"new", "processed", "booked", "confirmed", "active"}
CANCELLED_BOOKING_STATUSES = {"cancelled", "canceled", "declined", "expired"}

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CheckoutInspectionOut(BaseModel):
    id: int
    hostfully_reservation_uid: str
    linked_new_booking_uid: Optional[str] = None
    hostfully_property_uid: Optional[str] = None
    hostfully_guest_uid: Optional[str] = None
    unit_name: str
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    guest_email: Optional[str] = None
    scheduled_checkout_at: Optional[datetime] = None
    original_checkout_at: Optional[datetime] = None
    actual_checkout_at: Optional[datetime] = None
    status: str
    assigned_ops_number: Optional[str] = None
    late_checkout_followup_at: Optional[datetime] = None
    damage_case_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_hostfully_sync_at: Optional[datetime] = None
    last_message_sent_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReplyBody(BaseModel):
    reply: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.utcnow().isoformat()


def _parse_dt(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            return None
    return None


def _parse_date_flexible(val) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    s = str(val).strip()[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt)
        except ValueError:
            continue
    return None


def _get_inspection_or_404(sb: Client, insp_id: int) -> dict:
    resp = sb.table("checkout_inspections").select("*").eq("id", insp_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail=f"Checkout inspection {insp_id} not found")
    return resp.data[0]


def _update_inspection(sb: Client, insp_id: int, fields: dict) -> dict:
    fields["updated_at"] = _now_utc()
    resp = sb.table("checkout_inspections").update(fields).eq("id", insp_id).execute()
    return resp.data[0]


def _get_field(obj: dict, *keys, default=None):
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return default


def _extract_bookings_list(data) -> list:
    if isinstance(data, list):
        return data
    for key in ("bookings", "leads", "reservations", "results", "items"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


def _norm_phone(p: str) -> str:
    digits = "".join(c for c in str(p) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _norm_name(n: str) -> str:
    return " ".join(str(n).strip().lower().split())


def _extract_booking_fields(booking: dict) -> dict:
    reservation_uid = _get_field(booking, "uid", "id", "bookingUid", "reservationUid", "leadUid")
    property_uid = _get_field(booking, "propertyUid", "propertyId", "property_uid")
    guest_uid = _get_field(booking, "guestUid", "guestId", "guest_uid", "leadGuestUid")
    first = _get_field(booking, "guestFirstName", "firstName", "") or ""
    last = _get_field(booking, "guestLastName", "lastName", "") or ""
    guest_name = (
        _get_field(booking, "guestName", "guest_name", "leadName")
        or f"{first} {last}".strip()
        or None
    )
    guest_phone = _get_field(booking, "guestPhone", "phoneNumber", "phone", "guestPhoneNumber")
    guest_email = _get_field(booking, "guestEmail", "email", "guestEmailAddress")
    unit_name = (
        _get_field(booking, "propertyName", "unitName", "property_name")
        or property_uid
        or "Unknown Unit"
    )
    checkout_str = _get_field(booking, "checkOutDate", "checkoutDate", "checkOut", "endDate")
    checkin_str = _get_field(booking, "checkInDate", "checkinDate", "checkIn", "startDate")
    status = (booking.get("status") or "").lower()
    return {
        "reservation_uid": reservation_uid,
        "property_uid": property_uid,
        "guest_uid": guest_uid,
        "guest_name": guest_name,
        "guest_phone": guest_phone,
        "guest_email": guest_email,
        "unit_name": unit_name,
        "checkout_str": checkout_str,
        "checkin_str": checkin_str,
        "status": status,
    }


async def _find_rebooking(
    api_key: str,
    agency_uid: str,
    base_url: str,
    property_uid: str,
    guest_uid: Optional[str],
    guest_phone: Optional[str],
    guest_email: Optional[str],
    guest_name: Optional[str],
    old_checkout_dt: Optional[datetime],
) -> Optional[dict]:
    """Search Hostfully for a rebooking/extension for the same property + guest."""
    if not old_checkout_dt or not property_uid:
        return None

    search_from = (old_checkout_dt - timedelta(hours=1)).date().isoformat()
    search_to = (old_checkout_dt + timedelta(hours=25)).date().isoformat()

    status_code, data = await fetch_bookings(
        api_key, agency_uid, base_url,
        checkin_from=search_from,
        checkin_to=search_to,
        property_uid=property_uid,
    )
    if status_code != 200:
        logger.warning("Could not fetch bookings for rebooking check: status %d", status_code)
        return None

    for candidate in _extract_bookings_list(data):
        f = _extract_booking_fields(candidate)

        if f["status"] in CANCELLED_BOOKING_STATUSES:
            continue

        # Must be same property
        if f["property_uid"] and str(f["property_uid"]) != str(property_uid):
            continue

        # Check-in within 24-hour window of old checkout
        c_checkin = _parse_date_flexible(f["checkin_str"])
        if not c_checkin:
            continue
        diff_hours = (c_checkin - old_checkout_dt).total_seconds() / 3600
        if not (-1 <= diff_hours <= 25):
            continue

        # Guest identity check: uid / phone / email / name
        guest_matches = any([
            guest_uid and f["guest_uid"] and str(guest_uid) == str(f["guest_uid"]),
            guest_phone and f["guest_phone"] and _norm_phone(guest_phone) == _norm_phone(f["guest_phone"]),
            guest_email and f["guest_email"] and guest_email.lower() == f["guest_email"].lower(),
            guest_name and f["guest_name"] and _norm_name(guest_name) == _norm_name(f["guest_name"]),
        ])
        if guest_matches:
            logger.info(
                "Rebooking detected: property=%s checkin=%s (%.1fh after old checkout)",
                property_uid, c_checkin, diff_hours,
            )
            return candidate

    return None


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/checkout-inspections", tags=["Checkout Inspections"])
hostfully_checkout_router = APIRouter(prefix="/hostfully", tags=["Hostfully"])


# ---------------------------------------------------------------------------
# GET /checkout-inspections
# ---------------------------------------------------------------------------

@router.get("", response_model=List[CheckoutInspectionOut], summary="List all checkout inspections")
def list_checkout_inspections(sb: Client = Depends(get_supabase_dep)):
    resp = (
        sb.table("checkout_inspections")
        .select("*")
        .order("scheduled_checkout_at", desc=True)
        .execute()
    )
    return [CheckoutInspectionOut.model_validate(r) for r in resp.data]


# ---------------------------------------------------------------------------
# GET /checkout-inspections/pending
# ---------------------------------------------------------------------------

@router.get("/pending", response_model=List[CheckoutInspectionOut], summary="List pending checkout inspections")
def list_pending_checkouts(sb: Client = Depends(get_supabase_dep)):
    resp = (
        sb.table("checkout_inspections")
        .select("*")
        .in_("status", ["checkout_verification_pending", "late_checkout", "inspection_pending"])
        .order("scheduled_checkout_at", desc=True)
        .execute()
    )
    return [CheckoutInspectionOut.model_validate(r) for r in resp.data]


# ---------------------------------------------------------------------------
# POST /checkout-inspections/check-due  (fixed path before /{id})
# ---------------------------------------------------------------------------

@router.post("/check-due", summary="Send reminders for due checkout inspections (3h throttle)")
async def check_due_checkouts(sb: Client = Depends(get_supabase_dep)):
    now = datetime.utcnow()
    throttle = timedelta(hours=3)
    sent = []
    skipped = 0
    errors = []

    pending = (
        sb.table("checkout_inspections")
        .select("*")
        .in_("status", ["checkout_verification_pending", "late_checkout"])
        .execute()
        .data
    )

    for insp in pending:
        ops_number = insp.get("assigned_ops_number")
        if not ops_number:
            skipped += 1
            continue

        # Determine trigger time
        if insp["status"] == "checkout_verification_pending":
            trigger_at = _parse_dt(insp.get("scheduled_checkout_at"))
        else:
            trigger_at = _parse_dt(insp.get("late_checkout_followup_at"))

        if trigger_at and trigger_at > now:
            skipped += 1
            continue

        # 3-hour throttle
        last_sent = _parse_dt(insp.get("last_message_sent_at"))
        if last_sent and (now - last_sent) < throttle:
            skipped += 1
            continue

        checkout_str = (
            _parse_dt(insp.get("scheduled_checkout_at")).strftime("%d %b %Y %H:%M UTC")
            if _parse_dt(insp.get("scheduled_checkout_at")) else "—"
        )

        if insp["status"] == "checkout_verification_pending":
            msg = (
                f"*Checkout Reminder* — Unit: {insp['unit_name']}\n"
                f"Guest: {insp.get('guest_name') or '—'}\n"
                f"Scheduled checkout: {checkout_str}\n\n"
                f"Reply 1 Guest checked out, inspect unit\n"
                f"Reply 2 Guest still inside / late checkout\n"
                f"Reply 3 Issue"
            )
        else:
            msg = (
                f"*Late Checkout Follow-up* — Unit: {insp['unit_name']}\n"
                f"Guest: {insp.get('guest_name') or '—'}\n"
                f"Please confirm — has the guest checked out?\n\n"
                f"Reply 1 Guest checked out, inspect unit\n"
                f"Reply 2 Still inside\n"
                f"Reply 3 Issue"
            )

        try:
            await send_text_message(ops_number, msg)
            _update_inspection(sb, insp["id"], {"last_message_sent_at": _now_utc()})
            sent.append({"id": insp["id"], "unit_name": insp["unit_name"], "ops_number": ops_number})
            logger.info("Checkout reminder sent: inspection=%d ops=%s", insp["id"], ops_number)
        except Exception as exc:
            errors.append(f"Inspection {insp['id']}: {exc}")
            logger.error("Checkout reminder failed for inspection %d: %s", insp["id"], exc)

    return {"sent_count": len(sent), "skipped_count": skipped, "errors": errors, "sent": sent}


# ---------------------------------------------------------------------------
# POST /checkout-inspections/{id}/reply
# ---------------------------------------------------------------------------

@router.post(
    "/{insp_id}/reply",
    response_model=CheckoutInspectionOut,
    summary="Handle ops reply: 1=inspecting, 2=late checkout, 3=issue",
)
async def handle_checkout_reply(
    insp_id: int,
    body: ReplyBody,
    sb: Client = Depends(get_supabase_dep),
):
    insp = _get_inspection_or_404(sb, insp_id)
    reply = body.reply.strip()
    ops_number = insp.get("assigned_ops_number")
    default_ops = os.getenv("DEFAULT_OPS_WHATSAPP_NUMBER")
    owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")

    if reply in ("1", "done"):
        updated = _update_inspection(sb, insp_id, {
            "status": "inspection_pending",
            "actual_checkout_at": _now_utc(),
        })
        notify = ops_number or default_ops
        if notify:
            msg = (
                f"*Checkout Confirmed — Please Inspect*\n"
                f"Unit: {insp['unit_name']}\n"
                f"Guest: {insp.get('guest_name') or '—'} has checked out.\n\n"
                f"Please inspect the unit and report:\n"
                f"  • No damages found\n"
                f"  • Damages found (describe)"
            )
            await send_text_message(notify, msg)
            _update_inspection(sb, insp_id, {"last_message_sent_at": _now_utc()})
        logger.info("Inspection %d → inspection_pending", insp_id)
        return CheckoutInspectionOut.model_validate(updated)

    elif reply in ("2", "late"):
        followup_at = (datetime.utcnow() + timedelta(hours=2)).isoformat()
        updated = _update_inspection(sb, insp_id, {
            "status": "late_checkout",
            "late_checkout_followup_at": followup_at,
        })
        notify = ops_number or default_ops
        if notify:
            checkout_str = (
                _parse_dt(insp.get("scheduled_checkout_at")).strftime("%d %b %Y %H:%M UTC")
                if _parse_dt(insp.get("scheduled_checkout_at")) else "—"
            )
            msg = (
                f"*Late Checkout Alert*\n"
                f"Unit: {insp['unit_name']}\n"
                f"Guest: {insp.get('guest_name') or '—'}\n"
                f"Scheduled checkout: {checkout_str}\n"
                f"Guest is still inside. Please confirm whether a new booking/extension exists.\n"
                f"Follow-up reminder set for 2 hours from now."
            )
            await send_text_message(notify, msg)
            _update_inspection(sb, insp_id, {"last_message_sent_at": _now_utc()})
        logger.info("Inspection %d → late_checkout, follow-up at %s", insp_id, followup_at)
        return CheckoutInspectionOut.model_validate(updated)

    elif reply in ("3", "issue"):
        updated = _update_inspection(sb, insp_id, {"status": "issue"})
        msg = (
            f"*⚠ Checkout Issue*\n"
            f"Unit: {insp['unit_name']}\n"
            f"Guest: {insp.get('guest_name') or '—'}\n"
            f"Operations has reported an issue with this checkout. Please follow up."
        )
        notified = set()
        for number in [owner_number, default_ops, ops_number]:
            if number and number not in notified:
                await send_text_message(number, msg)
                notified.add(number)
        if notified:
            _update_inspection(sb, insp_id, {"last_message_sent_at": _now_utc()})
        logger.info("Inspection %d → issue, notified: %s", insp_id, notified)
        return CheckoutInspectionOut.model_validate(updated)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown reply '{reply}'. Use 1, 2, or 3.")


# ---------------------------------------------------------------------------
# POST /hostfully/sync-checkouts
# ---------------------------------------------------------------------------

@hostfully_checkout_router.post("/sync-checkouts", summary="Sync Hostfully checkouts, detect rebookings, send ops messages")
async def sync_checkouts(sb: Client = Depends(get_supabase_dep)):
    try:
        api_key, agency_uid, base_url = get_hostfully_config()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    ops_number = os.getenv("DEFAULT_OPS_WHATSAPP_NUMBER")
    if not ops_number:
        raise HTTPException(status_code=500, detail="DEFAULT_OPS_WHATSAPP_NUMBER is not set")

    now = datetime.utcnow()
    today = now.date()
    tomorrow = today + timedelta(days=1)

    # Fetch checkouts for today and tomorrow
    status_code, bookings_data = await fetch_bookings(
        api_key, agency_uid, base_url,
        checkout_from=today.isoformat(),
        checkout_to=tomorrow.isoformat(),
    )
    if status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Hostfully API returned {status_code}: {bookings_data}",
        )

    all_bookings = _extract_bookings_list(bookings_data)
    checkouts = [
        b for b in all_bookings
        if (b.get("status") or "").lower() not in CANCELLED_BOOKING_STATUSES
    ]

    created_count = 0
    updated_count = 0
    rebooked_count = 0
    messages_sent = 0
    skipped_count = 0
    errors = []

    for booking in checkouts:
        f = _extract_booking_fields(booking)
        reservation_uid = f["reservation_uid"]
        if not reservation_uid:
            errors.append("Booking missing UID — skipped")
            continue

        # Dedup by reservation UID
        existing_resp = (
            sb.table("checkout_inspections")
            .select("id, status")
            .eq("hostfully_reservation_uid", str(reservation_uid))
            .execute()
        )
        if existing_resp.data:
            sb.table("checkout_inspections").update({
                "last_hostfully_sync_at": _now_utc(),
                "updated_at": _now_utc(),
            }).eq("id", existing_resp.data[0]["id"]).execute()
            updated_count += 1
            skipped_count += 1
            continue

        checkout_dt = _parse_date_flexible(f["checkout_str"])

        # Rebooking/extension detection
        rebooking = None
        if f["property_uid"]:
            try:
                rebooking = await _find_rebooking(
                    api_key, agency_uid, base_url,
                    property_uid=f["property_uid"],
                    guest_uid=f["guest_uid"],
                    guest_phone=f["guest_phone"],
                    guest_email=f["guest_email"],
                    guest_name=f["guest_name"],
                    old_checkout_dt=checkout_dt,
                )
            except Exception as exc:
                logger.warning("Rebooking check failed for %s: %s", reservation_uid, exc)

        now_str = _now_utc()
        base_record = {
            "hostfully_reservation_uid": str(reservation_uid),
            "hostfully_property_uid":    f["property_uid"],
            "hostfully_guest_uid":       f["guest_uid"],
            "unit_name":                 f["unit_name"],
            "guest_name":                f["guest_name"],
            "guest_phone":               f["guest_phone"],
            "guest_email":               f["guest_email"],
            "original_checkout_at":      checkout_dt.isoformat() if checkout_dt else None,
            "assigned_ops_number":       ops_number,
            "last_hostfully_sync_at":    now_str,
            "created_at":                now_str,
            "updated_at":                now_str,
        }

        if rebooking:
            rb_fields = _extract_booking_fields(rebooking)
            new_checkout_dt = _parse_date_flexible(rb_fields["checkout_str"])
            new_uid = rb_fields["reservation_uid"]

            record = {
                **base_record,
                "status":                   "rebooked_extension_detected",
                "linked_new_booking_uid":   str(new_uid) if new_uid else None,
                "scheduled_checkout_at":    new_checkout_dt.isoformat() if new_checkout_dt else None,
            }
            sb.table("checkout_inspections").insert(record).execute()
            created_count += 1
            rebooked_count += 1

            old_co = checkout_dt.strftime("%d %b %Y") if checkout_dt else "—"
            new_co = new_checkout_dt.strftime("%d %b %Y") if new_checkout_dt else "—"
            try:
                await send_text_message(
                    ops_number,
                    f"*Rebooking / Extension Detected*\n"
                    f"Unit: {f['unit_name']}\n"
                    f"Guest: {f['guest_name'] or '—'}\n"
                    f"Original checkout: {old_co} → moved to: {new_co}\n"
                    f"No checkout inspection needed today.",
                )
                messages_sent += 1
                sb.table("checkout_inspections").update({"last_message_sent_at": _now_utc()}).eq(
                    "hostfully_reservation_uid", str(reservation_uid)
                ).execute()
            except Exception as exc:
                errors.append(f"Rebooking info message failed for {reservation_uid}: {exc}")

            logger.info(
                "Rebooked extension: reservation=%s → new_booking=%s",
                reservation_uid, new_uid,
            )

        else:
            record = {
                **base_record,
                "status":               "checkout_verification_pending",
                "scheduled_checkout_at": checkout_dt.isoformat() if checkout_dt else None,
            }
            sb.table("checkout_inspections").insert(record).execute()
            created_count += 1

            co_str = checkout_dt.strftime("%d %b %Y %H:%M") if checkout_dt else "—"
            msg = (
                f"Checkout verification required. "
                f"Unit: {f['unit_name']}. "
                f"Guest: {f['guest_name'] or '—'}. "
                f"Scheduled checkout: {co_str}. "
                f"Reply 1 Guest checked out, inspect unit, "
                f"2 Guest still inside / late checkout, "
                f"3 Issue."
            )
            try:
                await send_text_message(ops_number, msg)
                messages_sent += 1
                sb.table("checkout_inspections").update({"last_message_sent_at": _now_utc()}).eq(
                    "hostfully_reservation_uid", str(reservation_uid)
                ).execute()
                logger.info(
                    "Checkout verification sent: unit=%s reservation=%s",
                    f["unit_name"], reservation_uid,
                )
            except Exception as exc:
                errors.append(f"WhatsApp failed for {reservation_uid}: {exc}")

    return {
        "created_count":                    created_count,
        "updated_count":                    updated_count,
        "rebooked_extension_detected_count": rebooked_count,
        "inspection_messages_sent":         messages_sent,
        "skipped_count":                    skipped_count,
        "errors":                           errors,
    }
