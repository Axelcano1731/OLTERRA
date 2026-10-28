"""Drivers por fabricante y familia: plantillas de comandos, parsers y capacidades.

Un driver es datos y funciones puras; no abre conexiones. La nube lo usa para
armar planes y para interpretar la salida cruda que devuelve el ejecutor.
"""

from __future__ import annotations

from olterra.drivers.base import Driver
from olterra.drivers.bcd_gpon import BCD_GPON
from olterra.drivers.hsgq_gpon import HSGQ_GPON
from olterra.drivers.vsol_gpon import VSOL_GPON

DRIVERS: dict[str, Driver] = {driver.key: driver for driver in (VSOL_GPON, HSGQ_GPON, BCD_GPON)}


def get_driver(key: str) -> Driver:
    try:
        return DRIVERS[key]
    except KeyError:
        raise KeyError(
            f"No hay driver '{key}'. Disponibles: {', '.join(sorted(DRIVERS))}"
        ) from None
