"""Servicio del dashboard (migrado de las celdas 12.5.1 y 12.5.2).

Encapsula en `DashboardService` el estado que en el notebook vivía como globales
(config mutable de las 5 secciones, EXTRAS en RAM, broadcasts dinámicos, cache de
CSV Argos y builders Plotly). Recibe por inyección los componentes vivos del bot
(dispatcher, config Anthropic, gate horario, scheduler y ring buffer de logs), de
modo que los routers FastAPI solo delegan aquí.
"""
from __future__ import annotations

import os
import threading
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import BOGOTA_TZ, AnthropicConfig, OperatingHoursGate
from app.core.logging import get_logger

logger = get_logger(__name__)

# Layout base para Plotly compatible con el tema oscuro del dashboard.
_PLOTLY_BASE_LAYOUT = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": "#cbd5e1", "family": "Space Grotesk, sans-serif"},
    "margin": {"l": 60, "r": 20, "t": 30, "b": 50},
    "xaxis": {"gridcolor": "rgba(255,255,255,0.05)", "zerolinecolor": "rgba(255,255,255,0.1)"},
    "yaxis": {"gridcolor": "rgba(255,255,255,0.05)", "zerolinecolor": "rgba(255,255,255,0.1)"},
}


class DriveNotConfiguredError(Exception):
    """Directorio de datos Argos no configurado o inexistente (devuelve 503)."""


