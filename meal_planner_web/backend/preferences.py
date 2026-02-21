"""
Preferences management for meal planner
Handles loading and saving user preferences
"""

import yaml
from pathlib import Path
from typing import Dict, Optional

PREFERENCES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "preferences.yaml"

# Default preferences
DEFAULT_PREFERENCES = {
    "family": {
        "size": 5,
        "composition": "2 adults, 3 kids (aged 17, 12, 4)",
        "note": "Big eaters!"
    },
    "cooking": {
        "style": "Simple food with fewer ingredients",
        "priorities": ["Fast", "Healthy", "Cheap"],
        "max_cook_time": 30
    },
    "food": {
        "favorites": [],
        "dislikes": [],
        "dietary_restrictions": []
    },
    "planning": {
        "default_dinners": 7,
        "variety_rule": "No protein repeated 2 days in a row",
        "max_budget": None
    }
}


class PreferencesManager:
    """Manage user preferences for meal planning."""
    
    def __init__(self):
        """Initialize preferences manager."""
        self.preferences_file = PREFERENCES_FILE
        self.preferences_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load preferences or create default
        if not self.preferences_file.exists():
            self.save_preferences(DEFAULT_PREFERENCES)
    
    def load_preferences(self) -> Dict:
        """Load preferences from YAML file."""
        try:
            with open(self.preferences_file, 'r', encoding='utf-8') as f:
                prefs = yaml.safe_load(f)
                return prefs if prefs else DEFAULT_PREFERENCES
        except Exception as e:
            print(f"Error loading preferences: {e}")
            return DEFAULT_PREFERENCES.copy()
    
    def save_preferences(self, preferences: Dict) -> bool:
        """Save preferences to YAML file."""
        try:
            with open(self.preferences_file, 'w', encoding='utf-8') as f:
                yaml.dump(preferences, f, default_flow_style=False, allow_unicode=True)
            return True
        except Exception as e:
            print(f"Error saving preferences: {e}")
            return False
    
    def get_preference(self, category: str, key: str) -> Optional[any]:
        """Get a specific preference value."""
        prefs = self.load_preferences()
        return prefs.get(category, {}).get(key)
    
    def update_preference(self, category: str, key: str, value: any) -> bool:
        """Update a specific preference."""
        prefs = self.load_preferences()
        if category not in prefs:
            prefs[category] = {}
        prefs[category][key] = value
        return self.save_preferences(prefs)
    
    def format_for_prompt(self) -> str:
        """Format preferences as text for Claude prompt."""
        prefs = self.load_preferences()
        
        lines = [
            "# Family Context",
            f"- Size: {prefs['family']['size']} people ({prefs['family']['composition']})",
            f"- Note: {prefs['family']['note']}",
            f"- Cooking style: {prefs['cooking']['style']}",
            f"- Priorities: {', '.join(prefs['cooking']['priorities'])}",
            f"- Max cook time: {prefs['cooking']['max_cook_time']} minutes",
            ""
        ]
        
        if prefs['food']['dietary_restrictions']:
            lines.append(f"- Dietary restrictions: {', '.join(prefs['food']['dietary_restrictions'])}")
        
        if prefs['food']['favorites']:
            lines.append("")
            lines.append("# Family Favorites")
            for item in prefs['food']['favorites']:
                lines.append(f"- {item}")
        
        if prefs['food']['dislikes']:
            lines.append("")
            lines.append("# Foods to Avoid")
            for item in prefs['food']['dislikes']:
                lines.append(f"- {item}")
        
        lines.append("")
        lines.append("# Meal Planning Rules")
        lines.append(f"- Default dinners per week: {prefs['planning']['default_dinners']}")
        lines.append(f"- Variety: {prefs['planning']['variety_rule']}")
        
        if prefs['planning']['max_budget']:
            lines.append(f"- Budget limit: {prefs['planning']['max_budget']} kr/week")
        
        return "\n".join(lines)
    
    def reset_to_defaults(self) -> bool:
        """Reset preferences to default values."""
        return self.save_preferences(DEFAULT_PREFERENCES.copy())