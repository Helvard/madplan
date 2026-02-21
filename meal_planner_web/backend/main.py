#!/usr/bin/env python3
"""
Meal Planner Web Application
FastAPI backend with htmx frontend
"""

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json
import os
from datetime import datetime
from typing import Optional
from html import escape
import markdown

from starlette.middleware.sessions import SessionMiddleware

from fastapi.responses import HTMLResponse, StreamingResponse
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# Import our existing modules
from database import Database
from claude_client import ClaudeClient
from scraper import load_offers_from_db, format_offers_for_claude

app = FastAPI(title="Meal Planner")

_session_secret = os.environ.get("SESSION_SECRET")
if not _session_secret:
    raise RuntimeError("SESSION_SECRET environment variable is not set")
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

# Create directories if they don't exist
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files and templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Initialize services
db = Database()
claude = ClaudeClient()

# Session storage (in-memory for now, could move to Redis later)
chat_sessions = {}


@app.get("/shopping-list/items", response_class=HTMLResponse)
async def get_shopping_list_items_html(request: Request):
    """Get shopping list items as HTML partial."""
    db = Database()
    shopping_list = db.get_active_shopping_list()
    
    if not shopping_list:
        return "<div class='text-center py-8 text-gray-500'>No shopping list found</div>"
    
    items = db.get_shopping_list_items(shopping_list['id'])
    
    # Group by category
    items_by_category = {}
    categories = []
    
    for item in items:
        cat = item['category'] or 'Other'
        if cat not in items_by_category:
            items_by_category[cat] = []
            categories.append(cat)
        items_by_category[cat].append(item)
    
    # Sort categories
    category_order = ['Produce', 'Dairy', 'Meat', 'Pantry', 'Bakery', 'Frozen', 'Beverages', 'Other']
    categories.sort(key=lambda x: category_order.index(x) if x in category_order else 999)
    
    if not items:
        return """
        <div class='text-center py-8 text-gray-500'>
            <p class='text-lg font-medium mb-2'>No items in shopping list</p>
            <p class='text-sm'>Add items manually or from offers/meal plans</p>
        </div>
        """
    
    return templates.TemplateResponse("partials/shopping_list_items.html", {
        "request": request,
        "items_by_category": items_by_category,
        "categories": categories
    })


@app.get("/shopping-list/stats", response_class=HTMLResponse)
async def get_shopping_list_stats(request: Request):
    """Get shopping list statistics as HTML partial."""
    db = Database()
    shopping_list = db.get_active_shopping_list()
    
    if not shopping_list:
        return ""
    
    items = db.get_shopping_list_items(shopping_list['id'])
    
    total_count = len(items)
    checked_count = sum(1 for item in items if item['checked'])
    unchecked_count = total_count - checked_count
    progress_percent = (checked_count / total_count * 100) if total_count > 0 else 0
    
    # Calculate estimated cost (only unchecked items)
    total_cost = sum(
        item['price_estimate'] or 0 
        for item in items 
        if not item['checked'] and item['price_estimate']
    )
    
    return templates.TemplateResponse("partials/shopping_list_stats.html", {
        "request": request,
        "total_count": total_count,
        "checked_count": checked_count,
        "unchecked_count": unchecked_count,
        "progress_percent": progress_percent,
        "total_cost": total_cost
    })


@app.post("/shopping-list/toggle/{item_id}")
async def toggle_shopping_list_item_endpoint(item_id: int):
    """Toggle checked status of an item."""
    db = Database()
    db.toggle_shopping_list_item(item_id)
    
    # Redirect back to shopping list page
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/shopping-list", status_code=303)

@app.post("/shopping-list/item/{item_id}/delete")
async def delete_shopping_list_item_endpoint(item_id: int):
    """Delete an item from shopping list."""
    db = Database()
    db.remove_shopping_list_item(item_id)
    
    # Redirect back to shopping list page
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/shopping-list", status_code=303)

@app.delete("/shopping-list/item/{item_id}", response_class=HTMLResponse)
async def remove_shopping_list_item_endpoint(item_id: int):
    """Remove an item from shopping list."""
    db = Database()
    db.remove_shopping_list_item(item_id)
    
    return """
    <script>
        document.body.dispatchEvent(new Event('item-removed'));
    </script>
    """


@app.post("/shopping-list/clear-checked")
async def clear_checked_items():
    """Clear all checked items from shopping list."""
    from fastapi.responses import RedirectResponse
    
    db = Database()
    shopping_list = db.get_active_shopping_list()
    
    if shopping_list:
        db.clear_shopping_list(shopping_list['id'], checked_only=True)
    
    return RedirectResponse(url="/shopping-list", status_code=303)


@app.post("/shopping-list/clear-all")
async def clear_all_items():
    """Clear all items from shopping list."""
    from fastapi.responses import RedirectResponse
    
    db = Database()
    shopping_list = db.get_active_shopping_list()
    
    if shopping_list:
        db.clear_shopping_list(shopping_list['id'], checked_only=False)
    
    return RedirectResponse(url="/shopping-list", status_code=303)


@app.get("/shopping-list/export-pdf")
async def export_shopping_list_pdf():
    """Export shopping list as PDF."""
    db = Database()
    shopping_list = db.get_active_shopping_list()
    
    if not shopping_list:
        return HTMLResponse("No shopping list found", status_code=404)
    
    items = db.get_shopping_list_items(shopping_list['id'], include_checked=False)
    
    # Group by category
    items_by_category = {}
    for item in items:
        cat = item['category'] or 'Other'
        if cat not in items_by_category:
            items_by_category[cat] = []
        items_by_category[cat].append(item)
    
    # Generate PDF
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Title
    p.setFont("Helvetica-Bold", 20)
    p.drawString(30, height - 40, "Shopping List")
    
    # Date
    p.setFont("Helvetica", 10)
    p.drawString(30, height - 60, datetime.now().strftime("%A, %B %d, %Y"))
    
    # Items
    y = height - 100
    category_order = ['Produce', 'Dairy', 'Meat', 'Pantry', 'Bakery', 'Frozen', 'Beverages', 'Other']
    sorted_categories = sorted(
        items_by_category.keys(),
        key=lambda x: category_order.index(x) if x in category_order else 999
    )
    
    for category in sorted_categories:
        cat_items = items_by_category[category]
        
        # Category header
        p.setFont("Helvetica-Bold", 14)
        p.drawString(30, y, category)
        y -= 25
        
        # Items
        p.setFont("Helvetica", 11)
        for item in cat_items:
            # Checkbox
            p.rect(40, y - 3, 10, 10)
            
            # Item text
            text = f"{item['item_name']}"
            if item['quantity']:
                text += f" - {item['quantity']}"
            if item['price_estimate']:
                text += f" (~{item['price_estimate']:.2f} kr)"
            
            p.drawString(55, y, text)
            y -= 20
            
            # New page if needed
            if y < 50:
                p.showPage()
                y = height - 40
                p.setFont("Helvetica", 11)
        
        y -= 10  # Extra space between categories
    
    p.save()
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=shopping_list_{datetime.now().strftime('%Y%m%d')}.pdf"
        }
    )

