"""Esquemas de entrada y salida de la API. Ninguna salida lleva claves."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from olterra.drivers.vsol_gpon.provisioning import ClientData, TemplateBody


def _ip_text(value: Any) -> str | None:
    # asyncpg devuelve INET como IPv4Address/IPv4Interface.
    return None if value is None else str(value).split("/")[0]


class OltCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    model: str | None = None
    firmware: str | None = None
    router_id: UUID | None = Field(None, description="Router del túnel detrás del cual está la OLT")
    real_ip: IPv4Address | None = Field(None, description="IP de la OLT en la red del ISP")
    ssh_port: int = Field(22, ge=1, le=65535)
    snmp_port: int = Field(161, ge=1, le=65535)
    username: str | None = Field(
        None, min_length=1, max_length=64, description="Vacío: el usuario de fábrica de VSOL"
    )
    password: SecretStr | None = Field(
        None,
        description="Vacío en una OLT nueva (nunca se ha entrado por SSH): se usa la clave de "
        "fábrica que el servidor tenga configurada",
    )
    enable_password: SecretStr | None = Field(
        None, description="La que el cliente configuró en su OLT para entrar a modo privilegiado"
    )
    snmp_community: SecretStr | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class OltDefaults(BaseModel):
    """Lo que la interfaz necesita saber de las credenciales de fábrica (nunca la clave)."""

    username: str
    password_configured: bool


class OltUpdate(BaseModel):
    """Cambia solo lo que viene. En las claves de enable y SNMP, una cadena vacía la quita."""

    model: str | None = None
    firmware: str | None = None
    real_ip: IPv4Address | None = Field(None, description="IP de la OLT en la red del ISP")
    ssh_port: int | None = Field(None, ge=1, le=65535)
    snmp_port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, min_length=1, max_length=64)
    password: SecretStr | None = Field(None, min_length=1)
    enable_password: SecretStr | None = None
    snmp_community: SecretStr | None = None


class OltOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    driver: str
    model: str | None
    firmware: str | None
    router_id: UUID | None
    real_ip: str | None
    nat_ip: str | None
    ssh_port: int
    snmp_port: int
    status: str = Field(description="pending (nunca consultada), online o unreachable")
    last_seen_at: datetime | None = Field(None, description="Última vez que respondió")
    created_at: datetime

    _ips = field_validator("real_ip", "nat_ip", mode="before")(_ip_text)


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str


class MeOut(BaseModel):
    """Quién llama: el ISP y la llave. La interfaz lo usa para saludar y ocultar acciones."""

    tenant: TenantOut
    key_name: str
    scopes: list[str]


class CommandOut(BaseModel):
    key: str
    command: str = Field(description="Como se envía a esta OLT (con sus overrides de modelo)")
    scope: Literal["olt", "pon", "onu"] = Field(
        description="Qué pide la consulta: nada, al menos un PON o al menos un PON:ONU"
    )
    verified: bool = Field(description="Validado con una captura de laboratorio")
    parsed: bool = Field(description="La salida se interpreta en datos, no solo texto")
    notes: str = ""


class OltCreated(OltOut):
    used_default_credentials: bool = Field(
        False, description="Se usó la clave de fábrica: conviene cambiarla en la OLT y en Olterra"
    )


class TemplateIn(BaseModel):
    name: str = Field(
        pattern=r"^[A-Za-z0-9_. \-]{1,48}$", description="p. ej. 'Hogar 100M VLAN 111'"
    )
    body: TemplateBody


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    driver: str
    body: TemplateBody
    created_at: datetime
    updated_at: datetime


class AuthorizeRequest(ClientData):
    """Alta de una ONU: plantilla + lo del cliente. Las claves solo viajan selladas."""

    template_id: UUID


class OnuRef(BaseModel):
    pon: int = Field(ge=1, le=16)
    onu: int = Field(ge=1, le=128)


class WritePlanOut(BaseModel):
    plan_id: UUID
    olt_id: UUID
    status: str
    created_at: datetime
    unverified: list[str] = Field(
        default_factory=list,
        description="Comandos sin captura de laboratorio que este plan corre (solo en modo laboratorio)",
    )


class QueryRequest(BaseModel):
    commands: list[str] = Field(
        min_length=1, max_length=40, description="Llaves del catálogo de solo lectura"
    )
    # Topes para que una consulta no se vuelva un plan de miles de comandos.
    pon: list[int] = Field(
        default_factory=list, max_length=16, description="PON para los comandos por puerto"
    )
    onu: list[str] = Field(
        default_factory=list, max_length=64, description="Muestras PON:ONU para comandos por ONU"
    )


class PlanOut(BaseModel):
    plan_id: UUID
    olt_id: UUID
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None


class PlanSummary(BaseModel):
    """Un plan sin su resultado (que puede traer una running-config entera)."""

    plan_id: UUID
    status: str
    requested_by: str
    commands: list[str] = Field(description="Llaves del catálogo, en orden y sin repetir")
    created_at: datetime
    finished_at: datetime | None = None


Transport = Literal["wireguard", "sstp"]
ROUTEROS_VERSION = r"^[0-9]{1,2}(\.[0-9]{1,3}){0,2}$"


class RouterCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    routeros_version: str | None = Field(None, pattern=ROUTEROS_VERSION)
    transport: Transport | None = Field(
        None,
        description="Si no se indica: RouterOS 6 va por SSTP (no tiene WireGuard), el resto por WireGuard",
    )


class RouterUpdate(BaseModel):
    """Al rotar: otra versión de RouterOS cambia el transporte (6 → SSTP, 7 → WireGuard)."""

    routeros_version: str | None = Field(None, pattern=ROUTEROS_VERSION)
    transport: Transport | None = None


class RouterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    peer_index: int
    overlay_ip: str
    transport: Transport
    wg_public_key: str | None = Field(description="Solo WireGuard")
    ppp_user: str | None = Field(description="Solo SSTP: usuario del secreto PPP")
    routeros_version: str | None
    created_at: datetime


class RouterScriptOut(BaseModel):
    router: RouterOut
    isp_script: str = Field(
        description="Para el MikroTik del ISP. Contiene su llave privada o su clave SSTP."
    )
    hub_script: str = Field(description="Para el concentrador de la plataforma")


class ReconOnu(BaseModel):
    olt: str
    pon: int | None = None
    onu: int | None = None
    serial: str | None = None
    state: str | None = None
    description: str | None = None
    pppoe_user: str | None = None
    macs: list[str] = Field(default_factory=list)


class ReconSecret(BaseModel):
    router: str
    name: str
    profile: str | None = None
    disabled: bool = False
    comment: str | None = None


class ReconSession(BaseModel):
    router: str
    name: str
    caller_id: str | None = None
    address: str | None = None


class ReconCustomer(BaseModel):
    id: str
    name: str | None = None
    status: str | None = None
    pppoe_user: str | None = None
    onu_serial: str | None = None
    access: str | None = None
    nap: str | None = None
    nap_port: str | None = None
    router: str | None = None


class ReconRequest(BaseModel):
    onus: list[ReconOnu] = Field(default_factory=list, max_length=200_000)
    secrets: list[ReconSecret] = Field(default_factory=list, max_length=200_000)
    sessions: list[ReconSession] = Field(default_factory=list, max_length=200_000)
    customers: list[ReconCustomer] = Field(default_factory=list, max_length=200_000)


class ReconFile(BaseModel):
    kind: Literal["onus", "secrets", "sessions", "customers"]
    name: str
    records: int


class ReconSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    requested_by: str
    source: Literal["api", "upload", "demo"]
    files: list[ReconFile]
    counts: dict[str, int]


class ReconOut(ReconSummary):
    findings: list[dict[str, Any]]
