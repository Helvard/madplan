"""
Claude API client for meal planning
"""

import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)


class ClaudeClient:
    """Client for interacting with Claude API."""
    
    def __init__(self):
        """Initialize Claude client with API key from environment."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found.\n"
                "Please create a .env file with: ANTHROPIC_API_KEY=your-key-here"
            )
        
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"
    
    def generate_meal_plan(self, prompt: str, max_tokens: int = 4000) -> str:
        """
        Generate a meal plan using Claude.
        
        Args:
            prompt: The full prompt with context, offers, and instructions
            max_tokens: Maximum tokens in response
        
        Returns:
            The meal plan as markdown text
        """
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        return message.content[0].text
    
    def refine_meal_plan(self, original_plan: str, feedback: str, offers_context: str = None) -> str:
        """
        Refine an existing meal plan based on user feedback.
        
        Args:
            original_plan: The original meal plan
            feedback: User's feedback/requested changes
            offers_context: Optional context about available offers
        
        Returns:
            The refined meal plan
        """
        prompt_parts = [
            "You previously generated this meal plan:",
            "",
            original_plan,
            "",
            "The user has provided this feedback:",
            feedback,
            "",
            "Please revise the meal plan to address their feedback while maintaining the same format and structure.",
        ]
        
        if offers_context:
            prompt_parts.extend([
                "",
                "Available offers for reference:",
                offers_context
            ])
        
        prompt = "\n".join(prompt_parts)
        
        return self.generate_meal_plan(prompt)

    # -------------------------------------------------------------------------
    # Recipe helpers
    # -------------------------------------------------------------------------

    def _parse_json_response(self, text: str) -> dict:
        """Extract the first JSON object from a Claude response."""
        match = re.search(r'\{[\s\S]+\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}

    def generate_recipe_json(self, description: str, preferences: str = None) -> dict:
        """
        Ask Claude to generate a full recipe for a given description.

        Returns a dict with keys:
            name, description, ingredients ([{name, quantity, unit}]),
            instructions, servings, cook_time_minutes, tags
        """
        pref_block = f"\n\nFamily context:\n{preferences}" if preferences else ""
        prompt = (
            f"Generate a complete recipe for: {description}{pref_block}\n\n"
            "Reply with ONLY a JSON object in this exact shape — no markdown, no explanation:\n"
            "{\n"
            '  "name": "Recipe Name",\n'
            '  "description": "One sentence description",\n'
            '  "ingredients": [\n'
            '    {"name": "Pasta", "quantity": "400", "unit": "g"}\n'
            "  ],\n"
            '  "instructions": "1. Boil water...\\n2. ...",\n'
            '  "servings": 4,\n'
            '  "cook_time_minutes": 30,\n'
            '  "tags": ["quick", "pasta", "kids-friendly"]\n'
            "}"
        )
        response = self.generate_meal_plan(prompt, max_tokens=2000)
        return self._parse_json_response(response)

    def extract_recipe_from_url(self, page_text: str, url: str) -> dict:
        """
        Extract a recipe from scraped webpage text.
        Falls back to an "inspired by" generation if the text is too thin.

        Returns same dict shape as generate_recipe_json.
        """
        if len(page_text.strip()) < 200:
            # Page text is too sparse — generate inspired recipe instead
            return self.generate_recipe_json(f"A recipe inspired by: {url}")

        prompt = (
            f"Extract the recipe from the following webpage content (source: {url}).\n\n"
            f"Webpage text:\n{page_text[:6000]}\n\n"
            "If you can clearly identify a recipe, extract it exactly. "
            "If the content is unclear or incomplete, generate a good recipe inspired by the dish described.\n\n"
            "Reply with ONLY a JSON object in this exact shape — no markdown, no explanation:\n"
            "{\n"
            '  "name": "Recipe Name",\n'
            '  "description": "One sentence description",\n'
            '  "ingredients": [\n'
            '    {"name": "Pasta", "quantity": "400", "unit": "g"}\n'
            "  ],\n"
            '  "instructions": "1. Step one...\\n2. Step two...",\n'
            '  "servings": 4,\n'
            '  "cook_time_minutes": 30,\n'
            '  "tags": ["quick", "pasta"]\n'
            "}"
        )
        response = self.generate_meal_plan(prompt, max_tokens=2000)
        result = self._parse_json_response(response)
        if not result:
            result = self.generate_recipe_json(f"A recipe inspired by: {url}")
        if result and not result.get("source_url"):
            result["source_url"] = url
        return result

    def chat_recipe_message(self, messages: list, recipe_context: dict = None) -> str:
        """
        Multi-turn AI conversation for the per-recipe sidebar.

        Args:
            messages: list of {role, content} dicts (conversation history)
            recipe_context: the saved recipe dict for grounding

        Returns:
            Plain text (markdown) response from Claude
        """
        system_parts = [
            "You are a helpful cooking assistant. Answer questions about recipes, "
            "suggest variations, substitutions, and scaling adjustments. "
            "Keep answers concise and practical."
        ]
        if recipe_context:
            ingredients = recipe_context.get("ingredients", [])
            ing_lines = ", ".join(
                f"{i.get('quantity','')} {i.get('unit','')} {i.get('name','')}".strip()
                for i in ingredients
            )
            system_parts.append(
                f"\nCurrent recipe: {recipe_context.get('name', 'Unknown')}\n"
                f"Ingredients: {ing_lines}\n"
                f"Instructions: {recipe_context.get('instructions', '')[:500]}"
            )
        system_prompt = "\n".join(system_parts)

        message = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=system_prompt,
            messages=messages,
        )
        return message.content[0].text