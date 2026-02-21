# Madplan — Backend Code Evaluation

> **Document purpose:** Honest assessment of the current backend before the Supabase migration.  
> **Scope:** All Python files in `meal_planner_web/backend/`  
> **Last updated:** February 2026

---

## Summary

| File | Condition | Priority |
|---|---|---|
| `claude_client.py` | Good bones, minor fixes needed | Low |
| `shopping_list_parser.py` | Works but fragile by design | Superseded by JSON approach |
| `preferences.py` | Functional but architecturally obsolete | Deleted when JSONB row lands |
| `scraper.py` | Clean, minor path and logging issues | Fix during Docker migration |
| `main.py` | Needs significant cleanup — bugs in production today | High |
| `database.py` | SQLite-specific throughout, replaced entirely | Replaced by Supabase migration |

The backend works well enough as a local prototype. The problems are mostly things that don't matter on `localhost` but will hurt in production: in-memory sessions, hardcoded paths, duplicate routes, dead code, and inline HTML generation. None of it is a fundamental architectural failure — it's cleanup work that the migration forces us to do properly.

---

## `claude_client.py`

Honestly the cleanest file in the backend. Simple wrapper, does one thing.

### Issues

**Model hardcoded as a string.**  
`"claude-sonnet-4-20250514"` will silently go stale as new models release. Move it to `.env` so it can be updated without touching code.

**`refine_meal_plan` throws away conversation history.**  
It sends the original plan + feedback as a single new message, not as a genuine multi-turn conversation. Claude has no memory of *why* the plan was structured the way it was — it can't refer back to choices made during generation because that context is gone. Once chat sessions are persisted as an FSM, refinement should send the full `messages` array from the session so it's a genuine continuation of the conversation, not a fresh prompt dressed up as a revision.

**No error handling.**  
If the API call fails — rate limit, timeout, network blip — the exception propagates all the way up to the FastAPI handler where it's caught in a generic `except Exception as e`. Worth adding retry logic with exponential backoff, or at minimum a meaningful custom exception that the caller can handle explicitly.

---

## `shopping_list_parser.py`

Reasonably well written for what it is. The category extraction, price parsing, and quantity splitting all work. But there are structural problems that will compound over time.

### Issues

**The entire approach is brittle by design.**  
This file exists because Claude is asked to produce structured data formatted as markdown, which is then parsed back into structure using regex. This is one prompt-wording change away from breaking silently. The correct fix is to ask Claude to return a JSON block for the shopping list alongside the human-readable markdown — parse the JSON, display the markdown. This file becomes unnecessary.

**Quantity regex is too narrow.**  
The pattern handles `kg`, `g`, `l`, `ml`, `stk` but misses `dl`, `cl`, `tbsp`, `tsp`, and Danish units like `dåse`, `pakke`, `pose`. More importantly, unitless quantities like `"3 Onions"` fall through to the else branch, meaning the item name becomes `"3 Onions"` with no quantity extracted. That is wrong.

**No resilience to Claude formatting variations.**  
If Claude writes `- Tomatoes, 1 kg (19,95 kr)` instead of `- 1 kg Tomatoes (19,95 kr)`, the parser silently drops the quantity. Claude's output format is not guaranteed to be consistent across calls, prompt versions, or model updates.

**`test_parser()` is orphaned.**  
Fine during development, but this should live in a proper test file before the application is deployed.

---

## `preferences.py`

This file becomes largely irrelevant once preferences move to a single Supabase JSONB row, but its current problems are worth documenting.

### Issues

**Two preference stores with no clear ownership.**  
`PreferencesManager` reads and writes `preferences.yaml`. `Database` has a separate `preferences` KV table. `main.py` uses `prefs_manager` for building Claude prompts but `db` for everything else. There is nothing enforcing that they stay in sync — they can and likely do drift silently.

**YAML on disk is not viable in Docker.**  
Once the application is containerised on Hetzner, the YAML file lives inside the container. It survives restarts but is wiped on every redeploy unless a volume is mounted specifically for it. That is avoidable operational complexity. The Supabase JSONB row has no such problem.

**`format_for_prompt()` will need updating.**  
Once preferences are in Supabase, this method must read from the DB instead of the YAML file. The formatting logic itself is good and should be preserved — just the data source changes.

### Action
Delete this file after the JSONB migration. `format_for_prompt()` logic moves into `database.py`.

---

## `scraper.py`

Clean and well structured. The separation between `load_offers_from_db`, `format_offers_for_claude`, and `get_key_offers` is good design.

### Issues

