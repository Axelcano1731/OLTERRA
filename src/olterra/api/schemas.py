"""Esquemas de entrada y salida de la API. Ninguna salida lleva claves."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr
    enable_password: SecretStr | None = None
    snmp_community: SecretStr | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


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
    status: str
    created_at: datetime

    _ips = field_validator("real_ip", "nat_ip", mode="before")(_ip_text)


class QueryRequest(BaseModel):
    commands: list[str] = Field(
        min_length=1, max_length=40, description="Llaves del catálogo de solo lectura"
    )
    pon: list[int] = Field(default_factory=list, description="PON para los comandos por puerto")
    onu: list[str] = Field(
        default_factory=list, description="Muestras PON:ONU para comandos por ONU"
    )


class PlanOut(BaseModel):
    plan_id: UUID
    olt_id: UUID
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None


class RouterCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,32}$")
    routeros_version: str | None = None


class RouterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    peer_index: int
    overlay_ip: str
    wg_public_key: str
    routeros_version: str | None
    created_at: datetime


class RouterScriptOut(BaseModel):
    router: RouterOut
    isp_script: str = Field(
        description="Para pegar en el MikroTik del ISP. Contiene su llave privada."
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


class ReconOut(BaseModel):
    id: UUID
    created_at: datetime
    counts: dict[str, int]
    findings: list[dict[str, Any]]
