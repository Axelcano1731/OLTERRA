"""OLT: alta (con credenciales en la bóveda e IP NAT única) y consultas de solo lectura."""

from __future__ import annotations

import json
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
from olterra.db.models import Tenant as TenantRow
from olterra.drivers import get_driver
from olterra.drivers.base import Access, CliMode, CommandCall, CommandTemplate
from olterra.executor.plan import Credential, Priority, Target
from olterra.orchestrator import PARSERS, build_read_plan
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


def _lowest_free(used: set[int], start: int) -> int:
    index = start
    while index in used:
        index += 1
    return index


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
    return OltDefaults(username=settings.vsol_default_username, password_configured=configured)


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
                model=body.model,
                firmware=body.firmware,
                ssh_port=body.ssh_port,
                snmp_port=body.snmp_port,
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
                net_index = (
                    await session.execute(
                        select(TenantRow.net_index).where(TenantRow.id == ctx.tenant_id)
                    )
                ).scalar_one()
                used = {
                    index
                    for index in (await session.execute(select(Olt.nat_index))).scalars()
                    if index is not None
                }
                olt.router_id = body.router_id
                olt.nat_index = _lowest_free(used, 0)
                olt.nat_ip = str(state.addresses.nat_address(net_index, olt.nat_index))
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
            "model": olt.model,
            "firmware": olt.firmware,
            "real_ip": str(olt.real_ip).split("/")[0] if olt.real_ip else None,
            "ssh_port": olt.ssh_port,
            "snmp_port": olt.snmp_port,
        }
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
                "model": olt.model,
                "firmware": olt.firmware,
                "real_ip": str(olt.real_ip).split("/")[0] if olt.real_ip else None,
                "ssh_port": olt.ssh_port,
                "snmp_port": olt.snmp_port,
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
                    parsed=template.key in PARSERS,
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
            .where(PlanRun.olt_id == olt_id)
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


@router.post("/{olt_id}/queries", response_model=PlanOut, status_code=status.HTTP_202_ACCEPTED)
async def query_olt(olt_id: UUID, body: QueryRequest, ctx: Tenant, state: State) -> PlanOut:
    """Consulta de solo lectura: arma el plan, lo sella para el ejecutor y lo encola."""
    ctx.require("olt:read")
    executor_key = state.settings.executor_public_key
    if not executor_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Falta OLTERRA_EXECUTOR_PUBLIC_KEY"
        )
    bus = state.require_bus()
    async with ctx.session() as session:
        olt = await _get_olt(session, olt_id)
        host = olt.nat_ip or olt.real_ip
        if host is None or olt.credential_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "La OLT no tiene IP alcanzable o credencial"
            )
        row = await session.get(CredentialRow, olt.credential_id)
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "La credencial de la OLT no existe")
        dek = await state.tenant_dek(session, ctx.tenant_id)
        credential = Credential.model_validate_json(
            Vault.decrypt(ctx.tenant_id, dek, f"credential:{row.id}", row.ciphertext)
        )
        calls = _calls_for(olt.driver, body, olt.model, olt.firmware)
        driver = get_driver(olt.driver)
        plan = build_read_plan(
            driver,
            tenant_id=ctx.tenant_id,
            olt_id=olt.id,
            target=Target(
                host=str(host).split("/")[0],
                ssh_port=olt.ssh_port,
                snmp_port=olt.snmp_port,
                ssh_host_key=olt.ssh_host_key,
            ),
            calls=calls,
            credential=credential,
            executor_public_key=executor_key,
            model=olt.model,
            firmware=olt.firmware,
            priority=Priority.USER,
        )
        run = PlanRun(
            id=plan.plan_id,
            tenant_id=ctx.tenant_id,
            olt_id=olt.id,
            requested_by=ctx.actor,
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
        created_at = run.created_at
    # Se publica DESPUÉS de confirmar la fila: así el resultado siempre encuentra su plan.
    try:
        await bus.publish_plan(plan, state.settings.executor_group)
    except Exception as exc:
        async with ctx.session() as session:
            failed = await session.get(PlanRun, plan.plan_id)
            if failed is not None:
                failed.status = "rejected"
                failed.result = {"error": f"No se pudo encolar: {exc}"}
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo encolar el plan"
        ) from exc
    return PlanOut(plan_id=plan.plan_id, olt_id=olt_id, status="queued", created_at=created_at)
