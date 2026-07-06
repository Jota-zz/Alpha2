"""Dependencias de FastAPI (inyección de colaboradores del bot).

El `MessageProcessor` y demás singletons se construyen en el arranque de la app
(ver `app.main`) y se guardan en `app.state`. Estas dependencias los exponen a
los routers de forma desacoplada.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

from app.core.config import Settings, get_settings

if TYPE_CHECKING:  # evita import en runtime (y posibles ciclos)
    from app.bot.handlers import MessageProcessor


def settings_dependency() -> Settings:
    """Devuelve la configuración cacheada."""
    return get_settings()


def get_message_processor(request: Request) -> "MessageProcessor":
    """Recupera el `MessageProcessor` inicializado en el arranque.

    Lanza 503 si el bot aún no terminó de inicializarse.
    """
    processor = getattr(request.app.state, "processor", None)
    if processor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot no inicializado",
        )
    return processor


def get_dashboard_service(request: Request):
    """Recupera el `DashboardService` inicializado en el arranque."""
    service = getattr(request.app.state, "dashboard", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard no inicializado",
        )
    return service
