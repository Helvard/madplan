#!/usr/bin/env python3
"""
Rema 1000 Meal Planner
Interactive meal planning using current offers and Claude API
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional
import anthropic
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DB_FILE = "rema_offers.db"
OUTPUT_DIR = "meal_plans"

# Family context (constants)
FAMILY_CONTEXT = {
    "size": 5,
    "composition": "2 adults, 3 kids (aged 17, 12, 4)",
    "note": "Big eaters!",
    "dietary_restrictions": None,
    "cooking_style": "Simple food with fewer ingredients",
    "priorities": ["Fast", "Healthy", "Cheap"]
}


def load_offers_from_db() -> List[Dict]:
    """Load current offers from SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            name, 
            underline,
            price, 
            price_numeric,
            normal_price,
            savings_percent,
            price_per_unit,
            department,
            category
        FROM offers
        ORDER BY department, price_numeric
    """)
    
    offers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return offers


def categorize_offers(offers: List[Dict]) -> Dict[str, List[Dict]]:
    """Group offers by department for easier reading."""
    categorized = {}
    
    for offer in offers:
        dept = offer.get('department', 'Other')
        if dept not in categorized:
            categorized[dept] = []
        categorized[dept].append(offer)
    
    return categorized


def format_offers_for_claude(offers: List[Dict]) -> str:
    """Format offers into a readable structure for Claude."""
    categorized = categorize_offers(offers)
    
    output = ["# Current Rema 1000 Offers\n"]
    
    for dept, items in sorted(categorized.items()):
        output.append(f"\n## {dept}")
        output.append(f"({len(items)} items)\n")
        
        for item in items[:20]:  # Limit to top 20 per category to avoid token bloat
            name = item['name']
            underline = f" - {item['underline']}" if item.get('underline') else ""
            price = item['price']
            savings = f" (save {item['savings_percent']:.0f}%)" if item.get('savings_percent') else ""
            
            output.append(f"- {name}{underline}: {price}{savings}")
    
    return "\n".join(output)


def get_user_inputs() -> Dict:
    """Interactive prompts to gather user preferences for this week."""
    print("\n" + "="*60)
    print("MEAL PLANNER - Weekly Setup")
    print("="*60 + "\n")
    
    # Number of dinners
    while True:
        num_dinners = input("How many dinners to plan? [default: 7]: ").strip()
        if not num_dinners:
            num_dinners = 7
            break
        try:
            num_dinners = int(num_dinners)
            if 1 <= num_dinners <= 14:
                break
            print("Please enter a number between 1 and 14")
        except ValueError:
            print("Please enter a valid number")
    
    # Special preferences
    print("\nAny special preferences this week?")
    print("(e.g., 'extra fish', 'no beef', 'more vegetables', 'quick meals only')")
    special_prefs = input("Preferences [press Enter to skip]: ").strip()
    
    # Ingredients at home
    print("\nDo you have any ingredients at home to use up?")
    print("(e.g., 'leftover chicken', 'half bag of pasta', '500g ground beef')")
    existing_ingredients = input("Ingredients [press Enter to skip]: ").strip()
    
    return {
        "num_dinners": num_dinners,
        "special_preferences": special_prefs if special_prefs else None,
        "existing_ingredients": existing_ingredients if existing_ingredients else None
    }


def build_claude_prompt(offers_text: str, user_inputs: Dict) -> str:
    """Build the prompt for Claude API."""
    
    prompt_parts = [
        "You are a meal planning assistant. Create a weekly meal plan for a family based on current supermarket offers.",
        "",
        "# Family Context",
        f"- Size: {FAMILY_CONTEXT['size']} people ({FAMILY_CONTEXT['composition']})",
        f"- Note: {FAMILY_CONTEXT['note']}",
        f"- Cooking style: {FAMILY_CONTEXT['cooking_style']}",
        f"- Priorities: {', '.join(FAMILY_CONTEXT['priorities'])}",
    ]
    
    if FAMILY_CONTEXT['dietary_restrictions']:
        prompt_parts.append(f"- Dietary restrictions: {FAMILY_CONTEXT['dietary_restrictions']}")
    
    prompt_parts.extend([
        "",
        "# This Week's Parameters",
        f"- Number of dinners to plan: {user_inputs['num_dinners']}",
    ])
    
    if user_inputs['special_preferences']:
        prompt_parts.append(f"- Special preferences: {user_inputs['special_preferences']}")
    
    if user_inputs['existing_ingredients']:
        prompt_parts.append(f"- Ingredients already at home: {user_inputs['existing_ingredients']}")
    
    prompt_parts.extend([
        "",
        "# Guidelines",
        "- **Simple meals**: Avoid recipes with 15+ ingredients (assume basic cupboard staples like oil, salt, spices are available)",
        "- **Fast**: Prioritize meals under 30-40 minutes prep time",
        "- **Healthy**: Balanced meals with protein, carbs, and vegetables",
        "- **Cheap**: Prioritize items on offer, but fill gaps with affordable staples as needed",
        "- **Ingredient reuse**: Efficiently reuse ingredients across multiple days (e.g., buy 1kg ground beef for both pasta sauce Day 1 and tacos Day 3)",
        "- **Kid-friendly**: The 4-year-old eats what everyone else is having, so avoid overly spicy or exotic flavors",
        "",
        "# Available Offers",
        offers_text,
        "",
        "# Output Format",
        "Please provide:",
        "",
        "1. **Meal Plan**: List each day with the meal name and key ingredients",
        "2. **Shopping List**: Organized by department/category with:",
        "   - Items from offers (with prices)",
        "   - Additional items needed (with estimated prices if possible)",
        "   - Quantities needed",
        "3. **Ingredient Reuse Notes**: Briefly explain how ingredients are reused across days",
        "4. **Estimated Total Cost**: Rough estimate of total shopping cost",
        "",
        "Format the output in clean Markdown with headers and bullet points.",
    ])
    
    return "\n".join(prompt_parts)


def call_claude_api(prompt: str) -> str:
    """Call Claude API to generate meal plan."""
    
    # Get API key from environment
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not found.\n"
            "Please create a .env file with: ANTHROPIC_API_KEY=your-key-here"
        )
    
    client = anthropic.Anthropic(api_key=api_key)
    
    print("\n🤖 Asking Claude to create your meal plan...")
    print("(This may take 30-60 seconds)\n")
    
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    return message.content[0].text


def save_meal_plan(meal_plan: str, user_inputs: Dict) -> str:
    """Save meal plan to markdown file."""
    
    # Create output directory if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{OUTPUT_DIR}/meal_plan_{timestamp}.md"
    
    # Add metadata header
    header = [
        f"# Meal Plan - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**Family:** {FAMILY_CONTEXT['size']} people ({FAMILY_CONTEXT['composition']})",
        f"**Dinners planned:** {user_inputs['num_dinners']}",
    ]
    
    if user_inputs['special_preferences']:
        header.append(f"**Special preferences:** {user_inputs['special_preferences']}")
    
    if user_inputs['existing_ingredients']:
        header.append(f"**Using from home:** {user_inputs['existing_ingredients']}")
    
    header.extend(["", "---", ""])
    
    # Combine and save
    full_content = "\n".join(header) + meal_plan
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(full_content)
    
    return filename


def main():
    """Main execution function."""
    print("\n" + "="*60)
    print("🍽️  REMA 1000 MEAL PLANNER")
    print("="*60)
    
    # Load offers
    print("\n📊 Loading current offers from database...")
    try:
        offers = load_offers_from_db()
        print(f"   ✅ Loaded {len(offers)} offers from {DB_FILE}")
    except Exception as e:
        print(f"   ❌ Error loading offers: {e}")
        print(f"   Make sure {DB_FILE} exists (run scrape_rema_to_db.py first)")
        return
    
    if not offers:
        print("   ❌ No offers found in database")
        print(f"   Run scrape_rema_to_db.py to fetch current offers")
        return
    
    # Get user inputs
    user_inputs = get_user_inputs()
    
    # Format offers for Claude
    print("\n📝 Preparing offer data for Claude...")
    offers_text = format_offers_for_claude(offers)
    
    # Build prompt
    prompt = build_claude_prompt(offers_text, user_inputs)
    
    # Call Claude API
    try:
        meal_plan = call_claude_api(prompt)
    except ValueError as e:
        print(f"   ❌ {e}")
        return
    except Exception as e:
        print(f"   ❌ Error calling Claude API: {e}")
        return
    
    print("   ✅ Meal plan generated!\n")
    
    # Print to terminal
    print("="*60)
    print(meal_plan)
    print("="*60)
    
    # Save to file
    try:
        filename = save_meal_plan(meal_plan, user_inputs)
        print(f"\n💾 Meal plan saved to: {filename}")
    except Exception as e:
        print(f"\n⚠️  Could not save to file: {e}")
    
    print("\n✅ Done! Happy cooking! 🍳\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {str(e)}")
        import traceback
        traceback.print_exc()