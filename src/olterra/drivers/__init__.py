"""Drivers por fabricante y familia: plantillas de comandos, parsers y capacidades.

Un driver es datos y funciones puras; no abre conexiones. La nube lo usa para
armar planes y para interpretar la salida cruda que devuelve el ejecutor.
"""

from __future__ import annotations

from olterra.drivers.base import Driver
from olterra.drivers.vsol_gpon import VSOL_GPON

DRIVERS: dict[str, Driver] = {VSOL_GPON.key: VSOL_GPON}


def get_driver(key: str) -> Driver:
    try:
        return DRIVERS[key]
    except KeyError:
        raise KeyError(
            f"No hay driver '{key}'. Disponibles: {', '.join(sorted(DRIVERS))}"
        ) from None
