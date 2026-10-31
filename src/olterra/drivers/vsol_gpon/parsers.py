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
from olterra.drivers.common import MacEntry, OnuTraffic, mac_of, parse_mac_rows
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


# Lo que la OLT escribe cuando la ONU todavía no le dijo su modelo. En el autofind de la
# V1600G0-B salió "NULL" (2026-10-08): mandarlo como "pri equid NULL" dejó "pri equid MONUVPRI"
# en una V824 y la ONU no tomó la WAN.
_NO_MODEL = {"null", "n/a", "na", "none", "unknown", "-", "--", "0"}


def real_equipment_id(value: str | None) -> str | None:
    """El Equipment ID si es uno de verdad (VSOLV422); None si la OLT no lo sabe."""
    if value is None:
        return None
    value = value.strip()
    return None if not value or value.lower() in _NO_MODEL else value


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


# V1600G0-B V1.4.8R: "Index<TAB>Sn   Equipment ID" y filas "1<TAB>sn:GPON005c9160<TAB><TAB>VSOLV422".
# Tabulaciones y el prefijo "sn:" (con el hexadecimal en minúscula): no es una tabla de columnas.
_AUTOFIND_G0B_HEADER = re.compile(r"(?im)^\s*index\s+sn\s+equipment\s+id\s*$")
_AUTOFIND_G0B_ROW = re.compile(r"^\s*(\d+)\s+sn:(\S+)(?:\s+(\S+))?\s*$")


def _parse_autofind_g0b(text: str, default_pon: int | None) -> list[AutofindRow]:
    rows = []
    for line in text.splitlines():
        match = _AUTOFIND_G0B_ROW.match(line)
        if match is None:
            continue
        serial = normalize_gpon_serial(match.group(2))
        if serial is None:
            raise UnrecognizedOutput(
                "vsol.onu.autofind", f"serial inválido: {match.group(2)}", text
            )
        rows.append(
            AutofindRow(
                pon=default_pon,
                serial=serial,
                # Es el orden en la lista de autofind, no un índice de ONU: no se usa como tal.
                index=None,
                model=real_equipment_id(match.group(3)),
                raw={
                    "index": match.group(1),
                    "sn": match.group(2),
                    "equipment_id": match.group(3) or "",
                },
            )
        )
    return rows


def parse_autofind(text: str, default_pon: int | None = None) -> list[AutofindRow]:
    """Salida de ``show onu auto-find``: ONUs conectadas sin autorizar."""
    if _AUTOFIND_G0B_HEADER.search(text):
        return _parse_autofind_g0b(text, default_pon)
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
                    model=real_equipment_id(_cell(row, table.column(*MODEL_COLUMNS))),
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
                rx = parse_dbm(row.get(rx_col))
                # La OLT pone 0.00 cuando la ONU no está conectada: no es una lectura (0 dBm
                # sería saturación y dispararía una alarma de señal alta falsa).
                result.append(RxReading(ref.pon, ref.onu, None if rx == 0 else rx))
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


# "GPON0/1:3" (V1600G0-B V1.4.8R) o "1/1/1:3" (OLT de Tocaima, V1.1.9R).
_STATE_ROW = re.compile(
    r"^\s*(GPON\d+/\d+:\d+|\d+/\d+/\d+:\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE
)


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
    # La OLT de Tocaima (V1.1.9R) acepta el comando y no devuelve nada: sin contadores.
    if not values and text.strip():
        raise UnrecognizedOutput("vsol.pon.statistics", "no hay contadores 'clave: número'", text)
    return values


@dataclass(frozen=True)
class OnuPort:
    pon: int
    onu: int
    description: str | None
    up: bool


@dataclass(frozen=True)
class InterfacesBrief:
    pons: list[int]
    onus: list[OnuPort]


_BRIEF_PON = re.compile(r"^\s*GPON\d+/(\d+)\s+(?:up|down)\b", re.IGNORECASE)
# La descripción larga empuja la columna de estado ("JUAN_PEREZ_GOMEZup"): se lee desde el final.
_BRIEF_ONU = re.compile(r"^\s*GPON\d+/(\d+):(\d+)\s+(.*?)\s*(up|down)\s+GPON-ONUID\s*$")


def parse_interfaces_brief(text: str) -> InterfacesBrief:
    """``show interface brief``: los PON del equipo y cada ONU con su descripción y si está arriba."""
    pons: list[int] = []
    onus: list[OnuPort] = []
    for line in text.splitlines():
        onu = _BRIEF_ONU.match(line)
        if onu is not None:
            onus.append(
                OnuPort(
                    pon=int(onu.group(1)),
                    onu=int(onu.group(2)),
                    description=onu.group(3).strip() or None,
                    up=onu.group(4) == "up",
                )
            )
            continue
        pon = _BRIEF_PON.match(line)
        if pon is not None and "GPON-ONUID" not in line:
            pons.append(int(pon.group(1)))
    if not pons and not onus:
        raise UnrecognizedOutput("vsol.interfaces.brief", "no hay puertos GPON", text)
    return InterfacesBrief(pons=sorted(set(pons)), onus=onus)


