"""Registros de entrada y hallazgos de la conciliación."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from olterra.identifiers import clean_text, normalize_gpon_serial, normalize_mac

# Estado comercial canónico (el de ISPWatch). Activo, gratis y suspendido siguen
# siendo clientes; retirado y cancelado son bajas.
ACTIVE_STATES = frozenset({"activo", "gratis"})
CUSTOMER_STATES = frozenset({"activo", "gratis", "suspendido"})
CLOSED_STATES = frozenset({"retirado", "cancelado"})

_STATUS_ALIASES = {
    "activo": "activo",
    "active": "activo",
    "habilitado": "activo",
    "enabled": "activo",
    "gratis": "gratis",
    "free": "gratis",
    "cortesia": "gratis",
    "cortesía": "gratis",
    "suspendido": "suspendido",
    "suspended": "suspendido",
    "cortado": "suspendido",
    "moroso": "suspendido",
    "retirado": "retirado",
    "retired": "retirado",
    "inactivo": "retirado",
    "inactive": "retirado",
    "cancelado": "cancelado",
    "cancelled": "cancelado",
    "canceled": "cancelado",
}


def normalize_status(value: str | None) -> str | None:
    if not value:
        return None
    return _STATUS_ALIASES.get(clean_text(value).lower())


def normalize_access(value: str | None) -> str | None:
    if value is None:
        return None
    text = clean_text(str(value)).lower()
    if text in {"fibra", "fiber", "ftth", "gpon", "epon", "true", "1", "si", "sí", "yes"}:
        return "fiber"
    if text in {"inalambrico", "inalámbrico", "wireless", "radio", "wisp", "false", "0", "no"}:
        return "wireless"
    return None


def crm_serial(value: str | None) -> str | None:
    """Serial tal como lo guarda un CRM: canónico si es GPON, si no el texto limpio."""
    if not value:
        return None
    return normalize_gpon_serial(value) or clean_text(value) or None


def _opt(value: str | None) -> str | None:
    if value is None:
        return None
    text = clean_text(value)
    return text or None


@dataclass(frozen=True)
class OnuRecord:
    olt: str
    pon: int | None
    onu: int | None
    serial: str | None
    state: str | None = None
    description: str | None = None
    # Usuario PPPoE configurado en la WAN de la ONU (solo ONU en modo router/HGU).
    pppoe_user: str | None = None
    # MAC aprendidas detrás de la ONU (tabla MAC de la OLT).
    macs: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        olt: str,
        pon: int | None,
        onu: int | None,
        serial: str | None,
        state: str | None = None,
        description: str | None = None,
        pppoe_user: str | None = None,
        macs: tuple[str, ...] | list[str] = (),
    ) -> OnuRecord:
        return cls(
            olt=olt,
            pon=pon,
            onu=onu,
            serial=normalize_gpon_serial(serial) or _opt(serial),
            state=_opt(state),
            description=_opt(description),
            # El usuario PPPoE NO se limpia: un espacio de más es justo lo que hay que ver.
            pppoe_user=pppoe_user if pppoe_user else None,
            macs=tuple(m for m in (normalize_mac(x) for x in macs) if m),
        )

    @property
    def position(self) -> str:
        pon = "?" if self.pon is None else str(self.pon)
        onu = "?" if self.onu is None else str(self.onu)
        return f"{self.olt} PON {pon} ONU {onu}"


@dataclass(frozen=True)
class PppoeSecret:
    router: str
    name: str
    profile: str | None = None
    disabled: bool = False
    comment: str | None = None


@dataclass(frozen=True)
class PppoeSession:
    router: str
    name: str
    caller_id: str | None = None
    address: str | None = None

    @property
    def mac(self) -> str | None:
        return normalize_mac(self.caller_id)


@dataclass(frozen=True)
class CrmCustomer:
    id: str
    name: str | None = None
    status: str | None = None  # canónico, ver normalize_status
    pppoe_user: str | None = None
    onu_serial: str | None = None
    access: str | None = None  # "fiber" | "wireless" | None (desconocido)
    nap: str | None = None
    nap_port: str | None = None
    router: str | None = None

    @property
    def is_fiber(self) -> bool:
        if self.access is not None:
            return self.access == "fiber"
        return bool(self.onu_serial or self.nap)

    @property
    def label(self) -> str:
        return f"{self.name} (#{self.id})" if self.name else f"#{self.id}"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "advertencia"
    INFO = "info"


SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


class Kind(StrEnum):
    SERIAL_DUPLICADO = "serial_duplicado"
    SERIAL_CRUZADO = "serial_cruzado"
    PPPOE_DIGITACION = "pppoe_digitacion"
    ONU_PPPOE_SIN_SECRETO = "onu_pppoe_sin_secreto"
    SUSPENDIDO_NAVEGANDO = "suspendido_navegando"
    ONU_SIN_CLIENTE = "onu_sin_cliente"
    CLIENTE_SIN_ONU = "cliente_sin_onu"
    FIBRA_SIN_ONU = "fibra_sin_onu"
    SECRETO_SIN_CLIENTE = "secreto_sin_cliente"
    CLIENTE_SIN_SECRETO = "cliente_sin_secreto"
    ACTIVO_SIN_SERVICIO = "activo_sin_servicio"
    BAJA_CON_ONU = "baja_con_onu"
    SERIAL_POR_REGISTRAR = "serial_por_registrar"


KIND_TITLES = {
    Kind.SERIAL_DUPLICADO: "Serial autorizado en dos posiciones",
    Kind.SERIAL_CRUZADO: "Seriales cruzados entre el CRM y la red",
    Kind.PPPOE_DIGITACION: "Usuario PPPoE con error de digitación",
    Kind.ONU_PPPOE_SIN_SECRETO: "ONU con un usuario PPPoE que no existe en el MikroTik",
    Kind.SUSPENDIDO_NAVEGANDO: "Cliente dado de baja o suspendido que sigue navegando",
    Kind.ONU_SIN_CLIENTE: "ONU autorizada sin cliente",
    Kind.CLIENTE_SIN_ONU: "Cliente cuya ONU no está en ninguna OLT",
    Kind.FIBRA_SIN_ONU: "Cliente de fibra sin ONU identificable",
    Kind.SECRETO_SIN_CLIENTE: "Secreto PPPoE sin cliente en el CRM",
    Kind.CLIENTE_SIN_SECRETO: "Cliente activo sin secreto PPPoE",
    Kind.ACTIVO_SIN_SERVICIO: "Cliente activo con el secreto deshabilitado",
    Kind.BAJA_CON_ONU: "Cliente dado de baja con la ONU todavía autorizada",
    Kind.SERIAL_POR_REGISTRAR: "Serial por registrar en el CRM",
}


@dataclass(frozen=True)
class Finding:
    kind: Kind
    severity: Severity
    detail: str
    suggestion: str
    refs: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return KIND_TITLES[self.kind]


@dataclass(frozen=True)
class ReconInput:
    onus: list[OnuRecord] = field(default_factory=list)
    secrets: list[PppoeSecret] = field(default_factory=list)
    sessions: list[PppoeSession] = field(default_factory=list)
    customers: list[CrmCustomer] = field(default_factory=list)
