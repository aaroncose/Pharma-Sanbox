"""Agregación de routers de la versión 1 de la API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    audit,
    auth,
    catalog,
    evals,
    failure_lab,
    interactions,
    library,
    review,
    simulation,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(interactions.router)
api_router.include_router(library.router)
api_router.include_router(agent.router)
api_router.include_router(review.router)
api_router.include_router(audit.router)
api_router.include_router(simulation.router)
api_router.include_router(evals.router)
api_router.include_router(failure_lab.router)

__all__ = ["api_router"]
