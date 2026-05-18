import logging
from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db, DamageCase, DamageEvent, DamagePhoto
from schemas import (
    DamageCaseCreate,
    DamageCaseOut,
    DamageCaseDetail,
    DamagePhotoOut,
    QuoteBody,
    PhotoBody,
)
from whatsapp import send_text_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/damage-cases", tags=["Damage Cases"])

STATUS_LABELS = {
    "quote_pending": "Quote Pending",
    "tenant_approval_pending": "Tenant Approval Pending",
    "gm_action_pending": "GM Action Pending",
    "placement_proof_pending": "Placement Proof Pending",
    "accounts_refund_pending": "Accounts Refund Pending",
    "closed": "Closed",
    "cancelled": "Cancelled",
}

STATUS_COLORS = {
    "quote_pending": "#f59e0b",
    "tenant_approval_pending": "#3b82f6",
    "gm_action_pending": "#8b5cf6",
    "placement_proof_pending": "#ec4899",
    "accounts_refund_pending": "#06b6d4",
    "closed": "#22c55e",
    "cancelled": "#6b7280",
}


def _add_event(db: Session, case_id: int, event_type: str, message: str, whatsapp_number: str = None):
    event = DamageEvent(
        damage_case_id=case_id,
        event_type=event_type,
        message=message,
        whatsapp_number=whatsapp_number,
    )
    db.add(event)


def _touch(case: DamageCase):
    case.updated_at = datetime.now(timezone.utc)


def _set_due_at(case: DamageCase, hours: int):
    """Set due_at to now + N hours, stored as naive UTC (SQLite-compatible)."""
    case.due_at = datetime.utcnow() + timedelta(hours=hours)


def _get_case_or_404(db: Session, case_id: int) -> DamageCase:
    case = db.query(DamageCase).filter(DamageCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Damage case {case_id} not found")
    return case


def _require_status(case: DamageCase, required: str, extra_check: str = None):
    """Raise 400 if the case is not in the required status (or fails an extra condition)."""
    if case.status != required:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid workflow step — case is not in the required status.",
                "current_status": case.status,
                "required_status": required,
            },
        )
    if extra_check:
        raise HTTPException(status_code=400, detail={"error": extra_check})


@router.post("", response_model=DamageCaseOut, summary="Create a new damage case")
async def create_damage_case(body: DamageCaseCreate, db: Session = Depends(get_db)):
    if not body.unit_name and not body.hostfully_property_uid:
        raise HTTPException(
            status_code=400,
            detail="Provide either unit_name or hostfully_property_uid",
        )

    unit = body.unit_name or body.hostfully_property_uid

    case = DamageCase(
        hostfully_property_uid=body.hostfully_property_uid,
        hostfully_guest_uid=body.hostfully_guest_uid,
        unit_name=body.unit_name or body.hostfully_property_uid,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone,
        guest_email=body.guest_email,
        damage_description=body.damage_description,
        deposit_amount=body.deposit_amount,
        damage_amount=0.0,
        other_charges=0.0,
        status="quote_pending",
        waiting_on="GM",
        reported_by_number=body.reported_by_number,
        gm_number=body.gm_number,
        ops_supervisor_number=body.ops_supervisor_number,
        reservations_number=body.reservations_number,
        accounts_number=body.accounts_number,
        photo_proof_received=False,
    )
    db.add(case)
    _set_due_at(case, 24)  # quote_pending: 24-hour deadline
    db.commit()
    db.refresh(case)

    msg = (
        f"*New Damage Case #{case.id}*\n"
        f"Unit: {case.unit_name}\n"
        f"Guest: {case.guest_name}\n"
        f"Damage: {case.damage_description}\n"
        f"Deposit: AED {case.deposit_amount:.2f}\n\n"
        f"Please provide a damage quote (repair/replacement cost) as soon as possible."
    )
    wa_result = await send_text_message(case.gm_number, msg)
    _add_event(db, case.id, "case_created", msg, case.gm_number)
    logger.info("Damage case %d created, GM notified at %s", case.id, case.gm_number)

    db.commit()
    return case