@app.post("/chat/start")
async def start_chat(request: Request):
    """Start a new chat session."""
    session_id = datetime.now().isoformat()
    
    chat_sessions[session_id] = {
        "state": "ask_num_dinners",
        "messages": [],
        "preferences": {}
    }
    
    # Return initial bot message
    return templates.TemplateResponse("partials/message.html", {
        "request": request,
        "message": markdown.markdown("👋 Hi! Ready to plan your meals for the week?"),
        "is_bot": True,
        "session_id": session_id
    })


@app.post("/chat/message")
async def chat_message(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...)
):
    """Handle incoming chat messages and respond based on conversation state."""
    
    if session_id not in chat_sessions:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "Session expired. Please refresh the page."
        })
    
    session = chat_sessions[session_id]
    state = session["state"]
    
    # Echo user message
    user_msg = templates.TemplateResponse("partials/message.html", {
        "request": request,
        "message": message,
        "is_bot": False
    })
    
    # Process based on state
    if state == "ask_num_dinners":
        try:
            num_dinners = int(message.strip())
            if 1 <= num_dinners <= 14:
                session["preferences"]["num_dinners"] = num_dinners
                session["state"] = "ask_special_prefs"
                
                bot_response = f"Great! Planning {num_dinners} dinners. Any special preferences this week? (e.g., 'extra fish', 'no beef', 'quick meals only', or just say 'none')"
            else:
                bot_response = "Please enter a number between 1 and 14."
        except ValueError:
            bot_response = "Please enter a valid number of dinners (e.g., 7)."
    
    elif state == "ask_special_prefs":
        if message.lower().strip() not in ['none', 'no', 'n', '']:
            session["preferences"]["special_prefs"] = message
        session["state"] = "ask_existing_ingredients"
        
        bot_response = "Do you have any ingredients at home to use up? (e.g., 'leftover chicken', 'half bag of pasta', or 'none')"
    
    elif state == "ask_existing_ingredients":
        if message.lower().strip() not in ['none', 'no', 'n', '']:
            session["preferences"]["existing_ingredients"] = message
        
        session["state"] = "generating"
        bot_response = "Perfect! 🤖 Generating your meal plan... (this takes 30-60 seconds)"
    
    elif state == "generating":
        # User sent a message while we're supposed to be generating
        # This shouldn't happen in normal flow, but handle it gracefully
        bot_response = "I'm working on your meal plan. Please wait..."
    
    elif state == "review_plan":
        # Handle feedback on the meal plan
        if message.lower() in ['accept', 'yes', 'looks good', 'perfect']:
            session["state"] = "complete"
            bot_response = "✅ Great! Your meal plan has been saved. Happy cooking! 🍳"
            # TODO: Save to database
        else:
            # User wants changes
            bot_response = "I'll adjust the plan based on your feedback..."
            # TODO: Send feedback to Claude for revision
    
    else:
        bot_response = "I'm not sure what to do next. Let's start over."
    
    # Return bot response
    bot_msg = templates.TemplateResponse("partials/message.html", {
        "request": request,
        "message": markdown.markdown(bot_response),
        "is_bot": True,
        "session_id": session_id,
        "trigger_generation": (session["state"] == "generating")  # Auto-trigger if we just entered generating state
    })
    
    return bot_msg


