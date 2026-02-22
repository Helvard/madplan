"""
Database module for meal planner
Handles all SQLite operations including shopping lists (Phase 3)
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "meal_planner_unified.db"


class Database:
    """Database handler for meal planner."""
    
    def __init__(self):
        """Initialize database connection and create tables if needed."""
        self.db_path = DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_tables(self):
        """Create tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Meal history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_date TEXT NOT NULL,
                day_number INTEGER,
                meal_name TEXT NOT NULL,
                ingredients TEXT,
                cost_estimate REAL,
                rating INTEGER,
                comments TEXT,
                would_repeat BOOLEAN,
                date_rated TEXT
            )
        """)
        
        # User preferences table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # Chat sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                session_date TEXT NOT NULL,
                messages TEXT,
                final_plan TEXT,
                preferences TEXT
            )
        """)
        
        # Shopping lists table (Phase 3)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopping_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_date TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                status TEXT DEFAULT 'active'
            )
        """)
        
        # Shopping list items table (Phase 3)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopping_list_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity TEXT,
                category TEXT,
                checked BOOLEAN DEFAULT 0,
                source TEXT,
                source_id TEXT,
                price_estimate REAL,
                added_date TEXT NOT NULL,
                FOREIGN KEY (list_id) REFERENCES shopping_lists(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes for shopping list performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopping_list_items_list_id 
            ON shopping_list_items(list_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopping_list_items_category 
            ON shopping_list_items(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopping_lists_active 
            ON shopping_lists(is_active)
        """)
        
        # Ensure there's an active shopping list
        cursor.execute("SELECT COUNT(*) FROM shopping_lists WHERE is_active = 1")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO shopping_lists (name, created_date, is_active, status)
                VALUES (?, ?, 1, 'active')
            """, ("My Shopping List", datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
    
    # ========== MEAL HISTORY METHODS ==========
    
    def save_meal_plan(self, plan_date: str, meals: List[Dict]):
        """Save a complete meal plan to history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        for day_num, meal in enumerate(meals, 1):
            cursor.execute("""
                INSERT INTO meal_history (
                    plan_date, day_number, meal_name, ingredients, cost_estimate
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                plan_date,
                day_num,
                meal.get('name'),
                json.dumps(meal.get('ingredients', [])),
                meal.get('cost')
            ))
        
        conn.commit()
        conn.close()
    
    def get_meal_history(self, limit: int = 30) -> List[Dict]:
        """Get recent meal history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                plan_date,
                day_number,
                meal_name,
                ingredients,
                cost_estimate,
                rating,
                comments,
                would_repeat
            FROM meal_history
            ORDER BY plan_date DESC, day_number ASC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def rate_meal(self, meal_id: int, rating: int, comments: str = None, would_repeat: bool = True):
        """Rate a meal from history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE meal_history
            SET rating = ?,
                comments = ?,
                would_repeat = ?,
                date_rated = ?
            WHERE id = ?
        """, (rating, comments, would_repeat, datetime.now().isoformat(), meal_id))
        
        conn.commit()
        conn.close()
    
    def get_meal_history_for_context(self, weeks_back: int = 4) -> str:
        """Get meal history formatted for Claude's context."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get meals from last X weeks
        cursor.execute("""
            SELECT 
                meal_name,
                plan_date,
                rating,
                comments,
                would_repeat
            FROM meal_history
            WHERE plan_date >= date('now', '-' || ? || ' days')
            AND rating IS NOT NULL
            ORDER BY plan_date DESC, rating DESC
        """, (weeks_back * 7,))
        
        rated_meals = cursor.fetchall()
        
        # Get all unique meal names from last X weeks (to avoid repetition)
        cursor.execute("""
            SELECT DISTINCT meal_name
            FROM meal_history
            WHERE plan_date >= date('now', '-' || ? || ' days')
        """, (weeks_back * 7,))
        
        recent_meal_names = [row[0] for row in cursor.fetchall()]
        
        # Get top-rated meals (ever)
        cursor.execute("""
            SELECT 
                meal_name,
                AVG(rating) as avg_rating,
                COUNT(*) as times_made,
                MAX(plan_date) as last_made,
                MAX(comments) as last_comment
            FROM meal_history
            WHERE rating >= 4 AND would_repeat = 1
            GROUP BY meal_name
            HAVING COUNT(*) >= 1
            ORDER BY avg_rating DESC, times_made DESC
            LIMIT 10
        """)
        
        top_rated = cursor.fetchall()
        
        # Get low-rated meals to avoid
        cursor.execute("""
            SELECT DISTINCT
                meal_name,
                AVG(rating) as avg_rating,
                MAX(comments) as comment
            FROM meal_history
            WHERE rating <= 2
            GROUP BY meal_name
        """)
        
        low_rated = cursor.fetchall()
        
        conn.close()
        
        # Format for Claude
        context_parts = []
        
        if recent_meal_names:
            context_parts.append("# Recent Meals (Last " + str(weeks_back) + " Weeks)")
            context_parts.append("**Avoid repeating these:**")
            for meal in recent_meal_names[:15]:
                context_parts.append(f"- {meal}")
            context_parts.append("")
        
        if top_rated:
            context_parts.append("# Family Favorites (Highly Rated)")
            context_parts.append("**Consider suggesting these again (if not too recent):**")
            for row in top_rated:
                meal_name, avg_rating, times_made, last_made, comment = row
                stars = "⭐" * int(avg_rating)
                context_parts.append(f"- {meal_name} ({stars}, made {times_made}x, last: {last_made})")
                if comment:
                    context_parts.append(f"  Note: {comment}")
            context_parts.append("")
        
        if low_rated:
            context_parts.append("# Meals to Avoid (Low Rated)")
            context_parts.append("**Do NOT suggest these:**")
            for row in low_rated:
                meal_name, avg_rating, comment = row
                context_parts.append(f"- {meal_name} (⭐{avg_rating:.1f})")
                if comment:
                    context_parts.append(f"  Reason: {comment}")
            context_parts.append("")
        
        if rated_meals:
            context_parts.append("# Recent Ratings & Feedback")
            context_parts.append("**Learn from these comments:**")
            for row in rated_meals[:10]:
                meal_name, plan_date, rating, comments, would_repeat = row
                stars = "⭐" * rating
                repeat = "✅" if would_repeat else "❌"
                context_parts.append(f"- {meal_name} ({stars} {repeat}) - {plan_date}")
                if comments:
                    context_parts.append(f"  \"{comments}\"")
            context_parts.append("")
        
        if not context_parts:
            return "# Meal History\nNo rated meals yet. This is the first meal plan!\n"
        
        return "\n".join(context_parts)
    
    def get_recent_meals(self, weeks: int = 4) -> List[str]:
        """Get list of recent meal names to avoid repetition."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT meal_name
            FROM meal_history
            WHERE plan_date >= date('now', '-' || ? || ' days')
            ORDER BY plan_date DESC
        """, (weeks * 7,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [row['meal_name'] for row in rows]
    
    def get_highly_rated_meals(self, min_rating: int = 4) -> List[Dict]:
        """Get highly rated meals for suggestions."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                meal_name,
                AVG(rating) as avg_rating,
                COUNT(*) as times_made,
                MAX(plan_date) as last_made
            FROM meal_history
            WHERE rating >= ? AND would_repeat = 1
            GROUP BY meal_name
            ORDER BY avg_rating DESC, times_made DESC
            LIMIT 20
        """, (min_rating,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_unrated_meals(self, limit: int = 50) -> List[Dict]:
        """Get meals that haven't been rated yet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                id,
                plan_date,
                day_number,
                meal_name,
                ingredients,
                cost_estimate
            FROM meal_history
            WHERE rating IS NULL
            ORDER BY plan_date DESC, day_number ASC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    # ========== PREFERENCES METHODS ==========
    
    def get_preferences(self) -> Dict:
        """Get all user preferences."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT key, value FROM preferences")
        rows = cursor.fetchall()
        conn.close()
        
        prefs = {}
        for row in rows:
            try:
                prefs[row['key']] = json.loads(row['value'])
            except json.JSONDecodeError:
                prefs[row['key']] = row['value']
        
        return prefs
    
    def set_preference(self, key: str, value):
        """Set a user preference."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if not isinstance(value, str):
            value = json.dumps(value)
        
        cursor.execute("""
            INSERT OR REPLACE INTO preferences (key, value)
            VALUES (?, ?)
        """, (key, value))
        
        conn.commit()
        conn.close()
    
    # ========== CHAT SESSION METHODS ==========
    
    def save_chat_session(self, session_id: str, messages: List, final_plan: str = None, preferences: Dict = None):
        """Save a chat session to database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO chat_sessions 
            (session_id, session_date, messages, final_plan, preferences)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id,
            datetime.now().isoformat(),
            json.dumps(messages),
            final_plan,
            json.dumps(preferences) if preferences else None
        ))
        
        conn.commit()
        conn.close()
    
    # ========== SHOPPING LIST METHODS (Phase 3) ==========
    
    def get_active_shopping_list(self) -> Optional[Dict]:
        """Get the currently active shopping list."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, created_date, status
            FROM shopping_lists
            WHERE is_active = 1
            LIMIT 1
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def get_shopping_list_items(self, list_id: int, include_checked: bool = True) -> List[Dict]:
        """Get all items from a shopping list, optionally excluding checked items."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                id,
                item_name,
                quantity,
                category,
                checked,
                source,
                price_estimate,
                added_date
            FROM shopping_list_items
            WHERE list_id = ?
        """
        
        if not include_checked:
            query += " AND checked = 0"
        
        query += " ORDER BY category, item_name"
        
        cursor.execute(query, (list_id,))
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_shopping_list_by_category(self, list_id: int, include_checked: bool = True) -> Dict[str, List[Dict]]:
        """Get shopping list items grouped by category."""
        items = self.get_shopping_list_items(list_id, include_checked)
        
        by_category = {}
        for item in items:
            category = item.get('category', 'Other')
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(item)
        
        return by_category
    
    
    def add_shopping_list_items_bulk(self, list_id: int, items: List[Dict]) -> int:
        """Add multiple items to shopping list at once."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        added_count = 0
        for item in items:
            category = item.get('category')
            if not category:
                category = self._auto_categorize_item(item['item_name'])
            
            cursor.execute("""
                INSERT INTO shopping_list_items (
                    list_id, item_name, quantity, category,
                    source, source_id, price_estimate, added_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                list_id,
                item['item_name'],
                item.get('quantity'),
                category,
                item.get('source', 'bulk'),
                item.get('source_id'),
                item.get('price_estimate'),
                datetime.now().isoformat()
            ))
            added_count += 1
        
        conn.commit()
        conn.close()
        
        return added_count
    
    def toggle_shopping_list_item(self, item_id: int) -> bool:
        """Toggle checked status of a shopping list item. Returns new checked status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get current status
        cursor.execute("SELECT checked FROM shopping_list_items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return False
        
        new_status = not row['checked']
        
        cursor.execute("""
            UPDATE shopping_list_items
            SET checked = ?
            WHERE id = ?
        """, (new_status, item_id))
        
        conn.commit()
        conn.close()
        
        return new_status
    
    def remove_shopping_list_items(self, item_id: int):
        """Remove an item from the shopping list."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
        
        conn.commit()
        conn.close()
    
    def clear_shopping_list(self, list_id: int, checked_only: bool = False):
        """Clear items from shopping list. Optionally clear only checked items."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if checked_only:
            cursor.execute("""
                DELETE FROM shopping_list_items 
                WHERE list_id = ? AND checked = 1
            """, (list_id,))
        else:
            cursor.execute("""
                DELETE FROM shopping_list_items 
                WHERE list_id = ?
            """, (list_id,))
        
        conn.commit()
        conn.close()

    def get_shopping_list_stats(self, list_id: int) -> Dict:
        """Get statistics about the shopping list."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_items,
                SUM(CASE WHEN checked = 1 THEN 1 ELSE 0 END) as checked_items,
                SUM(CASE WHEN price_estimate IS NOT NULL THEN price_estimate ELSE 0 END) as total_estimate
            FROM shopping_list_items
            WHERE list_id = ?
        """, (list_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'total_items': row['total_items'] or 0,
                'checked_items': row['checked_items'] or 0,
                'unchecked_items': (row['total_items'] or 0) - (row['checked_items'] or 0),
                'total_estimate': row['total_estimate'] or 0.0
            }
        
        return {
            'total_items': 0,
            'checked_items': 0,
            'unchecked_items': 0,
            'total_estimate': 0.0
        }
    def get_offer_by_id(self, product_id: str) -> Optional[Dict]:
        """Get a single offer by product_id."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                product_id, name, underline, price, price_numeric,
                normal_price, savings_percent, price_per_unit,
                department, category
            FROM offers
            WHERE product_id = ?
        """, (product_id,))
        
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_offers_by_ids(self, product_ids: List[str]) -> List[Dict]:
        """Get multiple offers by their product IDs."""
        if not product_ids:
            return []
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(product_ids))
        cursor.execute(f"""
            SELECT 
                product_id, name, underline, price, price_numeric,
                normal_price, savings_percent, price_per_unit,
                department, category
            FROM offers
            WHERE product_id IN ({placeholders})
        """, product_ids)
        
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return items
    
    def create_shopping_list(self, name: str) -> int:
        """Create a new shopping list and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO shopping_lists (name, created_date, is_active, status)
            VALUES (?, ?, 1, 'active')
        """, (name, datetime.now().isoformat()))
        
        list_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return list_id

    def _auto_categorize_item(self, item_name: str) -> str:
        """Auto-categorize an item based on keywords."""
        item_lower = item_name.lower()
        
        categories = {
            'Produce': ['tomato', 'lettuce', 'onion', 'garlic', 'potato', 'carrot', 
                       'pepper', 'cucumber', 'apple', 'banana', 'orange', 'lemon',
                       'spinach', 'broccoli', 'cauliflower', 'celery', 'mushroom',
                       'fruit', 'vegetable', 'salad', 'avocado'],
            'Dairy': ['milk', 'cheese', 'yogurt', 'butter', 'cream', 'egg', 
                     'mælk', 'ost', 'yoghurt', 'smør', 'fløde', 'æg'],
            'Meat & Fish': ['chicken', 'beef', 'pork', 'fish', 'salmon', 'sausage', 
                           'bacon', 'meat', 'turkey', 'lamb', 'tuna', 'cod',
                           'kylling', 'oksekød', 'svinekød', 'fisk', 'laks'],
            'Pantry': ['pasta', 'rice', 'flour', 'sugar', 'oil', 'spice', 'sauce', 
                      'canned', 'can', 'jar', 'salt', 'pepper', 'vinegar',
                      'ris', 'mel', 'sukker', 'olie', 'salt', 'peber'],
            'Bakery': ['bread', 'bun', 'roll', 'tortilla', 'bagel', 'croissant',
                      'brød', 'bolle', 'rundstykke'],
            'Frozen': ['frozen', 'ice cream', 'fros', 'is'],
            'Beverages': ['juice', 'soda', 'coffee', 'tea', 'water', 'beer', 'wine',
                         'kaffe', 'te', 'vand', 'øl', 'vin']
        }
        
        for category, keywords in categories.items():
            if any(keyword in item_lower for keyword in keywords):
                return category
        
        return 'Other'