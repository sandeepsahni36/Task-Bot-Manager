# WhatsApp Task Reminder Bot

A FastAPI backend that sends WhatsApp reminders to holiday-home staff for cleaning, maintenance, inspection, and check-in readiness tasks. Staff reply to update task status.

## Run & Operate

- **WhatsApp Bot workflow** — runs the FastAPI server at port 8000
- `cd artifacts/whatsapp-bot && uvicorn main:app --reload --port 8000` — run manually
- Required secrets: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `TEST_WHATSAPP_TO`

## Stack

- Python 3.11 + FastAPI + Uvicorn
- SQLite via SQLAlchemy ORM
- httpx for async WhatsApp Cloud API calls
- Pydantic v2 for request/response schemas

## Where things live

- `artifacts/whatsapp-bot/main.py` — all API endpoints
- `artifacts/whatsapp-bot/database.py` — SQLAlchemy models + SQLite engine
- `artifacts/whatsapp-bot/schemas.py` — Pydantic response schemas
- `artifacts/whatsapp-bot/whatsapp.py` — WhatsApp Cloud API client
- `artifacts/whatsapp-bot/README.md` — full setup guide including Meta webhook and template setup
- `artifacts/whatsapp-bot/whatsapp_bot.db` — SQLite database (auto-created on first run)

## API Endpoints

- `GET /` — health check
- `GET /webhooks/whatsapp` — Meta webhook verification
- `POST /webhooks/whatsapp` — receive inbound WhatsApp replies, update task status
- `POST /send-test-task` — create a test task and fire WhatsApp template message
- `GET /tasks` — list all tasks with status
- `GET /docs` — interactive Swagger UI

## Architecture decisions

- SQLite hardcoded (not DATABASE_URL) to avoid picking up any PostgreSQL env var in the workspace.
- Webhook reply parsing is case-insensitive: "1"/"done" → completed, "2"/"delayed" → delayed, "3"/"issue" → issue.
- Most-recent open task lookup: when a reply comes in, finds the latest task with `status = "open"` for that phone number.
- Template message uses WhatsApp Cloud API v19.0 with `task_reminder` template (must be pre-approved in Meta Business Manager).

## Product

Staff receive WhatsApp reminders about property tasks. They reply with 1, 2, or 3 to mark tasks as completed, delayed, or having an issue. All messages and status changes are stored in SQLite.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- The `task_reminder` WhatsApp template must be approved in Meta Business Manager before `/send-test-task` will succeed.
- Phone numbers must be in international format without `+` (e.g. `447911123456`).
- Webhook URL must be publicly accessible for Meta to call it — the Replit dev URL works for testing.
- Set `WHATSAPP_VERIFY_TOKEN` in Replit Secrets before Meta webhook verification will pass.

## Pointers

- See `artifacts/whatsapp-bot/README.md` for the full step-by-step setup guide.
