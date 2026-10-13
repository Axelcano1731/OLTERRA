"""Modelos ORM. El esquema manda en la migración (``migrations/versions``); esto lo refleja.

Toda tabla con ``tenant_id`` tiene RLS. Las restricciones de unicidad que deben
valer entre tenants (IP del overlay, IP NAT de cada OLT) se cumplen igual: un
índice único ve todas las filas aunque la aplicación solo vea las suyas.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    FetchedValue,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    # Lo asigna la secuencia tenant_net_index_seq (ver la migración).
    net_index: Mapped[int] = mapped_column(Integer, unique=True, server_default=FetchedValue())
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = _created()


class TenantKey(Base):
    __tablename__ = "tenant_keys"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TunnelRouter(Base):
    __tablename__ = "tunnel_routers"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    peer_index: Mapped[int] = mapped_column(SmallInteger)
    overlay_ip: Mapped[str] = mapped_column(INET, unique=True)
    transport: Mapped[str] = mapped_column(Text, server_default="wireguard")  # wireguard | sstp
    wg_public_key: Mapped[str | None] = mapped_column(Text)  # solo WireGuard
    ppp_user: Mapped[str | None] = mapped_column(Text, unique=True)  # solo SSTP
    routeros_version: Mapped[str | None] = mapped_column(Text)
    last_handshake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Olt(Base):
    __tablename__ = "olts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    driver: Mapped[str] = mapped_column(Text, server_default="vsol-gpon")
    model: Mapped[str | None] = mapped_column(Text)
    firmware: Mapped[str | None] = mapped_column(Text)
    router_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tunnel_routers.id", ondelete="SET NULL")
    )
    real_ip: Mapped[str | None] = mapped_column(INET)
    nat_index: Mapped[int | None] = mapped_column(SmallInteger)
    nat_ip: Mapped[str | None] = mapped_column(INET, unique=True)
    ssh_port: Mapped[int] = mapped_column(Integer, server_default="22")
    snmp_port: Mapped[int] = mapped_column(Integer, server_default="161")
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    pon_ports: Mapped[int | None] = mapped_column(SmallInteger)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Monitoreo: cuándo se pidió el último sondeo, cuál está en curso y cuándo terminó uno bien.
    monitor_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitor_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    monitored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Onu(Base):
    __tablename__ = "onus"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    pon: Mapped[int] = mapped_column(SmallInteger)
    onu: Mapped[int] = mapped_column(SmallInteger)
    serial: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(Text)
    rx_dbm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    tx_dbm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    olt_rx_dbm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    distance_m: Mapped[int | None] = mapped_column(Integer)
    customer_ref: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class PlanRun(Base):
    __tablename__ = "plan_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    requested_by: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger)
    access: Mapped[str] = mapped_column(Text)
    calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    commands: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    username: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="admin")
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default="true")
    failed_logins: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_ip: Mapped[str | None] = mapped_column(INET)


class ProvisionJob(Base):
    __tablename__ = "provision_jobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    step: Mapped[str] = mapped_column(Text)
    template: Mapped[dict[str, Any]] = mapped_column(JSONB)
    template_name: Mapped[str | None] = mapped_column(Text)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    secrets: Mapped[bytes | None] = mapped_column(LargeBinary)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    current_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OltBackup(Base):
    __tablename__ = "olt_backups"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    requested_by: Mapped[str] = mapped_column(Text)
    # "show running-config" con las claves tapadas, comprimido y cifrado con la llave del ISP.
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    sha256: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    line_count: Mapped[int | None] = mapped_column(Integer)
    changed: Mapped[bool | None] = mapped_column(Boolean)
    added: Mapped[int | None] = mapped_column(Integer)
    removed: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnuStatus(Base):
    """Lo último que se sabe de cada ONU (lo reescribe cada sondeo del monitoreo)."""

    __tablename__ = "onu_status"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE"), primary_key=True
    )
    pon: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    onu: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    serial: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(Text)
    online: Mapped[bool] = mapped_column(Boolean)
    rx_dbm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OnuSample(Base):
    __tablename__ = "onu_samples"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE"), primary_key=True
    )
    pon: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    onu: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    online: Mapped[bool] = mapped_column(Boolean)
    rx_dbm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class Alarm(Base):
    __tablename__ = "alarms"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    # 0 = toda la OLT o todo el PON.
    pon: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    onu: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MapBox(Base):
    """Caja del mapa FTTH: NAP (con puertos para clientes), CEO (empalme) u ODF (cabecera)."""

    __tablename__ = "map_boxes"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    ports: Mapped[int] = mapped_column(SmallInteger, server_default="8")
    splitter: Mapped[str | None] = mapped_column(Text)
    olt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="SET NULL")
    )
    pon: Mapped[int | None] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MapBoxPort(Base):
    """Qué ONU (cliente) está en cada puerto de una caja NAP."""

    __tablename__ = "map_box_ports"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    box_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("map_boxes.id", ondelete="CASCADE"), primary_key=True
    )
    port: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    olt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("olts.id", ondelete="CASCADE")
    )
    pon: Mapped[int] = mapped_column(SmallInteger)
    onu: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = _created()


class MapPole(Base):
    __tablename__ = "map_poles"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    owner: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class MapCable(Base):
    __tablename__ = "map_cables"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    fibers: Mapped[int] = mapped_column(SmallInteger, server_default="12")
    path: Mapped[Any] = mapped_column(Geometry("LINESTRING", srid=4326))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProvisionTemplate(Base):
    __tablename__ = "provision_templates"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    driver: Mapped[str] = mapped_column(Text, server_default="vsol-gpon")
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Claves de las cuentas de la ONU (JSON cifrado con la llave del ISP).
    secrets: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = _created()


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    requested_by: Mapped[str] = mapped_column(Text)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text, server_default="api")  # api | upload | demo
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _created()
