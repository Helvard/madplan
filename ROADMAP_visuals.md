# Madplan — Rotating Meal Photo Backgrounds
**Feature description & implementation plan**
*March 2026*

---

## Overview

The app background rotates through photos of meals the household has actually cooked. On first load it shows a default fallback photo. Over time, as the family rates meals and uploads photos, the app becomes visually personal — reflecting their own kitchen.

Photos are also attachable to recipes in the Family Food Almanac.

---

## User-facing behaviour

### Where photos appear
- Full-page background on all pages (blurred, warm-toned overlay)
- One photo per page load, chosen randomly from the household's 10 most recent meal photos
- Falls back to a hardcoded default photo (the bread photo) until the household has uploaded their own

### Where photos are uploaded
1. **Meal rating flow** — after selecting a star rating, an optional prompt appears: *"Tog du et billede?"* with a file upload field and instant preview. Photo is not uploaded until the user saves the rating.
2. **Recipe page** — an optional photo field when creating or editing a recipe.

### Upload UX detail
Both upload fields must show an instant client-side preview (via `FileReader`) before submission. The user sees what they're attaching before anything is saved. The field is clearly optional — never a required step.

---

## Data model changes

### `meals` table
```sql
ALTER TABLE meals ADD COLUMN photo_url TEXT;
```

### `recipes` table
```sql
ALTER TABLE recipes ADD COLUMN photo_url TEXT;
```

---

## Supabase Storage

**Bucket name:** `meal-photos`

**Path structure:**
```
households/{household_id}/meals/{meal_id}.jpg
households/{household_id}/recipes/{recipe_id}.jpg
```

This keeps photos scoped per household, which prepares cleanly for RLS enforcement later.

---

## Backend changes

### `database.py`
Add one new method:
```python
def get_household_background_photos(self, household_id, limit=10) -> list[str]:
    # Query meals WHERE household_id = ? AND photo_url IS NOT NULL
    # ORDER BY created_at DESC LIMIT {limit}
    # Returns list of public photo URLs
```

Update the meal rating save method to accept an optional `photo_url` parameter and write it to the `meals` row.

### `main.py`

**Rating submission endpoint** (`POST /rate-meals/submit`):
- Accept an optional file upload alongside the existing rating fields
- If a file is present: upload to Supabase Storage at the correct path, retrieve the public URL, pass it to the database save method
- If no file: save the rating without a photo, no change to existing behaviour

**Index page load** (`GET /`):
- Call `get_household_background_photos(household_id)`
- If results exist: pass the list to the template as `background_photos`
- If empty: pass the fallback default photo URL instead

Apply the same background photo fetch to all other full-page routes (offers, shopping list, history, recipes, preferences).

---

## Frontend changes

### All full-page templates

Inject the selected background photo via a CSS variable in the `<head>`:

```html
<style>
  :root {
    --bg-photo: url('{{ background_photo_url }}');
  }
</style>
```

The `background_photo_url` value is chosen randomly in the template from the `background_photos` list passed by the backend:

```jinja2
{% set background_photo_url = background_photos | random %}
```

The CSS for the background (blur + warm overlay) lives in the shared base template and never needs to change — only the variable value changes per load.

### `rate_meals_simple.html`

Add to each meal's rating card, below the star selector:

```html
<div class="photo-upload-field">
  <label>Tog du et billede? <span class="optional">(valgfrit)</span></label>
  <input type="file" accept="image/*" onchange="previewPhoto(this)" />
  <img class="photo-preview" style="display:none" />
</div>
```

JavaScript preview handler:
```javascript
function previewPhoto(input) {
  const preview = input.nextElementSibling;
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = e => {
      preview.src = e.target.result;
      preview.style.display = 'block';
    };
    reader.readAsDataURL(input.files[0]);
  }
}
```

### Recipe pages (`recipes/{id}`, recipe create form)

Same optional upload field and preview pattern as the rating page.

---

## CSS background implementation

```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: var(--bg-photo);
  background-size: cover;
  background-position: center;
  filter: blur(48px);
  transform: scale(1.05); /* prevents blur edge bleed */
  z-index: -1;
}

body::after {
  content: '';
  position: fixed;
  inset: 0;
  background: rgba(244, 240, 232, 0.72); /* warm parchment overlay */
  z-index: -1;
}
```

The `transform: scale(1.05)` is important — CSS blur creates transparent edges at the viewport boundary. Scaling up slightly hides this.

All UI cards retain their solid/near-solid backgrounds. The photo only shows through in the gaps between elements — the nav area, margins, and spacing. Text never sits directly on the photo.

---

## Implementation order

1. `schema.sql` — add `photo_url` to `meals` and `recipes`
2. Supabase Storage — create `meal-photos` bucket, set public read policy
3. `database.py` — add `get_household_background_photos()`, update rating save method
4. `main.py` — update rating endpoint to handle file upload, update all page routes to pass background photos
5. Base template — add CSS variable injection and `::before`/`::after` background CSS
6. `rate_meals_simple.html` — add optional photo upload field with preview
7. Recipe templates — add optional photo upload field with preview

---

## Fallback behaviour summary

| Situation | Background shown |
|---|---|
| Household has meal photos | Random photo from 10 most recent |
| Household has no photos yet | Hardcoded default (bread photo) |
| Photo URL is broken/missing | CSS gracefully shows the parchment overlay only |
