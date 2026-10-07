"""Esquemas de entrada y salida de la API. Ninguna salida lleva claves."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from olterra.drivers.vsol_gpon.provisioning import TemplateBody, check_cli_secret
from olterra.identifiers import normalize_gpon_serial


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


class UserOut(BaseModel):
    username: str
    display_name: str
    role: Literal["admin", "tecnico", "lectura"]
    must_change_password: bool


class MeOut(BaseModel):
    """Quién llama: el ISP y la llave o el usuario. La interfaz lo usa para saludar y ocultar acciones."""

    tenant: TenantOut
    key_name: str
    scopes: list[str]
    user: UserOut | None = Field(None, description="Solo si entró con usuario y contraseña")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)
    remember: bool = Field(False, description="Sesión de 30 días en vez de 12 horas")


class LoginOut(BaseModel):
    token: str = Field(description="Va en Authorization: Bearer. Se entrega una sola vez")
    expires_at: datetime
    must_change_password: bool


class PasswordChangeIn(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=1, max_length=128)


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


class _CustomerService(BaseModel):
    """PPPoE y WiFi del cliente. Las claves solo viajan selladas y no se devuelven nunca."""

    template_id: UUID
    pppoe_user: str | None = Field(None, pattern=r"^[A-Za-z0-9_.@\-]{1,64}$")
    pppoe_password: SecretStr | None = None
    wifi_name: str | None = Field(
        None,
        min_length=1,
        max_length=40,
        description="Como lo escribe el cliente; la OLT no acepta espacios",
    )
    wifi_key: SecretStr | None = None

    @field_validator("pppoe_password")
    @classmethod
    def _pppoe_password(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=1, label="Clave PPPoE")

    @field_validator("wifi_key")
    @classmethod
    def _wifi_key(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=8, label="Clave WiFi")


class AuthorizeIn(_CustomerService):
    """Alta de una ONU nueva: lo que sabe quien aprovisiona. Lo técnico lo resuelve Olterra."""

    pon: int = Field(ge=1, le=16)
    serial: str
    customer: str = Field(
        min_length=1,
        max_length=80,
        description="Nombre del cliente (tildes y espacios se arreglan)",
    )
    equipment_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    onu: int | None = Field(None, ge=1, le=128, description="Vacío: la primera posición libre")

    @field_validator("serial")
    @classmethod
    def _serial(cls, value: str) -> str:
        canonical = normalize_gpon_serial(value)
        if canonical is None:
            raise ValueError("Serial GPON inválido (VSOL0008D09C o su forma hexadecimal)")
        return canonical


class ConfigureIn(_CustomerService):
    """Internet y WiFi de una ONU ya autorizada."""

    pon: int = Field(ge=1, le=16)
    onu: int = Field(ge=1, le=128)


class JobStep(BaseModel):
    key: str
    label: str
    status: Literal["pending", "running", "done", "failed", "skipped"]
    message: str | None = None


class JobOut(BaseModel):
    id: UUID
    olt_id: UUID
    kind: Literal["authorize", "configure"]
    status: Literal["running", "done", "failed"]
    step: str
    template_name: str | None
    pon: int | None
    onu: int | None
    serial: str | None
    description: str | None
    pppoe_user: str | None
    wifi_ssid: str | None
    equipment_id: str | None
    phase: str | None
    rx_dbm: float | None
    unverified: list[str] = Field(
        default_factory=list,
        description="Comandos sin validar que corre (solo en modo laboratorio)",
    )
    steps: list[JobStep]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


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
