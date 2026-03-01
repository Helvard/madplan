# Madplan — Development Roadmap

> **Document purpose:** Prioritised implementation plan covering bug fixes and new features.
> **Based on:** Full codebase review of `main.py`, `database.py`, all templates, and the schema files.
> **Last updated:** March 2026

---

## Quick Reference

| Item | Type | Effort | Priority | Depends on |
|---|---|---|---|---|
| [BUG-1] Shopping list template not found | Bug fix | 20 min | 🔴 Critical | — |
| [BUG-2] Department filter does nothing | Bug fix | 10 min | 🔴 Critical | — |
| [BUG-3] Offers selections not cleared after submit | Bug fix | 10 min | 🟠 High | — |
| [BUG-4] Recipes internal server error | Bug fix | 5 min (SQL) | 🟠 High | — |
| [FEAT-1] Member likes/dislikes preferences | Feature | 5–7 hrs | 🟡 Medium | — |
| [FEAT-2] Admin section | Feature | 2–3 hrs | 🟡 Medium | FEAT-1 (partial) |

---

## Phase 1 — Bug Fixes (Target: one session, ~45 minutes total)

Tackle all four bugs in a single session. They are independent of each other and of any feature work. Once done, the app is stable enough to use daily while feature development continues.

---

### [BUG-1] Shopping list — "template not found" error

**Symptom:** Adding an item shows `Error: 'partials/shopping_list_items.html' not found`. Item does save correctly; the error is on the response render.

**Root cause — two problems:**

The template file is named `shopping_list_items_partial.html` but six endpoints in `main.py` reference `partials/shopping_list_items.html` (without the `_partial` suffix). Additionally, `main.py` has grown to contain **duplicate route definitions** for several shopping list endpoints — FastAPI silently uses the last definition, but the stale first copies also reference the wrong template name.

**Files to change:**

1. `meal_planner_web/frontend/templates/partials/shopping_list_items_partial.html`
   — Rename to `shopping_list_items.html`.

2. `meal_planner_web/backend/main.py`
   — Remove the stale first copy of each duplicated endpoint. The duplicated routes are:
   - `@app.get("/shopping-list/items")`
   - `@app.delete("/shopping-list/item/{item_id}")` (appears as both `@app.delete` and `@app.post("/shopping-list/item/{item_id}/delete")`)
   - `@app.post("/shopping-list/clear-checked")` and `@app.post("/shopping-list/clear-all")` (old versions; newer `@app.post("/shopping-list/clear")` with `clear_type` param is the correct one)

**Verification:** Add an item, confirm no error and list updates in place without page reload.

---

### [BUG-2] Offers — department filter does nothing

**Symptom:** Selecting a department from the dropdown visually does nothing. The old list stays unchanged.

**Root cause:** In `filter_offers()` in `main.py`:
```python
query = db._client.table("offers").select(...)
```
The `Database` class stores the client as `self.db`, not `self._client`. This raises `AttributeError`. FastAPI returns a 500; htmx does not swap error responses into `#offers-list` by default, so the page appears unchanged.

**Files to change:**

1. `meal_planner_web/backend/main.py` — one line in `filter_offers()`:
   ```python
   # Before:
   query = db._client.table("offers").select(...)
   # After:
   query = db.db.table("offers").select(...)
   ```

**Secondary check:** After fixing the attribute reference, verify that the `department` option values in `offers.html` (populated from the `departments` dict passed by `offers_page`) match the exact casing stored in the `offers` table. A mismatch between stored values and filter params will cause the PostgREST `.eq()` to return zero results silently.

**Verification:** Select a department; confirm the list reloads and shows only offers from that department.

---

### [BUG-3] Offers — selections not cleared after submitting

**Symptom:** After clicking Submit Selections and seeing the success message, all checkboxes remain ticked and the selection counter stays non-zero. Re-submitting adds items again.

**Root cause:** `submit_offer_selections()` returns HTML that includes a `clearForm()` JS function, but that function only runs when the user manually clicks "Continue Browsing". It does not auto-fire on success.

**Files to change:**

1. `meal_planner_web/backend/main.py` — in the `<script>` block of the success HTML returned by `submit_offer_selections()`, add one call at the bottom:
   ```javascript
   // Add this as the last line inside the <script> block:
   clearForm();
   ```
   The function already does everything correct (unchecks boxes, resets quantities, calls `updateSelectedCount()`). It just needs to run automatically.

2. The "Continue Browsing" button can be repurposed to dismiss the success banner only (since clearing already happened).

**Verification:** Submit selections, confirm checkboxes are immediately unchecked and count badge resets to zero.

---

### [BUG-4] Recipes — Internal Server Error on `/recipes`

