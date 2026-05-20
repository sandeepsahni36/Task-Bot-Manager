# Holiday Homes Ops Bot

WhatsApp task reminders, damage case management, and checkout inspection workflow for **Everluxe Real Estate And Holiday Homes**.

- **Backend:** https://task-bot-manager.replit.app
- **Frontend (Netlify):** https://ai.stayeverluxe.com
- **API Docs:** https://task-bot-manager.replit.app/docs

---

## Full Flow

```
Hostfully PMS  →  POST /hostfully/sync-checkouts
                    │
                    ├─ Rebooking detected?
                    │     Yes → status: rebooked_extension_detected
                    │           Notify ops: "Inspection moved to new checkout date"
                    │
                    └─ Normal checkout
                          status: checkout_verification_pending
                          WhatsApp → ops: "Reply 1/2/3"

Staff WhatsApp reply  →  POST /webhooks/whatsapp
                            │
                            ├─ Open checkout_inspection for sender?
                            │     1 → inspection_pending  (guest left, go inspect)
                            │     2 → late_checkout       (guest still inside)
                            │     3 → issue               (notify owner)
                            │
                            └─ inspection_pending reply?
                                  1 → no_damage_reported → closed
                                  2 → damage_reported
                                        POST /checkout-inspections/{id}/damage-found
                                          → creates damage_case, notifies GM

Damage case workflow:
  quote_pending → tenant_approval_pending → gm_action_pending
  → placement_proof_pending → accounts_refund_pending → closed
```

---

## Required Environment Variables

Set all of these in **Replit Secrets** before running:

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (not the anon key) |
| `WHATSAPP_ACCESS_TOKEN` | Meta WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Token for Meta webhook verification |
| `WHATSAPP_TEMPLATE_NAME` | Approved template name (e.g. `task_reminder`) |
| `WHATSAPP_TEMPLATE_LANGUAGE` | Template language code (e.g. `en_US`) |
| `OWNER_WHATSAPP_NUMBER` | Owner's number for escalations (no `+`, e.g. `971501234567`) |
| `DEFAULT_OPS_WHATSAPP_NUMBER` | Default ops number for checkout inspections |
| `HOSTFULLY_API_KEY` | Hostfully API key |
| `HOSTFULLY_AGENCY_UID` | Hostfully agency UID |
| `HOSTFULLY_BASE_URL` | Hostfully API base URL — use `https://api.hostfully.com/api/v3` |
| `CRON_SECRET` | Secret for protecting cron endpoints (optional but strongly recommended) |
| `TEST_WHATSAPP_TO` | Phone number for `/send-test-task` smoke tests |

**Frontend (Netlify) variable:**

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | Backend URL, e.g. `https://task-bot-manager.replit.app` |

---

## Cron Endpoints

Call these on a schedule (Replit cron, GitHub Actions, EasyCron, etc.):

| Endpoint | Recommended frequency | Purpose |
|---|---|---|
| `POST /damage-cases/check-overdue` | Every hour | Escalate overdue GM-action cases (6h throttle per case) |
| `POST /checkout-inspections/check-due` | Every 30 minutes | Remind ops of due/late checkouts (3h throttle per record) |

Both require the `X-CRON-SECRET` header **if** `CRON_SECRET` is configured:

```bash
curl -X POST https://task-bot-manager.replit.app/checkout-inspections/check-due \
  -H "X-CRON-SECRET: your_cron_secret"
```

If `CRON_SECRET` is not set the endpoints are open (not recommended for production).

---

## API Endpoints

### Health & Debug

| Method | Path | Description |
|---|---|---|
| GET | `/` | Homepage with navigation links |
| GET | `/db/health` | Supabase connection check |
| GET | `/debug/routes` | All registered routes and methods |
| GET | `/storage/health` | Supabase Storage bucket check |

### Hostfully

| Method | Path | Description |
|---|---|---|
| GET | `/hostfully/test` | Test properties API connectivity |
| GET | `/hostfully/leads-test` | Test leads/reservations API — shows raw first record for debugging |
| GET | `/hostfully/properties` | List all properties |
| GET | `/hostfully/guests` | List first 10 guests |
| POST | `/hostfully/sync-checkouts` | Sync checkouts, detect rebookings, send ops messages |

### Checkout Inspections

