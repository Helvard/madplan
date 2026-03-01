-- FEAT-2: Staples — recurring household items
--
-- A staple is a named item a household buys regularly (milk, bread, etc.).
-- Users can quickly add all (or selected) staples to their shopping list.
-- times_added tracks frequency; last_added_at helps surface suggestions.
--
-- Run once in the Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS staples (
    id            BIGSERIAL    PRIMARY KEY,
    household_id  BIGINT       NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    item_name     TEXT         NOT NULL,
    category      TEXT,
    quantity      TEXT,
    times_added   INT          NOT NULL DEFAULT 0,
    last_added_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS staples_household_idx ON staples (household_id);
