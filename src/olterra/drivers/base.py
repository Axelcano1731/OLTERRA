"""Piezas comunes a todos los drivers."""

from __future__ import annotations

import ipaddress
import re
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any

from olterra.executor.plan import CliCommand, SessionProfile, Step, secret_marker


class Access(StrEnum):
    READ = "read"
    WRITE = "write"


class CliMode(StrEnum):
    EXEC = "exec"  # modo privilegiado (#)
    CONFIG = "config"  # configure terminal
    PON = "pon"  # dentro de interface gpon 0/<pon>


class UnrecognizedOutput(ValueError):
    """El parser no reconoce la salida: firmware nuevo o comando distinto.

    La plataforma debe alertar (no adivinar) y guardar la salida para volverla una
    prueba del parser.
    """

    def __init__(self, parser: str, reason: str, output: str) -> None:
        super().__init__(f"{parser}: {reason}")
        self.parser = parser
        self.reason = reason
        self.sample = output[:2000]


class ParamError(ValueError):
    pass


# --- Validación de parámetros --------------------------------------------------
# Nada que venga de un usuario entra a una plantilla sin pasar por aquí: un
# "\n" en una descripción sería un comando inyectado en la OLT.


def _int_range(low: int, high: int) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if isinstance(value, bool):
            raise ParamError("se esperaba un entero")
        try:
            number = int(str(value), 10)
        except ValueError as exc:
            raise ParamError("se esperaba un entero") from exc
        if not low <= number <= high:
            raise ParamError(f"debe estar entre {low} y {high}")
        return str(number)

    return check


def _pattern(regex: str, hint: str) -> Callable[[Any], str]:
    compiled = re.compile(regex)

    def check(value: Any) -> str:
        text = str(value)
        if not compiled.fullmatch(text):
            raise ParamError(hint)
        return text

    return check


def _ipv4(value: Any) -> str:
    try:
        return str(ipaddress.IPv4Address(str(value)))
    except ValueError as exc:
        raise ParamError("se esperaba una IPv4") from exc


def _netmask(value: Any) -> str:
    try:
        network = ipaddress.IPv4Network(f"0.0.0.0/{value}")
    except ValueError as exc:
        raise ParamError("se esperaba una máscara IPv4 (255.255.255.0)") from exc
    return str(network.netmask)


PARAM_TYPES: dict[str, Callable[[Any], str]] = {
    "pon": _int_range(1, 16),
    "onu": _int_range(1, 128),
    "tcont": _int_range(1, 255),
    "gemport": _int_range(1, 255),
    "service_port": _int_range(1, 128),
    "uni": _int_range(1, 32),
    "vlan": _int_range(1, 4094),
    "profile_id": _int_range(1, 32767),
    "serial": _pattern(r"[A-Z0-9]{4}[0-9A-F]{8}", "serial GPON en forma canónica (VSOL0008D09C)"),
    "name": _pattern(r"[A-Za-z0-9_.\-]{1,32}", "nombre de 1 a 32 caracteres sin espacios"),
    "description": _pattern(r"[A-Za-z0-9_.\-]{1,64}", "descripción sin espacios ni símbolos"),
    "profile_kind": _pattern(r"onu|dba|traffic|line|srv|alarm", "tipo de perfil inválido"),
    "uni_kind": _pattern(r"eth|wifi|veip", "tipo de puerto de ONU inválido"),
    "access_service": _pattern(r"ssh|snmp|telnet|web", "servicio inválido"),
    "username": _pattern(r"[A-Za-z][A-Za-z0-9_.\-]{2,31}", "usuario de 3 a 32 caracteres"),
    "password": _pattern(r"[!-~]{8,64}", "clave de 8 a 64 caracteres imprimibles, sin espacios"),
    "community": _pattern(r"[A-Za-z0-9_.\-@#%+]{8,32}", "comunidad de 8 a 32 caracteres"),
    # Aprovisionamiento (sintaxis de la configuración de la V1600G0-B)
    "label": _pattern(r"[A-Za-z0-9_\-]{1,32}", "nombre de 1 a 32 caracteres sin espacios"),
    "cos": _int_range(0, 7),
    "mtu": _int_range(576, 1500),
    "wan": _int_range(1, 8),
    "ssid_index": _int_range(1, 8),
    "ssid": _pattern(r"[A-Za-z0-9_.\-]{1,32}", "SSID de 1 a 32 caracteres sin espacios"),
    "pppoe_user": _pattern(r"[A-Za-z0-9_.@\-]{1,64}", "usuario PPPoE sin espacios"),
    "on_off": _pattern(r"enable|disable", "enable o disable"),
    "firewall_level": _pattern(
        r"disable|low|middle|high", "nivel de firewall: disable, low, middle o high"
    ),
    "onu_account": _pattern(r"[A-Za-z0-9_.@\-]{1,32}", "usuario de la ONU sin espacios"),
    "onu_acl_service": _pattern(
        r"ping|telnet|ftp|http|https|tftp|ssh", "servicio de la ONU inválido"
    ),
    "uni_bind": _pattern(
        r"(?:lan[1-8]|ssid[1-8])(?: (?:lan[1-8]|ssid[1-8])){0,15}",
        "puertos de la ONU: lan1..lan8 y ssid1..ssid8 separados por espacio",
    ),
    "ipv4": _ipv4,
    "netmask": _netmask,
}


