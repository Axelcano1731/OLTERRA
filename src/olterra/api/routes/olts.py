"""OLT: alta (con credenciales en la bóveda e IP NAT única) y consultas de solo lectura."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.schemas import OltCreate, OltOut, PlanOut, QueryRequest
from olterra.api.state import audit
from olterra.db.models import Credential as CredentialRow
from olterra.db.models import Olt, PlanRun, TunnelRouter
from olterra.db.models import Tenant as TenantRow
from olterra.drivers import get_driver
from olterra.drivers.base import Access, CliMode, CommandCall
from olterra.executor.plan import Credential, Priority, Target
from olterra.orchestrator import build_read_plan
from olterra.security.vault import Vault

router = APIRouter(prefix="/v1/olts", tags=["OLT"])


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


@router.post("", response_model=OltOut, status_code=status.HTTP_201_CREATED)
async def create_olt(body: OltCreate, ctx: Tenant, state: State) -> Olt:
    ctx.require("olt:write")
    credential = Credential(
        username=body.username,
        password=body.password,
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
                },
                source_ip=ctx.client_ip,
            )
            await session.refresh(olt)
            return olt
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya existe una OLT con ese nombre o esa IP"
        ) from exc


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
        placeholders = set(template.placeholders())
        if "onu" in placeholders:
            if not samples:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"'{key}' necesita al menos una ONU (PON:ONU)"
                )
            calls += [CommandCall(key, {"pon": p, "onu": o}) for p, o in samples]
        elif template.mode is CliMode.PON or "pon" in placeholders:
            if not body.pon:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"'{key}' necesita al menos un PON"
                )
            calls += [CommandCall(key, {"pon": p}) for p in body.pon]
        elif placeholders:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{key}' necesita parámetros que esta consulta no admite",
            )
        else:
            calls.append(CommandCall(key))
    return calls


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
