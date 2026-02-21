-- Auth migration — run in Supabase SQL Editor AFTER schema.sql
-- Adds households, user_profiles, and migrates existing tables to be household-scoped.

-- ============================================================
-- households
-- ============================================================
CREATE TABLE IF NOT EXISTS households (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    invite_code TEXT        NOT NULL UNIQUE DEFAULT substring(md5(random()::text), 1, 8),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- user_profiles
-- Links auth.users → household. One row per registered user.
-- ============================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id           UUID        PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    email        TEXT        NOT NULL,
    household_id BIGINT      REFERENCES households (id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_household ON user_profiles (household_id);

-- ============================================================
-- Migrate meal_history: add household_id column
-- ============================================================
ALTER TABLE meal_history
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households (id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_meal_history_household ON meal_history (household_id);

-- ============================================================
-- Migrate shopping_lists: add household_id column
-- ============================================================
ALTER TABLE shopping_lists
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households (id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_shopping_lists_household ON shopping_lists (household_id);

-- ============================================================
-- Migrate preferences: replace TEXT user_id with household_id
-- ============================================================
ALTER TABLE preferences
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households (id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_preferences_household ON preferences (household_id);

-- ============================================================
-- Seed: create a default household for existing data
-- and link the old 'default' preferences row to it
-- ============================================================
DO $$
DECLARE
    hid BIGINT;
BEGIN
    -- Create default household if none exist
    INSERT INTO households (name, invite_code)
    SELECT 'Default Household', 'default0'
    WHERE NOT EXISTS (SELECT 1 FROM households WHERE invite_code = 'default0');

    SELECT id INTO hid FROM households WHERE invite_code = 'default0';

    -- Link existing preferences row
    UPDATE preferences SET household_id = hid WHERE user_id = 'default' AND household_id IS NULL;

    -- Link existing meal_history rows
    UPDATE meal_history SET household_id = hid WHERE household_id IS NULL;

    -- Link existing shopping_lists rows
    UPDATE shopping_lists SET household_id = hid WHERE household_id IS NULL;
END $$;
