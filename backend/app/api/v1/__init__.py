"""Agregación de routers de la versión 1 de la API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, interactions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(interactions.router)

__all__ = ["api_router"]
