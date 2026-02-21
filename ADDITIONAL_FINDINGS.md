# Madplan — Additional Findings

> **Document purpose:** Issues found outside the main application code — scraper, dependencies, secrets management, and repository hygiene.  
> **Scope:** `scrape_rema_to_db.py`, `requirements.txt`, `.env`, `data/`  
> **Last updated:** February 2026

---

## Summary

| Area | Condition | Priority |
|---|---|---|
| `.env` + no `.gitignore` | Live API key with no protection | **Critical — do this now** |
| `scrape_rema_to_db.py` | Hardcoded absolute path, hardcoded API keys | High |
| `requirements.txt` | Unpinned versions, no lockfile | Medium |
| `data/` directory | Three databases, orphaned YAML | Low — cleaned up by migration |

---

## Secret Management — Critical

### No `.gitignore` exists

There is no `.gitignore` in the project. The moment `git init` is run and `git add .` is executed — or if this has already happened — the following get committed to version history:

- `.env` containing the live Anthropic API key
- `*.db` files containing all meal history, preferences, and shopping list data
- `data/preferences.yaml`
- `__pycache__/` and `.pyc` files
- `.DS_Store` files

**Create `.gitignore` immediately with at minimum:**

```
.env
*.db
venv/
__pycache__/
*.pyc
.DS_Store
data/
```

### Live API key in `.env`

The `.env` file contains a real, active `ANTHROPIC_API_KEY`. With no `.gitignore` protecting it, this key is one `git push` away from being public. Even if the repo is private, keys in version history are a persistent risk — they survive branch deletions and repo visibility changes.

**Action required before anything else:**
1. Go to [console.anthropic.com](https://console.anthropic.com) → API Keys
2. Revoke the current key
3. Generate a new key
4. Update `.env` with the new key
5. Add `.env` to `.gitignore`
6. Create `.env.example` with placeholder values for documentation:

```
ANTHROPIC_API_KEY=your-key-here
SUPABASE_URL=your-project-url
SUPABASE_KEY=your-anon-key
SESSION_SECRET=generate-a-random-string
CLAUDE_MODEL=claude-sonnet-4-20250514
```

---

## `scrape_rema_to_db.py`

### Issues

**Hardcoded absolute path.**
```python
DB_FILE = "/Users/erikhelvard/Desktop/Madplan/data/meal_planner_unified.db"
```
This is a personal home directory path baked into the source code. It will fail immediately on Hetzner. It needs to be either a path relative to the script file (like the other backend files use), or — better — an environment variable read from `.env`. Once offers move to Supabase, this line disappears entirely, but it needs fixing before any intermediate deployment.

**Algolia credentials hardcoded in source.**
```python
ALGOLIA_APP_ID = "FLWDN2189E"
ALGOLIA_API_KEY = "fa20981a63df668e871a87a8fbd0caed"
```
These are Rema 1000's public-facing read-only Algolia keys extracted from their frontend. The security risk is lower than a private API key — they're intentionally public — but they still shouldn't be in source code. If Rema rotates them, you're editing source code rather than a config file. Move them to `.env`.

**No error handling on the Algolia request beyond a bare `except`.**
The scraper catches all exceptions with a print and a `return`. If the API returns a partial result, a malformed response, or a rate-limit error, the script silently exits after deleting the old offers but before inserting new ones — leaving the offers table empty until the next run. The delete and insert should be wrapped in a transaction, or the delete should only happen after a successful fetch.

**`print_summary()` uses `SELECT *` patterns that will break on the new schema.**
Once offers move to Supabase with UUIDs and the new column names, the summary queries need updating. Minor, but worth noting.

---

## `requirements.txt`

### Issues

**All versions use `>=` — no upper bounds, no lockfile.**
```
anthropic>=0.39.0
fastapi>=0.109.0
```
This means `pip install` on a fresh Hetzner deployment will pull the latest available version of every package. If Anthropic ships a breaking change in a major version, or FastAPI changes a default behaviour, the next deployment silently breaks production with no easy way to reproduce the previous working state.

**Fix:** Run `pip freeze > requirements.txt` in the current working venv to capture exact versions. Commit that as the lockfile. The loose `>=` version can go in a separate `requirements.in` if you want to track intentional constraints separately.

**`pyyaml` will be redundant after the migration.**
`preferences.py` and the YAML file are deleted when preferences move to Supabase. `pyyaml` has no other uses in the codebase. Remove it from `requirements.txt` at that point.

**Missing packages that will be needed:**
- `supabase` — the `supabase-py` client for all DB operations
- `httpx` — recommended async HTTP client for FastAPI; `requests` is sync-only

---

## `data/` Directory

### Issues

**Three databases coexist.**
`meal_planner.db`, `rema_offers.db`, and `meal_planner_unified.db` all sit in `data/`. The first two are superseded by the unified database, which is itself superseded by Supabase. Once the migration is complete and verified, the `data/` directory can be removed from the project entirely — its only remaining use before then is as a migration source.

**`preferences.yaml` is a third copy of preference data.**
Alongside the SQLite KV table and the `preferences` dict in memory, `data/preferences.yaml` is a third divergent copy of the same data. All three can drift independently. This file is deleted as part of the Supabase JSONB migration.

---

## Recommended Pre-Migration Checklist

Work through these before writing a line of migration code:

1. **Rotate the Anthropic API key** — revoke current, generate new, update `.env`
2. **Create `.gitignore`** — protect `.env`, `*.db`, `venv/`, `__pycache__/`, `data/`
3. **Create `.env.example`** — document all required environment variables with placeholders
4. **Pin `requirements.txt`** — run `pip freeze` to capture exact working versions
5. **Add `supabase` and `httpx`** to requirements
6. **Move Algolia credentials to `.env`** — out of `scrape_rema_to_db.py`
7. **Remove the debug block from `index.html`** — it is live right now
