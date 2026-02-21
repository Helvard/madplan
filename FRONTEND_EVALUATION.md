# Madplan — Frontend Code Evaluation

> **Document purpose:** Honest assessment of the current frontend before the Supabase migration.  
> **Scope:** All HTML/JS files in `meal_planner_web/frontend/`  
> **Last updated:** February 2026

---

## Summary

| File | Condition | Priority |
|---|---|---|
| `index.html` | Works, has a debug block left in production | High — remove debug block immediately |
| `offers.html` | Navigation duplicated, not using shared component | Medium |
| `shopping_list.html` | Navigation duplicated, full-page reloads for interactions | Medium |
| `history.html` | Broken HTML structure, orphaned closing tag | Medium |
| `preferences.html` | Broken HTML structure, session ID wiring fragile | Medium |
| `rate_meals_simple.html` | Hardcoded nav, orphaned `rate_meals.html` exists | Low |
| `partials/navigation.html` | Good idea, inconsistently applied | Medium |
| `partials/message.html` | XSS risk on bot messages | High |
| `partials/meal_plan.html` | `meal_plan_raw` passed as inline JSON — risky | High |
| `partials/shopping_list_items.html` | Logic-heavy Jinja, grouping belongs in backend | Low |
| `partials/shopping_list_stats.html` | Dual data source handling is clever but fragile | Low |

The frontend is coherent and functional as a local prototype. The technology choices — htmx + Tailwind + Jinja2 — are sensible and appropriate for this application. The problems are a mix of one active security risk, structural inconsistencies born from iterative development, and duplicate code that will make maintenance painful at scale.

---

## `index.html`

The chat interface is well structured. The htmx wiring is correct and the session ID capture pattern works.

### Issues

**Debug block is live in production.**
The yellow debug panel at the top of the page — showing `selected_offers` variable contents, type, and length — is rendered for every user on every page load. This was clearly left in during development and never removed. It should be deleted before the application goes anywhere near Hetzner.

**Two `htmx:afterSwap` listeners on `document.body`.**
Both handlers fire on every swap event. The first scrolls and clears input; the second captures the session ID. They should be merged into one listener with internal conditionals, otherwise both run on every htmx interaction across the entire page, including unrelated swaps.

**Session ID wiring is fragile.**
The session ID is extracted by querying the just-swapped DOM for a `[data-session-id]` attribute. This works, but it's indirect — if the markup structure of the message partial changes, the session ID silently stops being captured and all subsequent messages fail. A cleaner approach is to have the `/chat/start` endpoint return the session ID in a response header (`HX-Trigger` or a custom header) and read it from there.

**`clearSelectedOffers()` uses `window.location.reload()`.**
This causes a full page reload to reflect cleared state. Since the selected offers banner is server-rendered, this is technically correct — but it creates a noticeable flash. Once sessions are in Supabase, this state should be managed without a reload.

---

## `partials/message.html`

### Issues

**XSS risk on bot messages.**
Bot messages are rendered with `{{ message | safe }}`. This disables Jinja2's auto-escaping and renders the content as raw HTML. The rationale is that Claude returns markdown which gets converted to HTML in the backend. However, if anything other than Claude-generated content ever reaches this partial — an error message, user-influenced text, a piece of data from the DB — it will be injected unescaped into the DOM. The `| safe` filter should be used only on content that is provably sanitised, ideally via a dedicated Bleach/MarkupSafe step in the backend before the template ever sees it.

User messages do not use `| safe` — correct, but inconsistent.

**Auto-trigger generation via an empty div.**
The `trigger_generation` mechanism works by rendering a `<div>` with `hx-trigger="load"` that fires a POST as soon as it appears in the DOM. This is a creative htmx pattern but it is not obvious to anyone reading the template what is happening or why. A comment explaining the intent would help. More importantly, if this div is ever accidentally rendered in a context where generation shouldn't trigger, it will fire silently.

---

## `partials/meal_plan.html`

### Issues

