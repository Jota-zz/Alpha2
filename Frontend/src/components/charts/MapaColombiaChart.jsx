/**
 * Mapa de cobertura: scatter de municipios sobre teselas de OpenStreetMap
 * (equivalente en frontend al combo matplotlib + seaborn + contextily).
 *
 * Datos: CSV `argos_perfiles_alertas.csv` (mismo que la tabla de Perfiles &
 * Alertas). Cada cod_municipio único se grafica con el color de su alerta
 * más crítica. Las coordenadas se resuelven con el lookup DANE local
 * (src/config/municipios.js).
 *
 * Props:
 *   - csvText: contenido crudo del CSV.
 *   - codMunicipio: filtro global (Argos.jsx). Inicializa el select local.
 *   - municipiosOpts: lista [{ cod, nombre }] para el select.
 */

import { useMemo, useState, useEffect } from 'react';
import { Map } from 'lucide-react';
import { Card, CardTitle } from '../ui/Card';
import { EmptyState } from '../ui/EmptyState';
import { PlotChart } from './PlotChart';
import { buildMapa, uniqueValues, marcasOptions } from '../../lib/csvParsers';

export function MapaColombiaChart({
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
      return buildMapa(csvText, {
        perfil: perfil || null,
        alerta: alerta || null,
        codMunicipio: codMunicipio || null,
        marca: marca || null,
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[mapa] error parseando', e);
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
        data-testid="mapa-filter-marca"
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
        data-testid="mapa-filter-perfil"
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
        data-testid="mapa-filter-alerta"
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
          data-testid="mapa-filter-municipio"
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
        <CardTitle icon={<Map size={18} />} actions={filtersUI}>
          Mapa de cobertura
        </CardTitle>
        <EmptyState icon="📂" title="Sin datos">
          No se pudo parsear el CSV de perfiles para generar el mapa.
        </EmptyState>
      </Card>
    );
  }

  const sub = parsed.stats.n_sin_geocod
    ? `${parsed.stats.n_puntos} municipios · ⚠ ${parsed.stats.n_sin_geocod} sin coordenadas DANE`
    : `${parsed.stats.n_puntos} municipios geolocalizados sobre OpenStreetMap`;

  return (
    <Card>
      <CardTitle icon={<Map size={18} />} subtitle={sub} actions={filtersUI}>
        Mapa de cobertura
      </CardTitle>
      <PlotChart
        data={parsed.data}
        layout={parsed.layout}
        height={520}
        testId="argos-mapa"
      />
      <div className="text-[0.65rem] text-slate-500 mt-2 text-right">
        © OpenStreetMap contributors
      </div>
    </Card>
  );
}
