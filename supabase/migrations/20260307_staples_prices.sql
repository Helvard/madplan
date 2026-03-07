-- FEAT: Rema price tracking on staples
--
-- Adds columns so the scraper can store current Rema 1000 prices for each
-- staple item. The scraper runs after the offers sync and looks up every
-- staple by name in the Algolia catalog (no discount filter).
--
-- Run once in the Supabase SQL Editor.

ALTER TABLE staples
  ADD COLUMN IF NOT EXISTS rema_product_id  TEXT,
  ADD COLUMN IF NOT EXISTS current_price    NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS normal_price     NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS is_on_offer      BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS savings_percent  NUMERIC(5,1),
  ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMPTZ;
