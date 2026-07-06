"""Modelos ORM SQLAlchemy (migrado de la celda 5 del notebook).

Incluye la máquina de estados de la ferretería (`EstadoFereteria`,
`ESTADOS_EN_CONVERSACION`, `TRANSICIONES_VALIDAS`) y todas las tablas del
esquema `public`, más `webhook_events_processed` para idempotencia de webhooks.
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class EstadoFereteria(Enum):
    """Ciclo de vida de una ferretería frente al bot.

    Estado especial NULL (None): existe en BD pero el bot aún no le ha escrito;
    únicas candidatas al broadcast inicial. Estados de negocio:
    primer_mensaje -> inicio -> cotizacion -> cierre -> terminado (final).
    `sin_respuesta` es una bandera interna del scheduler (>7 días de silencio).
    """

    primer_mensaje = "primer_mensaje"
    inicio = "inicio"
    sin_respuesta = "sin_respuesta"
    cotizacion = "cotizacion"
    cierre = "cierre"
    terminado = "terminado"


# Ferreterías con conversación activa. Se usa para el job nocturno que marca
# sin_respuesta tras 7 días de silencio. NO usar como filtro de broadcast:
# el broadcast solo va a `estado IS NULL`.
ESTADOS_EN_CONVERSACION = {
    EstadoFereteria.primer_mensaje,
    EstadoFereteria.inicio,
    EstadoFereteria.cotizacion,
    EstadoFereteria.cierre,
}

# Transiciones válidas (origen -> destinos permitidos). Cualquier transición
# fuera de este mapa se rechaza en transicionar_estado().
TRANSICIONES_VALIDAS = {
    None: {EstadoFereteria.primer_mensaje},
    EstadoFereteria.primer_mensaje: {
        EstadoFereteria.inicio,
        EstadoFereteria.terminado,
        EstadoFereteria.sin_respuesta,
    },
    EstadoFereteria.inicio: {
        EstadoFereteria.cotizacion,
        EstadoFereteria.cierre,
        EstadoFereteria.terminado,
        EstadoFereteria.sin_respuesta,
    },
    EstadoFereteria.cotizacion: {
        EstadoFereteria.cierre,
        EstadoFereteria.terminado,
        EstadoFereteria.sin_respuesta,
    },
    EstadoFereteria.cierre: {
        EstadoFereteria.terminado,
        EstadoFereteria.sin_respuesta,
    },
    EstadoFereteria.terminado: set(),  # estado final
    # sin_respuesta SOLO sale cuando la ferretería nos escribe (regla Meta 24h).
    EstadoFereteria.sin_respuesta: {
        EstadoFereteria.inicio,
        EstadoFereteria.cotizacion,
        EstadoFereteria.cierre,
        EstadoFereteria.terminado,
    },
}


class Regional(Base):
    __tablename__ = "regionales"
    __table_args__ = {"schema": "public"}
    regional = Column(String(100), primary_key=True)


class Producto(Base):
    __tablename__ = "productos"
    __table_args__ = {"schema": "public"}
    id_producto = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text)
    kilogramos = Column(Numeric(10, 2))


class Geografia(Base):
    __tablename__ = "geografia"
    __table_args__ = {"schema": "public"}
    cod_municipio = Column(String(50), primary_key=True)
    cod_departamento = Column(String(50), nullable=False)
    nombre_municipio = Column(String(100), nullable=False)
    nombre_departamento = Column(String(100), nullable=False)
    regional = Column(String(100), ForeignKey("public.regionales.regional"))
    latitud = Column(Numeric(10, 8))
    longitud = Column(Numeric(10, 8))
    regional_rel = relationship("Regional", backref="geografias")


class Ferreteria(Base):
    __tablename__ = "ferreterias"
    __table_args__ = {"schema": "public"}
    id_ferreteria = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nit = Column(String(50), unique=True)
    nombre_ferreteria = Column(String(150))
    nombre_propietario = Column(String(150))
    razon_social = Column(String(150))
    correo = Column(String(150), unique=True)
    num_telefono = Column(String(20), nullable=False)
    num_vetados = Column(ARRAY(String), nullable=True, default=list)
    direccion = Column(String(200))
    cod_municipio = Column(
        String(50), ForeignKey("public.geografia.cod_municipio"), nullable=False
    )
    regional = Column(
        String(100), ForeignKey("public.regionales.regional"), nullable=False
    )
    estado = Column(
        SQLEnum(
            EstadoFereteria,
            name="estado",
            create_type=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=True,
        default=None,
    )
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    latitud = Column(Numeric(10, 8))
    longitud = Column(Numeric(10, 8))
    municipio_rel = relationship("Geografia", backref="ferreterias")
    regional_rel = relationship("Regional", backref="ferreterias")


class HistorialInteraccion(Base):
    __tablename__ = "historial_interacciones"
    __table_args__ = {"schema": "public"}
    id_interaccion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_ferreteria = Column(
        UUID(as_uuid=True),
        ForeignKey("public.ferreterias.id_ferreteria"),
        nullable=False,
    )
    mensaje_usuario = Column(Text)
    respuesta_ia = Column(Text)
    tokens_consumidos = Column(Integer)
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    ferreteria_rel = relationship("Ferreteria", backref="interacciones")


class HistorialInteraccionAntiguo(Base):
    __tablename__ = "historial_interacciones_antiguos"
    __table_args__ = {"schema": "public"}
    id_interaccion = Column(UUID(as_uuid=True), primary_key=True)
    id_ferreteria = Column(
        UUID(as_uuid=True),
        ForeignKey("public.ferreterias.id_ferreteria"),
        nullable=False,
    )
    mensaje_usuario = Column(Text)
    respuesta_ia = Column(Text)
    tokens_consumidos = Column(Integer)
    fecha_registro = Column(DateTime(timezone=True))
    motivo_archivo = Column(Text, default="Migración/Archivo")


class MarcaProducto(Base):
    __tablename__ = "marcas_productos"
    __table_args__ = {"schema": "public"}
    id_marca = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre_marca = Column(String(150), nullable=False)
    regional = Column(String(100), ForeignKey("public.regionales.regional"))
    cod_municipio = Column(String(50), ForeignKey("public.geografia.cod_municipio"))
    regional_rel = relationship("Regional", backref="marcas")
    municipio_rel = relationship("Geografia", backref="marcas")


class Cotizacion(Base):
    __tablename__ = "cotizaciones"
    __table_args__ = {"schema": "public"}
    id_cotizacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_interaccion = Column(
        UUID(as_uuid=True),
        ForeignKey("public.historial_interacciones.id_interaccion"),
        nullable=False,
    )
    id_ferreteria = Column(
        UUID(as_uuid=True),
        ForeignKey("public.ferreterias.id_ferreteria"),
        nullable=False,
    )
    id_producto = Column(
        UUID(as_uuid=True),
        ForeignKey("public.productos.id_producto"),
        nullable=False,
    )
    id_marca = Column(
        UUID(as_uuid=True), ForeignKey("public.marcas_productos.id_marca")
    )
    precio = Column(
        Numeric(15, 2), CheckConstraint("precio >= 0"), nullable=False
    )
    disponibilidad = Column(String(100))
    confianza_extraccion = Column(Numeric(5, 2))
    info_solicitada_ferreteria = Column(Text)
    fecha_cotizacion = Column(DateTime(timezone=True), server_default=func.now())
    regional = Column(
        String(100), ForeignKey("public.regionales.regional"), nullable=False
    )
    interaccion_rel = relationship("HistorialInteraccion", backref="cotizaciones")
    ferreteria_rel = relationship("Ferreteria", backref="cotizaciones")
    producto_rel = relationship("Producto", backref="cotizaciones")
    marca_rel = relationship("MarcaProducto", backref="cotizaciones")
    regional_rel = relationship("Regional", backref="cotizaciones")


class PreferenciasUsuario(Base):
    __tablename__ = "preferencias_usuario"
    __table_args__ = {"schema": "public"}
    id_ferreteria = Column(
        UUID(as_uuid=True),
        ForeignKey("public.ferreterias.id_ferreteria"),
        primary_key=True,
    )
    tono_preferido_ia = Column(String(50), default="Profesional")
    ultima_actualizacion = Column(DateTime(timezone=True), server_default=func.now())
    ferreteria_rel = relationship("Ferreteria", backref="preferencias")


class WebhookEventoProcesado(Base):
    """Idempotencia de webhooks de WhatsApp (sustituye al set in-memory).

    Requiere la tabla en Supabase::

        CREATE TABLE IF NOT EXISTS public.webhook_events_processed (
          msg_id        text PRIMARY KEY,
          fecha_proceso timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_webhook_events_fecha
          ON public.webhook_events_processed(fecha_proceso);

    El job de mantenimiento purga registros >24h (Meta no reintenta tras eso).
    """

    __tablename__ = "webhook_events_processed"
    __table_args__ = {"schema": "public"}
    msg_id = Column(String(255), primary_key=True)
    fecha_proceso = Column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "EstadoFereteria",
    "ESTADOS_EN_CONVERSACION",
    "TRANSICIONES_VALIDAS",
    "Regional",
    "Producto",
    "Geografia",
    "Ferreteria",
    "HistorialInteraccion",
    "HistorialInteraccionAntiguo",
    "MarcaProducto",
    "Cotizacion",
    "PreferenciasUsuario",
    "WebhookEventoProcesado",
]
