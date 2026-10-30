"""Estructuras que devuelven los parsers de más de una marca (HSGQ, BCD) para una sola ONU."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from olterra.identifiers import normalize_mac


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


@dataclass
class MacEntry:
    """Una MAC aprendida detrás de una ONU (``onu.mac``, ``mac.by_pon``).

    ``onu`` va como lo cuenta Olterra (desde 1); None si la OLT no dice de qué ONU es (la BCD
    en su tabla por PON).
    """

    mac: str
    vlan: int | None
    pon: int | None
    onu: int | None
    kind: str | None = None


# Una MAC en cualquiera de las formas de las OLT: 70:b6:4f:64:59:85, 0228:9a89:6a98, 7088.b64f.6459.
_MAC = re.compile(
    r"(?<![0-9A-Fa-f:.])((?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}"
    r"|[0-9A-Fa-f]{4}[:.][0-9A-Fa-f]{4}[:.][0-9A-Fa-f]{4})(?![0-9A-Fa-f:.])"
)


def parse_mac_rows(
    text: str, row: Callable[[str, re.Match[str]], MacEntry | None]
) -> list[MacEntry]:
    """Una entrada por línea que tenga una MAC; ``row`` arma la entrada con el resto de la línea."""
    entries: list[MacEntry] = []
    for line in text.splitlines():
        match = _MAC.search(line)
        if match is None or normalize_mac(match.group(1)) is None:
            continue
        entry = row(line, match)
        if entry is not None:
            entries.append(entry)
    return entries


def mac_of(match: re.Match[str]) -> str:
    # parse_mac_rows solo llama con MAC válidas; el "or" es para el tipo.
    return normalize_mac(match.group(1)) or match.group(1)