**`meal_plan_raw` passed as inline JSON in an htmx attribute.**
The raw Claude output (potentially thousands of characters of markdown) is passed via:
```html
hx-vals='{"session_id": "{{ session_id }}", "meal_plan": {{ meal_plan_raw|tojson }}}'
```
This is fragile. If the meal plan contains characters that break JSON serialisation within an HTML attribute — certain Unicode sequences, deeply nested quotes — the entire `hx-vals` attribute becomes invalid and the request silently sends nothing. The raw plan should be stored in the session on the backend and retrieved by session ID, not round-tripped through the DOM.

**Inline `<script>` per rendered instance.**
`showShoppingListPrompt()` and `acceptPlanFinal()` are defined in a `<script>` block inside the partial. If multiple meal plans are ever rendered in the same session (e.g., after a refinement), this script block is injected into the DOM multiple times, redefining the same functions repeatedly. Move these to a shared JS file or the base template.

**`onclick` and `hx-post` on the same button.**
The "Yes, Add to Shopping List" button has both `hx-post` (handled by htmx) and `onclick="acceptPlanFinal(...)"` (inline JS). The order of execution is not guaranteed. This works today because `acceptPlanFinal` just hides a div, but it's a pattern that becomes unpredictable when either handler does something async.

---

## `partials/navigation.html`

Good design decision — a shared navigation component is the right call. The `current_page` variable for active state highlighting works cleanly.

### Issues

**Inconsistently applied.**
`shopping_list.html`, `offers.html`, and `rate_meals_simple.html` all define their own hardcoded navigation bars instead of using this partial. This means three separate codebases for what should be one component. When a nav item is added or changed, it must be updated in four places. `navigation.html` exists but is only used in `index.html`, `history.html`, and `preferences.html`.

**Badge polling every 30 seconds.**
`setInterval(() => AppState.updateBadges(), 30000)` makes a fetch to `/shopping-list/count` every 30 seconds on every page. Once Supabase Realtime is wired up, this polling should be replaced entirely with a websocket subscription — the count updates the moment someone checks off an item, not on a 30-second lag.

**`AppState` is a global object on `window`.**
This works for a single-page context but is not scoped. If two scripts on the same page define `AppState`, they clobber each other silently. For a small app this is fine, but worth noting as the JS surface grows.

---

## `shopping_list.html`

### Issues

**Navigation is duplicated, not using the shared partial.**
This page has its own `<nav>` block, identical in purpose to `navigation.html` but different in markup. It should `{% include 'partials/navigation.html' %}` like the other pages.

**Interactions use full-page form POSTs.**
Checking off an item (`/shopping-list/toggle/{id}`), deleting an item (`/shopping-list/item/{id}/delete`), and clearing the list (`/shopping-list/clear-all`) all use standard HTML form submission with a redirect. This causes a full page reload on every interaction. Given that this page is intended to replace Apple Reminders — used in real-time on a phone in a shop — full-page reloads are not acceptable. These interactions should be htmx POSTs that swap only the affected element, exactly as the chat interface works.

**Empty state shows the form, items state does not include the form.**
The add-item form appears in the empty state but also separately in the non-empty state. Both show the form, but the empty state version has no quantity field. This inconsistency means the UI behaves differently depending on whether the list is empty or not, which is surprising.

---

## `offers.html`

### Issues

**Navigation is duplicated.**
Same issue as `shopping_list.html` — own `<nav>` block instead of using the shared partial.

**`updateSelectedCount()` uses `querySelectorAll` with a name-based filter.**
```javascript
document.querySelectorAll('input[name^="meal_plan_"]:not([name*="_qty_"]):checked')
```
This works but is brittle. When the offer list is replaced by htmx (via the filter endpoint), the newly rendered checkboxes come from the server as f-string HTML — meaning they are not Jinja2-rendered and do not go through the template engine. If the naming convention changes on the backend side, the JS selector breaks silently.

**Entire offer list re-renders on filter.**
When the user types in the search box or changes department, htmx re-renders the entire `#offers-list`. Any checkboxes the user has already ticked are lost because the DOM is replaced. This is a significant UX problem — a user who has ticked 5 items, then filters to find one more, loses all 5 selections. Selections need to be preserved across filter operations, either by tracking state in JS or by sending current selections with each filter request.