| Method | Path | Description |
|---|---|---|
| GET | `/checkout-inspections` | List all inspections |
| GET | `/checkout-inspections/pending` | List pending (verification / late / inspection) |
| GET | `/checkout-inspections/{id}` | Get single inspection |
| POST | `/checkout-inspections/{id}/reply` | Handle ops reply (1/2/3) |
| POST | `/checkout-inspections/{id}/no-damage` | Mark no damage → close |
| POST | `/checkout-inspections/{id}/damage-found` | Create linked damage case + notify GM |
| POST | `/checkout-inspections/check-due` | Send due reminders (3h throttle, X-CRON-SECRET) |
| DELETE | `/checkout-inspections/clear-test-data` | Delete records with null `scheduled_checkout_at` |

### Damage Cases

| Method | Path | Description |
|---|---|---|
| GET | `/damage-cases` | List all damage cases |
| GET | `/damage-cases/pending` | List non-closed cases |
| POST | `/damage-cases` | Create new damage case |
| POST | `/damage-cases/check-overdue` | Escalate overdue GM-action cases |
| GET | `/damage-cases/{id}` | Get case with events + photos |
| POST | `/damage-cases/{id}/quote` | Submit GM quote |
| POST | `/damage-cases/{id}/tenant-approved` | Tenant approves deduction |
| POST | `/damage-cases/{id}/tenant-rejected` | Tenant rejects |
| POST | `/damage-cases/{id}/gm-purchased` | GM confirms replacement purchased |
| POST | `/damage-cases/{id}/photo` | Upload placement proof photo |
| POST | `/damage-cases/{id}/replacement-placed` | Confirm replacement placed |
| POST | `/damage-cases/{id}/refund-completed` | Mark refund completed |
| POST | `/damage-cases/{id}/close` | Close/cancel case |

### Tasks & Webhooks

| Method | Path | Description |
|---|---|---|
| GET | `/tasks` | List all tasks |
| POST | `/send-test-task` | Create test task + send WhatsApp |
| GET | `/webhooks/whatsapp` | Meta webhook verification (GET) |
| POST | `/webhooks/whatsapp` | Receive inbound WhatsApp replies |

### Dashboard

| Method | Path | Description |
|---|---|---|
| GET | `/dashboard-view` | HTML dashboard (damage cases + checkout inspections) |
| GET | `/owner-summary` | High-level owner metrics (JSON) |

---

## Checkout Inspection Statuses

| Status | Meaning |
|---|---|
| `checkout_verification_pending` | Waiting for ops to confirm guest has left |
| `rebooked_extension_detected` | Extension/rebooking detected — no inspection needed today |
| `late_checkout` | Guest still inside, follow-up pending |
| `inspection_pending` | Guest left, ops instructed to inspect |
| `no_damage_reported` | Inspection complete, no damage found |
| `damage_reported` | Damage found, damage case being created |
| `issue` | Issue reported, owner/supervisor notified |
| `closed` | Fully resolved |

---

## Rebooking / Extension Detection

Everluxe does not extend existing bookings. When a guest extends, a **new booking** is created in Hostfully. The bot auto-detects this pattern at sync time:

1. For each checkout due today/tomorrow, fetch leads for the same property with check-in within 24 hours of the old checkout date.
2. A rebooking match requires: same `propertyUid` **and** at least one of: same guest UID / same phone / same email / same guest name.
3. **Match found** → set `status = rebooked_extension_detected`, store `linked_new_booking_uid`, update `scheduled_checkout_at` to the new checkout date, send informational WhatsApp to ops.
4. **No match** → set `status = checkout_verification_pending`, send ops verification message.

---

## WhatsApp Reply Routing (Webhook Priority)

Incoming replies are routed in this priority order:

1. **Checkout inspection** — sender has an open inspection (`checkout_verification_pending`, `late_checkout`, or `inspection_pending`):
   - `checkout_verification_pending` / `late_checkout`: `1` = checked out → inspection_pending, `2` = late checkout, `3` = issue
   - `inspection_pending`: `1` = no damage → closed, `2` = damage found → damage_reported
2. **Task reply** — sender has an open task: `1`/`done` = completed, `2`/`delayed` = delayed, `3`/`issue` = issue
3. **Unrecognised** — logged, no crash, no status change

This ensures a reply intended for a checkout inspection never accidentally updates a damage case task.

---

## Hostfully API Notes

