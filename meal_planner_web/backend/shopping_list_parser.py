"""
Shopping List Parser for Phase 3
Parses Claude's structured shopping list output into database-ready format
"""

import re
from typing import List, Dict, Optional


class ShoppingListParser:
    """Parse Claude's meal plan output to extract shopping list items."""
    
    @staticmethod
    def parse_shopping_list(meal_plan_text: str) -> List[Dict]:
        """
        Parse shopping list from Claude's meal plan text.
        
        Expected format:
        ### Category Name
        - [Quantity] [Item Name] ([Price])
        - [Quantity] [Item Name]
        
        Returns:
            List of dicts with keys: item_name, quantity, category, price_estimate
        """
        items = []
        
        # Find the Shopping List section
        shopping_list_match = re.search(
            r'## Shopping List\s*(.*?)(?=##|$)', 
            meal_plan_text, 
            re.DOTALL | re.IGNORECASE
        )
        
        if not shopping_list_match:
            print("Warning: No 'Shopping List' section found in meal plan")
            return items
        
        shopping_list_text = shopping_list_match.group(1)
        
        # Parse categories and items
        current_category = "Other"
        
        # Split by lines
        lines = shopping_list_text.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if not line:
                continue
            
            # Check if this is a category header (### Category or **Category**)
            category_match = re.match(r'###\s*(.+?)$', line)
            if category_match:
                current_category = category_match.group(1).strip()
                continue
            
            # Alternative category format: **Category:**
            alt_category_match = re.match(r'\*\*(.+?)[:*]+', line)
            if alt_category_match:
                current_category = alt_category_match.group(1).strip()
                continue
            
            # Check if this is an item line (starts with -, *, or •)
            item_match = re.match(r'^[-*•]\s*(.+)$', line)
            if item_match:
                item_text = item_match.group(1).strip()
                
                # Parse the item: [Quantity] [Name] ([Price])
                parsed_item = ShoppingListParser._parse_item_line(item_text, current_category)
                if parsed_item:
                    items.append(parsed_item)
        
        return items
    
    @staticmethod
    def _parse_item_line(item_text: str, category: str) -> Optional[Dict]:
        """
        Parse a single item line.
        
        Expected formats:
        - 1 kg Tomatoes (19,95 kr)
        - 2L Milk (14,95 kr)
        - 500g Yogurt
        - 3 Onions
        """
        # Extract price if present (text in parentheses at the end)
        price_match = re.search(r'\(([0-9,.]+)\s*kr\)$', item_text)
        price_estimate = None
        
        if price_match:
            price_str = price_match.group(1).replace(',', '.')
            try:
                price_estimate = float(price_str)
            except ValueError:
                pass
            # Remove price from item text
            item_text = re.sub(r'\s*\([0-9,.]+\s*kr\)$', '', item_text)
        
        item_text = item_text.strip()
        
        if not item_text:
            return None
        
        # Try to split quantity from item name
        # Common patterns: "1 kg Tomatoes", "2L Milk", "500g Beef", "3 Onions"
        quantity_match = re.match(r'^([0-9.,]+\s*(?:kg|g|l|ml|stk|pcs?|piece|pieces)?)\s+(.+)$', item_text, re.IGNORECASE)
        
        if quantity_match:
            quantity = quantity_match.group(1).strip()
            item_name = quantity_match.group(2).strip()
        else:
            # No clear quantity, treat entire text as item name
            quantity = None
            item_name = item_text
        
        return {
            'item_name': item_name,
            'quantity': quantity,
            'category': category,
            'price_estimate': price_estimate,
            'source': 'meal_plan'
        }
    
    @staticmethod
    def extract_category_from_department(department: str) -> str:
        """
        Map store department to shopping list category.
        Useful for items from offers page.
        """
        department_lower = department.lower() if department else ''
        
        mapping = {
            'frugt': 'Produce',
            'grønt': 'Produce',
            'fruit': 'Produce',
            'vegetables': 'Produce',
            'produce': 'Produce',
            
            'mejeriprodukter': 'Dairy',
            'dairy': 'Dairy',
            'mejeri': 'Dairy',
            
            'kød': 'Meat & Fish',
            'meat': 'Meat & Fish',
            'fisk': 'Meat & Fish',
            'fish': 'Meat & Fish',
            
            'kolonial': 'Pantry',
            'pantry': 'Pantry',
            'grocery': 'Pantry',
            
            'bageri': 'Bakery',
            'bakery': 'Bakery',
            'bread': 'Bakery',
            
            'frost': 'Frozen',
            'frozen': 'Frozen',
            
            'drikkevarer': 'Beverages',
            'beverages': 'Beverages',
            'drinks': 'Beverages'
        }
        
        for key, category in mapping.items():
            if key in department_lower:
                return category
        
        return 'Other'


# Test function for development
def test_parser():
    """Test the shopping list parser."""
    sample_text = """
    ## Meal Plan
    Day 1: Spaghetti Bolognese
    
    ## Shopping List
    
    ### Produce
    - 1 kg Tomatoes (19,95 kr)
    - 2 Onions
    - 3 cloves Garlic
    
    ### Dairy
    - 2L Milk (14,95 kr)
    - 500g Yogurt
    
    ### Meat & Fish
    - 800g Ground beef (65,00 kr)
    
    ### Pantry
    - 500g Spaghetti (12,50 kr)
    - 1 can Tomato sauce
    
    ## Ingredient Reuse
    The ground beef will be used for both Day 1 and Day 3.
    """
    
    parser = ShoppingListParser()
    items = parser.parse_shopping_list(sample_text)
    
    print(f"Parsed {len(items)} items:")
    for item in items:
        print(f"  - {item}")


if __name__ == "__main__":
    test_parser()