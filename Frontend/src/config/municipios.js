/**
 * Lookup DANE → (nombre, departamento, lat, lon) para los municipios que
 * aparecen en los CSVs Argos. Lo usamos para enriquecer las tablas y para
 * geolocalizar los puntos en el mapa de cobertura sin pegarle a una API
 * externa de geocoding cada vez.
 *
 * Si aparece un cod_municipio nuevo en los CSVs, agregalo acá con su lat/lon.
 * Coordenadas tomadas del centroide del casco urbano (referencias IGAC/DANE).
 *
 * Códigos DANE (5 dígitos): 2 primeros = departamento, 3 últimos = municipio
 * dentro del departamento. Ej. 5001 = (05) Antioquia + (001) Medellín.
 */

export const MUNICIPIOS = {
  5001: { nombre: 'Medellín', departamento: 'Antioquia', lat: 6.2486, lon: -75.5742 },
  11001: { nombre: 'Bogotá D.C.', departamento: 'Bogotá D.C.', lat: 4.7110, lon: -74.0721 },
  68001: { nombre: 'Bucaramanga', departamento: 'Santander', lat: 7.1193, lon: -73.1227 },
  76001: { nombre: 'Cali', departamento: 'Valle del Cauca', lat: 3.4516, lon: -76.5320 },
  8001: { nombre: 'Barranquilla', departamento: 'Atlántico', lat: 10.9685, lon: -74.7813 },
  13001: { nombre: 'Cartagena', departamento: 'Bolívar', lat: 10.3910, lon: -75.4794 },
  17001: { nombre: 'Manizales', departamento: 'Caldas', lat: 5.0689, lon: -75.5174 },
  41001: { nombre: 'Neiva', departamento: 'Huila', lat: 2.9273, lon: -75.2819 },
  50001: { nombre: 'Villavicencio', departamento: 'Meta', lat: 4.1420, lon: -73.6266 },
  52001: { nombre: 'Pasto', departamento: 'Nariño', lat: 1.2136, lon: -77.2811 },
  54001: { nombre: 'Cúcuta', departamento: 'Norte de Santander', lat: 7.8939, lon: -72.5078 },
  63001: { nombre: 'Armenia', departamento: 'Quindío', lat: 4.5339, lon: -75.6811 },
  66001: { nombre: 'Pereira', departamento: 'Risaralda', lat: 4.8133, lon: -75.6961 },
  73001: { nombre: 'Ibagué', departamento: 'Tolima', lat: 4.4389, lon: -75.2322 },
  19001: { nombre: 'Popayán', departamento: 'Cauca', lat: 2.4448, lon: -76.6147 },
  20001: { nombre: 'Valledupar', departamento: 'Cesar', lat: 10.4631, lon: -73.2532 },
  23001: { nombre: 'Montería', departamento: 'Córdoba', lat: 8.7479, lon: -75.8814 },
  44001: { nombre: 'Riohacha', departamento: 'La Guajira', lat: 11.5444, lon: -72.9072 },
  47001: { nombre: 'Santa Marta', departamento: 'Magdalena', lat: 11.2408, lon: -74.1990 },
  70001: { nombre: 'Sincelejo', departamento: 'Sucre', lat: 9.3047, lon: -75.3978 },
  15001: { nombre: 'Tunja', departamento: 'Boyacá', lat: 5.5446, lon: -73.3578 },
  18001: { nombre: 'Florencia', departamento: 'Caquetá', lat: 1.6149, lon: -75.6062 },
  27001: { nombre: 'Quibdó', departamento: 'Chocó', lat: 5.6919, lon: -76.6583 },
};

/** Devuelve metadata del municipio o un fallback con el código tal cual. */
export function getMunicipio(cod) {
  if (cod == null) return null;
  // Algunos CSVs llegan con int (5001) y otros con string ("5001"); normalizo
  const key = Number(cod);
  return MUNICIPIOS[key] || null;
}

/** Nombre legible (con fallback al código si no está en el lookup). */
export function nombreMunicipio(cod) {
  const m = getMunicipio(cod);
  return m ? m.nombre : `Municipio ${cod}`;
}
