/**
 * Wrapper sobre `react-plotly.js`.
 *
 * IMPORTANTE: usamos el patrón `createPlotlyComponent(Plotly)` con
 * `plotly.js-dist-min` para evitar cargar el bundle completo de Plotly
 * (~6MB). Con el dist-min minificado quedamos en ~2MB que ya está en
 * su propio chunk (manual chunks en vite.config.js).
 *
 * Props:
 *   - data    : Plotly traces (viene del backend tal cual).
 *   - layout  : Plotly layout (viene del backend tal cual).
 *   - height  : altura mínima del contenedor (px). Default: 360.
 *   - testId  : útil para tests / debugging.
 *
 * Comportamiento responsive:
 *   - `useResizeHandler` + `autosize: true` → el chart se redimensiona
 *     cuando cambia la ventana del navegador.
 *   - `style={{ width: '100%' }}` → fill horizontal del card padre.
 */

import { useMemo } from 'react';
import Plotly from 'plotly.js-dist-min';
import createPlotlyComponent from 'react-plotly.js/factory';

const Plot = createPlotlyComponent(Plotly);

// Config compartido: quitamos el logo de Plotly y los botones que no aportan.
const DEFAULT_CONFIG = {
  displaylogo: false,
  responsive: true,
  modeBarButtonsToRemove: [
    'lasso2d',
    'select2d',
    'toggleSpikelines',
    'autoScale2d',
    'hoverClosestCartesian',
    'hoverCompareCartesian',
  ],
  toImageButtonOptions: {
    format: 'png',
    filename: 'argos_chart',
    scale: 2,
  },
};

export function PlotChart({ data, layout, height = 360, testId }) {
  // Fusionar el layout del backend con `autosize:true` para responsive.
  // useMemo evita que Plotly re-renderice innecesariamente cada vez.
  const finalLayout = useMemo(
    () => ({
      ...layout,
      autosize: true,
      // El backend ya envía height, pero forzamos que sea al menos lo pedido
      // por el componente (algunos charts colapsan si el contenedor es 0px).
      height: layout?.height || height,
    }),
    [layout, height]
  );

  return (
    <div data-testid={testId} style={{ width: '100%' }}>
      <Plot
        data={data}
        layout={finalLayout}
        config={DEFAULT_CONFIG}
        useResizeHandler
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  );
}
