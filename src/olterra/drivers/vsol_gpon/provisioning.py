"""Aprovisionamiento de ONU en VSOL GPON: plantillas y la receta de comandos de un alta.

Una plantilla guarda lo que es igual para todos los clientes de un plan (perfiles, T-CONT,
GEM, VLAN, modo de la WAN, si lleva WiFi). Lo de cada cliente (serial, nombre, usuario y clave
PPPoE, SSID y clave WiFi) llega en cada alta. Las claves nunca entran a los comandos: van en
la credencial sellada y los pasos llevan ``{{secret:pppoe_password}}`` / ``{{secret:wifi_key}}``.

``parse_onu_running_config`` arma una plantilla desde ``show running-config onu N`` de una ONU
que ya funciona ("copiar una ONU"): la V1600G0-B guarda su configuración con la misma sintaxis
con que se teclea.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from olterra.drivers.base import CommandCall, ParamError, UnrecognizedOutput
from olterra.identifiers import normalize_gpon_serial

_NAME = r"^[A-Za-z0-9_.\-]{1,32}$"
_LABEL = r"^[A-Za-z0-9_\-]{1,32}$"
_ACCOUNT = r"^[A-Za-z0-9_.@\-]{1,32}$"
_UNI = re.compile(r"^(?:lan[1-8]|ssid[1-8])$")


class Tcont(BaseModel):
    id: int = Field(ge=1, le=255)
    name: str = Field("INTERNET", pattern=_LABEL)
    dba: str = Field(pattern=_NAME, description="Perfil DBA (ancho de banda de subida)")


class Gemport(BaseModel):
    id: int = Field(ge=1, le=255)
    tcont: int = Field(ge=1, le=255)
    name: str = Field("INTERNET", pattern=_LABEL)
    limit_down: str | None = Field(
        None, pattern=_NAME, description="Perfil de tráfico de bajada (traffic-limit downstream)"
    )


class Service(BaseModel):
    name: str = Field("ser_1", pattern=_LABEL)
    gemport: int = Field(ge=1, le=255)
    vlan: int = Field(ge=1, le=4094)


class ServicePort(BaseModel):
    id: int = Field(ge=1, le=128)
    gemport: int = Field(ge=1, le=255)
    user_vlan: int = Field(ge=1, le=4094)
    vlan: int = Field(ge=1, le=4094)
    cos: int = Field(0, ge=0, le=7)


class Wan(BaseModel):
    """WAN en modo router con PPPoE: la ONU marca y hace NAT."""

    index: int = Field(1, ge=1, le=8)
    mtu: int = Field(1492, ge=576, le=1500)
    vlan: int = Field(ge=1, le=4094)
    cos: int = Field(0, ge=0, le=7)
    nat: bool = True
    binds: list[str] = Field(
        default_factory=lambda: ["lan1", "lan2", "lan3", "lan4", "ssid1"],
        min_length=1,
        max_length=16,
        description="Puertos de la ONU que salen por esta WAN",
    )

    @field_validator("binds")
    @classmethod
    def _known_ports(cls, value: list[str]) -> list[str]:
        for port in value:
            if not _UNI.fullmatch(port):
                raise ValueError(f"Puerto de ONU inválido: {port!r} (lan1..lan8, ssid1..ssid8)")
        return list(dict.fromkeys(value))


class Wifi(BaseModel):
    ssid_index: int = Field(1, ge=1, le=8)


# Servicios de la ONU que se pueden abrir desde la WAN (además del ping), en el orden de la
# ayuda de la V1600G0-B (`onu N pri acl ?`).
WAN_SERVICES: tuple[str, ...] = ("telnet", "ftp", "http", "https", "tftp", "ssh")
WanService = Literal["telnet", "ftp", "http", "https", "tftp", "ssh"]
_WEB: tuple[WanService, ...] = ("http", "https")


class Management(BaseModel):
    """Gestión remota de la ONU: nivel de firewall y qué responde desde internet (la WAN).

    Desde la LAN todo sigue abierto. Lo que no está en ``wan_access`` se cierra desde la WAN.
    """

    firewall: Literal["disable", "low", "middle", "high"] | None = Field(
        "low", description="Nivel de firewall de la ONU (disable = apagado); null = no se toca"
    )
    ping_wan: bool = Field(True, description="Responder ping desde internet")
    wan_access: list[WanService] = Field(
        default_factory=lambda: list(_WEB),
        description="Servicios de la ONU a los que se entra desde internet",
    )

    admin_user: str | None = Field(
        None,
        pattern=_ACCOUNT,
        description="Usuario de administración de la ONU; null = las cuentas de la ONU no se "
        "tocan. Su clave se guarda cifrada aparte (onu_admin_password), nunca en este cuerpo",
    )
    user_account: str | None = Field(
        None,
        pattern=_ACCOUNT,
        description="Cuenta normal del cliente (user); null = desactivada. Clave aparte "
        "(onu_user_password)",
    )

    @field_validator("wan_access")
    @classmethod
    def _unique(cls, value: list[WanService]) -> list[WanService]:
        return sorted(set(value), key=WAN_SERVICES.index)

    @model_validator(mode="after")
    def _accounts(self) -> Management:
        # La OLT pide las dos cuentas en el mismo comando, empezando por la de administración.
        if self.user_account is not None and self.admin_user is None:
            raise ValueError("La cuenta normal de la ONU va junto con la de administración")
        return self

    def secret_fields(self) -> list[str]:
        """Las claves que este plan necesita guardadas (cifradas) para poder aplicarse."""
        fields = []
        if self.admin_user is not None:
            fields.append("onu_admin_password")
        if self.user_account is not None:
            fields.append("onu_user_password")
        return fields


class TemplateBody(BaseModel):
    auth_profile: str = Field(
        "default", pattern=_NAME, description="Perfil con el que se autoriza (onu add … profile)"
    )
    onu_profile: str | None = Field(None, pattern=_NAME, description="onu N profile onu …")
    tconts: list[Tcont] = Field(default_factory=list, max_length=8)
    gemports: list[Gemport] = Field(default_factory=list, max_length=8)
    services: list[Service] = Field(default_factory=list, max_length=8)
    service_ports: list[ServicePort] = Field(default_factory=list, max_length=8)
    wan: Wan | None = None
    wifi: Wifi | None = None
    management: Management | None = None

    @model_validator(mode="after")
    def _references(self) -> TemplateBody:
        tconts = {t.id for t in self.tconts}
        gemports = {g.id for g in self.gemports}
        if len(tconts) != len(self.tconts) or len(gemports) != len(self.gemports):
            raise ValueError("T-CONT o GEM repetido")
        for gem in self.gemports:
            if gem.tcont not in tconts:
                raise ValueError(f"El GEM {gem.id} usa el T-CONT {gem.tcont}, que no está")
        used = [s.gemport for s in self.services] + [p.gemport for p in self.service_ports]
        for gemport in used:
            if gemport not in gemports:
                raise ValueError(f"Se usa el GEM {gemport}, que no está")
        return self


def check_cli_secret(value: SecretStr | None, *, low: int, label: str) -> SecretStr | None:
    """Una clave que se escribe en la CLI: imprimible, sin espacios ni '?' (la CLI abre la ayuda)."""
    if value is None:
        return None
    text = value.get_secret_value()
    if not re.fullmatch(rf"[!-~]{{{low},63}}", text) or "?" in text:
        raise ValueError(
            f"{label}: de {low} a 63 caracteres imprimibles, sin espacios ni signo de pregunta"
        )
    return value


class OnuServiceData(BaseModel):
    """WAN y WiFi de una ONU: lo de cada cliente. Las claves solo viajan selladas."""

    pon: int = Field(ge=1, le=16)
    onu: int = Field(ge=1, le=128)
    equipment_id: str | None = Field(
        None,
        pattern=r"^[A-Za-z0-9_.\-]{1,32}$",
        description="Equipment ID del autofind (p. ej. VSOLV422). Sin él la V1600G0-B responde "
        "'Unsupport private protocol' a la WAN y al WiFi de una ONU recién autorizada",
    )
    pppoe_user: str | None = Field(None, pattern=r"^[A-Za-z0-9_.@\-]{1,64}$")
    pppoe_password: SecretStr | None = None
    wifi_ssid: str | None = Field(None, pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    wifi_key: SecretStr | None = None

    @field_validator("pppoe_password")
    @classmethod
    def _pppoe_password(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=1, label="Clave PPPoE")

    @field_validator("wifi_key")
    @classmethod
    def _wifi_key(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=8, label="Clave WiFi")


class ClientData(OnuServiceData):
    """Lo de cada cliente en un alta."""

    serial: str
    description: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")

    @field_validator("serial")
    @classmethod
    def _serial(cls, value: str) -> str:
        canonical = normalize_gpon_serial(value)
        if canonical is None:
            raise ValueError("Serial GPON inválido (VSOL0008D09C o su forma hexadecimal)")
        return canonical


def _caller(data: OnuServiceData) -> Callable[..., CommandCall]:
    where: dict[str, Any] = {"pon": data.pon, "onu": data.onu}

    def call(key: str, **params: Any) -> CommandCall:
        return CommandCall(key, {**where, **params})

    return call


def clean_label(text: str, *, limit: int) -> str:
    """Un nombre como lo escribe una persona, en la forma que acepta la CLI de la OLT.

    "José Pérez Ñuñez" → "Jose_Perez_Nunez": sin tildes, espacios como "_" y solo letras,
    números, punto, guion y guion bajo. La OLT no acepta espacios en la descripción ni en el SSID.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]+", "_", ascii_text.strip()).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned)[:limit].rstrip("_")
    if not cleaned:
        raise ParamError("El nombre no tiene letras ni números que la OLT pueda guardar")
    return cleaned