**`max_per_category` truncation is silent.**  
The default cap of 20 items per department means Claude may not see relevant offers. The prompt tells Claude "and 12 more items" but Claude cannot use what it cannot see. The current sort (best savings first) is the right heuristic — just document the cap explicitly in the prompt so Claude understands it is seeing a curated subset.

**`DEBUG:` print statement in production code.**  
`print(f"DEBUG: Loaded {len(offers)} offers from {OFFERS_DB}")` will spam stdout in production. Replace with `logging.info()`.

**Path resolution will break in Docker.**  
`OFFERS_DB = Path(__file__).resolve().parent.parent.parent / "data" / "meal_planner_unified.db"` — three `.parent` calls from a file inside a container filesystem is fragile. This goes away entirely once offers move to Supabase, which is another argument for doing the migration before the Docker work.

---

## `main.py`

The file with the most problems. It has clearly grown organically and it shows. The issues range from bugs that exist in production today to structural problems that will make the codebase hard to maintain.

### Bugs (exist today)

**Two `@app.get("/")` routes.**  
There are two `async def home()` handlers registered on the same path. FastAPI silently uses the last one. The first is dead code. This is a live bug.

**Two `@app.post("/shopping-list/add-item")` routes.**  
Same problem — one silently shadows the other. Whichever endpoint runs, it may not be the one the developer intends.

**Dead code after `return` in `build_claude_prompt()`.**  
Everything after `return "\n".join(prompt_parts)` on approximately line 340 is unreachable. It appears to be a copy-paste of an earlier prompt builder version that was never cleaned up.

**Session middleware secret key is a placeholder.**  
`secret_key="your-secret-key-here-change-in-production"` — if this reaches Hetzner as-is, session cookies are cryptographically meaningless. Must come from `.env`.

### Structural problems

**`chat_sessions = {}` in memory.**  
The primary known issue, already documented in the architecture decisions. Every server restart kills all active sessions. The FSM migration fixes this.

**`pref_chat_sessions = {}` is a second in-memory session store.**  
The preferences chat has the same restart problem. Once the FSM migration is done, both should use the `chat_sessions` table with `session_type` distinguishing them — not two separate in-memory dicts.

**`Database()` is reinstantiated per request in some endpoints.**  
Some handlers do `db = Database()` locally (creating a new connection on every request) while others use the module-level `db` instance. This is inconsistent. In the Supabase rewrite, the client should be initialised once at startup and injected via FastAPI dependency injection.

**Inline HTML generation in endpoint handlers.**  
The `/offers/filter` endpoint returns 80+ lines of raw HTML built with f-strings inside a Python function. This is hard to maintain, impossible to style consistently, and will break silently if any item name contains `<`, `>`, or `"`. HTML belongs in Jinja2 templates.

**`build_claude_prompt()` mixes concerns.**  
The function both assembles the prompt *and* reads from the database (via `prefs_manager.format_for_prompt()` and `db.get_meal_history_for_context()`). It should receive pre-loaded data as arguments and only be responsible for formatting. This makes it untestable in its current form.

---

## `database.py`

Not evaluated in detail as it is replaced entirely by the Supabase migration. The existing method signatures are useful as a reference for what `database.py` v2 needs to support — the API surface should remain largely compatible to minimise changes in `main.py`.

### Patterns to carry forward
- `get_active_shopping_list()` — keep this concept, single active list per context
- `add_shopping_list_item()` with duplicate merging — good UX, keep the behaviour
- `get_meal_history_for_context()` — the formatted output for Claude is good, just needs to query the new normalized tables

### Patterns to drop
- All `sqlite3` connection management
- `_get_connection()` / `conn.row_factory` boilerplate
- Manual `json.dumps` / `json.loads` around structured data
- `_init_tables()` — schema management moves to `schema.sql` + Supabase migrations

---

## Cross-cutting issues

**No logging framework.**  
The entire backend uses `print()` statements. In production, these go to stdout with no timestamps, no levels, and no filtering. Replace with Python's `logging` module before deploying.

**No input validation.**  
FastAPI form parameters are taken at face value. Item names, quantities, and categories are inserted directly into SQL without sanitisation. Supabase's parameterised queries handle SQL injection, but application-level validation (length limits, allowed characters, numeric ranges) is absent.

**No tests.**  
There are no unit tests, integration tests, or even smoke tests. The `test_parser()` function in `shopping_list_parser.py` is the closest thing. Before the Supabase migration, add at minimum: tests for the shopping list parser, tests for the prompt builder, and tests for the FSM state transitions.

**`.env` discipline is inconsistent.**  
`ANTHROPIC_API_KEY` is correctly read from environment. The session secret key, the model name, and database paths are all hardcoded. Establish a single `config.py` that reads all configuration from environment variables with sensible defaults and validation on startup.