@app.post("/chat/generate-plan")
async def generate_plan(request: Request, session_id: str = Form(...)):
    """Generate the meal plan using Claude."""
    
    if session_id not in chat_sessions:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "Session expired."
        })
    
    session = chat_sessions[session_id]
    prefs = session["preferences"]
    
    # Load offers
    offers = load_offers_from_db()
    offers_text = format_offers_for_claude(offers)
    
    # Get selected offers from request session (NEW)
    selected_offers = request.session.get('selected_offers', [])
    if selected_offers:
        prefs['selected_offers'] = selected_offers
        # Clear them after using so they don't persist forever
        request.session.pop('selected_offers', None)
        # Build prompt
    prompt = build_claude_prompt(offers_text, prefs)

    # Call Claude
    try:
        meal_plan = claude.generate_meal_plan(prompt)
        
        # Convert markdown to HTML for display
        meal_plan_html = markdown.markdown(meal_plan, extensions=['tables', 'fenced_code'])
        
        session["meal_plan"] = meal_plan  # Store raw text
        session["state"] = "review_plan"
        
        # Return meal plan with action buttons
        # UPDATED: Add meal_plan_raw to pass to shopping list parser
        return templates.TemplateResponse("partials/meal_plan.html", {
            "request": request,
            "meal_plan": meal_plan_html,      # HTML version for display
            "meal_plan_raw": meal_plan,       # Raw text for parser (NEW)
            "session_id": session_id
        })
    
    except Exception as e:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": f"Error generating plan: {str(e)}"
        })


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the main chat interface."""
    selected_offers = request.session.get('selected_offers', [])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "selected_offers": selected_offers
    })


# Add endpoint to clear selected offers
@app.post("/offers/clear-selected")
async def clear_selected_offers(request: Request):
    """Clear selected offers from session."""
    request.session.pop('selected_offers', None)
    return {"success": True, "message": "Selected offers cleared"}

@app.post("/chat/accept-plan")
async def accept_plan(request: Request, session_id: str = Form(...)):
    """Accept the meal plan and save it to database."""
    
    if session_id not in chat_sessions:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "Session expired."
        })
    
    session = chat_sessions[session_id]
    meal_plan_text = session.get("meal_plan", "")
    
    if not meal_plan_text:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "No meal plan to save."
        })
    
    # Parse the meal plan to extract individual meals
    # This is a simple parser - assumes format "Day 1: Meal Name"
    try:
        plan_date = datetime.now().strftime('%Y-%m-%d')
        meals = []
        
        # Simple regex to find "Day X: Meal Name" patterns
        import re
        day_pattern = re.compile(r'Day (\d+):\s*([^\n]+)')
        matches = day_pattern.findall(meal_plan_text)
        
        for day_num, meal_name in matches:
            meals.append({
                'name': meal_name.strip(),
                'day_number': int(day_num)
            })
        
        # Save to database
        if meals:
            db.save_meal_plan(plan_date, meals)
            session["state"] = "complete"
            
            bot_response = f"✅ Perfect! I've saved your {len(meals)}-day meal plan.\n\nAfter you've cooked these meals, come back and rate them so I can make even better suggestions next time!\n\nWould you like to start planning another week?"
        else:
            # Couldn't parse meals, save anyway with generic names
            bot_response = "✅ Your meal plan has been saved! After cooking, you can rate the meals in the History section.\n\nWould you like to start planning another week?"
        
    except Exception as e:
        bot_response = f"✅ Meal plan accepted, but I had trouble saving it: {str(e)}\n\nWould you like to start planning another week?"
    
    return templates.TemplateResponse("partials/message.html", {
        "request": request,
        "message": markdown.markdown(bot_response),
        "is_bot": True,
        "session_id": session_id
    })


@app.post("/chat/refine-plan")
async def refine_plan(request: Request, session_id: str = Form(...), feedback: str = Form(...)):
    """Refine the meal plan based on user feedback."""
    
    if session_id not in chat_sessions:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "Session expired."
        })
    
    session = chat_sessions[session_id]
    original_plan = session.get("meal_plan", "")
    
    if not original_plan:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": "No meal plan found to refine."
        })
    
    # Load offers for context
    try:
        offers = load_offers_from_db()
        offers_text = format_offers_for_claude(offers)
        
        # Call Claude to refine
        refined_plan = claude.refine_meal_plan(original_plan, feedback, offers_text)
        
        # Convert to HTML
        refined_plan_html = markdown.markdown(refined_plan, extensions=['tables', 'fenced_code'])
        
        # Update session
        session["meal_plan"] = refined_plan
        
        # Return the refined plan
        return templates.TemplateResponse("partials/meal_plan.html", {
            "request": request,
            "meal_plan": refined_plan_html,
            "session_id": session_id
        })
    
    except Exception as e:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": f"Error refining plan: {str(e)}"
        })


@app.get("/rate-meals")
async def rate_meals_page(request: Request):
    """Show page to rate unrated meals."""
    # Get unrated meals from database
    unrated = db.get_unrated_meals()
    
    return templates.TemplateResponse("rate_meals_simple.html", {
        "request": request,
        "unrated_meals": unrated
    })


@app.post("/rate-meals/submit")
async def submit_ratings(request: Request):
    """Submit meal ratings."""
    from fastapi.responses import RedirectResponse
    
    form_data = await request.form()
    meal_count = int(form_data.get('meal_count', 0))
    
    print(f"\n=== RATING SUBMISSION DEBUG ===")
    print(f"Total meals in form: {meal_count}")
    
    rated_count = 0
    skipped_count = 0
    
    for i in range(1, meal_count + 1):
        meal_id = form_data.get(f'meal_id_{i}')
        rating = form_data.get(f'rating_{i}')
        comments = form_data.get(f'comments_{i}', '').strip()
        would_repeat = form_data.get(f'would_repeat_{i}') == 'true'
        
        # Skip if no rating selected
        if not rating or rating == '':
            print(f"\nMeal {i}: Skipped (no rating)")
            skipped_count += 1
            continue
        
        print(f"\nMeal {i}:")
        print(f"  ID: {meal_id}")
        print(f"  Rating: {rating}")
        print(f"  Comments: {comments}")
        print(f"  Would repeat: {would_repeat}")
        
        if meal_id:
            try:
                db.rate_meal(
                    meal_id=int(meal_id),
                    rating=int(rating),
                    comments=comments if comments else None,
                    would_repeat=would_repeat
                )
                rated_count += 1
                print(f"  ✅ Saved successfully")
            except Exception as e:
                print(f"  ❌ Error: {e}")
                import traceback
                traceback.print_exc()
    
    print(f"\n=== RESULTS ===")
    print(f"Rated: {rated_count}")
    print(f"Skipped: {skipped_count}")
    print("================\n")
    
    # Redirect to history page
    return RedirectResponse(url="/history", status_code=303)


@app.get("/offers")
async def offers_page(request: Request):
    """Show offers browsing page."""
    try:
        offers = load_offers_from_db()
        
        # Calculate stats
        total_offers = len(offers)
        avg_savings = sum(o.get('savings_percent', 0) for o in offers) / total_offers if total_offers > 0 else 0
        
        # Group by department
        departments = {}
        for offer in offers:
            dept = offer.get('department', 'Other')
            departments[dept] = departments.get(dept, 0) + 1
        
        return templates.TemplateResponse("offers.html", {
            "request": request,
            "offers": offers,
            "total_offers": total_offers,
            "avg_savings": round(avg_savings, 1),
            "departments": departments
        })
    except FileNotFoundError:
        return templates.TemplateResponse("offers.html", {
            "request": request,
            "offers": [],
            "total_offers": 0,
            "avg_savings": 0,
            "departments": {},
            "error": "No offers found. Please run the scraper first."
        })

"""
Add this endpoint to your main.py

