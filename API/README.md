# Alpha Bot API

Bot de WhatsApp para outreach y captura de cotizaciones de ferreterías, con
dashboard de administración y el sistema Argos de inteligencia de precios.
Migrado desde el notebook `Alpha_BotV1_4_4_with_API FUNCIONAL.ipynb` (Flask +
Colab) a una aplicación **FastAPI** modular.

## Estructura

```
app/
  main.py                 Punto de entrada FastAPI (raíz de composición, lifespan)
  core/
    config.py             Settings (.env) + configs de dominio (WhatsApp, Anthropic, horario)
    logging.py            Logging con filtro PII + ring buffer para el dashboard
    scheduler.py          BroadcastScheduler (outreach + jobs de mantenimiento)
  db/
    base.py               Base declarativa SQLAlchemy
    session.py            DatabaseManager
  models/__init__.py      Modelos ORM + máquina de estados de la ferretería
  schemas/__init__.py     Dataclasses de resultado (búsqueda, precios, extracción)
  services/
    matching.py           Similitud de productos + extractor acumulativo de cotizaciones
    message_handler.py    WhatsAppClient + MessageDispatcher
    anthropic_client.py   Cliente Anthropic (conversacional + extracción)
    dashboard.py          Estado y operaciones del dashboard
    argos.py              Sistema Argos (Bayes jerárquico + K-Means)
  bot/handlers.py         MessageProcessor (orquestación del flujo)
  api/
    deps.py               Dependencias FastAPI
    routes/               health, webhook, dashboard (/api/*)
```

## Configuración

Copia `.env.example` a `.env` y completa las variables (WhatsApp, Anthropic, BD).
`DB_HOST` admite `host` o `host:puerto`.

## Ejecutar

```bash
pip install -r requirements.txt
./scripts/run_dev.sh            # uvicorn con reload en el puerto 8000
```

O con Docker:

```bash
docker build -t alpha-bot .
docker run --env-file .env -p 8000:8000 alpha-bot
```

## Endpoints

- `GET /health` — liveness.
- `GET|POST /webhook` — verificación y recepción de eventos de WhatsApp.
- `GET /api/bot/status`, `POST /api/bot/pause|resume`, `GET /api/bot/logs`
- `GET|PUT /api/config[/{section}]` — dispatcher, anthropic, operating_hours, webhook, extras.
- `GET|POST|PUT|DELETE /api/broadcasts[...]` — CRUD de broadcasts dinámicos.
- `GET /api/argos/*` — charts Plotly (precios, HDI, perfiles, mapa) + files/refresh.

## Notas de migración

- Los patrones de Colab (`userdata`, `drive.mount`, `pyngrok`) se reemplazaron por
  `pydantic-settings` (.env) y arranque vía uvicorn.
- El sistema Argos escribe columnas `precios_mu`/`perfiles` mientras el dashboard
  lee `precio_mu`/`perfil` (inconsistencia heredada del notebook; ver
  `services/argos.py`).
- `pymc`/`arviz` (Argos) son dependencias pesadas y opcionales.