@dataclass(frozen=True)
class CommandTemplate:
    key: str
    template: str
    mode: CliMode
    access: Access
    source: str  # de dónde salió la sintaxis
    verified: bool = False  # validada contra una captura de laboratorio
    sensitive: bool = False  # lleva una clave: no se registra en claro
    notes: str = ""
    # placeholder -> tipo de PARAM_TYPES (por defecto, el mismo nombre)
    param_types: Mapping[str, str] = field(default_factory=dict)
    # placeholder -> campo de la credencial sellada (``new_password``...). Esa clave NUNCA entra
    # al plan: el paso lleva ``{{secret:campo}}`` y el ejecutor la pone al escribir en la OLT.
    secret_params: Mapping[str, str] = field(default_factory=dict)

    def placeholders(self) -> list[str]:
        return [name for _, name, _, _ in string.Formatter().parse(self.template) if name]

    def render(self, **values: Any) -> str:
        rendered: dict[str, str] = {}
        for name in self.placeholders():
            if name in self.secret_params:
                rendered[name] = secret_marker(self.secret_params[name])
                continue
            if name not in values:
                raise ParamError(f"{self.key}: falta el parámetro '{name}'")
            type_name = self.param_types.get(name, name)
            validator = PARAM_TYPES.get(type_name)
            if validator is None:
                raise ParamError(f"{self.key}: tipo de parámetro desconocido '{type_name}'")
            try:
                rendered[name] = validator(values[name])
            except ParamError as exc:
                raise ParamError(f"{self.key}.{name}: {exc}") from exc
        return self.template.format(**rendered)


@dataclass(frozen=True)
class CommandOverride:
    """Otra sintaxis para un comando en ciertos modelos o firmwares."""

    model: str  # patrón fnmatch, p. ej. "V1600GS*"
    firmware: str
    key: str
    template: str
    source: str
    verified: bool = False  # validada con una captura de laboratorio de ese modelo y firmware


class Capability(StrEnum):
    SNMP_ONU_STATUS = "snmp_onu_status"
    SNMP_ONU_SERIAL = "snmp_onu_serial"
    SNMP_ONU_OPTICAL = "snmp_onu_optical"
    SNMP_ONU_DISTANCE = "snmp_onu_distance"
    SNMP_ONU_TRAFFIC = "snmp_onu_traffic"
    SNMP_PON_OPTICAL = "snmp_pon_optical"
    CLI_AUTOFIND = "cli_autofind"
    CLI_AUTHORIZE = "cli_authorize"
    CLI_ONU_OPTICAL = "cli_onu_optical"
    ONU_WAN_VIA_OLT = "onu_wan_via_olt"
    ONU_WIFI_VIA_OLT = "onu_wifi_via_olt"
    SSH_LANDS_IN_SHELL = "ssh_lands_in_shell"


class Support(StrEnum):
    YES = "si"
    NO = "no"
    UNKNOWN = "desconocido"


