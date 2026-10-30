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

Aprovisionar (2026-10-29, de la ayuda ``?`` y de cómo la OLT guarda sus ONU): dentro de
``interface gpon``, ``ont add <pon> <onu> sn-auth ...`` con los perfiles de línea y de
servicio de la OLT, ``ont description`` y ``save``. Este firmware no configura WAN ni Wi-Fi de
la ONU desde la OLT. Ojo: la OLT de Tocaima tiene ``ont autoauth`` en todos los PON, así que
autoriza sola las ONU nuevas (por eso su autofind siempre está vacío). Sin verificar hasta
probarlo con una ONU de prueba.
"""

from __future__ import annotations

import dataclasses
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
from olterra.drivers.common import OnuDetail, OnuVersion
from olterra.drivers.textparse import parse_key_values
from olterra.drivers.vsol_gpon import PROMPT
from olterra.drivers.vsol_gpon.parsers import (
    AutofindRow,
    InterfacesBrief,
    OnuPort,
    OnuRow,
    OnuState,
    OpticalInfo,
    RxReading,
    VersionInfo,
)
from olterra.drivers.vsol_gpon.parsers import (
    parse_autofind as generic_autofind,
)
from olterra.drivers.vsol_gpon.parsers import (
    parse_onu_optical as vsol_optical,
)
from olterra.executor.plan import SessionProfile
from olterra.identifiers import normalize_gpon_serial

SOURCE = "OLT BCD-1508GS V2.1.6 de Tocaima (ayuda '?' y salida real, 2026-10-27)"
WRITE_SOURCE = (
    "OLT BCD-1508GS V2.1.6 de Tocaima: ayuda '?' y show current-config (2026-10-29), sin probar"
)

R, W = Access.READ, Access.WRITE
CONFIG, EXEC, PON = CliMode.CONFIG, CliMode.EXEC, CliMode.PON


def _c(key: str, template: str, mode: CliMode, **kw: Any) -> CommandTemplate:
    return CommandTemplate(
        key=key, template=template, mode=mode, access=R, source=SOURCE, verified=True, **kw
    )


def _w(key: str, template: str, mode: CliMode = PON, **kw: Any) -> CommandTemplate:
    return CommandTemplate(
        key=key, template=template, mode=mode, access=W, source=WRITE_SOURCE, **kw
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
        _c(
            "onu.detail",
            "show ont info {pon} {onu}",
            PON,
            notes="Estado, distancia, serial, descripción y perfiles",
        ),
        _c("onu.version", "show ont version {pon} {onu}", PON),
        _c("onu.optical", "show ont optical-info {pon} {onu}", PON),
        # --- Escrituras (dentro de interface gpon) ----------------------------------------
        _w(
            "onu.authorize",
            "ont add {pon} {onu} sn-auth {serial} ont-lineprofile-id {line_profile}"
            " ont-srvprofile-id {srv_profile}",
            param_types={"line_profile": "profile_id0", "srv_profile": "profile_id0"},
        ),
        _w("onu.set_description", "ont description {pon} {onu} {description}"),
        _w("onu.reboot", "ont reboot {pon} {onu}"),
        _w("onu.delete", "ont delete {pon} {onu}"),
        _w("config.save", "save", CONFIG),
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


def _blank(text: str | None) -> str | None:
    value = (text or "").strip()
    return None if value in ("", "-", "--") else value


_NOT_ONLINE = re.compile(r"(?i)does not online|not online")


def parse_onu_detail(text: str, params: Mapping[str, Any]) -> OnuDetail:
    """``show ont info <pon> <onu>``: ficha de la ONU y después sus perfiles de línea y de
    servicio (los dos con ``Profile-name``: el primero es el de línea)."""
    kv = parse_key_values(text)
    if "ontid" not in kv or "runstate" not in kv:
        raise UnrecognizedOutput("bcd.ont_info_onu", "no hay 'ONT-ID' ni 'Run state'", text)
    profiles = re.findall(r"(?im)^\s*Profile-name\s*:\s*(.*?)\s*$", text)
    serial = re.match(r"\s*([0-9A-Za-z]{12})", kv.get("sn", ""))
    distance = re.search(r"\d+", kv.get("ontdistancem", ""))
    run_state = kv["runstate"]
    return OnuDetail(
        pon=int(kv.get("portid") or params["pon"]),
        onu=int(kv["ontid"]),
        serial=normalize_gpon_serial(serial.group(1)) if serial else None,
        name=_blank(kv.get("description")),
        phase=_phase(run_state),
        run_state=run_state,
        distance_m=int(distance.group()) if distance else None,
        line_profile=_blank(profiles[0]) if profiles else None,
        service_profile=_blank(profiles[1]) if len(profiles) > 1 else None,
        last_up=_blank(kv.get("lastuptime")),
        last_down=_blank(kv.get("lastdowntime")),
        last_down_cause=None,
        uptime=_blank(kv.get("onlinetime")),
    )


def parse_onu_version(text: str) -> OnuVersion:
    """``show ont version <pon> <onu>``; "The ONT does not online" si está apagada."""
    if _NOT_ONLINE.search(text):
        return OnuVersion(online=False, vendor=None, model=None, hardware=None, software=None)
    kv = parse_key_values(text)
    vendor = _blank(kv.get("vendorid") or kv.get("vendor"))
    model = _blank(kv.get("equipmentid") or kv.get("ontmodel") or kv.get("model"))
    software = _blank(
        kv.get("mainsoftwareversion") or kv.get("softwareversion") or kv.get("ontsoftwareversion")
    )
    hardware = _blank(kv.get("ontversion") or kv.get("hardwareversion"))
    if not any((vendor, model, software, hardware)):
        raise UnrecognizedOutput("bcd.ont_version", "no se reconoció la versión", text)
    return OnuVersion(online=True, vendor=vendor, model=model, hardware=hardware, software=software)


def parse_onu_optical(text: str, params: Mapping[str, Any]) -> OpticalInfo | None:
    """``show ont optical-info <pon> <onu>``: ficha ``Clave : valor`` como la de VSOL, con la
    corriente del láser en µA. None si la ONU no está en línea."""
    if _NOT_ONLINE.search(text):
        return None
    info = vsol_optical(text)
    if info.bias_ma is not None and re.search(r"(?i)bias[^\n]*\(ua\)", text):
        info = dataclasses.replace(info, bias_ma=round(info.bias_ma / 1000, 3))
    return info


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
    "onu.detail": parse_onu_detail,
    "onu.version": lambda text, params: parse_onu_version(text),
    "onu.optical": parse_onu_optical,
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
