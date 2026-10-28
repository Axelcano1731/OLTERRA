"""OLT: alta (con credenciales en la bóveda) y consultas de solo lectura.

Una OLT detrás de un MikroTik del túnel se alcanza por él (la puerta): el plan lleva la IP
real de la OLT y ``Target.via`` con el router. Las OLT de un router con script viejo siguen
por su IP NAT (198.19.x.x) hasta que el router se rote.
"""

from __future__ import annotations

import json
from datetime import datetime
from ipaddress import IPv4Network
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.schemas import (
    CommandOut,
    DriverOption,
    OltCreate,
    OltCreated,
    OltDefaults,
    OltOut,
    OltUpdate,
    PlanOut,
    PlanSummary,
    QueryRequest,
)
from olterra.api.state import audit
from olterra.db.models import Credential as CredentialRow
from olterra.db.models import Olt, PlanRun, TunnelRouter
from olterra.db.session import tenant_session
from olterra.drivers import DRIVERS, get_driver
from olterra.drivers.base import Access, CliMode, CommandCall, CommandTemplate
from olterra.executor.plan import Credential, Gateway, Plan, Priority, Target
from olterra.orchestrator import build_read_plan
from olterra.security.vault import Vault

router = APIRouter(prefix="/v1/olts", tags=["OLT"])


def _check_real_ip(state: Any, real_ip: Any) -> None:
    """Confusión real: poner la IP del router en el túnel (198.18.x) en vez de la de la OLT."""
    pools = [
        IPv4Network(state.settings.tunnel_peer_pool),
        IPv4Network(state.settings.tunnel_nat_pool),
    ]
    if real_ip is not None and any(real_ip in pool for pool in pools):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{real_ip} es una IP del túnel. Va la IP que la OLT tiene en la red del ISP,"
            " la misma con la que se abre su página web (por ejemplo 192.168.1.50).",
        )


async def _get_olt(session: Any, olt_id: UUID) -> Olt:
    olt = await session.get(Olt, olt_id)
    if olt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")
    return olt


@router.get("/defaults", response_model=OltDefaults)
async def olt_defaults(ctx: Tenant, state: State) -> OltDefaults:
    """Si hay credenciales de fábrica para una OLT nueva. Nunca devuelve la clave."""
    ctx.require("olt:read")
    settings = state.settings
    configured = settings.vsol_default_password is not None
    drivers = [
        DriverOption(
            key=driver.key,
            label=driver.label or driver.vendor,
            writes=any(c.access is not Access.READ for c in driver.commands.values()),
        )
        for driver in DRIVERS.values()
    ]
    return OltDefaults(
        username=settings.vsol_default_username,
        password_configured=configured,
        drivers=drivers,
    )


@router.get("", response_model=list[OltOut])
async def list_olts(ctx: Tenant) -> list[Olt]:
    ctx.require("olt:read")
    async with ctx.session() as session:
        return list((await session.execute(select(Olt).order_by(Olt.name))).scalars())


@router.get("/{olt_id}", response_model=OltOut)
async def get_olt(olt_id: UUID, ctx: Tenant) -> Olt:
    ctx.require("olt:read")
    async with ctx.session() as session:
        return await _get_olt(session, olt_id)


def _login(body: OltCreate, state: State) -> tuple[str, SecretStr, bool]:
    """Usuario y clave de la OLT; sin clave, los de fábrica del servidor (OLT nueva)."""
    settings = state.settings
    password = body.password
    if password is not None and password.get_secret_value():
        if not body.username:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta el usuario de la OLT")
        return body.username, password, False
    if body.driver != "vsol-gpon":
        # Las credenciales de fábrica del servidor son las de VSOL.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta el usuario y la clave de la OLT")
    if settings.vsol_default_password is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Falta la clave de la OLT (este servidor no tiene una de fábrica configurada)",
        )
    username = body.username or settings.vsol_default_username
    return username, settings.vsol_default_password, True


