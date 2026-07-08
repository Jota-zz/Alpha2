/**
 * Chart "Precios por regional": bar horizontal con el precio medio (mu_h)
 * por regional. Datos: CSV `argos_precios_regionales.csv` descargado desde
 * Drive y parseado en cliente con PapaParse.
 *
 * Props:
 *   - csvText: contenido crudo del CSV (string). Si está vacío, mostramos
 *     un placeholder "Sin datos".
 *   - producto, regional: filtros globales heredados desde Argos.jsx que
 *     actúan como valor inicial; el usuario puede sobre-escribirlos con los
 *     selects locales del header de la card.
 */

import { useMemo, useState, useEffect } from 'react';
import { TrendingUp } from 'lucide-react';
import { Card, CardTitle } from '../ui/Card';
import { EmptyState } from '../ui/EmptyState';
import { PlotChart } from './PlotChart';
import {
  buildPreciosRegionales,
  uniqueValues,
  marcasOptions,
} from '../../lib/csvParsers';

export function PreciosRegionalesChart({
  csvText,
  producto: productoGlobal = '',
  regional: regionalGlobal = '',
  marca: marcaGlobal = '',
}) {
  const [producto, setProducto] = useState(productoGlobal || '');
  const [regional, setRegional] = useState(regionalGlobal || '');
  const [marca, setMarca] = useState(marcaGlobal || '');

  // Si el global cambia y el local no fue tocado manualmente,
  // sincronizamos para reflejar el filtro global.
  useEffect(() => setProducto(productoGlobal || ''), [productoGlobal]);
  useEffect(() => setRegional(regionalGlobal || ''), [regionalGlobal]);
  useEffect(() => setMarca(marcaGlobal || ''), [marcaGlobal]);

  const productos = useMemo(() => uniqueValues(csvText, 'id_producto'), [csvText]);
  const regionales = useMemo(() => uniqueValues(csvText, 'regional'), [csvText]);
  const marcas = useMemo(() => marcasOptions(csvText), [csvText]);

  const parsed = useMemo(() => {
    if (!csvText) return null;
    try {
      return buildPreciosRegionales(csvText, {
        producto: producto || null,
        regional: regional || null,
        marca: marca || null,
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[precios] error parseando', e);
      return null;
    }
  }, [csvText, producto, regional, marca]);

  const filtersUI = (
    <>
      <select
        className="ds-select-sm"
        value={marca}
        onChange={(e) => setMarca(e.target.value)}
        title="Filtrar por marca"
        data-testid="precios-filter-marca"
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
        value={producto}
        onChange={(e) => setProducto(e.target.value)}
        title="Filtrar por producto"
        data-testid="precios-filter-producto"
      >
        <option value="">Producto: todos</option>
        {productos.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <select
        className="ds-select-sm"
        value={regional}
        onChange={(e) => setRegional(e.target.value)}
        title="Filtrar por regional"
        data-testid="precios-filter-regional"
      >
        <option value="">Regional: todas</option>
        {regionales.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
    </>
  );

  if (!parsed) {
    return (
      <Card>
        <CardTitle icon={<TrendingUp size={18} />} actions={filtersUI}>
          Precio medio por regional
        </CardTitle>
        <EmptyState icon="📂" title="Sin datos">
          No se pudo parsear el CSV de precios regionales.
        </EmptyState>
      </Card>
    );
  }

  const { stats } = parsed;
  const subtitle = stats.precio_promedio
    ? `${stats.n_regionales} regionales · promedio $${Math.round(
        stats.precio_promedio
      ).toLocaleString('es-CO')} · ${stats.n_filas} filas`
    : `${stats.n_regionales} regionales`;

  return (
    <Card>
      <CardTitle
        icon={<TrendingUp size={18} />}
        subtitle={subtitle}
        actions={filtersUI}
      >
        Precio medio por regional
      </CardTitle>
      <PlotChart
        data={parsed.data}
        layout={parsed.layout}
        height={260}
        testId="argos-precios-regionales"
      />
    </Card>
  );
}