def adapt_binds(
    binds: Sequence[str], ethernet_ports: int | None, wifi_ports: int | None
) -> list[str]:
    """Los puertos de la plantilla que de verdad tiene esta ONU (una V422 tiene 2 LAN, la V824 4)."""
    kept = []
    for port in binds:
        number = int(port[-1])
        if port.startswith("lan") and ethernet_ports is not None and number > ethernet_ports:
            continue
        if port.startswith("ssid") and wifi_ports == 0:
            continue
        kept.append(port)
    if not any(port.startswith("lan") for port in kept) and (ethernet_ports or 1) >= 1:
        kept.insert(0, "lan1")
    return kept


def base_calls(template: TemplateBody, client: ClientData) -> list[CommandCall]:
    """Autorizar y el servicio (T-CONT, GEM, VLAN). No necesita que la ONU esté conectada."""
    call = _caller(client)
    calls = [
        call("onu.authorize", profile=template.auth_profile, serial=client.serial),
        call("onu.set_description", description=client.description),
    ]
    # "onu N profile onu P" aparece en la configuración guardada pero la V1600G0-B lo rechaza
    # (% Unknown command, 2026-10-07): es lo que deja "onu add … profile P". Solo se manda si
    # pide otro perfil.
    if template.onu_profile and template.onu_profile != template.auth_profile:
        calls.append(call("onu.bind_onu_profile", profile=template.onu_profile))
    for tcont in template.tconts:
        calls.append(call("onu.tcont", tcont=tcont.id, tcont_name=tcont.name, profile=tcont.dba))
    for gem in template.gemports:
        calls.append(call("onu.gemport", gemport=gem.id, tcont=gem.tcont, gemport_name=gem.name))
        if gem.limit_down:
            calls.append(call("onu.gemport_limit_down", gemport=gem.id, profile=gem.limit_down))
    for service in template.services:
        calls.append(
            call("onu.service", service=service.name, gemport=service.gemport, vlan=service.vlan)
        )
    for port in template.service_ports:
        calls.append(
            call(
                "onu.service_port",
                service_port=port.id,
                gemport=port.gemport,
                user_vlan=port.user_vlan,
                vlan=port.vlan,
                cos=port.cos,
            )
        )
    return calls