This handles filtering offers by department, search, and sorting
"""

"""
REPLACE the filter_offers endpoint in main.py (around line 800-850)
with this updated version that includes quantity inputs
"""

@app.get("/offers/filter", response_class=HTMLResponse)
async def filter_offers(
    request: Request,
    search: str = "",
    department: str = "",
    sort: str = "savings"
):
    """Filter and sort offers based on user selections."""
    db = Database()
    
    # Get all offers from database
    conn = db._get_connection()
    cursor = conn.cursor()
    
    # Build query based on filters
    query = """
        SELECT 
            product_id, name, underline, price, price_numeric,
            normal_price, savings_percent, price_per_unit,
            department, category
        FROM offers
        WHERE 1=1
    """
    params = []
    
    # Add search filter
    if search:
        query += " AND (name LIKE ? OR underline LIKE ?)"
        search_pattern = f"%{search}%"
        params.extend([search_pattern, search_pattern])
    
    # Add department filter
    if department:
        query += " AND department = ?"
        params.append(department)
    
    # Add sorting
    if sort == "savings":
        query += " ORDER BY savings_percent DESC"
    elif sort == "price_asc":
        query += " ORDER BY price_numeric ASC"
    elif sort == "price_desc":
        query += " ORDER BY price_numeric DESC"
    elif sort == "name":
        query += " ORDER BY name ASC"
    else:
        query += " ORDER BY savings_percent DESC"
    
    cursor.execute(query, params)
    offers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Return just the offers list HTML (for htmx to swap in)
    html_parts = []
    
    for offer in offers:
        html_parts.append(f'''
        <div class="bg-white rounded-lg shadow hover:shadow-md transition p-4 border border-gray-200">
            <div class="flex items-center justify-between">
                <div class="flex-1">
                    <h3 class="font-semibold text-gray-900">{offer['name']}</h3>
                    {'<p class="text-sm text-gray-600">' + offer['underline'] + '</p>' if offer.get('underline') else ''}
                    <div class="mt-2 flex items-center gap-4">
                        <span class="text-2xl font-bold text-green-600">{offer['price']}</span>
                        {'<span class="text-sm text-gray-500 line-through">' + offer['normal_price'] + '</span>' if offer.get('normal_price') else ''}
                        {'<span class="text-sm font-semibold text-red-600">Save ' + str(int(offer['savings_percent'])) + '%</span>' if offer.get('savings_percent') else ''}
                    </div>
                    {'<p class="text-xs text-gray-500 mt-1">' + offer['price_per_unit'] + '</p>' if offer.get('price_per_unit') else ''}
                    <div class="mt-1">
                        <span class="inline-block px-2 py-1 text-xs bg-blue-100 text-blue-800 rounded">
                            {offer.get('department', 'Other')}
                        </span>
                    </div>
                </div>
                
                <!-- Dual Checkboxes with Quantity Inputs -->
                <div class="flex flex-col gap-3 ml-4">
                    <!-- Meal Plan Selection -->
                    <div class="flex items-center gap-2">
                        <label class="flex items-center gap-2 cursor-pointer group">
                            <input type="checkbox" 
                                   id="meal_plan_{offer['product_id']}"
                                   name="meal_plan_{offer['product_id']}"
                                   value="{offer['product_id']}"
                                   class="w-5 h-5 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
                                   onchange="updateSelectedCount(); toggleQuantityInput('meal_plan_qty_{offer['product_id']}', this.checked)">
                            <span class="text-sm font-medium text-gray-700 group-hover:text-blue-600 whitespace-nowrap">
                                📋 Meal Plan
                            </span>
                        </label>
                        <input type="number" 
                               id="meal_plan_qty_{offer['product_id']}"
                               name="meal_plan_qty_{offer['product_id']}"
                               min="1"
                               value="1"
                               disabled
                               class="w-16 px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-100 disabled:text-gray-400"
                               placeholder="Qty">
                    </div>
                    
                    <!-- Shopping List Selection -->
                    <div class="flex items-center gap-2">
                        <label class="flex items-center gap-2 cursor-pointer group">
                            <input type="checkbox" 
                                   id="shopping_list_{offer['product_id']}"
                                   name="shopping_list_{offer['product_id']}"
                                   value="{offer['product_id']}"
                                   class="w-5 h-5 text-green-600 rounded border-gray-300 focus:ring-green-500"
                                   onchange="updateSelectedCount(); toggleQuantityInput('shopping_list_qty_{offer['product_id']}', this.checked)">
                            <span class="text-sm font-medium text-gray-700 group-hover:text-green-600 whitespace-nowrap">
                                🛒 Shopping List
                            </span>
                        </label>
                        <input type="number" 
                               id="shopping_list_qty_{offer['product_id']}"
                               name="shopping_list_qty_{offer['product_id']}"
                               min="1"
                               value="1"
                               disabled
                               class="w-16 px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-100 disabled:text-gray-400"
                               placeholder="Qty">
                    </div>
                </div>
            </div>
        </div>
        ''')
    
    if not html_parts:
        return '''
        <div class="text-center py-12 text-gray-500">
            <p class="text-lg font-medium">No offers found</p>
            <p class="text-sm mt-2">Try adjusting your filters</p>
        </div>
        '''
    
    return "\n".join(html_parts)
    

@app.post("/offers/submit-selections", response_class=HTMLResponse)
async def submit_offer_selections(request: Request):
    """
    Handle form submission with dual checkboxes and quantities.
    Processes meal_plan_* and shopping_list_* checkbox selections with quantities.
    """
    form_data = await request.form()
    
    # Parse selections with quantities
    meal_plan_selections = []  # List of (product_id, quantity, offer_name)
    shopping_list_selections = []  # List of (product_id, quantity)
    
    for key, value in form_data.items():
        if key.startswith('meal_plan_') and not key.endswith('_qty') and value:
            product_id = key.replace('meal_plan_', '')
            quantity = form_data.get(f'meal_plan_qty_{product_id}', '1')
            meal_plan_selections.append((product_id, quantity))
        elif key.startswith('shopping_list_') and not key.endswith('_qty') and value:
            product_id = key.replace('shopping_list_', '')
            quantity = form_data.get(f'shopping_list_qty_{product_id}', '1')
            shopping_list_selections.append((product_id, quantity))
    
    # Get offer details from database
    db = Database()
    
    # Process meal plan selections (store in session for use in chat)
    meal_plan_offer_names = []
    if meal_plan_selections:
        selected_offers = []
        for product_id, quantity in meal_plan_selections:
            offer = db.get_offer_by_id(product_id)
            if offer:
                offer_data = {
                    'product_id': product_id,
                    'name': offer['name'],
                    'quantity': quantity,
                    'price': offer.get('price'),
                    'price_numeric': offer.get('price_numeric')
                }
                selected_offers.append(offer_data)
                meal_plan_offer_names.append(f"{quantity}x {offer['name']}")
        
        # Store in session for meal planner to use
        if 'selected_offers' not in request.session:
            request.session['selected_offers'] = []
        
        # Add to existing or create new
        existing = request.session.get('selected_offers', [])
        existing.extend(selected_offers)
        request.session['selected_offers'] = existing
    
    # Process shopping list selections (add to database)
    shopping_list_count = 0
    if shopping_list_selections:
        # Get or create active shopping list
        active_list = db.get_active_shopping_list()
        if not active_list:
            list_id = db.create_shopping_list(f"Shopping List {datetime.now().strftime('%Y-%m-%d')}")
        else:
            list_id = active_list['id']
        
        # Add items to shopping list with quantities
        for product_id, quantity in shopping_list_selections:
            offer = db.get_offer_by_id(product_id)
            if offer:
                # Auto-categorize based on department
                category = categorize_item(offer['name'], offer.get('department'))
                
                db.add_shopping_list_item(
                    list_id=list_id,
                    item_name=offer['name'],
                    quantity=str(quantity),  # Use the selected quantity
                    category=category,
                    source='offer',
                    source_id=product_id,
                    price_estimate=offer['price_numeric']
                )
                shopping_list_count += 1
    
    # Build success message
    messages = []
    if meal_plan_selections:
        total_meal_qty = sum(int(qty) for _, qty in meal_plan_selections)
        messages.append(f"{len(meal_plan_selections)} item(s) ({total_meal_qty} total) saved for meal planning")
        if meal_plan_offer_names:
            messages.append(f"Selected: {', '.join(meal_plan_offer_names[:3])}{'...' if len(meal_plan_offer_names) > 3 else ''}")
    if shopping_list_selections:
        total_shop_qty = sum(int(qty) for _, qty in shopping_list_selections)
        messages.append(f"{shopping_list_count} item(s) ({total_shop_qty} total) added to shopping list")
    
    if not messages:
        return """
        <div class="bg-yellow-100 border border-yellow-400 text-yellow-800 px-4 py-3 rounded-lg">
            <p class="font-medium">No items selected. Please select items to add.</p>
        </div>
        """
    
    # Success message with action buttons
    return f"""
    <div class="bg-green-100 border border-green-400 text-green-800 px-4 py-3 rounded-lg" 
         id="success-message">
        <p class="font-medium mb-3">✅ Success!</p>
        <ul class="list-disc list-inside mb-4 space-y-1">
            {''.join(f'<li>{msg}</li>' for msg in messages)}
        </ul>
        <div class="flex gap-3">
            {f'<a href="/" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 inline-block">Go to Meal Planner</a>' if meal_plan_selections else ''}
            {f'<a href="/shopping-list" class="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 inline-block">View Shopping List</a>' if shopping_list_selections else ''}
            <button onclick="clearForm()" class="bg-gray-600 text-white px-4 py-2 rounded-lg hover:bg-gray-700">Continue Browsing</button>
        </div>
    </div>
    
    <script>
        // Trigger shopping list badge update
        document.body.dispatchEvent(new Event('shopping-list-updated'));
        
        function clearForm() {{
            // Uncheck all checkboxes
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
            // Disable all quantity inputs
            document.querySelectorAll('input[type="number"]').forEach(input => {{
                input.disabled = true;
                input.value = '1';
                input.classList.remove('bg-white', 'text-gray-900');
                input.classList.add('bg-gray-100', 'text-gray-400');
            }});
            // Hide success message
            document.getElementById('success-message').innerHTML = '';
            // Update count
            updateSelectedCount();
        }}
    </script>
    """


@app.get("/shopping-list/badge", response_class=HTMLResponse)
async def get_shopping_list_badge():
    """Get the current shopping list item count for the badge."""
    db = Database()
    active_list = db.get_active_shopping_list()
    
    if not active_list:
        return "0"
    
    items = db.get_shopping_list_items(active_list['id'], include_checked=False)
    count = len(items)
    
    return str(count) if count > 0 else "0"


def categorize_item(item_name: str, department: Optional[str] = None) -> str:
    """
    Auto-categorize items based on name and department.
    """
    CATEGORY_KEYWORDS = {
        'Produce': ['tomato', 'lettuce', 'onion', 'garlic', 'potato', 'carrot', 'pepper', 
                   'cucumber', 'broccoli', 'cauliflower', 'spinach', 'cabbage', 'fruit',
                   'apple', 'banana', 'orange', 'grape', 'berry', 'melon'],
        'Dairy': ['milk', 'cheese', 'yogurt', 'butter', 'cream', 'egg', 'mælk', 'ost', 
                 'yoghurt', 'smør', 'fløde', 'æg'],
        'Meat': ['chicken', 'beef', 'pork', 'fish', 'salmon', 'sausage', 'bacon', 'meat',
                'kylling', 'oksekød', 'svinekød', 'fisk', 'laks', 'pølse', 'bacon', 'kød'],
        'Pantry': ['pasta', 'rice', 'flour', 'sugar', 'oil', 'spice', 'sauce', 'canned',
                  'pasta', 'ris', 'mel', 'sukker', 'olie', 'krydderi', 'sauce', 'dåse'],
        'Bakery': ['bread', 'bun', 'roll', 'tortilla', 'brød', 'bolle', 'rundstykke'],
        'Frozen': ['frozen', 'ice cream', 'frossen', 'is'],
        'Beverages': ['juice', 'soda', 'coffee', 'tea', 'water', 'juice', 'kaffe', 'te', 'vand'],
    }
    
    # Try department first if available
    if department:
        dept_lower = department.lower()
        if 'grønt' in dept_lower or 'frugt' in dept_lower:
            return 'Produce'
        elif 'mejeri' in dept_lower or 'dairy' in dept_lower:
            return 'Dairy'
        elif 'kød' in dept_lower or 'meat' in dept_lower:
            return 'Meat'
        elif 'frost' in dept_lower or 'frozen' in dept_lower:
            return 'Frozen'
    
    # Try keywords
    item_lower = item_name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in item_lower for keyword in keywords):
            return category
    
    return 'Other'


@app.get("/preferences")
async def preferences_page(request: Request):
    """Show preferences management page."""
    preferences = db.load_preferences()
    
    return templates.TemplateResponse("preferences.html", {
        "request": request,
        "preferences": preferences
    })


@app.post("/preferences/reset")
async def reset_preferences(request: Request):
    """Reset preferences to defaults."""
    db.reset_preferences_to_defaults()
    return HTMLResponse("OK")


# Preference chat sessions (separate from meal planning sessions)
pref_chat_sessions = {}


@app.post("/preferences/chat/start")
async def start_pref_chat(request: Request):
    """Start a preference editing chat session."""
    session_id = datetime.now().isoformat()
    
    pref_chat_sessions[session_id] = {
        "state": "editing",
        "messages": [],
        "pending_changes": {}
    }
    
    # Return initial messages (clear existing + bot greeting)
    bot_greeting = """<div class="flex items-start gap-3" data-session-id="{session_id}">
    <div class="flex-shrink-0 w-8 h-8 bg-green-600 rounded-full flex items-center justify-center text-white font-bold">
        🤖
    </div>
    <div class="flex-1 bg-green-50 border border-green-200 rounded-lg p-4 max-w-2xl">
        <div class="prose prose-sm">
            Hi! I can help you update your preferences. What would you like to change?<br><br>
            You can say things like:<br>
            • "Add pasta dishes to favorites"<br>
            • "Remove mushrooms from dislikes"<br>
            • "Change family size to 4"<br>
            • "We now avoid dairy"<br>
            • "Our cooking style is quick and easy"<br><br>
            When you're done, say "save" or "apply changes"!
        </div>
    </div>
