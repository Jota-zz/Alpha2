# Plan de migración — Alpha_BotV1_4_4 (.ipynb → estructura `app/`)

**Origen:** `API/Alpha_BotV1_4_4_with_API FUNCIONAL.ipynb`
**Total:** 61 celdas (30 de código) · 6.882 líneas · ≈ 85.000 tokens de código → 16 archivos destino.

**Regla de ventana:** cada sesión debe consumir ≤ 44.000 tokens.
Estimación por sesión ≈ (tokens del código fuente × 2, por leer + reescribir) + ~8.000 de overhead (razonamiento, imports, boilerplate).
Ninguna celda individual > ~16K tokens por sesión; la celda 42 se divide en dos sesiones (S8 y S9).

---

## Fase 1 · Núcleo

| Sesión | Celdas | Archivos destino | Contenido | ~Ventana |
|--------|--------|------------------|-----------|----------|
| **S1** | 4, 6, 8 | `core/config.py`, `core/logging.py`, `utils/text.py` | Secretos/env, `PIIFilter` + logging, clases `*Config`, `OperatingHoursGate` | ~17K |
| **S2** | 10 | `db/base.py`, `models/__init__.py` | Modelos SQLAlchemy (`Ferreteria`, `Producto`, `Cotizacion`, etc.) | ~14K |
| **S3** | 12 | `db/session.py` | `DatabaseManager` (750 líneas) | ~28K |

## Fase 2 · Servicios de dominio

| Sesión | Celdas | Archivos destino | Contenido | ~Ventana |
|--------|--------|------------------|-----------|----------|
| **S4** | 15–27 | `services/matching.py`, `schemas/__init__.py` | `NormalizadorNombres`, `MetricasSimilitud`, `BuscadorProductos`, `GestorBusquedaProductos`, `ResultadoBusqueda` | ~20K |
| **S5** | 30–36 | `services/matching.py` (extractor) | `DetectorPrecios`, `GestorBusquedaMarcas`, `EstadoExtraccionAcumulado`, `ExtractorTextoAcumulativo` | ~25K |
| **S6** | 38 | `services/message_handler.py` (whatsapp) | `WhatsAppClient`, `MessageDispatcher` | ~20K |
| **S7** | 40 | `services/anthropic_client.py` | `AnthropicAIClient`, `AnthropicExtractionClient` (794 líneas) | ~28K |

## Fase 3 · Orquestación

| Sesión | Celdas | Archivos destino | Contenido | ~Ventana |
|--------|--------|------------------|-----------|----------|
| **S8** | 42 (parte 1) | `bot/handlers.py` | `MessageProcessor` | ~26K |
| **S9** | 42 (parte 2) | `api/routes/webhook.py`, `api/deps.py` | `WebhookServer` (recepción de webhooks) | ~23K |
| **S10** | 44, 46, 48, 50 | `core/scheduler.py`, `main.py` (init) | `BroadcastScheduler` + jobs, inicialización de componentes | ~17K |

## Fase 4 · API y arranque

| Sesión | Celdas | Archivos destino | Contenido | ~Ventana |
|--------|--------|------------------|-----------|----------|
| **S11** | 52, 53 | `api/routes/webhook.py` (extendido), `api/routes/health.py` | `_RingBufferLogHandler`, API HTTP del dashboard, `WebhookServerExtendido` | ~32K |
| **S12** | 55, 57, 59, 60 | `main.py`, `scripts/run_dev.sh` | Arranque del bot, `Sistema Argos`, diagnósticos | ~16K |

---

## Orden y dependencias

Ejecutar **S1 → S12 en orden**: cada fase depende de la anterior (config → modelos → DB → servicios → orquestación → API/arranque).

**Notas de la celda 42** (la más grande, ~16K tokens): contiene `MessageProcessor` **y** `WebhookServer`. Se separa deliberadamente en S8 (handlers) y S9 (webhook) para no superar la ventana y respetar la separación de responsabilidades del árbol `app/`.

**Al cerrar cada sesión:** dejar los `import` correctos entre módulos y actualizar `app/__init__.py` / `__init__.py` de cada paquete con las exportaciones necesarias.
