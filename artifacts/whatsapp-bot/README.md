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
Returns all tasks with their current status.

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
