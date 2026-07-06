"""Endpoint de health check (migrado de la ruta /health de la celda 9)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe simple."""
    return {"status": "healthy"}