@dataclass(frozen=True)
class OnuCapability:
    ethernet_ports: int | None
    wifi_ports: int | None
    onu_type: str | None


def parse_onu_capability(text: str) -> OnuCapability:
    """``show onu <n> capability``: puertos LAN y WiFi que tiene la ONU (para la WAN)."""
    kv = parse_key_values(text)

    def number(*needles: str) -> int | None:
        raw = _kv_find(kv, *needles)
        value = parse_number(raw) if raw is not None else None
        return int(value) if value is not None else None

    capability = OnuCapability(
        ethernet_ports=number("ethernet", "uni"),
        wifi_ports=number("wifi", "uni"),
        onu_type=_kv_find(kv, "onu", "type"),
    )
    if capability.ethernet_ports is None and capability.wifi_ports is None:
        raise UnrecognizedOutput("vsol.onu.capability", "no hay 'Ethernet UNI number'", text)
    return capability


_EQUIPMENT_ID = re.compile(r"(?im)^\s*equipment\s*id\s*:\s*(\S+)")


def parse_equipment_id(text: str) -> str | None:
    """El ``Equipment ID`` de ``show onu detail-info <n>`` (VSOLV422), o None si no está."""
    match = _EQUIPMENT_ID.search(text)
    return real_equipment_id(match.group(1)) if match else None


_VSOL_ONU_REF = re.compile(r"GPON\s*\d+/(\d+):0*(\d+)", re.IGNORECASE)


def parse_mac_table(text: str, default_pon: int | None = None) -> list[MacEntry]:
    """``show mac address-table interface gpon 0/<pon>`` y ``... pon <pon> <onu>``.

    La de interfaz trae ``Vlan Mac Tipo Puerto`` (en la V1.1.9R el puerto dice solo "PON", sin
    la ONU); la de ``pon`` trae ``Mac Vlan Tipo GPON0/<pon>:<onu> ...``.
    """

    def row(line: str, match: re.Match[str]) -> MacEntry | None:
        ref = _VSOL_ONU_REF.search(line)
        before = line[: match.start()].split()
        after = line[match.end() :].split()
        vlan_text = before[-1] if before and before[-1].isdigit() else (after[0] if after else "")
        return MacEntry(
            mac=mac_of(match),
            vlan=int(vlan_text) if vlan_text.isdigit() else None,
            pon=int(ref.group(1)) if ref else default_pon,
            onu=int(ref.group(2)) if ref else None,
            kind=next((w.lower() for w in after if w.lower() in ("dynamic", "static")), None),
        )

    return parse_mac_rows(text, row)


def parse_onu_mac(text: str, pon: int | None, onu: int | None) -> list[MacEntry]:
    """Las MAC de una ONU: la tabla por PON filtrada (la OLT puede listar el PON entero)."""
    return [e for e in parse_mac_table(text, pon) if onu is None or e.onu in (None, onu)]


# --- Consumo, WAN y WiFi de una ONU (OLT de Tocaima V1.1.9R, 2026-10-31) ---------------------

_TRAFFIC_ONU = re.compile(r"GPON\s*\d+/(\d+):0*(\d+)", re.IGNORECASE)
_TRAFFIC_BYTES = re.compile(r"(?i)^\s*(input|output)\s+bytes\s*:\s*(\d+)\s*$")


def parse_onu_traffic(text: str, default_pon: int | None = None) -> list[OnuTraffic]:
    """``show onu <lista> statistics``: un bloque por ONU que empieza con ``GPON0/1:3``.

    ``Input`` es lo que la OLT recibió de la ONU (la subida del cliente) y ``Output`` lo que le
    mandó (la bajada). El paginador puede dejar ``--More--`` pegado a la línea del bloque.
    """
    rows: list[OnuTraffic] = []
    current: tuple[int, int] | None = None
    counters: dict[str, int] = {}

    def close() -> None:
        if current is not None and {"input", "output"} <= counters.keys():
            rows.append(OnuTraffic(current[0], current[1], counters["output"], counters["input"]))

    for line in text.splitlines():
        ref = _TRAFFIC_ONU.search(line)
        if ref is not None:
            close()
            current, counters = (int(ref.group(1)), int(ref.group(2))), {}
            continue
        match = _TRAFFIC_BYTES.match(line)
        if match is not None and current is not None:
            counters[match.group(1).lower()] = int(match.group(2))
    close()
    if not rows and re.search(r"(?i)\bbytes\s*:", text):
        raise UnrecognizedOutput("vsol.onu.traffic", "hay contadores pero ninguna ONU", text)
    return rows


@dataclass(frozen=True)
class WanStatus:
    """Una WAN como la reporta la ONU (``show onu <n> pri wan_conn``). Nunca trae la clave."""

    index: int
    mode: str | None  # tr069_internet, internet…
    connection: str | None  # PPPOE, DHCP, STATIC
    kind: str | None  # route o bridge
    vlan: int | None
    nat: bool | None
    pppoe_user: str | None
    status: str | None  # connected, disconnected…
    mtu: int | None
    ip: str | None