def service_calls(
    template: TemplateBody, data: OnuServiceData, binds: Sequence[str] | None = None
) -> list[CommandCall]:
    """WAN PPPoE y WiFi (comandos privados de VSOL). Sin guardar: lo agrega quien la llama.

    ``binds``: los puertos ya adaptados al modelo de la ONU (``adapt_binds``); si no, los de la
    plantilla.
    """
    if template.wan is not None and not (data.pppoe_user and data.pppoe_password):
        raise ParamError("La plantilla configura PPPoE: faltan el usuario y la clave PPPoE")
    if (data.wifi_ssid is None) != (data.wifi_key is None):
        raise ParamError("El WiFi necesita SSID y clave")
    if data.wifi_ssid is not None and template.wifi is None:
        raise ParamError("La plantilla no configura WiFi")
    wants_private = (
        template.wan is not None or data.wifi_ssid is not None or template.management is not None
    )
    if wants_private and not data.equipment_id:
        raise ParamError(
            "Falta el Equipment ID de la ONU (sale en el autofind, p. ej. VSOLV422): sin él la "
            "OLT no configura la WAN, el WiFi ni la gestión remota"
        )
    call = _caller(data)
    calls: list[CommandCall] = []
    # La OLT necesita saber el modelo VSOL para hablarle a la ONU en su protocolo privado
    # ("pri"): en las ONU que funcionan aparece "onu N pri equid VSOLV824" antes de la WAN.
    if wants_private:
        calls.append(call("onu.pri_equid", equipment_id=data.equipment_id))
    if template.wan is not None:
        wan = template.wan
        calls += [
            call("onu.wan_add_route"),
            call("onu.wan_route_mode", wan=wan.index, mtu=wan.mtu),
            call(
                "onu.wan_pppoe",
                wan=wan.index,
                pppoe_user=data.pppoe_user,
                nat="enable" if wan.nat else "disable",
            ),
            call("onu.wan_vlan", wan=wan.index, vlan=wan.vlan, cos=wan.cos),
            call("onu.wan_bind", wan=wan.index, binds=" ".join(binds or wan.binds)),
        ]
    if template.wifi is not None and data.wifi_ssid is not None:
        calls.append(
            call("onu.wifi_ssid", ssid_index=template.wifi.ssid_index, ssid=data.wifi_ssid)
        )
    if template.management is not None:
        calls += management_calls(template.management, call)
    return calls


