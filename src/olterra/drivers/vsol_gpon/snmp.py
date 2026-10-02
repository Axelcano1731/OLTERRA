"""SNMP de VSOL GPON: OIDs y lectura de las tablas por ONU.

OIDs resueltos del MIB V1600G del fabricante (incluido en LibreNMS PR #19368).
Índice de cada fila: ``<columna>.<pon>.<onu>``. Los valores ópticos llegan como
texto decimal (``"-10.47"``), confirmado en el snmprec de un V1600GS real.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from olterra.drivers.textparse import parse_number
from olterra.executor.plan import Varbind
from olterra.identifiers import normalize_gpon_serial

ENTERPRISE = "1.3.6.1.4.1.37950"
ROOT = f"{ENTERPRISE}.1.1.6.1.1"
STATUS_TABLE = f"{ROOT}.1"  # gOnuStaInfoTable
AUTH_TABLE = f"{ROOT}.2"  # gOnuAuthInfoTable
OPTICAL_TABLE = f"{ROOT}.3"  # gOnuOpticalInfoTable
DETAIL_TABLE = f"{ROOT}.4"  # gOnuDetailInfoTable
RTT_TABLE = f"{ROOT}.12"  # gOnuRttTable
PON_TRANSCEIVER_TABLE = f"{ENTERPRISE}.1.1.5.10.13.1.1"  # ponTransceiverTable
SYS_DESCR = "1.3.6.1.2.1.1.1"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# Tablas que el sondeo periódico recorre cuando la OLT las soporta.
POLL_TABLES = (STATUS_TABLE, AUTH_TABLE, OPTICAL_TABLE, RTT_TABLE)

# gOnuStaInfoPhaseSta
PHASE_STATES = {
    0: "logging",
    1: "los",
    2: "sync_mib",
    3: "working",
    4: "dying_gasp",
    5: "auth_fail",
    6: "offline",
    7: "deactivated",
    8: "config_fail",
}
# gOnuAuthInfoAuthMode
AUTH_MODES = {
    1: "sn",
    2: "pw",
    3: "hpw",
    4: "sn+pw",
    5: "sn+hpw",
    6: "loid",
    7: "loid+pw",
    10: "loid+hpw",
}

_MODEL = re.compile(r"\bV\d{4}[A-Z0-9-]*\b")


def detect_model(sys_descr: str) -> str | None:
    """``sysDescr`` de VSOL trae el modelo (``V1600GS``, ``V1600G1B``)."""
    match = _MODEL.search(sys_descr or "")
    return match.group(0) if match else None


def table_cells(
    varbinds: Iterable[Varbind], table_oid: str
) -> dict[tuple[int, int], dict[int, str]]:
    prefix = table_oid + ".1."
    rows: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    for vb in varbinds:
        if not vb.oid.startswith(prefix):
            continue
        parts = vb.oid[len(prefix) :].split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        column, pon, onu = (int(p) for p in parts)
        rows[(pon, onu)][column] = vb.value
    return dict(rows)


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _dbm(value: str | None) -> float | None:
    # La OLT reporta 0.00 cuando no tiene la lectura; 0 dBm no es una potencia
    # plausible para una ONU GPON (sería saturación), así que se toma como ausente.
    number = parse_number(value)
    return None if number is None or number == 0 else number


@dataclass(frozen=True)
class OnuStatus:
    pon: int
    onu: int
    admin_enabled: bool | None
    phase: str | None
    description: str | None
    last_register: str | None
    last_deregister: str | None
    last_deregister_reason: str | None

    @property
    def online(self) -> bool:
        return self.phase == "working"


def parse_status(varbinds: Iterable[Varbind]) -> list[OnuStatus]:
    result = []
    for (pon, onu), cols in sorted(table_cells(varbinds, STATUS_TABLE).items()):
        admin = _int(cols.get(3))
        phase = _int(cols.get(5))
        result.append(
            OnuStatus(
                pon=pon,
                onu=onu,
                admin_enabled=None if admin is None else admin == 1,
                phase=PHASE_STATES.get(phase, f"desconocido({phase})")
                if phase is not None
                else None,
                description=cols.get(7) or None,
                last_register=cols.get(8) or None,
                last_deregister=cols.get(9) or None,
                last_deregister_reason=cols.get(10) or None,
            )
        )
    return result


@dataclass(frozen=True)
class OnuAuth:
    pon: int
    onu: int
    profile: str | None
    auth_mode: str | None
    serial: str | None
    serial_raw: str | None
    model: str | None


def parse_auth(varbinds: Iterable[Varbind]) -> list[OnuAuth]:
    result = []
    for (pon, onu), cols in sorted(table_cells(varbinds, AUTH_TABLE).items()):
        mode = _int(cols.get(4))
        raw = cols.get(5)
        result.append(
            OnuAuth(
                pon=pon,
                onu=onu,
                profile=cols.get(3) or None,
                auth_mode=AUTH_MODES.get(mode, str(mode)) if mode is not None else None,
                serial=normalize_gpon_serial(raw),
                serial_raw=raw,
                model=cols.get(6) or None,
            )
        )
    return result


@dataclass(frozen=True)
class OnuOptical:
    pon: int
    onu: int
    temperature_c: float | None
    voltage_v: float | None
    bias_ma: float | None
    tx_dbm: float | None
    rx_dbm: float | None
    olt_rx_dbm: float | None


def parse_optical(varbinds: Iterable[Varbind]) -> list[OnuOptical]:
    result = []
    for (pon, onu), cols in sorted(table_cells(varbinds, OPTICAL_TABLE).items()):
        result.append(
            OnuOptical(
                pon=pon,
                onu=onu,
                temperature_c=parse_number(cols.get(3)),
                voltage_v=parse_number(cols.get(4)),
                bias_ma=parse_number(cols.get(5)),
                tx_dbm=_dbm(cols.get(6)),
                rx_dbm=_dbm(cols.get(7)),
                olt_rx_dbm=_dbm(cols.get(8)),
            )
        )
    return result


def parse_distance(varbinds: Iterable[Varbind]) -> dict[tuple[int, int], int]:
    """Distancia de ranging por ONU. Unidad por confirmar en laboratorio (¿metros?)."""
    result = {}
    for key, cols in table_cells(varbinds, RTT_TABLE).items():
        distance = _int(cols.get(3))
        if distance is not None:
            result[key] = distance
    return result
