/**
 * Parsers de los CSVs Argos + transformaciones a las shapes que esperan los
 * charts existentes ({ data, layout, stats }).
 *
 * Estructura de los CSVs:
 *   - argos_precios_regionales.csv
 *       id, id_producto, mu_h, regional
 *   - argos_intervalos_hdi.csv
 *       id, cod_municipio, precios_mu, hdi_lower, hdi_upper, hdi_vals
 *   - argos_perfiles_alertas.csv
 *       id, cod_municipio, perfiles, alerta
 *
 * Salida: para cada chart generamos `data` (traces Plotly) y `layout` listos
 * para pasarle al wrapper PlotChart, replicando lo que antes hacía el
 * backend Flask.
 */

import Papa from 'papaparse';
import { getMunicipio, nombreMunicipio } from '../config/municipios';
import { MARCAS } from '../config/marcas';

// ────────────────────────────────────────────────────────────────────
// Parsing CSV base
// ────────────────────────────────────────────────────────────────────

/**
 * Parsea texto CSV con PapaParse. `dynamicTyping` convierte automáticamente
 * números y booleans. `skipEmptyLines` evita filas vacías al final del file.
 */
export function parseCsv(text) {
  const out = Papa.parse(text, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
    transformHeader: (h) => h.trim(),
  });
  if (out.errors?.length) {
    // Sólo logueamos: PapaParse devuelve filas válidas aunque algunas fallen.
    // eslint-disable-next-line no-console
    console.warn('[csv] errores parseando', out.errors.slice(0, 3));
  }
  return out.data;
}

// ────────────────────────────────────────────────────────────────────
// Theme tokens compartidos (alineados con design-system.css)
// ────────────────────────────────────────────────────────────────────

const PLOTLY_THEME = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#cbd5e1', family: 'Space Grotesk, system-ui' },
  margin: { l: 80, r: 20, t: 20, b: 40 },
};

const PERFIL_COLORS = {
  'Argos Dominante': '#34d399', // emerald
  Paridad: '#fbbf24', // amber
  'Competencia por encima': '#f87171', // red
};

const ALERTA_COLORS = {
  '🔴': '#ef4444',
  '🟡': '#f59e0b',
  '🟢': '#10b981',
};

function alertEmoji(alerta) {
  if (!alerta) return '';
  const m = alerta.match(/^([🔴🟡🟢])/);
  return m ? m[1] : '';
}

// ────────────────────────────────────────────────────────────────────
// Helpers genéricos para derivar valores únicos de un CSV
// (usados por los selects de filtros en los charts).
// ────────────────────────────────────────────────────────────────────

/**
 * Devuelve los valores únicos de `field` presentes en el texto CSV.
 * Útil para alimentar selects de filtros sin re-parsear N veces.
 */
export function uniqueValues(text, field) {
  if (!text) return [];
  const rows = parseCsv(text);
  const set = new Set();
  for (const r of rows) {
    const v = r?.[field];
    if (v === null || v === undefined || v === '') continue;
    set.add(v);
  }
  return [...set].sort((a, b) => String(a).localeCompare(String(b)));
}

/**
 * Opciones para el select de marca: prioriza los valores reales del CSV
 * si existen; en caso contrario usa el catálogo estático MARCAS.
 */
export function marcasOptions(text) {
  const fromCsv = uniqueValues(text, 'marca');
  if (fromCsv.length > 0) return fromCsv;
  return [...MARCAS];
}

/**
 * Aplica un objeto de filtros `{ field: value }` a un array de filas.
 *
 * Reglas:
 *  - Si el valor es null/''/undefined, se ignora ese filtro.
 *  - Si la columna no existe en NINGUNA fila del dataset (p. ej. el CSV
 *    todavía no expone `marca`), también se ignora ese filtro, en lugar
 *    de devolver 0 filas. Así los selects de marca degradan grácilmente
 *    si el CSV no incluye aún la columna.
 *  - Comparación case-insensitive en strings (los nombres de marca pueden
 *    venir en distinta capitalización: "Argos" vs "argos").
 */
