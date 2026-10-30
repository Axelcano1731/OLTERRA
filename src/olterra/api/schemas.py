"""Esquemas de entrada y salida de la API. Ninguna salida lleva claves."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from olterra.drivers import DRIVERS
from olterra.drivers.vsol_gpon.provisioning import TemplateBody, check_cli_secret
from olterra.identifiers import normalize_gpon_serial


def _ip_text(value: Any) -> str | None:
    # asyncpg devuelve INET como IPv4Address/IPv4Interface.
    return None if value is None else str(value).split("/")[0]


CliProtocol = Literal["ssh", "telnet"]


def _known_driver(value: str | None) -> str | None:
    if value is not None and value not in DRIVERS:
        raise ValueError(f"Marca de OLT desconocida: {value} (hay {', '.join(sorted(DRIVERS))})")
    return value


class OltCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    driver: str = Field("vsol-gpon", description="Marca de la OLT: vsol-gpon, hsgq-gpon o bcd-gpon")
    model: str | None = None
    firmware: str | None = None
    router_id: UUID | None = Field(None, description="Router del túnel detrás del cual está la OLT")
    real_ip: IPv4Address | None = Field(None, description="IP de la OLT en la red del ISP")
    ssh_port: int = Field(22, ge=1, le=65535)
    snmp_port: int = Field(161, ge=1, le=65535)
    cli_protocol: CliProtocol = Field(
        "ssh",
        description="telnet solo para OLT que no aceptan a los usuarios de la CLI por SSH; viaja "
        "en claro entre el MikroTik y la OLT",
    )
    telnet_port: int = Field(23, ge=1, le=65535)
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

    _driver = field_validator("driver")(_known_driver)


class DriverOption(BaseModel):
    key: str
    label: str
    writes: bool = Field(description="Si Olterra sabe aprovisionar y cambiar cosas en esta marca")


class OltDefaults(BaseModel):
    """Lo que la interfaz necesita para crear una OLT: credenciales de fábrica (nunca la
    clave) y las marcas que Olterra conoce."""

    username: str
    password_configured: bool
    drivers: list[DriverOption] = []


class OltUpdate(BaseModel):
    """Cambia solo lo que viene. En las claves de enable y SNMP, una cadena vacía la quita."""

    driver: str | None = None
    model: str | None = None
    firmware: str | None = None
    real_ip: IPv4Address | None = Field(None, description="IP de la OLT en la red del ISP")
    ssh_port: int | None = Field(None, ge=1, le=65535)
    snmp_port: int | None = Field(None, ge=1, le=65535)
    cli_protocol: CliProtocol | None = None
    telnet_port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, min_length=1, max_length=64)
    password: SecretStr | None = Field(None, min_length=1)
    enable_password: SecretStr | None = None
    snmp_community: SecretStr | None = None

    _driver = field_validator("driver")(_known_driver)


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
    cli_protocol: CliProtocol = "ssh"
    telnet_port: int = 23
    router_name: str | None = Field(None, description="El MikroTik por el que entra Olterra")
    router_gateway: bool | None = Field(
        None,
        description="Ese MikroTik ya es puerta (toda OLT nueva funciona sola); False: tiene el "
        "script viejo y hay que rotarlo una vez",
    )
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
    # Claves de las cuentas de la ONU (body.management.admin_user / user_account). Sin valor se
    # deja la que ya está guardada. Se guardan cifradas y no se devuelven nunca.
    onu_admin_password: SecretStr | None = None
    onu_user_password: SecretStr | None = None

    @field_validator("onu_admin_password")
    @classmethod
    def _admin_password(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=6, label="Contraseña de administración de la ONU")

    @field_validator("onu_user_password")
    @classmethod
    def _user_password(cls, value: SecretStr | None) -> SecretStr | None:
        return check_cli_secret(value, low=6, label="Contraseña de la cuenta normal de la ONU")


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    driver: str
    body: TemplateBody
    # Solo si están guardadas; las claves no salen de la base.
    onu_admin_password_set: bool = False
    onu_user_password_set: bool = False
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
    kind: Literal["authorize", "configure", "olt_password"]
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
    username: str | None = Field(None, description="Cambio de clave: el usuario de la OLT")
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


class OltPasswordChangeIn(BaseModel):
    """Claves nuevas del usuario con el que Olterra entra a la OLT. Al menos una."""

    new_password: SecretStr | None = Field(None, description="Clave de acceso (SSH y web)")
    new_enable_password: SecretStr | None = Field(None, description="Clave de enable")

    @field_validator("new_password", "new_enable_password")
    @classmethod
    def _printable(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        text = value.get_secret_value()
        if not 8 <= len(text) <= 64 or any(not "!" <= ch <= "~" for ch in text):
            raise ValueError("La clave va de 8 a 64 caracteres, sin espacios ni tildes")
        return value

    @model_validator(mode="after")
    def _at_least_one(self) -> OltPasswordChangeIn:
        if self.new_password is None and self.new_enable_password is None:
            raise ValueError("Escribe la clave de acceso nueva, la de enable o las dos")
        return self


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
    ssh_port: int = Field(
        22, ge=1, le=65535, description="Puerto del SSH del MikroTik: por ahí entra Olterra"
    )


class RouterSettings(BaseModel):
    """Lo que se corrige sin rotar: no cambia nada en el MikroTik ni en el concentrador."""

    ssh_port: int = Field(ge=1, le=65535, description="Puerto del SSH del MikroTik")


class RouterUpdate(BaseModel):
    """Al rotar: otra versión de RouterOS cambia el transporte (6 → SSTP, 7 → WireGuard)."""

    routeros_version: str | None = Field(None, pattern=ROUTEROS_VERSION)
    transport: Transport | None = None
    ssh_port: int | None = Field(None, ge=1, le=65535)


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
    ssh_port: int = 22
    gateway: bool = Field(
        False,
        description="El script del router ya lo deja como puerta hacia sus OLT; si no, tiene un "
        "script viejo y hay que rotarlo una vez",
    )
    created_at: datetime


class RouterScriptOut(BaseModel):
    router: RouterOut
    isp_script: str = Field(
        description="Para el MikroTik del ISP. Contiene su llave privada o su clave SSTP."
    )
    hub_script: str = Field(
        description="Para el concentrador, si Olterra no pudo darlo de alta sola (hub_registered)"
    )
    hub_registered: bool = Field(
        default=False, description="Olterra ya dio de alta el router en el concentrador por su API"
    )
    hub_error: str | None = Field(default=None, description="Por qué no se pudo darlo de alta solo")


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


class BackupOut(BaseModel):
    """Un respaldo de la configuración de la OLT (sin el contenido)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    olt_id: UUID
    trigger: Literal["auto", "manual"]
    status: Literal["pending", "ok", "failed"]
    requested_by: str
    size_bytes: int | None
    line_count: int | None
    changed: bool | None = Field(description="Distinto del respaldo anterior")
    added: int | None
    removed: int | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class BackupDetail(BackupOut):
    content: str = Field(description="show running-config, con las claves tapadas")