</div>""".format(session_id=session_id)
    
    return HTMLResponse(bot_greeting)


@app.post("/preferences/chat/message")
async def pref_chat_message(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...)
):
    """Handle preference chat messages."""
    
    if session_id not in pref_chat_sessions:
        return HTMLResponse("""
        <div class="flex items-start gap-3">
            <div class="flex-shrink-0 w-8 h-8 bg-red-600 rounded-full flex items-center justify-center text-white font-bold">⚠️</div>
            <div class="flex-1 bg-red-50 border border-red-200 rounded-lg p-4 max-w-2xl">
                <p class="text-red-800 font-medium">Session expired. Please refresh the page.</p>
            </div>
        </div>
        """)
    
    session = pref_chat_sessions[session_id]
    current_prefs = db.load_preferences()
    
    # Check if user wants to save
    save_keywords = ['save', 'apply', 'done', 'finish']
    should_save = any(keyword == message.lower().strip() for keyword in save_keywords)
    
    try:
        if should_save and session.get('pending_changes'):
            # Apply pending changes
            db.save_preferences(session['pending_changes'])
            bot_response = "✅ **Preferences saved!**\n\nYour changes have been applied. Refresh the page to see the updates."
        else:
            # Use Claude to understand the preference change
            prompt = f"""You are helping a user update their meal planning preferences. 

