-- =====================================================================
-- Esquema de base de datos para Alpha Bot API
-- Generado desde los modelos SQLAlchemy (app/models). PostgreSQL / Supabase.
--
-- Uso en Supabase: pega este archivo en el SQL Editor y ejecútalo.
-- Uso en psql:      psql "$DATABASE_URL" -f scripts/schema.sql
-- Es idempotente (IF NOT EXISTS / DO $$ ... $$), se puede correr varias veces.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS public;

-- Tipo ENUM 'estado' (los modelos usan create_type=False, así que debe existir).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'estado' AND n.nspname = 'public'
    ) THEN
        CREATE TYPE public.estado AS ENUM ('primer_mensaje', 'inicio', 'sin_respuesta', 'cotizacion', 'cierre', 'terminado');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.productos (
	id_producto UUID NOT NULL, 
	nombre VARCHAR(150) NOT NULL, 
	descripcion TEXT, 
	kilogramos NUMERIC(10, 2), 
	PRIMARY KEY (id_producto)
);

CREATE TABLE IF NOT EXISTS public.regionales (
	regional VARCHAR(100) NOT NULL, 
	PRIMARY KEY (regional)
);

CREATE TABLE IF NOT EXISTS public.webhook_events_processed (
	msg_id VARCHAR(255) NOT NULL, 
	fecha_proceso TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (msg_id)
);

CREATE TABLE IF NOT EXISTS public.geografia (
	cod_municipio VARCHAR(50) NOT NULL, 
	cod_departamento VARCHAR(50) NOT NULL, 
	nombre_municipio VARCHAR(100) NOT NULL, 
	nombre_departamento VARCHAR(100) NOT NULL, 
	regional VARCHAR(100), 
	latitud NUMERIC(10, 8), 
	longitud NUMERIC(10, 8), 
	PRIMARY KEY (cod_municipio), 
	FOREIGN KEY(regional) REFERENCES public.regionales (regional)
);

CREATE TABLE IF NOT EXISTS public.ferreterias (
	id_ferreteria UUID NOT NULL, 
	nit VARCHAR(50), 
	nombre_ferreteria VARCHAR(150), 
	nombre_propietario VARCHAR(150), 
	razon_social VARCHAR(150), 
	correo VARCHAR(150), 
	num_telefono VARCHAR(20) NOT NULL, 
	num_vetados VARCHAR[], 
	direccion VARCHAR(200), 
	cod_municipio VARCHAR(50) NOT NULL, 
	regional VARCHAR(100) NOT NULL, 
	estado estado, 
	fecha_registro TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	latitud NUMERIC(10, 8), 
	longitud NUMERIC(10, 8), 
	PRIMARY KEY (id_ferreteria), 
	UNIQUE (nit), 
	UNIQUE (correo), 
	FOREIGN KEY(cod_municipio) REFERENCES public.geografia (cod_municipio), 
	FOREIGN KEY(regional) REFERENCES public.regionales (regional)
);

CREATE TABLE IF NOT EXISTS public.marcas_productos (
	id_marca UUID NOT NULL, 
	nombre_marca VARCHAR(150) NOT NULL, 
	regional VARCHAR(100), 
	cod_municipio VARCHAR(50), 
	PRIMARY KEY (id_marca), 
	FOREIGN KEY(regional) REFERENCES public.regionales (regional), 
	FOREIGN KEY(cod_municipio) REFERENCES public.geografia (cod_municipio)
);

CREATE TABLE IF NOT EXISTS public.historial_interacciones (
	id_interaccion UUID NOT NULL, 
	id_ferreteria UUID NOT NULL, 
	mensaje_usuario TEXT, 
	respuesta_ia TEXT, 
	tokens_consumidos INTEGER, 
	fecha_registro TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id_interaccion), 
	FOREIGN KEY(id_ferreteria) REFERENCES public.ferreterias (id_ferreteria)
);

CREATE TABLE IF NOT EXISTS public.historial_interacciones_antiguos (
	id_interaccion UUID NOT NULL, 
	id_ferreteria UUID NOT NULL, 
	mensaje_usuario TEXT, 
	respuesta_ia TEXT, 
	tokens_consumidos INTEGER, 
	fecha_registro TIMESTAMP WITH TIME ZONE, 
	motivo_archivo TEXT, 
	PRIMARY KEY (id_interaccion), 
	FOREIGN KEY(id_ferreteria) REFERENCES public.ferreterias (id_ferreteria)
);

CREATE TABLE IF NOT EXISTS public.preferencias_usuario (
	id_ferreteria UUID NOT NULL, 
	tono_preferido_ia VARCHAR(50), 
	ultima_actualizacion TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id_ferreteria), 
	FOREIGN KEY(id_ferreteria) REFERENCES public.ferreterias (id_ferreteria)
);

CREATE TABLE IF NOT EXISTS public.cotizaciones (
	id_cotizacion UUID NOT NULL, 
	id_interaccion UUID NOT NULL, 
	id_ferreteria UUID NOT NULL, 
	id_producto UUID NOT NULL, 
	id_marca UUID, 
	precio NUMERIC(15, 2) NOT NULL CHECK (precio >= 0), 
	disponibilidad VARCHAR(100), 
	confianza_extraccion NUMERIC(5, 2), 
	info_solicitada_ferreteria TEXT, 
	fecha_cotizacion TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	regional VARCHAR(100) NOT NULL, 
	PRIMARY KEY (id_cotizacion), 
	FOREIGN KEY(id_interaccion) REFERENCES public.historial_interacciones (id_interaccion), 
	FOREIGN KEY(id_ferreteria) REFERENCES public.ferreterias (id_ferreteria), 
	FOREIGN KEY(id_producto) REFERENCES public.productos (id_producto), 
	FOREIGN KEY(id_marca) REFERENCES public.marcas_productos (id_marca), 
	FOREIGN KEY(regional) REFERENCES public.regionales (regional)
);

-- Índice para la purga de eventos de webhook (>24h) del job de mantenimiento.
CREATE INDEX IF NOT EXISTS idx_webhook_events_fecha
    ON public.webhook_events_processed (fecha_proceso);

-- =====================================================================
-- Datos semilla mínimos requeridos por el bot
-- crear_ferreteria_minima() busca la regional 'CENTRO' y el municipio '05001';
-- sin estas filas, el registro automático de una ferretería nueva falla.
-- =====================================================================
INSERT INTO public.regionales (regional) VALUES ('CENTRO')
    ON CONFLICT (regional) DO NOTHING;

INSERT INTO public.geografia
    (cod_municipio, cod_departamento, nombre_municipio, nombre_departamento, regional)
VALUES
    ('05001', '05', 'Medellín', 'Antioquia', 'CENTRO')
    ON CONFLICT (cod_municipio) DO NOTHING;
