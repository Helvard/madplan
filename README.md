# Madplan — AI-Powered Family Meal Planner

A full-stack web application for weekly meal planning with Claude AI. Households chat with an AI assistant to generate personalised meal plans based on their preferences, current supermarket offers from Rema 1000, and past meal history. Includes a shared shopping list, a recipe library, and a multi-user household system.

---

## Features

- **AI Meal Planning** — Conversational meal plan generation via Claude. The AI considers household preferences, dietary restrictions, budget, and current supermarket offers.
- **Rema 1000 Offers Browser** — Browse and filter current discounts scraped daily from Rema 1000. Select items to feed directly into the meal plan prompt.
- **Shopping List** — Auto-populated from meal plans. Manually editable, categorised by department, with PDF export.
- **Family Food Almanac** — Save, generate, and import recipes. Each recipe has an AI sidebar chat for scaling, substitutions, and questions.
- **Meal History & Ratings** — Rate past meals 1–5. Ratings feed back into Claude prompts to improve future suggestions.
- **Preferences** — Per-household JSONB settings (family size, cooking style, dietary restrictions, budget). Editable via a chat interface.
- **Multi-user Households** — Invite code system so all family members share the same meal plans, shopping list, and recipes.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Frontend | Jinja2 templates + HTMX + Tailwind CSS v4 |
| Database | Supabase (PostgreSQL) |
| AI | Anthropic Claude API (`claude-sonnet-4-*`) |
| Auth | Supabase Auth + Starlette session cookies |
| PDF export | ReportLab |
| Offers scraping | Rema 1000 Algolia API |
| Scheduled sync | Supabase Edge Function (Deno) + pg_cron |
| Deployment | Docker + nginx (TLS) |

---

## Project Structure

```
Madplan/
├── meal_planner_web/
│   ├── backend/
│   │   ├── main.py                 # All routes (~2200 lines)
│   │   ├── database.py             # Supabase ORM layer
│   │   ├── claude_client.py        # Claude API wrapper
│   │   ├── auth.py                 # JWT + session auth
│   │   ├── preferences.py          # Preference helpers
│   │   ├── scraper.py              # Offer loading utilities
│   │   ├── scrape_rema_to_db.py    # Rema 1000 scraper (standalone)
│   │   └── shopping_list_parser.py
│   └── frontend/
│       ├── templates/              # Jinja2 page templates + HTMX partials
│       └── static/css/             # Compiled Tailwind CSS
├── supabase/
│   ├── functions/sync-offers/      # Edge Function: daily offer sync
│   │   └── index.ts
│   └── migrations/                 # SQL to run in Supabase SQL Editor
├── schema.sql                      # Core tables (offers, shopping_list, history, preferences)
├── schema_auth.sql                 # Auth tables (households, user_profiles)
├── schema_recipes.sql              # Recipes table (Family Food Almanac)
├── Dockerfile
├── docker-compose.yml
├── nginx.conf.example
├── requirements.txt
└── .env.example
```

---

## Prerequisites