Current preferences (YAML format):
```yaml
family:
  size: {current_prefs['family']['size']}
  composition: "{current_prefs['family']['composition']}"
  note: "{current_prefs['family']['note']}"
cooking:
  style: "{current_prefs['cooking']['style']}"
  priorities: {current_prefs['cooking']['priorities']}
  max_cook_time: {current_prefs['cooking']['max_cook_time']}
food:
  favorites: {current_prefs['food']['favorites']}
  dislikes: {current_prefs['food']['dislikes']}
  dietary_restrictions: {current_prefs['food']['dietary_restrictions']}
planning:
  default_dinners: {current_prefs['planning']['default_dinners']}
  variety_rule: "{current_prefs['planning']['variety_rule']}"
  max_budget: {current_prefs['planning']['max_budget']}
```

User says: "{message}"

Your task:
1. Understand what preference they want to change
2. Respond conversationally to confirm what you understood
3. If it's a valid change, update the YAML and return it at the end wrapped in ```yaml ``` tags
4. If you need clarification, ask questions

Examples:
- "Add pasta to favorites" → Add "pasta dishes" to food.favorites list
- "We avoid dairy" → Add "dairy" to food.dietary_restrictions
- "Family size is 4" → Change family.size to 4
- "No pre-fab, DIY pasta sauce ok but not butter chicken" → Update cooking.style to reflect preference for homemade vs store-bought based on complexity

