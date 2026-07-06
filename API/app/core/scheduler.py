"""Scheduler de broadcasts y jobs de mantenimiento (migrado de la celda 10).

`BroadcastScheduler` agenda el outreach semanal (delegando en el
`MessageProcessor`) y los jobs diarios de mantenimiento (archivado de
interacciones, marcado de ferreterías sin respuesta, purga de eventos de
webhook). Todos los cron se interpretan en hora local de Bogotá.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import BOGOTA_TZ, OperatingHoursGate
from app.core.logging import get_logger
from app.db.session import DatabaseManager
from app.models import (
    ESTADOS_EN_CONVERSACION,
    EstadoFereteria,
    Ferreteria,
    HistorialInteraccion,
)
from app.services.anthropic_client import AnthropicAIClient
from app.services.message_handler import MessageDispatcher, WhatsAppClient
from app.utils.text import retry_on_failure

logger = get_logger(__name__)

if False:  # solo para type checkers (evita import circular con bot.handlers)
    from app.bot.handlers import MessageProcessor


class BroadcastScheduler:
    """
    Scheduler de jobs programados:
      - Job de outreach (broadcast semanal): delega en
        processor._handle_ai_flow(modo="outreach", topic=...).
        ✅ v1.1: chequea OperatingHoursGate al inicio del job (decisión 2a+2c).
        El cron sigue agendado siempre; el gate decide si la corrida procede.
      - Jobs de mantenimiento diario:
          * archive_old_interactions (02:00)
          * check_unresponsive_ferreterias (03:00)
          * purge_webhook_events (04:00)
    """

    def __init__(self, db_manager: DatabaseManager, wa_client: WhatsAppClient,
                 ai_client: AnthropicAIClient,
                 processor: "MessageProcessor",
                 dispatcher: Optional["MessageDispatcher"] = None,
                 hours_gate: Optional["OperatingHoursGate"] = None):
        self.db_manager = db_manager
        self.wa_client = wa_client
        self.ai_client = ai_client
        self.processor = processor          # ✅ v1.1: necesario para outreach
        self.dispatcher = dispatcher
        self.hours_gate = hours_gate        # ✅ v1.1: gate opcional
        # ⚠️ TZ Bogotá: los CronTriggers (broadcast semanal, jobs de mantenimiento)
        # se interpretan en hora LOCAL DE BOGOTÁ. Sin esto, en Colab (UTC) el
        # cron 'mon 17:08' se dispararía a las 12:08 hora Bogotá.
        self.scheduler = BackgroundScheduler(timezone=BOGOTA_TZ)
        logger.info("✅ BroadcastScheduler inicializado")

    @retry_on_failure(max_retries=3)
    def _run_broadcast_job(self, campaign_topic: str):
        """
        Wrapper delgado que delega en processor._handle_ai_flow(modo="outreach").
        ✅ v1.1: gate horario al inicio (decisión 2a+2c).
        """
        # ✅ Gate horario (decisión 2a+2c)
        if self.hours_gate is not None and not self.hours_gate.is_open():
            logger.info(
                f"🌙 Broadcast '{campaign_topic}' abortado: "
                f"bot fuera de ventana operativa"
            )
            return

        logger.info(f"🚀 Broadcast aprobado por gate — topic: {campaign_topic!r}")
        try:
            self.processor._handle_ai_flow(
                modo="outreach", topic=campaign_topic
            )
        except Exception as e:
            logger.error(f"Error en broadcast '{campaign_topic}': {e}")

    def schedule_weekly_broadcast(self, topic: str, day_of_week: str = 'mon',
                                  hour: int = 8, minute: int = 0):
        trigger = CronTrigger(
            day_of_week=day_of_week, hour=hour, minute=minute,
            timezone=BOGOTA_TZ  # ⚠️ explícito: sin esto APScheduler usa UTC en algunos casos
        )
        self.scheduler.add_job(
            self._run_broadcast_job,
            trigger=trigger,
            args=[topic],
            id=f"broadcast_{day_of_week}",
            replace_existing=True
        )
        logger.info(f"📅 Broadcast programado: {day_of_week.upper()} {hour:02d}:{minute:02d}")

    def check_unresponsive_ferreterias(self):
        """
        Marca como `sin_respuesta` ferreterías sin actividad >7 días.
        Usa transicionar_estado para validar contra TRANSICIONES_VALIDAS.
        """
        try:
            seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
            ids_candidatos: List[uuid.UUID] = []
            with self.db_manager.get_session() as session:
                candidatas = session.query(Ferreteria).filter(
                    Ferreteria.estado.in_(list(ESTADOS_EN_CONVERSACION))
                ).all()
                for ferreteria in candidatas:
                    last = session.query(HistorialInteraccion).filter(
                        HistorialInteraccion.id_ferreteria == ferreteria.id_ferreteria
                    ).order_by(HistorialInteraccion.fecha_registro.desc()).first()
                    ref_fecha = (
                        last.fecha_registro if last and last.fecha_registro
                        else ferreteria.fecha_registro
                    )
                    if ref_fecha and ref_fecha < seven_days_ago:
                        ids_candidatos.append(ferreteria.id_ferreteria)

            transicionadas = 0
            for fid in ids_candidatos:
                if self.db_manager.transicionar_estado(
                    fid, EstadoFereteria.sin_respuesta
                ):
                    transicionadas += 1
            logger.info(
                f"🕒 Job check_unresponsive: {len(ids_candidatos)} candidatas, "
                f"{transicionadas} transicionadas a sin_respuesta"
            )
        except Exception as e:
            logger.error(f"Error check_unresponsive_ferreterias: {e}")

    def archive_old_interactions(self):
        try:
            n = self.db_manager.archive_old_interactions(days=7)
            logger.info(f"Archivado: {n} interacciones transferidas")
        except Exception as e:
            logger.error(f"Error archivando: {e}")

    def purge_webhook_events(self):
        """Job diario para purgar eventos de idempotencia >24h (FIX 2.7)."""
        try:
            n = self.db_manager.purgar_webhook_events_antiguos(days=1)
            logger.info(f"Purga webhook_events: {n} eliminados")
        except Exception as e:
            logger.error(f"Error purgando webhook_events: {e}")

    def start_scheduler(self):
        try:
            self.scheduler.add_job(
                self.check_unresponsive_ferreterias,
                CronTrigger(hour=3, minute=0, timezone=BOGOTA_TZ),
                id='check_unresponsive_ferreterias',
                replace_existing=True,
                name='Verificar ferreterías sin respuesta'
            )
            self.scheduler.add_job(
                self.archive_old_interactions,
                CronTrigger(hour=2, minute=0, timezone=BOGOTA_TZ),
                id='archive_old_interactions',
                replace_existing=True,
                name='Archivar interacciones antiguas'
            )
            self.scheduler.add_job(
                self.purge_webhook_events,
                CronTrigger(hour=4, minute=0, timezone=BOGOTA_TZ),
                id='purge_webhook_events',
                replace_existing=True,
                name='Purgar webhook_events antiguos'
            )
            if not self.scheduler.running:
                self.scheduler.start()
            logger.info("✅ Scheduler iniciado con jobs de mantenimiento")
        except Exception as e:
            logger.error(f"Error iniciando scheduler: {e}")

    def stop(self):
        self.scheduler.shutdown()
        logger.info("🛑 Scheduler detenido")


__all__ = ["BroadcastScheduler"]