- Python 3.12+
- Node.js (for Tailwind CSS compilation)
- A [Supabase](https://supabase.com) project
- An [Anthropic](https://console.anthropic.com) API key
- Docker + Docker Compose (for production)

---

## Local Development

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd Madplan
pip install -r requirements.txt
npm install   # for Tailwind CSS
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

See the [Environment Variables](#environment-variables) section for details.

### 3. Set up the database

Run the following SQL files in order in your Supabase SQL Editor:

1. `schema_auth.sql` — households, user_profiles
2. `schema.sql` — offers, shopping_list_items, meal_history, preferences
3. `schema_recipes.sql` — recipes (Family Food Almanac)

### 4. Run the app

```bash
cd meal_planner_web/backend
uvicorn main:app --reload --port 8000
```

The app will be available at `http://localhost:8000`.

### 5. Compile Tailwind CSS (if editing styles)

```bash
npm run build   # or: npx tailwindcss -i ... -o ... --watch
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `SUPABASE_URL` | Yes | Your Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase anon key (JWT format: `eyJ...`) |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key — needed to bypass RLS on DB writes |
| `SESSION_SECRET` | Yes | Random string for signing Starlette session cookies |
| `CLAUDE_MODEL` | No | Defaults to `claude-sonnet-4-20250514` |
| `HTTPS_ONLY` | No | Set to `true` in production (behind nginx TLS) to add `Secure` flag to session cookie |

> **Note:** `SUPABASE_KEY` must be the JWT-format anon key (`eyJ...`), not the newer publishable key format (`sb_publishable_...`). Find it in Supabase Dashboard → Settings → API → `anon` `public`.

---

## Production Deployment

### Docker

```bash
docker compose up -d
```

The `web` service runs on `127.0.0.1:8000`. Use nginx as a reverse proxy for HTTPS.

### nginx

Copy `nginx.conf.example` to `/etc/nginx/sites-available/madplan` and update the domain name and certificate paths. The config handles:

- HTTP → HTTPS redirect
- TLS termination (Let's Encrypt / Certbot)
- Proxy to `127.0.0.1:8000`
- Security headers
- Disabled buffering (needed for HTMX)

### Required `.env` settings in production

```
HTTPS_ONLY=true
SUPABASE_SERVICE_KEY=<your-service-role-key>
```

---

## Rema 1000 Offer Sync

Offers are fetched from Rema 1000's Algolia search API (public, read-only) and stored in the `offers` Supabase table.

### Automatic sync (recommended)

A Supabase Edge Function (`sync-offers`) runs daily at 23:59 UTC via `pg_cron`.

**Setup:**

1. Ensure `pg_cron` and `pg_net` extensions are enabled in your Supabase project (Database → Extensions).
2. Run the migration SQL in the Supabase SQL Editor:

```
supabase/migrations/20260301_cron_sync_offers.sql
```

**Check scheduled jobs:**
```sql
SELECT * FROM cron.job;
```

**Check run history:**
```sql
SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;
```

### Manual sync options

**From the app** — `POST /admin/scrape-offers`

**Via SQL:**
```sql
SELECT net.http_post(
  url     := 'https://<your-project-ref>.supabase.co/functions/v1/sync-offers',
  headers := jsonb_build_object(
    'Content-Type',  'application/json',
    'Authorization', 'Bearer <CRON_SECRET>'
  ),
  body    := '{}'::jsonb
);
```

**Via the standalone Python script:**
```bash
cd meal_planner_web/backend
python scrape_rema_to_db.py
```

### Deploying the Edge Function

```bash
# Install Supabase CLI
brew install supabase/tap/supabase

# Link your project
supabase login
supabase link --project-ref <your-project-ref>

# Set the cron secret
supabase secrets set CRON_SECRET=<your-secret>

# Deploy
supabase functions deploy sync-offers --no-verify-jwt
```

---

## Routes Reference

### Auth
| Method | Path | Description |
|---|---|---|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate with Supabase |
| GET | `/signup` | Signup page |
| POST | `/signup` | Create account |
| POST | `/logout` | Clear session |

### Household
| Method | Path | Description |
|---|---|---|
| GET | `/household` | Household setup page |
| POST | `/household/create` | Create new household |
| POST | `/household/join` | Join by invite code |

### Meal Planning
| Method | Path | Description |
|---|---|---|
| GET | `/` | Chat interface |
| POST | `/chat/start` | Start new session |
| POST | `/chat/message` | Send message to Claude |
| POST | `/chat/generate-plan` | Generate meal plan |
| POST | `/chat/accept-plan` | Save plan to history |
| POST | `/chat/refine-plan` | Revise plan with feedback |

### Shopping List
| Method | Path | Description |
|---|---|---|
| GET | `/shopping-list` | Shopping list page |
| POST | `/shopping-list/add-item` | Add item manually |
| POST | `/shopping-list/toggle-item/{id}` | Check/uncheck item |
| DELETE | `/shopping-list/item/{id}` | Remove item |
| POST | `/shopping-list/clear-checked` | Clear checked items |
| POST | `/shopping-list/add-from-offers` | Add selected offers |
| GET | `/shopping-list/export-pdf` | Download as PDF |

### Offers
| Method | Path | Description |
|---|---|---|
| GET | `/offers` | Browse Rema 1000 offers |
| GET | `/offers/filter` | Filter by department/search |
| POST | `/offers/submit-selections` | Add to shopping list or meal plan |
| POST | `/admin/scrape-offers` | Manually trigger sync |

### Recipes (Family Food Almanac)
| Method | Path | Description |
|---|---|---|
| GET | `/recipes` | List all recipes |
| GET | `/recipes/{id}` | Recipe detail + AI chat |
| POST | `/recipes/generate` | Generate recipe with AI |
| POST | `/recipes/import-url` | Import from webpage URL |
| POST | `/recipes/save-from-meal-plan` | Save meal as recipe |
| DELETE | `/recipes/{id}` | Delete recipe |
| POST | `/recipes/{id}/add-to-shopping-list` | Add ingredients to list |
| POST | `/recipes/{id}/chat/message` | Chat about this recipe |

### History & Preferences
| Method | Path | Description |
|---|---|---|
| GET | `/history` | Past meal plans |
| GET | `/rate-meals` | Rate unrated meals |
| POST | `/rate-meals/submit` | Submit ratings |
| GET | `/preferences` | Preferences page |
| POST | `/preferences/chat/message` | Edit preferences via chat |

---

## Auth Notes

- Authentication uses Supabase Auth with Starlette signed session cookies.
- All data is scoped by `household_id` — users only see their household's data.
- `SUPABASE_SERVICE_KEY` is required for all DB writes (bypasses Row Level Security).
- If `SUPABASE_JWT_SECRET` is set in `.env` and doesn't match the project, all authenticated routes will silently fail. The app falls back to trusting the signed session cookie if JWT validation fails.
- In production, `HTTPS_ONLY=true` is required so the session cookie gets the `Secure` flag.