def management_calls(management: Management, call: Callable[..., CommandCall]) -> list[CommandCall]:
    """Cuentas, firewall y acceso desde la WAN. Cada servicio queda explícito: abierto o cerrado.

    Las cuentas van primero: la clave de administración cambia antes de abrir la web.
    """
    calls: list[CommandCall] = []
    if management.admin_user is not None and management.user_account is not None:
        calls.append(
            call(
                "onu.account_admin_user",
                admin_user=management.admin_user,
                user_name=management.user_account,
            )
        )
    elif management.admin_user is not None:
        calls.append(call("onu.account_admin", admin_user=management.admin_user))
    if management.firewall is not None:
        calls.append(call("onu.firewall", level=management.firewall))

    def acl(service: str, open_from_wan: bool) -> CommandCall:
        return call("onu.acl", service=service, wan_access="enable" if open_from_wan else "disable")

    calls.append(acl("ping", management.ping_wan))
    calls += [acl(service, service in management.wan_access) for service in WAN_SERVICES]
    return calls


def configure_calls(template: TemplateBody, data: OnuServiceData) -> list[CommandCall]:
    """WAN y WiFi de una ONU ya autorizada (reintento o cambio de cliente), y guardar."""
    calls = service_calls(template, data)
    if not calls:
        raise ParamError("La plantilla no tiene WAN, WiFi ni gestión remota que configurar")
    return [*calls, CommandCall("config.save")]


def authorize_calls(template: TemplateBody, client: ClientData) -> list[CommandCall]:
    """La receta completa en un solo plan (base, WAN/WiFi y guardar).

    En la V1600G0-B la WAN y el WiFi no entran hasta que la ONU se conecta: el alta de la
    interfaz va en dos fases (``api/jobs.py``). Esto queda para equipos que sí la aceptan de una.
    """
    # Se arma primero: valida PPPoE, WiFi y Equipment ID antes de tocar la OLT.
    private = service_calls(template, client)
    return [*base_calls(template, client), *private, CommandCall("config.save")]