function applyFilters(rows, filters = {}) {
  const active = Object.entries(filters).filter(
    ([, v]) => v !== null && v !== undefined && v !== ''
  );
  if (active.length === 0) return rows;
  // Detectamos columnas inexistentes para no aniquilar el dataset.
  const validEntries = active.filter(([k]) =>
    rows.some((r) => r?.[k] !== undefined && r?.[k] !== null && r?.[k] !== '')
  );
  if (validEntries.length === 0) return rows;
  return rows.filter((r) =>
    validEntries.every(([k, v]) => {
      const cell = r?.[k];
      if (cell === undefined || cell === null) return false;
      return String(cell).toLowerCase() === String(v).toLowerCase();
    })
  );
}

// ────────────────────────────────────────────────────────────────────
// 1. Precios Regionales (bar chart horizontal)
// ────────────────────────────────────────────────────────────────────

/**
 * @param {string} text - CSV crudo.
 * @param {{producto?: string|null, regional?: string|null, marca?: string|null}} filters - filtros opcionales.
 */
export function buildPreciosRegionales(text, filters = {}) {
  const allRows = parseCsv(text);
  const rows = applyFilters(allRows, {
    id_producto: filters.producto ?? null,
    regional: filters.regional ?? null,
    marca: filters.marca ?? null,
  });
  // Agrupar por regional, promediar mu_h
  const agg = new Map(); // regional → { sum, n }
  for (const r of rows) {
    if (!r.regional || r.mu_h == null) continue;
    const cur = agg.get(r.regional) || { sum: 0, n: 0 };
    cur.sum += Number(r.mu_h);
    cur.n += 1;
    agg.set(r.regional, cur);
  }
  // Ordenar por precio descendente para que el bar quede agradable
  const ordered = [...agg.entries()]
    .map(([reg, { sum, n }]) => ({
      regional: reg,
      precio: sum / n,
      n,
    }))
    .sort((a, b) => a.precio - b.precio); // asc → barra más grande arriba en h

  const labels = ordered.map((r) => r.regional);
  const values = ordered.map((r) => Math.round(r.precio));

  const trace = {
    type: 'bar',
    orientation: 'h',
    x: values,
    y: labels,
    text: values.map((v) => `$${v.toLocaleString('es-CO')}`),
    textposition: 'outside',
    marker: {
      color: '#a3e635',
      line: { color: 'rgba(163,230,53,0.4)', width: 1 },
    },
    hovertemplate:
      '<b>%{y}</b><br>$%{x:,.0f} promedio<br>%{customdata} registros<extra></extra>',
    customdata: ordered.map((r) => r.n),
  };

  const layout = {
    ...PLOTLY_THEME,
    height: 260,
    margin: { l: 130, r: 60, t: 20, b: 40 },
    xaxis: { title: 'Precio promedio (COP)', gridcolor: 'rgba(255,255,255,0.05)' },
    yaxis: { automargin: true },
  };

  const totalAvg =
    values.length > 0 ? values.reduce((a, b) => a + b, 0) / values.length : 0;

  return {
    data: [trace],
    layout,
    stats: {
      n_regionales: ordered.length,
      precio_promedio: totalAvg,
      n_filas: rows.length,
    },
  };
}

// ────────────────────────────────────────────────────────────────────
// 2. Intervalos HDI (forest plot)
// ────────────────────────────────────────────────────────────────────

export function buildIntervalosHdi(text, codMunicipio = null) {
  const allRows = parseCsv(text);
  let filtered = allRows.filter((r) => r.precios_mu != null);
  if (codMunicipio) {
    const key = String(codMunicipio);
    filtered = filtered.filter((r) => String(r.cod_municipio) === key);
  }
  // Orden + cap top 30 si no hay filtro
  filtered.sort((a, b) => a.precios_mu - b.precios_mu);
  if (!codMunicipio) filtered = filtered.slice(-30);

  const labels = filtered.map((r, i) => {
    const nm = nombreMunicipio(r.cod_municipio);
    return `${nm} #${i + 1}`;
  });
  const x = filtered.map((r) => r.precios_mu);
  const lower = filtered.map((r) => r.precios_mu - r.hdi_lower);
  const upper = filtered.map((r) => r.hdi_upper - r.precios_mu);

  const trace = {
    type: 'scatter',
    mode: 'markers',
    x,
    y: labels,
    error_x: {
      type: 'data',
      symmetric: false,
      array: upper,
      arrayminus: lower,
      color: 'rgba(163,230,53,0.45)',
      thickness: 1.4,
      width: 4,
    },
    marker: { color: '#a3e635', size: 8, line: { color: '#fff', width: 1 } },
    hovertemplate:
      '<b>%{y}</b><br>μ: $%{x:,.0f}<br>HDI: $%{customdata[0]:,.0f} – $%{customdata[1]:,.0f}<extra></extra>',
    customdata: filtered.map((r) => [r.hdi_lower, r.hdi_upper]),
  };

  const layout = {
    ...PLOTLY_THEME,
    height: 400,
    margin: { l: 180, r: 30, t: 20, b: 50 },
    xaxis: { title: 'Precio (COP) · barras = HDI 94%', gridcolor: 'rgba(255,255,255,0.05)' },
    yaxis: { automargin: true, type: 'category' },
  };

  return {
    data: [trace],
    layout,
    stats: { n_municipios: filtered.length },
  };
}

