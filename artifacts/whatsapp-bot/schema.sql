-- ============================================================
-- Everluxe WhatsApp Bot — Supabase PostgreSQL schema
-- Run this in the Supabase SQL Editor for your project.
-- ============================================================

CREATE TABLE IF NOT EXISTS staff (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    whatsapp_number TEXT UNIQUE NOT NULL,
    role    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                      SERIAL PRIMARY KEY,
    staff_whatsapp_number   TEXT NOT NULL,
    property_name           TEXT NOT NULL,
    task_description        TEXT NOT NULL,
    due_time                TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'open',
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id                      SERIAL PRIMARY KEY,
    task_id                 INTEGER REFERENCES tasks(id),
    staff_whatsapp_number   TEXT NOT NULL,
    direction               TEXT NOT NULL,
    message_text            TEXT,
    raw_payload             TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS damage_cases (
    id                      SERIAL PRIMARY KEY,
    hostfully_property_uid  TEXT,
    hostfully_guest_uid     TEXT,
    unit_name               TEXT NOT NULL,
    guest_name              TEXT NOT NULL,
    guest_phone             TEXT,
    guest_email             TEXT,
    damage_description      TEXT NOT NULL,
    deposit_amount          DOUBLE PRECISION NOT NULL DEFAULT 0,
    damage_amount           DOUBLE PRECISION DEFAULT 0,
    other_charges           DOUBLE PRECISION DEFAULT 0,
    refund_amount           DOUBLE PRECISION,
    status                  TEXT NOT NULL DEFAULT 'quote_pending',
    waiting_on              TEXT,
    reported_by_number      TEXT,
    gm_number               TEXT NOT NULL,
    ops_supervisor_number   TEXT NOT NULL,
    reservations_number     TEXT NOT NULL,
    accounts_number         TEXT NOT NULL,
    photo_proof_received    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    due_at                  TIMESTAMPTZ,
    closed_at               TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS damage_events (
    id              SERIAL PRIMARY KEY,
    damage_case_id  INTEGER NOT NULL REFERENCES damage_cases(id),
    event_type      TEXT NOT NULL,
    message         TEXT,
    whatsapp_number TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS damage_photos (
    id                      SERIAL PRIMARY KEY,
    damage_case_id          INTEGER NOT NULL REFERENCES damage_cases(id),
    photo_url_or_media_id   TEXT NOT NULL,
    photo_type              TEXT NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tasks_staff_number        ON tasks(staff_whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_tasks_status              ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_wa_messages_staff_number  ON whatsapp_messages(staff_whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_damage_cases_status       ON damage_cases(status);
CREATE INDEX IF NOT EXISTS idx_damage_cases_hostfully    ON damage_cases(hostfully_property_uid);
CREATE INDEX IF NOT EXISTS idx_damage_events_case_id     ON damage_events(damage_case_id);
CREATE INDEX IF NOT EXISTS idx_damage_photos_case_id     ON damage_photos(damage_case_id);