# --- Copiar una ONU -------------------------------------------------------------------------


@dataclass
class OnuRunningConfig:
    """Una ONU leída de su configuración: la plantilla y lo que era de ese cliente."""

    onu: int
    serial: str | None
    template: dict[str, Any]
    client: dict[str, Any] = field(default_factory=dict)
    # Líneas que la plantilla no reproduce (otras WAN, otros SSID): se muestran, no se copian.
    ignored: list[str] = field(default_factory=list)


_P = r"onu (?P<onu>\d+) "
_LINES: list[tuple[str, re.Pattern[str]]] = [
    ("add", re.compile(r"onu add (?P<onu>\d+) profile (?P<profile>\S+) sn (?P<serial>\S+)")),
    ("desc", re.compile(_P + r"desc (?P<desc>.*)")),
    ("onu_profile", re.compile(_P + r"profile onu (?P<profile>\S+)")),
    ("tcont", re.compile(_P + r"tcont (?P<id>\d+)(?: name (?P<name>\S+))? dba (?P<dba>\S+)")),
    (
        "gemport",
        re.compile(
            _P + r"gemport (?P<id>\d+) tcont (?P<tcont>\d+)"
            r"(?: gemport_name (?P<name>\S+))?(?: portid \d+)?"
        ),
    ),
    ("limit_down", re.compile(_P + r"gemport (?P<id>\d+) traffic-limit downstream (?P<p>\S+)")),
    ("service", re.compile(_P + r"service (?P<name>\S+) gemport (?P<gem>\d+) vlan (?P<vlan>\d+)")),
    (
        "service_port",
        re.compile(
            _P + r"service-port (?P<id>\d+) gemport (?P<gem>\d+) uservlan (?P<uvlan>\d+) "
            r"vlan (?P<vlan>\d+)(?: new_cos (?P<cos>\d+))?"
        ),
    ),
    ("equid", re.compile(_P + r"pri equid (?P<id>\S+)")),
    ("wan_add", re.compile(_P + r"pri wan_adv add route")),
    (
        "wan_mode",
        re.compile(_P + r"pri wan_adv index (?P<i>\d+) route mode internet mtu (?P<mtu>\d+)"),
    ),
    (
        "wan_pppoe",
        re.compile(
            _P + r"pri wan_adv index (?P<i>\d+) route ipv4 pppoe .*?user (?P<user>\S+) pwd \S+"
            r".*? nat (?P<nat>enable|disable)"
        ),
    ),
    (
        "wan_vlan",
        re.compile(
            _P + r"pri wan_adv index (?P<i>\d+) vlan tag wan_vlan (?P<vlan>\d+) (?P<cos>\d+)"
        ),
    ),
    ("wan_bind", re.compile(_P + r"pri wan_adv index (?P<i>\d+) bind (?P<binds>.+)")),
    ("wifi", re.compile(_P + r"pri wifi_ssid (?P<i>\d+) name (?P<ssid>\S+) .*")),
    ("firewall", re.compile(_P + r"pri firewall level (?P<level>disable|low|middle|high)")),
    (
        "accounts",
        re.compile(
            _P + r"pri username admin_control enable (?P<admin>\S+) \S+ user_control "
            r"(?:disable|enable (?P<user>\S+) \S+)"
        ),
    ),
    (
        "acl",
        re.compile(
            _P + r"pri acl (?P<service>ping|telnet|ftp|http|https|tftp|ssh) control enable "
            r"lan enable "
            r"wan (?P<wan>enable|disable) ipv4_control disable ipv6_control disable"
            r"(?: port \d+)?"
        ),
    ),
]