@router.post("", response_model=OltCreated, status_code=status.HTTP_201_CREATED)
async def create_olt(body: OltCreate, ctx: Tenant, state: State) -> OltCreated:
    ctx.require("olt:write")
    _check_real_ip(state, body.real_ip)
    username, password, used_default = _login(body, state)
    credential = Credential(
        username=username,
        password=password,
        enable_password=body.enable_password,
        snmp_community=body.snmp_community,
    )
    try:
        async with ctx.session() as session:
            dek = await state.tenant_dek(session, ctx.tenant_id)
            credential_id = uuid4()
            session.add(
                CredentialRow(
                    id=credential_id,
                    tenant_id=ctx.tenant_id,
                    kind="olt",
                    label=f"OLT {body.name}",
                    ciphertext=Vault.encrypt(
                        ctx.tenant_id, dek, f"credential:{credential_id}", credential.reveal_json()
                    ),
                )
            )
            olt = Olt(
                tenant_id=ctx.tenant_id,
                name=body.name,
                driver=body.driver,
                model=body.model,
                firmware=body.firmware,
                ssh_port=body.ssh_port,
                snmp_port=body.snmp_port,
                cli_protocol=body.cli_protocol,
                telnet_port=body.telnet_port,
                credential_id=credential_id,
                real_ip=str(body.real_ip) if body.real_ip else None,
            )
            if body.latitude is not None and body.longitude is not None:
                olt.location = func.ST_SetSRID(
                    func.ST_MakePoint(body.longitude, body.latitude), 4326
                )
            if body.router_id is not None:
                if body.real_ip is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "Con router_id hace falta real_ip"
                    )
                if await session.get(TunnelRouter, body.router_id) is None:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Router del túnel no encontrado")
                # Sin IP NAT: Olterra llega a la IP real de la OLT desde el MikroTik (la puerta).
                olt.router_id = body.router_id
            session.add(olt)
            await session.flush()
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="olt.create",
                target_type="olt",
                target_id=str(olt.id),
                after={
                    "name": olt.name,
                    "model": olt.model,
                    "nat_ip": olt.nat_ip,
                    "real_ip": olt.real_ip,
                    "used_default_credentials": used_default,
                },
                source_ip=ctx.client_ip,
            )
            await session.refresh(olt)
            return OltCreated(
                **OltOut.model_validate(olt).model_dump(), used_default_credentials=used_default
            )
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya existe una OLT con ese nombre o esa IP"
        ) from exc


@router.delete("/{olt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_olt(olt_id: UUID, ctx: Tenant) -> None:
    """Borra la OLT, su credencial cifrada y su historial de consultas.

    Quedan sus ONU y planes borrados en cascada; la bitácora conserva quién la borró y cómo era.
    """
    ctx.require("olt:write")
    async with ctx.session() as session:
        olt = await _get_olt(session, olt_id)
        credential = (
            await session.get(CredentialRow, olt.credential_id) if olt.credential_id else None
        )
        before = {
            "name": olt.name,
            "model": olt.model,
            "real_ip": str(olt.real_ip).split("/")[0] if olt.real_ip else None,
            "nat_ip": str(olt.nat_ip).split("/")[0] if olt.nat_ip else None,
            "router_id": str(olt.router_id) if olt.router_id else None,
        }
        await session.delete(olt)
        await session.flush()
        if credential is not None:
            await session.delete(credential)
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="olt.delete",
            target_type="olt",
            target_id=str(olt_id),
            before=before,
            source_ip=ctx.client_ip,
        )


