"""Sistema Argos: inteligencia competitiva de precios (migrado de la celda 14).

Pipeline: muestreo estratificado + modelo bayesiano jerárquico (PyMC) +
clustering K-Means, con persistencia incremental en 3 CSV que consume el
dashboard. Reutiliza el engine de `DatabaseManager` y escribe en `argos_dir`.

Dependencias pesadas: pymc, arviz, scikit-learn, numpy, pandas.

NOTA DE COMPATIBILIDAD (heredada del notebook): el writer genera las columnas
`precios_mu` y `perfiles`, mientras que el lector del dashboard
(`DashboardService.chart_*`) espera `precio_mu` y `perfil`. Se conserva el
comportamiento original; si se quiere que el dashboard grafique los HDI y
perfiles, hay que unificar estos nombres en una de las dos capas.
"""
from __future__ import annotations

import os
import uuid
from typing import Dict, Optional

import numpy as np
import pandas as pd
import sqlalchemy as sa

from app.core.logging import get_logger

logger = get_logger(__name__)


def argos_csv_paths(argos_dir: str) -> Dict[str, str]:
    """Rutas de los 3 CSV de salida, derivadas del directorio Argos."""
    return {
        "precios_regionales": os.path.join(argos_dir, "argos_precios_regionales.csv"),
        "intervalos_hdi": os.path.join(argos_dir, "argos_intervalos_hdi.csv"),
        "perfiles_alertas": os.path.join(argos_dir, "argos_perfiles_alertas.csv"),
    }


def guardar_csv_incremental(nuevos_df: pd.DataFrame, ruta: str, nombre_tabla: str) -> None:
    """Crea el CSV (si no existe) o hace append respetando los registros previos.

    Cada registro nuevo recibe un UUID único en la columna `id`.
    """
    if "id" not in nuevos_df.columns:
        nuevos_df = nuevos_df.copy()
        nuevos_df.insert(0, "id", [str(uuid.uuid4()) for _ in range(len(nuevos_df))])

    if os.path.exists(ruta):
        existente = pd.read_csv(ruta)
        combinado = pd.concat([existente, nuevos_df], ignore_index=True)
        combinado.to_csv(ruta, index=False)
        logger.info(
            "📝 %s: APPEND → %s nuevos | total: %s filas",
            nombre_tabla, len(nuevos_df), len(combinado),
        )
    else:
        nuevos_df.to_csv(ruta, index=False)
        logger.info("🆕 %s: CREATE → %s filas iniciales", nombre_tabla, len(nuevos_df))


