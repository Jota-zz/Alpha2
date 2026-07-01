/**
 * Página Argos (label "Dashboard"): alimentada por 3 CSVs publicados en
 * Google Sheets. Modo MVP, sin OAuth ni API Key.
 *
 *   1. `useArgosCsvs()` baja los 3 CSVs en paralelo de sus URLs públicas.
 *   2. Pasamos el texto crudo a cada chart, que lo parsea con PapaParse
 *      (memoizado) y arma sus trazas Plotly.
 *   3. Si faltan variables de entorno, mostramos guía de configuración.
 *
 * Filtros (modelo híbrido):
 *   - Globales (esta página): producto, regional, municipio. Se pasan como
 *     defaults a cada chart card.
 *   - Locales por ds-card: cada card tiene selects compactos en su header
 *     que permiten sobre-escribir o agregar filtros propios (perfil, alerta,
 *     etc). Los filtros locales NO propagan hacia atrás al estado global.
 */

import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertCircle, RefreshCcw, Filter } from 'lucide-react';
import { areCsvUrlsConfigured } from '../config/drive';
import { useArgosCsvs } from '../hooks/useArgosCsvs';
import { Card, CardTitle } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { ErrorCard, Loading } from '../components/ui/EmptyState';
import { DriveConnect } from '../components/drive/DriveConnect';
import { PerfilesAlertasChart } from '../components/charts/PerfilesAlertasChart';
import { IntervalosHdiChart } from '../components/charts/IntervalosHdiChart';
import { PreciosRegionalesChart } from '../components/charts/PreciosRegionalesChart';
import { MapaColombiaChart } from '../components/charts/MapaColombiaChart';
import {
  buildPerfilesAlertas,
  uniqueValues,
  marcasOptions,
} from '../lib/csvParsers';

export function ArgosPage() {
  // ── Filtros globales ───────────────────────────────────────────────
  const [marca, setMarca] = useState('');
  const [producto, setProducto] = useState('');
  const [regional, setRegional] = useState('');
  const [codMunicipio, setCodMunicipio] = useState('');

  const qc = useQueryClient();
  const csvs = useArgosCsvs();

  // Si faltan URLs, mostramos guía y nada más.
  if (!areCsvUrlsConfigured()) {
    return (
      <div className="anim-fadein">
        <DriveConnect />
      </div>
    );
  }

  const refresh = () => qc.invalidateQueries({ queryKey: ['argos-csvs'] });

  // ── Loading inicial ──────────────────────────────────────────────
  if (csvs.isLoading) {
    return (
      <div className="anim-fadein space-y-4">
        <DriveConnect />
        <Loading label="Descargando CSVs publicados…" />
      </div>
    );
  }

  // ── Error de descarga/parsing ────────────────────────────────────
  if (csvs.isError) {
    return (
      <div className="anim-fadein space-y-4">
        <DriveConnect />
        <Card>
          <CardTitle icon={<AlertCircle size={18} />}>
            No se pudieron leer los CSVs
          </CardTitle>
          <ErrorCard error={csvs.error} />
          <hr className="ds-hr" />
          <Button
            variant="ghost"
            icon={<RefreshCcw size={16} />}
            onClick={refresh}
          >
            Reintentar
          </Button>
        </Card>
      </div>
    );
  }

  // ── Caso normal ──────────────────────────────────────────────────
  const precios = csvs.data.precios.text;
  const intervalos = csvs.data.intervalos.text;
  const perfiles = csvs.data.perfiles.text;

  // Opciones para selects globales derivadas de los CSVs.
  // useMemo no es estrictamente necesario aquí (los CSVs son texto estable
  // mientras no se refresque), pero evita reparseos en re-renders por filtros.
  return (
    <ArgosBody
      precios={precios}
      intervalos={intervalos}
      perfiles={perfiles}
      marca={marca}
      setMarca={setMarca}
      producto={producto}
      setProducto={setProducto}
      regional={regional}
      setRegional={setRegional}
      codMunicipio={codMunicipio}
      setCodMunicipio={setCodMunicipio}
      refresh={refresh}
      isFetching={csvs.isFetching}
    />
  );
}

/**
 * Cuerpo separado para poder usar hooks (useMemo) sólo cuando ya tenemos
 * los CSVs cargados.
 */
