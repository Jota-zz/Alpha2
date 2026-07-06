"""Punto de entrada de la aplicación FastAPI (migrado de las celdas 11 y 12).

Actúa como raíz de composición: en el arranque construye todos los colaboradores
del bot (config, BD, clientes WhatsApp/Anthropic, dispatcher, processor, gestores
de búsqueda, extractor acumulativo y scheduler), los cablea entre sí y expone el
`MessageProcessor` en `app.state` para los routers. En el apagado detiene el
scheduler y el dispatcher.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import dashboard, health, webhook
from app.bot.handlers import MessageProcessor
from app.core.config import (
    AnthropicConfig,
    DispatcherConfig,
    OperatingHoursConfig,
    OperatingHoursGate,
    Settings,
    WhatsAppConfig,
    get_settings,
)
from app.core.logging import LOG_BUFFER, attach_ring_buffer, get_logger, setup_logging
from app.core.scheduler import BroadcastScheduler
from app.db.session import DatabaseManager
from app.services.anthropic_client import AnthropicAIClient, AnthropicExtractionClient
from app.services.dashboard import DashboardService
from app.services.matching import (
    ExtractorTextoAcumulativo,
    GestorBusquedaMarcas,
    GestorBusquedaProductos,
)
from app.services.message_handler import MessageDispatcher, WhatsAppClient

logger = get_logger(__name__)


def default_operating_hours() -> OperatingHoursConfig:
    """Ventana operativa por defecto: todos los días 24h (config activa del bot).

    Ajustar por día de la semana (0=Lun..6=Dom) para restringir el horario.
    """
    return OperatingHoursConfig(windows={i: (0, 24) for i in range(7)})


def build_components(settings: Settings) -> Dict[str, Any]:
    """Construye y cablea todos los componentes del bot."""
    wa_config = WhatsAppConfig.from_settings(settings)
    anthropic_config = AnthropicConfig.from_settings(settings)
    db_manager = DatabaseManager.from_settings(settings)

    wa_client = WhatsAppClient(wa_config)
    ai_client = AnthropicAIClient(anthropic_config, settings.baseprompt_xml_path)
    extraction_client = AnthropicExtractionClient(anthropic_config)

    hours_gate = OperatingHoursGate(default_operating_hours())
    dispatcher = MessageDispatcher(
        wa_client, DispatcherConfig(), hours_gate=hours_gate
    )

    processor = MessageProcessor(
        wa_client,
        ai_client,
        db_manager,
        extraction_client=extraction_client,
        extractor_acumulativo=None,  # se asigna abajo
        csv_cotizaciones_pdf=settings.csv_cotizaciones_pdf,
        dispatcher=dispatcher,
        hours_gate=hours_gate,
    )
    # Puente cola -> processor cuando vence un inbound_intent.
    dispatcher.set_inbound_intent_callback(processor._procesar_inbound_intent)

    scheduler = BroadcastScheduler(
        db_manager,
        wa_client,
        ai_client,
        processor=processor,
        dispatcher=dispatcher,
        hours_gate=hours_gate,
    )

    # Gestor de búsqueda de productos (tolerante a fallos de BD).
    try:
        gestor_busqueda = GestorBusquedaProductos(db_manager)
        processor.gestor_busqueda = gestor_busqueda
    except Exception as e:
        gestor_busqueda = None
        processor.gestor_busqueda = None
        logger.warning("⚠️  GestorBusquedaProductos no disponible: %s", e)

    # Extractor acumulativo por texto (determinista + fallback LLM).
    try:
        gestor_marcas = GestorBusquedaMarcas(db_manager)
        extractor = ExtractorTextoAcumulativo(
            gestor_productos=gestor_busqueda,
            gestor_marcas=gestor_marcas,
            anthropic_client=extraction_client,
            threshold_producto=0.75,
            threshold_marca=0.80,
        )
        processor.extractor_acumulativo = extractor
        processor.gestor_marcas = gestor_marcas
    except Exception as e:
        processor.extractor_acumulativo = None
        processor.gestor_marcas = None
        logger.warning("⚠️  ExtractorTextoAcumulativo no disponible: %s", e)

    dashboard_service = DashboardService(
        dispatcher=dispatcher,
        anthropic_config=anthropic_config,
        hours_gate=hours_gate,
        scheduler=scheduler,
        log_buffer=LOG_BUFFER,
        argos_dir=settings.argos_dir,
    )

    return {
        "db_manager": db_manager,
        "processor": processor,
        "dispatcher": dispatcher,
        "scheduler": scheduler,
        "dashboard": dashboard_service,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: inicializa componentes al arrancar, los detiene al apagar."""
    setup_logging()
    attach_ring_buffer()
    settings = get_settings()
    components = build_components(settings)

    app.state.processor = components["processor"]
    app.state.dispatcher = components["dispatcher"]
    app.state.scheduler = components["scheduler"]
    app.state.dashboard = components["dashboard"]

    try:
        components["dispatcher"].start()
    except Exception as e:
        logger.error("Error iniciando dispatcher: %s", e)

    # Rehidratar mensajes inbound huérfanos de un reinicio anterior (celda 13).
    # Nunca debe bloquear el arranque.
    try:
        stats = components["processor"].rehidratar_inbounds_huerfanos(
            ventana_minutos_responder=15,
            ventana_minutos_marcar=120,
        )
        if stats.get("rehidratados") or stats.get("marcados_perdidos"):
            logger.info(
                "♻️  Rehidratación: %s reagendados, %s marcados como perdidos",
                stats.get("rehidratados"), stats.get("marcados_perdidos"),
            )
    except Exception as e:
        logger.error("Rehidratación falló: %s", e)

    try:
        components["scheduler"].start_scheduler()
    except Exception as e:
        logger.error("Error iniciando scheduler: %s", e)

    logger.info("✅ Bot inicializado — modelo IA: %s", settings.anthropic_model)
    try:
        yield
    finally:
        try:
            components["scheduler"].stop()
        except Exception:
            pass
        try:
            components["dispatcher"].stop()
        except Exception:
            pass
        logger.info("🛑 Bot detenido")


app = FastAPI(title="Alpha Bot API", version="1.4.4", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(dashboard.router)


@app.get("/")
async def root() -> dict:
    return {"service": "Alpha Bot API", "status": "running"}
