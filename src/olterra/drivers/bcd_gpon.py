"""Driver de OLT BCD GPON (BCD-1508GS, firmware V2.1.6_181108), CLI estilo C-Data.

Todo salió de la OLT de Tocaima (172.16.0.2) el 2026-10-27: la ayuda ``?`` de cada modo y
las salidas reales. ``show device`` dice "BCD-1508GS" y "GPON OLT" de fabricante; la CLI
(``ont`` con perfiles de línea y servicio, ``>>User name:``) es la de C-Data. Solo
lectura: aprovisionar todavía no existe para esta marca.

- Configuración con ``config``; la interfaz GPON es una sola (``interface gpon``, sin
  número) y el PON va como parámetro de cada comando.
- No tiene ``terminal length``: la paginación (``--More ( Press 'Q' to quit )--``) la
  atiende el ejecutor.
- El modelo sale de ``show device`` y el firmware de ``show version``.
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
from olterra.drivers.vsol_gpon.parsers import (
    parse_autofind as generic_autofind,
)
from olterra.executor.plan import SessionProfile
from olterra.identifiers import normalize_gpon_serial

SOURCE = "OLT BCD-1508GS V2.1.6 de Tocaima (ayuda '?' y salida real, 2026-10-27)"

R = Access.READ
CONFIG, EXEC, PON = CliMode.CONFIG, CliMode.EXEC, CliMode.PON


def _c(key: str, template: str, mode: CliMode, **kw: Any) -> CommandTemplate:
    return CommandTemplate(
        key=key, template=template, mode=mode, access=R, source=SOURCE, verified=True, **kw
    )


COMMANDS: dict[str, CommandTemplate] = {
    c.key: c
    for c in (
        _c("system.version", "show version", EXEC, notes="Solo el firmware"),
        _c("system.device", "show device", EXEC, notes="Modelo y serial del equipo"),
        _c("system.running_config", "show current-config", CONFIG),
        _c(
            "interfaces.brief",
            "show ont info all",
            CONFIG,
            notes="Todas las ONU de todos los PON, con su estado y descripción",
        ),
        _c("onu.list", "show ont info {pon} all", CONFIG),
        _c("onu.state", "show ont info {pon} all", CONFIG),
        _c("onu.rx_power_all", "show ont optical-info {pon} all", PON),
        _c("onu.autofind", "show ont autofind {pon}", CONFIG),
    )
}

# "Error: The automatically found ONTs does not exist." no es una falla: es un PON sin
# ONU nuevas. El resto de "Error:" sí.
ERRORS = (
    r"(?i)^\s*(?:unknown command|command incomplete|too many parameters|ambiguous command)",
    r"(?i)^\s*error:(?!.*automatically found)",
)

SESSION = SessionProfile(
    prompt_pattern=PROMPT,  # "OLT>", "OLT#", "OLT(config)#", "OLT(config-interface-gpon)#"
    pager_pattern=r"(?i)-{2,}\s*more\b.*-{2,}|press any key|\(q to quit\)",
    pager_response=" ",
    enable_command="enable",
    enable_password_pattern=r"(?i)password:\s*$",  # noqa: S106 - texto del prompt
    error_patterns=ERRORS,
)

# --- Parsers ----------------------------------------------------------------------------------

_STATE_ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([0-9A-Za-z]{12})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$"
)
_DESC_ROW = re.compile(r"^\s*(\d+)\s+(\d+)(?:\s+(.*?))?\s*$")
_OPTICAL_ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$"
)
_NOTHING = re.compile(r"(?i)do(?:es)? not exist")


def _ont_info(text: str) -> list[dict[str, Any]]:
    """``show ont info``: una tabla de estado y, aparte, una de descripciones."""
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    in_descriptions = False
    for line in text.splitlines():
        if re.search(r"(?i)\bdescription\b", line):
            in_descriptions = True
            continue
        if not in_descriptions:
            match = _STATE_ROW.match(line)
            if match:
                pon, onu = int(match.group(1)), int(match.group(2))
                rows[(pon, onu)] = {
                    "pon": pon,
                    "onu": onu,
                    "serial": match.group(3),
                    "control": match.group(4),
                    "run": match.group(5),
                    "config": match.group(6),
                    "match": match.group(7),
                    "description": None,
                }
        else:
            match = _DESC_ROW.match(line)
            if match and (int(match.group(1)), int(match.group(2))) in rows:
                rows[(int(match.group(1)), int(match.group(2)))]["description"] = (
                    match.group(3) or None
                )
    # Sin filas: vale si vino el encabezado (un PON sin ONU) o "does not exist"; si no, no se
    # reconoce la salida.
    if not rows and text.strip() and not (_NOTHING.search(text) or re.search(r"\bSN\b", text)):
        raise UnrecognizedOutput("bcd.ont_info", "no se reconoció ninguna ONU", text)
    return list(rows.values())


def _phase(run_state: str) -> str:
    return "working" if run_state.lower() == "online" else "offline"


def parse_onu_state(text: str, default_pon: int | None = None) -> list[OnuState]:
    return [
        OnuState(
            pon=row["pon"],
            onu=row["onu"],
            admin_state="enable" if row["control"].lower() == "active" else "disable",
            omcc_state="enable" if row["run"].lower() == "online" else "disable",
            phase=_phase(row["run"]),
            serial=normalize_gpon_serial(row["serial"]),
        )
        for row in _ont_info(text)
    ]


def parse_onu_list(text: str, default_pon: int | None = None) -> list[OnuRow]:
    return [
        OnuRow(
            pon=row["pon"],
            onu=row["onu"],
            serial=normalize_gpon_serial(row["serial"]),
            state=_phase(row["run"]),
            description=row["description"],
        )
        for row in _ont_info(text)
    ]


def parse_interfaces_brief(text: str) -> InterfacesBrief:
    rows = _ont_info(text)
    return InterfacesBrief(
        pons=sorted({row["pon"] for row in rows}),
        onus=[
            OnuPort(
                pon=row["pon"],
                onu=row["onu"],
                description=row["description"],
                up=row["run"].lower() == "online",
            )
            for row in rows
        ],
    )


def parse_rx_power(text: str, default_pon: int | None = None) -> list[RxReading]:
    """``show ont optical-info <pon> all``: P, ONT ID, voltaje, TX, RX, bias, temperatura."""
    readings = []
    for line in text.splitlines():
        match = _OPTICAL_ROW.match(line)
        if match:
            rx = float(match.group(5))
            readings.append(
                RxReading(int(match.group(1)), int(match.group(2)), None if rx == 0 else rx)
            )
    if not readings and text.strip() and not re.search(r"(?i)optical|do(?:es)? not exist", text):
        raise UnrecognizedOutput("bcd.ont_optical", "no se reconoció ninguna fila", text)
    return readings


def parse_autofind(text: str, default_pon: int | None = None) -> list[AutofindRow]:
    if _NOTHING.search(text) or not text.strip():
        return []
    return generic_autofind(text, default_pon)


def parse_version(text: str) -> VersionInfo:
    """``show version`` (firmware) o ``show device`` (modelo)."""
    kv = parse_key_values(text)
    model = kv.get("devicemodel")
    firmware = kv.get("firmwareversion")
    if model is None and firmware is None:
        raise UnrecognizedOutput("bcd.version", "no hay 'Device model' ni 'Firmware version'", text)
    return VersionInfo(model=model, firmware=firmware, hardware=kv.get("hardwareversion"))


def _pon(params: Mapping[str, Any]) -> int | None:
    value = params.get("pon")
    return int(value) if value is not None else None


def pon_count(model: str | None) -> int | None:
    """BCD-1508GS → 8, BCD-1504GS → 4: ``show ont info all`` no muestra los PON vacíos."""
    match = re.search(r"-\d{2}(\d{2})G", model or "")
    return int(match.group(1)) if match else None


PARSERS: dict[str, Parser] = {
    "system.version": lambda text, params: parse_version(text),
    "system.device": lambda text, params: parse_version(text),
    "interfaces.brief": lambda text, params: parse_interfaces_brief(text),
    "onu.list": lambda text, params: parse_onu_list(text, _pon(params)),
    "onu.state": lambda text, params: parse_onu_state(text, _pon(params)),
    "onu.rx_power_all": lambda text, params: parse_rx_power(text, _pon(params)),
    "onu.autofind": lambda text, params: parse_autofind(text, _pon(params)),
}

BCD_GPON = Driver(
    key="bcd-gpon",
    vendor="BCD",
    family="GPON",
    session=SESSION,
    commands=COMMANDS,
    parsers=PARSERS,
    label="BCD GPON (CLI tipo C-Data)",
    pon_count=pon_count,
    enter_config="config",
    enter_pon="interface gpon",
)
