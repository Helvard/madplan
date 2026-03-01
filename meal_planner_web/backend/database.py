"""
Database module for meal planner — Supabase backend.
Replaces the SQLite-based implementation.
"""

import os
from datetime import datetime, timezone
from typing import List, Dict, Optional

from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_KEY")
if not _url or not _key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")

# Prefer the service role key for DB operations so RLS doesn't block server-side queries.
# Falls back to the anon key if SUPABASE_SERVICE_KEY is not set.
_service_key = os.environ.get("SUPABASE_SERVICE_KEY") or _key
_client: Client = create_client(_url, _service_key)

# Default preferences structure — used when no row exists yet
DEFAULT_PREFERENCES = {
    "family": {
        "size": 5,
        "composition": "2 adults, 3 kids (aged 17, 12, 4)",
        "note": "Big eaters!",
    },
    "cooking": {
        "style": "Simple food with fewer ingredients",
        "priorities": ["Fast", "Healthy", "Cheap"],
        "max_cook_time": 30,
    },
    "food": {
        "favorites": [],
        "dislikes": [],
        "dietary_restrictions": [],
    },
    "planning": {
        "default_dinners": 7,
        "variety_rule": "No protein repeated 2 days in a row",
        "max_budget": None,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Supabase-backed database handler for meal planner."""

    def __init__(self):
        self.db = _client

    # ========== MEAL HISTORY ==========

    def save_meal_plan(self, plan_date: str, meals: List[Dict], household_id: int = None):
        """Save a complete meal plan to history."""
        rows = [
            {
                "plan_date": plan_date,
                "day_number": i + 1,
                "meal_name": meal.get("name"),
                "ingredients": meal.get("ingredients", []),
                "cost_estimate": meal.get("cost"),
                **({"household_id": household_id} if household_id else {}),
            }
            for i, meal in enumerate(meals)
        ]
        self.db.table("meal_history").insert(rows).execute()

    def get_meal_history(self, limit: int = 30, household_id: int = None) -> List[Dict]:
        """Get recent meal history."""
        q = (
            self.db.table("meal_history")
            .select("id, plan_date, day_number, meal_name, ingredients, cost_estimate, rating, comments, would_repeat")
            .order("plan_date", desc=True)
            .order("day_number")
            .limit(limit)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        return q.execute().data or []

    def rate_meal(self, meal_id: int, rating: int, comments: str = None, would_repeat: bool = True):
        """Rate a meal from history."""
        self.db.table("meal_history").update({
            "rating": rating,
            "comments": comments,
            "would_repeat": would_repeat,
            "date_rated": _now(),
        }).eq("id", meal_id).execute()

    def get_meal_history_for_context(self, weeks_back: int = 4, household_id: int = None) -> str:
        """Get meal history formatted for Claude's context."""
        from datetime import date, timedelta
        cutoff_date = (date.today() - timedelta(weeks=weeks_back)).isoformat()

        def _filter(q):
            if household_id:
                q = q.eq("household_id", household_id)
            return q

        # Recent meals (to avoid repetition)
        recent_res = _filter(
            self.db.table("meal_history")
            .select("meal_name")
            .gte("plan_date", cutoff_date)
        ).execute()
        recent_meal_names = list({r["meal_name"] for r in (recent_res.data or [])})

        # Top-rated meals
        top_res = _filter(
            self.db.table("meal_history")
            .select("meal_name, rating, would_repeat, plan_date, comments")
            .gte("rating", 4)
            .eq("would_repeat", True)
            .order("rating", desc=True)
            .limit(10)
        ).execute()
        top_rated = top_res.data or []

        # Low-rated meals to avoid
        low_res = _filter(
            self.db.table("meal_history")
            .select("meal_name, rating, comments")
            .lte("rating", 2)
        ).execute()
        low_rated = low_res.data or []

        # Recent rated meals for feedback context (includes member pref hits if column exists)
        rated_res = _filter(
            self.db.table("meal_history")
            .select("meal_name, plan_date, rating, comments, would_repeat, member_pref_hits")
            .gte("plan_date", cutoff_date)
            .not_.is_("rating", "null")
            .order("plan_date", desc=True)
            .limit(10)
        ).execute()
        rated_meals = rated_res.data or []

        # Format for Claude
        parts = []

        if recent_meal_names:
            parts.append(f"# Recent Meals (Last {weeks_back} Weeks)")
            parts.append("**Avoid repeating these:**")
            for name in recent_meal_names[:15]:
                parts.append(f"- {name}")
            parts.append("")

        if top_rated:
            parts.append("# Family Favorites (Highly Rated)")
            parts.append("**Consider suggesting these again (if not too recent):**")
            for r in top_rated:
                stars = "⭐" * int(r["rating"])
                parts.append(f"- {r['meal_name']} ({stars}, last: {r['plan_date']})")
                if r.get("comments"):
                    parts.append(f"  Note: {r['comments']}")
            parts.append("")

        if low_rated:
            parts.append("# Meals to Avoid (Low Rated)")
            parts.append("**Do NOT suggest these:**")
            for r in low_rated:
                parts.append(f"- {r['meal_name']} (⭐{r['rating']:.1f})")
                if r.get("comments"):
                    parts.append(f"  Reason: {r['comments']}")
            parts.append("")

        if rated_meals:
            parts.append("# Recent Ratings & Feedback")
            parts.append("**Learn from these comments:**")
            for r in rated_meals:
                stars = "⭐" * int(r["rating"])
                repeat = "✅" if r["would_repeat"] else "❌"
                parts.append(f"- {r['meal_name']} ({stars} {repeat}) - {r['plan_date']}")
                if r.get("comments"):
                    parts.append(f"  \"{r['comments']}\"")
            parts.append("")

        # Member preference patterns aggregated from rated meals
        all_liked: List[str] = []
        all_disliked: List[str] = []
        for r in rated_meals:
            hits = r.get("member_pref_hits") or {}
            all_liked.extend(hits.get("liked_hits", []))
            all_disliked.extend(hits.get("disliked_hits", []))
        if all_liked or all_disliked:
            parts.append("# Member Preference History")
            parts.append("**Use this to personalise the plan — avoid disliked meals for specific members:**")
            if all_liked:
                for hit in all_liked[:10]:
                    parts.append(f"- ✅ {hit}")
            if all_disliked:
                for hit in all_disliked[:10]:
                    parts.append(f"- ❌ {hit}")
            parts.append("")

        if not parts:
            return "# Meal History\nNo rated meals yet. This is the first meal plan!\n"

        return "\n".join(parts)

    def get_unrated_meals(self, limit: int = 50, household_id: int = None) -> List[Dict]:
        """Get meals that haven't been rated yet."""
        q = (
            self.db.table("meal_history")
            .select("id, plan_date, day_number, meal_name, ingredients, cost_estimate")
            .is_("rating", "null")
            .order("plan_date", desc=True)
            .order("day_number")
            .limit(limit)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        return q.execute().data or []

    # ========== PREFERENCES ==========

    def load_preferences(self, household_id: int = None) -> Dict:
        """Load preferences from Supabase (single JSONB row per household)."""
        if household_id:
            res = (
                self.db.table("preferences")
                .select("data")
                .eq("household_id", household_id)
                .limit(1)
                .execute()
            )
        else:
            res = (
                self.db.table("preferences")
                .select("data")
                .eq("user_id", "default")
                .limit(1)
                .execute()
            )
        if res.data:
            return res.data[0]["data"]
        return DEFAULT_PREFERENCES.copy()

    def save_preferences(self, preferences: Dict, household_id: int = None) -> bool:
        """Save preferences to Supabase."""
        if household_id:
            # Check if row exists
            existing = (
                self.db.table("preferences")
                .select("household_id")
                .eq("household_id", household_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                self.db.table("preferences").update({
                    "data": preferences,
                    "updated_at": _now(),
                }).eq("household_id", household_id).execute()
            else:
                self.db.table("preferences").insert({
                    "user_id": f"household_{household_id}",
                    "household_id": household_id,
                    "data": preferences,
                    "updated_at": _now(),
                }).execute()
        else:
            self.db.table("preferences").upsert({
                "user_id": "default",
                "data": preferences,
                "updated_at": _now(),
            }).execute()
        return True

    def reset_preferences_to_defaults(self, household_id: int = None) -> bool:
        """Reset preferences to default values."""
        return self.save_preferences(DEFAULT_PREFERENCES.copy(), household_id=household_id)

    def format_for_prompt(self, household_id: int = None) -> str:
        """Format preferences as text for Claude prompt."""
        prefs = self.load_preferences(household_id=household_id)

        lines = [
            "# Family Context",
            f"- Size: {prefs['family']['size']} people ({prefs['family']['composition']})",
            f"- Note: {prefs['family']['note']}",
            f"- Cooking style: {prefs['cooking']['style']}",
            f"- Priorities: {', '.join(prefs['cooking']['priorities'])}",
            f"- Max cook time: {prefs['cooking']['max_cook_time']} minutes",
            "",
        ]

        if prefs["food"]["dietary_restrictions"]:
            lines.append(f"- Dietary restrictions: {', '.join(prefs['food']['dietary_restrictions'])}")

        if prefs["food"]["favorites"]:
            lines.append("")
            lines.append("# Family Favorites")
            for item in prefs["food"]["favorites"]:
                lines.append(f"- {item}")

        if prefs["food"]["dislikes"]:
            lines.append("")
            lines.append("# Foods to Avoid")
            for item in prefs["food"]["dislikes"]:
                lines.append(f"- {item}")

        lines.append("")
        lines.append("# Meal Planning Rules")
        lines.append(f"- Default dinners per week: {prefs['planning']['default_dinners']}")
        lines.append(f"- Variety: {prefs['planning']['variety_rule']}")
        if prefs["planning"]["max_budget"]:
            lines.append(f"- Budget limit: {prefs['planning']['max_budget']} kr/week")

        return "\n".join(lines)

    # ========== SHOPPING LISTS ==========

    def get_active_shopping_list(self, household_id: int = None) -> Optional[Dict]:
        """Get the currently active shopping list."""
        q = (
            self.db.table("shopping_lists")
            .select("id, name, created_at, status")
            .eq("is_active", True)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        res = q.limit(1).execute()
        return res.data[0] if res.data else None

    def create_shopping_list(self, name: str, household_id: int = None) -> int:
        """Create a new shopping list and return its ID."""
        row = {"name": name, "is_active": True, "status": "active"}
        if household_id:
            row["household_id"] = household_id
        res = self.db.table("shopping_lists").insert(row).execute()
        return res.data[0]["id"]

    def get_shopping_list_items(self, list_id: int, include_checked: bool = True) -> List[Dict]:
        """Get all items from a shopping list."""
        q = (
            self.db.table("shopping_list_items")
            .select("id, item_name, quantity, category, checked, source, price_estimate, added_at")
            .eq("list_id", list_id)
            .order("category")
            .order("item_name")
        )
        if not include_checked:
            q = q.eq("checked", False)
        return q.execute().data or []

    def get_shopping_list_by_category(self, list_id: int, include_checked: bool = True) -> Dict[str, List[Dict]]:
        """Get shopping list items grouped by category."""
        items = self.get_shopping_list_items(list_id, include_checked)
        by_category: Dict[str, List[Dict]] = {}
        for item in items:
            cat = item.get("category") or "Other"
            by_category.setdefault(cat, []).append(item)
        return by_category

    def get_shopping_list_stats(self, list_id: int) -> Dict:
        """Get statistics about the shopping list."""
        items = self.get_shopping_list_items(list_id, include_checked=True)
        total = len(items)
        checked = sum(1 for i in items if i["checked"])
        estimate = sum(i["price_estimate"] or 0 for i in items)
        return {
            "total_items": total,
            "checked_items": checked,
            "unchecked_items": total - checked,
            "total_estimate": estimate,
        }

    def add_shopping_list_item(
        self,
        list_id: int,
        item_name: str,
        quantity: str = None,
        category: str = None,
        source: str = "manual",
        source_id: str = None,
        price_estimate: float = None,
    ) -> int:
        """Add an item to the shopping list, merging duplicates by updating quantity."""
        if not category:
            category = self._auto_categorize_item(item_name)

        # Check for existing unchecked item with same name
        existing_res = (
            self.db.table("shopping_list_items")
            .select("id, quantity")
            .eq("list_id", list_id)
            .ilike("item_name", item_name)
            .eq("checked", False)
            .limit(1)
            .execute()
        )

        if existing_res.data:
            existing = existing_res.data[0]
            existing_qty = existing["quantity"] or "1"
            new_qty = quantity or "1"

            try:
                existing_num = float(existing_qty.split()[0])
                new_num = float(new_qty.split()[0])
                total = existing_num + new_num
                unit_parts = existing_qty.split()[1:] or new_qty.split()[1:]
                unit = " " + " ".join(unit_parts) if unit_parts else ""
                merged = f"{int(total) if total == int(total) else total}{unit}"
            except (ValueError, AttributeError):
                merged = f"{existing_qty} + {new_qty}"

            self.db.table("shopping_list_items").update({
                "quantity": merged,
                **({"price_estimate": price_estimate} if price_estimate else {}),
            }).eq("id", existing["id"]).execute()
            return existing["id"]

        res = self.db.table("shopping_list_items").insert({
            "list_id": list_id,
            "item_name": item_name,
            "quantity": quantity or "1",
            "category": category,
            "source": source,
            "source_id": source_id,
            "price_estimate": price_estimate,
        }).execute()
        return res.data[0]["id"]

    def add_shopping_list_items_bulk(self, list_id: int, items: List[Dict]) -> int:
        """Add multiple items to shopping list at once."""
        rows = [
            {
                "list_id": list_id,
                "item_name": item["item_name"],
                "quantity": item.get("quantity"),
                "category": item.get("category") or self._auto_categorize_item(item["item_name"]),
                "source": item.get("source", "bulk"),
                "source_id": item.get("source_id"),
                "price_estimate": item.get("price_estimate"),
            }
            for item in items
        ]
        self.db.table("shopping_list_items").insert(rows).execute()
        return len(rows)

    def toggle_shopping_list_item(self, item_id: int) -> bool:
        """Toggle checked status. Returns new checked status."""
        current_res = (
            self.db.table("shopping_list_items")
            .select("checked")
            .eq("id", item_id)
            .single()
            .execute()
        )
        if not current_res.data:
            return False
        new_status = not current_res.data["checked"]
        self.db.table("shopping_list_items").update({"checked": new_status}).eq("id", item_id).execute()
        return new_status

    def remove_shopping_list_item(self, item_id: int):
        """Remove an item from the shopping list."""
        self.db.table("shopping_list_items").delete().eq("id", item_id).execute()

    def clear_shopping_list(self, list_id: int, checked_only: bool = False, clear_checked_only: bool = False):
        """Clear items from shopping list. Optionally clear only checked items."""
        # Support both parameter names for backwards compatibility
        only_checked = checked_only or clear_checked_only
        q = self.db.table("shopping_list_items").delete().eq("list_id", list_id)
        if only_checked:
            q = q.eq("checked", True)
        q.execute()

    # ========== OFFERS ==========

    def get_offer_by_id(self, product_id: str) -> Optional[Dict]:
        """Get a single offer by product_id."""
        res = (
            self.db.table("offers")
            .select("product_id, name, underline, price, price_numeric, normal_price, savings_percent, price_per_unit, department, category")
            .eq("product_id", product_id)
            .single()
            .execute()
        )
        return res.data if res.data else None

    # ========== HOUSEHOLDS ==========

    def get_user_profile(self, user_id: str) -> Optional[Dict]:
        """Return the user_profile row (includes household_id) or None."""
        res = (
            self.db.table("user_profiles")
            .select("id, email, household_id")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def create_user_profile(self, user_id: str, email: str) -> Dict:
        """Insert a new user_profile row (household_id is NULL until they join/create one)."""
        res = (
            self.db.table("user_profiles")
            .insert({"id": user_id, "email": email})
            .execute()
        )
        return res.data[0]

    def create_household(self, name: str, user_id: str) -> Dict:
        """Create a new household and link the creating user to it."""
        # Create household
        res = self.db.table("households").insert({"name": name}).execute()
        household = res.data[0]
        household_id = household["id"]
        # Link user to household
        self.db.table("user_profiles").update({"household_id": household_id}).eq("id", user_id).execute()
        return household

    def join_household(self, invite_code: str, user_id: str) -> Optional[Dict]:
        """Look up household by invite_code, link user, return household or None if not found."""
        res = (
            self.db.table("households")
            .select("id, name, invite_code")
            .eq("invite_code", invite_code.strip().lower())
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        household = res.data[0]
        self.db.table("user_profiles").update({"household_id": household["id"]}).eq("id", user_id).execute()
        return household

    def get_household(self, household_id: int) -> Optional[Dict]:
        """Return household row or None."""
        res = (
            self.db.table("households")
            .select("id, name, invite_code, created_at")
            .eq("id", household_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def get_household_members(self, household_id: int) -> List[Dict]:
        """Return list of user_profile rows for a household."""
        res = (
            self.db.table("user_profiles")
            .select("id, email, created_at")
            .eq("household_id", household_id)
            .execute()
        )
        return res.data or []

    # ========== RECIPES ==========

    def save_recipe(self, household_id, data: Dict) -> Dict:
        """Insert a new recipe and return the created row."""
        row = {
            "name": data["name"],
            "description": data.get("description", ""),
            "ingredients": data.get("ingredients", []),
            "instructions": data.get("instructions", ""),
            "servings": data.get("servings"),
            "cook_time_minutes": data.get("cook_time_minutes"),
            "tags": data.get("tags", []),
            "source": data.get("source", "manual"),
            "source_url": data.get("source_url"),
            "notes": data.get("notes", ""),
        }
        if household_id:
            row["household_id"] = household_id
        res = self.db.table("recipes").insert(row).execute()
        return res.data[0]

    def get_recipes(self, household_id, search: str = None, tag: str = None) -> List[Dict]:
        """List all recipes for a household, with optional search and tag filter."""
        q = (
            self.db.table("recipes")
            .select("id, name, description, cook_time_minutes, servings, tags, source, rating, created_at")
            .order("created_at", desc=True)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        if search:
            q = q.ilike("name", f"%{search}%")
        if tag:
            q = q.contains("tags", [tag])
        return q.execute().data or []

    def get_recipe(self, recipe_id: int, household_id) -> Optional[Dict]:
        """Get a single recipe by ID, scoped to household."""
        q = (
            self.db.table("recipes")
            .select("*")
            .eq("id", recipe_id)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        res = q.limit(1).execute()
        return res.data[0] if res.data else None

    def update_recipe(self, recipe_id: int, household_id, updates: Dict) -> Optional[Dict]:
        """Patch specific fields on a recipe."""
        q = (
            self.db.table("recipes")
            .update(updates)
            .eq("id", recipe_id)
        )
        if household_id:
            q = q.eq("household_id", household_id)
        res = q.execute()
        return res.data[0] if res.data else None

    def delete_recipe(self, recipe_id: int, household_id):
        """Delete a recipe."""
        q = self.db.table("recipes").delete().eq("id", recipe_id)
        if household_id:
            q = q.eq("household_id", household_id)
        q.execute()

    def rate_recipe(self, recipe_id: int, rating: int, notes: str = None):
        """Set rating and optionally update family notes on a recipe."""
        updates: Dict = {"rating": rating}
        if notes is not None:
            updates["notes"] = notes
        self.db.table("recipes").update(updates).eq("id", recipe_id).execute()

    # ========== MEMBER PREFERENCES ==========

    def get_member_preferences(self, household_id: int) -> List[Dict]:
        """Return all member profiles for a household, ordered by creation date."""
        res = (
            self.db.table("member_preferences")
            .select("*")
            .eq("household_id", household_id)
            .order("created_at")
            .execute()
        )
        return res.data or []

    def create_member_profile(self, household_id: int, display_name: str,
                              user_profile_id: str = None) -> Dict:
        """Insert a new member profile and return the created row."""
        row: Dict = {
            "household_id": household_id,
            "display_name": display_name.strip(),
        }
        if user_profile_id:
            row["user_profile_id"] = user_profile_id
        res = self.db.table("member_preferences").insert(row).execute()
        return res.data[0]

    def update_member_preferences(self, member_id: int, household_id: int,
                                  updates: Dict) -> Optional[Dict]:
        """Update allowed fields on a member profile."""
        allowed = {
            "display_name", "liked_meals", "disliked_meals",
            "liked_ingredients", "disliked_ingredients", "can_self_edit",
        }
        safe = {k: v for k, v in updates.items() if k in allowed}
        if not safe:
            return None
        res = (
            self.db.table("member_preferences")
            .update(safe)
            .eq("id", member_id)
            .eq("household_id", household_id)
            .execute()
        )
        return res.data[0] if res.data else None

    def delete_member_profile(self, member_id: int, household_id: int):
        """Delete a member profile, scoped to the household."""
        (
            self.db.table("member_preferences")
            .delete()
            .eq("id", member_id)
            .eq("household_id", household_id)
            .execute()
        )

    def get_member_profile_for_user(self, user_profile_id: str) -> Optional[Dict]:
        """Return a member profile linked to a specific user_profile_id, or None."""
        res = (
            self.db.table("member_preferences")
            .select("*")
            .eq("user_profile_id", user_profile_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def save_member_pref_hits(self, meal_id: int, hits: Dict):
        """Write member preference hits to a meal_history row (Stage C)."""
        self.db.table("meal_history").update(
            {"member_pref_hits": hits}
        ).eq("id", meal_id).execute()

    # ========== STAPLES ==========

    def get_staples(self, household_id: int) -> List[Dict]:
        """Return all staples for a household, most-used first."""
        res = (
            self.db.table("staples")
            .select("*")
            .eq("household_id", household_id)
            .order("times_added", desc=True)
            .order("item_name")
            .execute()
        )
        return res.data or []

    def create_staple(self, household_id: int, item_name: str,
                      category: str = None, quantity: str = None) -> Dict:
        """Insert a new staple and return the row."""
        row = {
            "household_id": household_id,
            "item_name": item_name.strip(),
            "category": category or self._auto_categorize_item(item_name),
            "quantity": quantity or None,
        }
        res = self.db.table("staples").insert(row).execute()
        return res.data[0]

    def delete_staple(self, staple_id: int, household_id: int):
        """Delete a staple scoped to the household."""
        (
            self.db.table("staples")
            .delete()
            .eq("id", staple_id)
            .eq("household_id", household_id)
            .execute()
        )

    def increment_staple_usage(self, staple_ids: List[int], household_id: int):
        """Increment times_added and update last_added_at for given staples."""
        for staple_id in staple_ids:
            # Fetch current count first (Supabase Python SDK doesn't support column += 1 directly)
            res = (
                self.db.table("staples")
                .select("times_added")
                .eq("id", staple_id)
                .eq("household_id", household_id)
                .limit(1)
                .execute()
            )
            if not res.data:
                continue
            current = res.data[0]["times_added"]
            self.db.table("staples").update({
                "times_added": current + 1,
                "last_added_at": _now(),
            }).eq("id", staple_id).eq("household_id", household_id).execute()

    # ========== HELPERS ==========

    def _auto_categorize_item(self, item_name: str) -> str:
        """Auto-categorize an item based on keywords."""
        item_lower = item_name.lower()
        categories = {
            "Produce": ["tomato", "lettuce", "onion", "garlic", "potato", "carrot",
                        "pepper", "cucumber", "apple", "banana", "orange", "lemon",
                        "spinach", "broccoli", "cauliflower", "celery", "mushroom",
                        "fruit", "vegetable", "salad", "avocado"],
            "Dairy": ["milk", "cheese", "yogurt", "butter", "cream", "egg",
                      "mælk", "ost", "yoghurt", "smør", "fløde", "æg"],
            "Meat & Fish": ["chicken", "beef", "pork", "fish", "salmon", "sausage",
                            "bacon", "meat", "turkey", "lamb", "tuna", "cod",
                            "kylling", "oksekød", "svinekød", "fisk", "laks"],
            "Pantry": ["pasta", "rice", "flour", "sugar", "oil", "spice", "sauce",
                       "canned", "can", "jar", "salt", "pepper", "vinegar",
                       "ris", "mel", "sukker", "olie", "peber"],
            "Bakery": ["bread", "bun", "roll", "tortilla", "bagel", "croissant",
                       "brød", "bolle", "rundstykke"],
            "Frozen": ["frozen", "ice cream", "fros", "is"],
            "Beverages": ["juice", "soda", "coffee", "tea", "water", "beer", "wine",
                          "kaffe", "te", "vand", "øl", "vin"],
        }
        for category, keywords in categories.items():
            if any(kw in item_lower for kw in keywords):
                return category
        return "Other"
