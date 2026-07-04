/**
 * Chart "Perfiles & Alertas":
 *   - Donut con distribución de perfiles (Argos Dominante, Competencia, Paridad).
 *   - Tabla con (cod_municipio, nombre, perfil, alerta) ordenada por
 *     prioridad de alerta (🔴 > 🟡 > 🟢).
 *
 * Datos: CSV `argos_perfiles_alertas.csv` descargado desde Drive.
 *
 * Props:
 *   - csvText: contenido crudo del CSV (string).
 *   - codMunicipio: filtro global (Argos.jsx). Valor inicial; el header
 *     incluye un select local para sobre-escribir.
 *   - municipiosOpts: lista [{ cod, nombre }] para el select de municipio.
 */

import { useMemo, useState, useEffect } from 'react';
import { PieChart } from 'lucide-react';
import { Card, CardTitle } from '../ui/Card';
import { EmptyState } from '../ui/EmptyState';
import { PlotChart } from './PlotChart';
import {
  buildPerfilesAlertas,
  uniqueValues,
  marcasOptions,
} from '../../lib/csvParsers';

function alertaVariant(alerta) {
  if (alerta?.includes('🔴')) return 'crit';
  if (alerta?.includes('🟡')) return 'warn';
  if (alerta?.includes('🟢')) return 'ok';
  return 'neutral';
}

function AlertaBadge({ alerta }) {
  const cls = {
    crit: 'ds-badge-crit',
    warn: 'ds-badge-warn',
    ok: 'ds-badge-ok',
    neutral: 'ds-badge-neutral',
  }[alertaVariant(alerta)];
  const text = alerta?.replace(/^[🔴🟡🟢]\s*/, '').trim() || alerta;
  return <span className={cls}>{text}</span>;
}

export function PerfilesAlertasChart({
  csvText,
  codMunicipio: codGlobal = '',
  marca: marcaGlobal = '',
  municipiosOpts = [],
}) {
  const [perfil, setPerfil] = useState('');
  const [alerta, setAlerta] = useState('');
  const [marca, setMarca] = useState(marcaGlobal || '');
  const [codMunicipio, setCodMunicipio] = useState(codGlobal || '');
  useEffect(() => setCodMunicipio(codGlobal || ''), [codGlobal]);
  useEffect(() => setMarca(marcaGlobal || ''), [marcaGlobal]);

  const perfiles = useMemo(() => uniqueValues(csvText, 'perfiles'), [csvText]);
  const alertas = useMemo(() => uniqueValues(csvText, 'alerta'), [csvText]);
  const marcas = useMemo(() => marcasOptions(csvText), [csvText]);

  const parsed = useMemo(() => {
    if (!csvText) return null;
    try {
      return buildPerfilesAlertas(csvText, {
        perfil: perfil || null,
        alerta: alerta || null,
        codMunicipio: codMunicipio || null,
        marca: marca || null,
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[perfiles] error parseando', e);
      return null;
    }
  }, [csvText, perfil, alerta, codMunicipio, marca]);

  const filtersUI = (
    <>
      <select
        className="ds-select-sm"
        value={marca}
        onChange={(e) => setMarca(e.target.value)}
        title="Filtrar por marca"
        data-testid="perfiles-filter-marca"
      >
        <option value="">Marca: todas</option>
        {marcas.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <select
        className="ds-select-sm"
        value={perfil}
        onChange={(e) => setPerfil(e.target.value)}
        title="Filtrar por perfil"
        data-testid="perfiles-filter-perfil"
      >
        <option value="">Perfil: todos</option>
        {perfiles.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <select
        className="ds-select-sm"
        value={alerta}
        onChange={(e) => setAlerta(e.target.value)}
        title="Filtrar por alerta"
        data-testid="perfiles-filter-alerta"
      >
        <option value="">Alerta: todas</option>
        {alertas.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>
      {municipiosOpts.length > 0 && (
        <select
          className="ds-select-sm"
          value={codMunicipio}
          onChange={(e) => setCodMunicipio(e.target.value)}
          title="Filtrar por municipio"
          data-testid="perfiles-filter-municipio"
        >
          <option value="">Municipio: todos</option>
          {municipiosOpts.map((m) => (
            <option key={m.cod} value={m.cod}>
              {m.nombre} ({m.cod})
            </option>
          ))}
        </select>
      )}
    </>
  );

  if (!parsed) {
    return (
      <Card>
        <CardTitle icon={<PieChart size={18} />} actions={filtersUI}>
          Perfiles & alertas por municipio
        </CardTitle>
        <EmptyState icon="📂" title="Sin datos">
          No se pudo parsear el CSV de perfiles y alertas.
        </EmptyState>
      </Card>
    );
  }

  const { donut, tabla, stats } = parsed;

  return (
    <Card>
      <CardTitle
        icon={<PieChart size={18} />}
        subtitle={`${stats.n_municipios} combinaciones · ${stats.rojas}🔴 · ${stats.amarillas}🟡 · ${stats.verdes}🟢`}
        actions={filtersUI}
      >
        Perfiles & alertas por municipio
      </CardTitle>

      <div className="ds-grid2" style={{ marginBottom: 0 }}>
        {/* Donut */}
        <div>
          <PlotChart
            data={donut.data}
            layout={donut.layout}
            height={320}
            testId="argos-donut"
          />
        </div>

        {/* Tabla */}
        <div
          className="scrollbar-thin"
          style={{ maxHeight: 360, overflowY: 'auto' }}
        >
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-400 uppercase tracking-wider">
              <tr className="border-b border-white/10">
                <th className="text-left py-2 pr-2 font-semibold">Municipio</th>
                <th className="text-left py-2 pr-2 font-semibold">Perfil</th>
                <th className="text-right py-2 font-semibold">Alerta</th>
              </tr>
            </thead>
            <tbody>
              {tabla.map((row, i) => (
                <tr
                  key={`${row.cod_municipio}-${row.perfil}-${i}`}
                  className="border-b border-white/5 hover:bg-white/[0.02]"
                >
                  <td className="py-2 pr-2 text-slate-200">
                    <div>{row.nombre_municipio}</div>
                    <div className="text-xs text-slate-500 font-mono">
                      {row.cod_municipio}
                    </div>
                  </td>
                  <td className="py-2 pr-2 text-slate-300">{row.perfil}</td>
                  <td className="py-2 text-right">
                    <AlertaBadge alerta={row.alerta} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
