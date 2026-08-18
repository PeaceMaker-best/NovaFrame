"""Uvicorn entry point used by ``npm run dev:api``."""

from backend.main import app, create_app

__all__ = ["app", "create_app"]
