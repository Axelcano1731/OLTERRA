"""Estructuras que devuelven los parsers de más de una marca (HSGQ, BCD) para una sola ONU."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OnuDetail:
    """Detalle de una ONU (``onu.detail``). ``onu`` va como lo cuenta Olterra (desde 1)."""

    pon: int
    onu: int
    serial: str | None
    name: str | None
    # working, offline, los o dyinggasp, como en el monitoreo.
    phase: str
    run_state: str
    distance_m: int | None
    line_profile: str | None
    service_profile: str | None
    last_up: str | None
    last_down: str | None
    last_down_cause: str | None
    uptime: str | None


@dataclass
class OnuVersion:
    """Versión de una ONU (``onu.version``); todo None si la ONU no está en línea."""

    online: bool
    vendor: str | None
    model: str | None
    hardware: str | None
    software: str | None