@dataclass(frozen=True)
class CapabilityFact:
    support: Support
    source: str


@dataclass(frozen=True)
class CapabilityRule:
    model: str
    firmware: str
    facts: Mapping[Capability, CapabilityFact]


def _model_key(text: str) -> str:
    # VSOL escribe "V1600G1-B" en la web de la OLT y "V1600G1B" en otros lados: es el mismo.
    return re.sub(r"[\s_-]", "", text).upper()


def _matches(pattern: str, value: str | None) -> bool:
    return fnmatchcase(_model_key(value or ""), _model_key(pattern))


@dataclass(frozen=True)
class CommandCall:
    key: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Driver:
    key: str
    vendor: str
    family: str
    session: SessionProfile
    commands: Mapping[str, CommandTemplate]
    overrides: Sequence[CommandOverride] = ()
    # En orden: de lo general a lo específico; la última regla que calza manda.
    capability_rules: Sequence[CapabilityRule] = ()
    enter_config: str = "configure terminal"
    enter_pon: str = "interface gpon 0/{pon}"
    leave_to_exec: str = "end"
    leave_one_level: str = "exit"

    def command(
        self, key: str, model: str | None = None, firmware: str | None = None
    ) -> CommandTemplate:
        base = self.commands[key]
        for override in self.overrides:
            if (
                override.key == key
                and _matches(override.model, model)
                and _matches(override.firmware, firmware)
            ):
                base = CommandTemplate(
                    key=base.key,
                    template=override.template,
                    mode=base.mode,
                    access=base.access,
                    source=override.source,
                    verified=override.verified,
                    sensitive=base.sensitive,
                    notes=base.notes,
                    param_types=base.param_types,
                    secret_params=base.secret_params,
                )
        return base

    def capabilities(
        self, model: str | None, firmware: str | None
    ) -> dict[Capability, CapabilityFact]:
        resolved: dict[Capability, CapabilityFact] = {
            cap: CapabilityFact(Support.UNKNOWN, "sin datos") for cap in Capability
        }
        for rule in self.capability_rules:
            if _matches(rule.model, model) and _matches(rule.firmware, firmware):
                resolved.update(rule.facts)
        return resolved

    def supports(self, capability: Capability, model: str | None, firmware: str | None) -> Support:
        return self.capabilities(model, firmware)[capability].support

    def read_only_catalog(self) -> list[CommandTemplate]:
        return [c for c in self.commands.values() if c.access is Access.READ]

    def build_cli_steps(
        self,
        calls: Sequence[CommandCall],
        *,
        model: str | None = None,
        firmware: str | None = None,
        timeout_s: float = 30.0,
    ) -> list[Step]:
        """Convierte llamadas a comandos en pasos CLI, con los cambios de modo necesarios.

        Agrupa por contexto para no entrar y salir de ``interface gpon`` en cada
        comando, y siempre termina en modo privilegiado.
        """
        steps: list[Step] = []
        context: tuple[CliMode, int | None] = (CliMode.EXEC, None)

        def emit(command: str) -> None:
            steps.append(CliCommand(command=command, timeout_s=10))

        def switch(target: tuple[CliMode, int | None]) -> None:
            nonlocal context
            if target == context:
                return
            current, (mode, pon) = context[0], target
            if mode is CliMode.EXEC:
                emit(self.leave_to_exec)
            elif mode is CliMode.CONFIG:
                emit(self.enter_config if current is CliMode.EXEC else self.leave_one_level)
            else:  # PON
                if current is CliMode.EXEC:
                    emit(self.enter_config)
                elif current is CliMode.PON:
                    emit(self.leave_one_level)
                emit(self.enter_pon.format(pon=pon))
            context = target

        for call in calls:
            template = self.command(call.key, model, firmware)
            pon: int | None = None
            if template.mode is CliMode.PON:
                pon = int(PARAM_TYPES["pon"](call.params.get("pon")))
            switch((template.mode, pon))
            steps.append(
                CliCommand(
                    command=template.render(**call.params),
                    timeout_s=timeout_s,
                    sensitive=template.sensitive,
                )
            )
        switch((CliMode.EXEC, None))
        return steps
