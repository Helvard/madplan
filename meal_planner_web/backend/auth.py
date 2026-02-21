"""
Authentication helpers for Meal Planner.
Uses Supabase Auth (email/password). JWT tokens are validated server-side.
"""

import os
import jwt
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import RedirectResponse

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")


def get_current_user(request: Request) -> dict | None:
    """
    Extract and validate the Supabase JWT from the session cookie.
    Returns user dict {id, email, household_id} or None if not authenticated.
    """
    token = request.session.get("access_token")
    if not token:
        return None

    if not SUPABASE_JWT_SECRET:
        # Fallback: trust the session without re-validating the JWT.
        # Safe because Starlette signs the session cookie with SESSION_SECRET.
        user = request.session.get("user")
        return user if user else None

    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return {
            "id": payload["sub"],
            "email": payload.get("email", ""),
            "household_id": request.session.get("household_id"),
        }
    except jwt.ExpiredSignatureError:
        request.session.clear()
        return None
    except Exception:
        return None


def require_user(request: Request) -> dict | None:
    """
    Return current user or redirect to /login.
    Use as a FastAPI dependency: user = require_user(request)
    But since we need redirect behaviour, call inline at the top of each route.
    """
    return get_current_user(request)


def login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)
