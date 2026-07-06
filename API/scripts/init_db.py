#!/usr/bin/env python3
"""Crea el esquema de base de datos del Alpha Bot API.

Usa los mismos modelos SQLAlchemy que el bot (`app.models`) y la configuración
de `.env` (`app.core.config.Settings`), de modo que el esquema creado siempre
coincide con lo que el código espera.

Qué hace (todo idempotente):
  1. Crea el schema `public` si no existe.
  2. Crea el tipo ENUM `estado` (los modelos usan create_type=False).
  3. Crea todas las tablas (`Base.metadata.create_all`).
  4. Crea el índice de purga de `webhook_events_processed`.
  5. Inserta los datos semilla mínimos (regional 'CENTRO', municipio '05001')
     que necesita `crear_ferreteria_minima`.

Uso:
    python -m scripts.init_db            # crea todo + semillas
    python -m scripts.init_db --no-seed  # crea todo sin semillas
    python -m scripts.init_db --drop     # ⚠️ elimina todo y lo recrea

Requiere las variables de entorno / .env de conexión (DB_USER, DB_PASSWORD,
DB_HOST, DB_NAME, DB_PORT).
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.db.base import Base
from app.models import EstadoFereteria

# Importa el paquete de modelos para registrar todas las tablas en el metadata.
import app.models  # noqa: F401


def _enum_values_sql() -> str:
    return ", ".join(f"'{e.value}'" for e in EstadoFereteria)


def create_enum_type(conn) -> None:
    """Crea el tipo ENUM `public.estado` si no existe."""
    conn.execute(text(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = 'estado' AND n.nspname = 'public'
            ) THEN
                CREATE TYPE public.estado AS ENUM ({_enum_values_sql()});
            END IF;
        END
        $$;
        """
    ))


def create_index_and_seed(conn, seed: bool) -> None:
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_webhook_events_fecha "
        "ON public.webhook_events_processed (fecha_proceso);"
    ))
    if not seed:
        return
    conn.execute(text(
        "INSERT INTO public.regionales (regional) VALUES ('CENTRO') "
        "ON CONFLICT (regional) DO NOTHING;"
    ))
    conn.execute(text(
        "INSERT INTO public.geografia "
        "(cod_municipio, cod_departamento, nombre_municipio, nombre_departamento, regional) "
        "VALUES ('05001', '05', 'Medellín', 'Antioquia', 'CENTRO') "
        "ON CONFLICT (cod_municipio) DO NOTHING;"
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa la BD del Alpha Bot.")
    parser.add_argument(
        "--drop", action="store_true",
        help="⚠️ Elimina todas las tablas y el tipo enum antes de recrear.",
    )
    parser.add_argument(
        "--no-seed", action="store_true",
        help="No insertar los datos semilla (regional CENTRO, municipio 05001).",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)

    print(f"→ Conectando a {settings.db_host_only}:{settings.db_port_effective}"
          f"/{settings.db_name} como {settings.db_user}")

    if args.drop:
        confirm = input("⚠️  Esto ELIMINA todas las tablas. Escribe 'SI' para continuar: ")
        if confirm.strip().upper() != "SI":
            print("Cancelado.")
            return 1
        print("→ Eliminando tablas...")
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TYPE IF EXISTS public.estado;"))

    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS public;"))
        create_enum_type(conn)

    print("→ Creando tablas...")
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        create_index_and_seed(conn, seed=not args.no_seed)

    tablas = sorted(t.name for t in Base.metadata.sorted_tables)
    print(f"✅ Listo. {len(tablas)} tablas: {', '.join(tablas)}")
    if not args.no_seed:
        print("   Semillas: regional 'CENTRO', municipio '05001' (Medellín).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
