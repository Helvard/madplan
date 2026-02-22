-- Madplan — Recipes table migration
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Safe to re-run: all statements use IF NOT EXISTS

-- ============================================================
-- recipes
-- Family Food Almanac — saved recipes per household
-- ============================================================
CREATE TABLE IF NOT EXISTS recipes (
    id                BIGSERIAL PRIMARY KEY,
    household_id      UUID,
    name              TEXT        NOT NULL,
    description       TEXT,
    ingredients       JSONB       DEFAULT '[]',
    -- each element: {"name": "Pasta", "quantity": "400", "unit": "g"}
    instructions      TEXT,
    -- markdown numbered steps
    servings          SMALLINT,
    cook_time_minutes SMALLINT,
    tags              TEXT[]      DEFAULT '{}',
    -- e.g. ["quick", "fish", "kids-love-it"]
    source            TEXT        DEFAULT 'manual',
    -- 'ai_generated' | 'meal_plan' | 'web_import' | 'manual'
    source_url        TEXT,
    rating            SMALLINT    CHECK (rating BETWEEN 1 AND 5),
    notes             TEXT,
    -- family notes, memories, tips
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recipes_household ON recipes (household_id);
CREATE INDEX IF NOT EXISTS idx_recipes_created   ON recipes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recipes_tags      ON recipes USING GIN (tags);