**Symptom:** Navigating to the Recipes page returns a 500 Internal Server Error.

**Root cause:** `schema_recipes.sql` defines `recipes.household_id` as `UUID`, but `schema_auth.sql` defines `households.id` as `BIGSERIAL` (64-bit integer). When `get_recipes(household_id)` runs `.eq("household_id", household_id)` with an integer value, PostgreSQL raises a type mismatch error.

**Files to change — SQL only (Supabase SQL Editor):**

```sql
ALTER TABLE recipes
    ALTER COLUMN household_id TYPE BIGINT
    USING CASE
        WHEN household_id IS NULL THEN NULL
        ELSE household_id::text::bigint
    END;

ALTER TABLE recipes
    ADD CONSTRAINT fk_recipes_household
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE;
```

No application code changes required. `database.py` is already correct.

**Verification:** Navigate to `/recipes` — page loads. Create a recipe — it saves and redirects to the detail page.

---

## Phase 2 — Member Likes/Dislikes Preferences (Target: 5–7 hours across multiple sessions)

This is the largest feature and the one with the most design surface. Implemented in four independent stages so each can be tested before the next begins.

---

### [FEAT-1] Member preferences

**Goal:** The household owner can create a profile for each family member with liked and disliked meals and ingredients. When generating a meal plan, Claude uses these preferences as soft guidance, producing warnings rather than hard exclusions. The rating system logs how well each plan served each member's preferences over time.

#### Stage A — Data model and basic admin UI (2 hours)

**New DB table — run in Supabase SQL Editor:**

```sql
CREATE TABLE member_preferences (
    id                   BIGSERIAL   PRIMARY KEY,
    household_id         BIGINT      NOT NULL REFERENCES households(id) ON DELETE CASCADE,

    -- NULL if this is a managed-only profile (no login)
    user_profile_id      UUID        REFERENCES user_profiles(id) ON DELETE SET NULL,

    display_name         TEXT        NOT NULL,

    -- Soft preferences — arrays of freetext strings
    liked_meals          TEXT[]      DEFAULT '{}',
    disliked_meals       TEXT[]      DEFAULT '{}',
    liked_ingredients    TEXT[]      DEFAULT '{}',
    disliked_ingredients TEXT[]      DEFAULT '{}',

    -- Whether a linked user can edit their own row
    can_self_edit        BOOLEAN     DEFAULT FALSE,

    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON member_preferences (household_id);
CREATE INDEX ON member_preferences (user_profile_id);

-- Track preference hits per saved meal plan
-- Stored as JSONB on meal_history to avoid a separate join table
ALTER TABLE meal_history
    ADD COLUMN IF NOT EXISTS member_pref_hits JSONB DEFAULT '{}';
-- Schema: {"liked_hits": ["Bertil: pasta"], "disliked_hits": ["Bertil: mashed potatoes"]}
```

**New `database.py` methods:**

```python
def get_member_preferences(self, household_id) -> List[Dict]
def create_member_profile(self, household_id, display_name, user_profile_id=None) -> Dict
def update_member_preferences(self, member_id, updates: Dict) -> Dict
def delete_member_profile(self, member_id, household_id)
def link_member_to_user(self, member_id, user_profile_id)
```

**New routes in `main.py`:**

```
GET  /preferences/members                  — list all member profiles
POST /preferences/members/create           — add a new member
POST /preferences/members/{id}/update      — update likes/dislikes
POST /preferences/members/{id}/delete      — remove a member
POST /preferences/members/{id}/link-user   — link a member to a user_profile
```

**UI changes — `preferences.html`:**

Add a "Family Members" section below the existing household preferences panel. Owner sees all members with inline editable tag inputs for likes/dislikes. Non-owner linked users see only their own row if `can_self_edit` is true.

Tags UI pattern: comma-separated text input on save, displayed as removable chips (same pattern already used for recipe tags in `main.py`).

---

#### Stage B — Claude integration (1 hour)

**Changes to `build_claude_prompt()` in `main.py`:**

After the existing household preferences block, add:

```python
member_prefs = db.get_member_preferences(household_id)
if member_prefs:
    prompt_parts.append("# Family Member Preferences")
    prompt_parts.append(
        "These are SOFT preferences. Do not hard-exclude meals. "
        "Instead, add a ⚠️ note inline when a suggested meal conflicts with a dislike. "
        "Example: '⚠️ Note: Bertil doesn't like mashed potatoes'"
    )
    for member in member_prefs:
        lines = [f"\n**{member['display_name']}**"]
        if member['liked_meals']:
            lines.append(f"  Likes (meals): {', '.join(member['liked_meals'])}")
        if member['disliked_meals']:
            lines.append(f"  Dislikes (meals): {', '.join(member['disliked_meals'])}")
        if member['liked_ingredients']:
            lines.append(f"  Likes (ingredients): {', '.join(member['liked_ingredients'])}")
        if member['disliked_ingredients']:
            lines.append(f"  Dislikes (ingredients): {', '.join(member['disliked_ingredients'])}")
        prompt_parts.extend(lines)
```