- Uses Hostfully **v3** at `https://api.hostfully.com/api/v3/leads`.
- Do **not** use `/bookings` — it returns `"Unknown api: bookings"`.
- Guest info is nested in `guestInformation` (v3) rather than flat top-level fields (v2).
- Date fields: `checkInZonedDateTime` / `checkOutZonedDateTime` (ISO 8601 with timezone offset).
- Server-side date filtering may not be supported in v3 — the bot applies **client-side** filtering to only process checkouts for today and tomorrow.
- Use `/hostfully/leads-test` to inspect the raw first record and confirm field names are as expected.

---

## Supabase Storage

The `damage-photos` bucket stores photo-proof uploads for damage cases. Create it manually in the Supabase dashboard under **Storage**. Verify with `GET /storage/health`.

---

## How to Test End-to-End

```bash
BASE=https://task-bot-manager.replit.app

# 1. Verify secrets + DB
curl $BASE/db/health

# 2. Verify Hostfully properties API
curl $BASE/hostfully/test

# 3. Verify leads/reservations API (v3)
curl $BASE/hostfully/leads-test
# Expect: success=true, guest name populated, checkOut date present

# 4. Run checkout sync
curl -X POST $BASE/hostfully/sync-checkouts

# 5. Check what was created
curl $BASE/checkout-inspections
curl $BASE/checkout-inspections/pending

# 6. Simulate ops reply
curl -X POST $BASE/checkout-inspections/1/reply \
  -H "Content-Type: application/json" \
  -d '{"reply": "1"}'

# 7. Test damage case workflow
curl -X POST $BASE/damage-cases \
  -H "Content-Type: application/json" \
  -d '{
    "unit_name": "Test Villa 101",
    "guest_name": "Test Guest",
    "damage_description": "Broken kettle",
    "deposit_amount": 2000,
    "gm_number": "971501234567",
    "ops_supervisor_number": "971501234568",
    "reservations_number": "971501234569",
    "accounts_number": "971501234570"
  }'

# 8. Run cron endpoints
curl -X POST $BASE/damage-cases/check-overdue -H "X-CRON-SECRET: your_secret"
curl -X POST $BASE/checkout-inspections/check-due -H "X-CRON-SECRET: your_secret"

# 9. Check CORS for Netlify frontend
curl -I -H "Origin: https://ai.stayeverluxe.com" $BASE/db/health | grep access-control

# 10. View dashboard
open $BASE/dashboard-view
```

---

## Setup Checklist

- [ ] All Replit Secrets configured (see table above)
- [ ] `schema.sql` run once in Supabase SQL Editor
- [ ] `task_reminder` WhatsApp template approved in Meta Business Manager
- [ ] `damage-photos` bucket created in Supabase Storage
- [ ] Meta webhook URL set to `https://task-bot-manager.replit.app/webhooks/whatsapp`
- [ ] `HOSTFULLY_BASE_URL=https://api.hostfully.com/api/v3` set in Replit Secrets
- [ ] `CRON_SECRET` set and cron jobs configured for `/damage-cases/check-overdue` and `/checkout-inspections/check-due`
- [ ] `VITE_API_BASE_URL=https://task-bot-manager.replit.app` set in Netlify environment
- [ ] After any code change: **redeploy** Replit Autoscale deployment
- [ ] After any Netlify env change: **trigger redeploy** from Netlify dashboard

---

## Architecture

- **Python 3.11** + **FastAPI** + **Uvicorn**
- **Supabase PostgreSQL** via `supabase-py` v2 (PostgREST REST API — no direct Postgres connection needed)
- **httpx** for async WhatsApp Cloud API and Hostfully API calls
- **Pydantic v2** for request/response validation
- **CORS** configured for `https://ai.stayeverluxe.com`, `localhost:5173`, `localhost:3000`
- Singleton Supabase client initialised at startup; warning logged if secrets are missing

## File Map

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, CORS, webhook routing, task/Hostfully endpoints |
| `checkout_inspections.py` | Checkout inspection workflow + `/hostfully/sync-checkouts` |
| `damage_cases.py` | Damage case workflow + dashboard HTML + owner summary |
| `hostfully.py` | Hostfully API client (`fetch_properties`, `fetch_guests`, `fetch_leads`) |
| `whatsapp.py` | WhatsApp Cloud API client |
| `supabase_client.py` | Supabase singleton + FastAPI dependency |
| `schema.sql` | PostgreSQL DDL — run once in Supabase SQL Editor |
| `schemas.py` | Pydantic response schemas |
