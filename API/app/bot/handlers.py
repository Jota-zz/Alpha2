"""Procesador de mensajes entrantes (migrado de la celda 9, parte 1).

`MessageProcessor` orquesta todo el flujo inbound/outreach: idempotencia de
webhooks, coalescencia de mensajes (burst) por cliente, manejo de adjuntos
(PDF/imagen de cotización), flujo conversacional con IA, extracción acumulativa
de cotizaciones y transiciones de estado de la ferretería. Es agnóstico del
framework web: el router de FastAPI solo le entrega el payload crudo.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import OperatingHoursGate
from app.core.logging import get_logger
from app.db.session import DatabaseManager
from app.models import EstadoFereteria, Ferreteria, Producto
from app.schemas import EstadoExtraccionAcumulado
from app.services.anthropic_client import AnthropicAIClient, AnthropicExtractionClient
from app.services.matching import ExtractorTextoAcumulativo
from app.services.message_handler import MessageDispatcher, WhatsAppClient
from app.utils.text import mask_phone, sanitize_user_input, validate_phone_number

logger = get_logger(__name__)


class MessageProcessor:
    """
    Procesa mensajes y coordina:
      - generación de respuesta (AnthropicAIClient)
      - extracción de señales (AnthropicExtractionClient)
      - persistencia (DatabaseManager: interacción + cotización)
      - transiciones de estado de la ferretería

    ✅ v1.1 — _handle_ai_flow polimórfico, dos modos:
      modo="outreach": itera ferreterías con `estado IS NULL` y no vetadas.
                       Construye user_message desde `topic`, ejecuta pasos 2–6
                       por cada una, y transiciona None → primer_mensaje al final.
      modo="inbound":  comportamiento original. Busca ferretería por teléfono,
                       valida veto, ejecuta pasos 2–6 con el (recipient, message)
                       que llegó del webhook.

    Reglas de transición aplicadas (modo inbound):
      None + texto del cliente              → primer_mensaje → inicio  (encadenado)
      primer_mensaje + texto del cliente    → inicio                    (cliente respondió broadcast)
      inicio + (precio + marca extraídos)   → cotizacion (+ persistir cotización)
      cotizacion + (image | document)       → cierre   (si la extracción produjo ≥1 línea)
      inicio + (image | document)           → cotizacion → cierre  (encadenado, si extracción produjo líneas)
      cualquier estado + despedida          → terminado (forzado)

    Optimización: no se ejecuta el extractor si el estado actual es
    `cierre` o `terminado` (ya no aporta señales útiles, ahorra tokens).
    """

    TIPOS_TEXTO = {"text"}
    TIPOS_ADJUNTO = {"image", "document"}

    # Estados donde NO tiene sentido correr el extractor de señales
    ESTADOS_SIN_EXTRACTOR = {EstadoFereteria.cierre, EstadoFereteria.terminado}

    def __init__(self, wa_client: WhatsAppClient, ai_client: AnthropicAIClient,
                 db_manager: DatabaseManager,
                 extraction_client: Optional[AnthropicExtractionClient] = None,
                 extractor_acumulativo: Optional["ExtractorTextoAcumulativo"] = None,
                 csv_cotizaciones_pdf: Optional[str] = None,
                 dispatcher: Optional["MessageDispatcher"] = None,
                 hours_gate: Optional["OperatingHoursGate"] = None):
        self.wa_client = wa_client
        self.ai_client = ai_client
        self.db_manager = db_manager
        self.extraction_client = extraction_client
        # ✅ NUEVO: extractor acumulativo (determinista + fallback LLM acotado).
        # Si está disponible, REEMPLAZA al extractor LLM de un solo turno para
        # detectar la transición inicio → cotizacion.
        self.extractor_acumulativo = extractor_acumulativo
        self.csv_cotizaciones_pdf = csv_cotizaciones_pdf
        self.dispatcher = dispatcher
        self.hours_gate = hours_gate  # ✅ v1.1: gate opcional
        # Cache in-memory como respaldo si la BD falla durante la verificación
        # de idempotencia. La fuente de verdad es la tabla webhook_events_processed.
        self.processed_messages: set = set()
        # ✅ v1.4.2: buffer de mensajes inbound pendientes de procesar por
        # recipient. Acumula los textos del 'burst' (mensajes en cascada del
        # mismo cliente dentro de la ventana de escucha). Cuando el dispatcher
        # dispara el inbound_intent vencido, _procesar_inbound_intent() lee y
        # vacía el buffer aquí. Protegido por _pending_lock para evitar race
        # entre el hilo del webhook y el hilo del dispatcher.
        #
        # Estructura:
        #   recipient(str) -> List[Dict] donde cada dict es
        #     {'text': str, 'interaction_id': str}
        # El interaction_id apunta a la fila ya persistida en BD (con
        # respuesta_ia=NULL). Cuando la IA finalmente genere la respuesta,
        # haremos UPDATE sobre el id de la ÚLTIMA fila — los anteriores
        # quedan como mensajes del usuario sin respuesta del bot, que es
        # semánticamente correcto: el bot solo respondió una vez, al final.
        self._pending_messages: Dict[str, List[Dict[str, str]]] = {}
        self._pending_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Entrada del webhook
    # ✅ v1.1: gate horario estricto (decisión 1a). Si estamos fuera de
    #          ventana, descartamos el webhook entrante completamente.
    # ------------------------------------------------------------------
    def process_incoming(self, data: Dict):
        try:
            # ✅ v1.1: gate estricto (decisión 1a)
            if self.hours_gate is not None and not self.hours_gate.is_open():
                logger.info(
                    "🌙 Webhook entrante descartado: bot fuera de ventana operativa"
                )
                return

            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})

            if "messages" not in value:
                return

            msg = value["messages"][0]
            msg_id = msg.get("id")
            msg_type = msg.get("type")

            # ✅ FIX 2.7: idempotencia persistente
            # 1) Si ya está en el cache in-memory, descartar
            if msg_id and msg_id in self.processed_messages:
                logger.info(f"Mensaje duplicado (cache local): {msg_id}")
                return
            # 2) Verificar+insertar atómicamente en BD
            if msg_id and self.db_manager.msg_ya_procesado(msg_id):
                logger.info(f"Mensaje duplicado (BD): {msg_id}")
                self.processed_messages.add(msg_id)  # cache para acelerar próximos
                return
            # 3) Si pasó ambos, marcar en cache
            if msg_id:
                self.processed_messages.add(msg_id)
                if len(self.processed_messages) > 5000:
                    self.processed_messages = set(list(self.processed_messages)[-2500:])

            sender = msg.get("from")
            if not sender or not validate_phone_number(sender):
                logger.warning("Mensaje sin 'from' válido, descartado")
                return

            if msg_type in self.TIPOS_ADJUNTO:
                attach = msg.get(msg_type, {}) or {}
                media_id = attach.get("id")
                mime_type = attach.get("mime_type", "")
                self._handle_attachment(sender, msg_type, media_id, mime_type)
                return

            if msg_type not in self.TIPOS_TEXTO:
                logger.info(f"Tipo de mensaje no soportado: {msg_type}")
                return

            text = msg.get("text", {}).get("body", "")
            text = sanitize_user_input(text)
            if not text:
                logger.info(f"Mensaje vacío tras sanitización")
                return

            # ✅ v1.4.2: coalescencia inbound. En vez de llamar directo a
            # _handle_ai_flow (que generaba IA + encolaba envío con doble
            # delay), encolamos un inbound_intent. Si la ferretería envía
            # más mensajes durante la ventana de escucha, los acumulamos
            # en el buffer y reiniciamos el timer (debounce).
            self._encolar_mensaje_inbound(sender, text)
        except Exception as e:
            logger.error(f"Error en process_incoming: {e}")

    # ------------------------------------------------------------------
    # ✅ v1.4.2: coalescencia inbound
    # ------------------------------------------------------------------
    def _encolar_mensaje_inbound(self, recipient: str, text: str) -> None:
        """
        Acumula un mensaje inbound en el buffer del recipient y (re)agenda
        el inbound_intent en el dispatcher. Cada llamada reinicia el timer
        de escucha (debounce). Si el recipient no corresponde a ninguna
        ferretería conocida o está vetado, descarta silenciosamente.

        Persiste el mensaje del usuario en BD inmediatamente (con
        respuesta_ia=NULL) para que los mensajes siguientes del mismo burst
        lo vean al consultar el historial. Cuando finalmente la IA genere
        la respuesta, se hará UPDATE sobre la última fila pendiente.
        """
        # 1) Validar la ferretería ANTES de tocar buffer o dispatcher. Si el
        # número es desconocido o vetado, no queremos gastar slots de cola.
        try:
            ferreteria = self.db_manager.obtener_ferreteria_por_telefono(recipient)
        except Exception as e:
            logger.error(f"Error buscando ferretería en inbound: {e}")
            return
        if not ferreteria:
            logger.warning(
                f"Mensaje de número desconocido {mask_phone(recipient)}. "
                "Bot no crea ferreterías automáticamente. Ignorando."
            )
            return
        try:
            if self.db_manager.is_phone_vetoed(ferreteria.id_ferreteria, recipient):
                logger.info(f"Mensaje ignorado (número vetado)")
                return
        except Exception as e:
            logger.error(f"Error verificando veto: {e}")
            return

        # 2) Persistir el mensaje del usuario YA (con respuesta_ia=NULL) para
        # que aparezca en el historial cuando lleguen mensajes posteriores.
        # Guardamos el interaction_id para hacerle UPDATE más tarde — solo al
        # ÚLTIMO de la ráfaga, que es el que carga la respuesta del bot.
        try:
            interaction_id = self.db_manager.guardar_mensaje_usuario(
                id_ferreteria=str(ferreteria.id_ferreteria),
                mensaje_usuario=text,
            )
        except Exception as e:
            logger.error(f"Error persistiendo mensaje usuario: {e}")
            interaction_id = None  # buffer en memoria sigue salvando la respuesta

        # 3) Apilar en el buffer y (re)agendar el intent. Se hace bajo el
        # mismo lock que protege el buffer, para que no se mezcle con el
        # vaciado del buffer en _procesar_inbound_intent.
        with self._pending_lock:
            self._pending_messages.setdefault(recipient, []).append(
                {"text": text, "interaction_id": interaction_id or ""}
            )
            pendientes = len(self._pending_messages[recipient])

        if self.dispatcher is None:
            # Sin dispatcher no hay coalescencia posible: caemos al flujo viejo
            # síncronamente. Caso de bootstrap incompleto.
            logger.warning(
                "Inbound sin dispatcher configurado: ejecutando flujo síncrono"
            )
            with self._pending_lock:
                msgs = self._pending_messages.pop(recipient, [])
            self._handle_ai_flow(
                modo="inbound", recipient=recipient,
                message="\n".join(m["text"] for m in msgs) if msgs else text,
            )
            return

        # Cancelar cualquier intent pendiente y encolar uno nuevo (reinicia timer).
        cancelled = self.dispatcher.cancel_inbound_intent_for(recipient)
        self.dispatcher.enqueue_inbound_intent(recipient)
        logger.info(
            f"👂 Mensaje #{pendientes} en buffer de {mask_phone(recipient)} "
            f"(intent reagendado, prev_cancelados={cancelled})"
        )

    def _procesar_inbound_intent(self, recipient: str) -> None:
        """
        Callback que invoca el dispatcher cuando vence un inbound_intent.
        Lee y vacía el buffer del recipient, concatena los mensajes
        acumulados en uno solo (separados por \\n) y ejecuta el flujo
        clásico inbound (pasos 2–6). La respuesta final se encolará como
        kind='text' dentro de _ejecutar_pasos_2_a_6 vía dispatcher.enqueue(),
        que ya no aplica delay artificial — sale tan pronto el inter_chat
        y el gate horario lo permitan.

        IMPORTANTE: este método corre en el hilo del dispatcher, NO en el
        de Flask. Por eso es seguro hacer llamadas potencialmente lentas
        (Anthropic API, BD): no bloquea webhooks.
        """
        with self._pending_lock:
            mensajes = self._pending_messages.pop(recipient, [])

        if not mensajes:
            # Race poco probable: el intent venció pero el buffer ya está vacío
            # (¿alguien lo procesó? ¿se vació externamente?). Log y salir.
            logger.warning(
                f"inbound_intent venció para {mask_phone(recipient)} pero "
                f"el buffer está vacío — nada que procesar"
            )
            return

        # Concatenar mensajes del burst. El extractor de cotizaciones recibe
        # este texto como user_message (paso 6); inyectar todos los mensajes
        # juntos da más señal al extractor sin perder contexto. El historial
        # también lo verá: las filas con respuesta_ia=NULL aparecen como
        # turnos del usuario sueltos (obtener_historial_reciente ya filtra
        # los assistant vacíos).
        user_message_agregado = "\n".join(m["text"] for m in mensajes)
        # La fila que recibirá la respuesta del bot es la del ÚLTIMO mensaje
        # del burst (cronológicamente la última de la conversación). Los
        # mensajes previos del burst se quedan con respuesta_ia=NULL — refleja
        # la realidad: el bot esperó a que la ferretería terminara y luego
        # respondió una sola vez.
        interaction_id_pendiente = mensajes[-1].get("interaction_id") or None
        logger.info(
            f"🧠 Procesando inbound_intent de {mask_phone(recipient)}: "
            f"{len(mensajes)} mensaje(s) coalescido(s)"
        )
        # Reusamos el camino existente. _handle_ai_flow_inbound vuelve a
        # buscar la ferretería y revalidar veto — redundante pero barato y
        # blindado frente a cambios de estado durante la ventana de escucha
        # (p.ej. el operador veta el número en medio del burst).
        self._handle_ai_flow(
            modo="inbound", recipient=recipient,
            message=user_message_agregado,
            interaction_id_pendiente=interaction_id_pendiente,
        )

    # ------------------------------------------------------------------
    # ✅ v1.4.3: rehidratación al arranque
    # ------------------------------------------------------------------
    def rehidratar_inbounds_huerfanos(self,
                                       ventana_minutos_responder: int = 15,
                                       ventana_minutos_marcar: int = 120) -> Dict[str, int]:
        """
        Tras un reinicio del bot, busca en BD mensajes inbound que entraron
        pero nunca recibieron respuesta (respuesta_ia=NULL). Dos rangos:

        1) [< ventana_minutos_responder min] — los repuebla en el buffer
           y encola inbound_intent para que el dispatcher los procese
           normalmente. El cliente recibirá la respuesta diferida (con un
           pequeño atraso por el reinicio, pero la recibirá).

        2) [entre ventana_minutos_responder y ventana_minutos_marcar] —
           demasiado tarde para responder (el cliente probablemente se
           fue), pero los marca con un placeholder en respuesta_ia para
           que dejen de aparecer como NULL en queries y reportes.

        Las filas más antiguas que `ventana_minutos_marcar` se ignoran:
           son historia antigua que no nos toca limpiar.

        Devuelve un dict con las cuentas:
           {'rehidratados': N, 'marcados_perdidos': M}

        IDEMPOTENTE: se puede llamar varias veces sin duplicar trabajo.
        Las filas rehidratadas se completan al vencer su nuevo intent.
        """
        if self.dispatcher is None:
            logger.warning(
                "rehidratar_inbounds_huerfanos: sin dispatcher, no se puede "
                "reagendar. Marcando todos los pendientes como perdidos."
            )
            ventana_minutos_responder = 0

        pendientes = self.db_manager.obtener_inbounds_pendientes(
            ventana_minutos=ventana_minutos_marcar
        )
        if not pendientes:
            logger.info("✅ No hay inbounds huérfanos para rehidratar")
            return {"rehidratados": 0, "marcados_perdidos": 0}

        ahora = datetime.now(timezone.utc)
        umbral_responder = ahora - timedelta(minutes=ventana_minutos_responder)

        # Agrupar por número de teléfono — varios mensajes del mismo cliente
        # en el burst original deben rehidratarse JUNTOS, no separados.
        por_numero: Dict[str, List[Dict]] = {}
        marcar_perdidos: List[str] = []

        for p in pendientes:
            # Asegurar que fecha sea aware (Postgres devuelve aware con tz)
            fecha = p["fecha"]
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            if fecha >= umbral_responder:
                por_numero.setdefault(p["numero"], []).append(p)
            else:
                marcar_perdidos.append(p["id_interaccion"])

        # Rehidratar — repoblar buffer y encolar UN intent por número
        rehidratados = 0
        for numero, items in por_numero.items():
            with self._pending_lock:
                buffer_existente = self._pending_messages.setdefault(numero, [])
                ids_ya_en_buffer = {e.get("interaction_id") for e in buffer_existente}
                for item in items:
                    if item["id_interaccion"] in ids_ya_en_buffer:
                        continue  # idempotencia: ya estaba
                    buffer_existente.append({
                        "text": item["mensaje"],
                        "interaction_id": item["id_interaccion"],
                    })
                    rehidratados += 1
            # Reagendar intent (cancela previo si lo había)
            self.dispatcher.cancel_inbound_intent_for(numero)
            self.dispatcher.enqueue_inbound_intent(numero)
            logger.info(
                f"♻️  Rehidratados {len(items)} mensaje(s) huérfanos de "
                f"{mask_phone(numero)} → intent reagendado"
            )

        # Marcar los demasiado antiguos
        marcados = 0
        for id_int in marcar_perdidos:
            try:
                if self.db_manager.marcar_inbound_perdido(id_int):
                    marcados += 1
            except Exception as e:
                logger.error(f"Error marcando perdido {id_int}: {e}")

        if marcados:
            logger.info(
                f"🪦 {marcados} inbound(s) demasiado antiguos marcados como "
                f"'respuesta perdida' (>{ventana_minutos_responder}min de retraso)"
            )
        return {"rehidratados": rehidratados, "marcados_perdidos": marcados}

    # ------------------------------------------------------------------
    # Adjuntos (image/document):
    #   - Las IMÁGENES también se descargan y se extraen (no solo PDFs).
    #   - La transición a `cierre` solo ocurre si la extracción produjo
    #     al menos UNA línea válida persistida.
    #   - Si el estado actual era `inicio`, se hace transición encadenada
    #     `inicio → cotizacion → cierre` (porque el adjunto aporta cotización
    #     Y la cierra simultáneamente).
    # ------------------------------------------------------------------
    def _handle_attachment(self, recipient: str, tipo: str,
                           media_id: Optional[str] = None,
                           mime_type: str = ""):
        try:
            ferreteria = self.db_manager.obtener_ferreteria_por_telefono(recipient)
            if not ferreteria:
                logger.info(f"Adjunto recibido de número desconocido, ignorado")
                return
            if self.db_manager.is_phone_vetoed(ferreteria.id_ferreteria, recipient):
                logger.info(f"Adjunto ignorado (número vetado)")
                return

            estado_actual = ferreteria.estado
            es_pdf = (tipo == "document" and "pdf" in (mime_type or "").lower())
            es_imagen = (tipo == "image")

            # Capturar contexto sin riesgo de session detached
            ferreteria_id_str = str(ferreteria.id_ferreteria)
            ferreteria_id_uuid = ferreteria.id_ferreteria
            regional = ferreteria.regional
            cod_municipio = ferreteria.cod_municipio

            # Solo procesamos PDFs e imágenes con extractor disponible
            persistidas = 0
            if media_id and self.extraction_client and (es_pdf or es_imagen):
                persistidas = self._procesar_adjunto_cotizacion(
                    media_id=media_id,
                    mime_type=mime_type,
                    es_pdf=es_pdf,
                    es_imagen=es_imagen,
                    ferreteria_id_str=ferreteria_id_str,
                    regional=regional,
                    cod_municipio=cod_municipio,
                )
            else:
                logger.info(
                    f"📎 Adjunto ({tipo}, mime={mime_type}) no procesable "
                    f"(media_id={bool(media_id)}, extractor={bool(self.extraction_client)}, "
                    f"pdf={es_pdf}, img={es_imagen})"
                )

            # Transiciones SOLO si se persistió al menos una línea
            if persistidas == 0:
                logger.info(
                    f"📎 Adjunto sin líneas extraídas → no se aplica transición "
                    f"(estado sigue en {estado_actual.value if estado_actual else 'None'})"
                )
                return

            # Hubo extracción exitosa → aplicar transición(es) según estado actual
            if estado_actual == EstadoFereteria.inicio:
                ok1 = self.db_manager.transicionar_estado(
                    ferreteria_id_uuid, EstadoFereteria.cotizacion
                )
                if ok1:
                    self.db_manager.transicionar_estado(
                        ferreteria_id_uuid, EstadoFereteria.cierre
                    )
                    logger.info(f"📎 Adjunto válido → inicio → cotizacion → cierre")
                else:
                    logger.warning(f"📎 No se pudo transicionar inicio → cotizacion")
            elif estado_actual == EstadoFereteria.cotizacion:
                ok = self.db_manager.transicionar_estado(
                    ferreteria_id_uuid, EstadoFereteria.cierre
                )
                if ok:
                    logger.info(f"📎 Adjunto válido → cotizacion → cierre")
                else:
                    logger.warning(f"📎 No se pudo transicionar a cierre")
            elif estado_actual == EstadoFereteria.cierre:
                logger.info(f"📎 Adjunto válido en estado cierre (idempotente)")
            elif estado_actual == EstadoFereteria.primer_mensaje:
                ok1 = self.db_manager.transicionar_estado(
                    ferreteria_id_uuid, EstadoFereteria.inicio
                )
                if ok1:
                    ok2 = self.db_manager.transicionar_estado(
                        ferreteria_id_uuid, EstadoFereteria.cotizacion
                    )
                    if ok2:
                        self.db_manager.transicionar_estado(
                            ferreteria_id_uuid, EstadoFereteria.cierre
                        )
                        logger.info(
                            f"📎 Adjunto válido → primer_mensaje → inicio → cotizacion → cierre"
                        )
            else:
                estado_str = estado_actual.value if estado_actual else "None"
                logger.info(
                    f"📎 Adjunto válido pero estado actual '{estado_str}' "
                    f"no permite transición a cierre"
                )
        except Exception as e:
            logger.error(f"Error manejando adjunto: {e}")

    def _procesar_adjunto_cotizacion(self, media_id: str, mime_type: str,
                                     es_pdf: bool, es_imagen: bool,
                                     ferreteria_id_str: str,
                                     regional: str,
                                     cod_municipio: Optional[str]) -> int:
        """
        Pipeline UNIFICADO para adjuntos (PDF e imagen):
          1) WhatsAppClient.download_media → bytes + mime
          2) Extracción con el método correspondiente al tipo
          3) Resolver cemento en catálogo UNA sola vez (NO crearlo)
          4) Por cada línea válida (con marca + precio):
             a) crear interacción marcador propia (id_interaccion único)
             b) registrar cotización en BD (FKs reales)
             c) anexar al CSV incremental
          5) Devolver el número de líneas persistidas

        Devuelve: int = líneas persistidas (0 si no hubo extracción exitosa).
        """
        try:
            # 1) descarga
            descarga = self.wa_client.download_media(media_id)
            if not descarga:
                logger.warning("Adjunto: no se pudo descargar el media")
                return 0
            file_bytes, mime_real = descarga

            # 2) extracción según tipo
            tipo_log = "PDF" if es_pdf else "imagen"
            if es_pdf:
                if "pdf" not in (mime_real or "").lower():
                    logger.info(f"Documento descargado no es PDF (mime={mime_real}), omitido")
                    return 0
                lineas = self.extraction_client.extract_quote_from_pdf(file_bytes)
            elif es_imagen:
                lineas = self.extraction_client.extract_quote_from_image(
                    file_bytes, mime_type=mime_real or mime_type
                )
            else:
                logger.warning(f"Adjunto: tipo no soportado")
                return 0

            if lineas is None:
                logger.warning(f"{tipo_log}: extractor falló (None); no se persiste nada")
                return 0
            if len(lineas) == 0:
                logger.info(f"{tipo_log}: no contiene líneas de cemento; nada que persistir")
                return 0

            # 3) resolver cemento en catálogo UNA vez
            cemento = self.db_manager.obtener_producto_cemento()
            if cemento is None:
                logger.warning(
                    f"{tipo_log}: no hay producto 'cemento' en catálogo; "
                    f"se descartan las {len(lineas)} líneas detectadas"
                )
                return 0
            id_producto, nombre_real = cemento

            # 4) iterar y persistir
            csv_path = getattr(self, "csv_cotizaciones_pdf", None)
            persistidas = 0
            descartadas = 0

            for i, linea in enumerate(lineas, start=1):
                tag = f"[{i}/{len(lineas)}]"
                marca_nombre = (linea.get("marca") or "").strip()
                precio_raw = linea.get("precio_unitario")

                if not marca_nombre or precio_raw is None:
                    logger.info(
                        f"{tipo_log} {tag}: incompleta (marca={marca_nombre!r}, "
                        f"precio={precio_raw}); línea descartada"
                    )
                    descartadas += 1
                    continue
                try:
                    precio_f = float(precio_raw)
                    if precio_f <= 0:
                        raise ValueError("precio <= 0")
                except (TypeError, ValueError) as e:
                    logger.info(f"{tipo_log} {tag}: precio inválido ({precio_raw!r}: {e}); descartada")
                    descartadas += 1
                    continue

                # 4a) interacción marcador propia para esta línea
                interaction_id = self.db_manager.guardar_interaccion(
                    id_ferreteria=ferreteria_id_str,
                    mensaje_usuario=(
                        f"[{tipo_log} cotización media_id={media_id} línea {i}/{len(lineas)}]"
                    ),
                    respuesta_ia=(
                        f"[{tipo_log} procesado: {nombre_real} {marca_nombre} ${precio_f:.2f}]"
                    ),
                )
                if not interaction_id:
                    logger.error(f"{tipo_log} {tag}: no se pudo crear interacción marcador")
                    descartadas += 1
                    continue

                # 4b) registrar cotización en BD
                fila = self.db_manager.registrar_cotizacion(
                    id_interaccion=interaction_id,
                    id_ferreteria=ferreteria_id_str,
                    producto_nombre=nombre_real,
                    marca_nombre=marca_nombre,
                    precio=precio_f,
                    regional=regional,
                    disponibilidad=linea.get("disponibilidad"),
                    confianza=linea.get("confianza"),
                    info_solicitada=linea.get("observaciones"),
                    cod_municipio=cod_municipio,
                    id_producto=id_producto,
                )
                if not fila:
                    logger.warning(f"{tipo_log} {tag}: inserción en `cotizaciones` falló")
                    descartadas += 1
                    continue

                # 4c) anexar al CSV
                if csv_path:
                    self.db_manager.append_cotizacion_a_csv(fila, csv_path)
                persistidas += 1

            # 5) resumen
            if not csv_path:
                logger.warning(f"{tipo_log}: csv_cotizaciones_pdf no configurado; solo BD")
            logger.info(
                f"📄 {tipo_log} resumen: {len(lineas)} líneas detectadas, "
                f"{persistidas} persistidas, {descartadas} descartadas"
            )
            return persistidas
        except Exception as e:
            logger.error(f"Error procesando adjunto: {e}")
            return 0

    # ==================================================================
    # ✅ v1.1: _handle_ai_flow POLIMÓRFICO
    # Dispatcher por modo. Cada modo prepara su contexto y luego ambos
    # convergen en _ejecutar_pasos_2_a_6() que contiene los pasos compartidos.
    # ==================================================================
    def _handle_ai_flow(self, modo: str, *,
                        recipient: Optional[str] = None,
                        message: Optional[str] = None,
                        topic: Optional[str] = None,
                        interaction_id_pendiente: Optional[str] = None):
        """
        Dispatcher polimórfico (v1.1).

        modo="inbound":
            Requiere `recipient` y `message`. Comportamiento clásico: busca la
            ferretería por teléfono, valida veto y ejecuta pasos 2–6 con el
            mensaje del cliente.
            ✅ v1.4.2: si viene `interaction_id_pendiente`, NO se crea una
            nueva fila en historial_interacciones — se hace UPDATE sobre la
            fila existente (que se creó al recibir el mensaje del usuario,
            con respuesta_ia=NULL). Esto evita duplicar el mensaje del
            usuario en historial cuando hubo coalescencia.

        modo="outreach":
            Requiere `topic`. Itera ferreterías con estado=NULL no vetadas,
            construye user_message desde topic, ejecuta pasos 2–6 por cada una
            y transiciona None → primer_mensaje al final.
        """
        if modo == "inbound":
            if recipient is None or message is None:
                logger.error("_handle_ai_flow inbound: recipient y message son obligatorios")
                return
            self._handle_ai_flow_inbound(
                recipient, message,
                interaction_id_pendiente=interaction_id_pendiente,
            )
        elif modo == "outreach":
            if topic is None:
                logger.error("_handle_ai_flow outreach: topic es obligatorio")
                return
            self._handle_ai_flow_outreach(topic)
        else:
            logger.error(f"_handle_ai_flow: modo desconocido {modo!r}")

    # ------------------------------------------------------------------
    # MODO INBOUND (reactivo): mensaje entrante desde webhook
    # ------------------------------------------------------------------
    def _handle_ai_flow_inbound(self, recipient: str, message: str,
                                 interaction_id_pendiente: Optional[str] = None):
        try:
            ferreteria = self.db_manager.obtener_ferreteria_por_telefono(recipient)

            if not ferreteria:
                logger.warning(
                    f"Mensaje de número desconocido {mask_phone(recipient)}. "
                    "El bot no está configurado para crear nuevas ferreterías automáticamente. Ignorando mensaje."
                )
                return

            if self.db_manager.is_phone_vetoed(ferreteria.id_ferreteria, recipient):
                logger.info(f"Mensaje ignorado (número vetado)")
                return

            self._ejecutar_pasos_2_a_6(
                ferreteria=ferreteria,
                recipient=recipient,
                user_message=message,
                modo="inbound",
                interaction_id_pendiente=interaction_id_pendiente,
            )
        except Exception as e:
            logger.error(f"Error en flujo inbound: {e}")

    # ------------------------------------------------------------------
    # MODO OUTREACH (proactivo): saluda a ferreterías con estado=NULL
    # ------------------------------------------------------------------
    def _handle_ai_flow_outreach(self, topic: str):
        """
        Itera ferreterías candidatas (estado IS NULL y no vetadas), genera el
        primer mensaje desde `topic`, ejecuta pasos 2–6 y transiciona None →
        primer_mensaje al final de cada ferretería.

        El gate horario se chequea ANTES de invocar este método (en el cron
        del BroadcastScheduler, decisión 2a+2c). Aquí asumimos que el bot
        está dentro de ventana.
        """
        logger.info(f"🚀 Outreach iniciado — topic: {topic!r}")
        # Snapshot de candidatas en memoria para evitar mantener una sesión
        # de BD abierta durante todo el bucle.
        candidatas_data: List[Tuple[Any, str]] = []
        try:
            with self.db_manager.get_session() as session:
                candidatas = session.query(Ferreteria).filter(
                    Ferreteria.estado.is_(None)
                ).all()
                # Materializar atributos necesarios antes de cerrar la sesión
                for ferr in candidatas:
                    if ferr.num_telefono in (ferr.num_vetados or []):
                        continue
                    # Capturamos un dict-like con los campos que necesitamos
                    candidatas_data.append({
                        "id_ferreteria": ferr.id_ferreteria,
                        "num_telefono": ferr.num_telefono,
                        "regional": ferr.regional,
                        "cod_municipio": ferr.cod_municipio,
                    })
            logger.info(
                f"📋 Outreach: {len(candidatas_data)} ferreterías candidatas "
                f"(estado=NULL y no vetadas)"
            )
        except Exception as e:
            logger.error(f"Outreach: error consultando candidatas: {e}")
            return

        # user_message a partir del topic (mismo patrón que tenía
        # BroadcastScheduler antes del refactor)
        user_message = f"Genera un saludo informativo sobre: {topic}"

        enviadas = 0
        falladas = 0
        for cdata in candidatas_data:
            try:
                # Re-cargar la ferretería en una sesión nueva para evitar
                # session detached al transicionar al final.
                ferreteria = self.db_manager.obtener_ferreteria_por_telefono(
                    cdata["num_telefono"]
                )
                if not ferreteria:
                    logger.warning(
                        f"Outreach: ferretería desapareció entre snapshot "
                        f"y carga ({mask_phone(cdata['num_telefono'])}); skip"
                    )
                    falladas += 1
                    continue

                self._ejecutar_pasos_2_a_6(
                    ferreteria=ferreteria,
                    recipient=ferreteria.num_telefono,
                    user_message=user_message,
                    modo="outreach",
                )

                # Tras encolar (ya hizo el dispatcher.enqueue dentro del paso 5),
                # transicionamos None → primer_mensaje. Esto es lo que antes
                # hacía BroadcastScheduler._run_broadcast_job.
                self.db_manager.transicionar_estado(
                    ferreteria.id_ferreteria, EstadoFereteria.primer_mensaje
                )
                enviadas += 1
            except Exception as e:
                logger.error(
                    f"Outreach: error procesando "
                    f"{mask_phone(cdata['num_telefono'])}: {e}"
                )
                falladas += 1
                continue

        logger.info(
            f"🏁 Outreach finalizado: {enviadas} enviadas, {falladas} fallidas"
        )

    # ------------------------------------------------------------------
    # PASOS 2–6 COMPARTIDOS
    # Idénticos para ambos modos. La única diferencia es que en outreach el
    # `estado` que se inyecta al prompt es siempre "primer_mensaje" (porque
    # la ferretería viene de NULL), mientras que en inbound usamos el estado
    # actual.
    # ------------------------------------------------------------------
    def _ejecutar_pasos_2_a_6(self, ferreteria, recipient: str,
                              user_message: str, modo: str,
                              interaction_id_pendiente: Optional[str] = None):
        """
        Pasos 2–6 del flujo (comunes a outreach e inbound):
          2) Recuperar historial reciente
          3) get_response (BASE+REGION+ESTADO+HISTORIAL)
          4) Persistir interacción:
              - outreach: INSERT vía guardar_interaccion
              - inbound con interaction_id_pendiente: UPDATE vía
                actualizar_respuesta_ia (✅ v1.4.2: la fila ya existe,
                creada al recibir el último mensaje del burst)
              - inbound sin interaction_id_pendiente: INSERT vía
                guardar_interaccion (path legacy de seguridad)
          5) dispatcher.enqueue (sin delay; respeta solo inter_chat + gate)
          6) Si estado NO es {cierre, terminado}: extract_quote_info + transiciones
        """
        try:
            estado_actual = ferreteria.estado
            # En outreach, el estado para el prompt SIEMPRE es "primer_mensaje"
            # (la ferretería todavía no ha sido contactada, viene de NULL).
            # En inbound, usamos el estado real.
            if modo == "outreach":
                estado_nombre_prompt = "primer_mensaje"
            else:
                estado_nombre_prompt = (
                    estado_actual.value if hasattr(estado_actual, 'value')
                    else str(estado_actual)
                )
            region_nombre = ferreteria.regional

            # 2) Recuperar historial conversacional
            limite = getattr(self.ai_client.config, 'history_limit', 10)
            historial = self.db_manager.obtener_historial_reciente(
                id_ferreteria=str(ferreteria.id_ferreteria),
                limite=limite
            )

            # ✅ v1.3: bifurcación inbound vs outreach.
            # En outreach (primer contacto) Meta NO permite texto libre si el
            # cliente no ha escrito en las últimas 24h, así que usamos la
            # plantilla aprobada "saludo" con un único parámetro {{1}}
            # generado por Claude a partir del catálogo de productos de cemento.
            if modo == "outreach":
                # 3-out) Obtener catálogo de productos cemento desde BD.
                productos_catalogo: List[str] = []
                try:
                    with self.db_manager.get_session() as session:
                        for p in session.query(Producto).all():
                            nombre_norm = self.db_manager._normalizar_nombre(p.nombre)
                            if "cemento" in nombre_norm:
                                productos_catalogo.append(p.nombre)
                except Exception as e:
                    logger.warning(
                        f"Outreach: no se pudo leer catálogo de productos: {e}; "
                        f"se usará fallback en generate_outreach_param"
                    )

                # 3-out) Generar el parámetro {{1}} con la IA.
                outreach_param = self.ai_client.generate_outreach_param(
                    productos_disponibles=productos_catalogo,
                    region=region_nombre,
                )

                # Renderizar el texto que verá el cliente para guardarlo en
                # historial (la plantilla literal aprobada en Meta).
                template_rendered = (
                    "Hola, buen día. Quisiera consultar si tienen disponibilidad "
                    f"de: {outreach_param}. Además, ¿me podrían confirmar el "
                    "precio? Quedo atento, muchas gracias."
                )

                # 4-out) Persistir interacción. Marcamos con [OUTREACH-TEMPLATE]
                # para distinguir de outreach antiguo de texto libre en logs.
                interaction_id = self.db_manager.guardar_interaccion(
                    id_ferreteria=str(ferreteria.id_ferreteria),
                    mensaje_usuario=f"[OUTREACH-TEMPLATE] {user_message}",
                    respuesta_ia=template_rendered,
                )

                # 5-out) Encolar PLANTILLA (no texto libre) respetando delays + gate.
                # ✅ CORRECCIÓN: garantizar que dispatcher siempre existe en outreach
                template_name = self.ai_client.config.outreach_template_name
                template_lang = self.ai_client.config.outreach_template_lang
                if self.dispatcher is None:
                    raise RuntimeError(
                        "❌ MessageProcessor requiere dispatcher configurado para outreach. "
                        "Verifica bootstrap en CELDA 11: "
                        "dispatcher = MessageDispatcher(wa_client, dispatcher_config, hours_gate=hours_gate)"
                    )

                self.dispatcher.enqueue_template(
                    recipient=recipient,
                    template_name=template_name,
                    language_code=template_lang,
                    body_params=[outreach_param],
                )
            else:
                # ── Modo inbound (sin cambios respecto a v1.1) ──
                # 3) Generar respuesta del bot (BASE+REGION+ESTADO+HISTORIAL)
                ai_response = self.ai_client.get_response(
                    user_message=user_message,
                    region=region_nombre,
                    estado=estado_nombre_prompt,
                    historial=historial,
                )

                # 4) Persistir interacción:
                # - Si vino interaction_id_pendiente (caso coalescencia v1.4.2),
                #   UPDATE sobre la fila existente, o INSERT con MISMO UUID si
                #   la fila no existe (v1.4.3, vía fallbacks).
                # - Si no vino, fallback al INSERT clásico (path defensivo:
                #   sucede si el caller invocó el flujo sin pasar por
                #   process_incoming, p. ej. tests manuales o llamadas directas).
                if interaction_id_pendiente:
                    ok = self.db_manager.actualizar_respuesta_ia(
                        id_interaccion=interaction_id_pendiente,
                        respuesta_ia=ai_response,
                        # ✅ v1.4.3: fallbacks para UPSERT semántico. Si la fila
                        # no existe (reinicio del kernel previo), se reinsertará
                        # con el MISMO UUID en lugar de crear duplicado.
                        mensaje_usuario_fallback=user_message,
                        id_ferreteria_fallback=str(ferreteria.id_ferreteria),
                    )
                    if ok:
                        interaction_id = interaction_id_pendiente
                    else:
                        # Solo entra aquí si actualizar_respuesta_ia retorna False,
                        # lo que con los fallbacks v1.4.3 NO debería pasar nunca.
                        # Mantenemos el path por completitud defensiva.
                        logger.error(
                            f"actualizar_respuesta_ia retornó False con fallbacks "
                            f"presentes para {interaction_id_pendiente} — fallback "
                            f"a guardar_interaccion clásico"
                        )
                        interaction_id = self.db_manager.guardar_interaccion(
                            id_ferreteria=str(ferreteria.id_ferreteria),
                            mensaje_usuario=user_message,
                            respuesta_ia=ai_response,
                        )
                else:
                    interaction_id = self.db_manager.guardar_interaccion(
                        id_ferreteria=str(ferreteria.id_ferreteria),
                        mensaje_usuario=user_message,
                        respuesta_ia=ai_response,
                    )

                # 5) Encolar respuesta. ✅ v1.4.2: dispatcher.enqueue ya NO
                # aplica delay artificial — la ventana de escucha ya absorbió
                # el 'tiempo humano' antes de generar.
                if self.dispatcher is not None:
                    self.dispatcher.enqueue(recipient, ai_response)
                else:
                    self.wa_client.send_text_message(recipient, ai_response)

            # 6) Aplicar transiciones de estado
            #    En outreach NO ejecutamos el extractor: el user_message es
            #    sintético (no viene del cliente), no aporta señales reales.
            #    La transición None → primer_mensaje la hace el caller
            #    (_handle_ai_flow_outreach) tras esta función.
            if modo == "outreach":
                logger.info(
                    f"⏭️  Outreach: extractor omitido (user_message sintético)"
                )
                return

            # ── A partir de aquí: solo modo inbound ──
            if estado_actual in self.ESTADOS_SIN_EXTRACTOR:
                logger.info(
                    f"⏭️  Extractor saltado "
                    f"(estado={estado_nombre_prompt}, no aporta señales)"
                )
                return

            if self.extraction_client and interaction_id:
                self._aplicar_transiciones(
                    ferreteria=ferreteria,
                    estado_actual=estado_actual,
                    user_message=user_message,
                    ai_response=ai_response,
                    interaction_id=interaction_id,
                )
        except Exception as e:
            logger.error(f"Error en pasos 2–6 ({modo}): {e}")

    # ------------------------------------------------------------------
    # Lógica de transiciones (centralizada y testeable). Solo se invoca en
    # modo inbound; en outreach el extractor no aporta señales útiles.
    # ------------------------------------------------------------------
    def _aplicar_transiciones(self, ferreteria, estado_actual,
                              user_message: str,
                              ai_response: str, interaction_id: str):
        """
        Aplica las transiciones según las reglas de negocio.

        ✅ v1.4: ahora usa DOS extractores complementarios:
          - ExtractorTextoAcumulativo (determinista + fallback LLM acotado)
            → para detectar la transición `inicio → cotizacion`
            → reconstruye el estado del HISTORIAL completo, no solo último turno
          - AnthropicExtractionClient (LLM de un solo turno)
            → para detectar `es_despedida` y `confirma_cierre`
              (juicio semántico genuino)

        Si el extractor acumulativo no está configurado, cae al comportamiento
        anterior (LLM puro de un solo turno), preservando compatibilidad.
        """
        try:
            # Recuperar historial UNA sola vez para que ambos extractores lo usen.
            historial: List[Dict[str, str]] = []
            try:
                limite = getattr(self.ai_client.config, 'history_limit', 10)
                historial = self.db_manager.obtener_historial_reciente(
                    id_ferreteria=str(ferreteria.id_ferreteria),
                    limite=limite,
                )
            except Exception as e:
                logger.warning(f"No se pudo cargar historial para extractor: {e}")

            # ── EXTRACCIÓN A: cotización (acumulativo si disponible) ──────
            estado_acumulado: Optional[EstadoExtraccionAcumulado] = None
            if self.extractor_acumulativo is not None:
                try:
                    estado_acumulado = self.extractor_acumulativo.extraer_de_historial(
                        historial=historial,
                        mensaje_actual=user_message,
                        usar_llm_fallback=True,
                    )
                except Exception as e:
                    logger.error(f"Extractor acumulativo falló: {e}")

            # ── EXTRACCIÓN B: señales semánticas (despedida / cierre) ─────
            extraccion_llm = None
            if self.extraction_client is not None:
                extraccion_llm = self.extraction_client.extract_quote_info(
                    mensaje_ferreteria=user_message,
                    respuesta_bot=ai_response,
                    interaction_id=interaction_id,
                    historial=historial,  # ← v1.4: ahora se pasa historial
                )

            # 1) Despedida → terminado (forzado, desde cualquier estado)
            if extraccion_llm and extraccion_llm.get("es_despedida"):
                self.db_manager.transicionar_estado(
                    ferreteria.id_ferreteria,
                    EstadoFereteria.terminado,
                    forzar=True,
                )
                return

            # 2) primer_mensaje → inicio (la ferretería respondió, sea por broadcast
            #    o por mensaje espontáneo recién creada).
            if estado_actual == EstadoFereteria.primer_mensaje:
                ok = self.db_manager.transicionar_estado(
                    ferreteria.id_ferreteria, EstadoFereteria.inicio
                )
                if ok:
                    estado_actual = EstadoFereteria.inicio

            # 2b) sin_respuesta → inicio (la ferretería volvió a escribir tras
            #    el timeout 7d).
            if estado_actual == EstadoFereteria.sin_respuesta:
                ok = self.db_manager.transicionar_estado(
                    ferreteria.id_ferreteria, EstadoFereteria.inicio
                )
                if ok:
                    estado_actual = EstadoFereteria.inicio

            # 3) inicio → cotizacion. PRIORIZAR extractor acumulativo.
            if estado_actual == EstadoFereteria.inicio:
                cotizacion_lista = False
                producto_nombre = marca_nombre = None
                precio = None
                disponibilidad = None
                confianza = None
                info_solicitada = None

                if estado_acumulado and estado_acumulado.es_completo():
                    # Solo aceptar precios plausibles (no sospechosos por rango)
                    if not estado_acumulado.precio_sospechoso:
                        cotizacion_lista = True
                        producto_nombre = estado_acumulado.producto_nombre
                        marca_nombre = estado_acumulado.marca_nombre
                        precio = float(estado_acumulado.precio_unitario)
                        disponibilidad = estado_acumulado.disponibilidad
                        confianza = max(
                            estado_acumulado.producto_score,
                            estado_acumulado.marca_score,
                        )
                        info_solicitada = (
                            f"Acumulativo: {estado_acumulado.resumen()} | "
                            f"fuentes={estado_acumulado.fuentes}"
                        )
                        logger.info(
                            f"✅ Transición inicio→cotizacion vía acumulativo: "
                            f"{estado_acumulado.resumen()}"
                        )
                    else:
                        logger.warning(
                            f"⚠️  Cotización completa pero precio SOSPECHOSO: "
                            f"{estado_acumulado.resumen()} — no se transiciona"
                        )

                # Fallback al extractor LLM si el acumulativo no está disponible
                if (not cotizacion_lista
                        and self.extractor_acumulativo is None
                        and extraccion_llm
                        and AnthropicExtractionClient.tiene_cotizacion_completa(extraccion_llm)):
                    cotizacion_lista = True
                    producto_nombre = extraccion_llm.get("producto")
                    marca_nombre = extraccion_llm.get("marca")
                    precio = float(extraccion_llm.get("precio_unitario"))
                    disponibilidad = extraccion_llm.get("disponibilidad")
                    confianza = extraccion_llm.get("confianza")
                    info_solicitada = extraccion_llm.get("observaciones")
                    logger.info("Transición inicio→cotizacion vía LLM (fallback)")

                if cotizacion_lista:
                    cot_id = self.db_manager.registrar_cotizacion(
                        id_interaccion=interaction_id,
                        id_ferreteria=str(ferreteria.id_ferreteria),
                        producto_nombre=producto_nombre,
                        marca_nombre=marca_nombre,
                        precio=precio,
                        regional=ferreteria.regional,
                        disponibilidad=disponibilidad,
                        confianza=confianza,
                        info_solicitada=info_solicitada,
                        cod_municipio=ferreteria.cod_municipio,
                    )
                    if cot_id:
                        self.db_manager.transicionar_estado(
                            ferreteria.id_ferreteria, EstadoFereteria.cotizacion
                        )
                        estado_actual = EstadoFereteria.cotizacion

            # 4) cotizacion → cierre (cliente confirma envío de cotización
            #    formal o pedido por TEXTO)
            if (estado_actual == EstadoFereteria.cotizacion
                    and extraccion_llm
                    and AnthropicExtractionClient.tiene_confirmacion_cierre(extraccion_llm)):
                self.db_manager.transicionar_estado(
                    ferreteria.id_ferreteria, EstadoFereteria.cierre
                )
        except Exception as e:
            logger.error(f"Error aplicando transiciones: {e}")


__all__ = ["MessageProcessor"]