class DiffLine(BaseModel):
    kind: Literal["+", "-", " ", "@"]
    text: str


class BackupDiff(BaseModel):
    to_id: UUID
    from_id: UUID | None = Field(description="El respaldo anterior con el que se compara")
    from_created_at: datetime | None
    added: int
    removed: int
    lines: list[DiffLine]


class AuditOut(BaseModel):
    """Una entrada de la bitácora: quién, qué, cuándo, desde dónde, antes y después."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    target_type: str | None
    target_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    source_ip: str | None
    created_at: datetime

    @field_validator("source_ip", mode="before")
    @classmethod
    def _ip(cls, value: Any) -> str | None:
        return _ip_text(value)


class OnuStatusOut(BaseModel):
    """Lo último que se sabe de una ONU (del monitoreo)."""

    model_config = ConfigDict(from_attributes=True)

    olt_id: UUID
    pon: int
    onu: int
    serial: str | None
    description: str | None = Field(description="La descripción que tiene la ONU en la OLT")
    customer_name: str | None = Field(
        None, description="El nombre del cliente puesto en el panel; manda sobre la descripción"
    )
    phase: str | None
    phase_label: str = Field(description="La fase en palabras: En línea, Sin luz (fibra)…")
    online: bool
    rx_dbm: float | None
    status_since: datetime = Field(description="Desde cuándo está en línea o caída")
    last_online_at: datetime | None
    updated_at: datetime


class OnuNameIn(BaseModel):
    """Vacío o null quita el nombre (vuelve a verse la descripción de la OLT)."""

    customer_name: str | None = Field(None, max_length=64)

    @field_validator("customer_name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        text = " ".join((value or "").split())
        if any(not ch.isprintable() for ch in text):
            raise ValueError("El nombre lleva caracteres que no se pueden mostrar")
        return text or None


class OnuSampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    online: bool
    rx_dbm: float | None


class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    olt_id: UUID
    olt_name: str | None = None
    pon: int = Field(description="0 = toda la OLT")
    onu: int = Field(description="0 = toda la OLT o todo el PON")
    kind: Literal["olt_unreachable", "pon_down", "onu_offline", "rx_low", "rx_high"]
    severity: Literal["critical", "warning"]
    message: str
    started_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None


class OltHealth(BaseModel):
    id: UUID
    name: str
    status: str
    monitored_at: datetime | None = Field(description="Último sondeo que salió bien")
    onus: int
    online: int
    offline: int
    low_signal: int
    alarms: int


class MonitoringSummary(BaseModel):
    every_minutes: float = Field(description="Cada cuánto se sondea (0 = apagado)")
    onus: int
    online: int
    offline: int
    low_signal: int
    critical: int = Field(description="Alarmas críticas abiertas")
    warning: int = Field(description="Avisos abiertos")
    olts: list[OltHealth]


# --- Mapa FTTH ------------------------------------------------------------------------------

BoxKind = Literal["nap", "ceo", "odf"]
Splitter = Literal["1:2", "1:4", "1:8", "1:16", "1:32", "1:64"]


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class MapBoxIn(LatLng):
    kind: BoxKind = "nap"
    name: str = Field(min_length=1, max_length=60)
    ports: int = Field(8, ge=0, le=144, description="Puertos para clientes (NAP)")
    splitter: Splitter | None = None
    olt_id: UUID | None = Field(None, description="OLT de la que sale")
    pon: int | None = Field(None, ge=1, le=16)
    notes: str | None = Field(None, max_length=500)


class MapBoxPatch(BaseModel):
    """Cambia solo lo que viene."""

    kind: BoxKind | None = None
    name: str | None = Field(None, min_length=1, max_length=60)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    ports: int | None = Field(None, ge=0, le=144)
    splitter: Splitter | None = None
    olt_id: UUID | None = None
    pon: int | None = Field(None, ge=1, le=16)
    notes: str | None = Field(None, max_length=500)


class MapBoxOut(LatLng):
    id: UUID
    kind: BoxKind
    name: str
    ports: int
    used: int = Field(description="Puertos con cliente")
    splitter: str | None
    olt_id: UUID | None
    pon: int | None
    notes: str | None
    down: int = Field(description="Clientes de la caja que están caídos (monitoreo)")
    low_signal: int = Field(description="Clientes con señal baja (monitoreo)")


class MapPortOut(BaseModel):
    port: int
    olt_id: UUID
    pon: int
    onu: int
    description: str | None
    online: bool | None = Field(description="None si el monitoreo todavía no la vio")
    phase_label: str | None
    rx_dbm: float | None


class MapBoxDetail(MapBoxOut):
    port_list: list[MapPortOut]


class PortAssignIn(BaseModel):
    olt_id: UUID
    pon: int = Field(ge=1, le=16)
    onu: int = Field(ge=1, le=128)


class MapPoleIn(LatLng):
    name: str | None = Field(None, max_length=60)
    owner: str | None = Field(None, max_length=60, description="Dueño del poste (la eléctrica…)")
    notes: str | None = Field(None, max_length=500)


class MapPoleOut(MapPoleIn):
    id: UUID


class MapCableIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    fibers: int = Field(12, ge=1, le=288)
    coords: list[tuple[float, float]] = Field(
        min_length=2, max_length=2000, description="Puntos [lng, lat] en orden"
    )
    notes: str | None = Field(None, max_length=500)

    @field_validator("coords")
    @classmethod
    def _in_range(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for lng, lat in value:
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                raise ValueError("Coordenadas fuera de rango")
        return value


class MapCablePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    fibers: int | None = Field(None, ge=1, le=288)
    notes: str | None = Field(None, max_length=500)


class MapCableOut(MapCableIn):
    id: UUID
    length_m: float = Field(description="Longitud geográfica (sobre el elipsoide)")


class MapOlt(BaseModel):
    id: UUID
    name: str
    status: str
    lat: float | None
    lng: float | None


class MapData(BaseModel):
    olts: list[MapOlt]
    boxes: list[MapBoxOut]
    poles: list[MapPoleOut]
    cables: list[MapCableOut]


class MapConfig(BaseModel):
    style_url: str = Field(description="Estilo de MapLibre (teselas)")


class FeasibilityOption(BaseModel):
    box: MapBoxOut
    distance_m: float
    free_ports: int


class Feasibility(BaseModel):
    radius_m: int
    options: list[FeasibilityOption]
