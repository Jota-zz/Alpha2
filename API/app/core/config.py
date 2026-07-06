"""Configuración de la aplicación (migrado de las celdas 4 y 8 del notebook).

Reemplaza los patrones específicos de Colab:
- `userdata.get(...)` + dict `SECRETS`  ->  `Settings` de pydantic-settings
  que lee variables de entorno / archivo `.env`.
- El parseo manual de `DB_HOST` (host:puerto) se conserva en `database_url`.

Se mantienen las dataclasses de configuración de dominio
(`WhatsAppConfig`, `AnthropicConfig`, `DispatcherConfig`, `OperatingHoursConfig`
y `OperatingHoursGate`) para no romper las interfaces que consumen las celdas
posteriores (cliente WhatsApp, cliente Anthropic, dispatcher, scheduler...).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------
SUPPORTED_MESSAGE_TYPES = {"text"}
MAX_USER_MESSAGE_LENGTH = 1000

# Zona horaria del bot. Bogotá no tiene DST, así que ZoneInfo es estable y
# equivale siempre a UTC-5.
BOGOTA_TZ = ZoneInfo("America/Bogota")


class MessageType(str, Enum):
    TEXT = "text"
    TEMPLATE = "template"
    IMAGE = "image"
    DOCUMENT = "document"


# ---------------------------------------------------------------------------
# Settings (variables de entorno / .env) — reemplaza SECRETS de Colab
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """Configuración leída desde el entorno o un archivo `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # WhatsApp / Meta
    wa_token: str = Field(..., validation_alias="WA_TOKEN")
    wa_phone_id: str = Field(..., validation_alias="WA_PHONE_ID")
    webhook_verify_token: str = Field(..., validation_alias="WEBHOOK_VERIFY_TOKEN")
    wa_api_version: str = Field("v17.0", validation_alias="WA_API_VERSION")

    # Anthropic
    anthropic_api_key: str = Field(..., validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-sonnet-4-6", validation_alias="ANTHROPIC_MODEL")

    # Base de datos
    db_user: str = Field(..., validation_alias="DB_USER")
    db_password: str = Field(..., validation_alias="DB_PASSWORD")
    db_host: str = Field(..., validation_alias="DB_HOST")
    db_name: str = Field(..., validation_alias="DB_NAME")
    db_port: int = Field(5432, validation_alias="DB_PORT")

    # Opcional (solo dev / túnel local)
    ngrok_auth_token: Optional[str] = Field(None, validation_alias="NGROK_AUTH_TOKEN")

    # Rutas de recursos (antes en Google Drive; ahora configurables por entorno)
    baseprompt_xml_path: Optional[str] = Field(None, validation_alias="BASEPROMPT_XML_PATH")
    csv_cotizaciones_pdf: str = Field(
        "cotizaciones_pdf.csv", validation_alias="CSV_COTIZACIONES_PDF"
    )
    argos_dir: Optional[str] = Field(None, validation_alias="ARGOS_DIR")

    @property
    def db_host_only(self) -> str:
        """Host sin puerto, aun si `DB_HOST` viene como ``host:puerto``."""
        return self.db_host.split(":", 1)[0]

    @property
    def db_port_effective(self) -> int:
        """Puerto embebido en `DB_HOST` si existe; si no, `db_port`."""
        if ":" in self.db_host:
            try:
                return int(self.db_host.split(":", 1)[1])
            except ValueError:
                return self.db_port
        return self.db_port

    @property
    def database_url(self) -> str:
        """URL SQLAlchemy para PostgreSQL (psycopg2)."""
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host_only}:{self.db_port_effective}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de `Settings` (patrón dependencia FastAPI)."""
    return Settings()


# ---------------------------------------------------------------------------
# Config de WhatsApp
# ---------------------------------------------------------------------------
@dataclass
class WhatsAppConfig:
    token: str
    phone_id: str
    verify_token: str
    api_version: str = "v17.0"

    @property
    def messages_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_id}/messages"

    def validate(self) -> None:
        if not self.token or not self.phone_id:
            raise ValueError("WA_TOKEN y WA_PHONE_ID son obligatorios")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "WhatsAppConfig":
        return cls(
            token=settings.wa_token,
            phone_id=settings.wa_phone_id,
            verify_token=settings.webhook_verify_token,
            api_version=settings.wa_api_version,
        )


# ---------------------------------------------------------------------------
# Config de Anthropic
#
# Modelos vigentes (abril 2026):
#   - claude-opus-4-7              (más capaz)
#   - claude-opus-4-6
#   - claude-sonnet-4-6           (mejor relación calidad/costo, recomendado)
#   - claude-haiku-4-5-20251001   (más rápido y económico)
# ---------------------------------------------------------------------------
@dataclass
class AnthropicConfig:
    api_key: str
    model_name: str = "claude-sonnet-4-6"
    max_tokens: int = 120
    temperature: float = 0.7
    typo_rate: float = 0.012  # Probabilidad por carácter de error de dedo.
    history_limit: int = 10   # Interacciones recientes a inyectar como contexto.
    # Plantilla Meta para primer contacto (regla 24h). Deben coincidir EXACTO
    # con la plantilla aprobada en Meta Business Manager; el bot no las inventa.
    outreach_template_name: str = "saludo"
    outreach_template_lang: str = "es_CO"
    # Tope de tokens para generar SOLO el parámetro {{1}} (frase corta).
    outreach_param_max_tokens: int = 60

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY es obligatorio")
        if not self.outreach_template_name:
            raise ValueError("outreach_template_name no puede estar vacío")
        if not self.outreach_template_lang:
            raise ValueError("outreach_template_lang no puede estar vacío")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "AnthropicConfig":
        return cls(api_key=settings.anthropic_api_key, model_name=settings.anthropic_model)


# ---------------------------------------------------------------------------
# Config del dispatcher (delays humanos + anti-bloqueo Meta)
#
# v1.4.2: un solo tiempo de "escucha activa" (listen_window_*) durante el cual
# el bot espera más mensajes del MISMO cliente; cada mensaje nuevo reinicia el
# timer (debounce). Al expirar sin novedades, responde de inmediato.
# ---------------------------------------------------------------------------
@dataclass
class DispatcherConfig:
    listen_window_min_s: int = 120
    listen_window_max_s: int = 300
    outreach_delay_min_s: int = 60
    outreach_delay_max_s: int = 300
    inter_chat_min_s: int = 120
    inter_chat_max_s: int = 300

    def validate(self) -> None:
        if not (0 <= self.listen_window_min_s <= self.listen_window_max_s):
            raise ValueError("listen_window_min/max inválidos")
        if not (0 <= self.outreach_delay_min_s <= self.outreach_delay_max_s):
            raise ValueError("outreach_delay_min/max inválidos")
        if not (0 <= self.inter_chat_min_s <= self.inter_chat_max_s):
            raise ValueError("inter_chat_min/max inválidos")


# ---------------------------------------------------------------------------
# Ventana horaria de operación (v1.1) — hora LOCAL de Bogotá
# ---------------------------------------------------------------------------
@dataclass
class OperatingHoursConfig:
    """Ventana horaria del bot en hora LOCAL DE BOGOTÁ.

    `windows` mapea weekday (0=Lun..6=Dom) → (hora_apertura, hora_cierre) o
    None para "cerrado todo el día". Horas enteras 0..24 (24 = medianoche
    siguiente, exclusivo).
    """

    windows: Dict[int, Optional[Tuple[int, int]]]

    def validate(self) -> None:
        for wd, w in self.windows.items():
            if not (0 <= wd <= 6):
                raise ValueError(f"weekday inválido: {wd}")
            if w is None:
                continue
            o, c = w
            if not (0 <= o < c <= 24):
                raise ValueError(f"ventana inválida en weekday {wd}: ({o}, {c})")


class OperatingHoursGate:
    """Decide si el bot está dentro de su ventana operativa (hora Bogotá).

    Métodos puros, sin estado mutable: seguros desde varios threads. Si se pasa
    un `now` naive se ASUME UTC (caso típico en servidores); si es aware, se
    respeta su tzinfo. Siempre se compara en hora local de Bogotá.
    """

    def __init__(self, config: OperatingHoursConfig, tz: ZoneInfo = BOGOTA_TZ):
        self.config = config
        self.config.validate()
        self.tz = tz

    def _to_local(self, now: Optional[datetime]) -> datetime:
        """Normaliza `now` a hora local del gate; devuelve siempre AWARE."""
        if now is None:
            return datetime.now(self.tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(self.tz)

    def is_open(self, now: Optional[datetime] = None) -> bool:
        """¿El bot está dentro de la ventana operativa AHORA (hora Bogotá)?"""
        local = self._to_local(now)
        window = self.config.windows.get(local.weekday())
        if window is None:
            return False
        opens_h, closes_h = window
        local_minutes = local.hour * 60 + local.minute
        return (opens_h * 60) <= local_minutes < (closes_h * 60)

    def next_open(self, now: Optional[datetime] = None) -> datetime:
        """Próximo datetime AWARE (Bogotá) en que abre la ventana.

        Si AHORA ya está dentro, devuelve `now` en Bogotá. Busca hasta 8 días;
        si no encuentra, lanza error (configuración con todos los días cerrados).
        """
        local = self._to_local(now)
        if self.is_open(local):
            return local

        cursor = local
        for _ in range(8):
            window = self.config.windows.get(cursor.weekday())
            if window is not None:
                opens_h, _ = window
                opens_at = cursor.replace(hour=opens_h, minute=0, second=0, microsecond=0)
                if cursor.date() == local.date():
                    if local.hour < opens_h:
                        return opens_at
                else:
                    return opens_at
            cursor = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        raise RuntimeError(
            "OperatingHoursGate.next_open: no hay ventana operativa en los "
            "próximos 8 días — revisa OperatingHoursConfig.windows"
        )

    def closes_at(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """Si AHORA está dentro de ventana, devuelve cuándo cierra HOY; si no, None."""
        local = self._to_local(now)
        if not self.is_open(local):
            return None
        window = self.config.windows[local.weekday()]
        _, closes_h = window
        if closes_h == 24:
            return (local + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return local.replace(hour=closes_h, minute=0, second=0, microsecond=0)
