#!/usr/bin/env python3
"""
Rema 1000 Offers Scraper — writes to Supabase.
Run from project root: python meal_planner_web/backend/scrape_rema_to_db.py
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Dict

import requests
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Algolia — Rema 1000's public read-only search API
ALGOLIA_APP_ID  = os.environ.get("ALGOLIA_APP_ID",  "FLWDN2189E")
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY", "fa20981a63df668e871a87a8fbd0caed")
ALGOLIA_INDEX   = "aws-prod-products"
ALGOLIA_URL     = f"https://flwdn2189e-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_KEY")
if not _url or not _key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")

# Use service role key for writes (bypasses RLS); fall back to anon key if not set
_write_key = os.environ.get("SUPABASE_SERVICE_KEY") or _key
_client: Client = create_client(_url, _write_key)


def _format_price(price: float) -> str:
    """Convert numeric price to Danish format: 49,95 kr"""
    if price is None:
        return ""
    return f"{price:.2f} kr".replace(".", ",")


def fetch_offers(limit: int = 500) -> List[Dict]:
    """Fetch all products currently on discount from Rema 1000 via Algolia."""
    params_string = "&".join([
        "query=",
        f"length={limit}",
        "offset=0",
        "clickAnalytics=true",
        'facetFilters=[["labels:on_discount"]]',
        'facets=["labels"]',
    ])

    response = requests.post(
        ALGOLIA_URL,
        params={
            "x-algolia-agent": "Algolia for vanilla JavaScript 3.21.1",
            "x-algolia-application-id": ALGOLIA_APP_ID,
            "x-algolia-api-key": ALGOLIA_API_KEY,
        },
        headers={"accept": "application/json", "content-type": "application/json"},
        json={"params": params_string},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    offers = []
    for hit in data.get("hits", []):
        pricing = hit.get("pricing", {})
        normal_price = pricing.get("normal_price", 0)
        sale_price   = pricing.get("price", 0)
        savings_pct  = 0.0
        if normal_price and sale_price:
            savings_pct = round((1 - sale_price / normal_price) * 100, 1)

        offers.append({
            "product_id":      str(hit.get("objectID")),
            "name":            hit.get("name"),
            "underline":       hit.get("underline"),
            "price":           _format_price(sale_price),
            "price_numeric":   sale_price,
            "normal_price":    _format_price(normal_price) if normal_price else None,
            "savings_percent": savings_pct,
            "price_per_unit":  pricing.get("price_per_unit"),
            "department":      hit.get("department_name"),
            "category":        hit.get("category_name"),
            "scraped_at":      datetime.now(timezone.utc).isoformat(),
        })

    return offers


def sync_offers(offers: List[Dict]):
    """
    Replace all offers in Supabase atomically:
    delete existing rows, then insert fresh ones.
    The delete only happens after a successful fetch.
    """
    if not offers:
        logger.warning("No offers fetched — aborting sync to avoid wiping the table.")
        return 0

    logger.info("Deleting existing offers...")
    _client.table("offers").delete().neq("product_id", "").execute()

    logger.info("Inserting %d offers...", len(offers))
    # Supabase has a default row limit per request; batch in chunks of 200
    chunk_size = 200
    inserted = 0
    for i in range(0, len(offers), chunk_size):
        chunk = offers[i : i + chunk_size]
        _client.table("offers").insert(chunk).execute()
        inserted += len(chunk)

    return inserted


def search_product(query: str) -> Dict | None:
    """Search Algolia catalog (no discount filter) for a single product by name.
    Returns the top hit or None if nothing found."""
    import urllib.parse
    params_string = "&".join([
        f"query={urllib.parse.quote(query)}",
        "length=1",
        "offset=0",
    ])
    try:
        response = requests.post(
            ALGOLIA_URL,
            params={
                "x-algolia-agent": "Algolia for vanilla JavaScript 3.21.1",
                "x-algolia-application-id": ALGOLIA_APP_ID,
                "x-algolia-api-key": ALGOLIA_API_KEY,
            },
            headers={"accept": "application/json", "content-type": "application/json"},
            json={"params": params_string},
            timeout=10,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        return hits[0] if hits else None
    except Exception as e:
        logger.warning("Algolia search failed for %r: %s", query, e)
        return None


def sync_staple_prices():
    """After the offers sync, look up current Rema prices for all staples.

    For each staple item, searches Algolia by name (full catalog, not just
    discounted items) and updates the staples table with the current price and
    whether the item is currently on offer.
    """
    res = _client.table("staples").select("id, item_name").execute()
    staples = res.data or []
    if not staples:
        logger.info("No staples found — skipping price sync.")
        return

    logger.info("Syncing Rema prices for %d staple(s)...", len(staples))
    updated = 0
    for staple in staples:
        hit = search_product(staple["item_name"])
        if not hit:
            logger.debug("No Rema match for staple %r", staple["item_name"])
            continue

        pricing = hit.get("pricing", {})
        sale_price   = pricing.get("price") or 0
        normal_price = pricing.get("normal_price")
        labels       = hit.get("labels") or []
        is_on_offer  = "on_discount" in labels

        savings_pct = None
        if is_on_offer and normal_price and sale_price:
            savings_pct = round((1 - sale_price / normal_price) * 100, 1)

        _client.table("staples").update({
            "rema_product_id":  str(hit.get("objectID")),
            "current_price":    sale_price if sale_price else None,
            "normal_price":     normal_price if is_on_offer else None,
            "is_on_offer":      is_on_offer,
            "savings_percent":  savings_pct,
            "price_updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", staple["id"]).execute()
        updated += 1

    logger.info("Staple price sync complete: %d/%d updated.", updated, len(staples))


def print_summary(offers: List[Dict]):
    """Log a brief summary of synced offers."""
    from collections import Counter
    dept_counts = Counter(o.get("department") or "Other" for o in offers)
    savings = [o["savings_percent"] for o in offers if o.get("savings_percent")]
    avg_savings = sum(savings) / len(savings) if savings else 0

    logger.info("=" * 50)
    logger.info("SYNC SUMMARY")
    logger.info("Total offers: %d", len(offers))
    logger.info("Average savings: %.1f%%", avg_savings)
    logger.info("Top departments:")
    for dept, count in dept_counts.most_common(5):
        logger.info("  • %s: %d", dept, count)
    logger.info("=" * 50)


def main():
    logger.info("Rema 1000 scraper starting...")

    logger.info("Fetching offers from Algolia...")
    offers = fetch_offers(limit=500)
    logger.info("Fetched %d offers on sale", len(offers))

    inserted = sync_offers(offers)
    logger.info("Inserted %d offers into Supabase", inserted)

    print_summary(offers)

    sync_staple_prices()
    logger.info("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Cancelled by user.")
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        raise
