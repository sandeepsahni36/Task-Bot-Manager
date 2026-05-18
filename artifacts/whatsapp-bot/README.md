# WhatsApp Task Reminder Bot

A FastAPI backend that sends WhatsApp reminders to holiday-home staff for cleaning, maintenance, inspection, and check-in tasks. Staff reply with a number to update the task status.

---

## How It Works

1. You call `POST /send-test-task` to create a task and send a WhatsApp template message.
2. The staff member receives the message and replies: `1` (done), `2` (delayed), or `3` (issue).
3. The webhook receives the reply and updates the task status in SQLite.

---

## Running on Replit

### 1. Set Replit Secrets

Go to **Tools → Secrets** in your Replit workspace and add:

| Secret name | Value |
|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Your Meta Cloud API permanent access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Your WhatsApp Business phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Any random string you choose (e.g. `my_secret_token_123`) |
| `TEST_WHATSAPP_TO` | A WhatsApp number to receive test messages (e.g. `447911123456`) |

> **Phone number format:** Include country code, no `+`, no spaces. Example: `447911123456`

### 2. Start the Server

The workflow is pre-configured. Click **Run** or start the `WhatsApp Bot` workflow. The server starts on port 8000.

### 3. Set the Webhook URL in Meta

In the [Meta Developer Console](https://developers.facebook.com/), go to your app → **WhatsApp → Configuration → Webhooks**:

- **Callback URL:** `https://<your-repl-domain>/webhooks/whatsapp`
- **Verify token:** The same value you set as `WHATSAPP_VERIFY_TOKEN`

Subscribe to the **messages** field.

> Your Repl's public domain looks like `https://your-project-name.your-username.repl.co`

---

## API Endpoints

### `GET /`
Health check.

```json
{ "status": "ok", "service": "WhatsApp Task Reminder Bot" }
```

---

### `GET /webhooks/whatsapp`
Meta webhook verification. Called automatically by Meta when you save the webhook URL.

**Query params (sent by Meta):**
- `hub.mode`
- `hub.verify_token`
- `hub.challenge`

Returns the challenge as plain text if the token matches, otherwise `403`.

---

### `POST /webhooks/whatsapp`
Receives incoming WhatsApp messages from Meta.

- Logs the full payload.
- Parses reply text:
  - `1` or `done` → **completed**
  - `2` or `delayed` → **delayed**
  - `3` or `issue` → **issue**
- Saves the message to the database.
- Updates the most recent open task for that WhatsApp number.

---

### `POST /send-test-task`
Creates a test task in SQLite and sends a WhatsApp template message to `TEST_WHATSAPP_TO`.

**Template:** `hello_world` with variables:
1. Staff name
2. Property name
3. Task description
4. Due time

> Make sure your `hello_world` template is approved in Meta Business Manager.

---

### `GET /tasks`
Returns all tasks with their current status, newest first.

---

### `POST /tasks/{task_id}/close`
Marks a task as `closed`/cancelled. Useful for cancelling a task without deleting it.

---

### `DELETE /tasks/{task_id}`
Permanently deletes a single task by ID.

---

### `POST /tasks/clear-test`
Deletes all tasks where `property_name = "Sunset Villa"`. Use this to wipe test data created by `/send-test-task` without touching real tasks.

```bash
curl -X POST https://<your-repl-domain>/tasks/clear-test
```

---

## Damage Case Workflow

### Overview

Operations discovers damage in a unit → GM is chased for a quote → Reservations shares charges with tenant → Tenant approves → GM purchases and places replacement → Photo proof required → Accounts processes refund → Case closed.

### Workflow Order & Validation

Steps must be called in strict order. Calling a step out of sequence returns `HTTP 400` with the current and required status:

```json
{
  "error": "Invalid workflow step — case is not in the required status.",
  "current_status": "quote_pending",
  "required_status": "tenant_approval_pending"
}
```

| Step | Endpoint | Required status | Extra condition |
|---|---|---|---|
| 1 | `POST /damage-cases` | — | Creates case, status → `quote_pending` |
| 2 | `POST /{id}/quote` | `quote_pending` | — |
| 3 | `POST /{id}/tenant-approved` | `tenant_approval_pending` | `refund_amount` must not be null |
| 4 | `POST /{id}/gm-purchased` | `gm_action_pending` | — |
| 5 | `POST /{id}/photo` | any status | Sets `photo_proof_received = true` |
| 6 | `POST /{id}/replacement-placed` | `placement_proof_pending` | `photo_proof_received` must be `true` |
| 7 | `POST /{id}/refund-completed` | `accounts_refund_pending` | — |

`POST /{id}/cancel` can be called at any status.

### Deadline Rules (`due_at`)

`due_at` is set automatically on every status transition — it is not accepted as a request body field. Closed and cancelled cases always have `due_at = null`.

| Status entered | Deadline | Set by |
|---|---|---|
| `quote_pending` | **now + 24 h** | Case creation |
| `tenant_approval_pending` | **now + 24 h** | `POST /{id}/quote` |
| `gm_action_pending` | **now + 46 h** | `POST /{id}/tenant-approved` |
| `placement_proof_pending` | **now + 24 h** | `POST /{id}/gm-purchased` |
| `accounts_refund_pending` | **now + 24 h** | `POST /{id}/replacement-placed` |
| `closed` / `cancelled` | **null** | `refund-completed` / `cancel` |

The dashboard "Due In / Overdue" column shows remaining time in green or elapsed time in red (⚠). An "Overdue" stat card counts all breached deadlines at a glance.

### Statuses

| Status | Meaning |
|---|---|
| `quote_pending` | Waiting on GM to provide damage quote |
| `tenant_approval_pending` | Waiting on Reservations/Ops to confirm tenant approved |
| `gm_action_pending` | Tenant approved — GM + Ops must purchase and place item |
| `placement_proof_pending` | Waiting on GM to send photo proof of placement |
| `accounts_refund_pending` | Waiting on Accounts to process refund |
| `closed` | Refund complete |
| `cancelled` | Case cancelled |

### Endpoints

**`POST /damage-cases`** — Create a new damage case. Required fields: `unit_name` or `hostfully_property_uid`, `guest_name`, `damage_description`, `deposit_amount`, `gm_number`, `ops_supervisor_number`, `reservations_number`, `accounts_number`. On creation, a WhatsApp message is sent to the GM asking for a quote.

```json
{
  "unit_name": "Sunset Villa",
  "guest_name": "John Smith",
  "guest_phone": "971501234567",
  "damage_description": "Broken TV in master bedroom",
  "deposit_amount": 2000,
  "gm_number": "971501234567",
  "ops_supervisor_number": "971507654321",
  "reservations_number": "971509876543",
  "accounts_number": "971502345678"
}
```

**`GET /damage-cases`** — All cases, newest first.

**`GET /damage-cases/pending`** — Only non-closed, non-cancelled cases.

**`GET /damage-cases/{id}`** — Single case with full event log and photos.

**`POST /damage-cases/{id}/quote`** — Submit damage quote. Calculates `refund = deposit - damage - other_charges`. Notifies Reservations.
```json
{ "damage_amount": 500, "other_charges": 100, "notes": "TV replacement" }
```

**`POST /damage-cases/{id}/tenant-approved`** — Tenant agreed to charges. Notifies GM and Ops Supervisor.

**`POST /damage-cases/{id}/gm-purchased`** — GM confirms item purchased. Asks GM for photo proof.

**`POST /damage-cases/{id}/photo`** — Upload photo proof (URL or WhatsApp media ID).
```json
{ "photo_url_or_media_id": "https://...", "photo_type": "placement_proof" }
```

**`POST /damage-cases/{id}/replacement-placed`** — Confirm item placed. Requires photo proof. Notifies Accounts with full financial summary.

**`POST /damage-cases/{id}/refund-completed`** — Close case after refund is done.

**`POST /damage-cases/{id}/cancel`** — Cancel the case.

**`GET /owner-summary`** — JSON summary: total pending, overdue, broken down by waiting_on, missing photos, closed today, top 10 oldest open cases.

**`POST /send-owner-summary`** — Builds the same summary and sends it as a WhatsApp message to `OWNER_WHATSAPP_NUMBER`. Add that secret in **Tools → Secrets** before calling this endpoint. Returns the summary data plus the WhatsApp API result.

Example message sent to owner:
```
Damage Cases Summary
━━━━━━━━━━━━━━━━━━
📋 Total pending: 3
⏰ Overdue: 1
👤 Waiting on GM: 1
🔧 Waiting on Ops: 2
🏠 Waiting on Reservations: 0
💰 Waiting on Accounts: 1
📷 Missing photo proof: 2
━━━━━━━━━━━━━━━━━━
Top 5 Oldest Open Cases:
  1. #4 Sunset Villa – John Smith (GM Action Pending, waiting: GM + Ops, age: 5d)
  ...
```

**`GET /dashboard-view`** — HTML dashboard grouped by status with all case details. Open directly in a browser.

---

## Hostfully Integration

### Setup

Add these three secrets in **Tools → Secrets**:

| Secret | Value |
|---|---|
| `HOSTFULLY_API_KEY` | Your Hostfully API key |
| `HOSTFULLY_AGENCY_UID` | Your agency UID from Hostfully |
| `HOSTFULLY_BASE_URL` | API base URL (e.g. `https://api.hostfully.com/v2`) |

### Endpoints

**`GET /hostfully/test`**
Quick connectivity check. Returns `success: true/false`, the HTTP status code from Hostfully, and the first 3 property names if the connection succeeds.

**`GET /hostfully/properties`**
Returns all properties with UID, name, address, city, and active/status fields.

**`GET /hostfully/guests`**
Returns the first 10 guests with UID, name, email, and phone.

> The API key is never returned in any response — it is only sent as a request header to Hostfully.

---

## WhatsApp Template Setup

Before using `/send-test-task`, you must create and get approved a template named `hello_world` in [Meta Business Manager](https://business.facebook.com/) → **Account Tools → Message Templates**.

**Suggested template body:**
```
Hi {{1}}, you have a task at {{2}}: {{3}}. Please complete by {{4}}.

Reply with:
1 - Done
2 - Delayed
3 - Issue
```

---

## Database

SQLite file: `whatsapp_bot.db` (created automatically on first run).

### Tables

**staff** — `id`, `name`, `whatsapp_number`, `role`

**tasks** — `id`, `staff_whatsapp_number`, `property_name`, `task_description`, `due_time`, `status`, `created_at`, `updated_at`

**whatsapp_messages** — `id`, `task_id`, `staff_whatsapp_number`, `direction`, `message_text`, `raw_payload`, `created_at`

---

## Local Development (outside Replit)

```bash
cd artifacts/whatsapp-bot
pip install -r requirements.txt
export WHATSAPP_ACCESS_TOKEN=your_token
export WHATSAPP_PHONE_NUMBER_ID=your_phone_id
export WHATSAPP_VERIFY_TOKEN=your_verify_token
export TEST_WHATSAPP_TO=447911123456
uvicorn main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs
