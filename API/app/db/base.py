"""Base declarativa de SQLAlchemy (migrado de la celda 5 del notebook).

Todos los modelos ORM heredan de `Base`. Se mantiene en su propio módulo para
evitar imports circulares entre los modelos y la sesión de base de datos.
"""
from __future__ import annotations

from sqlalchemy.orm import declarative_base

Base = declarative_base()

__all__ = ["Base"]
