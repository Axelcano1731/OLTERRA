"""Fuentes de datos de la conciliación. Cada una devuelve registros del modelo."""


class SourceError(ValueError):
    """La fuente no se pudo leer o no trae las columnas mínimas."""
