"""Configuración de logging con filtro PII (migrado de la celda 6 del notebook).

`PIIFilter` enmascara Bearer tokens y números de teléfono en cualquier log,
evitando filtrar secretos o datos personales. `setup_logging` centraliza la
configuración que en el notebook estaba en `logging.basicConfig` + `addFilter`.
"""
from __future__ import annotations

import logging
import re

from app.utils.text import mask_phone

_DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class PIIFilter(logging.Filter):
    """Enmascara tokens, secrets y números de teléfono en los logs.

    Nota histórica (FIX 2.1): las regex originales tenían doble escape
    (``\\d``, ``\\+``) que las volvía literales y nunca matcheaban. Aquí los
    metacaracteres son correctos, así que los teléfonos sí se enmascaran.
    """

    # Bearer tokens largos.
    _TOKEN_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.]{20,})")
    # Teléfonos: 10-15 dígitos, opcionalmente con + delante, con boundary numérica.
    _PHONE_RE = re.compile(r"(?<!\d)(\+?\d{10,15})(?!\d)")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            msg = self._TOKEN_RE.sub(r"\1***REDACTED***", msg)
            msg = self._PHONE_RE.sub(lambda m: mask_phone(m.group(1)), msg)
            record.msg = msg
        # También sanear args si vienen.
        if record.args:
            try:
                new_args = []
                for a in record.args:
                    if isinstance(a, str):
                        a = self._TOKEN_RE.sub(r"\1***REDACTED***", a)
                        a = self._PHONE_RE.sub(lambda m: mask_phone(m.group(1)), a)
                    new_args.append(a)
                record.args = tuple(new_args)
            except Exception:
                pass
        return True


def setup_logging(level: int = logging.INFO, fmt: str = _DEFAULT_FORMAT) -> logging.Logger:
    """Configura el logging raíz con formato estándar y `PIIFilter`.

    Idempotente: puede llamarse en el arranque de la app sin duplicar filtros.
    Devuelve el logger raíz ya configurado.
    """
    logging.basicConfig(level=level, format=fmt)

    root = logging.getLogger()
    root.setLevel(level)

    if not any(isinstance(f, PIIFilter) for f in root.filters):
        root.addFilter(PIIFilter())

    return root


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger con `PIIFilter` aplicado."""
    log = logging.getLogger(name)
    if not any(isinstance(f, PIIFilter) for f in log.filters):
        log.addFilter(PIIFilter())
    return log


# ---------------------------------------------------------------------------
# Ring buffer de logs para el dashboard (migrado de la celda 12.5.1)
# ---------------------------------------------------------------------------
class RingBufferLogHandler(logging.Handler):
    """Handler que guarda las últimas N entradas de log en memoria.

    El dashboard hace polling a /api/bot/logs. No persiste: al reiniciar el
    proceso el buffer queda vacío.
    """

    def __init__(self, maxlen: int = 1000):
        super().__init__()
        import collections
        import threading

        self._buf = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        from datetime import datetime, timezone

        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        with self._lock:
            self._buf.append(entry)

    def get(self, level=None, limit: int = 200, since=None):
        with self._lock:
            items = list(self._buf)
        if level:
            level = level.upper().strip()
            items = [x for x in items if x["level"] == level]
        if since:
            items = [x for x in items if x["timestamp"] > since]
        return items[-limit:]


# Singleton del ring buffer, conectado al logger raíz por `attach_ring_buffer`.
LOG_BUFFER = RingBufferLogHandler(maxlen=1000)


def attach_ring_buffer(level: int = logging.DEBUG) -> RingBufferLogHandler:
    """Conecta `LOG_BUFFER` al logger raíz (con `PIIFilter`). Idempotente."""
    root = logging.getLogger()
    LOG_BUFFER.setLevel(level)
    if not any(isinstance(f, PIIFilter) for f in LOG_BUFFER.filters):
        LOG_BUFFER.addFilter(PIIFilter())
    if LOG_BUFFER not in root.handlers:
        root.addHandler(LOG_BUFFER)
    return LOG_BUFFER
