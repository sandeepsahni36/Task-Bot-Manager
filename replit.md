# WhatsApp Task Reminder Bot

A FastAPI backend that sends WhatsApp reminders to holiday-home staff for cleaning, maintenance, inspection, and check-in readiness tasks. Staff reply to update task status.

## Run & Operate

- **WhatsApp Bot workflow** — runs the FastAPI server at port 8000
- `cd artifacts/whatsapp-bot && uvicorn main:app --reload --port 8000` — run manually
- Required secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `TEST_WHATSAPP_TO`, `OWNER_WHATSAPP_NUMBER`, `DEFAULT_OPS_WHATSAPP_NUMBER`

## Stack

- Python 3.11 + FastAPI + Uvicorn
- Supabase PostgreSQL via `supabase-py` v2 (REST/PostgREST client)
- httpx for async WhatsApp Cloud API calls
- Pydantic v2 for request/response schemas

## Where things live

- `artifacts/whatsapp-bot/main.py` — all API endpoints (tasks, webhooks, health, Hostfully)
- `artifacts/whatsapp-bot/damage_cases.py` — damage case workflow endpoints + dashboard
- `artifacts/whatsapp-bot/checkout_inspections.py` — checkout inspection endpoints + `/hostfully/sync-checkouts`
- `artifacts/whatsapp-bot/supabase_client.py` — Supabase singleton client + FastAPI dependency
- `artifacts/whatsapp-bot/schema.sql` — PostgreSQL DDL; run once in Supabase SQL Editor
- `artifacts/whatsapp-bot/schemas.py` — Pydantic response schemas
- `artifacts/whatsapp-bot/whatsapp.py` — WhatsApp Cloud API client
- `artifacts/whatsapp-bot/hostfully.py` — Hostfully PMS integration (properties, guests, bookings)
- `artifacts/whatsapp-bot/README.md` — full setup guide

## API Endpoints

- `GET /` — homepage
- `GET /db/health` — confirm Supabase connection and schema are working
- `GET /webhooks/whatsapp` — Meta webhook verification
- `POST /webhooks/whatsapp` — receive inbound WhatsApp replies, update task status
- `POST /send-test-task` — create a test task and fire WhatsApp template message
- `GET /tasks` — list all tasks with status
- `GET /damage-cases` — list all damage cases
- `GET /damage-cases/pending` — list non-closed damage cases
- `POST /damage-cases/check-overdue` — escalate overdue gm_action_pending cases (6h throttle)
- `GET /owner-summary` — high-level owner dashboard JSON
- `GET /dashboard-view` — HTML dashboard (damage cases + checkout inspections)
- `POST /hostfully/sync-checkouts` — sync Hostfully checkouts, detect rebookings, send ops messages
- `GET /checkout-inspections` — list all checkout inspections
- `GET /checkout-inspections/pending` — list pending checkout inspections
- `POST /checkout-inspections/check-due` — send reminders for due inspections (3h throttle)
- `POST /checkout-inspections/{id}/reply` — handle ops reply (1/2/3)
- `GET /docs` — interactive Swagger UI

## Architecture decisions

- Supabase Python client (`supabase-py` v2) uses PostgREST REST API with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. No direct Postgres connection string needed.
- Singleton client initialised at startup; warning logged if secrets are missing.
- Webhook reply parsing is case-insensitive: "1"/"done" → completed, "2"/"delayed" → delayed, "3"/"issue" → issue.
- Most-recent open task lookup: when a reply comes in, finds the latest task with `status = "open"` for that phone number.
- Damage case workflow: strict status ordering enforced at each transition, 400 with current/required status on mismatch.
- Auto due_at deadlines at each transition (24h/46h rules).
- 6-hour per-recipient overdue escalation throttle on `check-overdue` endpoint.
- Photo-proof gate before `replacement-placed` transition.
- Template message uses WhatsApp Cloud API v19.0 with `task_reminder` template (must be pre-approved in Meta Business Manager).
- Checkout inspection workflow: `sync-checkouts` deduplicates by `hostfully_reservation_uid`. Rebooking detection matches by property UID + (guest UID | phone | email | name) with check-in within 24h of old checkout. Rebooked cases are flagged without messaging ops. Standard checkouts send a verification message to `DEFAULT_OPS_WHATSAPP_NUMBER`.
- `check-due` reminder throttle: 3 hours per inspection record (tracks via `last_message_sent_at`).
- Extensions/rebookings: Everluxe creates a new booking rather than extending; the bot detects this pattern automatically and moves the inspection to the new checkout date.

## Product

Staff receive WhatsApp reminders about property tasks. They reply with 1, 2, or 3 to mark tasks as completed, delayed, or having an issue. A full damage case workflow tracks property damages from discovery through refund, with Hostfully PMS integration. All data is stored in Supabase PostgreSQL.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- The `task_reminder` WhatsApp template must be approved in Meta Business Manager before `/send-test-task` will succeed.
- Phone numbers must be in international format without `+` (e.g. `447911123456`).
- Webhook URL must be publicly accessible for Meta to call it — the Replit dev URL works for testing.
- Set `WHATSAPP_VERIFY_TOKEN` in Replit Secrets before Meta webhook verification will pass.

## Pointers

- See `artifacts/whatsapp-bot/README.md` for the full step-by-step setup guide.
