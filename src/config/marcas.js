/**
 * Catálogo de marcas competidoras de cemento en Colombia (y vecinos).
 *
 * Se usa como fallback en los selects de filtro de marca cuando el CSV
 * todavía no expone una columna `marca` con sus propios valores distintos.
 *
 * Si el CSV `argos_precios_regionales.csv` (o `argos_perfiles_alertas.csv`)
 * gana una columna `marca` con valores distintos, los selects la priorizan
 * sobre esta lista (ver `uniqueValues` + `marcasOptions` en csvParsers.js).
 */

export const MARCAS = [
  'alion',
  'argos',
  'cemento pais',
  'cemento patriota',
  'cementos del oriente',
  'cementos progreso',
  'cementos tequendama',
  'cemex',
  'cemnal',
  'ecocem',
  'fortecem',
  'holcim',
  'kolcem',
  'san marcos',
  'topex',
  'ultracem',
];
