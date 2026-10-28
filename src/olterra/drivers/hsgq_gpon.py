"""Driver de OLT HSGQ GPON (HSGQ-G008, software V1.3.12C).

Todo salió de la OLT de Tocaima (172.17.1.2) el 2026-10-27: la ayuda ``?`` de cada modo y
las salidas reales de cada comando. Solo lectura: aprovisionar en HSGQ todavía no existe.

Dos cosas de esta marca:

- Todo se consulta en modo ``configure`` (no ``configure terminal``), con el PON como
  parámetro: no hace falta entrar a la interfaz.
- **Numera las ONU desde 0** (``1/0``). Olterra las cuenta desde 1 (la base reserva el 0
  para "todo el PON" en las alarmas), así que la ONU ``1/0`` de la OLT es la ONU 1 en
  Olterra, la ``1/1`` es la 2, etc. Los parsers suman 1; si algún día hay comandos por ONU,
  la plantilla tiene que restarlo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from olterra.drivers.base import (
    Access,
    CliMode,
    CommandTemplate,
    Driver,
    Parser,
    UnrecognizedOutput,
)
from olterra.drivers.textparse import parse_key_values
from olterra.drivers.vsol_gpon import PROMPT
from olterra.drivers.vsol_gpon.parsers import (
    AutofindRow,
    InterfacesBrief,
    OnuPort,
    OnuRow,
    OnuState,
    RxReading,
    VersionInfo,
)
from olterra.executor.plan import SessionProfile
from olterra.identifiers import normalize_gpon_serial

SOURCE = "OLT HSGQ-G008 V1.3.12C de Tocaima (ayuda '?' y salida real, 2026-10-27)"
ONU_OFFSET = 1  # ONU de la OLT (desde 0) + 1 = ONU en Olterra

R = Access.READ
CONFIG, EXEC = CliMode.CONFIG, CliMode.EXEC


def _c(key: str, template: str, mode: CliMode, **kw: Any) -> CommandTemplate:
    return CommandTemplate(
        key=key, template=template, mode=mode, access=R, source=SOURCE, verified=True, **kw
    )


COMMANDS: dict[str, CommandTemplate] = {
    c.key: c
    for c in (
        _c("system.version", "show version", EXEC),
        _c("system.running_config", "show running-config", CONFIG),
        _c(
            "interfaces.brief",
            "show ont-info all",
            CONFIG,
            notes="Todas las ONU de todos los PON, con su estado y nombre",
        ),
        _c("onu.list", "show ont-info {pon} all", CONFIG),
        _c("onu.state", "show ont-info {pon} all", CONFIG),
        _c("onu.rx_power_all", "show ont-optical gpon {pon}", CONFIG),
        _c("onu.autofind", "show ont-autofind {pon}", CONFIG),
    )
}

# Errores: la CLI los antepone con "vty%" ("vty% Command incomplete.",
# "vty% [VTY] vty[node:6],Unknown command: …").
ERRORS = (
    r"(?i)^\s*vty%",
    r"(?i)^\s*%?\s*(?:unknown command|command incomplete|too many parameters|ambiguous command)",
)

SESSION = SessionProfile(
    prompt_pattern=PROMPT,  # "OLT_3>", "OLT_3#", "OLT_3(config)#"
    pager_pattern=r"(?i)-{2,}\s*more\b.*-{2,}|press any key|\(q to quit\)",
    pager_response=" ",
    enable_command="enable",
    enable_password_pattern=r"(?i)password:\s*$",  # noqa: S106 - texto del prompt
    setup_commands=("terminal length 0",),
    error_patterns=ERRORS,
)

# --- Parsers ----------------------------------------------------------------------------------

_ONT_INFO_COLUMNS = ("PON/ONU", "Serial", "State", "Run", "Config", "Match", "Last", "ONT")
_OPTICAL_ROW = re.compile(
    r"^\s*(\d+)/(\d+)\s+(\S+)\s+(-?[\d.]+)\s*C\s+(-?[\d.]+)\s*V\s+(-?[\d.]+)\s*mA\s+"
    r"(-?[\d.]+)\s*dBm\s+(-?[\d.]+)\s*dBm(?:\s+(.*?))?\s*$"
)
_NOTHING = re.compile(r"(?i)do(?:es)? not exist")


def _phase(run_state: str, reason: str) -> str:
    """Fase como la entiende el monitoreo: working, offline, los o dyinggasp."""
    if run_state.lower() == "online":
        return "working"
    text = reason.lower().replace(" ", "").replace("-", "")
    if "dying" in text:
        return "dyinggasp"
    if "los" in text or "lofi" in text:
        return "los"
    return "offline"


def _ont_info_rows(text: str) -> list[dict[str, Any]]:
    """Filas de ``show ont-info``: columnas por posición del encabezado (el nombre lleva espacios)."""
    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("PON/ONU")), None
    )
    if header_index is None:
        if _NOTHING.search(text) or not text.strip():
            return []
        raise UnrecognizedOutput("hsgq.ont_info", "no hay encabezado 'PON/ONU'", text)
    header = lines[header_index]
    starts = [header.find(name) for name in _ONT_INFO_COLUMNS]
    if min(starts) < 0:
        raise UnrecognizedOutput(
            "hsgq.ont_info", "el encabezado no trae las columnas esperadas", text
        )
    rows = []
    for line in lines[header_index + 1 :]:
        match = re.match(r"^\s*(\d+)/(\d+)\s", line)
        if match is None:
            continue
        cells = [
            line[start : (starts[i + 1] if i + 1 < len(starts) else None)].strip()
            for i, start in enumerate(starts)
        ]
        rows.append(
            {
                "pon": int(match.group(1)),
                "onu": int(match.group(2)) + ONU_OFFSET,
                "serial": cells[1],
                "admin": cells[2],
                "run": cells[3],
                "config": cells[4],
                "match": cells[5],
                "reason": cells[6],
                "name": cells[7] or None,
            }
        )
    return rows  # con encabezado y sin filas: un PON sin ONU


def parse_onu_state(text: str, default_pon: int | None = None) -> list[OnuState]:
    return [
        OnuState(
            pon=row["pon"],
            onu=row["onu"],
            admin_state="enable" if row["admin"].lower() == "active" else "disable",
            omcc_state="enable" if row["run"].lower() == "online" else "disable",
            phase=_phase(row["run"], row["reason"]),
            serial=normalize_gpon_serial(row["serial"]),
        )
        for row in _ont_info_rows(text)
    ]


def parse_onu_list(text: str, default_pon: int | None = None) -> list[OnuRow]:
    return [
        OnuRow(
            pon=row["pon"],
            onu=row["onu"],
            serial=normalize_gpon_serial(row["serial"]),
            state=_phase(row["run"], row["reason"]),
            description=row["name"],
        )
        for row in _ont_info_rows(text)
    ]


def parse_interfaces_brief(text: str) -> InterfacesBrief:
    """Con ``show ont-info all``: las ONU de todos los PON (los PON sin ONU no aparecen)."""
    rows = _ont_info_rows(text)
    onus = [
        OnuPort(
            pon=row["pon"],
            onu=row["onu"],
            description=row["name"],
            up=row["run"].lower() == "online",
        )
        for row in rows
    ]
    return InterfacesBrief(pons=sorted({row["pon"] for row in rows}), onus=onus)


def parse_rx_power(text: str, default_pon: int | None = None) -> list[RxReading]:
    readings = []
    for line in text.splitlines():
        match = _OPTICAL_ROW.match(line)
        if match is None:
            continue
        rx = float(match.group(8))
        readings.append(
            RxReading(
                int(match.group(1)), int(match.group(2)) + ONU_OFFSET, None if rx == 0 else rx
            )
        )
    if not readings and re.search(r"\d+/\d+\s+GPON", text):
        raise UnrecognizedOutput("hsgq.ont_optical", "hay ONU pero no se leyó su potencia", text)
    return readings


def parse_autofind(text: str, default_pon: int | None = None) -> list[AutofindRow]:
    if _NOTHING.search(text) or not text.strip():
        return []
    rows = []
    for line in text.splitlines():
        match = re.match(
            r"^\s*(?:(\d+)/(\d+)\s+|\d+\s+)?.*?\b([A-Za-z0-9]{4}[0-9A-Fa-f]{8})\b", line
        )
        serial = normalize_gpon_serial(match.group(3)) if match else None
        if serial is not None:
            pon = int(match.group(1)) if match and match.group(1) else default_pon
            rows.append(AutofindRow(pon=pon, serial=serial))
    if not rows:
        raise UnrecognizedOutput("hsgq.ont_autofind", "no se reconoció ningún serial", text)
    return rows


def parse_version(text: str) -> VersionInfo:
    kv = parse_key_values(text)
    model = kv.get("product")
    firmware = kv.get("softwareversion")
    if model is None and firmware is None:
        raise UnrecognizedOutput("hsgq.version", "no hay 'Product' ni 'Software Version'", text)
    return VersionInfo(model=model, firmware=firmware, hardware=kv.get("hardwareversion"))


def _pon(params: Mapping[str, Any]) -> int | None:
    value = params.get("pon")
    return int(value) if value is not None else None


def pon_count(model: str | None) -> int | None:
    """HSGQ-G008 → 8, HSGQ-G004 → 4: ``show ont-info all`` no muestra los PON vacíos."""
    match = re.search(r"G0*(\d{1,2})\b", model or "")
    return int(match.group(1)) if match else None


PARSERS: dict[str, Parser] = {
    "system.version": lambda text, params: parse_version(text),
    "interfaces.brief": lambda text, params: parse_interfaces_brief(text),
    "onu.list": lambda text, params: parse_onu_list(text, _pon(params)),
    "onu.state": lambda text, params: parse_onu_state(text, _pon(params)),
    "onu.rx_power_all": lambda text, params: parse_rx_power(text, _pon(params)),
    "onu.autofind": lambda text, params: parse_autofind(text, _pon(params)),
}

HSGQ_GPON = Driver(
    key="hsgq-gpon",
    vendor="HSGQ",
    family="GPON",
    session=SESSION,
    commands=COMMANDS,
    parsers=PARSERS,
    label="HSGQ GPON",
    pon_count=pon_count,
    enter_config="configure",
)