Be conversational and helpful!"""
            
            response = claude.client.messages.create(
                model=claude.model,
                max_tokens=1500,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            bot_response = response.content[0].text
            
            # Check if Claude returned updated YAML
            if '```yaml' in bot_response:
                import re
                yaml_match = re.search(r'```yaml\n(.*?)\n```', bot_response, re.DOTALL)
                if yaml_match:
                    try:
                        import yaml
                        updated_prefs = yaml.safe_load(yaml_match.group(1))
                        session['pending_changes'] = updated_prefs
                        # Remove the YAML from the response shown to user
                        bot_response = re.sub(r'```yaml\n.*?\n```', '', bot_response, flags=re.DOTALL).strip()
                        bot_response += "\n\n✅ Got it! Say **'save'** to apply these changes, or tell me more preferences to update."
                    except Exception as e:
                        print(f"Error parsing YAML: {e}")
        
    except Exception as e:
        print(f"Error processing preference change: {e}")
        import traceback
        traceback.print_exc()
        bot_response = "Sorry, I had trouble with that. Could you try rephrasing?"
    
    # Return both user message and bot response
    safe_message = escape(message)
    safe_bot_response = markdown.markdown(bot_response)
    return HTMLResponse(f"""
    <div class="flex items-start gap-3 justify-end">
        <div class="flex-1 bg-blue-50 border border-blue-200 rounded-lg p-4 max-w-2xl text-right">
            <div class="prose prose-sm">{safe_message}</div>
        </div>
        <div class="flex-shrink-0 w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white font-bold">👤</div>
    </div>

    <div class="flex items-start gap-3" data-session-id="{session_id}">
        <div class="flex-shrink-0 w-8 h-8 bg-green-600 rounded-full flex items-center justify-center text-white font-bold">🤖</div>
        <div class="flex-1 bg-green-50 border border-green-200 rounded-lg p-4 max-w-2xl">
            <div class="prose prose-sm">{safe_bot_response}</div>
        </div>
    </div>
    """)


@app.get("/history")
async def view_history(request: Request):
    """View meal history and ratings."""
    history = db.get_meal_history(limit=20)

    return templates.TemplateResponse("history.html", {
        "request": request,
        "history": history
    })


def build_claude_prompt(offers_text: str, preferences: dict) -> str:
    """Build the prompt for Claude with structured shopping list output."""
    
    # Load overall preferences from preferences manager
    overall_prefs_text = db.format_for_prompt()
    
    # Load meal history for context
    meal_history_text = db.get_meal_history_for_context(weeks_back=4)
    
    prompt_parts = [
        "You are a meal planning assistant. Create a weekly meal plan based on current supermarket offers.",
        "",
        overall_prefs_text,  # Overall preferences (persistent)
        "",
        meal_history_text,  # Meal history (ratings, recent meals, favorites)
        "",
        "# This Week's Parameters",
        f"- Number of dinners: {preferences.get('num_dinners', 7)}",
    ]
    
    # Ad-hoc preferences (session-only)
    if preferences.get('special_prefs'):
        prompt_parts.append(f"- Special preferences THIS WEEK: {preferences['special_prefs']}")
    
    if preferences.get('existing_ingredients'):
        prompt_parts.append(f"- Ingredients at home: {preferences['existing_ingredients']}")
    
    # Selected offers - UPDATED with much stronger emphasis
    if preferences.get('selected_offers'):
        prompt_parts.append("")
        prompt_parts.append("# ⭐ MUST-INCLUDE ITEMS - USER SELECTED FROM OFFERS ⭐")
        prompt_parts.append("=" * 70)
        prompt_parts.append("**CRITICAL REQUIREMENT**: The user has specifically selected these items.")
        prompt_parts.append("**YOU MUST BUILD MEALS THAT USE THESE EXACT ITEMS.**")
        prompt_parts.append("**Do not treat these as optional suggestions - they are mandatory.**")
        prompt_parts.append("")
        prompt_parts.append("Selected items to incorporate:")
        for offer in preferences['selected_offers']:
            qty = offer.get('quantity', '1')
            name = offer.get('name')
            price = offer.get('price', 'N/A')
            prompt_parts.append(f"  • {qty}x {name} (Price: {price})")
        prompt_parts.append("")
        prompt_parts.append("=" * 70)
        prompt_parts.append("Plan your meals around these items as the centerpieces!")
        prompt_parts.append("")
    
    prompt_parts.extend([
        "",
        "# Guidelines",
        "- **HIGHEST PRIORITY**: If user selected specific items above, build meals using those items as main ingredients",
        "- **IMPORTANT**: Avoid meals from 'Recent Meals' list unless specifically requested",
        "- **IMPORTANT**: NEVER suggest meals from 'Meals to Avoid' list",
        "- Consider suggesting 'Family Favorites' if they haven't appeared recently (4+ weeks ago)",
        "- Learn from recent ratings and comments to improve suggestions",
        "- Simple meals (avoid 15+ ingredients, assume basic staples available)",
        "- Fast (under 30-40 min prep)",
        "- Healthy (balanced: protein, carbs, vegetables)",
        "- Cheap (prioritize offers, fill gaps with affordable staples)",
        "- Ingredient reuse (efficiently use ingredients across multiple days)",
        "- Kid-friendly (4-year-old eats same food, avoid overly spicy)",
        "",
        "# Available Offers",
        offers_text,
        "",
        "# Output Format",
        "",
        "Provide your response in this exact structure:",
        "",
        "## Meal Plan",
        "List each day with:",
        "- Day number",
        "- Meal name",
        "- Brief description",
        "- Key ingredients (highlight which selected items are used)",
        "",
        "## Shopping List",
        "**CRITICAL**: Format the shopping list EXACTLY as shown below, organized by category.",
        "Use these specific categories: Produce, Dairy, Meat & Fish, Pantry, Bakery, Frozen, Beverages, Other",
        "",
        "Format each item as: `- [Quantity] [Item Name] ([Price estimate if from offers])`",
        "",
        "Example format:",
        "```",
        "### Produce",
        "- 1 kg Tomatoes (19,95 kr)",
        "- 2 Onions",
        "- 1 head Lettuce (12,95 kr)",
        "",
        "### Dairy",
        "- 2L Milk (14,95 kr)",
        "- 500g Yogurt",
        "",
        "### Meat & Fish",
        "- 800g Chicken breast (59,95 kr)",
        "- 500g Ground beef (45,00 kr)",
        "```",
        "",
        "## Ingredient Reuse Notes",
        "Explain how ingredients are reused across multiple days",
        "",
        "## Estimated Total Cost",
        "Provide rough estimate",
        "",
        "**IMPORTANT**: The shopping list MUST be properly formatted with category headers (###) and items in the format shown above. This is critical for the system to parse it correctly."
    ])
    
    return "\n".join(prompt_parts)

"""
CORRECTED /shopping-list endpoint for main.py
Replace your current shopping_list_page function with this one
"""

@app.get("/shopping-list", response_class=HTMLResponse)
async def shopping_list_page(request: Request):
    """Display the shopping list page."""
    db = Database()
    
    # Get or create active shopping list
    shopping_list = db.get_active_shopping_list()
    if not shopping_list:
        list_id = db.create_shopping_list(f"Shopping List {datetime.now().strftime('%Y-%m-%d')}")
        shopping_list = db.get_active_shopping_list()
    
    # Get items
    items = db.get_shopping_list_items(shopping_list['id'])
    
    # Get stats
    stats = db.get_shopping_list_stats(shopping_list['id'])
    
    # Calculate progress
    total = stats['total_items']
    checked = stats['checked_items']
    progress_percent = (checked / total * 100) if total > 0 else 0
    
    return templates.TemplateResponse("shopping_list.html", {
        "request": request,
        "shopping_list": shopping_list,
        "items": items,
        "stats": stats,
        "progress_percent": progress_percent
    })

@app.get("/shopping-list/count")
async def get_shopping_list_count(request: Request):
    """Get count of items in active shopping list (for navigation badge)."""
    try:
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return {"count": 0}
        
        stats = db.get_shopping_list_stats(active_list['id'])
        
        # Return unchecked items count (items still to buy)
        return {"count": stats['unchecked_items']}
    
    except Exception as e:
        print(f"Error getting shopping list count: {e}")
        return {"count": 0}

@app.post("/shopping-list/add-item")
async def add_shopping_list_item_endpoint(
    request: Request,
    item_name: str = Form(...),
    quantity: str = Form(None),
    category: str = Form(None)
):
    """Add a manual item to the shopping list."""
    try:
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return HTMLResponse("Error: No active shopping list", status_code=400)
        
        # Add the item
        db.add_shopping_list_item(
            list_id=active_list['id'],
            item_name=item_name,
            quantity=quantity,
            category=category,
            source='manual'
        )
        
        # Return updated list section (htmx will swap this in)
        items_by_category = db.get_shopping_list_by_category(active_list['id'])
        stats = db.get_shopping_list_stats(active_list['id'])
        
        return templates.TemplateResponse("partials/shopping_list_items.html", {
            "request": request,
            "items_by_category": items_by_category,
            "stats": stats
        })
    
    except Exception as e:
        print(f"Error adding item: {e}")
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.post("/shopping-list/toggle-item/{item_id}")
async def toggle_shopping_list_item_endpoint(request: Request, item_id: int):
    """Toggle checked status of an item."""
    try:
        new_status = db.toggle_shopping_list_item(item_id)
        
        # Return updated stats
        active_list = db.get_active_shopping_list()
        stats = db.get_shopping_list_stats(active_list['id'])
        
        return templates.TemplateResponse("partials/shopping_list_stats.html", {
            "request": request,
            "stats": stats
        })
    
    except Exception as e:
        print(f"Error toggling item: {e}")
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.delete("/shopping-list/item/{item_id}")
async def remove_shopping_list_item_endpoint(request: Request, item_id: int):
    """Remove an item from the shopping list."""
    try:
        db.remove_shopping_list_item(item_id)
        
        # Return updated list
        active_list = db.get_active_shopping_list()
        items_by_category = db.get_shopping_list_by_category(active_list['id'])
        stats = db.get_shopping_list_stats(active_list['id'])
        
        return templates.TemplateResponse("partials/shopping_list_items.html", {
            "request": request,
            "items_by_category": items_by_category,
            "stats": stats
        })
    
    except Exception as e:
        print(f"Error removing item: {e}")
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.post("/shopping-list/clear")
async def clear_shopping_list_endpoint(
    request: Request,
    clear_type: str = Form("all")  # "all" or "checked"
):
    """Clear shopping list items."""
    try:
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return HTMLResponse("Error: No active shopping list", status_code=400)
        
        # Clear based on type
        if clear_type == "checked":
            db.clear_shopping_list(active_list['id'], clear_checked_only=True)
        else:
            db.clear_shopping_list(active_list['id'])
        
        # Return updated list
        items_by_category = db.get_shopping_list_by_category(active_list['id'])
        stats = db.get_shopping_list_stats(active_list['id'])
        
        return templates.TemplateResponse("partials/shopping_list_items.html", {
            "request": request,
            "items_by_category": items_by_category,
            "stats": stats
        })
    
    except Exception as e:
        print(f"Error clearing list: {e}")
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.post("/shopping-list/add-from-offers")
async def add_from_offers_endpoint(request: Request):
    """Add selected offers to shopping list."""
    try:
        form_data = await request.form()
        selected_offers_json = form_data.get('selected_offers', '[]')
        
        import json
        selected_offers = json.loads(selected_offers_json)
        
        if not selected_offers:
            return HTMLResponse("No offers selected", status_code=400)
        
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return HTMLResponse("Error: No active shopping list", status_code=400)
        
        # Add offers to shopping list
        added_count = 0
        for offer in selected_offers:
            # Parse offer data
            db.add_shopping_list_item(
                list_id=active_list['id'],
                item_name=offer.get('name'),
                quantity="1",  # Default quantity
                category=offer.get('category'),
                source='offer',
                source_id=offer.get('product_id'),
                price_estimate=offer.get('price_numeric')
            )
            added_count += 1
        
        # Return success message
        return HTMLResponse(
            f"""<div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
                ✅ Added {added_count} items to your shopping list!
                <a href="/shopping-list" class="underline ml-2">View Shopping List</a>
            </div>""",
            status_code=200
        )
    
    except Exception as e:
        print(f"Error adding from offers: {e}")
        import traceback
        traceback.print_exc()
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.post("/shopping-list/add-from-meal-plan")
async def add_from_meal_plan_endpoint(
    request: Request,
    session_id: str = Form(...),
    meal_plan: str = Form(...)
):
    """Parse meal plan and add shopping list items."""
    try:
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return HTMLResponse("Error: No active shopping list", status_code=400)
        
        # Parse shopping list from meal plan
        parser = ShoppingListParser()
        items = parser.parse_shopping_list(meal_plan)
        
        if not items:
            return HTMLResponse(
                """<div class="bg-yellow-100 border border-yellow-400 text-yellow-700 px-4 py-3 rounded">
                    ⚠️ Could not extract shopping list from meal plan.
                </div>""",
                status_code=200
            )
        
        # Add items in bulk
        added_count = db.add_shopping_list_items_bulk(active_list['id'], items)
        
        # Return success message
        return HTMLResponse(
            f"""<div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
                ✅ Added {added_count} items to your shopping list!
                <a href="/shopping-list" class="underline ml-2">View Shopping List</a>
            </div>""",
            status_code=200
        )
    
    except Exception as e:
        print(f"Error adding from meal plan: {e}")
        import traceback
        traceback.print_exc()
        return HTMLResponse(f"Error: {str(e)}", status_code=500)


@app.get("/shopping-list/export-pdf")
async def export_shopping_list_pdf(request: Request):
    """Export shopping list as PDF."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        from datetime import datetime
        
        active_list = db.get_active_shopping_list()
        
        if not active_list:
            return HTMLResponse("Error: No active shopping list", status_code=400)
        
        # Get items by category
        items_by_category = db.get_shopping_list_by_category(
            active_list['id'], 
            include_checked=False  # Only unchecked items
        )
        
        if not items_by_category:
            return HTMLResponse("Shopping list is empty", status_code=400)
        
        # Create PDF
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        # Title
        p.setFont("Helvetica-Bold", 20)
        p.drawString(30*mm, height - 30*mm, "Shopping List")
        
        # Date
        p.setFont("Helvetica", 10)
        p.drawString(30*mm, height - 40*mm, f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        y = height - 55*mm
        
        # Category order
        category_order = ['Produce', 'Dairy', 'Meat & Fish', 'Pantry', 'Bakery', 'Frozen', 'Beverages', 'Other']
        
        for category in category_order:
            if category not in items_by_category:
                continue
            
            items = items_by_category[category]
            
            # Category header
            p.setFont("Helvetica-Bold", 14)
            p.drawString(30*mm, y, category)
            y -= 7*mm
            
            # Items
            p.setFont("Helvetica", 11)
            for item in items:
                checkbox = "☐"
                quantity = item['quantity'] if item['quantity'] else ""
                price = f" ({item['price_estimate']:.2f} kr)" if item['price_estimate'] else ""
                
                text = f"{checkbox}  {quantity} {item['item_name']}{price}"
                p.drawString(35*mm, y, text)
                y -= 6*mm
                
                # New page if needed
                if y < 30*mm:
                    p.showPage()
                    y = height - 30*mm
                    p.setFont("Helvetica", 11)
            
            # Extra space after category
            y -= 3*mm
        
        # Total estimate
        stats = db.get_shopping_list_stats(active_list['id'])
        if stats['total_estimate'] > 0:
            y -= 5*mm
            p.setFont("Helvetica-Bold", 12)
            p.drawString(30*mm, y, f"Estimated Total: {stats['total_estimate']:.2f} kr")
        
        p.save()
        buffer.seek(0)
        
        # Return PDF
        return Response(
            content=buffer.getvalue(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=shopping_list_{datetime.now().strftime('%Y%m%d')}.pdf"
            }
        )
    
    except ImportError:
        return HTMLResponse(
            "Error: reportlab not installed. Run: pip install reportlab",
            status_code=500
        )
    except Exception as e:
        print(f"Error exporting PDF: {e}")
        import traceback
        traceback.print_exc()
        return HTMLResponse(f"Error: {str(e)}", status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)