def ejecutar_sistema_integral_argos(
    db_manager, argos_dir: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """Sistema de inteligencia competitiva de precios para Argos.

    Combina muestreo estratificado + Bayes jerárquico + K-Means y genera/actualiza
    los 3 CSV de salida en `argos_dir`. Requiere un `DatabaseManager` con engine.
    """
    import pymc as pm
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    if not argos_dir:
        raise ValueError("argos_dir es obligatorio para persistir los CSV de Argos")
    os.makedirs(argos_dir, exist_ok=True)
    paths = argos_csv_paths(argos_dir)

    engine = db_manager.engine
    logger.info("🚀 Iniciando Sistema Integral Argos: Muestreo + Bayes + Clustering")

    # ─── 1. MUESTREO ESTRATIFICADO ──────────────────────────────────────────
    logger.info("📌 Paso 1: Configurando Muestreo Estratificado...")
    with engine.connect() as conn:
        query_vista = """
        CREATE OR REPLACE VIEW ferreterias_muestra AS
        SELECT DISTINCT ON (g.regional) f.*
        FROM   ferreterias f
        JOIN   geografia   g ON f.cod_municipio = g.cod_municipio
        WHERE  f.estado NOT IN ('terminado', 'sin_respuesta')
        ORDER  BY g.regional, RANDOM();
        """
        try:
            conn.execute(sa.text(query_vista))
            conn.commit()
            logger.info("✅ Vista 'ferreterias_muestra' creada/actualizada")
        except Exception as e:
            logger.warning("⚠️  No se pudo crear la vista: %s", e)

    # ─── 2. EXTRACCIÓN DE DATOS ─────────────────────────────────────────────
    logger.info("📌 Paso 2: Extrayendo datos de COTIZACIONES + FERRETERIAS...")
    query_raw = """
    SELECT
        c.id_cotizacion,
        c.id_ferreteria,
        c.id_producto,
        c.precio,
        c.confianza_extraccion,
        c.disponibilidad,
        c.regional,
        f.cod_municipio,
        g.nombre_municipio AS ciudad,
        m.nombre_marca
    FROM   cotizaciones        c
    JOIN   ferreterias         f ON c.id_ferreteria = f.id_ferreteria
    JOIN   geografia           g ON f.cod_municipio = g.cod_municipio
    LEFT JOIN marcas_productos m ON c.id_marca      = m.id_marca;
    """
    df = pd.read_sql(query_raw, engine)

    if df.empty:
        logger.warning("⚠️  No hay datos en COTIZACIONES aún. El proceso se detiene.")
        return None

    tasa_exito = df["precio"].notnull().mean()
    confianza_llm = df["confianza_extraccion"].mean()
    logger.info(
        "📊 Tasa Éxito: %.2f%%  |  Confianza LLM: %.2f (meta ≥ 0.80)",
        tasa_exito * 100, confianza_llm,
    )

    # ─── 3. MODELO BAYESIANO JERÁRQUICO POR PRODUCTO × REGIONAL ─────────────
    logger.info("📌 Paso 3: Calculando Precios Estimados (μ̂) con PyMC...")
    df_clean = df[df["confianza_extraccion"] >= 0.80].dropna(subset=["precio"]).copy()

    if df_clean.empty:
        logger.warning("⚠️  Sin datos con confianza ≥ 0.80. Aumenta el corpus.")
        return None

    df_clean["grupo"] = df_clean["id_producto"].astype(str) + "|" + df_clean["regional"]
    grupos = df_clean["grupo"].unique()
    grupo_idx = pd.Categorical(df_clean["grupo"]).codes

    with pm.Model() as modelo:  # noqa: F841
        mu_0 = pm.Normal("mu_0", mu=30_000, sigma=5_000)
        tau = pm.HalfNormal("tau", sigma=5_000)
        sigma = pm.HalfNormal("sigma", sigma=2_000)
        mu_h = pm.Normal("mu_h", mu=mu_0, sigma=tau, shape=len(grupos))
        _ = pm.Normal(
            "y_obs", mu=mu_h[grupo_idx], sigma=sigma,
            observed=df_clean["precio"].astype(float),
        )
        trace = pm.sample(
            1000, chains=2, progressbar=True, target_accept=0.95,
            return_inferencedata=True,
        )

    precios_mu = trace.posterior["mu_h"].mean(dim=["chain", "draw"]).values

    # Extracción robusta del HDI (compatible con distintas versiones de ArviZ).
    import arviz as az

    hdi_ds = az.hdi(trace.posterior, hdi_prob=0.95)
    hdi_arr = hdi_ds["mu_h"].values  # shape (n_grupos, 2)
    if hdi_arr.shape[-1] != 2:
        hdi_arr = hdi_arr.T
    hdi_vals = hdi_arr

    # ─── 4. CSV #1: PRECIOS REGIONALES (mu_h) POR PRODUCTO ──────────────────
    logger.info("📌 Paso 4: Persistiendo CSV de precios regionales por producto...")
    df_precios = pd.DataFrame({
        "id_producto": [g.split("|")[0] for g in grupos],
        "mu_h": precios_mu,
        "regional": [g.split("|")[1] for g in grupos],
    })
    guardar_csv_incremental(
        df_precios, paths["precios_regionales"], "argos_precios_regionales"
    )

    # ─── 5. CSV #2: HDI + PRECIOS_MU POR MUNICIPIO ──────────────────────────
    logger.info("📌 Paso 5: Persistiendo CSV de intervalos HDI por municipio...")
    df_clean["grupo_idx"] = grupo_idx
    municipio_por_grupo = (
        df_clean.groupby("grupo_idx")["cod_municipio"]
        .agg(lambda s: s.mode().iloc[0])
        .reindex(range(len(grupos)))
        .values
    )

    df_hdi = pd.DataFrame({
        "cod_municipio": municipio_por_grupo,
        "precios_mu": precios_mu,  # NOTA: dashboard espera 'precio_mu' (ver docstring)
        "hdi_lower": hdi_vals[:, 0],
        "hdi_upper": hdi_vals[:, 1],
        "hdi_vals": [
            f"[{hdi_vals[i, 0]:.2f}, {hdi_vals[i, 1]:.2f}]" for i in range(len(hdi_vals))
        ],
    })
    guardar_csv_incremental(df_hdi, paths["intervalos_hdi"], "argos_intervalos_hdi")

    # ─── 6. CLUSTERING K-MEANS Y ALERTAS ────────────────────────────────────
    logger.info("📌 Paso 6: Segmentando con K-Means y generando alertas...")
    scaler = StandardScaler()
    precios_norm = scaler.fit_transform(precios_mu.reshape(-1, 1))
    k = min(3, len(grupos))
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    clusters = kmeans.fit_predict(precios_norm)

    perfiles_map = {0: "Paridad", 1: "Argos Dominante", 2: "Competencia por encima"}

    # ─── 7. CSV #3: PERFILES + ALERTAS POR MUNICIPIO ────────────────────────
    logger.info("📌 Paso 7: Persistiendo CSV de perfiles y alertas por municipio...")
    df_perfiles = pd.DataFrame({
        "cod_municipio": municipio_por_grupo,
        "perfiles": [  # NOTA: dashboard espera 'perfil' (ver docstring)
            perfiles_map.get(c, "Análisis Exploratorio") for c in clusters
        ],
        "alerta": [
            alerta_para(precios_mu[i], hdi_vals[i, 0], hdi_vals[i, 1])
            for i in range(len(grupos))
        ],
    })
    guardar_csv_incremental(df_perfiles, paths["perfiles_alertas"], "argos_perfiles_alertas")

    # ─── 8. RESUMEN FINAL ───────────────────────────────────────────────────
    logger.info("✅ PROCESO ARGOS COMPLETADO")
    resumen = pd.DataFrame({
        "Regional": [g.split("|")[1] for g in grupos],
        "Producto": [g.split("|")[0][:8] + "..." for g in grupos],
        "Municipio": municipio_por_grupo,
        "Precio_mu": precios_mu.round(2),
        "Perfil": [perfiles_map.get(c, "?") for c in clusters],
        "Alerta": [
            alerta_para(precios_mu[i], hdi_vals[i, 0], hdi_vals[i, 1])
            for i in range(len(grupos))
        ],
    })
    return resumen


def alerta_para(precio_est: float, lo: float, hi: float) -> str:
    """Clasifica una alerta según amplitud del HDI y nivel de precio estimado."""
    if (hi - lo) > 2500:
        return "🟡 Región con datos insuficientes"
    elif precio_est > 32_500:
        return "🔴 Precio competidor por debajo de Argos"
    else:
        return "✅ Estable"


__all__ = [
    "argos_csv_paths",
    "guardar_csv_incremental",
    "ejecutar_sistema_integral_argos",
    "alerta_para",
]
