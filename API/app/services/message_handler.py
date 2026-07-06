"""Cliente de WhatsApp Cloud API y dispatcher de mensajes (migrado celda 7).

`WhatsAppClient` encapsula el envío de mensajes/plantillas y la descarga de
media contra la Graph API de Meta. `MessageDispatcher` gestiona el envío
diferido con delays humanos, coalescencia de mensajes inbound (debounce) y el
gate de horario operativo (anti-bloqueo Meta).
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from app.core.config import DispatcherConfig, OperatingHoursGate, WhatsAppConfig
from app.core.logging import get_logger
from app.utils.text import retry_on_failure, validate_phone_number

logger = get_logger(__name__)


class WhatsAppClient:
    """Cliente para enviar mensajes por WhatsApp con rate limiting y retry"""

    def __init__(self, config: WhatsAppConfig):
        self.config = config
        self.config.validate()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json"
        })
        self.last_request_time = 0.0
        self.min_request_interval = 0.05

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()

    @retry_on_failure(max_retries=3)
    def send_text_message(self, to: str, message: str) -> Dict:
        if not validate_phone_number(to):
            raise ValueError(f"Número inválido: {mask_phone(to)}")
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message}
        }
        logger.info(f"📤 Enviando mensaje a {mask_phone(to)}")
        self._wait_for_rate_limit()
        response = self.session.post(self.config.messages_url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        msg_id = result.get('messages', [{}])[0].get('id', 'N/A')
        logger.info(f"✅ Enviado. ID: {msg_id}")
        return result

    def send_message(self, phone_number: str, message_text: str) -> bool:
        try:
            self.send_text_message(phone_number, message_text)
            return True
        except Exception as e:
            logger.error(f"Error enviando mensaje: {e}")
            return False

    @retry_on_failure(max_retries=3)
    def send_template_message(self, to: str, template_name: str,
                              language_code: str,
                              body_params: Optional[List[str]] = None) -> Dict:
        """
        ✅ v1.3: envía un mensaje basado en plantilla aprobada de Meta.

        Necesario para ABRIR conversación con un número que no ha escrito al
        bot en las últimas 24h (Meta bloquea texto libre en ese caso).

        Args:
          to:            número destino en formato sin "+", solo dígitos.
          template_name: nombre EXACTO de la plantilla aprobada en Meta.
          language_code: código de idioma EXACTO con que fue aprobada
                         (ej. "es_CO"). Si no coincide, Meta rechaza.
          body_params:   lista ordenada de strings que rellenan {{1}}, {{2}}, …
                         del cuerpo. Para la plantilla "saludo" (1 placeholder)
                         se pasa una lista de un solo string.

        Devuelve el JSON de la API; lanza excepción si la API responde error.
        """
        if not validate_phone_number(to):
            raise ValueError(f"Número inválido: {mask_phone(to)}")

        components = []
        if body_params:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(p)} for p in body_params
                ],
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                # `components` se omite si no hay parámetros (plantillas estáticas).
                **({"components": components} if components else {}),
            },
        }

        logger.info(
            f"📤 Enviando plantilla '{template_name}' [{language_code}] "
            f"a {mask_phone(to)} (params={len(body_params or [])})"
        )
        self._wait_for_rate_limit()
        response = self.session.post(self.config.messages_url, json=payload, timeout=10)
        # Si Meta rechaza la plantilla (no aprobada, idioma incorrecto, parámetros
        # mal contados), el cuerpo trae detalle. Lo logueamos antes de raise.
        if not response.ok:
            try:
                err_body = response.json()
            except Exception:
                err_body = response.text
            logger.error(
                f"❌ Plantilla rechazada por Meta (status={response.status_code}): {err_body}"
            )
        response.raise_for_status()
        result = response.json()
        msg_id = result.get('messages', [{}])[0].get('id', 'N/A')
        logger.info(f"✅ Plantilla enviada. ID: {msg_id}")
        return result

    @retry_on_failure(max_retries=3)
    def download_media(self, media_id: str) -> Optional[Tuple[bytes, str]]:
        """
        Descarga un archivo adjunto de WhatsApp Cloud API en 2 pasos:
          1) GET /{media_id}            → JSON con `url` temporal y `mime_type`
          2) GET <url> con Authorization → bytes del archivo
        Devuelve (bytes, mime_type) o None si falla.
        """
        if not media_id:
            return None
        try:
            meta_url = f"https://graph.facebook.com/{self.config.api_version}/{media_id}"
            self._wait_for_rate_limit()
            r = self.session.get(meta_url, timeout=10)
            r.raise_for_status()
            meta = r.json()
            file_url = meta.get("url")
            mime_type = meta.get("mime_type", "application/octet-stream")
            if not file_url:
                logger.error("download_media: respuesta sin URL")
                return None
            self._wait_for_rate_limit()
            # El bearer ya está en self.session.headers
            r2 = self.session.get(file_url, timeout=30)
            r2.raise_for_status()
            logger.info(f"📥 Descargado media {media_id} ({mime_type}, {len(r2.content)} bytes)")
            return r2.content, mime_type
        except Exception as e:
            logger.error(f"Error descargando media {media_id}: {e}")
            return None



# =============================================================================
# ✅ NUEVO: MessageDispatcher
# Cola in-memory + worker thread daemon que maneja TRES tipos de payload:
#
#   1) kind='text'  → envío directo de texto libre. Sin delay extra: el delay
#                     ya lo absorbió la ventana de escucha del intent.
#   2) kind='template' → envío de plantilla aprobada (outreach). Aplica
#                     outreach_delay_* antes del envío físico.
#   3) kind='inbound_intent' (✅ v1.4.2) → INTENT de respuesta diferida.
#                     Cuando se desencola, llama al callback registrado
#                     (processor._procesar_inbound_intent) que internamente
#                     genera la respuesta con IA y la encola como 'text'.
#                     Si llegan más mensajes del MISMO recipient mientras
#                     este intent está pendiente, el processor lo cancela
#                     vía cancel_inbound_intent_for() y encola uno nuevo
#                     con process_at reiniciado (debounce).
#
# Esto reemplaza el viejo delay intra-chat: ya no esperamos antes Y después
# de generar la respuesta. Esperamos UNA vez (listen_window_*), durante esa
# espera coalescemos mensajes, y al final enviamos inmediatamente.
#
# Reglas globales que siguen aplicando a TODOS los envíos físicos:
#   - inter-chat (2..5 min) entre envíos a números distintos (anti-bloqueo Meta)
#   - gate horario: si el envío caería fuera de ventana, requeue al next_open()
#
# El webhook NUNCA bloquea: encola y responde 200 inmediato. Esto es esencial
# porque WhatsApp reintenta el webhook si tarda y procesaríamos varias veces.
# =============================================================================
import threading
import heapq
import random as _rnd


class MessageDispatcher:
    """Cola de envíos diferidos con delays humanos + gate horario + coalescencia inbound."""

    def __init__(self, wa_client: WhatsAppClient, config: DispatcherConfig,
                 hours_gate: Optional["OperatingHoursGate"] = None):
        self.wa_client = wa_client
        self.config = config
        self.config.validate()
        self.hours_gate = hours_gate  # ✅ v1.1: gate opcional
        # Heap de items: (send_at_epoch, seq, recipient, payload)
        # payload['kind'] ∈ {'text', 'template', 'inbound_intent'}
        self._heap: list = []
        self._seq = 0
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._last_physical_send_epoch: float = 0.0
        self._worker: Optional[threading.Thread] = None
        # ✅ v1.4.2: callback que dispara la generación + envío de la respuesta
        # cuando vence un inbound_intent. Lo registra MessageProcessor al
        # construirse. Firma: callback(recipient: str) -> None.
        self._inbound_intent_callback: Optional[callable] = None

    # ------------------------------------------------------------------
    # ✅ v1.4.2: registro del callback para inbound_intent.
    # Se llama una sola vez desde el bootstrap, tras crear el processor.
    # ------------------------------------------------------------------
    def set_inbound_intent_callback(self, callback) -> None:
        self._inbound_intent_callback = callback

    def enqueue(self, recipient: str, text: str) -> float:
        """Encola un mensaje de TEXTO LIBRE listo para enviar.

        ✅ v1.4.2: sin delay artificial. La 'sensación humana' ya la dio el
        listen_window del inbound_intent que precede a este envío. El único
        delay que aún se aplica es inter_chat entre destinatarios distintos
        (en el worker) + el gate horario.
        """
        if not validate_phone_number(recipient):
            logger.warning("Dispatcher: número inválido, mensaje descartado")
            return 0.0
        send_at = time.time()  # ya: el worker aplicará inter_chat si toca
        payload = {"kind": "text", "body": text}
        with self._cv:
            self._seq += 1
            heapq.heappush(self._heap, (send_at, self._seq, recipient, payload))
            self._cv.notify_all()
        logger.info(
            f"📨 Encolado [text] para {mask_phone(recipient)} "
            f"(envío inmediato, cola={len(self._heap)})"
        )
        return send_at

    def enqueue_template(self, recipient: str, template_name: str,
                         language_code: str,
                         body_params: Optional[List[str]] = None) -> float:
        """
        ✅ v1.3: encola un mensaje de PLANTILLA aprobada (outreach / primer
        contacto). Aplica outreach_delay_* y respeta inter_chat + gate horario.
        """
        if not validate_phone_number(recipient):
            logger.warning("Dispatcher: número inválido, plantilla descartada")
            return 0.0
        delay = _rnd.uniform(
            self.config.outreach_delay_min_s, self.config.outreach_delay_max_s
        )
        send_at = time.time() + delay
        payload = {
            "kind": "template",
            "name": template_name,
            "lang": language_code,
            "params": list(body_params or []),
        }
        with self._cv:
            self._seq += 1
            heapq.heappush(self._heap, (send_at, self._seq, recipient, payload))
            self._cv.notify_all()
        logger.info(
            f"📨 Encolado [template:{template_name}] para {mask_phone(recipient)} "
            f"en {delay:.0f}s (cola={len(self._heap)})"
        )
        return send_at

    # ------------------------------------------------------------------
    # ✅ v1.4.2: inbound_intent (coalescencia / debounce)
    # ------------------------------------------------------------------
    def enqueue_inbound_intent(self, recipient: str) -> float:
        """
        Encola un INTENT de respuesta diferida para `recipient`. Cuando venza,
        el worker invocará el callback registrado, que es quien lee el buffer
        acumulado del cliente, genera la respuesta IA y la encola como 'text'.

        El delay (listen_window_*) es la ventana durante la cual esperamos
        nuevos mensajes del mismo cliente. Si llegan, el processor llama a
        cancel_inbound_intent_for() y re-llama a este método (reinicia el timer).
        """
        if not validate_phone_number(recipient):
            logger.warning("Dispatcher: número inválido, intent descartado")
            return 0.0
        delay = _rnd.uniform(
            self.config.listen_window_min_s, self.config.listen_window_max_s
        )
        process_at = time.time() + delay
        payload = {"kind": "inbound_intent"}
        with self._cv:
            self._seq += 1
            heapq.heappush(self._heap, (process_at, self._seq, recipient, payload))
            self._cv.notify_all()
        logger.info(
            f"👂 Encolado [inbound_intent] para {mask_phone(recipient)} "
            f"escuchando {delay:.0f}s (cola={len(self._heap)})"
        )
        return process_at

    def cancel_inbound_intent_for(self, recipient: str) -> int:
        """
        Cancela los inbound_intent pendientes para `recipient`. Retorna
        cuántos canceló. Solo afecta a kind='inbound_intent' — los envíos
        físicos ya encolados (text/template) NO se cancelan, porque
        representan trabajo terminado a punto de salir.

        O(n) sobre el heap. n es pequeño (decenas) en la práctica.
        """
        with self._cv:
            before = len(self._heap)
            self._heap = [
                item for item in self._heap
                if not (
                    item[2] == recipient
                    and isinstance(item[3], dict)
                    and item[3].get("kind") == "inbound_intent"
                )
            ]
            heapq.heapify(self._heap)
            cancelled = before - len(self._heap)
            if cancelled > 0:
                self._cv.notify_all()
        if cancelled > 0:
            logger.info(
                f"🔄 Cancelados {cancelled} inbound_intent(s) pendientes para "
                f"{mask_phone(recipient)}"
            )
        return cancelled

    def start(self):
        """Arranca el worker (idempotente)."""
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, name="MsgDispatcher", daemon=True
        )
        self._worker.start()
        gate_info = "con gate horario" if self.hours_gate else "sin gate horario"
        logger.info(
            f"🚀 Dispatcher iniciado {gate_info} "
            f"(listen={self.config.listen_window_min_s}..{self.config.listen_window_max_s}s, "
            f"outreach_delay={self.config.outreach_delay_min_s}..{self.config.outreach_delay_max_s}s, "
            f"inter={self.config.inter_chat_min_s}..{self.config.inter_chat_max_s}s)"
        )

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._worker:
            self._worker.join(timeout=timeout)
        logger.info("🛑 Dispatcher detenido")

    def pending(self) -> int:
        with self._cv:
            return len(self._heap)

    def _seconds_until_next_open(self) -> float:
        """Devuelve segundos hasta el próximo `next_open` del gate. Solo se
        llama cuando ya sabemos que estamos fuera de ventana.

        ⚠️ TZ-aware: pasamos `now` aware en UTC; el gate lo convierte a
        Bogotá internamente y devuelve un datetime aware. La resta entre
        dos aware es correcta (no requiere conversión adicional).
        """
        try:
            now_dt = datetime.now(timezone.utc)
            next_open_dt = self.hours_gate.next_open(now_dt)
            secs = (next_open_dt - now_dt).total_seconds()
            return max(secs, 1.0)
        except Exception as e:
            logger.error(f"Dispatcher: error consultando next_open: {e}")
            return 300.0

    def _run(self):
        while not self._stop.is_set():
            with self._cv:
                while not self._heap and not self._stop.is_set():
                    self._cv.wait(timeout=30)
                if self._stop.is_set():
                    return

                send_at, seq, recipient, payload = self._heap[0]
                now = time.time()
                kind = payload.get("kind") if isinstance(payload, dict) else "text"

                # ✅ v1.4.2: los inbound_intent NO consumen el slot inter_chat.
                # No son envíos físicos: solo disparan generación de respuesta.
                # El envío físico ocurre cuando el callback haga enqueue() del
                # 'text' resultante; ahí sí se aplicará inter_chat.
                if kind == "inbound_intent":
                    if now < send_at:
                        self._cv.wait(timeout=send_at - now)
                        continue
                    heapq.heappop(self._heap)
                    # caer fuera del lock para invocar el callback
                else:
                    # Envío físico (text o template): respetar inter_chat
                    inter = (
                        _rnd.uniform(self.config.inter_chat_min_s, self.config.inter_chat_max_s)
                        if self._last_physical_send_epoch > 0 else 0.0
                    )
                    earliest_allowed = self._last_physical_send_epoch + inter
                    target = max(send_at, earliest_allowed)

                    if now < target:
                        self._cv.wait(timeout=target - now)
                        continue

                    # ✅ v1.1: gate horario (decisión 3a). Solo aplica a envíos físicos.
                    if self.hours_gate is not None and not self.hours_gate.is_open():
                        secs_until = self._seconds_until_next_open()
                        new_send_at = time.time() + secs_until
                        heapq.heapreplace(
                            self._heap, (new_send_at, seq, recipient, payload)
                        )
                        logger.info(
                            f"⏸️  Fuera de ventana: requeue mensaje para "
                            f"{mask_phone(recipient)} en {secs_until:.0f}s"
                        )
                        continue

                    heapq.heappop(self._heap)

            # ─── Fuera del lock ───────────────────────────────────────────
            if kind == "inbound_intent":
                # Disparar el callback que genera + encola la respuesta.
                # NO actualiza _last_physical_send_epoch: aún no hay envío real.
                cb = self._inbound_intent_callback
                if cb is None:
                    logger.error(
                        f"inbound_intent venció para {mask_phone(recipient)} "
                        f"pero no hay callback registrado — descartado"
                    )
                    continue
                try:
                    cb(recipient)
                except Exception as e:
                    logger.error(
                        f"Error procesando inbound_intent para "
                        f"{mask_phone(recipient)}: {e}"
                    )
                continue

            # kind in {text, template}: enviar físicamente
            try:
                if kind == "template":
                    self.wa_client.send_template_message(
                        to=recipient,
                        template_name=payload["name"],
                        language_code=payload["lang"],
                        body_params=payload.get("params") or [],
                    )
                else:
                    body = payload["body"] if isinstance(payload, dict) else payload
                    self.wa_client.send_text_message(recipient, body)
                self._last_physical_send_epoch = time.time()
                logger.info(
                    f"✉️  Enviado a {mask_phone(recipient)} "
                    f"(kind={kind}, restantes={self.pending()})"
                )
            except Exception as e:
                logger.error(f"Error en envío diferido a {mask_phone(recipient)}: {e}")




__all__ = ["WhatsAppClient", "MessageDispatcher"]
