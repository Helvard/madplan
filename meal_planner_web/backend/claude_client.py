"""
Claude API client for meal planning
"""

import os
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