**Sticky submit button overlaps content on mobile.**
The `fixed bottom-6 right-6` submit button will overlap list content on small screens. On mobile — which is likely the primary use case when shopping — this becomes a real usability issue.

---

## `history.html` and `preferences.html`

### Issues

**Broken HTML structure in both files.**
Both files include the navigation partial inside a `<div class="min-h-screen flex flex-col">`, then have a stray `</header>` closing tag immediately after the include:
```html
{% include 'partials/navigation.html' %}
</header>
```
The navigation partial renders a `<header>` element that opens and closes itself. The stray `</header>` in the parent template is an orphaned tag that produces invalid HTML. Browsers recover silently, but it is a bug.

**`preferences.html` has the same session ID wiring problem as `index.html`.**
The `data-session-id` attribute extraction from the swapped DOM is fragile for the same reasons described under `index.html`. The two chat interfaces — meal planning and preferences — share the same structural weakness.

**`preferences.html` displays preferences from the YAML-backed `PreferencesManager`.**
Once preferences move to Supabase, the template data source changes. The template structure itself (`preferences.food.favorites`, `preferences.cooking.style`, etc.) maps cleanly to the planned JSONB structure, so the template needs minimal changes — just the backend data source.

---

## `rate_meals_simple.html`

### Issues

**Hardcoded navigation.**
This page has its own `<header>` block with a truncated nav (only Plan, History, Preferences — missing Offers and Shopping List). It should use the shared partial.

**`rate_meals.html` exists as a separate file.**
There are two rating templates: `rate_meals.html` and `rate_meals_simple.html`. The backend routes to `rate_meals_simple.html`. The other file appears to be an earlier version that was never deleted. One of them is dead code.

**`would_repeat` checkbox defaults to `checked`.**
Every meal defaults to "I'd make this again" regardless of the star rating selected. A user who gives a meal 1 star will still submit `would_repeat=true` unless they remember to uncheck the box. The default should be unchecked, or the checkbox state should update automatically based on the rating selected.

---

## `partials/shopping_list_items.html`

### Issues

**Grouping logic is in the template.**
The template does its own category grouping using Jinja2's limited dict-manipulation syntax (`items_by_category.update(...)`, `.append()`). This works but it is not what templates are for. The backend should send `items_by_category` as a pre-grouped dict, which is actually what the backend's `get_shopping_list_by_category()` method already returns — this template just doesn't use it.

**Two versions of this partial exist.**
`shopping_list_items.html` and `shopping_list_items_partial.html` both exist in the partials directory. Similarly, `shopping_list_stats.html` and `shopping_list_stats_partial.html`. The `_partial` suffix versions appear to be duplicates from an earlier iteration. One set is dead code.

---

## Cross-cutting Issues

**No base template.**
Every full-page template (`index.html`, `history.html`, `offers.html`, etc.) independently loads htmx and Tailwind from CDN, defines its own `<head>`, and writes its own footer. A Jinja2 `base.html` with `{% block content %}` would eliminate this duplication entirely and make global changes (adding a favicon, updating library versions, adding a CSP header meta tag) a one-line change.

**CDN dependencies with unpinned versions.**
`https://cdn.tailwindcss.com` — the Tailwind CDN script — is the development build, intended for prototyping only. It downloads the entire Tailwind library and generates CSS on the fly in the browser. For production, Tailwind should be compiled to a static CSS file containing only the classes actually used. The Tailwind docs explicitly warn against using the CDN script in production.

htmx is pinned to `1.9.10` in most templates, but should be verified consistent across all templates.

**No mobile-first design consideration.**
The shopping list and offers pages — the two most likely to be used on a phone while standing in a shop — are designed for desktop widths. The offers page has a fixed-position button that overlaps content on small screens. The shopping list checkbox targets are adequate but not optimised for touch. Given that replacing Apple Reminders is a stated goal, mobile usability should be a first-class concern in the next iteration.

**`confirm()` dialogs for destructive actions.**
Delete and clear operations use `onclick="return confirm('...')"` — the browser's native modal. This is fine functionally but looks out of place against the otherwise polished Tailwind UI. An inline confirmation pattern (show a "Are you sure? Yes / No" inline where the button was) would be more consistent with the design.
