"""Utilidades de texto y helpers generales (migrado de la celda 8 del notebook).

Contiene funciones puras y sin estado usadas de forma transversal:
- Enmascarado de PII (`mask_phone`), reutilizado por el filtro de logging.
- Validación y saneamiento de entrada del usuario antes de pasarla al LLM.
- Decorador genérico de reintentos con backoff exponencial.
"""
from __future__ import annotations

import logging
import re
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Longitud máxima del mensaje de usuario que se envía al LLM.
MAX_USER_MESSAGE_LENGTH = 1000


def mask_phone(phone: str) -> str:
    """Enmascara un teléfono para logs: +573001234567 -> +57***4567."""
    if not phone:
        return "***"
    phone = str(phone)
    if len(phone) < 6:
        return "***"
    return f"{phone[:3]}***{phone[-4:]}"


def validate_phone_number(phone: str) -> bool:
    """Valida que el teléfono sea válido (solo dígitos, 10-15 caracteres)."""
    if not phone:
        return False
    return phone.isdigit() and 10 <= len(phone) <= 15


def sanitize_user_input(text: str, max_length: int = MAX_USER_MESSAGE_LENGTH) -> str:
    """Sanitiza el texto del usuario antes de pasarlo al LLM.

    - Elimina caracteres de control (preserva saltos de línea y tabs).
    - Colapsa espacios y saltos de línea múltiples.
    - Trunca a ``max_length``.
    """
    if not text or not isinstance(text, str):
        return ""
    text = "".join(ch for ch in text if ch >= " " or ch in ("\n", "\t"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return text


def retry_on_failure(max_retries: int = 3, delay: float = 1.0) -> Callable:
    """Decorador para reintentar funciones que fallan, con backoff exponencial."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(
                        "Intento %s falló: %s. Reintentando...", attempt + 1, e
                    )
                    time.sleep(delay * (2 ** attempt))
            return None

        return wrapper

    return decorator