@router.patch("/{olt_id}", response_model=OltOut)
async def update_olt(olt_id: UUID, body: OltUpdate, ctx: Tenant, state: State) -> Olt:
    """Corrige la OLT: modelo, firmware, IP, puertos o credenciales (se vuelven a cifrar).

    Si cambia la IP de una OLT detrás de un router, hay que rotar el router: su script
    publica la IP nueva.
    """
    ctx.require("olt:write")
    changes = body.model_fields_set
    _check_real_ip(state, body.real_ip)
    async with ctx.session() as session:
        olt = await _get_olt(session, olt_id)
        before = {
            "driver": olt.driver,
            "model": olt.model,
            "firmware": olt.firmware,
            "real_ip": str(olt.real_ip).split("/")[0] if olt.real_ip else None,
            "ssh_port": olt.ssh_port,
            "snmp_port": olt.snmp_port,
            "cli_protocol": olt.cli_protocol,
            "telnet_port": olt.telnet_port,
        }
        if body.driver is not None:
            olt.driver = body.driver
        if "model" in changes:
            olt.model = (body.model or "").strip() or None
        if "firmware" in changes:
            olt.firmware = (body.firmware or "").strip() or None
        if "real_ip" in changes:
            if body.real_ip is None and olt.router_id is not None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Una OLT detrás de un router necesita su IP"
                )
            olt.real_ip = str(body.real_ip) if body.real_ip else None
        if body.ssh_port is not None:
            olt.ssh_port = body.ssh_port
        if body.snmp_port is not None:
            olt.snmp_port = body.snmp_port
        if body.cli_protocol is not None:
            olt.cli_protocol = body.cli_protocol
        if body.telnet_port is not None:
            olt.telnet_port = body.telnet_port
        secrets = sorted(changes & {"username", "password", "enable_password", "snmp_community"})
        if secrets:
            if olt.credential_id is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "La OLT no tiene credencial")
            row = await session.get(CredentialRow, olt.credential_id)
            if row is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "La credencial de la OLT no existe")
            dek = await state.tenant_dek(session, ctx.tenant_id)
            aad = f"credential:{row.id}"
            current = Credential.model_validate_json(
                Vault.decrypt(ctx.tenant_id, dek, aad, row.ciphertext)
            )
            update: dict[str, Any] = {}
            if body.username is not None:
                update["username"] = body.username
            if body.password is not None:
                update["password"] = body.password
            for name in ("enable_password", "snmp_community"):
                if name in changes:
                    value = getattr(body, name)
                    update[name] = value if value and value.get_secret_value() else None
            row.ciphertext = Vault.encrypt(
                ctx.tenant_id, dek, aad, current.model_copy(update=update).reveal_json()
            )
        await session.flush()
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="olt.update",
            target_type="olt",
            target_id=str(olt.id),
            before=before,
            # De las credenciales solo se anota cuáles cambiaron, nunca su valor.
            after={
                "driver": olt.driver,
                "model": olt.model,
                "firmware": olt.firmware,
                "real_ip": str(olt.real_ip).split("/")[0] if olt.real_ip else None,
                "ssh_port": olt.ssh_port,
                "snmp_port": olt.snmp_port,
                "cli_protocol": olt.cli_protocol,
                "telnet_port": olt.telnet_port,
                "credenciales": secrets,
            },
            source_ip=ctx.client_ip,
        )
        await session.refresh(olt)
        return olt


def query_scope(template: CommandTemplate) -> Literal["olt", "pon", "onu"] | None:
    """Qué pide un comando en una consulta: nada, un PON o un PON:ONU.

    None si pide otros parámetros (perfil, VLAN...): esos no se consultan por aquí.
    """
    placeholders = set(template.placeholders())
    if placeholders - {"pon", "onu"}:
        return None
    if "onu" in placeholders:
        return "onu"
    if template.mode is CliMode.PON or "pon" in placeholders:
        return "pon"
    return "olt"


