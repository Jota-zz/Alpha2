"""Rutas del webhook de WhatsApp (migrado de `WebhookServer`, celda 9 parte 2).

Adaptado de Flask a FastAPI:
- GET  /webhook  -> verificación del challenge de Meta (hub.mode/verify_token).
- POST /webhook  -> entrega el payload crudo a `MessageProcessor.process_incoming`.

El gate horario se aplica DENTRO de `process_incoming`; aquí devolvemos 200 en
éxito para que Meta no reintente. La exposición pública (ngrok/túnel) es parte
del arranque, no de las rutas.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import get_message_processor, settings_dependency
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["webhook"])


@router.get("/webhook")
async def verify_webhook(
    request: Request, settings: Settings = Depends(settings_dependency)
):
    """Verificación del webhook (handshake de Meta)."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.webhook_verify_token:
        logger.info("✅ Webhook verificado")
        return PlainTextResponse(challenge or "", status_code=200)
    logger.warning("Intento de verificación con token inválido")
    return PlainTextResponse("Token inválido", status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request, processor=Depends(get_message_processor)):
    """Recepción de eventos de WhatsApp."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        processor.process_incoming(data or {})
        return {"status": "ok"}
    except Exception as e:
        logger.error("Error webhook: %s", e)
        return JSONResponse({"error": "internal"}, status_code=500)
