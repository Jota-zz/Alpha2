/**
 * Chart "Intervalos HDI": forest plot con precios_mu y barras de error
 * asimétricas (hdi_lower, hdi_upper) por municipio. Datos: CSV
 * `argos_intervalos_hdi.csv` descargado desde Drive.
 *
 * Props:
 *   - csvText: contenido crudo del CSV (string).
 *   - codMunicipio: filtro global heredado de Argos.jsx (valor inicial). El
 *     usuario puede sobre-escribirlo localmente desde el select del header.
 *   - municipiosOpts: lista [{ cod, nombre }] derivada en Argos.jsx para que
 *     el select muestre nombres legibles.
 */

import { useMemo, useState, useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { Card, CardTitle } from '../ui/Card';
import { EmptyState } from '../ui/EmptyState';
import { PlotChart } from './PlotChart';
import { buildIntervalosHdi } from '../../lib/csvParsers';

export function IntervalosHdiChart({
  csvText,
  codMunicipio: codGlobal = null,
  municipiosOpts = [],
}) {
  const [codLocal, setCodLocal] = useState(codGlobal || '');
  useEffect(() => setCodLocal(codGlobal || ''), [codGlobal]);

  const codMunicipio = codLocal || null;

  const parsed = useMemo(() => {
    if (!csvText) return null;
    try {
      return buildIntervalosHdi(csvText, codMunicipio);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[hdi] error parseando', e);
      return null;
    }
  }, [csvText, codMunicipio]);

  const filtersUI = (
    <select
      className="ds-select-sm"
      value={codLocal}
      onChange={(e) => setCodLocal(e.target.value)}
      title="Filtrar por municipio"
      data-testid="hdi-filter-municipio"
    >
      <option value="">Municipio: top 30</option>
      {municipiosOpts.map((m) => (
        <option key={m.cod} value={m.cod}>
          {m.nombre} ({m.cod})
        </option>
      ))}
    </select>
  );

  const titleSuffix = codMunicipio
    ? `municipio ${codMunicipio} · ${parsed?.stats?.n_municipios ?? 0} registros`
    : `top ${parsed?.stats?.n_municipios ?? 0} por precio medio`;

  if (!parsed) {
    return (
      <Card>
        <CardTitle icon={<Sparkles size={18} />} actions={filtersUI}>
          Intervalos HDI 94%
        </CardTitle>
        <EmptyState icon="📂" title="Sin datos">
          No se pudo parsear el CSV de intervalos HDI.
        </EmptyState>
      </Card>
    );
  }

  return (
    <Card>
      <CardTitle
        icon={<Sparkles size={18} />}
        subtitle={titleSuffix}
        actions={filtersUI}
      >
        Intervalos HDI 94%
      </CardTitle>
      <PlotChart
        data={parsed.data}
        layout={parsed.layout}
        height={400}
        testId="argos-hdi"
      />
    </Card>
  );
}
