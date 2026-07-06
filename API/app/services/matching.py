"""Identificación de productos por similitud de strings (migrado celdas 6.5.x).

Pipeline: normalización de nombres -> métricas de similitud combinadas ->
búsqueda escalonada en catálogo -> integración con `DatabaseManager`.

Dependencias:
- rapidfuzz: requerida (métricas base, rápida).
- fuzzywuzzy: opcional; si falta, se usa `rapidfuzz.fuzz` (API compatible).
- scikit-learn: opcional; habilita la métrica coseno TF-IDF (si falta, devuelve 0).
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz as rf_fuzz

try:  # fuzzywuzzy es opcional; rapidfuzz expone la misma API.
    from fuzzywuzzy import fuzz
except ImportError:  # pragma: no cover
    from rapidfuzz import fuzz

try:  # scikit-learn es opcional (solo para la métrica coseno TF-IDF).
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    _HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    _HAS_SKLEARN = False

from app.core.logging import get_logger
from app.models import MarcaProducto, Producto
from app.schemas import EstadoExtraccionAcumulado, PrecioDetectado, ResultadoBusqueda

logger = get_logger(__name__)


class NormalizadorNombres:
    """Normaliza nombres de productos eliminando variaciones comunes.

    Útil para comparar "Cemento Argos", "cemento argos", "cémento argos", etc.
    """

    # Sinónimos y abreviaciones comunes en ferretería.
    SINONIMOS = {
        "kg": "kilogramo",
        "bulto": "bolsa",
        "saco": "bolsa",
        "presentación": "",
        "pres": "",
        "gris": "",
        "blanco": "",
        "portland": "",
        "estructural": "",
        "tipo i": "",
        "tipo ii": "",
        "tipo iii": "",
    }

    # Stopwords específicos de ferretería a eliminar.
    STOPWORDS_FERRETERIA = {
        "de", "la", "el", "y", "un", "una", "unos", "unas",
        "en", "por", "para", "con", "sin", "a", "o", "del",
        "bolsa", "saco", "paquete", "caja",  # contenedores
    }

    @staticmethod
    def remover_tildes(texto: str) -> str:
        """Convierte acentos: "cémento" -> "cemento"."""
        if not texto:
            return ""
        nfd = unicodedata.normalize("NFD", texto)
        return "".join(c for c in nfd if unicodedata.category(c) != "Mn")

    @staticmethod
    def limpiar_espacios_puntuacion(texto: str) -> str:
        """Elimina puntuación y normaliza espacios."""
        if not texto:
            return ""
        texto = re.sub(r"[.,;:!?\"'`()\[\]{}]", " ", texto)
        texto = re.sub(r"\s+", " ", texto).strip()
        return texto

    @classmethod
    def normalizar(cls, nombre: str, eliminar_stopwords: bool = True) -> str:
        """Pipeline completo de normalización.

        Ejemplo: "CEMENTO Argos - Bolsa 50 kg" -> "cemento argos".
        """
        if not nombre or not isinstance(nombre, str):
            return ""

        texto = nombre.lower()
        texto = cls.remover_tildes(texto)
        texto = cls.limpiar_espacios_puntuacion(texto)

        # Expandir sinónimos (antes de eliminar stopwords).
        for sino, reemplazo in cls.SINONIMOS.items():
            texto = re.sub(rf"\b{re.escape(sino)}\b", reemplazo, texto)

        if eliminar_stopwords:
            palabras = texto.split()
            palabras = [p for p in palabras if p not in cls.STOPWORDS_FERRETERIA]
            texto = " ".join(palabras)

        texto = re.sub(r"\s+", " ", texto).strip()
        return texto if texto else ""

    @classmethod
    def normalizar_preservar_orden(cls, nombre: str) -> str:
        """Versión que NO elimina stopwords (preserva orden de palabras)."""
        return cls.normalizar(nombre, eliminar_stopwords=False)


class MetricasSimilitud:
    """Colección de métricas para comparar strings (score 0.0–1.0)."""

    @staticmethod
    def levenshtein_ratio(s1: str, s2: str) -> float:
        """Distancia de Levenshtein normalizada (0–1)."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        try:
            return rf_fuzz.ratio(s1, s2) / 100.0
        except Exception:
            return SequenceMatcher(None, s1, s2).ratio()

    @staticmethod
    def jaro_winkler(s1: str, s2: str) -> float:
        """Jaro-Winkler: favorece prefijos iguales (typos al inicio)."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        try:
            return rf_fuzz.jaro_winkler(s1, s2) / 100.0
        except Exception:
            return 0.0

    @staticmethod
    def token_sort_ratio(s1: str, s2: str) -> float:
        """Ordena tokens y compara (orden de palabras distinto)."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        try:
            return fuzz.token_sort_ratio(s1, s2) / 100.0
        except Exception:
            return 0.0

    @staticmethod
    def token_set_ratio(s1: str, s2: str) -> float:
        """Compara tokens únicos (ordena + elimina duplicados)."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        try:
            return fuzz.token_set_ratio(s1, s2) / 100.0
        except Exception:
            return 0.0

    @staticmethod
    def cosine_tfidf(s1: str, s2: str) -> float:
        """Similitud de coseno usando TF-IDF (requiere scikit-learn)."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        if not _HAS_SKLEARN:
            return 0.0
        try:
            vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 3))
            matriz = vectorizer.fit_transform([s1, s2])
            sim = cosine_similarity(matriz)[0, 1]
            return float(sim)
        except Exception:
            return 0.0

    @staticmethod
    def partial_ratio(s1: str, s2: str) -> float:
        """Encuentra la mejor coincidencia de substring."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        try:
            return fuzz.partial_ratio(s1, s2) / 100.0
        except Exception:
            return 0.0

    @classmethod
    def score_ponderado(
        cls, s1: str, s2: str, pesos: Optional[Dict[str, float]] = None
    ) -> float:
        """Combina múltiples métricas con pesos personalizables (0.0–1.0)."""
        if pesos is None:
            pesos = {
                "levenshtein": 0.15,
                "jaro_winkler": 0.25,
                "token_sort": 0.30,
                "token_set": 0.15,
                "cosine": 0.10,
                "partial": 0.05,
            }

        suma_pesos = sum(pesos.values())
        if suma_pesos <= 0:
            return 0.0
        pesos = {k: v / suma_pesos for k, v in pesos.items()}

        scores = {
            "levenshtein": cls.levenshtein_ratio(s1, s2),
            "jaro_winkler": cls.jaro_winkler(s1, s2),
            "token_sort": cls.token_sort_ratio(s1, s2),
            "token_set": cls.token_set_ratio(s1, s2),
            "cosine": cls.cosine_tfidf(s1, s2),
            "partial": cls.partial_ratio(s1, s2),
        }

        return sum(scores.get(k, 0.0) * v for k, v in pesos.items())


class BuscadorProductos:
    """Busca productos en un catálogo usando similitud de nombres.

    Flujo escalonado (mayor a menor confianza): exacto normalizado ->
    contención -> difuso ponderado.
    """

    def __init__(self, catalogo: Dict[uuid.UUID, str]):
        self.catalogo = catalogo
        self.catalogo_normalizado = {
            id_: NormalizadorNombres.normalizar(nombre)
            for id_, nombre in catalogo.items()
        }
        logger.info("🔍 Catálogo cargado: %s productos", len(catalogo))

    def actualizar_catalogo(self, catalogo: Dict[uuid.UUID, str]):
        """Actualiza el catálogo interno (p.ej., tras updates en BD)."""
        self.catalogo = catalogo
        self.catalogo_normalizado = {
            id_: NormalizadorNombres.normalizar(nombre)
            for id_, nombre in catalogo.items()
        }
        logger.info("🔄 Catálogo actualizado: %s productos", len(catalogo))

    def buscar(
        self,
        nombre_buscado: str,
        threshold_minimo: float = 0.60,
        limitar_alternativas: int = 3,
    ) -> ResultadoBusqueda:
        """Busca un producto en el catálogo con búsqueda escalonada."""
        if not nombre_buscado or not isinstance(nombre_buscado, str):
            return ResultadoBusqueda(
                id_producto=None,
                nombre_producto="",
                nombre_buscado=nombre_buscado,
                score_similitud=0.0,
                metodo="error_input",
                alternativas=[],
                confianza="NO_ENCONTRADO",
            )

        nombre_norm = NormalizadorNombres.normalizar(nombre_buscado)

        # PASO 1: Búsqueda exacta normalizada.
        for id_, nombre_catalogo_norm in self.catalogo_normalizado.items():
            if nombre_catalogo_norm == nombre_norm:
                return ResultadoBusqueda(
                    id_producto=id_,
                    nombre_producto=self.catalogo[id_],
                    nombre_buscado=nombre_buscado,
                    score_similitud=1.0,
                    metodo="exacto_normalizado",
                    alternativas=[],
                    confianza="ALTA",
                )

        # PASO 2: Búsqueda por contención.
        if nombre_norm:
            for id_, nombre_catalogo_norm in self.catalogo_normalizado.items():
                if nombre_norm in nombre_catalogo_norm.split():
                    return ResultadoBusqueda(
                        id_producto=id_,
                        nombre_producto=self.catalogo[id_],
                        nombre_buscado=nombre_buscado,
                        score_similitud=0.95,
                        metodo="contenido",
                        alternativas=[],
                        confianza="ALTA",
                    )

        # PASO 3: Búsqueda difusa ponderada.
        candidatos: List[Tuple[uuid.UUID, str, float]] = []
        for id_, nombre_catalogo in self.catalogo.items():
            nombre_catalogo_norm = self.catalogo_normalizado[id_]
            score = MetricasSimilitud.score_ponderado(nombre_norm, nombre_catalogo_norm)
            if score >= threshold_minimo:
                candidatos.append((id_, nombre_catalogo, score))

        if not candidatos:
            return ResultadoBusqueda(
                id_producto=None,
                nombre_producto="",
                nombre_buscado=nombre_buscado,
                score_similitud=0.0,
                metodo="difuso_sin_resultado",
                alternativas=[],
                confianza="NO_ENCONTRADO",
            )

        candidatos.sort(key=lambda x: x[2], reverse=True)
        id_ganador, nombre_ganador, score_ganador = candidatos[0]
        alternativas = [
            (nombre, score, id_)
            for id_, nombre, score in candidatos[1 : limitar_alternativas + 1]
        ]

        if score_ganador >= 0.90:
            confianza = "ALTA"
        elif score_ganador >= 0.75:
            confianza = "MEDIA"
        else:
            confianza = "BAJA"

        return ResultadoBusqueda(
            id_producto=id_ganador,
            nombre_producto=nombre_ganador,
            nombre_buscado=nombre_buscado,
            score_similitud=score_ganador,
            metodo="difuso_ponderado",
            alternativas=alternativas,
            confianza=confianza,
        )

    def buscar_multiples(self, nombres: List[str]) -> List[ResultadoBusqueda]:
        """Busca múltiples nombres de productos en batch."""
        return [self.buscar(nombre) for nombre in nombres]


class GestorBusquedaProductos:
    """Integra `BuscadorProductos` con `DatabaseManager`.

    Carga el catálogo desde BD, busca por similitud y persiste cotizaciones
    con el producto correcto identificado.
    """

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.buscador: Optional[BuscadorProductos] = None
        self._cargar_catalogo()

    def _cargar_catalogo(self):
        """Carga el catálogo de productos desde BD."""
        try:
            with self.db_manager.get_session() as session:
                productos = session.query(Producto).all()
                catalogo = {p.id_producto: p.nombre for p in productos}
                self.buscador = BuscadorProductos(catalogo)
                logger.info("📚 Catálogo BD cargado: %s productos", len(catalogo))
        except Exception as e:
            logger.error("Error cargando catálogo: %s", e)
            self.buscador = BuscadorProductos({})  # catálogo vacío

    def recargar_catalogo(self):
        """Recarga el catálogo (después de cambios en BD)."""
        self._cargar_catalogo()

    def buscar_producto(self, nombre: str, threshold: float = 0.80) -> Optional[uuid.UUID]:
        """Busca un producto y devuelve su UUID si alcanza el threshold."""
        if not self.buscador:
            logger.warning("Buscador no inicializado")
            return None

        resultado = self.buscador.buscar(nombre)
        if resultado.es_valido(threshold):
            logger.info(
                "✅ Producto encontrado: %r → %r (%.2f%%)",
                resultado.nombre_buscado,
                resultado.nombre_producto,
                resultado.score_similitud * 100,
            )
            return resultado.id_producto
        logger.warning(
            "⚠️  Score insuficiente para %r: %.2f%% < %s",
            resultado.nombre_buscado,
            resultado.score_similitud * 100,
            threshold,
        )
        return None

    def obtener_producto_con_alternativas(self, nombre: str) -> ResultadoBusqueda:
        """Devuelve el resultado completo incluyendo alternativas."""
        if not self.buscador:
            logger.warning("Buscador no inicializado")
            return ResultadoBusqueda(
                id_producto=None,
                nombre_producto="",
                nombre_buscado=nombre,
                score_similitud=0.0,
                metodo="error",
                alternativas=[],
                confianza="NO_ENCONTRADO",
            )
        return self.buscador.buscar(nombre)

    def registrar_cotizacion_con_busqueda(
        self,
        id_interaccion: str,
        id_ferreteria: str,
        producto_nombre: str,
        marca_nombre: str,
        precio: float,
        regional: str,
        disponibilidad: Optional[str] = None,
        confianza: Optional[float] = None,
        info_solicitada: Optional[str] = None,
        cod_municipio: Optional[str] = None,
        threshold_busqueda: float = 0.75,
    ) -> Optional[Dict]:
        """Busca el producto por similitud y persiste la cotización con su ID."""
        id_producto = self.buscar_producto(producto_nombre, threshold=threshold_busqueda)
        if id_producto is None:
            logger.warning(
                "❌ No se encontró producto %r (threshold=%s). Cotización rechazada.",
                producto_nombre,
                threshold_busqueda,
            )
            return None

        return self.db_manager.registrar_cotizacion(
            id_interaccion=id_interaccion,
            id_ferreteria=id_ferreteria,
            producto_nombre=producto_nombre,
            marca_nombre=marca_nombre,
            precio=precio,
            regional=regional,
            disponibilidad=disponibilidad,
            confianza=confianza,
            info_solicitada=info_solicitada,
            cod_municipio=cod_municipio,
            id_producto=id_producto,
        )


# ===========================================================================
# Extractor acumulativo de cotizaciones por texto (celdas 6.6.x)
# ===========================================================================


class DetectorPrecios:
    """
    Detector determinista de precios en mensajes de WhatsApp en español
    colombiano. Probado contra casos reales del CSV de historial:
      $32.000, 32.000, 32 mil, 32mil, 32k, "a 32", "=32 mil", 250.000,
      3200000 (precio total), 23 mil, 32 lucas, etc.

    NO decide si el precio es válido — solo detecta candidatos.
    El sanity check de rango lo hace el llamador con `precio_unitario_plausible()`.
    """

    # Rango plausible para precio UNITARIO de un bulto de cemento en COP.
    # Fuera de este rango → marcar como sospechoso (probablemente sea total).
    PRECIO_UNITARIO_MIN = 15_000
    PRECIO_UNITARIO_MAX = 60_000

    SUFIJOS_MIL = {"mil", "k", "lucas", "luca"}

    def __init__(self):
        # Patrón: precio EXPLÍCITO con $ o con separadores ($32.000, 3200000)
        self._re_precio_completo = re.compile(
            r'\$?\s*'
            r'(\d{1,3}(?:[.,]\d{3})+|\d{4,})'
            r'(?:\s*pesos?)?',
            re.IGNORECASE
        )
        # Patrón: con sufijo coloquial (32 mil, 32k, 32 lucas)
        sufijos_pat = '|'.join(self.SUFIJOS_MIL)
        self._re_precio_sufijo = re.compile(
            r'(\d{1,3}(?:[.,]\d{1,3})?)'
            rf'\s*({sufijos_pat})\b',
            re.IGNORECASE
        )
        # Patrón: implícito ("a 32", "=32", "queda en 35")
        self._re_precio_implicito = re.compile(
            r'\b(?:a|en|por|=|queda(?:n)?\s+en)\s+'
            r'(\d{1,3})\b'
            r'(?!\s*(?:mil|k|lucas?|\.\d|,\d|\d))',
            re.IGNORECASE
        )
        # Contextos: kg y unidad
        self._re_kg = re.compile(
            r'(\d{1,3})\s*(?:kg|kilos?|kilogramos?)\b',
            re.IGNORECASE
        )
        self._re_unidad = re.compile(
            r'\b(bulto|saco|tonelada|ton|m3|metro\s+cubico)s?\b',
            re.IGNORECASE
        )

    @staticmethod
    def _normalizar_numero(texto: str) -> Optional[float]:
        """
        '32.000'    → 32000.0   (separador de miles, convención CO)
        '1,200,000' → 1200000.0
        '3200000'   → 3200000.0
        '32.5' / '32,5' → 32.5  (decimal, pero raro en precios)
        """
        t = texto.strip().replace('$', '').replace(' ', '')
        if not t:
            return None
        if re.fullmatch(r'\d{1,3}[.,]\d{1,2}', t):
            return float(t.replace(',', '.'))
        try:
            return float(t.replace('.', '').replace(',', ''))
        except ValueError:
            return None

    def detectar(self, texto: str) -> List[PrecioDetectado]:
        """Devuelve TODOS los candidatos de precio en orden de aparición."""
        if not texto:
            return []

        candidatos: List[PrecioDetectado] = []
        spans_usados: List[Tuple[int, int]] = []

        def _ocupa(start, end):
            return any(not (end <= s or start >= e) for s, e in spans_usados)

        # 1) Sufijo "mil/k/lucas" PRIMERO (más específico que dígitos solos)
        for m in self._re_precio_sufijo.finditer(texto):
            num = self._normalizar_numero(m.group(1))
            if num is None:
                continue
            candidatos.append(PrecioDetectado(
                valor=num * 1000,
                texto_original=m.group(0),
                posicion=m.start(),
            ))
            spans_usados.append(m.span())

        # 2) Precio completo con separadores ($32.000, 3200000)
        for m in self._re_precio_completo.finditer(texto):
            if _ocupa(*m.span()):
                continue
            num = self._normalizar_numero(m.group(1))
            if num is None or num < 1000:
                continue
            candidatos.append(PrecioDetectado(
                valor=num,
                texto_original=m.group(0),
                posicion=m.start(),
            ))
            spans_usados.append(m.span())

        # 3) Implícito ("a 32") — asumir miles si está en rango plausible
        for m in self._re_precio_implicito.finditer(texto):
            if _ocupa(*m.span()):
                continue
            num = self._normalizar_numero(m.group(1))
            if num is None:
                continue
            if 10 <= num <= 999:
                candidatos.append(PrecioDetectado(
                    valor=num * 1000,
                    texto_original=m.group(0),
                    posicion=m.start(),
                ))
                spans_usados.append(m.span())

        # 4) Anotar contextos por proximidad
        kg_matches = [(m.start(), m.end(), int(m.group(1)))
                      for m in self._re_kg.finditer(texto)]
        unidad_matches = [(m.start(), m.end(), m.group(1).lower())
                          for m in self._re_unidad.finditer(texto)]

        for c in candidatos:
            mejor_kg, mejor_dist_kg = None, 30
            for s, e, kg in kg_matches:
                dist = min(abs(s - c.posicion), abs(e - c.posicion))
                if dist < mejor_dist_kg:
                    mejor_dist_kg, mejor_kg = dist, kg
            c.contexto_kg = mejor_kg

            mejor_unidad, mejor_dist_u = None, 40
            for s, e, u in unidad_matches:
                dist = min(abs(s - c.posicion), abs(e - c.posicion))
                if dist < mejor_dist_u:
                    mejor_dist_u, mejor_unidad = dist, u
            c.contexto_unidad = mejor_unidad

        candidatos.sort(key=lambda x: x.posicion)
        return candidatos

    @classmethod
    def precio_unitario_plausible(cls, valor: float) -> bool:
        """¿Cae en el rango razonable para un bulto de cemento?"""
        return cls.PRECIO_UNITARIO_MIN <= valor <= cls.PRECIO_UNITARIO_MAX




class GestorBusquedaMarcas:
    """
    Análogo a GestorBusquedaProductos pero para la tabla `marcas_productos`.
    Carga el catálogo de marcas (filtrado por regional opcional) y permite
    resolver nombres con typos via fuzzy matching.
    """

    def __init__(self, db_manager, regional: Optional[str] = None):
        self.db_manager = db_manager
        self.regional = regional
        self.buscador = None
        self._cargar_catalogo()

    def _cargar_catalogo(self):
        try:
            with self.db_manager.get_session() as session:
                q = session.query(MarcaProducto)
                if self.regional:
                    q = q.filter(
                        (MarcaProducto.regional == self.regional)
                        | (MarcaProducto.regional.is_(None))
                    )
                marcas = q.all()
                # Dedup por nombre normalizado para no inflar el catálogo si
                # hay marcas duplicadas en distintas regionales/municipios.
                catalogo: Dict[uuid.UUID, str] = {}
                vistos: set = set()
                for m in marcas:
                    norm = NormalizadorNombres.normalizar(m.nombre_marca)
                    if norm in vistos:
                        continue
                    vistos.add(norm)
                    catalogo[m.id_marca] = m.nombre_marca
                self.buscador = BuscadorProductos(catalogo)
                logger.info(f"🏷️  Catálogo de marcas cargado: {len(catalogo)} marcas")
        except Exception as e:
            logger.error(f"Error cargando catálogo de marcas: {e}")
            self.buscador = BuscadorProductos({})

    def recargar_catalogo(self):
        self._cargar_catalogo()

    def buscar_marca(self, nombre: str,
                     threshold: float = 0.80) -> Optional[ResultadoBusqueda]:
        """
        Busca una marca por nombre. Devuelve ResultadoBusqueda completo
        (con score y alternativas) si supera el threshold, None si no.
        """
        if not self.buscador or not nombre:
            return None
        resultado = self.buscador.buscar(nombre, threshold_minimo=0.60)
        if resultado.es_valido(threshold):
            return resultado
        return None




class ExtractorTextoAcumulativo:
    """
    Extractor que reconstruye el estado de la cotización a partir del historial
    completo, no solo del último mensaje. Resuelve el problema de info repartida
    en varios turnos.

    Args al construir:
        gestor_productos: GestorBusquedaProductos (catálogo de los 11 productos)
        gestor_marcas: GestorBusquedaMarcas (catálogo de las 16 marcas)
        anthropic_client: AnthropicExtractionClient (solo como fallback)

    Thresholds:
        threshold_producto: score mínimo fuzzy para aceptar producto (default 0.75)
        threshold_marca: score mínimo fuzzy para aceptar marca (default 0.80)
    """

    def __init__(self,
                 gestor_productos,
                 gestor_marcas,
                 anthropic_client: Optional["AnthropicExtractionClient"] = None,
                 threshold_producto: float = 0.75,
                 threshold_marca: float = 0.80):
        self.gestor_productos = gestor_productos
        self.gestor_marcas = gestor_marcas
        self.anthropic_client = anthropic_client
        self.threshold_producto = threshold_producto
        self.threshold_marca = threshold_marca
        self.detector_precios = DetectorPrecios()

    # ------------------------------------------------------------------
    # Palabras genéricas que NO deben usarse aisladas para matching de producto.
    # "cemento" sola matchearía con CUALQUIER producto del catálogo (todos los
    # productos contienen la palabra). Necesitamos un calificador adicional.
    _PALABRAS_GENERICAS_PRODUCTO = {"cemento", "producto", "bulto", "saco", "kg", "kilos"}

    def _extraer_producto(self, texto: str) -> Optional[Tuple[uuid.UUID, str, float]]:
        """
        Busca el producto en el texto. Estrategia escalonada:
        1. Frase completa (sin filtros).
        2. Ventanas de 2-4 palabras.
        3. Palabras sueltas calificadoras (≥5 chars y NO genéricas), para
           capturar abreviaciones tipo "estruc" → "estructural".

        Filtra ventanas que tras normalizar quedan solo en palabras genéricas
        (p. ej. "cemento" sola), porque matchearían con cualquier producto.
        """
        if not texto or not self.gestor_productos or not self.gestor_productos.buscador:
            return None

        candidatos: List[ResultadoBusqueda] = []

        def _es_consulta_inutil(consulta: str) -> bool:
            """¿La consulta es solo palabras genéricas? Si sí, descartarla."""
            try:
                norm = NormalizadorNombres.normalizar(consulta)
            except Exception:
                return False
            tokens = [t for t in norm.split() if t]
            if not tokens:
                return True
            # Si todos los tokens son genéricos, la consulta no aporta señal
            return all(t in self._PALABRAS_GENERICAS_PRODUCTO for t in tokens)

        def _intentar(consulta: str):
            if not consulta or _es_consulta_inutil(consulta):
                return
            r = self.gestor_productos.buscador.buscar(consulta, threshold_minimo=0.60)
            if r.id_producto and r.score_similitud >= self.threshold_producto:
                candidatos.append(r)

        # 1) Frase completa
        _intentar(texto)

        # 2) Ventanas de 4..2 palabras
        palabras = texto.split()
        for n in (4, 3, 2):
            for i in range(len(palabras) - n + 1):
                ventana = ' '.join(palabras[i:i+n])
                if len(ventana) < 5:
                    continue
                _intentar(ventana)

        # 3) Palabras sueltas SIGNIFICATIVAS (>=5 chars, no genéricas).
        # Esto captura abreviaciones tipo "estruc" → "Cemento estructural max"
        # via partial_ratio interno del BuscadorProductos.
        for palabra in palabras:
            limpia = palabra.strip(".,;:¿?¡!").lower()
            if len(limpia) < 5:
                continue
            if limpia in self._PALABRAS_GENERICAS_PRODUCTO:
                continue
            _intentar(limpia)

        # 4) Fallback: comparar palabras significativas contra los nombres
        # ORIGINALES del catálogo (sin pasar por NormalizadorNombres). Esto
        # es necesario porque algunos catálogos tienen palabras clave que
        # el normalizador trata como sinónimos vacíos (ej. "estructural",
        # "portland"). partial_ratio encuentra abreviaciones como
        # "estruc" → "estructural" sobre el nombre crudo.
        try:
            from fuzzywuzzy import fuzz as _fz
            for palabra in palabras:
                limpia = palabra.strip(".,;:¿?¡!").lower()
                if len(limpia) < 5 or limpia in self._PALABRAS_GENERICAS_PRODUCTO:
                    continue
                for pid, pnombre in self.gestor_productos.buscador.catalogo.items():
                    pn_low = pnombre.lower()
                    pr = _fz.partial_ratio(limpia, pn_low) / 100.0
                    # Exigir alta confianza para palabra suelta sobre nombre
                    # original (es una señal débil, debe estar muy contenida).
                    if pr >= 0.90:
                        # Construir un ResultadoBusqueda manual
                        rb = ResultadoBusqueda(
                            id_producto=pid,
                            nombre_producto=pnombre,
                            nombre_buscado=limpia,
                            score_similitud=pr * 0.85,  # penalizar señal indirecta
                            metodo="partial_directo",
                            alternativas=[],
                            confianza="MEDIA" if pr >= 0.95 else "BAJA",
                        )
                        if rb.score_similitud >= self.threshold_producto:
                            candidatos.append(rb)
        except Exception as e:
            logger.debug(f"Fallback partial_ratio falló: {e}")

        if not candidatos:
            return None
        # Preferir el match con más palabras coincidentes (más específico).
        # En empate de score, gana el de nombre_producto más largo (más específico).
        candidatos.sort(
            key=lambda x: (x.score_similitud, len(x.nombre_producto)),
            reverse=True
        )
        ganador = candidatos[0]
        return (ganador.id_producto, ganador.nombre_producto, ganador.score_similitud)

    # ------------------------------------------------------------------
    def _extraer_marca(self, texto: str) -> Optional[Tuple[uuid.UUID, str, float]]:
        """Devuelve (id, nombre, score) de la marca si la encuentra."""
        if not texto or not self.gestor_marcas or not self.gestor_marcas.buscador:
            return None
        candidatos: List[ResultadoBusqueda] = []
        # Las marcas suelen ser 1-2 palabras (Argos, Cemex, San Marcos)
        palabras = texto.split()
        for n in (2, 1):
            for i in range(len(palabras) - n + 1):
                ventana = ' '.join(palabras[i:i+n])
                if len(ventana) < 3:
                    continue
                r = self.gestor_marcas.buscador.buscar(ventana, threshold_minimo=0.65)
                if r.id_producto and r.score_similitud >= self.threshold_marca:
                    candidatos.append(r)
        if not candidatos:
            return None
        candidatos.sort(key=lambda x: x.score_similitud, reverse=True)
        ganador = candidatos[0]
        return (ganador.id_producto, ganador.nombre_producto, ganador.score_similitud)

    # ------------------------------------------------------------------
    def _seleccionar_precio_unitario(self,
                                     precios: List[PrecioDetectado]
                                     ) -> Optional[PrecioDetectado]:
        """
        Dada una lista de precios detectados en un mensaje, elige el más
        probable como precio UNITARIO.

        Heurística:
        - Preferir precios en rango plausible ($15k–$60k)
        - Entre los plausibles, preferir el que tenga contexto "kg=50"
          (presentación estándar) o "bulto"
        - Si NINGUNO está en rango, devolver el menor de los grandes (sospechoso)
        """
        if not precios:
            return None

        plausibles = [p for p in precios
                      if DetectorPrecios.precio_unitario_plausible(p.valor)]

        if plausibles:
            # Preferir 50kg, luego con unidad bulto, luego el primero
            for p in plausibles:
                if p.contexto_kg == 50:
                    return p
            for p in plausibles:
                if p.contexto_unidad in ("bulto", "saco"):
                    return p
            return plausibles[0]

        # Ningún precio en rango → marcar como sospechoso pero devolverlo
        # Probablemente es precio total. El llamador decide qué hacer.
        precios_ordenados = sorted(precios, key=lambda p: p.valor)
        return precios_ordenados[0]

    # ------------------------------------------------------------------
    def _procesar_turno(self, mensaje_cliente: str, turno: int,
                        estado: EstadoExtraccionAcumulado):
        """Aplica detección determinista a un mensaje y mergea con el estado."""
        if not mensaje_cliente:
            return

        # Producto (refresca solo si encontramos uno con score ≥ al actual)
        prod = self._extraer_producto(mensaje_cliente)
        if prod:
            pid, pnombre, pscore = prod
            if pscore >= estado.producto_score:
                estado.producto_id = pid
                estado.producto_nombre = pnombre
                estado.producto_score = pscore
                estado.producto_turno = turno
                estado.fuentes.append(f"T{turno}:producto:fuzzy({pscore:.2f})")

        # Marca
        marca = self._extraer_marca(mensaje_cliente)
        if marca:
            mid, mnombre, mscore = marca
            if mscore >= estado.marca_score:
                estado.marca_id = mid
                estado.marca_nombre = mnombre
                estado.marca_score = mscore
                estado.marca_turno = turno
                estado.fuentes.append(f"T{turno}:marca:fuzzy({mscore:.2f})")

        # Precios
        precios = self.detector_precios.detectar(mensaje_cliente)
        elegido = self._seleccionar_precio_unitario(precios)
        if elegido:
            plausible = DetectorPrecios.precio_unitario_plausible(elegido.valor)
            # Si ya tenemos un precio plausible y el nuevo NO lo es, no sobrescribir.
            if estado.precio_unitario and not estado.precio_sospechoso and not plausible:
                pass  # mantener el viejo
            else:
                estado.precio_unitario = elegido.valor
                estado.precio_sospechoso = not plausible
                estado.precio_turno = turno
                if elegido.contexto_kg:
                    estado.kg_presentacion = elegido.contexto_kg
                if elegido.contexto_unidad:
                    estado.unidad = elegido.contexto_unidad
                estado.fuentes.append(
                    f"T{turno}:precio:{elegido.valor:.0f}"
                    f"{'⚠' if not plausible else ''}"
                )

    # ------------------------------------------------------------------
    def extraer_de_historial(self,
                             historial: List[Dict[str, str]],
                             mensaje_actual: str = "",
                             usar_llm_fallback: bool = True
                             ) -> EstadoExtraccionAcumulado:
        """
        Reconstruye el estado de la cotización a partir del historial completo.

        Args:
            historial: lista de dicts {role: "user"|"assistant", content: str}
                       (formato que devuelve DatabaseManager.obtener_historial_reciente)
            mensaje_actual: mensaje del cliente que está siendo procesado AHORA
                            (puede no estar todavía en `historial` si llamamos
                             ANTES de guardarlo)
            usar_llm_fallback: si True, invoca al LLM cuando faltan campos
                               tras la fase determinista

        Returns:
            EstadoExtraccionAcumulado con la mejor info disponible
        """
        estado = EstadoExtraccionAcumulado()

        # 1) Procesar TODOS los mensajes del cliente en el historial
        turno = 0
        for msg in historial or []:
            if msg.get("role") == "user":
                turno += 1
                contenido = msg.get("content", "") or ""
                # Saltar marcadores sintéticos de outreach
                if contenido.startswith("[OUTREACH") or contenido.startswith("[imagen"):
                    continue
                self._procesar_turno(contenido, turno, estado)

        # 2) Procesar el mensaje actual (que puede no estar en historial todavía)
        if mensaje_actual:
            turno += 1
            self._procesar_turno(mensaje_actual, turno, estado)

        # 3) Si está completo, listo
        if estado.es_completo():
            logger.info(f"✅ Extractor acumulativo (determinista): {estado.resumen()}")
            return estado

        # 4) Fallback a LLM SOLO si faltan campos Y hay un cliente disponible
        if usar_llm_fallback and self.anthropic_client and (mensaje_actual or historial):
            faltantes = estado.faltantes()
            logger.info(
                f"🤖 Extractor acumulativo: faltantes={faltantes} → fallback LLM"
            )
            self._fallback_llm(estado, historial, mensaje_actual)

        logger.info(f"📊 Extractor acumulativo (final): {estado.resumen()}")
        return estado

    # ------------------------------------------------------------------
    def _fallback_llm(self,
                      estado: EstadoExtraccionAcumulado,
                      historial: List[Dict[str, str]],
                      mensaje_actual: str):
        """
        Invoca al LLM SOLO para los campos que faltan, con el catálogo como
        opciones cerradas. Esto reduce alucinaciones y costo.
        """
        if not self.anthropic_client:
            return

        # Tomar últimos N mensajes del cliente para contexto
        mensajes_cliente = []
        for msg in (historial or [])[-10:]:
            if msg.get("role") == "user":
                c = msg.get("content", "")
                if c and not c.startswith("[OUTREACH") and not c.startswith("[imagen"):
                    mensajes_cliente.append(c)
        if mensaje_actual:
            mensajes_cliente.append(mensaje_actual)

        if not mensajes_cliente:
            return

        contexto = "\n".join(f"- {m}" for m in mensajes_cliente[-6:])

        # Catálogos como opciones cerradas
        productos_lista = []
        if self.gestor_productos and self.gestor_productos.buscador:
            productos_lista = list(self.gestor_productos.buscador.catalogo.values())
        marcas_lista = []
        if self.gestor_marcas and self.gestor_marcas.buscador:
            marcas_lista = list(self.gestor_marcas.buscador.catalogo.values())

        prompt = (
            "Eres un extractor de cotizaciones de cemento. Te paso una conversación "
            "de WhatsApp entre una FERRETERÍA (vendedora) y un BOT comprador. "
            "Tu trabajo es extraer SOLO los campos que aún faltan.\n\n"
            f"PRODUCTO ya identificado: {estado.producto_nombre or 'NO'}\n"
            f"MARCA ya identificada: {estado.marca_nombre or 'NO'}\n"
            f"PRECIO unitario ya identificado: {estado.precio_unitario or 'NO'}\n\n"
            "OPCIONES VÁLIDAS DE PRODUCTO (elige una EXACTAMENTE como aparece, o null):\n"
            f"{productos_lista}\n\n"
            "OPCIONES VÁLIDAS DE MARCA (elige una EXACTAMENTE como aparece, o null):\n"
            f"{marcas_lista}\n\n"
            "Devuelve JSON ESTRICTO con SOLO los campos faltantes:\n"
            '{"producto": str|null, "marca": str|null, "precio_unitario": float|null}\n\n'
            "REGLAS:\n"
            "- Si un campo ya está identificado, devuélvelo igual (no lo cambies).\n"
            "- Si NO encuentras un campo en la conversación, devuelve null.\n"
            "- precio_unitario en COP, sin separadores. '32 mil' → 32000.\n"
            "- NO inventes. Si dudas, null.\n"
            "- producto y marca DEBEN coincidir EXACTAMENTE con las opciones válidas.\n\n"
            f"CONVERSACIÓN:\n{contexto}"
        )

        try:
            response = self.anthropic_client.client.messages.create(
                model=self.anthropic_client.config.model_name,
                max_tokens=200,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            for fence in ('```json', '```'):
                if text.startswith(fence):
                    text = text[len(fence):].lstrip()
            if text.endswith('```'):
                text = text[:-3].rstrip()
            data = json.loads(text)

            # Aplicar SOLO si llena un hueco
            if estado.producto_nombre is None:
                p = data.get("producto")
                if p and self.gestor_productos:
                    pid = self.gestor_productos.buscar_producto(p, threshold=0.85)
                    if pid:
                        estado.producto_id = pid
                        estado.producto_nombre = p
                        estado.producto_score = 0.85
                        estado.fuentes.append("LLM:producto")

            if estado.marca_nombre is None:
                m = data.get("marca")
                if m and self.gestor_marcas:
                    rb = self.gestor_marcas.buscar_marca(m, threshold=0.85)
                    if rb:
                        estado.marca_id = rb.id_producto
                        estado.marca_nombre = rb.nombre_producto
                        estado.marca_score = rb.score_similitud
                        estado.fuentes.append("LLM:marca")

            if estado.precio_unitario is None:
                pr = data.get("precio_unitario")
                try:
                    pr_f = float(pr) if pr is not None else None
                except (TypeError, ValueError):
                    pr_f = None
                if pr_f and pr_f > 0:
                    estado.precio_unitario = pr_f
                    estado.precio_sospechoso = (
                        not DetectorPrecios.precio_unitario_plausible(pr_f)
                    )
                    estado.fuentes.append("LLM:precio")

        except json.JSONDecodeError as e:
            logger.error(f"Fallback LLM: JSON inválido: {e}")
        except Exception as e:
            logger.error(f"Fallback LLM: error: {e}")




__all__ = [
    "NormalizadorNombres",
    "MetricasSimilitud",
    "BuscadorProductos",
    "GestorBusquedaProductos",
    "DetectorPrecios",
    "GestorBusquedaMarcas",
    "ExtractorTextoAcumulativo",
]