@router.get("", response_model=List[DamageCaseOut], summary="List all damage cases (newest first)")
def list_damage_cases(db: Session = Depends(get_db)):
    return db.query(DamageCase).order_by(DamageCase.created_at.desc()).all()


@router.get("/pending", response_model=List[DamageCaseOut], summary="List pending (non-closed) damage cases")
def list_pending_cases(db: Session = Depends(get_db)):
    return (
        db.query(DamageCase)
        .filter(DamageCase.status.notin_(["closed", "cancelled"]))
        .order_by(DamageCase.created_at.desc())
        .all()
    )


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
async def check_overdue(db: Session = Depends(get_db)):
    import os
    owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")
    # Use naive UTC so the SQL comparison matches SQLite's string storage format.
    now_naive = datetime.utcnow()
    throttle_window = timedelta(hours=6)

    overdue_cases = (
        db.query(DamageCase)
        .filter(
            DamageCase.status == "gm_action_pending",
            DamageCase.due_at.isnot(None),
            DamageCase.due_at < now_naive,
        )
        .all()
    )

    notified = []
    errors = []
    skipped_due_to_throttle = 0

    for case in overdue_cases:
        # due_at stored as naive UTC; strip tzinfo if somehow set.
        due = case.due_at.replace(tzinfo=None) if case.due_at.tzinfo is not None else case.due_at
        hours_overdue = round((now_naive - due).total_seconds() / 3600, 1)

        msg = (
            f"*⚠ Overdue Damage Case #{case.id}*\n"
            f"Unit: {case.unit_name} | Guest: {case.guest_name}\n"
            f"Damage: {case.damage_description}\n"
            f"Damage amount: AED {(case.damage_amount or 0):.2f}\n"
            f"Overdue by: {hours_overdue}h\n\n"
            f"This case is awaiting GM action (purchase + placement of replacement item). "
            f"Please action this immediately."
        )

        recipients = [
            ("gm", case.gm_number),
            ("ops_supervisor", case.ops_supervisor_number),
        ]
        if owner_number:
            recipients.append(("owner", owner_number))

        case_notified = []
        for role, number in recipients:
            # Throttle: find the most recent overdue_escalation sent to this number for this case.
            last_event = (
                db.query(DamageEvent)
                .filter(
                    DamageEvent.damage_case_id == case.id,
                    DamageEvent.event_type == "overdue_escalation",
                    DamageEvent.whatsapp_number == number,
                )
                .order_by(DamageEvent.created_at.desc())
                .first()
            )

            if last_event and last_event.created_at:
                last_sent = (
                    last_event.created_at.replace(tzinfo=None)
                    if last_event.created_at.tzinfo is not None
                    else last_event.created_at
                )
                if (now_naive - last_sent) < throttle_window:
                    hours_since = round((now_naive - last_sent).total_seconds() / 3600, 1)
                    logger.info(
                        "Case %d throttle: skipping %s (%s) — last sent %.1fh ago",
                        case.id, number, role, hours_since,
                    )
                    skipped_due_to_throttle += 1
                    continue

            try:
                await send_text_message(number, msg)
                _add_event(db, case.id, "overdue_escalation", msg, number)
                case_notified.append({"role": role, "number": number})
                logger.info(
                    "Case %d overdue escalation sent to %s (%s)",
                    case.id, number, role,
                )
            except Exception as exc:
                err = f"Case {case.id} → {role} ({number}): {exc}"
                errors.append(err)
                logger.error("Overdue escalation failed: %s", err)

        if case_notified:
            notified.append({
                "case_id": case.id,
                "unit_name": case.unit_name,
                "guest_name": case.guest_name,
                "hours_overdue": hours_overdue,
                "notified": case_notified,
            })

    db.commit()
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
def get_damage_case(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    events = db.query(DamageEvent).filter(DamageEvent.damage_case_id == case_id).order_by(DamageEvent.created_at).all()
    photos = db.query(DamagePhoto).filter(DamagePhoto.damage_case_id == case_id).order_by(DamagePhoto.created_at).all()

    result = DamageCaseDetail.model_validate(case)
    result.events = [e for e in events]
    result.photos = [p for p in photos]
    return result


@router.post("/{case_id}/quote", response_model=DamageCaseOut, summary="Submit damage quote")
async def submit_quote(case_id: int, body: QuoteBody, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    _require_status(case, "quote_pending")

    case.damage_amount = body.damage_amount
    case.other_charges = body.other_charges
    case.refund_amount = case.deposit_amount - body.damage_amount - body.other_charges
    case.status = "tenant_approval_pending"
    case.waiting_on = "Reservations/Ops"
    _set_due_at(case, 24)  # tenant_approval_pending: 24-hour deadline
    _touch(case)

    notes_line = f"\nNotes: {body.notes}" if body.notes else ""
    msg = (
        f"*Damage Case #{case.id} – Quote Received*\n"
        f"Unit: {case.unit_name} | Guest: {case.guest_name}\n"
        f"Damage: AED {case.damage_amount:.2f}\n"
        f"Other charges: AED {case.other_charges:.2f}\n"
        f"Refund to guest: AED {case.refund_amount:.2f}{notes_line}\n\n"
        f"Please share these charges with the tenant and confirm their approval."
    )
    await send_text_message(case.reservations_number, msg)
    _add_event(db, case.id, "quote_submitted", msg, case.reservations_number)

    db.commit()
    db.refresh(case)
    logger.info("Case %d quote submitted, reservations notified", case.id)
    return case


@router.post("/{case_id}/tenant-approved", response_model=DamageCaseOut, summary="Mark tenant as approved")
async def tenant_approved(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    _require_status(case, "tenant_approval_pending")
    if case.refund_amount is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Cannot mark tenant as approved — refund_amount is not set. Submit a quote first.",
                "current_status": case.status,
                "required_status": "tenant_approval_pending",
            },
        )

    case.status = "gm_action_pending"
    case.waiting_on = "GM + Ops Supervisor"
    _set_due_at(case, 46)  # gm_action_pending: 46-hour deadline
    _touch(case)

    msg = (
        f"*Damage Case #{case.id} – Tenant Approved*\n"
        f"Unit: {case.unit_name} | Guest: {case.guest_name}\n"
        f"Damage amount: AED {case.damage_amount:.2f}\n\n"
        f"Tenant has approved the charges. Please purchase and place the replacement item, "
        f"then send photo proof once placed."
    )
    await send_text_message(case.gm_number, msg)
    await send_text_message(case.ops_supervisor_number, msg)
    _add_event(db, case.id, "tenant_approved", msg, case.gm_number)
    _add_event(db, case.id, "tenant_approved", msg, case.ops_supervisor_number)

    db.commit()
    db.refresh(case)
    logger.info("Case %d tenant approved, GM and Ops notified", case.id)
    return case


@router.post("/{case_id}/gm-purchased", response_model=DamageCaseOut, summary="GM confirms item purchased")
async def gm_purchased(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    _require_status(case, "gm_action_pending")

    case.status = "placement_proof_pending"
    case.waiting_on = "GM"
    _set_due_at(case, 24)  # placement_proof_pending: 24-hour deadline
    _touch(case)

    msg = (
        f"*Damage Case #{case.id} – Item Purchased*\n"
        f"Unit: {case.unit_name}\n\n"
        f"Item has been purchased. Please place the item in the unit and send a photo as proof of placement."
    )
    await send_text_message(case.gm_number, msg)
    _add_event(db, case.id, "gm_purchased", msg, case.gm_number)

    db.commit()
    db.refresh(case)
    logger.info("Case %d GM purchased, awaiting placement proof", case.id)
    return case


@router.post("/{case_id}/photo", response_model=DamagePhotoOut, summary="Upload photo proof")
async def add_photo(case_id: int, body: PhotoBody, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)

    photo = DamagePhoto(
        damage_case_id=case.id,
        photo_url_or_media_id=body.photo_url_or_media_id,
        photo_type=body.photo_type,
    )
    db.add(photo)

    case.photo_proof_received = True
    _touch(case)

    _add_event(
        db, case.id, "photo_uploaded",
        f"Photo uploaded: type={body.photo_type} ref={body.photo_url_or_media_id}",
    )

    db.commit()
    db.refresh(photo)
    logger.info("Case %d photo uploaded: type=%s", case.id, body.photo_type)
    return photo


@router.post("/{case_id}/replacement-placed", response_model=DamageCaseOut, summary="Confirm replacement placed — notifies Accounts")
async def replacement_placed(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    _require_status(case, "placement_proof_pending")
    if not case.photo_proof_received:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Photo proof is required before sending to accounts.",
                "current_status": case.status,
                "required_status": "placement_proof_pending",
            },
        )

    case.status = "accounts_refund_pending"
    case.waiting_on = "Accounts"
    _set_due_at(case, 24)  # accounts_refund_pending: 24-hour deadline
    _touch(case)

    refund = case.refund_amount if case.refund_amount is not None else 0.0
    msg = (
        f"*Damage Case #{case.id} – Ready for Refund Processing*\n"
        f"Unit: {case.unit_name}\n"
        f"Guest: {case.guest_name}"
        + (f" | {case.guest_email}" if case.guest_email else "")
        + (f" | {case.guest_phone}" if case.guest_phone else "") + "\n"
        f"Deposit held: AED {case.deposit_amount:.2f}\n"
        f"Damage charges: AED {(case.damage_amount or 0):.2f}\n"
        f"Other charges: AED {(case.other_charges or 0):.2f}\n"
        f"*Refund to guest: AED {refund:.2f}*\n\n"
        f"Replacement item has been placed and photo proof received. "
        f"Please process the refund to the guest."
    )
    await send_text_message(case.accounts_number, msg)
    _add_event(db, case.id, "sent_to_accounts", msg, case.accounts_number)

    db.commit()
    db.refresh(case)
    logger.info("Case %d sent to accounts for refund", case.id)
    return case


@router.post("/{case_id}/refund-completed", response_model=DamageCaseOut, summary="Mark refund as completed — closes case")
def refund_completed(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    _require_status(case, "accounts_refund_pending")

    case.status = "closed"
    case.waiting_on = None
    case.due_at = None
    case.closed_at = datetime.now(timezone.utc)
    _touch(case)

    _add_event(db, case.id, "case_closed", "Refund completed. Case closed.")
    db.commit()
    db.refresh(case)
    logger.info("Case %d closed", case.id)
    return case


@router.post("/{case_id}/cancel", response_model=DamageCaseOut, summary="Cancel a damage case")
def cancel_case(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)

    case.status = "cancelled"
    case.waiting_on = None
    case.due_at = None
    _touch(case)

    _add_event(db, case.id, "case_cancelled", "Case cancelled.")
    db.commit()
    db.refresh(case)
    logger.info("Case %d cancelled", case.id)
    return case


# ---------------------------------------------------------------------------
# Owner summary & dashboard
# ---------------------------------------------------------------------------

owner_router = APIRouter(tags=["Owner Dashboard"])


def _build_summary(db: Session) -> dict:
    """Shared logic for owner summary data."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    all_pending = (
        db.query(DamageCase)
        .filter(DamageCase.status.notin_(["closed", "cancelled"]))
        .all()
    )

    overdue = [c for c in all_pending if c.due_at and c.due_at < now]

    def waiting(label):
        return len([c for c in all_pending if c.waiting_on and label.lower() in c.waiting_on.lower()])

    closed_today = (
        db.query(DamageCase)
        .filter(DamageCase.status == "closed", DamageCase.closed_at >= today_start)
        .count()
    )

    oldest_open = (
        db.query(DamageCase)
        .filter(DamageCase.status.notin_(["closed", "cancelled"]))
        .order_by(DamageCase.created_at.asc())
        .limit(10)
        .all()
    )

    return {
        "total_pending": len(all_pending),
        "overdue": len(overdue),
        "waiting_on_gm": waiting("GM"),
        "waiting_on_ops_supervisor": waiting("Ops"),
        "waiting_on_reservations": waiting("Reservations"),
        "waiting_on_accounts": waiting("Accounts"),
        "missing_photo_proof": len([c for c in all_pending if not c.photo_proof_received]),
        "closed_today": closed_today,
        "top_10_oldest_open": [
            {
                "id": c.id,
                "unit_name": c.unit_name,
                "guest_name": c.guest_name,
                "status": c.status,
                "waiting_on": c.waiting_on,
                "age_days": (now - c.created_at.replace(tzinfo=timezone.utc)).days
                if c.created_at else None,
            }
            for c in oldest_open
        ],
    }


@owner_router.get("/owner-summary", summary="High-level summary for the owner")
def owner_summary(db: Session = Depends(get_db)):
    return _build_summary(db)


@owner_router.post(
    "/send-owner-summary",
    summary="Send owner summary via WhatsApp",
    description=(
        "Builds the same data as GET /owner-summary and sends it as a WhatsApp message "
        "to the number stored in the OWNER_WHATSAPP_NUMBER secret."
    ),
)
async def send_owner_summary(db: Session = Depends(get_db)):
    import os
    owner_number = os.getenv("OWNER_WHATSAPP_NUMBER")
    if not owner_number:
        raise HTTPException(status_code=500, detail="OWNER_WHATSAPP_NUMBER is not set")

    s = _build_summary(db)

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

    return {
        "sent_to": owner_number,
        "summary": s,
        "whatsapp_result": wa_result,
    }


@owner_router.get("/dashboard-view", response_class=HTMLResponse, summary="HTML dashboard grouped by status")
def dashboard_view(db: Session = Depends(get_db)):
    cases = db.query(DamageCase).order_by(DamageCase.created_at.desc()).all()
    now = datetime.now(timezone.utc)

    grouped: dict[str, list] = {s: [] for s in STATUS_LABELS}
    for c in cases:
        grouped.setdefault(c.status, []).append(c)

    def age(c):
        if not c.created_at:
            return "—"
        delta = now - c.created_at.replace(tzinfo=timezone.utc)
        if delta.days > 0:
            return f"{delta.days}d"
        hours = delta.seconds // 3600
        return f"{hours}h"

    def fmt_money(v):
        return f"AED {v:.2f}" if v is not None else "—"

    def photo_badge(v):
        return '<span style="color:#22c55e">✓</span>' if v else '<span style="color:#ef4444">✗</span>'

    def fmt_due(c):
        if c.status in ("closed", "cancelled") or not c.due_at:
            return "—"
        due = c.due_at.replace(tzinfo=timezone.utc) if c.due_at.tzinfo is None else c.due_at
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
        and c.due_at.replace(tzinfo=timezone.utc if c.due_at.tzinfo is None else c.due_at.tzinfo) < now
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
            rows_html += f"""
        <tr>
          <td>#{c.id}</td>
          <td>{c.unit_name or "—"}</td>
          <td>{c.guest_name}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="{c.damage_description}">{c.damage_description[:60]}{"…" if len(c.damage_description)>60 else ""}</td>
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
          <td>{c.updated_at.strftime("%d %b %H:%M") if c.updated_at else "—"}</td>
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
  <div class="stat"><div class="num" style="color:#22c55e">{len([c for c in cases if c.status=="closed"])}</div>
       <div class="lbl">Closed</div></div>
  <div class="stat"><div class="num" style="color:#ef4444">{len([c for c in cases if c.status not in("closed","cancelled") and not c.photo_proof_received])}</div>
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
