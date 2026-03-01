-- FEAT-1 Stage A: Member likes/dislikes preferences
--
-- Creates a per-household member profile table.
-- Also adds member_pref_hits JSONB column to meal_history for Stage C.
--
-- Run once in the Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS member_preferences (
    id                   BIGSERIAL    PRIMARY KEY,
    household_id         BIGINT       NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- NULL if this is a managed-only profile (no linked login)
    user_profile_id      UUID         REFERENCES user_profiles(id) ON DELETE SET NULL,

    display_name         TEXT         NOT NULL,

    -- Soft preferences — freetext arrays
    liked_meals          TEXT[]       DEFAULT '{}',
    disliked_meals       TEXT[]       DEFAULT '{}',
    liked_ingredients    TEXT[]       DEFAULT '{}',
    disliked_ingredients TEXT[]       DEFAULT '{}',

    -- Whether a linked user may edit their own row
    can_self_edit        BOOLEAN      DEFAULT FALSE,

    created_at           TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS member_preferences_household_idx ON member_preferences (household_id);
CREATE INDEX IF NOT EXISTS member_preferences_user_idx      ON member_preferences (user_profile_id);

-- Stage C: track which members liked/disliked each rated meal
ALTER TABLE meal_history
    ADD COLUMN IF NOT EXISTS member_pref_hits JSONB DEFAULT '{}';
-- Schema: {"liked_hits": ["Bertil: pasta"], "disliked_hits": ["Bertil: mashed potatoes"]}
