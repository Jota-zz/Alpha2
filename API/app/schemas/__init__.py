"""Esquemas / dataclasses de dominio (migrado de las celdas 6.5.4, 6.6.1, 6.6.3).

Objetos de transferencia usados por los servicios de matching y extracción.
No son modelos ORM (esos viven en `app.models`); son estructuras de resultado.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ResultadoBusqueda:
    """Resultado de búsqueda difusa de un producto en el catálogo."""

    id_producto: Optional[uuid.UUID]
    nombre_producto: str
    nombre_buscado: str
    score_similitud: float
    metodo: str  # "exacto", "normalizado", "difuso_ponderado"
    alternativas: List[Tuple[str, float, uuid.UUID]]  # (nombre, score, id)
    confianza: str  # "ALTA", "MEDIA", "BAJA", "NO_ENCONTRADO"

    def es_valido(self, threshold: float = 0.80) -> bool:
        """Determina si el resultado es lo suficientemente confiable."""
        return self.score_similitud >= threshold

    def __str__(self) -> str:
        return (
            f"Búsqueda: {self.nombre_buscado!r}\n"
            f"  → Encontrado: {self.nombre_producto!r}\n"
            f"  → Score: {self.score_similitud:.2%}\n"
            f"  → Confianza: {self.confianza}\n"
            f"  → Método: {self.metodo}"
        )


@dataclass
class PrecioDetectado:
    """Un candidato de precio detectado por regex en el texto."""

    valor: float                            # COP, normalizado a número
    texto_original: str                     # match crudo del regex
    contexto_unidad: Optional[str] = None   # "bulto", "saco" si se infirió
    contexto_kg: Optional[int] = None       # 50 o 25 si "50 kg" / "25 kg"
    posicion: int = 0                       # offset en el texto


@dataclass
class EstadoExtraccionAcumulado:
    """Estado acumulado de una cotización a lo largo de la conversación.

    Cada campo guarda el último valor confirmado + en qué turno apareció.
    """

    producto_nombre: Optional[str] = None
    producto_id: Optional[uuid.UUID] = None
    producto_score: float = 0.0
    producto_turno: int = -1

    marca_nombre: Optional[str] = None
    marca_id: Optional[uuid.UUID] = None
    marca_score: float = 0.0
    marca_turno: int = -1

    precio_unitario: Optional[float] = None
    precio_turno: int = -1
    precio_sospechoso: bool = False  # fuera de rango plausible

    cantidad: Optional[float] = None
    unidad: Optional[str] = None
    kg_presentacion: Optional[int] = None  # 50, 25, etc.
    disponibilidad: Optional[str] = None

    # Diagnóstico: qué métodos se usaron y dónde.
    fuentes: List[str] = field(default_factory=list)

    def es_completo(self) -> bool:
        """¿Tenemos los 3 campos mínimos para registrar cotización?"""
        return (
            self.producto_nombre is not None
            and self.marca_nombre is not None
            and self.precio_unitario is not None
            and self.precio_unitario > 0
        )

    def faltantes(self) -> List[str]:
        """Lista de campos faltantes para dar feedback útil al LLM fallback."""
        out = []
        if self.producto_nombre is None:
            out.append("producto")
        if self.marca_nombre is None:
            out.append("marca")
        if self.precio_unitario is None:
            out.append("precio")
        return out

    def resumen(self) -> str:
        """Línea de log compacta para diagnóstico."""
        p = self.producto_nombre or "?"
        m = self.marca_nombre or "?"
        pr = f"${self.precio_unitario:,.0f}" if self.precio_unitario else "?"
        flag = " ⚠SOSPECHOSO" if self.precio_sospechoso else ""
        return f"producto={p}, marca={m}, precio={pr}{flag}"


__all__ = [
    "ResultadoBusqueda",
    "PrecioDetectado",
    "EstadoExtraccionAcumulado",
]
