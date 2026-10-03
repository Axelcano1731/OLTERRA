"""Parsers de las salidas CLI de VSOL GPON.

Todavía no hay capturas reales (llegan en la fase 0 con el laboratorio), así que
estos parsers no suponen nombres de columna exactos: reconocen la tabla por su
forma y las columnas por nombre aproximado o por el contenido (una columna donde
casi todo es un serial GPON es la del serial). Lo que no reconocen lo reportan
con ``UnrecognizedOutput``; nunca devuelven datos a medias en silencio.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from olterra.drivers.base import UnrecognizedOutput
from olterra.drivers.textparse import (
    Table,
    find_tables,
    norm_header,
    parse_dbm,
    parse_key_values,
    parse_number,
    parse_onu_ref,
)
from olterra.identifiers import normalize_gpon_serial

INDEX_COLUMNS = ("onuindex", "onuid", "onu", "onuno", "index", "id", "no")
SERIAL_COLUMNS = ("sn", "serial", "serialnumber", "onusn", "authinfo", "snloid", "sninfo")
STATE_COLUMNS = (
    "phasestate",
    "phase",
    "state",
    "status",
    "runstate",
    "onlinestate",
    "onlinestatus",
    "estado",
)
MODEL_COLUMNS = ("model", "onumodel", "type", "onutype", "equipmentid", "equipid", "equid")
PROFILE_COLUMNS = ("profile", "onuprofile", "profilename")
DESCRIPTION_COLUMNS = ("description", "desc", "name", "onuname")
PON_COLUMNS = ("pon", "ponport", "port", "ponid")
PASSWORD_COLUMNS = ("password", "pw", "pwd")

_EMPTY_HINTS = re.compile(
    r"(?i)\b(no (onu|record|data|entry|entries)|not found|empty|total\s*:?\s*0)\b"
)
_SEPARATOR_LINE = re.compile(r"^[\s\-=+|]*[-=]{5,}[\s\-=+|]*$", re.MULTILINE)
_MODEL = re.compile(r"\bV\d{4}[A-Z0-9-]*\b")
_SLOT_PON = re.compile(r"\d+\s*/\s*(\d+)\s*$")


def _tables_or_empty(text: str, parser: str) -> list[Table]:
    tables = find_tables(text)
    if tables:
        return tables
    if not text.strip() or _EMPTY_HINTS.search(text) or _SEPARATOR_LINE.search(text):
        return []
    raise UnrecognizedOutput(parser, "no se encontró ninguna tabla", text)


def _column_by_values(
    table: Table, predicate: Callable[[str], bool], threshold: float = 0.8
) -> str | None:
    for header in table.headers:
        values = [row.get(header, "") for row in table.rows if row.get(header, "").strip()]
        if values and sum(1 for v in values if predicate(v)) / len(values) >= threshold:
            return header
    return None


def _is_onu_ref_with_pon(value: str) -> bool:
    ref = parse_onu_ref(value)
    return ref is not None and ref.pon is not None and not value.strip().isdigit()


@dataclass(frozen=True)
class OnuRow:
    pon: int | None
    onu: int
    serial: str | None
    state: str | None = None
    model: str | None = None
    profile: str | None = None
    description: str | None = None
    raw: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AutofindRow:
    pon: int | None
    serial: str
    index: int | None = None
    password: str | None = None
    model: str | None = None
    raw: Mapping[str, str] = field(default_factory=dict)


def _locate(table: Table) -> tuple[str | None, str | None]:
    index_col = table.column(*INDEX_COLUMNS) or _column_by_values(table, _is_onu_ref_with_pon)
    serial_col = table.column(*SERIAL_COLUMNS)
    if (
        serial_col is not None
        and _column_by_values(
            Table(headers=[serial_col], rows=table.rows),
            lambda v: normalize_gpon_serial(v) is not None,
            0.5,
        )
        is None
    ):
        serial_col = None  # se llama "SN" pero no trae seriales (p. ej. LOID)
    if serial_col is None:
        serial_col = _column_by_values(table, lambda v: normalize_gpon_serial(v) is not None)
    return index_col, serial_col


def _cell(row: Mapping[str, str], column: str | None) -> str | None:
    if column is None:
        return None
    value = row.get(column, "").strip()
    return value or None


def _pon_from_row(
    row: Mapping[str, str], pon_col: str | None, default_pon: int | None
) -> int | None:
    value = _cell(row, pon_col)
    if value:
        slot_pon = _SLOT_PON.search(value)  # "GPON0/1" o "0/1"
        if slot_pon:
            return int(slot_pon.group(1))
        number = parse_number(value)
        if number is not None:
            return int(number)
    return default_pon


def parse_onu_list(text: str, default_pon: int | None = None) -> list[OnuRow]:
    """Salida de ``show onu info``: ONUs autorizadas en el puerto PON."""
    result: list[OnuRow] = []
    for table in _tables_or_empty(text, "vsol.onu.list"):
        index_col, serial_col = _locate(table)
        if index_col is None:
            continue
        pon_col = table.column(*PON_COLUMNS)
        state_col = table.column(*STATE_COLUMNS)
        model_col = table.column(*MODEL_COLUMNS)
        profile_col = table.column(*PROFILE_COLUMNS)
        description_col = table.column(*DESCRIPTION_COLUMNS)
        for row in table.rows:
            ref = parse_onu_ref(row.get(index_col, ""), default_pon)
            if ref is None:
                continue
            pon = ref.pon if ref.pon is not None else _pon_from_row(row, pon_col, default_pon)
            result.append(
                OnuRow(
                    pon=pon,
                    onu=ref.onu,
                    serial=normalize_gpon_serial(_cell(row, serial_col)),
                    state=_cell(row, state_col),
                    model=_cell(row, model_col),
                    profile=_cell(row, profile_col),
                    description=_cell(row, description_col),
                    raw=dict(row),
                )
            )
    if not result and find_tables(text):
        raise UnrecognizedOutput(
            "vsol.onu.list", "hay tabla pero ninguna columna de índice de ONU", text
        )
    return result


def parse_autofind(text: str, default_pon: int | None = None) -> list[AutofindRow]:
    """Salida de ``show onu auto-find``: ONUs conectadas sin autorizar."""
    result: list[AutofindRow] = []
    tables = _tables_or_empty(text, "vsol.onu.autofind")
    for table in tables:
        index_col, serial_col = _locate(table)
        if serial_col is None:
            continue
        pon_col = table.column(*PON_COLUMNS)
        for row in table.rows:
            serial = normalize_gpon_serial(_cell(row, serial_col))
            if serial is None:
                continue
            ref = parse_onu_ref(row.get(index_col, ""), default_pon) if index_col else None
            pon = (
                ref.pon
                if ref is not None and ref.pon is not None
                else _pon_from_row(row, pon_col, default_pon)
            )
            result.append(
                AutofindRow(
                    pon=pon,
                    serial=serial,
                    index=ref.onu if ref is not None else None,
                    password=_cell(row, table.column(*PASSWORD_COLUMNS)),
                    model=_cell(row, table.column(*MODEL_COLUMNS)),
                    raw=dict(row),
                )
            )
    if not result and tables:
        raise UnrecognizedOutput(
            "vsol.onu.autofind", "hay tabla pero ninguna columna con seriales", text
        )
    return result


@dataclass(frozen=True)
class RxReading:
    pon: int | None
    onu: int
    rx_dbm: float | None


def parse_rx_power(text: str, default_pon: int | None = None) -> list[RxReading]:
    """Salida de ``show pon onu all rx-power``: potencia recibida por ONU."""
    result: list[RxReading] = []
    tables = _tables_or_empty(text, "vsol.onu.rx_power")
    for table in tables:
        index_col = table.column(*INDEX_COLUMNS) or _column_by_values(table, _is_onu_ref_with_pon)
        rx_col = next((h for h in table.headers if "rx" in norm_header(h)), None)
        if index_col is None or rx_col is None:
            continue
        for row in table.rows:
            ref = parse_onu_ref(row.get(index_col, ""), default_pon)
            if ref is not None:
                result.append(RxReading(ref.pon, ref.onu, parse_dbm(row.get(rx_col))))
    if not result and tables:
        raise UnrecognizedOutput("vsol.onu.rx_power", "no hay columnas de índice y de RX", text)
    return result


@dataclass(frozen=True)
class OpticalInfo:
    rx_dbm: float | None
    tx_dbm: float | None
    olt_rx_dbm: float | None
    temperature_c: float | None
    voltage_v: float | None
    bias_ma: float | None


def _kv_find(kv: Mapping[str, str], *needles: str, exclude: tuple[str, ...] = ()) -> str | None:
    for key, value in kv.items():
        if all(n in key for n in needles) and not any(e in key for e in exclude):
            return value
    return None


def parse_onu_optical(text: str) -> OpticalInfo:
    """Salida de ``show onu <n> optical-info`` (``optical_info`` en la V1600G0-B)."""
    kv = parse_key_values(text)
    thresholds = ("lower", "upper", "threshold", "olt")
    info = OpticalInfo(
        rx_dbm=parse_dbm(
            _kv_find(kv, "rx", "power", exclude=("olt",))
            or _kv_find(kv, "receive", "power", exclude=("olt",))
            # V1600G0-B V1.4.8R: "Rx optical level(ONU) : -18.15"
            or _kv_find(kv, "rx", "optical", "level", exclude=thresholds)
        ),
        tx_dbm=parse_dbm(
            _kv_find(kv, "tx", "power")
            or _kv_find(kv, "transmit", "power")
            or _kv_find(kv, "tx", "optical", "level", exclude=thresholds)
        ),
        olt_rx_dbm=parse_dbm(_kv_find(kv, "olt", "rx")),
        temperature_c=parse_number(_kv_find(kv, "temp")),
        voltage_v=parse_number(_kv_find(kv, "volt") or _kv_find(kv, "vcc")),
        bias_ma=parse_number(_kv_find(kv, "bias")),
    )
    if info.rx_dbm is None and info.tx_dbm is None and info.temperature_c is None:
        raise UnrecognizedOutput("vsol.onu.optical", "no hay potencias ni temperatura", text)
    return info


@dataclass(frozen=True)
class VersionInfo:
    model: str | None
    firmware: str | None
    hardware: str | None


def parse_version(text: str) -> VersionInfo:
    """Salida de ``show version``."""
    kv = parse_key_values(text)
    model = next(
        (
            kv[k]
            for k in (
                "oltdevicemodel",  # V1600G0-B V1.4.8R: "Olt Device Model: V1600G0-B"
                "devicemodel",
                "devicetype",
                "model",
                "productmodel",
                "productname",
                "producttype",
            )
            if k in kv
        ),
        None,
    )
    if model is None:
        match = _MODEL.search(text)
        model = match.group(0) if match else None
    firmware = next(
        (
            kv[k]
            for k in (
                "softwareversion",
                "firmwareversion",
                "swversion",
                "softversion",
                "firmware",
                "version",
            )
            if k in kv
        ),
        None,
    )
    hardware = next(
        (kv[k] for k in ("hardwareversion", "hwversion", "hardversion") if k in kv), None
    )
    if model is None and firmware is None:
        raise UnrecognizedOutput("vsol.system.version", "no se halló modelo ni firmware", text)
    return VersionInfo(model=model, firmware=firmware, hardware=hardware)


# --- V1600G0-B V1.4.8R: estado, distancia, descripción y estadísticas ----------------------


@dataclass(frozen=True)
class OnuState:
    pon: int | None
    onu: int
    admin_state: str
    omcc_state: str
    phase: str  # working, offline, LOS...
    serial: str | None


_STATE_ROW = re.compile(r"^\s*(GPON\d+/\d+:\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)


def parse_onu_state(text: str, default_pon: int | None = None) -> list[OnuState]:
    """Salida de ``show onu state``: administrativo, OMCC y fase de cada ONU del PON."""
    rows: list[OnuState] = []
    for line in text.splitlines():
        match = _STATE_ROW.match(line)
        if match is None:
            continue
        ref = parse_onu_ref(match.group(1), default_pon)
        if ref is None:
            continue
        rows.append(
            OnuState(
                pon=ref.pon,
                onu=ref.onu,
                admin_state=match.group(2),
                omcc_state=match.group(3),
                phase=match.group(4),
                serial=normalize_gpon_serial(match.group(5)),
            )
        )
    if not rows:
        if re.search(r"(?i)\bphase state\b", text) or not text.strip():
            return []  # tabla sin filas: PON sin ONU
        raise UnrecognizedOutput("vsol.onu.state", "ninguna fila 'GPON0/<pon>:<onu> …'", text)
    return rows


@dataclass(frozen=True)
class OnuDistance:
    onu: int | None
    meters: int


_DISTANCE = re.compile(r"(?im)^\s*onu\s+(\d+)\s+distance\s*:\s*(\d+)\s*m\b")


def parse_onu_distance(text: str) -> OnuDistance:
    """Salida de ``show onu <n> distance``: ``onu 2 Distance: 445m``."""
    match = _DISTANCE.search(text)
    if match is None:
        raise UnrecognizedOutput("vsol.onu.distance", "no hay 'onu <n> Distance: <m>m'", text)
    return OnuDistance(onu=int(match.group(1)), meters=int(match.group(2)))


@dataclass(frozen=True)
class OnuDescription:
    onu: int | None
    description: str | None


_DESCRIPTION = re.compile(r"(?im)^\s*onu\s+(\d+)\s+description\s*:[ \t]*(.*?)\s*$")


def parse_onu_description(text: str) -> OnuDescription:
    """Salida de ``show onu <n> desc``: ``onu 2 Description: <texto>`` (vacío si no tiene)."""
    match = _DESCRIPTION.search(text)
    if match is None:
        raise UnrecognizedOutput("vsol.onu.description", "no hay 'onu <n> Description:'", text)
    return OnuDescription(onu=int(match.group(1)), description=match.group(2) or None)


def parse_pon_statistics(text: str) -> dict[str, int]:
    """Salida de ``show pon <n> statistics``: tasas y contadores del puerto PON."""
    values: dict[str, int] = {}
    for key, raw in parse_key_values(text).items():
        number = parse_number(raw)
        if number is not None and re.fullmatch(r"\d+", raw.strip()):
            values[key] = int(number)
    if not values:
        raise UnrecognizedOutput("vsol.pon.statistics", "no hay contadores 'clave: número'", text)
    return values
