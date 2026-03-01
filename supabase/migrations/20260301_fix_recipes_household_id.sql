-- BUG-4: Fix type mismatch on recipes.household_id
--
-- schema_recipes.sql defined household_id as UUID, but households.id is BIGSERIAL (BIGINT).
-- This caused a PostgreSQL type mismatch error whenever /recipes was loaded.
--
-- Run this once in the Supabase SQL Editor.

ALTER TABLE recipes
    ALTER COLUMN household_id TYPE BIGINT
    USING CASE
        WHEN household_id IS NULL THEN NULL
        ELSE household_id::text::bigint
    END;

ALTER TABLE recipes
    ADD CONSTRAINT fk_recipes_household
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE;
