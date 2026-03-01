# Madplan — Architecture & Dependency Reference

> **Purpose:** Internal reference for developers making changes or additions. Covers how the system is structured, how the components connect, and the conventions to follow so new work stays consistent.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Dependency Graph](#2-dependency-graph)
3. [Component Reference](#3-component-reference)
   - [main.py — Routes & App Shell](#31-mainpy--routes--app-shell)
   - [database.py — Data Layer](#32-databasepy--data-layer)
   - [auth.py — Authentication](#33-authpy--authentication)
   - [claude_client.py — Claude API](#34-claude_clientpy--claude-api)
   - [scraper.py — Offer Loading](#35-scraperpy--offer-loading)
   - [preferences.py — DEPRECATED](#36-preferencespy--deprecated)
4. [Frontend Conventions](#4-frontend-conventions)
   - [Template Hierarchy](#41-template-hierarchy)
   - [HTMX Patterns](#42-htmx-patterns)
   - [JavaScript Globals](#43-javascript-globals)
5. [Data Flows](#5-data-flows)
   - [Authentication Flow](#51-authentication-flow)
   - [Meal Plan Generation Flow](#52-meal-plan-generation-flow)
   - [Shopping List Flow](#53-shopping-list-flow)
   - [Offers Flow](#54-offers-flow)
6. [Database Schema & Scoping](#6-database-schema--scoping)
7. [Session & State Management](#7-session--state-management)
8. [How to Add a New Feature](#8-how-to-add-a-new-feature)
9. [Key Non-Obvious Decisions](#9-key-non-obvious-decisions)
10. [Configuration Reference](#10-configuration-reference)

---

## 1. System Overview

Madplan is a **server-rendered, multi-tenant web app**. There is no client-side framework. The page is rendered by Jinja2 on the server; partial updates are handled by HTMX swapping HTML fragments returned by FastAPI endpoints.

```
Browser ──HTMX/fetch──▶ FastAPI (main.py)
                              │
                    ┌─────────┼──────────┐
                    ▼         ▼          ▼
               database.py  claude_client.py  scraper.py
                    │                         │
                    └──────────┬──────────────┘
                               ▼
                          Supabase (PostgreSQL)
```

**Key architectural choices:**
- All state lives in Supabase or the session cookie — the app itself is stateless except for `chat_sessions` (in-memory, lost on restart).
- Multi-tenancy is by **household_id** — every table that holds user data has a `household_id` column and all queries must filter by it.
- Claude is called synchronously — no background tasks or queues. Long AI calls block the request.

---

## 2. Dependency Graph

```
main.py
├── database.py          ← all DB reads/writes
│   └── supabase-py      (SUPABASE_SERVICE_KEY — bypasses RLS)
├── auth.py              ← session + JWT validation
│   └── PyJWT
├── claude_client.py     ← all Claude API calls
│   └── anthropic SDK    (ANTHROPIC_API_KEY)
├── scraper.py           ← loads & formats offers from DB
│   └── supabase-py      (SUPABASE_KEY — anon key)
├── shopping_list_parser.py  ← parses Claude's markdown output
└── preferences.py       ← DEPRECATED, do not use
```

**Frontend:**
```
base.html
└── partials/navigation.html   ← AppState JS object, badge polling
    └── /shopping-list/count   ← polled every 30s

Child templates (index, offers, shopping_list, recipes, …)
└── partials/*.html            ← HTMX swap targets
```

---

## 3. Component Reference

### 3.1 `main.py` — Routes & App Shell

**Initialised at module level (once, on startup):**
```python
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=..., https_only=..., same_site="lax")
db = Database()          # single instance, reused across all requests
claude = ClaudeClient()  # single instance
chat_sessions = {}       # in-memory: {session_id: [messages]}
```

**Auth helper — used at the top of every protected route:**
```python
user, household_id = _require_auth(request)
if not user:
    return RedirectResponse("/login", 303)
```
`_require_auth` calls `auth.get_current_user()` and returns `(user_dict, household_id)` or `(None, None)`.

**Template context convention — every `TemplateResponse` should include:**
```python
{
    "request": request,     # required by Jinja2/Starlette
    "user": user,           # drives nav user display + logout
    "current_page": "...",  # drives nav active-link highlighting
    # … page-specific keys
}
```

**`build_claude_prompt(offers_text, preferences, household_id)`**

Assembles the full prompt sent to Claude for meal plan generation. Sections in order:

| Section | Source |
|---|---|
| System message | Hardcoded |
| Household preferences | `db.format_for_prompt()` |
| Meal history context | `db.get_meal_history_for_context(weeks_back=4)` |
| This week's parameters | Session-merged preferences (`num_dinners`, etc.) |
| Selected offers ⭐ | `request.session['selected_offers']` |
| Output format instructions | Hardcoded |
| Available offers | `scraper.format_offers_for_claude(offers)` |

> **Rule:** Any new data source you want Claude to consider (e.g. member preferences) must be inserted into `build_claude_prompt()`. The function assembles a `prompt_parts` list of strings and joins them with `\n\n`.

---

### 3.2 `database.py` — Data Layer

**Client setup:**
```python
# Module level — one Supabase client, uses service role key to bypass RLS
_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)

class Database:
    def __init__(self):
        self.db = _client   # ← attribute is `self.db`, NOT `self._client`
```

> ⚠️ The Supabase client is `self.db`, not `self._client`. Getting this wrong (as in BUG-2) causes a silent `AttributeError`.

**Method naming convention:**

| Pattern | Example |
|---|---|
| `get_*` | `get_meal_history`, `get_active_shopping_list` |
| `save_*` / `create_*` | `save_meal_plan`, `create_household` |
| `update_*` | `update_member_preferences` |
| `delete_*` / `remove_*` | `remove_shopping_list_item` |
| `load_*` | `load_preferences` |

**Household scoping — the rule:**

Every method that touches user data must accept `household_id` and apply it:
```python
def get_recipes(self, household_id=None):
    query = self.db.table("recipes").select("*")
    if household_id:
        query = query.eq("household_id", household_id)
    return query.execute().data or []
```
Never query without scoping unless the data is genuinely global (e.g. `offers`).

**Preferences structure (JSONB):**
```python
DEFAULT_PREFERENCES = {
    "family":  { "size": 4, "children_ages": [], "dietary_restrictions": [] },
    "cooking": { "skill_level": "intermediate", "time_available": 30, "appliances": [] },
    "food":    { "cuisines": [], "disliked_ingredients": [], "favorite_meals": [] },
    "planning":{ "budget": "medium", "batch_cook": False, "variety": "high" }
}
```
Stored as a single JSONB `data` column per household. Loaded with `db.load_preferences(household_id)`, saved with `db.save_preferences(household_id, prefs_dict)`.

**Shopping list — duplicate handling:**
`add_shopping_list_item()` checks for an existing unchecked item with the same name and increments its quantity instead of inserting a duplicate row.

**Auto-categorisation:**
`_auto_categorize_item(name, department)` does keyword matching (Danish + English) to assign one of: `Produce`, `Dairy`, `Meat`, `Pantry`, `Bakery`, `Frozen`, `Beverages`, `Other`. Falls back to department name if no match.

---

### 3.3 `auth.py` — Authentication

**Session structure after login:**
```python
request.session = {
    "access_token": "<Supabase JWT>",
    "user": {
        "id": "<uuid>",
        "email": "user@example.com",
        "household_id": 42
    },
    "household_id": 42   # also stored at top level for fast access
}
```

> ⚠️ `household_id` is **NOT** in the JWT. It is stored separately in the session after login.

**`get_current_user(request)` → `dict | None`**

1. Extract JWT from `session["access_token"]`
2. If `SUPABASE_JWT_SECRET` is set: validate with HS256
3. If decode succeeds: return `{"id": sub, "email": ..., "household_id": session["household_id"]}`
4. If decode fails: **fall back to `session["user"]`** (safe — Starlette signs the cookie)
5. If token expired: clear session entirely, return `None`

**What this means for new routes:**
- Always call `_require_auth(request)` and check for `None` before accessing household data.
- Never trust `request.session["household_id"]` directly — go through `_require_auth`.

---

### 3.4 `claude_client.py` — Claude API

**Model:** `claude-sonnet-4-20250514` (hardcoded; override via `CLAUDE_MODEL` env var if added).

**Methods and their contracts:**

| Method | Input | Output | Max tokens |
|---|---|---|---|
| `generate_meal_plan(prompt)` | Full assembled prompt string | Markdown string | 4000 |
| `refine_meal_plan(plan, feedback, offers)` | Previous plan + user feedback | Markdown string | 4000 |
| `generate_recipe_json(description, prefs)` | Freetext description | Dict (strict schema) | 2000 |
| `extract_recipe_from_url(page_text, url)` | Raw webpage text | Dict (strict schema) | 2000 |
| `chat_recipe_message(messages, recipe_ctx)` | Message history list | String reply | 1000 |

**Recipe JSON schema** (what Claude must return):
```json
{
  "name": "string",
  "description": "string",
  "ingredients": [{"name": "string", "quantity": "string", "unit": "string"}],
  "instructions": ["step 1", "step 2"],
  "servings": 4,
  "cook_time_minutes": 30,
  "tags": ["tag1", "tag2"]
}
```

**JSON extraction:** `_parse_json_response(text)` uses regex to find the first `{...}` block. Invalid JSON returns `{}`. Always check the return value before using it.

**Adding a new Claude call:** Create a new method on `ClaudeClient`. Do not call `anthropic.Anthropic()` directly in `main.py` — all Claude interaction goes through `claude_client.py`.

---

### 3.5 `scraper.py` — Offer Loading

**Purpose:** Reads the `offers` table and formats it for Claude prompts. Does not write to the DB (that's `scrape_rema_to_db.py`).

**Key functions:**

| Function | Returns | Notes |
|---|---|---|
| `load_offers_from_db()` | `List[Dict]` | Ordered by department, then price |
| `format_offers_for_claude(offers, max_per_category=20)` | `str` | Max 20 items per dept, sorted by savings |
| `categorize_offers(offers)` | `Dict[str, List]` | Groups by `department` field |
| `get_key_offers(offers, min_savings=30)` | `List[Dict]` | Best deals only |

> ⚠️ `scraper.py` uses **`SUPABASE_KEY` (anon key)**, not the service role key. If RLS is enabled on `offers` without a public read policy, `load_offers_from_db()` will return an empty list silently.

**Offer sync** (`scrape_rema_to_db.py`) is separate — it calls the Rema 1000 Algolia API and does an atomic delete-then-insert. It is triggered by the Supabase Edge Function (`supabase/functions/sync-offers/index.ts`) daily at 23:59 UTC, or manually via `POST /admin/scrape-offers`.

---

### 3.6 `preferences.py` — DEPRECATED

This file contains a `PreferencesManager` class that persists preferences to a YAML file at `/data/preferences.yaml`. It is **not used** anywhere in the current codebase. All preference logic has moved to `database.py`. Do not use or extend this file. It can be deleted once confirmed safe.

---

## 4. Frontend Conventions

### 4.1 Template Hierarchy

```
base.html                    ← <html>, <head>, nav include, <main>, footer
└── partials/navigation.html ← sticky header, AppState JS

index.html extends base.html
shopping_list.html extends base.html
offers.html extends base.html
recipes.html extends base.html
recipe_detail.html extends base.html
…

partials/
├── shopping_list_items.html      ← HTMX swap target for list items
├── shopping_list_stats.html      ← progress bar / totals
├── meal_plan.html                ← rendered meal plan display
├── recipe_card.html              ← compact recipe tile
├── recipe_chat_message.html      ← single chat bubble
├── navigation.html               ← nav header
├── message.html                  ← flash success/info messages
└── error.html                    ← error alert
```

**Naming rule:** Partial templates live in `partials/` and are referenced in code as `"partials/<name>.html"`. Do not use `_partial` suffixes.

### 4.2 HTMX Patterns

The app uses HTMX for partial page updates without full reloads. Key patterns in use:

**Swap target:** HTMX swaps the response HTML into a target element.
```html
<div id="offers-list">…</div>

<form hx-get="/offers/filter" hx-target="#offers-list" hx-swap="innerHTML">
```

**Trigger custom event after action:** Endpoints that change the shopping list fire a JS event:
```javascript
document.body.dispatchEvent(new Event('shopping-list-updated'));
```
The nav badge listener picks this up and refreshes the count.

**HTMX error handling:** By default HTMX does not swap error responses (4xx/5xx). If a route can return an error state, return a 200 with an error HTML fragment, or configure `hx-on:htmx:response-error`.

**Loading indicator:** Wrap slow actions with `htmx-indicator`:
```html
<span class="htmx-indicator">Loading…</span>
```
The base template CSS hides `.htmx-indicator` by default and shows it during requests.

### 4.3 JavaScript Globals

**`AppState`** (defined in `navigation.html`) — the only global JS object:
```javascript
AppState = {
  async getShoppingListCount() { /* GET /shopping-list/count */ },
  async updateBadges()         { /* updates #nav-badge-shopping */ }
}
```

**`updateSelectedCount()`** (defined inline in `offers.html`) — updates the offer selection badge. Referenced from dynamically-generated offer card HTML in `filter_offers()`. Must remain a global function.

**`toggleQty(id, checked)`** (defined inline in `offers.html`) — shows/hides quantity inputs when a checkbox is ticked.

> **Convention:** Keep JS minimal and inline in the relevant template. If a function is called from server-generated HTML (like offer cards), it must be a global on `window`. Do not import npm modules.

---

## 5. Data Flows

### 5.1 Authentication Flow

```
POST /login
  → Supabase Auth API (email + password)
  → Store in session: access_token, user{id, email, household_id}, household_id
  → Redirect to / or /household

Every protected route:
  _require_auth(request)
    → auth.get_current_user(request)
        → Validate JWT (if SUPABASE_JWT_SECRET set)
        → Fallback: trust session["user"]
    → Returns (user_dict, household_id)
```

### 5.2 Meal Plan Generation Flow

```
User chats → POST /chat/message (state machine)
  State: ask_num_dinners → generating → review_plan

POST /chat/generate-plan
  1. _require_auth → (user, household_id)
  2. db.load_preferences(household_id)         ← persistent prefs
  3. Merge session ad-hoc prefs (num_dinners, special_prefs, selected_offers)
  4. load_offers_from_db() + format_offers_for_claude()
  5. build_claude_prompt(offers_text, merged_prefs, household_id)
     └── db.get_meal_history_for_context(household_id)
  6. claude.generate_meal_plan(prompt) → markdown
  7. Store plan in session["meal_plan"]
  8. Render partials/meal_plan.html

POST /chat/accept-plan
  1. Parse markdown → shopping list items (shopping_list_parser.py)
  2. db.save_meal_plan(household_id, plan)
  3. db.add_shopping_list_items_bulk(list_id, items)
```

### 5.3 Shopping List Flow

```
Item added (any source) → db.add_shopping_list_item()
  └── Checks for duplicate unchecked item → increments qty if found

HTMX partial refresh:
  GET /shopping-list/items
    → db.get_shopping_list_items(list_id)
    → Group by category (category_order sort)
    → Return partials/shopping_list_items.html

Badge update:
  GET /shopping-list/count → int (unchecked items only)
  Polled every 30s by AppState.updateBadges()
  Also fired on 'shopping-list-updated' custom event
```

### 5.4 Offers Flow

```
Daily at 23:59 UTC:
  pg_cron → supabase/functions/sync-offers/index.ts
    → Algolia API (Rema 1000, labels:on_discount)
    → DELETE all from offers table
    → INSERT fresh batch (chunks of 200)

GET /offers:
  load_offers_from_db()
  Group by department → template context

GET /offers/filter (HTMX):
  db.db.table("offers").select(...).eq("department", ...).order(...)
  → HTML fragment (no template — inline f-string in main.py)

POST /offers/submit-selections:
  Form data → product_ids + quantities
  db.get_offer_by_id(product_id) per selection
  → session["selected_offers"] (for meal plan) OR
  → db.add_shopping_list_item() (for shopping list)
```

---

## 6. Database Schema & Scoping

All user data tables have `household_id BIGINT` (references `households.id`). **Always filter by `household_id`** in queries.

| Table | household_id | Key columns |
|---|---|---|
| `meal_history` | ✅ | plan_date, meal_name, ingredients, rating, would_repeat |
| `preferences` | ✅ | data (JSONB), updated_at |
| `shopping_lists` | ✅ | is_active, status |
| `shopping_list_items` | via list | list_id, item_name, quantity, category, checked, source, price_estimate |
| `recipes` | ✅ | name, ingredients[], instructions, tags[], rating, source_url |
| `user_profiles` | ✅ | id (UUID), email |
| `households` | — | name, invite_code |
| `offers` | ✗ (global) | product_id, name, price_numeric, savings_percent, department |

**Type conventions:**
- `households.id` → `BIGSERIAL` (integer)
- `user_profiles.id` → `UUID` (Supabase Auth user ID)
- All `household_id` FK columns → `BIGINT` (matches `households.id`)
- All `user_profile_id` FK columns → `UUID` (matches `user_profiles.id`)

> ⚠️ Do not use UUID for columns that reference `households.id`. This was the cause of BUG-4.

---

## 7. Session & State Management

**What lives in the session (Starlette signed cookie):**

| Key | Type | Set by | Used by |
|---|---|---|---|
| `access_token` | str | `/login` | `auth.get_current_user()` |
| `user` | dict | `/login` | `_require_auth()`, nav template |
| `household_id` | int | `/login` or `/household/create` | `_require_auth()` |
| `meal_plan` | str | `/chat/accept-plan` | `/chat/refine-plan` |
| `chat_state` | str | `/chat/start` | FSM in `/chat/message` |
| `selected_offers` | list | `/offers/submit-selections` | `build_claude_prompt()` |

**What lives in memory (lost on restart):**

| Variable | Type | Notes |
|---|---|---|
| `chat_sessions` | dict | `{session_id: [messages]}` — conversation history |

**What lives only in the DB:**
Everything that needs to persist: meal history, shopping lists, recipes, preferences, households.

---

## 8. How to Add a New Feature

### New database-backed entity

1. **Schema:** Write a `CREATE TABLE` SQL file in `supabase/migrations/`. Include `household_id BIGINT NOT NULL REFERENCES households(id)`.
2. **Data layer:** Add methods to `database.py`. Follow the `get_*` / `save_*` / `delete_*` naming convention. Always scope by `household_id`.
3. **Routes:** Add to `main.py`. Call `_require_auth(request)` first. Pass `user` and `current_page` to every `TemplateResponse`.
4. **Template:** Create `meal_planner_web/frontend/templates/<name>.html` extending `base.html`. HTMX partials go in `partials/<name>.html`.
5. **Navigation:** Add a link to `partials/navigation.html` with the `current_page` condition.

### New Claude capability

1. Add a method to `claude_client.py`. Use the existing `ClaudeClient` instance — don't instantiate `anthropic.Anthropic` in routes.
2. If it affects meal plan generation, add it to `build_claude_prompt()` in `main.py`.
3. If it returns structured data, parse it with `_parse_json_response()` and validate the keys before use.

### New HTMX partial endpoint

1. Return `HTMLResponse` or `TemplateResponse` with the partial template.
2. Name the template `partials/<name>.html`.
3. If the action changes the shopping list, dispatch `shopping-list-updated` from the response `<script>` block.
4. If the action might fail, return a 200 with an error fragment (not a 4xx) so HTMX swaps it in.

---

## 9. Key Non-Obvious Decisions

| Decision | Reason |
|---|---|
| `self.db`, not `self._client` | The Supabase client is stored on the `Database` instance as `self.db`. The module-level variable `_client` is private by convention. |
| Service key in `database.py`, anon key in `scraper.py` | DB writes bypass RLS (safe, server-side). Scraper reads offers, which are public. Keep them separate so a bug in the scraper can't write. |
| `household_id` in session, not JWT | Supabase JWTs only carry `sub` (user UUID) and `email`. Household membership is app-level, stored in session after the user's profile is loaded at login. |
| Preferences = persistent JSONB + session ad-hoc | Persistent preferences (diet, family size) live in the DB. Per-request choices (number of dinners this week, special requests) are session-only and merged at prompt time. |
| In-memory `chat_sessions` | Chat history doesn't need to survive restarts (the generated plan is what matters). Persisting it adds complexity for no user-visible benefit. |
| Claude prompt assembled per request | No caching. Ensures the freshest offers, history, and preferences are always used. Acceptable because the generation step itself is the bottleneck. |
| Offers filter endpoint returns inline f-string HTML | `filter_offers()` builds HTML directly rather than using a template, because the card HTML depends on per-offer data and was faster to iterate inline. If it grows, extract to a partial. |
| `clearForm()` auto-called on offer submit success | Checkboxes are cleared immediately on success so re-submitting is impossible without re-selecting. The success banner is kept visible for the user to act on (navigate to planner or shopping list). |

---

## 10. Configuration Reference

| Variable | Required | Where used | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | `claude_client.py` | Raises on startup if missing |
| `SUPABASE_URL` | Yes | `database.py`, `scraper.py`, `scrape_rema_to_db.py` | Project URL |
| `SUPABASE_KEY` | Yes | `scraper.py`, `scrape_rema_to_db.py` | Anon key, JWT format (`eyJ…`) |
| `SUPABASE_SERVICE_KEY` | Yes (prod) | `database.py` | Service role key, bypasses RLS. Falls back to `SUPABASE_KEY` if absent. |
| `SESSION_SECRET` | Yes | `main.py` middleware | Signs session cookie. Change = all sessions invalidated. |
| `SUPABASE_JWT_SECRET` | No | `auth.py` | If set, validates JWTs. If wrong, all auth silently fails. Auth falls back to session trust. |
| `HTTPS_ONLY` | Yes (prod) | `main.py` middleware | Set to `true` behind nginx TLS. Adds `Secure` flag to cookie. |
| `CLAUDE_MODEL` | No | `claude_client.py` | Override default model string. |
| `CRON_SECRET` | Yes (edge fn) | `supabase/functions/sync-offers/index.ts` | Bearer token for pg_cron → edge function calls. Set via `supabase secrets set`. |
| `ALGOLIA_APP_ID` | No | `scrape_rema_to_db.py`, `sync-offers/index.ts` | Defaults to Rema's public ID. |
| `ALGOLIA_API_KEY` | No | `scrape_rema_to_db.py`, `sync-offers/index.ts` | Defaults to Rema's public read-only key. |
