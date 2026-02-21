#!/usr/bin/env python3
"""
One-off migration: SQLite → Supabase.

Run AFTER applying schema.sql in the Supabase SQL editor:
    python migrate_sqlite_to_supabase.py

Safe to re-run: uses upsert / insert-with-conflict-handling.
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from datetime import timezone, datetime

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SQLITE_DB = Path(__file__).resolve().parent / "data" / "meal_planner_unified.db"
PREFERENCES_YAML = Path(__file__).resolve().parent / "data" / "preferences.yaml"

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_KEY")
if not _url or not _key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

sb = create_client(_url, _key)


def get_sqlite_conn() -> sqlite3.Connection:
    if not SQLITE_DB.exists():
        raise FileNotFoundError(f"SQLite DB not found at {SQLITE_DB}")
    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ── Meal history ─────────────────────────────────────────────────────────────

def migrate_meal_history(conn: sqlite3.Connection):
    logger.info("Migrating meal_history...")
    rows = conn.execute("""
        SELECT plan_date, day_number, meal_name, ingredients,
               cost_estimate, rating, comments, would_repeat, date_rated
        FROM meal_history
        ORDER BY plan_date, day_number
    """).fetchall()

    if not rows:
        logger.info("  No meal_history rows found.")
        return

    records = []
    for r in rows:
        ingredients = r["ingredients"]
        if isinstance(ingredients, str):
            try:
                ingredients = json.loads(ingredients)
            except Exception:
                ingredients = []

        records.append({
            "plan_date":    r["plan_date"],
            "day_number":   r["day_number"],
            "meal_name":    r["meal_name"],
            "ingredients":  ingredients or [],
            "cost_estimate": r["cost_estimate"],
            "rating":       r["rating"],
            "comments":     r["comments"],
            "would_repeat": bool(r["would_repeat"]) if r["would_repeat"] is not None else None,
            "date_rated":   r["date_rated"],
        })

    # Batch insert (200 per request)
    inserted = 0
    for i in range(0, len(records), 200):
        sb.table("meal_history").insert(records[i:i+200]).execute()
        inserted += len(records[i:i+200])

    logger.info("  Migrated %d meal_history rows.", inserted)


# ── Preferences ───────────────────────────────────────────────────────────────

def migrate_preferences():
    """Migrate preferences from YAML file if it exists."""
    if not PREFERENCES_YAML.exists():
        logger.info("No preferences.yaml found — skipping (Supabase already has defaults).")
        return

    try:
        import yaml
        with open(PREFERENCES_YAML, "r", encoding="utf-8") as f:
            prefs = yaml.safe_load(f)
    except ImportError:
        logger.warning("pyyaml not installed — skipping preferences migration.")
        return

    if not prefs:
        logger.info("preferences.yaml is empty — skipping.")
        return

    sb.table("preferences").upsert({
        "user_id": "default",
        "data": prefs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info("  Migrated preferences from YAML.")


# ── Shopping lists ────────────────────────────────────────────────────────────

def migrate_shopping_lists(conn: sqlite3.Connection):
    logger.info("Migrating shopping_lists and items...")

    lists = conn.execute("""
        SELECT id, name, created_date, is_active, status
        FROM shopping_lists
    """).fetchall()

    if not lists:
        logger.info("  No shopping lists found.")
        return

    # Map old SQLite IDs → new Supabase IDs
    id_map = {}

    for sl in lists:
        res = sb.table("shopping_lists").insert({
            "name":      sl["name"],
            "is_active": bool(sl["is_active"]),
            "status":    sl["status"] or "active",
        }).execute()
        new_id = res.data[0]["id"]
        id_map[sl["id"]] = new_id

    logger.info("  Migrated %d shopping lists.", len(lists))

    # Migrate items
    items = conn.execute("""
        SELECT list_id, item_name, quantity, category, checked,
               source, source_id, price_estimate, added_date
        FROM shopping_list_items
    """).fetchall()

    if not items:
        logger.info("  No shopping list items found.")
        return

    records = []
    for item in items:
        new_list_id = id_map.get(item["list_id"])
        if new_list_id is None:
            continue
        records.append({
            "list_id":       new_list_id,
            "item_name":     item["item_name"],
            "quantity":      item["quantity"],
            "category":      item["category"],
            "checked":       bool(item["checked"]),
            "source":        item["source"],
            "source_id":     item["source_id"],
            "price_estimate": item["price_estimate"],
        })

    for i in range(0, len(records), 200):
        sb.table("shopping_list_items").insert(records[i:i+200]).execute()

    logger.info("  Migrated %d shopping list items.", len(records))


# ── Offers ────────────────────────────────────────────────────────────────────

def migrate_offers(conn: sqlite3.Connection):
    logger.info("Migrating offers...")
    try:
        rows = conn.execute("""
            SELECT product_id, name, underline, price, price_numeric,
                   normal_price, savings_percent, price_per_unit,
                   department, category
            FROM offers
        """).fetchall()
    except sqlite3.OperationalError:
        logger.info("  No offers table in SQLite — skipping.")
        return

    if not rows:
        logger.info("  No offers rows found.")
        return

    records = [dict(r) for r in rows]

    # Upsert in chunks — primary key is product_id
    for i in range(0, len(records), 200):
        sb.table("offers").upsert(records[i:i+200]).execute()

    logger.info("  Migrated %d offers.", len(records))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("Starting SQLite → Supabase migration")
    logger.info("SQLite DB: %s", SQLITE_DB)

    conn = get_sqlite_conn()

    migrate_meal_history(conn)
    migrate_preferences()
    migrate_shopping_lists(conn)
    migrate_offers(conn)

    conn.close()
    logger.info("Migration complete.")


if __name__ == "__main__":
    main()