def _calls_for(
    driver_key: str, body: QueryRequest, model: str | None, firmware: str | None
) -> list[CommandCall]:
    driver = get_driver(driver_key)
    samples = []
    for item in body.onu:
        pon, _, onu = item.partition(":")
        if not (pon.isdigit() and onu.isdigit()):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"ONU '{item}' no es PON:ONU")
        samples.append((int(pon), int(onu)))
    calls: list[CommandCall] = []
    for key in body.commands:
        if key not in driver.commands:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Comando desconocido: {key}")
        template = driver.command(key, model, firmware)
        if template.access is not Access.READ:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"'{key}' escribe en la OLT; aquí solo lecturas"
            )
        scope = query_scope(template)
        if scope == "onu":
            if not samples:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"'{key}' necesita al menos una ONU (PON:ONU)"
                )
            calls += [CommandCall(key, {"pon": p, "onu": o}) for p, o in samples]
        elif scope == "pon":
            if not body.pon:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"'{key}' necesita al menos un PON"
                )
            calls += [CommandCall(key, {"pon": p}) for p in body.pon]
        elif scope is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{key}' necesita parámetros que esta consulta no admite",
            )
        else:
            calls.append(CommandCall(key))
    return calls


@router.get("/{olt_id}/commands", response_model=list[CommandOut])
async def list_commands(olt_id: UUID, ctx: Tenant) -> list[CommandOut]:
    """Lecturas que acepta ``/queries`` para esta OLT, con la sintaxis de su modelo y firmware."""
    ctx.require("olt:read")
    async with ctx.session() as session:
        olt = await _get_olt(session, olt_id)
        driver_key, model, firmware = olt.driver, olt.model, olt.firmware
    driver = get_driver(driver_key)
    commands = []
    for base in driver.read_only_catalog():
        template = driver.command(base.key, model, firmware)
        scope = query_scope(template)
        if scope is not None:
            commands.append(
                CommandOut(
                    key=template.key,
                    command=template.template,
                    scope=scope,
                    verified=template.verified,
                    parsed=template.key in driver.parsers,
                    notes=template.notes,
                )
            )
    return commands


@router.get("/{olt_id}/plans", response_model=list[PlanSummary])
async def list_plans(
    olt_id: UUID, ctx: Tenant, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[PlanSummary]:
    """Las consultas más recientes de la OLT, sin su resultado (se pide con ``/v1/plans``)."""
    ctx.require("olt:read")
    async with ctx.session() as session:
        await _get_olt(session, olt_id)
        rows = await session.execute(
            select(
                PlanRun.id,
                PlanRun.status,
                PlanRun.requested_by,
                PlanRun.calls,
                PlanRun.created_at,
                PlanRun.finished_at,
            )
            # Los sondeos del monitoreo no son consultas de nadie (y se borran al procesarse).
            .where(PlanRun.olt_id == olt_id, PlanRun.requested_by != "olterra:monitoreo")
            .order_by(PlanRun.created_at.desc())
            .limit(limit)
        )
        return [
            PlanSummary(
                plan_id=row.id,
                status=row.status,
                requested_by=row.requested_by,
                commands=list(dict.fromkeys(call["key"] for call in row.calls)),
                created_at=row.created_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ]


def executor_key(state: Any) -> str:
    key = state.settings.executor_public_key
    if not key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Falta OLTERRA_EXECUTOR_PUBLIC_KEY"
        )
    return str(key)


async def olt_access(
    session: Any, tenant_id: UUID, state: Any, olt_id: UUID
) -> tuple[Olt, Credential, Target]:
    """La OLT, su credencial descifrada (solo en memoria) y a dónde conectarse.

    Detrás de un MikroTik que ya es puerta: la IP real de la OLT, por el router. Detrás de uno
    con script viejo: la IP NAT de la OLT, si la tiene. Sin router: su IP directa.
    """
    olt = await _get_olt(session, olt_id)
    if olt.credential_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La OLT no tiene credencial")
    row = await session.get(CredentialRow, olt.credential_id)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La credencial de la OLT no existe")
    dek = await state.tenant_dek(session, tenant_id)
    credential = Credential.model_validate_json(
        Vault.decrypt(tenant_id, dek, f"credential:{row.id}", row.ciphertext)
    )
    via: Gateway | None = None
    host = olt.real_ip
    gateway = await session.get(TunnelRouter, olt.router_id) if olt.router_id else None
    if gateway is not None and gateway.gateway_credential_id is not None:
        stored = await session.get(CredentialRow, gateway.gateway_credential_id)
        if stored is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Falta la clave de Olterra del MikroTik {gateway.name}: rótalo en Túnel",
            )
        gateway_login = Credential.model_validate_json(
            Vault.decrypt(tenant_id, dek, f"credential:{stored.id}", stored.ciphertext)
        )
        credential.gateway_password = gateway_login.password
        via = Gateway(
            host=str(gateway.overlay_ip).split("/")[0],
            port=gateway.ssh_port,
            username=gateway_login.username or "olterra",
            name=gateway.name,
        )
    elif gateway is not None:
        if olt.nat_ip is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"El MikroTik {gateway.name} tiene el script viejo de Olterra: en Túnel, rótalo e"
                " importa el script nuevo una última vez. Desde ahí, toda OLT nueva funciona sola.",
            )
        host = olt.nat_ip
    if host is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La OLT no tiene IP")
    target = Target(
        host=str(host).split("/")[0],
        ssh_port=olt.ssh_port,
        snmp_port=olt.snmp_port,
        ssh_host_key=olt.ssh_host_key,
        cli_protocol="telnet" if olt.cli_protocol == "telnet" else "ssh",
        telnet_port=olt.telnet_port,
        via=via,
    )
    return olt, credential, target