_KV_LINE = re.compile(r"^\s*([A-Za-z][\w ]*?)\s*:\s*(.*?)\s*$")


def _blocks(text: str, first_key: str) -> list[dict[str, str]]:
    """Bloques ``clave : valor`` que empiezan cada vez que aparece ``first_key``."""
    blocks: list[dict[str, str]] = []
    for line in text.splitlines():
        match = _KV_LINE.match(line)
        if match is None:
            continue
        key, value = norm_header(match.group(1)), match.group(2)
        if key == first_key:
            blocks.append({})
        if blocks and value:
            blocks[-1].setdefault(key, value)
    return blocks


def _int(value: str | None) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def parse_wan_status(text: str) -> list[WanStatus]:
    """``show onu <n> pri wan_conn``: ``wanNumber:1`` y un bloque ``wanIndex : 1`` por WAN."""
    wans = []
    for block in _blocks(text, "wanindex"):
        nat = block.get("wannatenable")
        wans.append(
            WanStatus(
                index=_int(block.get("wanindex")) or 0,
                mode=block.get("wanmode"),
                connection=block.get("wanconnmode"),
                kind=block.get("wanconntype"),
                vlan=_int(block.get("wanvlanid")),
                nat=None if nat is None else nat.lower() == "enable",
                pppoe_user=block.get("pppoeusername"),
                status=block.get("wanstatus"),
                mtu=_int(block.get("wanmtu")),
                ip=_kv_find(block, "ip", exclude=("mode", "proto", "version", "ipv6")),
            )
        )
    if not wans and not re.search(r"(?i)wannumber\s*:\s*0\b", text):
        raise UnrecognizedOutput("vsol.onu.wan_status", "no hay 'wanNumber' ni 'wanIndex'", text)
    return wans


@dataclass(frozen=True)
class WifiStatus:
    """Un SSID como lo reporta la ONU (``show onu <n> pri wifi_ssid <i>``). Sin la clave."""

    index: int | None
    name: str | None
    enabled: bool | None
    hidden: bool | None
    auth: str | None
    encrypt: str | None
    rekey_s: int | None


def parse_wifi_status(text: str) -> WifiStatus:
    kv = parse_key_values(text)
    if "name" not in kv and "authmode" not in kv:
        raise UnrecognizedOutput("vsol.onu.wifi_status", "no hay 'Name' ni 'Auth Mode'", text)

    def flag(key: str) -> bool | None:
        value = kv.get(key)
        return None if value is None else value.lower() == "enable"

    return WifiStatus(
        index=_int(kv.get("ssid")),
        name=kv.get("name"),
        enabled=flag("status"),
        hidden=flag("hide"),
        auth=kv.get("authmode"),
        encrypt=kv.get("encrypttype"),
        rekey_s=_int(kv.get("wparekeyinterval")),
    )


@dataclass(frozen=True)
class PppoeWan:
    """Una WAN PPPoE que guarda la OLT, con lo necesario para reescribir usuario y clave.

    ``route`` son sus opciones tal cual (``tr069_internet nat enable pppoe proxy disable``) y
    ``tail`` lo que va después de la clave (``mode auto``): ``onu.pppoe_change`` las repite.
    """

    kind: str  # wan_conn (Tocaima) o wan_adv (V1600G0-B)
    index: int
    route: str
    tail: str
    pppoe_user: str
    vlan: int | None


_PPPOE_LINE = re.compile(
    r"(?im)^\s*onu\s+\d+\s+pri\s+(wan_conn|wan_adv)\s+index\s+(\d+)\s+route\s+(.+?)\s+"
    r"user\s+(\S+)\s+pwd\s+\S+\s+(mode\s+\S+(?:\s+nat\s+\S+)?)\s*$"
)
_WAN_VLAN = re.compile(
    r"(?im)^\s*onu\s+\d+\s+pri\s+(wan_conn|wan_adv)\s+index\s+(\d+)\s+vlan\s+"
    r"(?:enable\s+vid|tag\s+wan_vlan)\s+(\d+)"
)


def parse_wan_config(text: str) -> list[PppoeWan]:
    """Las WAN PPPoE de ``show running-config onu <n>``; vacío si la OLT no guarda ninguna (la
    WAN se configuró en la propia ONU)."""
    vlans = {(m.group(1), int(m.group(2))): int(m.group(3)) for m in _WAN_VLAN.finditer(text)}
    return [
        PppoeWan(
            kind=match.group(1),
            index=int(match.group(2)),
            route=" ".join(match.group(3).split()),
            tail=" ".join(match.group(5).split()),
            pppoe_user=match.group(4),
            vlan=vlans.get((match.group(1), int(match.group(2)))),
        )
        for match in _PPPOE_LINE.finditer(text)
    ]