class DashboardService:
    """Estado y operaciones del dashboard de administración."""

    def __init__(
        self,
        dispatcher,
        anthropic_config: AnthropicConfig,
        hours_gate: OperatingHoursGate,
        scheduler,
        log_buffer,
        argos_dir: Optional[str] = None,
    ):
        self.dispatcher = dispatcher
        self.anthropic_config = anthropic_config
        self.hours_gate = hours_gate
        self.scheduler = scheduler  # BroadcastScheduler
        self.log_buffer = log_buffer

        self._config_lock = threading.RLock()
        self._broadcasts_lock = threading.RLock()
        self._argos_lock = threading.RLock()

        # Estado "extras" (no existía en el bot original; vive solo en RAM).
        self.extras: Dict[str, Any] = {
            "paused": False,
            "dry_run": False,
            "whitelist": [],
            "daily_quota": 1000,
            "log_level": "INFO",
            "_quota_used_today": 0,
            "_quota_day": datetime.now(BOGOTA_TZ).date().isoformat(),
        }

        self.broadcasts: Dict[str, Dict[str, Any]] = {}

        # Rutas de los CSV Argos, derivadas de un directorio configurable.
        self.argos_dir = argos_dir
        self.argos_paths: Dict[str, str] = {}
        if argos_dir:
            self.argos_paths = {
                "precios_regionales": os.path.join(argos_dir, "argos_precios_regionales.csv"),
                "intervalos_hdi": os.path.join(argos_dir, "argos_intervalos_hdi.csv"),
                "perfiles_alertas": os.path.join(argos_dir, "argos_perfiles_alertas.csv"),
            }
        self._argos_cache: Dict[str, Any] = {}

        self._import_legacy_broadcast()

    # ==================================================================
    # Config: operating hours <-> dict del dashboard
    # ==================================================================
    def _operating_hours_to_dict(self) -> Dict[str, Any]:
        raw = self.hours_gate.config.windows
        out: Dict[str, Any] = {}
        for k, v in raw.items():
            out[str(k)] = None if v is None else [int(v[0]), int(v[1])]
        return {"windows": out}

    def _operating_hours_from_dict(self, payload: Dict[str, Any]) -> Dict[int, Any]:
        windows = payload.get("windows", {})
        out: Dict[int, Any] = {}
        for k, v in windows.items():
            day = int(k)
            if v is None:
                out[day] = None
            else:
                opens, closes = int(v[0]), int(v[1])
                if not (0 <= opens < closes <= 24):
                    raise ValueError(
                        f"Ventana inválida para día {day}: opens={opens}, closes={closes}"
                    )
                out[day] = (opens, closes)
        return out

    # ==================================================================
    # Config: get / set de las 5 secciones
    # ==================================================================
    def cfg_get_all(self) -> Dict[str, Any]:
        with self._config_lock:
            return {
                "dispatcher": self.cfg_get("dispatcher"),
                "anthropic": self.cfg_get("anthropic"),
                "operating_hours": self.cfg_get("operating_hours"),
                "webhook": self.cfg_get("webhook"),
                "extras": self.cfg_get("extras"),
            }

    def cfg_get(self, section: str) -> Dict[str, Any]:
        with self._config_lock:
            if section == "dispatcher":
                dc = self.dispatcher.config
                return {
                    "listen_window_min_s": dc.listen_window_min_s,
                    "listen_window_max_s": dc.listen_window_max_s,
                    "outreach_delay_min_s": dc.outreach_delay_min_s,
                    "outreach_delay_max_s": dc.outreach_delay_max_s,
                    "inter_chat_min_s": dc.inter_chat_min_s,
                    "inter_chat_max_s": dc.inter_chat_max_s,
                }
            if section == "anthropic":
                ac = self.anthropic_config
                return {
                    "model_name": ac.model_name,
                    "max_tokens": ac.max_tokens,
                    "temperature": ac.temperature,
                    "typo_rate": ac.typo_rate,
                    "history_limit": getattr(ac, "history_limit", 20),
                }
            if section == "operating_hours":
                return self._operating_hours_to_dict()
            if section == "webhook":
                return {
                    "outreach_template_name": self.anthropic_config.outreach_template_name,
                    "outreach_template_lang": self.anthropic_config.outreach_template_lang,
                }
            if section == "extras":
                return {
                    "paused": self.extras["paused"],
                    "dry_run": self.extras["dry_run"],
                    "whitelist": list(self.extras["whitelist"]),
                    "daily_quota": self.extras["daily_quota"],
                    "log_level": self.extras["log_level"],
                }
            raise KeyError(f"sección desconocida: {section}")

    def cfg_set(self, section: str, body: Dict[str, Any]) -> Dict[str, Any]:
        import logging

        with self._config_lock:
            if section == "dispatcher":
                dc = self.dispatcher.config
                for k in (
                    "listen_window_min_s", "listen_window_max_s",
                    "outreach_delay_min_s", "outreach_delay_max_s",
                    "inter_chat_min_s", "inter_chat_max_s",
                ):
                    if k in body:
                        v = int(body[k])
                        if not (0 <= v <= 3600):
                            raise ValueError(f"{k} fuera de rango [0..3600]")
                        setattr(dc, k, v)
                for lo, hi in (
                    ("listen_window_min_s", "listen_window_max_s"),
                    ("outreach_delay_min_s", "outreach_delay_max_s"),
                    ("inter_chat_min_s", "inter_chat_max_s"),
                ):
                    if getattr(dc, lo) > getattr(dc, hi):
                        raise ValueError(f"{lo} > {hi}")
                return self.cfg_get("dispatcher")

            if section == "anthropic":
                ac = self.anthropic_config
                if "model_name" in body:
                    ac.model_name = str(body["model_name"]).strip()
                if "max_tokens" in body:
                    v = int(body["max_tokens"])
                    if not (1 <= v <= 8192):
                        raise ValueError("max_tokens debe estar en [1..8192]")
                    ac.max_tokens = v
                if "temperature" in body:
                    v = float(body["temperature"])
                    if not (0.0 <= v <= 1.0):
                        raise ValueError("temperature debe estar en [0..1]")
                    ac.temperature = v
                if "typo_rate" in body:
                    v = float(body["typo_rate"])
                    if not (0.0 <= v <= 0.05):
                        raise ValueError("typo_rate debe estar en [0..0.05]")
                    ac.typo_rate = v
                if "history_limit" in body and hasattr(ac, "history_limit"):
                    ac.history_limit = int(body["history_limit"])
                return self.cfg_get("anthropic")

            if section == "operating_hours":
                self.hours_gate.config.windows = self._operating_hours_from_dict(body)
                return self.cfg_get("operating_hours")

            if section == "webhook":
                if "outreach_template_name" in body:
                    self.anthropic_config.outreach_template_name = str(
                        body["outreach_template_name"]
                    ).strip()
                if "outreach_template_lang" in body:
                    self.anthropic_config.outreach_template_lang = str(
                        body["outreach_template_lang"]
                    ).strip()
                return self.cfg_get("webhook")

            if section == "extras":
                if "paused" in body:
                    self.extras["paused"] = bool(body["paused"])
                if "dry_run" in body:
                    self.extras["dry_run"] = bool(body["dry_run"])
                if "whitelist" in body:
                    wl = body["whitelist"] or []
                    if not isinstance(wl, list):
                        raise ValueError("whitelist debe ser lista")
                    self.extras["whitelist"] = [
                        str(x).strip() for x in wl if str(x).strip()
                    ]
                if "daily_quota" in body:
                    v = int(body["daily_quota"])
                    if not (0 <= v <= 100000):
                        raise ValueError("daily_quota fuera de rango")
                    self.extras["daily_quota"] = v
                if "log_level" in body:
                    lvl = str(body["log_level"]).upper().strip()
                    if lvl not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                        raise ValueError(f"log_level inválido: {lvl}")
                    self.extras["log_level"] = lvl
                    logging.getLogger().setLevel(lvl)
                return self.cfg_get("extras")

            raise KeyError(f"sección desconocida: {section}")

    # ==================================================================
    # Bot: pause / resume / logs / status
    # ==================================================================
    def pause(self) -> Dict[str, Any]:
        with self._config_lock:
            self.extras["paused"] = True
        logger.info("Bot pausado desde dashboard")
        return {"ok": True, "paused": True}

    def resume(self) -> Dict[str, Any]:
        with self._config_lock:
            self.extras["paused"] = False
        logger.info("Bot reanudado desde dashboard")
        return {"ok": True, "paused": False}

    def get_logs(self, level=None, limit: int = 200, since=None) -> Dict[str, Any]:
        records = self.log_buffer.get(level=level, limit=limit, since=since)
        return {"records": records, "level_filter": level or None}

    def _reset_daily_quota_if_needed(self) -> None:
        today = datetime.now(BOGOTA_TZ).date().isoformat()
        if today != self.extras["_quota_day"]:
            self.extras["_quota_used_today"] = 0
            self.extras["_quota_day"] = today

    def bot_status_payload(self) -> Dict[str, Any]:
        self._reset_daily_quota_if_needed()
        now = datetime.now(BOGOTA_TZ)
        gate_open = self.hours_gate.is_open(now)

        opens_at = None
        closes_at = None
        cursor = now
        last_state = gate_open
        for _ in range(7 * 24):
            cursor = cursor + timedelta(hours=1)
            st = self.hours_gate.is_open(cursor)
            if st != last_state:
                if st and opens_at is None:
                    opens_at = cursor.replace(minute=0, second=0, microsecond=0).isoformat()
                elif not st and closes_at is None:
                    closes_at = cursor.replace(minute=0, second=0, microsecond=0).isoformat()
                last_state = st
            if opens_at and closes_at:
                break

        jobs_out = []
        try:
            for j in self.scheduler.scheduler.get_jobs():
                nrt = getattr(j, "next_run_time", None)
                jobs_out.append({
                    "id": j.id,
                    "name": j.name or j.id,
                    "next_run_time": nrt.isoformat() if nrt else None,
                })
            jobs_out = [j for j in jobs_out if "broadcast" in (j["id"] + j["name"]).lower()]
            jobs_out.sort(key=lambda x: x["next_run_time"] or "")
        except Exception:
            pass

        try:
            pending = self.dispatcher.pending() if hasattr(self.dispatcher, "pending") else 0
        except Exception:
            pending = 0
        try:
            worker = getattr(self.dispatcher, "_worker", None)
            if worker is not None:
                running = bool(worker.is_alive())
            else:
                running = bool(
                    getattr(self.dispatcher, "_running", False)
                    or getattr(self.dispatcher, "running", False)
                )
        except Exception:
            running = False

        return {
            "now": now.isoformat(),
            "dispatcher": {
                "running": running,
                "pending": pending,
                "quota_used_today": self.extras["_quota_used_today"],
                "quota_total": self.extras["daily_quota"],
            },
            "scheduler": {
                "running": self.scheduler.scheduler.running,
                "jobs": jobs_out,
            },
            "gate": {"open": gate_open, "opens_at": opens_at, "closes_at": closes_at},
            "extras": {
                "paused": self.extras["paused"],
                "dry_run": self.extras["dry_run"],
                "log_level": self.extras["log_level"],
                "whitelist_count": len(self.extras["whitelist"]),
            },
        }

    # ==================================================================
    # Broadcasts dinámicos (CRUD sobre APScheduler en caliente)
    # ==================================================================
    @staticmethod
    def _broadcast_to_dict(b: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": b["id"],
            "topic": b["topic"],
            "day_of_week": b["day_of_week"],
            "hour": b["hour"],
            "minute": b["minute"],
            "enabled": b["enabled"],
            "last_run_at": b.get("last_run_at"),
        }

    @staticmethod
    def _broadcast_job_id(bid: str) -> str:
        return f"dyn_broadcast_{bid}"

    def _broadcast_register_job(self, b: Dict[str, Any]) -> None:
        from apscheduler.triggers.cron import CronTrigger

        job_id = self._broadcast_job_id(b["id"])
        try:
            self.scheduler.scheduler.remove_job(job_id)
        except Exception:
            pass
        if not b["enabled"]:
            return

        def _run():
            try:
                with self._broadcasts_lock:
                    if b["id"] not in self.broadcasts:
                        return
                    self.broadcasts[b["id"]]["last_run_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                self.scheduler._run_broadcast_job(b["topic"])
            except Exception as e:
                logger.error("Broadcast dyn %s falló: %s", b["id"], e)

        trigger = CronTrigger(
            day_of_week=b["day_of_week"],
            hour=b["hour"],
            minute=b["minute"],
            timezone=BOGOTA_TZ,
        )
        self.scheduler.scheduler.add_job(
            _run,
            trigger=trigger,
            id=job_id,
            name=f"broadcast_{b['day_of_week']}_{b['hour']:02d}{b['minute']:02d}",
            replace_existing=True,
        )

    def broadcasts_list(self) -> List[Dict[str, Any]]:
        with self._broadcasts_lock:
            return [self._broadcast_to_dict(b) for b in self.broadcasts.values()]

    def broadcasts_create(self, body: Dict[str, Any]) -> Dict[str, Any]:
        bid = str(_uuid.uuid4())
        b = {
            "id": bid,
            "topic": str(body["topic"]).strip(),
            "day_of_week": str(body["day_of_week"]).strip(),
            "hour": int(body["hour"]),
            "minute": int(body["minute"]),
            "enabled": bool(body.get("enabled", True)),
            "last_run_at": None,
        }
        if len(b["topic"]) < 3:
            raise ValueError("topic muy corto (mínimo 3 chars)")
        if b["day_of_week"] not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            raise ValueError("day_of_week inválido")
        if not (0 <= b["hour"] <= 23):
            raise ValueError("hour fuera de rango")
        if not (0 <= b["minute"] <= 59):
            raise ValueError("minute fuera de rango")
        with self._broadcasts_lock:
            self.broadcasts[bid] = b
            self._broadcast_register_job(b)
        return self._broadcast_to_dict(b)

    def broadcasts_update(self, bid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._broadcasts_lock:
            if bid not in self.broadcasts:
                raise KeyError("broadcast no existe")
            b = self.broadcasts[bid]
            for k in ("topic", "day_of_week", "hour", "minute", "enabled"):
                if k in body:
                    b[k] = body[k]
                    if k == "topic":
                        b[k] = str(b[k]).strip()
                    if k in ("hour", "minute"):
                        b[k] = int(b[k])
                    if k == "enabled":
                        b[k] = bool(b[k])
            self._broadcast_register_job(b)
            return self._broadcast_to_dict(b)

    def broadcasts_delete(self, bid: str) -> None:
        with self._broadcasts_lock:
            if bid not in self.broadcasts:
                raise KeyError("broadcast no existe")
            try:
                self.scheduler.scheduler.remove_job(self._broadcast_job_id(bid))
            except Exception:
                pass
            del self.broadcasts[bid]

    def broadcasts_run_now(self, bid: str) -> Dict[str, Any]:
        with self._broadcasts_lock:
            if bid not in self.broadcasts:
                raise KeyError("broadcast no existe")
            topic = self.broadcasts[bid]["topic"]
        if self.extras["paused"]:
            return {"ok": False, "reason": "bot pausado"}
        if not self.hours_gate.is_open(datetime.now(BOGOTA_TZ)):
            return {"ok": False, "reason": "fuera de ventana operativa"}
        try:
            result = self.scheduler._run_broadcast_job(topic)
            with self._broadcasts_lock:
                if bid in self.broadcasts:
                    self.broadcasts[bid]["last_run_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
            if isinstance(result, dict):
                return {"ok": True, **result}
            return {"ok": True, "enviadas": 0, "falladas": 0}
        except Exception as e:
            logger.error("run_now %s falló: %s", bid, e)
            return {"ok": False, "reason": str(e)}

    def _import_legacy_broadcast(self) -> None:
        try:
            for job in self.scheduler.scheduler.get_jobs():
                if job.id.startswith("dyn_broadcast_"):
                    continue
                if "broadcast" not in job.id.lower():
                    continue
                trig = job.trigger
                day_of_week_field = None
                hour_field = None
                minute_field = None
                for f in getattr(trig, "fields", []):
                    if f.name == "day_of_week":
                        day_of_week_field = str(f).split(",")[0].lower() or "sun"
                    elif f.name == "hour":
                        hour_field = int(str(f))
                    elif f.name == "minute":
                        minute_field = int(str(f))
                if None in (day_of_week_field, hour_field, minute_field):
                    continue
                dow_map = {
                    "0": "mon", "1": "tue", "2": "wed", "3": "thu",
                    "4": "fri", "5": "sat", "6": "sun",
                    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
                    "fri": "fri", "sat": "sat", "sun": "sun",
                }
                day_of_week_field = dow_map.get(day_of_week_field, "sun")
                bid = str(_uuid.uuid4())
                self.broadcasts[bid] = {
                    "id": bid,
                    "topic": "(broadcast legacy de la celda 12)",
                    "day_of_week": day_of_week_field,
                    "hour": hour_field,
                    "minute": minute_field,
                    "enabled": True,
                    "last_run_at": None,
                }
        except Exception as e:
            logger.warning("No se pudo importar broadcast legacy: %s", e)

    # ==================================================================
    # Argos: loaders de CSV + builders Plotly
    # ==================================================================
    def _argos_check_drive(self) -> None:
        if not self.argos_dir or not os.path.isdir(self.argos_dir):
            raise DriveNotConfiguredError(
                "Directorio de datos Argos no configurado. "
                "Define ARGOS_DIR y ejecuta el sistema Argos al menos una vez."
            )

    def _argos_load_csv(self, name: str):
        import pandas as pd

        path = self.argos_paths.get(name)
        if not path:
            return None
        with self._argos_lock:
            if name in self._argos_cache:
                return self._argos_cache[name]
            if not os.path.exists(path):
                return None
            try:
                df = pd.read_csv(path)
                self._argos_cache[name] = df
                return df
            except Exception as e:
                logger.error("Error leyendo Argos CSV %s: %s", name, e)
                return None

    def argos_refresh(self, name: Optional[str] = None) -> Dict[str, Any]:
        with self._argos_lock:
            if name:
                self._argos_cache.pop(name, None)
                return {"refreshed": [name]}
            keys = list(self._argos_cache.keys())
            self._argos_cache.clear()
            return {"refreshed": keys}

    def argos_files(self) -> Dict[str, Any]:
        out = []
        for name, path in self.argos_paths.items():
            info = {"name": name, "path": path, "exists": os.path.exists(path)}
            if info["exists"]:
                st = os.stat(path)
                info["size_bytes"] = st.st_size
                info["modified_at"] = datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat()
            out.append(info)
        return {"files": out}

    def chart_precios_regionales(self) -> Dict[str, Any]:
        self._argos_check_drive()
        df = self._argos_load_csv("precios_regionales")
        if df is None:
            raise FileNotFoundError(
                "CSV no encontrado: argos_precios_regionales.csv. "
                "Ejecuta la celda Argos del bot al menos una vez."
            )
        col_precio = "mu_h" if "mu_h" in df.columns else (
            "precio_mu" if "precio_mu" in df.columns else None
        )
        col_reg = "regional" if "regional" in df.columns else (
            "region" if "region" in df.columns else None
        )
        if col_precio is None or col_reg is None:
            raise ValueError(
                f"CSV argos_precios_regionales sin columnas esperadas. "
                f"Columnas disponibles: {list(df.columns)}"
            )
        agg = df.groupby(col_reg)[col_precio].mean().sort_values(ascending=True)
        layout = dict(_PLOTLY_BASE_LAYOUT)
        layout["xaxis"] = {**_PLOTLY_BASE_LAYOUT["xaxis"], "title": "Precio medio (COP)"}
        layout["yaxis"] = {**_PLOTLY_BASE_LAYOUT["yaxis"], "title": ""}
        layout["height"] = 260
        return {
            "data": [{
                "type": "bar",
                "orientation": "h",
                "x": [float(v) for v in agg.values],
                "y": [str(k) for k in agg.index],
                "marker": {"color": "#34d399"},
                "hovertemplate": "%{y}: $%{x:,.0f}<extra></extra>",
            }],
            "layout": layout,
            "stats": {
                "n_regionales": int(len(agg)),
                "precio_promedio": float(agg.mean()) if len(agg) else 0.0,
            },
        }

    def chart_intervalos_hdi(self, cod_municipio: Optional[str]) -> Dict[str, Any]:
        self._argos_check_drive()
        df = self._argos_load_csv("intervalos_hdi")
        if df is None:
            raise FileNotFoundError("CSV no encontrado: argos_intervalos_hdi.csv")
        needed = ["cod_municipio", "hdi_lower", "hdi_upper", "precio_mu"]
        for c in needed:
            if c not in df.columns:
                raise ValueError(
                    f"Columna '{c}' faltante en argos_intervalos_hdi.csv. "
                    f"Columnas disponibles: {list(df.columns)}"
                )
        work = df.copy()
        if cod_municipio:
            work = work[work["cod_municipio"].astype(str) == str(cod_municipio)]
        else:
            work["_amp"] = work["hdi_upper"] - work["hdi_lower"]
            work = work.sort_values("_amp", ascending=False).head(30)
        if "nombre_municipio" in work.columns:
            labels = work["nombre_municipio"].astype(str).tolist()
        else:
            labels = work["cod_municipio"].astype(str).tolist()

        layout = dict(_PLOTLY_BASE_LAYOUT)
        layout["xaxis"] = {**_PLOTLY_BASE_LAYOUT["xaxis"], "title": "Precio (COP)"}
        layout["yaxis"] = {**_PLOTLY_BASE_LAYOUT["yaxis"], "title": "", "automargin": True}
        layout["height"] = 320
        return {
            "data": [{
                "type": "scatter",
                "mode": "markers",
                "x": work["precio_mu"].astype(float).tolist(),
                "y": labels,
                "error_x": {
                    "type": "data",
                    "symmetric": False,
                    "array": (work["hdi_upper"] - work["precio_mu"]).astype(float).tolist(),
                    "arrayminus": (work["precio_mu"] - work["hdi_lower"]).astype(float).tolist(),
                    "color": "rgba(96, 165, 250, 0.5)",
                    "thickness": 1.5,
                    "width": 4,
                },
                "marker": {"color": "#60a5fa", "size": 8},
                "name": "HDI 94%",
                "hovertemplate": "%{y}<br>$%{x:,.0f}<extra></extra>",
            }],
            "layout": layout,
        }

    def chart_perfiles_alertas(self) -> Dict[str, Any]:
        self._argos_check_drive()
        df = self._argos_load_csv("perfiles_alertas")
        if df is None:
            raise FileNotFoundError("CSV no encontrado: argos_perfiles_alertas.csv")
        needed = ["cod_municipio", "perfil", "alerta"]
        for c in needed:
            if c not in df.columns:
                raise ValueError(
                    f"Columna '{c}' faltante en argos_perfiles_alertas.csv. "
                    f"Columnas disponibles: {list(df.columns)}"
                )
        counts = df["perfil"].value_counts()
        palette = ["#34d399", "#60a5fa", "#fbbf24", "#f87171", "#a78bfa"]
        donut_layout = dict(_PLOTLY_BASE_LAYOUT)
        donut_layout["height"] = 320
        donut_layout["showlegend"] = True
        donut_layout["legend"] = {"orientation": "v", "x": 1.05, "y": 0.5}
        donut = {
            "data": [{
                "type": "pie",
                "labels": counts.index.tolist(),
                "values": [int(v) for v in counts.values],
                "hole": 0.55,
                "marker": {"colors": palette[: len(counts)]},
                "textinfo": "percent",
                "hovertemplate": "%{label}: %{value} (%{percent})<extra></extra>",
            }],
            "layout": donut_layout,
        }

        def _alerta_rank(a):
            s = str(a)
            if "🔴" in s:
                return 0
            if "🟡" in s:
                return 1
            if "🟢" in s:
                return 2
            return 3

        tabla_df = df.copy()
        tabla_df["_rank"] = tabla_df["alerta"].apply(_alerta_rank)
        if "nombre_municipio" not in tabla_df.columns:
            tabla_df["nombre_municipio"] = tabla_df["cod_municipio"].astype(str)
        tabla_df = tabla_df.sort_values(["_rank", "nombre_municipio"])
        tabla = tabla_df[["cod_municipio", "nombre_municipio", "perfil", "alerta"]] \
            .astype(str).to_dict(orient="records")
        rojas = int((df["alerta"].astype(str).str.contains("🔴")).sum())
        amarillas = int((df["alerta"].astype(str).str.contains("🟡")).sum())
        verdes = int((df["alerta"].astype(str).str.contains("🟢")).sum())
        return {
            "donut": donut,
            "tabla": tabla,
            "stats": {
                "n_municipios": int(len(df)),
                "rojas": rojas,
                "amarillas": amarillas,
                "verdes": verdes,
            },
        }

    def chart_mapa(self) -> Dict[str, Any]:
        self._argos_check_drive()
        df = self._argos_load_csv("perfiles_alertas")
        if df is None:
            raise FileNotFoundError("CSV no encontrado: argos_perfiles_alertas.csv")
        if "lat" not in df.columns or "lon" not in df.columns:
            layout = dict(_PLOTLY_BASE_LAYOUT)
            layout["height"] = 480
            layout["mapbox"] = {
                "style": "open-street-map",
                "center": {"lat": 4.6, "lon": -74.1},
                "zoom": 4.5,
            }
            layout["annotations"] = [{
                "text": "Sin coordenadas en CSV. Añade columnas lat/lon a perfiles_alertas.",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "showarrow": False,
                "font": {"color": "#94a3b8"},
            }]
            return {
                "data": [],
                "layout": layout,
                "stats": {"n_puntos": 0, "n_sin_geocod": int(len(df))},
            }

        df_geo = df.dropna(subset=["lat", "lon"]).copy()
        color_map = {"🔴": "#ef4444", "🟡": "#eab308", "🟢": "#22c55e"}
        df_geo["_color"] = df_geo["alerta"].astype(str).apply(
            lambda s: next((v for k, v in color_map.items() if k in s), "#94a3b8")
        )
        if "nombre_municipio" not in df_geo.columns:
            df_geo["nombre_municipio"] = df_geo["cod_municipio"].astype(str)
        layout = dict(_PLOTLY_BASE_LAYOUT)
        layout["height"] = 480
        layout["mapbox"] = {
            "style": "open-street-map",
            "center": {
                "lat": float(df_geo["lat"].mean()),
                "lon": float(df_geo["lon"].mean()),
            },
            "zoom": 4.5,
        }
        layout["margin"] = {"l": 0, "r": 0, "t": 0, "b": 0}
        return {
            "data": [{
                "type": "scattermapbox",
                "lat": df_geo["lat"].astype(float).tolist(),
                "lon": df_geo["lon"].astype(float).tolist(),
                "text": df_geo["nombre_municipio"].astype(str).tolist(),
                "marker": {"size": 10, "color": df_geo["_color"].tolist(), "opacity": 0.85},
                "hovertemplate": "%{text}<extra></extra>",
            }],
            "layout": layout,
            "stats": {
                "n_puntos": int(len(df_geo)),
                "n_sin_geocod": int(len(df) - len(df_geo)),
            },
        }


__all__ = ["DashboardService", "DriveNotConfiguredError"]
