-- Madplan — Supabase schema
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Safe to re-run: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING


-- ============================================================
-- meal_history
-- ============================================================
CREATE TABLE IF NOT EXISTS meal_history (
    id          BIGSERIAL PRIMARY KEY,
    plan_date   DATE        NOT NULL,
    day_number  SMALLINT,
    meal_name   TEXT        NOT NULL,
    ingredients JSONB       DEFAULT '[]',
    cost_estimate NUMERIC(8,2),
    rating      SMALLINT    CHECK (rating BETWEEN 1 AND 5),
    comments    TEXT,
    would_repeat BOOLEAN,
    date_rated  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meal_history_plan_date ON meal_history (plan_date DESC);
CREATE INDEX IF NOT EXISTS idx_meal_history_rating    ON meal_history (rating);


-- ============================================================
-- preferences
-- Single row per application (user_id = 'default' for now)
-- ============================================================
CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT PRIMARY KEY DEFAULT 'default',
    data    JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed the default row so the app always finds one
INSERT INTO preferences (user_id, data) VALUES (
    'default',
    '{
        "family": {
            "size": 5,
            "composition": "2 adults, 3 kids (aged 17, 12, 4)",
            "note": "Big eaters!"
        },
        "cooking": {
            "style": "Simple food with fewer ingredients",
            "priorities": ["Fast", "Healthy", "Cheap"],
            "max_cook_time": 30
        },
        "food": {
            "favorites": [],
            "dislikes": [],
            "dietary_restrictions": []
        },
        "planning": {
            "default_dinners": 7,
            "variety_rule": "No protein repeated 2 days in a row",
            "max_budget": null
        }
    }'::jsonb
) ON CONFLICT (user_id) DO NOTHING;


-- ============================================================
-- shopping_lists
-- ============================================================
CREATE TABLE IF NOT EXISTS shopping_lists (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT        NOT NULL,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    status       TEXT        NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shopping_lists_active ON shopping_lists (is_active);

-- Seed one active list so the app always finds one
INSERT INTO shopping_lists (name, is_active, status)
SELECT 'My Shopping List', TRUE, 'active'
WHERE NOT EXISTS (SELECT 1 FROM shopping_lists WHERE is_active = TRUE);


-- ============================================================
-- shopping_list_items
-- ============================================================
CREATE TABLE IF NOT EXISTS shopping_list_items (
    id             BIGSERIAL PRIMARY KEY,
    list_id        BIGINT      NOT NULL REFERENCES shopping_lists (id) ON DELETE CASCADE,
    item_name      TEXT        NOT NULL,
    quantity       TEXT,
    category       TEXT,
    checked        BOOLEAN     NOT NULL DEFAULT FALSE,
    source         TEXT,
    source_id      TEXT,
    price_estimate NUMERIC(8,2),
    added_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sli_list_id  ON shopping_list_items (list_id);
CREATE INDEX IF NOT EXISTS idx_sli_category ON shopping_list_items (category);
CREATE INDEX IF NOT EXISTS idx_sli_checked  ON shopping_list_items (checked);


-- ============================================================
-- offers
-- Populated by scrape_rema_to_db.py
-- ============================================================
CREATE TABLE IF NOT EXISTS offers (
    product_id      TEXT PRIMARY KEY,
    name            TEXT        NOT NULL,
    underline       TEXT,
    price           TEXT,
    price_numeric   NUMERIC(8,2),
    normal_price    TEXT,
    savings_percent NUMERIC(5,1),
    price_per_unit  TEXT,
    department      TEXT,
    category        TEXT,
    scraped_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_offers_department      ON offers (department);
CREATE INDEX IF NOT EXISTS idx_offers_savings_percent ON offers (savings_percent DESC);