// ────────────────────────────────────────────────────────────────────
// 3. Perfiles & Alertas (donut + tabla)
// ────────────────────────────────────────────────────────────────────

/**
 * @param {string} text
 * @param {{perfil?: string|null, alerta?: string|null, codMunicipio?: string|null, marca?: string|null}} filters
 */
export function buildPerfilesAlertas(text, filters = {}) {
  const allRows = parseCsv(text);
  const rows = applyFilters(allRows, {
    perfiles: filters.perfil ?? null,
    alerta: filters.alerta ?? null,
    cod_municipio: filters.codMunicipio ?? null,
    marca: filters.marca ?? null,
  });

  // Donut: distribución de perfiles
  const perfilCount = new Map();
  for (const r of rows) {
    if (!r.perfiles) continue;
    perfilCount.set(r.perfiles, (perfilCount.get(r.perfiles) || 0) + 1);
  }
  const perfilLabels = [...perfilCount.keys()];
  const perfilValues = perfilLabels.map((l) => perfilCount.get(l));
  const perfilColors = perfilLabels.map(
    (l) => PERFIL_COLORS[l] || '#94a3b8'
  );

  const donut = {
    data: [
      {
        type: 'pie',
        hole: 0.55,
        labels: perfilLabels,
        values: perfilValues,
        marker: { colors: perfilColors, line: { color: '#020617', width: 2 } },
        textinfo: 'label+percent',
        textfont: { color: '#f1f5f9', size: 12 },
        hovertemplate: '<b>%{label}</b><br>%{value} casos (%{percent})<extra></extra>',
      },
    ],
    layout: {
      ...PLOTLY_THEME,
      height: 320,
      margin: { l: 10, r: 10, t: 10, b: 10 },
      showlegend: false,
    },
  };

  // Tabla: deduplicar por (cod_municipio, perfil, alerta) y enriquecer con nombre.
  // Orden por prioridad de alerta (🔴 > 🟡 > 🟢) → mostrar primero los rojos.
  const seen = new Set();
  const tabla = [];
  for (const r of rows) {
    if (!r.cod_municipio || !r.perfiles) continue;
    const key = `${r.cod_municipio}|${r.perfiles}|${r.alerta}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tabla.push({
      cod_municipio: r.cod_municipio,
      nombre_municipio: nombreMunicipio(r.cod_municipio),
      perfil: r.perfiles,
      alerta: r.alerta,
    });
  }
  const priority = { '🔴': 0, '🟡': 1, '🟢': 2 };
  tabla.sort((a, b) => {
    const pa = priority[alertEmoji(a.alerta)] ?? 9;
    const pb = priority[alertEmoji(b.alerta)] ?? 9;
    if (pa !== pb) return pa - pb;
    return a.nombre_municipio.localeCompare(b.nombre_municipio);
  });

  // Stats
  let rojas = 0;
  let amarillas = 0;
  let verdes = 0;
  for (const r of tabla) {
    const e = alertEmoji(r.alerta);
    if (e === '🔴') rojas++;
    else if (e === '🟡') amarillas++;
    else if (e === '🟢') verdes++;
  }

  return {
    donut,
    tabla,
    stats: {
      n_municipios: tabla.length,
      rojas,
      amarillas,
      verdes,
    },
  };
}

// ────────────────────────────────────────────────────────────────────
// 4. Mapa Colombia (scattermapbox sobre OSM)
// ────────────────────────────────────────────────────────────────────

/**
 * Recibe el texto del CSV perfiles_alertas y devuelve la shape esperada
 * por MapaColombiaChart: { data, layout, stats }.
 *
 * Cada cod_municipio único se grafica como un punto con el color de su
 * alerta más crítica (si hay varias filas por municipio, gana la peor).
 */
/**
 * @param {string} text
 * @param {{perfil?: string|null, alerta?: string|null, codMunicipio?: string|null, marca?: string|null}} filters
 */
export function buildMapa(text, filters = {}) {
  const allRows = parseCsv(text);
  const rows = applyFilters(allRows, {
    perfiles: filters.perfil ?? null,
    alerta: filters.alerta ?? null,
    cod_municipio: filters.codMunicipio ?? null,
    marca: filters.marca ?? null,
  });

  // Para cada municipio: nos quedamos con su alerta más severa.
  // priority: 🔴 (0) < 🟡 (1) < 🟢 (2) → menor número = peor.
  const priority = { '🔴': 0, '🟡': 1, '🟢': 2 };
  const worst = new Map(); // cod → { cod, alerta, perfiles, n_filas }
  for (const r of rows) {
    if (!r.cod_municipio) continue;
    const cur = worst.get(r.cod_municipio);
    const eNew = alertEmoji(r.alerta);
    if (!cur) {
      worst.set(r.cod_municipio, {
        cod: r.cod_municipio,
        alerta: r.alerta,
        perfiles: new Set([r.perfiles].filter(Boolean)),
        n_filas: 1,
      });
    } else {
      cur.n_filas += 1;
      if (r.perfiles) cur.perfiles.add(r.perfiles);
      const eOld = alertEmoji(cur.alerta);
      if ((priority[eNew] ?? 9) < (priority[eOld] ?? 9)) {
        cur.alerta = r.alerta;
      }
    }
  }

  // Generamos una traza por categoría de alerta (así Plotly hace leyenda).
  const byAlerta = new Map(); // emoji → { lat, lon, text, customdata, color }
  let n_sin_geocod = 0;

  for (const { cod, alerta, perfiles, n_filas } of worst.values()) {
    const muni = getMunicipio(cod);
    if (!muni) {
      n_sin_geocod++;
      continue;
    }
    const emoji = alertEmoji(alerta) || '⚪';
    const cur = byAlerta.get(emoji) || {
      alerta,
      lat: [],
      lon: [],
      text: [],
      customdata: [],
    };
    cur.lat.push(muni.lat);
    cur.lon.push(muni.lon);
    cur.text.push(`${muni.nombre} (${muni.departamento})`);
    cur.customdata.push([
      cod,
      [...perfiles].join(', ') || '—',
      n_filas,
      alerta || '—',
    ]);
    byAlerta.set(emoji, cur);
  }

  const traces = [...byAlerta.entries()].map(([emoji, group]) => ({
    type: 'scattermapbox',
    mode: 'markers',
    lat: group.lat,
    lon: group.lon,
    text: group.text,
    customdata: group.customdata,
    marker: {
      size: 14,
      color: ALERTA_COLORS[emoji] || '#94a3b8',
      opacity: 0.85,
    },
    name: `${emoji} ${group.alerta?.replace(/^[🔴🟡🟢]\s*/, '') || ''}`.trim(),
    hovertemplate:
      '<b>%{text}</b><br>cod %{customdata[0]}<br>perfiles: %{customdata[1]}<br>filas: %{customdata[2]}<br>alerta: %{customdata[3]}<extra></extra>',
  }));

  // Centro: promedio de los puntos (con fallback a Colombia centro)
  const allLats = traces.flatMap((t) => t.lat);
  const allLons = traces.flatMap((t) => t.lon);
  const center =
    allLats.length && allLons.length
      ? {
          lat: (Math.min(...allLats) + Math.max(...allLats)) / 2,
          lon: (Math.min(...allLons) + Math.max(...allLons)) / 2,
        }
      : { lat: 4.5709, lon: -74.2973 };

  const layout = {
    ...PLOTLY_THEME,
    height: 520,
    margin: { l: 0, r: 0, t: 0, b: 0 },
    mapbox: {
      style: 'open-street-map',
      center,
      zoom: 4.6,
    },
    legend: {
      x: 0.01,
      y: 0.99,
      bgcolor: 'rgba(15,23,42,0.75)',
      bordercolor: 'rgba(255,255,255,0.1)',
      borderwidth: 1,
      font: { color: '#e2e8f0', size: 11 },
    },
  };

  return {
    data: traces,
    layout,
    stats: {
      n_puntos: worst.size - n_sin_geocod,
      n_sin_geocod,
    },
  };
}