def parse_onu_running_config(text: str) -> OnuRunningConfig:
    """``show running-config onu N`` → plantilla + datos del cliente + lo que no se copia."""
    found: dict[str, list[re.Match[str]]] = {}
    ignored: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("-") or not line.startswith("onu "):
            continue
        for kind, pattern in _LINES:
            match = pattern.fullmatch(line)
            if match is not None:
                found.setdefault(kind, []).append(match)
                break
        else:
            ignored.append(re.sub(r"(shared_key|pwd) \S+", r"\1 ******", line))
    if "add" not in found:
        raise UnrecognizedOutput("vsol.onu.running_config", "no hay línea 'onu add'", text)
    add = found["add"][0]
    limits = {int(m["id"]): m["p"] for m in found.get("limit_down", [])}
    template: dict[str, Any] = {
        "auth_profile": add["profile"],
        "onu_profile": next(
            (m["profile"] for m in found.get("onu_profile", []) if m["profile"] != add["profile"]),
            None,
        ),
        "tconts": [
            {"id": int(m["id"]), "name": m["name"] or "INTERNET", "dba": m["dba"]}
            for m in found.get("tcont", [])
        ],
        "gemports": [
            {
                "id": int(m["id"]),
                "tcont": int(m["tcont"]),
                "name": m["name"] or "INTERNET",
                "limit_down": limits.get(int(m["id"])),
            }
            for m in found.get("gemport", [])
        ],
        "services": [
            {"name": m["name"], "gemport": int(m["gem"]), "vlan": int(m["vlan"])}
            for m in found.get("service", [])
        ],
        "service_ports": [
            {
                "id": int(m["id"]),
                "gemport": int(m["gem"]),
                "user_vlan": int(m["uvlan"]),
                "vlan": int(m["vlan"]),
                "cos": int(m["cos"] or 0),
            }
            for m in found.get("service_port", [])
        ],
        "wan": None,
        "wifi": None,
        "management": None,
    }
    client: dict[str, Any] = {"serial": add["serial"]}
    if "desc" in found:
        client["description"] = found["desc"][0]["desc"].strip() or None
    pppoe = found.get("wan_pppoe", [])
    if pppoe:
        index = pppoe[0]["i"]

        def of(kind: str) -> re.Match[str] | None:
            return next((m for m in found.get(kind, []) if m["i"] == index), None)

        mode, vlan, bind = of("wan_mode"), of("wan_vlan"), of("wan_bind")
        if vlan is not None:
            template["wan"] = {
                "index": int(index),
                "mtu": int(mode["mtu"]) if mode else 1492,
                "vlan": int(vlan["vlan"]),
                "cos": int(vlan["cos"]),
                "nat": pppoe[0]["nat"] == "enable",
                "binds": bind["binds"].split() if bind else ["lan1"],
            }
            client["pppoe_user"] = pppoe[0]["user"]
    wifi = found.get("wifi", [])
    if wifi:
        template["wifi"] = {"ssid_index": int(wifi[0]["i"])}
        client["wifi_ssid"] = wifi[0]["ssid"]
    if "equid" in found:
        client["equipment_id"] = found["equid"][0]["id"]
    if "firewall" in found or "acl" in found or "accounts" in found:
        from_wan = {m["service"] for m in found.get("acl", []) if m["wan"] == "enable"}
        accounts = found["accounts"][0] if "accounts" in found else None
        template["management"] = {
            "firewall": found["firewall"][0]["level"] if "firewall" in found else None,
            "ping_wan": "ping" in from_wan,
            "wan_access": [service for service in WAN_SERVICES if service in from_wan],
            # Los usuarios sí; las claves no se copian: se escriben al guardar el plan.
            "admin_user": accounts["admin"] if accounts else None,
            "user_account": accounts["user"] if accounts else None,
        }
    # Lo que se leyó pero no entra a la plantilla (otras WAN, otros SSID) también se avisa.
    for kind in ("wan_pppoe", "wifi"):
        for extra in found.get(kind, [])[1:]:
            ignored.append(re.sub(r"(shared_key|pwd) \S+", r"\1 ******", extra.group(0)))
    TemplateBody.model_validate(template)  # que lo copiado sea una plantilla válida
    return OnuRunningConfig(
        onu=int(add["onu"]),
        serial=normalize_gpon_serial(add["serial"]),
        template=template,
        client=client,
        ignored=ignored,
    )