function ArgosBody({
  precios,
  intervalos,
  perfiles,
  marca,
  setMarca,
  producto,
  setProducto,
  regional,
  setRegional,
  codMunicipio,
  setCodMunicipio,
  refresh,
  isFetching,
}) {
  // Opciones de selects globales.
  const productos = useMemo(() => uniqueValues(precios, 'id_producto'), [precios]);
  const regionales = useMemo(() => uniqueValues(precios, 'regional'), [precios]);
  // Marcas: prioriza columna real del CSV, sino cae al catálogo estático.
  // Usamos `precios` como fuente canónica (también funciona con `perfiles`).
  const marcas = useMemo(() => marcasOptions(precios), [precios]);

  // Municipios derivan del CSV de perfiles (mismo origen que la tabla del
  // chart Perfiles & Alertas).
  const municipios = useMemo(() => {
    if (!perfiles) return [];
    try {
      const { tabla } = buildPerfilesAlertas(perfiles);
      return Array.from(
        new Map(
          tabla.map((row) => [
            row.cod_municipio,
            { cod: row.cod_municipio, nombre: row.nombre_municipio },
          ])
        ).values()
      ).sort((a, b) => a.nombre.localeCompare(b.nombre));
    } catch {
      return [];
    }
  }, [perfiles]);

  const resetGlobal = () => {
    setMarca('');
    setProducto('');
    setRegional('');
    setCodMunicipio('');
  };
  const anyGlobal = marca || producto || regional || codMunicipio;

  return (
    <div className="anim-fadein">
      <DriveConnect />

      {/* ── Filtros globales ───────────────────────────────────────── */}
      <Card>
        <CardTitle icon={<Filter size={18} />} subtitle="Aplican como valor inicial a cada card. Cada ds-card también tiene sus propios filtros independientes en el header.">
          Filtros globales
        </CardTitle>

        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="ds-filter-label">Marca</span>
            <select
              className="ds-select"
              value={marca}
              onChange={(e) => setMarca(e.target.value)}
              data-testid="global-filter-marca"
            >
              <option value="">— Todas —</option>
              {marcas.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="ds-filter-label">Producto</span>
            <select
              className="ds-select"
              value={producto}
              onChange={(e) => setProducto(e.target.value)}
              data-testid="global-filter-producto"
            >
              <option value="">— Todos —</option>
              {productos.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="ds-filter-label">Regional</span>
            <select
              className="ds-select"
              value={regional}
              onChange={(e) => setRegional(e.target.value)}
              data-testid="global-filter-regional"
            >
              <option value="">— Todas —</option>
              {regionales.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="ds-filter-label">Municipio</span>
            <select
              className="ds-select"
              value={codMunicipio}
              onChange={(e) => setCodMunicipio(e.target.value)}
              data-testid="global-filter-municipio"
            >
              <option value="">— Todos —</option>
              {municipios.map((m) => (
                <option key={m.cod} value={m.cod}>
                  {m.nombre} ({m.cod})
                </option>
              ))}
            </select>
          </div>

          <div className="ml-auto flex items-center gap-2">
            {anyGlobal && (
              <Button variant="ghost" onClick={resetGlobal}>
                Limpiar filtros
              </Button>
            )}
            {isFetching && (
              <span className="text-xs text-slate-400">Refrescando…</span>
            )}
            <Button
              variant="ghost"
              icon={
                <RefreshCcw
                  size={16}
                  className={isFetching ? 'animate-spin' : ''}
                />
              }
              onClick={refresh}
              disabled={isFetching}
            >
              Refrescar desde Sheets
            </Button>
          </div>
        </div>
      </Card>

      <div className="ds-grid2">
        <PreciosRegionalesChart
          csvText={precios}
          producto={producto}
          regional={regional}
          marca={marca}
        />
        <IntervalosHdiChart
          csvText={intervalos}
          codMunicipio={codMunicipio || null}
          municipiosOpts={municipios}
        />
      </div>

      <PerfilesAlertasChart
        csvText={perfiles}
        codMunicipio={codMunicipio}
        marca={marca}
        municipiosOpts={municipios}
      />

      <MapaColombiaChart
        csvText={perfiles}
        codMunicipio={codMunicipio}
        marca={marca}
        municipiosOpts={municipios}
      />
    </div>
  );
}
