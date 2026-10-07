"""Aprovisionamiento: plantillas, alta de ONU, reinicio y borrado.

Cada escritura es un plan que se detiene al primer error, guarda en flash y termina leyendo el
estado del PON para verificar. Las claves del cliente (PPPoE, WiFi) van solo en la credencial
sellada al ejecutor: ni el plan guardado, ni NATS, ni la bitácora las ven.

Mientras un comando no tenga captura de laboratorio (``verified=False``) la API se niega a
correrlo y dice cuál falta, salvo con ``OLTERRA_ALLOW_UNVERIFIED_WRITES`` (laboratorio).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.routes.olts import executor_key, olt_access, publish_plan, record_plan
from olterra.api.schemas import AuthorizeRequest, OnuRef, TemplateIn, TemplateOut, WritePlanOut
from olterra.api.state import audit
from olterra.db.models import ProvisionTemplate
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.drivers.vsol_gpon.provisioning import TemplateBody, authorize_calls
from olterra.executor.plan import Credential
from olterra.orchestrator import build_write_plan, unverified_writes

router = APIRouter(tags=["Aprovisionamiento"])


# --- Plantillas -----------------------------------------------------------------------------


async def _get_template(session: Any, template_id: UUID) -> ProvisionTemplate:
    template = await session.get(ProvisionTemplate, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    return template


def _duplicate(exc: IntegrityError) -> HTTPException:
    if "provision_templates_tenant_id_name_key" in str(exc.orig):
        return HTTPException(status.HTTP_409_CONFLICT, "Ya hay una plantilla con ese nombre")
    raise exc


@router.get("/v1/provision-templates", response_model=list[TemplateOut])
async def list_templates(ctx: Tenant) -> list[ProvisionTemplate]:
    ctx.require("olt:read")
    async with ctx.session() as session:
        rows = await session.execute(select(ProvisionTemplate).order_by(ProvisionTemplate.name))
        return list(rows.scalars())


@router.get("/v1/provision-templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: UUID, ctx: Tenant) -> ProvisionTemplate:
    ctx.require("olt:read")
    async with ctx.session() as session:
        return await _get_template(session, template_id)


@router.post(
    "/v1/provision-templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_template(body: TemplateIn, ctx: Tenant) -> ProvisionTemplate:
    ctx.require("olt:write")
    try:
        async with ctx.session() as session:
            template = ProvisionTemplate(
                tenant_id=ctx.tenant_id, name=body.name.strip(), body=body.body.model_dump()
            )
            session.add(template)
            await session.flush()
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="template.create",
                target_type="provision_template",
                target_id=str(template.id),
                after={"name": template.name, "body": template.body},
                source_ip=ctx.client_ip,
            )
            await session.refresh(template)
            return template
    except IntegrityError as exc:
        raise _duplicate(exc) from exc


@router.put("/v1/provision-templates/{template_id}", response_model=TemplateOut)
async def update_template(template_id: UUID, body: TemplateIn, ctx: Tenant) -> ProvisionTemplate:
    ctx.require("olt:write")
    try:
        async with ctx.session() as session:
            template = await _get_template(session, template_id)
            before = {"name": template.name, "body": template.body}
            template.name = body.name.strip()
            template.body = body.body.model_dump()
            template.updated_at = func.now()
            await session.flush()
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="template.update",
                target_type="provision_template",
                target_id=str(template.id),
                before=before,
                after={"name": template.name, "body": template.body},
                source_ip=ctx.client_ip,
            )
            await session.refresh(template)
            return template
    except IntegrityError as exc:
        raise _duplicate(exc) from exc


@router.delete("/v1/provision-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: UUID, ctx: Tenant) -> None:
    ctx.require("olt:write")
    async with ctx.session() as session:
        template = await _get_template(session, template_id)
        before = {"name": template.name, "body": template.body}
        await session.delete(template)
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="template.delete",
            target_type="provision_template",
            target_id=str(template_id),
            before=before,
            source_ip=ctx.client_ip,
        )


# --- Escrituras sobre ONU -------------------------------------------------------------------


async def _write(
    olt_id: UUID,
    ctx: Any,
    state: Any,
    *,
    action: str,
    calls: list[CommandCall],
    verify: list[CommandCall],
    audit_after: dict[str, Any],
    secrets: dict[str, Any] | None = None,
    template_id: UUID | None = None,
) -> WritePlanOut:
    ctx.require("onu:write")
    key = executor_key(state)
    state.require_bus()
    allow = bool(state.settings.allow_unverified_writes)
    async with ctx.session() as session:
        olt, credential, target = await olt_access(session, ctx, state, olt_id)
        if template_id is not None:
            template = await _get_template(session, template_id)
            if template.driver != olt.driver:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "La plantilla es de otro tipo de OLT"
                )
        driver = get_driver(olt.driver)
        sealed: Credential = credential.model_copy(update=secrets or {})
        plan = build_write_plan(
            driver,
            tenant_id=ctx.tenant_id,
            olt_id=olt.id,
            target=target,
            calls=calls,
            verify=verify,
            credential=sealed,
            executor_public_key=key,
            model=olt.model,
            firmware=olt.firmware,
            allow_unverified=allow,
        )
        pending = unverified_writes(driver, calls, olt.model, olt.firmware)
        created_at = await record_plan(session, ctx, olt, plan, [*calls, *verify])
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action=action,
            target_type="olt",
            target_id=str(olt.id),
            # Nunca claves: solo qué se hizo, dónde y con qué plan.
            after={**audit_after, "plan_id": str(plan.plan_id), "sin_verificar": pending},
            source_ip=ctx.client_ip,
        )
    await publish_plan(ctx, state, plan)
    return WritePlanOut(
        plan_id=plan.plan_id,
        olt_id=olt_id,
        status="queued",
        created_at=created_at,
        unverified=pending,
    )


@router.post(
    "/v1/olts/{olt_id}/onus/authorize",
    response_model=WritePlanOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def authorize_onu(
    olt_id: UUID, body: AuthorizeRequest, ctx: Tenant, state: State
) -> WritePlanOut:
    """Autoriza una ONU con una plantilla: VLAN, perfiles y, si la plantilla lo dice, PPPoE y WiFi."""
    async with ctx.session() as session:
        template = TemplateBody.model_validate(
            (await _get_template(session, body.template_id)).body
        )
    calls = authorize_calls(template, body)
    return await _write(
        olt_id,
        ctx,
        state,
        action="onu.authorize",
        calls=calls,
        verify=[CommandCall("onu.state", {"pon": body.pon})],
        audit_after={
            "pon": body.pon,
            "onu": body.onu,
            "serial": body.serial,
            "description": body.description,
            "template_id": str(body.template_id),
            "pppoe_user": body.pppoe_user,
            "wifi_ssid": body.wifi_ssid,
        },
        secrets={"pppoe_password": body.pppoe_password, "wifi_key": body.wifi_key},
        template_id=body.template_id,
    )


@router.post(
    "/v1/olts/{olt_id}/onus/reboot",
    response_model=WritePlanOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reboot_onu(olt_id: UUID, body: OnuRef, ctx: Tenant, state: State) -> WritePlanOut:
    where = {"pon": body.pon, "onu": body.onu}
    return await _write(
        olt_id,
        ctx,
        state,
        action="onu.reboot",
        calls=[CommandCall("onu.reboot", where)],
        verify=[],
        audit_after=where,
    )


@router.post(
    "/v1/olts/{olt_id}/onus/delete",
    response_model=WritePlanOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_onu(olt_id: UUID, body: OnuRef, ctx: Tenant, state: State) -> WritePlanOut:
    """Desautoriza la ONU (se borra su configuración en la OLT) y guarda en flash."""
    where = {"pon": body.pon, "onu": body.onu}
    return await _write(
        olt_id,
        ctx,
        state,
        action="onu.delete",
        calls=[CommandCall("onu.delete", where), CommandCall("config.save")],
        verify=[CommandCall("onu.state", {"pon": body.pon})],
        audit_after=where,
    )