**Verification:** Generate a plan for a household where a member dislikes a common ingredient. Confirm Claude includes the ⚠️ warning in the meal plan output.

---

#### Stage C — Rating integration (1 hour)

**Goal:** When a meal is rated, log which member preferences were hit or missed at that moment in time. This creates a historical record without a complex join table.

**Changes to the rating flow:**

1. `rate_meals.html` — add an optional collapsible "Who ate this?" section per meal, showing all member names as checkboxes for liked/disliked.

2. `POST /rate-meals/submit` in `main.py` — when saving a rating, compute preference hits from the form data and write to `meal_history.member_pref_hits`.

3. `get_meal_history_for_context()` in `database.py` — include `member_pref_hits` in the context string sent to Claude, so it can learn over time that a particular member consistently dislikes meals with a certain ingredient.

**Format of `member_pref_hits`:**
```json
{
  "liked_hits": ["Bertil: pasta bolognese", "Emma: salmon"],
  "disliked_hits": ["Bertil: mashed potatoes"]
}
```

---

#### Stage D — Self-service member editing (30 minutes)

**Goal:** A family member who has their own login can view and edit their own `member_preferences` row.

**Changes:**

1. `database.py` — add `get_member_profile_for_user(user_profile_id)` method.

2. `main.py` — in `POST /preferences/members/{id}/update`, check if the requesting user is either the household owner or the member themselves (via `user_profile_id` match). Reject otherwise with 403.

3. Navigation — if the current user has a linked member profile with `can_self_edit = True`, show a "My Preferences" link in the nav.

---

## Phase 3 — Admin Section (Target: 2–3 hours, after Phase 2 Stage A)

**Goal:** Consolidate all household management and operational tasks into a single `/admin` page, visible only to the household owner.

#### Prerequisites

`households` table needs an `owner_id` column if not already present:

```sql
ALTER TABLE households
    ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES user_profiles(id);

-- Backfill: set owner to first member of each household
UPDATE households h
SET owner_id = (
    SELECT id FROM user_profiles
    WHERE household_id = h.id
    ORDER BY created_at ASC
    LIMIT 1
)
WHERE owner_id IS NULL;
```

#### Admin page sections

| Section | Content |
|---|---|
| Household | Name, invite code (click to copy), creation date |
| Members | Full member management UI (same as FEAT-1 Stage A, linked here) |
| Offers | Manual scrape trigger (currently at `/admin/scrape-offers`), last sync timestamp, total offer count |
| Preferences | Link to `/preferences` — no duplication |
| Usage stats | Meal plans saved (all time / last 30 days), shopping list item count, recipes saved |

#### Routes to add

```
GET  /admin            — admin dashboard (owner only)
POST /admin/household/rename  — rename the household
```

#### Auth guard

Add a helper `_require_owner(request)` that checks `user["id"] == household.owner_id`. Return 403 (not redirect) for non-owners so the nav link can be conditionally hidden without an extra round-trip.

#### Nav change

Add "Admin" link to `navigation.html`, conditionally rendered:
```html
{% if user and user.is_owner %}
<a href="/admin">Admin</a>
{% endif %}
```

Pass `is_owner` from each route handler that uses the nav, or compute it in a Jinja2 context processor.

---

## Deferred / Out of scope for now

These items were identified during the code review but are not blocking and can be picked up after the above is complete.

| Item | Notes |
|---|---|
| Supabase Realtime for shopping list | Replace 30-second badge polling with websocket subscription. Meaningful only once the family is actually using it concurrently. |
| `shopping_list_items_partial.html` dead twin | Once BUG-1 is fixed, the `_partial` filename convention is gone. Check whether any route still references the old name and delete the orphan. |
| Base template CDN pinning | `cdn.tailwindcss.com` is the dev build. Compile to static CSS before going to Hetzner. |
| `preferences.yaml` / `PreferencesManager` | Still referenced in older parts of the code. Remove entirely once all routes use `db.load_preferences()`. |
| Recurring items table | Documented in `ARCHITECTURE_DECISIONS.md` as Decision 8. Not yet implemented. |
| Mobile layout for offers/shopping list | Fixed-position submit button overlaps content on small screens. Needs a dedicated mobile pass. |
| Test coverage | No tests exist. At minimum: shopping list parser, prompt builder, FSM state transitions. |
