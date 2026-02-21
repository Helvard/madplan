"""
Scraper module — loads offers from Supabase.
"""

import os
import logging
from typing import List, Dict

from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

logger = logging.getLogger(__name__)

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_KEY")
if not _url or not _key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")

_client: Client = create_client(_url, _key)


def load_offers_from_db() -> List[Dict]:
    """Load current offers from Supabase."""
    res = (
        _client.table("offers")
        .select(
            "product_id, name, underline, price, price_numeric, "
            "normal_price, savings_percent, price_per_unit, department, category"
        )
        .order("department")
        .order("price_numeric")
        .execute()
    )
    offers = res.data or []
    logger.info("Loaded %d offers from Supabase", len(offers))
    return offers


def categorize_offers(offers: List[Dict]) -> Dict[str, List[Dict]]:
    """Group offers by department."""
    categorized: Dict[str, List[Dict]] = {}
    for offer in offers:
        dept = offer.get("department") or "Other"
        categorized.setdefault(dept, []).append(offer)
    return categorized


def format_offers_for_claude(offers: List[Dict], max_per_category: int = 20) -> str:
    """Format offers into a readable structure for Claude."""
    categorized = categorize_offers(offers)
    output = ["# Current Rema 1000 Offers\n"]

    for dept, items in sorted(categorized.items()):
        output.append(f"\n## {dept}")
        output.append(f"({len(items)} items available)\n")

        sorted_items = sorted(
            items,
            key=lambda x: x.get("savings_percent") or 0,
            reverse=True,
        )

        for item in sorted_items[:max_per_category]:
            name = item["name"]
            underline = f" - {item['underline']}" if item.get("underline") else ""
            price = item["price"]
            savings = f" (save {item['savings_percent']:.0f}%)" if item.get("savings_percent") else ""
            output.append(f"- {name}{underline}: {price}{savings}")

        if len(items) > max_per_category:
            output.append(f"  _(and {len(items) - max_per_category} more items)_")

    return "\n".join(output)


def get_key_offers(offers: List[Dict], min_savings: float = 30.0, limit: int = 10) -> List[Dict]:
    """Get the best offers (highest savings)."""
    good_offers = [o for o in offers if (o.get("savings_percent") or 0) >= min_savings]
    good_offers.sort(key=lambda x: x.get("savings_percent") or 0, reverse=True)
    return good_offers[:limit]