async def record_plan(
    session: Any, tenant_id: UUID, actor: str, olt: Olt, plan: Plan, calls: list[CommandCall]
) -> datetime:
    """Guarda el plan (sin claves: los pasos llevan marcadores) antes de encolarlo."""
    driver = get_driver(olt.driver)
    run = PlanRun(
        id=plan.plan_id,
        tenant_id=tenant_id,
        olt_id=olt.id,
        requested_by=actor,
        priority=int(plan.priority),
        access=plan.access,
        calls=json.loads(
            json.dumps(
                [
                    {
                        "key": c.key,
                        "params": dict(c.params),
                        "command": driver.command(c.key, olt.model, olt.firmware).render(
                            **c.params
                        ),
                    }
                    for c in calls
                ]
            )
        ),
        commands=[getattr(step, "command", "") for step in plan.steps],
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run.created_at


async def publish_plan(state: Any, plan: Plan) -> None:
    """Se publica DESPUÉS de confirmar la fila: así el resultado siempre encuentra su plan."""
    bus = state.require_bus()
    try:
        await bus.publish_plan(plan, state.settings.executor_group)
    except Exception as exc:
        async with tenant_session(state.sessions, plan.tenant_id) as session:
            failed = await session.get(PlanRun, plan.plan_id)
            if failed is not None:
                failed.status = "rejected"
                failed.result = {"error": f"No se pudo encolar: {exc}"}
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo encolar el plan"
        ) from exc


@router.post("/{olt_id}/queries", response_model=PlanOut, status_code=status.HTTP_202_ACCEPTED)
async def query_olt(olt_id: UUID, body: QueryRequest, ctx: Tenant, state: State) -> PlanOut:
    """Consulta de solo lectura: arma el plan, lo sella para el ejecutor y lo encola."""
    ctx.require("olt:read")
    key = executor_key(state)
    state.require_bus()
    async with ctx.session() as session:
        olt, credential, target = await olt_access(session, ctx.tenant_id, state, olt_id)
        calls = _calls_for(olt.driver, body, olt.model, olt.firmware)
        plan = build_read_plan(
            get_driver(olt.driver),
            tenant_id=ctx.tenant_id,
            olt_id=olt.id,
            target=target,
            calls=calls,
            credential=credential,
            executor_public_key=key,
            model=olt.model,
            firmware=olt.firmware,
            priority=Priority.USER,
        )
        created_at = await record_plan(session, ctx.tenant_id, ctx.actor, olt, plan, calls)
    await publish_plan(state, plan)
    return PlanOut(plan_id=plan.plan_id, olt_id=olt_id, status="queued", created_at=created_at)
