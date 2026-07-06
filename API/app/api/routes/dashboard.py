"""Rutas /api/* del dashboard (migrado de `WebhookServerExtendido`, celda 12.5.2).

Adaptado de Flask a un `APIRouter` de FastAPI. Toda la lógica vive en
`DashboardService`; aquí solo se mapean rutas y códigos de estado HTTP.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_dashboard_service
from app.core.logging import get_logger
from app.services.dashboard import DriveNotConfiguredError

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


# ── Bot status & control ─────────────────────────────────────────────────
@router.get("/bot/status")
async def bot_status(svc=Depends(get_dashboard_service)):
    try:
        return svc.bot_status_payload()
    except Exception as e:
        logger.error("/api/bot/status: %s", e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.post("/bot/pause")
async def bot_pause(svc=Depends(get_dashboard_service)):
    return svc.pause()


@router.post("/bot/resume")
async def bot_resume(svc=Depends(get_dashboard_service)):
    return svc.resume()


@router.get("/bot/logs")
async def bot_logs(
    request: Request,
    level: Optional[str] = None,
    limit: int = 200,
    since: Optional[str] = None,
    svc=Depends(get_dashboard_service),
):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    return svc.get_logs(level=level, limit=limit, since=since)


# ── Config (5 secciones) ─────────────────────────────────────────────────
@router.get("/config")
async def config_all(svc=Depends(get_dashboard_service)):
    try:
        return svc.cfg_get_all()
    except Exception as e:
        logger.error("/api/config: %s", e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.get("/config/{section}")
async def config_get(section: str, svc=Depends(get_dashboard_service)):
    try:
        return svc.cfg_get(section)
    except KeyError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    except Exception as e:
        logger.error("/api/config/%s GET: %s", section, e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.put("/config/{section}")
async def config_put(section: str, request: Request, svc=Depends(get_dashboard_service)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        new_value = svc.cfg_set(section, body or {})
        logger.info("Config actualizada: sección=%s", section)
        return new_value
    except KeyError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except Exception as e:
        logger.error("/api/config/%s PUT: %s", section, e)
        return JSONResponse({"detail": str(e)}, status_code=500)


# ── Broadcasts CRUD ──────────────────────────────────────────────────────
@router.get("/broadcasts")
async def broadcasts_list(svc=Depends(get_dashboard_service)):
    return svc.broadcasts_list()


@router.post("/broadcasts")
async def broadcasts_create(request: Request, svc=Depends(get_dashboard_service)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return JSONResponse(svc.broadcasts_create(body or {}), status_code=201)
    except (ValueError, KeyError) as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except Exception as e:
        logger.error("POST /api/broadcasts: %s", e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.put("/broadcasts/{bid}")
async def broadcasts_update(bid: str, request: Request, svc=Depends(get_dashboard_service)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return svc.broadcasts_update(bid, body or {})
    except KeyError:
        return JSONResponse({"detail": "broadcast no existe"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except Exception as e:
        logger.error("PUT /api/broadcasts/%s: %s", bid, e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.delete("/broadcasts/{bid}")
async def broadcasts_delete(bid: str, svc=Depends(get_dashboard_service)):
    try:
        svc.broadcasts_delete(bid)
        return {"ok": True}
    except KeyError:
        return JSONResponse({"detail": "broadcast no existe"}, status_code=404)
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.post("/broadcasts/{bid}/run-now")
async def broadcasts_run_now(bid: str, svc=Depends(get_dashboard_service)):
    try:
        return svc.broadcasts_run_now(bid)
    except KeyError:
        return JSONResponse({"detail": "broadcast no existe"}, status_code=404)
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)


# ── Argos (4 charts + files + refresh) ───────────────────────────────────
def _argos_response(fn, *args):
    try:
        return fn(*args)
    except DriveNotConfiguredError as e:
        return JSONResponse({"detail": str(e)}, status_code=503)
    except FileNotFoundError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    except Exception as e:
        logger.error("argos endpoint: %s", e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.get("/argos/precios-regionales")
async def argos_precios(svc=Depends(get_dashboard_service)):
    return _argos_response(svc.chart_precios_regionales)


@router.get("/argos/intervalos-hdi")
async def argos_intervalos(
    cod_municipio: Optional[str] = None, svc=Depends(get_dashboard_service)
):
    return _argos_response(svc.chart_intervalos_hdi, cod_municipio)


@router.get("/argos/perfiles-alertas")
async def argos_perfiles(svc=Depends(get_dashboard_service)):
    return _argos_response(svc.chart_perfiles_alertas)


@router.get("/argos/mapa")
async def argos_mapa(svc=Depends(get_dashboard_service)):
    return _argos_response(svc.chart_mapa)


@router.get("/argos/files")
async def argos_files(svc=Depends(get_dashboard_service)):
    return svc.argos_files()


@router.post("/argos/refresh")
async def argos_refresh(name: Optional[str] = None, svc=Depends(get_dashboard_service)):
    return svc.argos_refresh(name)
