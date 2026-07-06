"""Gestor de base de datos (migrado de la celda 6 del notebook).

`DatabaseManager` centraliza todas las operaciones de BD con context managers,
reintentos y la lógica de idempotencia de webhooks. Se conservan las firmas y
el comportamiento del notebook; se añade `from_settings` y un accessor cacheado
para integrarlo con la configuración de FastAPI.
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import asc, create_engine, desc
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.utils.text import retry_on_failure
from app.models import (
    Cotizacion,
    EstadoFereteria,
    Ferreteria,
    Geografia,
    HistorialInteraccion,
    HistorialInteraccionAntiguo,
    MarcaProducto,
    Producto,
    Regional,
    TRANSICIONES_VALIDAS,
    WebhookEventoProcesado,
)

logger = get_logger(__name__)




class DatabaseManager:
    """Gestor centralizado de operaciones de BD con context managers"""

    def __init__(self, db_user: str, db_password: str, db_host: str, db_name: str):
        self.db_url = (
            f"postgresql://{db_user}:{db_password}@{db_host}/{db_name}"
            f"?options=-c%20search_path=public"
        )
        self.engine = create_engine(
            self.db_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600
        )
        self.Session = sessionmaker(bind=self.engine)
        logger.info("✅ DatabaseManager inicializado")

    @contextmanager
    def get_session(self):
        """Context manager para sesiones de BD con rollback automático"""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error en BD: {e}")
            raise
        finally:
            session.close()

    def _get_session(self):
        return self.Session()

    @staticmethod
    def _normalizar_nombre(nombre: str) -> str:
        """Normaliza un nombre para búsqueda case-insensitive y sin tildes."""
        if not nombre:
            return ""
        s = ''.join(
            c for c in unicodedata.normalize('NFD', nombre)
            if unicodedata.category(c) != 'Mn'
        )
        return re.sub(r'\s+', ' ', s).strip().lower()

    @retry_on_failure(max_retries=3)
    def obtener_ferreteria_por_telefono(self, numero: str) -> Optional[Ferreteria]:
        session = self._get_session()
        try:
            ferreteria = session.query(Ferreteria).filter(
                Ferreteria.num_telefono == numero
            ).first()
            return ferreteria
        except Exception as e:
            logger.error(f"Error buscando ferretería: {e}")
            return None
        finally:
            session.close()

    @retry_on_failure(max_retries=3)
    def crear_ferreteria_minima(self, numero: str) -> Ferreteria:
        """
        ✅ FIX 2.2: Crea una ferretería mínima cuando llega un mensaje de un
        número desconocido. El estado inicial es `primer_mensaje` (no `inicio`).

        Razón: el flujo unificado es siempre None → primer_mensaje → inicio → ...
        En el MISMO turno en que se crea, MessageProcessor llamará a
        `transicionar_estado(..., inicio)` para reflejar que la ferretería
        ya respondió. De esta forma:
          - Auditable: quedan 2 entradas de log (None→primer_mensaje y
            primer_mensaje→inicio).
          - Consistente con el grafo TRANSICIONES_VALIDAS.
          - Se trata igual una ferretería iniciada por broadcast que una
            iniciada por mensaje espontáneo.
        """
        session = self._get_session()
        try:
            regional_default = session.query(Regional).filter_by(regional="CENTRO").first()
            geografia_default = session.query(Geografia).filter_by(cod_municipio="05001").first()
            if not regional_default or not geografia_default:
                raise ValueError("Datos por defecto no encontrados.")
            nueva = Ferreteria(
                num_telefono=numero,
                num_vetados=[],
                nombre_ferreteria="Ferretería (registro automático)",
                cod_municipio=geografia_default.cod_municipio,
                regional=regional_default.regional,
                estado=EstadoFereteria.primer_mensaje  # ✅ FIX 2.2: era `inicio`
            )
            session.add(nueva)
            session.commit()
            logger.info(f"🆕 Ferretería creada en estado primer_mensaje: {nueva.id_ferreteria}")
            return nueva
        except Exception as e:
            session.rollback()
            logger.error(f"Error creando ferretería: {e}")
            raise
        finally:
            session.close()

    @retry_on_failure(max_retries=3)
    def guardar_interaccion(self, id_ferreteria: str, mensaje_usuario: str,
                            respuesta_ia: str, tokens: Optional[int] = None) -> Optional[str]:
        session = self._get_session()
        try:
            nueva = HistorialInteraccion(
                id_ferreteria=uuid.UUID(id_ferreteria),
                mensaje_usuario=mensaje_usuario,
                respuesta_ia=respuesta_ia,
                tokens_consumidos=tokens
            )
            session.add(nueva)
            session.commit()
            logger.info(f"📊 Interacción guardada: {nueva.id_interaccion}")
            return str(nueva.id_interaccion)
        except Exception as e:
            session.rollback()
            logger.error(f"Error guardando interacción: {e}")
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # ✅ v1.4.2: dos métodos auxiliares para el flujo inbound coalescido.
    # El mensaje del usuario se persiste inmediatamente al llegar (sin
    # respuesta_ia) para que los mensajes siguientes del mismo burst lo
    # vean en el historial. Cuando finalmente la IA genera la respuesta,
    # se hace UPDATE sobre la fila existente.
    #
    # ✅ v1.4.3:
    # - guardar_mensaje_usuario: verifica con SELECT round-trip post-commit
    #   que la fila realmente se persistió. Detecta commits zombi (típicos
    #   tras reinicio del kernel con conexiones del pool en estado raro).
    # - actualizar_respuesta_ia: si la fila no existe (caso observado en
    #   producción tras reinicio), hace INSERT con el MISMO UUID en lugar
    #   de crear duplicado con id distinto. Patrón UPSERT semántico.
    #
    # obtener_historial_reciente ya filtra correctamente las filas con
    # respuesta_ia=NULL (solo se inyecta el rol 'user', no un 'assistant'
    # vacío), así que el comportamiento de prompts NO se ve afectado.
    # ------------------------------------------------------------------
    @retry_on_failure(max_retries=3)
    def guardar_mensaje_usuario(self, id_ferreteria: str,
                                 mensaje_usuario: str) -> Optional[str]:
        """INSERT con respuesta_ia=NULL. Retorna interaction_id.

        ✅ v1.4.3: tras commit, hace SELECT de verificación en la MISMA
        sesión. Si la fila no aparece, lanza excepción (el retry decorator
        reintenta, y si todos los intentos fallan, propaga). Esto cierra
        el bug de 'UUID válido en memoria pero fila inexistente en BD'
        que producía warnings al hacer UPDATE más tarde.
        """
        session = self._get_session()
        try:
            nueva = HistorialInteraccion(
                id_ferreteria=uuid.UUID(id_ferreteria),
                mensaje_usuario=mensaje_usuario,
                respuesta_ia=None,
                tokens_consumidos=None,
            )
            session.add(nueva)
            session.commit()
            # ✅ v1.4.3: verificación explícita post-commit. Forzamos un
            # SELECT round-trip. Si la BD no tiene la fila, algo está mal
            # con la conexión y debemos saberlo AHORA, no 5 minutos después
            # cuando intentemos UPDATE.
            verificacion = (
                session.query(HistorialInteraccion)
                .filter(HistorialInteraccion.id_interaccion == nueva.id_interaccion)
                .first()
            )
            if verificacion is None:
                raise RuntimeError(
                    f"guardar_mensaje_usuario: commit retornó OK pero la fila "
                    f"{nueva.id_interaccion} no es visible en BD. Conexión "
                    f"posiblemente en estado inválido."
                )
            logger.info(
                f"📨 Mensaje usuario persistido (pendiente respuesta IA): "
                f"{nueva.id_interaccion}"
            )
            return str(nueva.id_interaccion)
        except Exception as e:
            session.rollback()
            logger.error(f"Error guardando mensaje de usuario: {e}")
            raise
        finally:
            session.close()

    @retry_on_failure(max_retries=3)
    def actualizar_respuesta_ia(self, id_interaccion: str,
                                 respuesta_ia: str,
                                 tokens: Optional[int] = None,
                                 mensaje_usuario_fallback: Optional[str] = None,
                                 id_ferreteria_fallback: Optional[str] = None) -> bool:
        """UPDATE de la fila previamente creada por guardar_mensaje_usuario.

        ✅ v1.4.3: si la fila NO existe (caso de reinicio del kernel que
        invalidó la transacción previa), y se proveen los fallbacks de
        mensaje_usuario_fallback + id_ferreteria_fallback, hacemos INSERT
        preservando el MISMO UUID. Esto evita generar una fila zombi nueva
        y mantiene coherencia con cualquier referencia que ya use ese UUID
        (p. ej., tabla cotizaciones cuya FK apunta a id_interaccion).

        Retorna True si el resultado es una fila completa con ese UUID.
        Solo retorna False si los fallbacks no se proveyeron y la fila no
        existía (el caller hará otra cosa).
        """
        session = self._get_session()
        try:
            fila = (
                session.query(HistorialInteraccion)
                .filter(HistorialInteraccion.id_interaccion == uuid.UUID(id_interaccion))
                .first()
            )
            if fila is None:
                # Fila no existe: caso de reinicio o INSERT que falló silencioso.
                # Si tenemos los datos para reconstruirla, hacemos INSERT con
                # el mismo UUID — preserva referencias y elimina ruido.
                if mensaje_usuario_fallback and id_ferreteria_fallback:
                    logger.warning(
                        f"actualizar_respuesta_ia: fila {id_interaccion} no existe; "
                        f"insertando con MISMO UUID (probable reinicio previo)"
                    )
                    nueva = HistorialInteraccion(
                        id_interaccion=uuid.UUID(id_interaccion),
                        id_ferreteria=uuid.UUID(id_ferreteria_fallback),
                        mensaje_usuario=mensaje_usuario_fallback,
                        respuesta_ia=respuesta_ia,
                        tokens_consumidos=tokens,
                    )
                    session.add(nueva)
                    session.commit()
                    logger.info(
                        f"📊 Fila recreada con UUID original: {id_interaccion}"
                    )
                    return True
                logger.warning(
                    f"actualizar_respuesta_ia: interacción {id_interaccion} "
                    f"no existe y sin fallbacks; UPDATE descartado"
                )
                return False
            fila.respuesta_ia = respuesta_ia
            if tokens is not None:
                fila.tokens_consumidos = tokens
            session.commit()
            logger.info(f"📊 Respuesta IA actualizada en {id_interaccion}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error actualizando respuesta IA: {e}")
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # ✅ v1.4.3: rehidratación de mensajes inbound huérfanos tras reinicio
    # ------------------------------------------------------------------
    @retry_on_failure(max_retries=3)
    def obtener_inbounds_pendientes(self, ventana_minutos: int = 30) -> List[Dict]:
        """
        Lista las filas de historial con respuesta_ia=NULL de los últimos
        `ventana_minutos` minutos. Estas son los mensajes del cliente que
        ENTRARON al bot, se persistieron en BD, pero la respuesta nunca
        se generó (típicamente porque el bot se reinició durante la
        ventana de listen_window antes de que el timer venciera).

        Se usa al arranque del bot para reagendar los inbound_intent
        correspondientes — así no quedan clientes sin respuesta.

        Devuelve lista de dicts:
          [{'id_interaccion': str, 'id_ferreteria': str,
            'numero': str, 'mensaje': str, 'fecha': datetime}, ...]
        ordenados cronológicamente (más antiguo primero). Solo incluye
        ferreterías NO vetadas y con número de teléfono válido.
        """
        session = self._get_session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=ventana_minutos)
            filas = (
                session.query(HistorialInteraccion, Ferreteria)
                .join(
                    Ferreteria,
                    Ferreteria.id_ferreteria == HistorialInteraccion.id_ferreteria,
                )
                .filter(HistorialInteraccion.respuesta_ia.is_(None))
                .filter(HistorialInteraccion.fecha_registro >= cutoff)
                .filter(Ferreteria.num_telefono.isnot(None))
                .order_by(HistorialInteraccion.fecha_registro.asc())
                .all()
            )
            # Excluir entradas de outreach (marcadas en mensaje_usuario con
            # el prefijo '[OUTREACH-TEMPLATE]'). Solo nos interesan los
            # mensajes reales del cliente.
            resultado = []
            for interaccion, ferreteria in filas:
                msg = interaccion.mensaje_usuario or ""
                if msg.startswith("[OUTREACH-TEMPLATE]"):
                    continue
                resultado.append({
                    "id_interaccion": str(interaccion.id_interaccion),
                    "id_ferreteria": str(ferreteria.id_ferreteria),
                    "numero": ferreteria.num_telefono,
                    "mensaje": msg,
                    "fecha": interaccion.fecha_registro,
                })
            logger.info(
                f"🔎 Inbounds pendientes detectados (últimos {ventana_minutos}min): "
                f"{len(resultado)}"
            )
            return resultado
        except Exception as e:
            logger.error(f"Error obteniendo inbounds pendientes: {e}")
            return []
        finally:
            session.close()

    @retry_on_failure(max_retries=3)
    def marcar_inbound_perdido(self, id_interaccion: str) -> bool:
        """
        Marca una fila huérfana antigua con un placeholder en respuesta_ia.
        Se usa para inbounds anteriores a la ventana de rehidratación: no
        tiene sentido responder a algo de hace 2 horas (el cliente
        seguramente ya se aburrió y se fue), pero tampoco queremos dejar
        la fila eternamente NULL porque ensucia las queries.

        Tras marcar, esa fila SÍ aparecerá en obtener_historial_reciente
        como turno 'assistant' con el placeholder. Si no quieres eso,
        deja respuesta_ia NULL — el filtro de historial igual lo ignora.
        """
        session = self._get_session()
        try:
            fila = (
                session.query(HistorialInteraccion)
                .filter(HistorialInteraccion.id_interaccion == uuid.UUID(id_interaccion))
                .first()
            )
            if fila is None:
                return False
            fila.respuesta_ia = "[RESPUESTA PERDIDA POR REINICIO DEL BOT]"
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error marcando inbound perdido: {e}")
            raise
        finally:
            session.close()

    @retry_on_failure(max_retries=3)
    def obtener_historial_reciente(self, id_ferreteria: str,
                                    limite: int = 10) -> List[Dict[str, str]]:
        """
        Devuelve las últimas `limite` interacciones de una ferretería ordenadas
        cronológicamente (más antigua primero), en formato listo para inyectar
        en messages.create() de Anthropic.
        """
        session = self._get_session()
        try:
            registros = (
                session.query(HistorialInteraccion)
                .filter(HistorialInteraccion.id_ferreteria == uuid.UUID(id_ferreteria))
                .order_by(HistorialInteraccion.fecha_registro.desc())
                .limit(limite)
                .all()
            )
            registros = list(reversed(registros))
            historial: List[Dict[str, str]] = []
            for r in registros:
                if r.mensaje_usuario:
                    historial.append({"role": "user", "content": r.mensaje_usuario})
                if r.respuesta_ia:
                    historial.append({"role": "assistant", "content": r.respuesta_ia})
            logger.info(f"📚 Historial recuperado: {len(registros)} interacciones "
                        f"({len(historial)} mensajes)")
            return historial
        except Exception as e:
            logger.error(f"Error obteniendo historial: {e}")
            return []
        finally:
            session.close()

    # ------------------------------------------------------------------
    # ✅ FIX 2.7: Idempotencia de webhooks persistente en BD
    # Reemplaza al set in-memory que se perdía en cada reinicio del notebook.
    # ------------------------------------------------------------------
    def msg_ya_procesado(self, msg_id: str) -> bool:
        """
        Verifica si un msg_id ya fue procesado. Si NO existe, lo inserta
        atómicamente y devuelve False. Si ya existe, devuelve True.

        Usa INSERT con ON CONFLICT DO NOTHING para que la inserción y la
        verificación sean una sola operación atómica (sin race condition
        entre dos webhooks duplicados llegando casi simultáneos).

        Si la BD falla, devuelve False (no bloqueamos al cliente por un
        problema de infra) y dejamos que el set in-memory haga de respaldo
        a nivel de MessageProcessor.
        """
        if not msg_id:
            return False
        try:
            with self.get_session() as session:
                # Postgres-specific: INSERT ... ON CONFLICT
                result = session.execute(
                    sa_text(
                        "INSERT INTO public.webhook_events_processed (msg_id) "
                        "VALUES (:mid) ON CONFLICT (msg_id) DO NOTHING "
                        "RETURNING msg_id"
                    ),
                    {"mid": msg_id}
                )
                inserted = result.first() is not None
                # Si insertó, no estaba antes → no procesado
                # Si no insertó, ya existía → ya procesado
                return not inserted
        except Exception as e:
            logger.warning(f"msg_ya_procesado falló (asumiendo no procesado): {e}")
            return False

    def purgar_webhook_events_antiguos(self, days: int = 1) -> int:
        """Limpia eventos de webhook >24h. Meta no reintenta después de eso."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            with self.get_session() as session:
                eliminados = session.query(WebhookEventoProcesado).filter(
                    WebhookEventoProcesado.fecha_proceso < cutoff
                ).delete(synchronize_session=False)
                logger.info(f"🧹 Purgados {eliminados} webhook_events antiguos (>{days}d)")
                return eliminados
        except Exception as e:
            logger.error(f"Error purgando webhook_events: {e}")
            return 0

    # ------------------------------------------------------------------
    # Helpers para resolver/crear FKs del extractor
    # ------------------------------------------------------------------
    @retry_on_failure(max_retries=3)
    def obtener_producto_por_nombre(self, nombre: str) -> Optional[uuid.UUID]:
        """
        Busca un producto por nombre normalizado (sin tildes, case-insensitive).
        ⚠️ NO crea productos: el catálogo `productos` es cerrado. Si no existe,
        devuelve None y la cotización se debe descartar/loguear.
        """
        if not nombre or not nombre.strip():
            return None
        nombre_norm = self._normalizar_nombre(nombre)
        with self.get_session() as session:
            for p in session.query(Producto).all():
                if self._normalizar_nombre(p.nombre) == nombre_norm:
                    return p.id_producto
        logger.info(f"⚠️ Producto '{nombre}' no existe en catálogo, no se crea")
        return None

    @retry_on_failure(max_retries=3)
    def obtener_producto_cemento(self) -> Optional[Tuple[uuid.UUID, str]]:
        """
        Localiza el producto 'cemento' en el catálogo con MATCH FLEXIBLE:
        cualquier producto cuyo nombre normalizado CONTENGA la palabra 'cemento'.
        Ej: 'Cemento gris', 'Cemento Argos', 'CEMENTO PORTLAND' → match.

        Devuelve (id_producto, nombre_real) del primero encontrado, o None.
        Si hay varios candidatos, prioriza el más corto (más genérico) y loguea
        los demás para que se sepa cuál se usó.
        """
        with self.get_session() as session:
            candidatos = []
            for p in session.query(Producto).all():
                if "cemento" in self._normalizar_nombre(p.nombre):
                    candidatos.append((p.id_producto, p.nombre))
            if not candidatos:
                logger.warning("⚠️ No hay producto 'cemento' en el catálogo de `productos`")
                return None
            candidatos.sort(key=lambda c: len(c[1]))
            elegido = candidatos[0]
            if len(candidatos) > 1:
                otros = ", ".join(c[1] for c in candidatos[1:])
                logger.info(f"🔍 Cemento: usando '{elegido[1]}' (otros candidatos: {otros})")
            else:
                logger.info(f"🔍 Cemento: usando '{elegido[1]}'")
            return elegido

    @retry_on_failure(max_retries=3)
    def obtener_o_crear_marca(self, nombre: str, regional: Optional[str] = None,
                              cod_municipio: Optional[str] = None) -> Optional[uuid.UUID]:
        """
        Busca una marca por nombre normalizado. Si no existe la crea
        asociada (cuando es posible) a la regional/municipio recibidos.
        """
        if not nombre or not nombre.strip():
            return None
        nombre_norm = self._normalizar_nombre(nombre)
        with self.get_session() as session:
            for m in session.query(MarcaProducto).all():
                if self._normalizar_nombre(m.nombre_marca) == nombre_norm:
                    return m.id_marca
            nueva = MarcaProducto(
                nombre_marca=nombre.strip()[:150],
                regional=regional,
                cod_municipio=cod_municipio,
            )
            session.add(nueva)
            session.flush()
            logger.info(f"🆕 Marca creada: '{nueva.nombre_marca}' → {nueva.id_marca}")
            return nueva.id_marca

    @retry_on_failure(max_retries=3)
    def registrar_cotizacion(self, id_interaccion: str, id_ferreteria: str,
                             producto_nombre: str, marca_nombre: str,
                             precio: float, regional: str,
                             disponibilidad: Optional[str] = None,
                             confianza: Optional[float] = None,
                             info_solicitada: Optional[str] = None,
                             cod_municipio: Optional[str] = None,
                             id_producto: Optional[uuid.UUID] = None) -> Optional[Dict]:
        """
        Persiste una cotización resolviendo las FKs.

        - Producto: NO se crea. Si `id_producto` no se pasa, se busca por
          `producto_nombre` en el catálogo. Si tampoco existe → cotización
          rechazada.
        - Marca: SÍ se crea automáticamente si no existe.

        Devuelve un dict con TODAS las columnas insertadas (mirror de la fila
        de `cotizaciones`) — útil para anexarlo al CSV.
        Devuelve None si la inserción falla.
        """
        try:
            if id_producto is None:
                id_producto = self.obtener_producto_por_nombre(producto_nombre)
            if id_producto is None:
                logger.warning(
                    f"Cotización rechazada: producto '{producto_nombre}' "
                    f"no existe en catálogo (no se crea)"
                )
                return None

            id_marca = self.obtener_o_crear_marca(marca_nombre, regional, cod_municipio)
            if id_marca is None:
                logger.warning("Cotización rechazada: marca vacía")
                return None

            with self.get_session() as session:
                cot = Cotizacion(
                    id_interaccion=uuid.UUID(id_interaccion),
                    id_ferreteria=uuid.UUID(id_ferreteria),
                    id_producto=id_producto,
                    id_marca=id_marca,
                    precio=precio,
                    disponibilidad=(disponibilidad or "")[:100] or None,
                    confianza_extraccion=confianza,
                    info_solicitada_ferreteria=info_solicitada,
                    regional=regional,
                )
                session.add(cot)
                session.flush()
                fila = {
                    "id_cotizacion": str(cot.id_cotizacion),
                    "id_interaccion": str(cot.id_interaccion),
                    "id_ferreteria": str(cot.id_ferreteria),
                    "id_producto": str(cot.id_producto),
                    "id_marca": str(cot.id_marca),
                    "precio": float(cot.precio) if cot.precio is not None else None,
                    "disponibilidad": cot.disponibilidad,
                    "confianza_extraccion": (
                        float(cot.confianza_extraccion)
                        if cot.confianza_extraccion is not None else None
                    ),
                    "info_solicitada_ferreteria": cot.info_solicitada_ferreteria,
                    "fecha_cotizacion": (
                        cot.fecha_cotizacion.isoformat()
                        if cot.fecha_cotizacion else datetime.now(timezone.utc).isoformat()
                    ),
                    "regional": cot.regional,
                }
                logger.info(f"💰 Cotización registrada: {fila['id_cotizacion']} "
                            f"(producto={producto_nombre}, marca={marca_nombre}, precio={precio})")
                return fila
        except Exception as e:
            logger.error(f"Error registrando cotización: {e}")
            return None

    @staticmethod
    def append_cotizacion_a_csv(fila: Dict, csv_path: str) -> bool:
        """
        Anexa una fila de cotización al CSV incremental (UUIDs reales,
        mirror de la tabla `cotizaciones`). Crea el archivo con cabecera si no
        existe. Igual al patrón usado por ARGOS.
        """
        import csv
        import os
        columnas = [
            "id_cotizacion", "id_interaccion", "id_ferreteria",
            "id_producto", "id_marca", "precio", "disponibilidad",
            "confianza_extraccion", "info_solicitada_ferreteria",
            "fecha_cotizacion", "regional",
        ]
        try:
            existe = os.path.exists(csv_path)
            os.makedirs(os.path.dirname(csv_path), exist_ok=True) if os.path.dirname(csv_path) else None
            with open(csv_path, "a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columnas)
                if not existe:
                    writer.writeheader()
                writer.writerow({k: fila.get(k) for k in columnas})
            logger.info(f"📝 CSV actualizado: {csv_path} (+1 fila)")
            return True
        except Exception as e:
            logger.error(f"Error escribiendo CSV: {e}")
            return False

    # ------------------------------------------------------------------
    # Máquina de estados con guard
    # ------------------------------------------------------------------
    def transicionar_estado(self, ferreteria_id: uuid.UUID,
                            nuevo_estado: EstadoFereteria,
                            forzar: bool = False,
                            session: Optional[Any] = None) -> bool:
        """
        Aplica una transición de estado validándola contra TRANSICIONES_VALIDAS.

        - Si `forzar=True`, ignora el grafo (úsalo solo para `terminado` desde
          cualquier punto, p.ej. despedida o veto).
        - Si `session` se pasa, reusa esa sesión (útil para componer transiciones
          dentro de un job más grande). Si es None, abre una propia.

        ✅ FIX: usa flag_modified sobre el campo enum para garantizar que
        SQLAlchemy detecte el cambio (algunas versiones no lo notan en
        comparaciones de enum-string).
        """
        def _do(s) -> bool:
            ferreteria = s.query(Ferreteria).filter_by(id_ferreteria=ferreteria_id).first()
            if not ferreteria:
                logger.warning(f"transicionar_estado: ferretería no encontrada")
                return False
            actual = ferreteria.estado
            if actual == nuevo_estado:
                return True  # idempotente
            if not forzar:
                permitidos = TRANSICIONES_VALIDAS.get(actual, set())
                if nuevo_estado not in permitidos:
                    logger.info(
                        f"🚫 Transición rechazada: {actual} -> {nuevo_estado.value} "
                        f"(permitidos: {[e.value for e in permitidos]})"
                    )
                    return False
            ferreteria.estado = nuevo_estado
            flag_modified(ferreteria, "estado")
            anterior = actual.value if actual else "None"
            logger.info(f"🔄 Estado: {anterior} → {nuevo_estado.value}")
            return True

        try:
            if session is not None:
                # Reusar la sesión externa: no commiteamos aquí, lo hace el caller
                return _do(session)
            # Sesión propia con commit/rollback automático
            with self.get_session() as s:
                return _do(s)
        except Exception as e:
            logger.error(f"Error en transicionar_estado: {e}")
            return False

    def is_phone_vetoed(self, ferreteria_id: uuid.UUID, num_telefono: str) -> bool:
        with self.get_session() as session:
            ferreteria = session.query(Ferreteria).filter_by(id_ferreteria=ferreteria_id).first()
            if not ferreteria:
                return False
            if ferreteria.estado == EstadoFereteria.terminado:
                return True
            vetados = ferreteria.num_vetados or []
            return (num_telefono or "").strip() in [str(v).strip() for v in vetados]

    def add_to_vetados(self, ferreteria_id: uuid.UUID, num_telefono: str,
                       set_terminado: bool = True) -> bool:
        try:
            with self.get_session() as session:
                ferreteria = session.query(Ferreteria).filter_by(id_ferreteria=ferreteria_id).first()
                if not ferreteria:
                    return False
                vetados = list(ferreteria.num_vetados or [])
                if num_telefono and num_telefono not in vetados:
                    vetados.append(num_telefono)
                    ferreteria.num_vetados = vetados
                    flag_modified(ferreteria, "num_vetados")
                if set_terminado:
                    ferreteria.estado = EstadoFereteria.terminado
                    flag_modified(ferreteria, "estado")
                logger.info(f"Número agregado a vetados ({len(vetados)} total)")
                return True
        except Exception as e:
            logger.error(f"Error agregando a vetados: {e}")
            return False

    def update_ferreteria_estado(self, ferreteria_id: uuid.UUID, nuevo_estado: str) -> bool:
        """
        ⚠️ DEPRECADO: actualización directa sin validar transición.
        Mantenido solo por compatibilidad. Para flujo nuevo, usar
        `transicionar_estado` que respeta el grafo.
        """
        try:
            with self.get_session() as session:
                ferreteria = session.query(Ferreteria).filter_by(id_ferreteria=ferreteria_id).first()
                if not ferreteria:
                    return False
                ferreteria.estado = EstadoFereteria[nuevo_estado]
                flag_modified(ferreteria, "estado")
                logger.info(f"⚠️ Estado actualizado SIN validar grafo: {nuevo_estado}")
                return True
        except Exception as e:
            logger.error(f"Error actualizando estado: {e}")
            return False

    def archive_old_interactions(self, days: int = 7,
                                 motivo: str = "Archivo automático >7d") -> int:
        try:
            archived_count = 0
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            with self.get_session() as session:
                old_interactions = session.query(HistorialInteraccion).filter(
                    HistorialInteraccion.fecha_registro < cutoff_date
                ).all()
                for interaction in old_interactions:
                    old_record = HistorialInteraccionAntiguo(
                        id_interaccion=interaction.id_interaccion,
                        id_ferreteria=interaction.id_ferreteria,
                        mensaje_usuario=interaction.mensaje_usuario,
                        respuesta_ia=interaction.respuesta_ia,
                        tokens_consumidos=interaction.tokens_consumidos,
                        fecha_registro=interaction.fecha_registro,
                        motivo_archivo=motivo
                    )
                    session.add(old_record)
                    archived_count += 1
                session.query(HistorialInteraccion).filter(
                    HistorialInteraccion.fecha_registro < cutoff_date
                ).delete()
                logger.info(f"Archivadas {archived_count} interacciones antiguas (>{days}d)")
                return archived_count
        except Exception as e:
            logger.error(f"Error archivando interacciones: {e}")
            return 0


    @classmethod
    def from_settings(cls, settings: "Settings") -> "DatabaseManager":
        """Construye el manager desde la configuración de entorno (.env)."""
        host = f"{settings.db_host_only}:{settings.db_port_effective}"
        return cls(
            db_user=settings.db_user,
            db_password=settings.db_password,
            db_host=host,
            db_name=settings.db_name,
        )


@lru_cache
def get_database_manager() -> DatabaseManager:
    """Devuelve un `DatabaseManager` singleton (patrón dependencia FastAPI)."""
    return DatabaseManager.from_settings(get_settings())
