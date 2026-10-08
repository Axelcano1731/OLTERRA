"""Aprovisionamiento: planes (plantillas), búsqueda de ONU, alta, internet/WiFi, reinicio y borrado.

El alta y "configurar internet" son trabajos (``api/jobs.py``) que avanzan solos. Reiniciar y
borrar son un plan cada uno, que se detiene al primer error y guarda en flash. Las claves del
cliente (PPPoE, WiFi) viajan solo en la credencial sellada al ejecutor y, mientras el trabajo las
necesita, cifradas con la llave del ISP: ni el plan guardado, ni NATS, ni la bitácora las ven.

Mientras un comando no tenga captura de laboratorio (``verified=False``) la API se niega a
correrlo y dice cuál falta, salvo con ``OLTERRA_ALLOW_UNVERIFIED_WRITES`` (laboratorio).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from olterra.api import jobs
from olterra.api.deps import State, Tenant
from olterra.api.routes.olts import executor_key, olt_access, publish_plan, record_plan
from olterra.api.schemas import (
    AuthorizeIn,
    ConfigureIn,
    JobOut,
    JobStep,
    OnuRef,
    PlanOut,
    TemplateIn,
    TemplateOut,
    WritePlanOut,
)
from olterra.api.state import audit
from olterra.db.models import Olt, ProvisionJob, ProvisionTemplate
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.drivers.vsol_gpon.parsers import real_equipment_id
from olterra.drivers.vsol_gpon.provisioning import (
    ClientData,
    TemplateBody,
    base_calls,
    clean_label,
    service_calls,
)
from olterra.executor.plan import Credential, Priority
from olterra.orchestrator import build_read_plan, build_write_plan, unverified_writes
from olterra.security.vault import Vault

DEFAULT_PON_PORTS = 4

router = APIRouter(tags=["Aprovisionamiento"])


# --- Plantillas -----------------------------------------------------------------------------


async def _get_template(session: Any, template_id: UUID) -> ProvisionTemplate:
    template = await session.get(ProvisionTemplate, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    return template


_SECRET_LABELS = {
    "onu_admin_password": "la contraseña de administración de la ONU",
    "onu_user_password": "la contraseña de la cuenta normal de la ONU",
}


def _template_aad(template_id: UUID) -> str:
    return f"provision_template:{template_id}"


async def _template_secrets(
    session: Any, state: Any, tenant_id: UUID, template: ProvisionTemplate
) -> dict[str, str]:
    if template.secrets is None:
        return {}
    dek = await state.tenant_dek(session, tenant_id)
    plain = Vault.decrypt(tenant_id, dek, _template_aad(template.id), template.secrets)
    return dict(json.loads(plain))


async def _template_out(
    session: Any, state: Any, tenant_id: UUID, template: ProvisionTemplate
) -> TemplateOut:
    stored = await _template_secrets(session, state, tenant_id, template)
    return TemplateOut(
        id=template.id,
        name=template.name,
        driver=template.driver,
        body=TemplateBody.model_validate(template.body),
        onu_admin_password_set="onu_admin_password" in stored,
        onu_user_password_set="onu_user_password" in stored,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def _store_secrets(
    session: Any, state: Any, tenant_id: UUID, template: ProvisionTemplate, body: TemplateIn
) -> list[str]:
    """Guarda cifradas solo las claves que el plan usa; las que no llegan, se conservan.

    Devuelve los nombres de las claves guardadas (para la bitácora, nunca los valores).
    """
    stored = await _template_secrets(session, state, tenant_id, template)
    given = {
        name: value.get_secret_value()
        for name, value in (
            ("onu_admin_password", body.onu_admin_password),
            ("onu_user_password", body.onu_user_password),
        )
        if value is not None
    }
    management = body.body.management
    needed = management.secret_fields() if management is not None else []
    kept = {name: given.get(name) or stored.get(name) for name in needed}
    missing = [_SECRET_LABELS[name] for name, value in kept.items() if not value]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta " + " y ".join(missing))
    if kept:
        dek = await state.tenant_dek(session, tenant_id)
        data = json.dumps(kept).encode()
        template.secrets = Vault.encrypt(tenant_id, dek, _template_aad(template.id), data)
    else:
        template.secrets = None
    return sorted(kept)


def _duplicate(exc: IntegrityError) -> HTTPException:
    if "provision_templates_tenant_id_name_key" in str(exc.orig):
        return HTTPException(status.HTTP_409_CONFLICT, "Ya hay una plantilla con ese nombre")
    raise exc


@router.get("/v1/provision-templates", response_model=list[TemplateOut])
async def list_templates(ctx: Tenant, state: State) -> list[TemplateOut]:
    ctx.require("olt:read")
    async with ctx.session() as session:
        rows = await session.execute(select(ProvisionTemplate).order_by(ProvisionTemplate.name))
        return [
            await _template_out(session, state, ctx.tenant_id, template)
            for template in rows.scalars()
        ]


@router.get("/v1/provision-templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: UUID, ctx: Tenant, state: State) -> TemplateOut:
    ctx.require("olt:read")
    async with ctx.session() as session:
        template = await _get_template(session, template_id)
        return await _template_out(session, state, ctx.tenant_id, template)


@router.post(
    "/v1/provision-templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_template(body: TemplateIn, ctx: Tenant, state: State) -> TemplateOut:
    ctx.require("olt:write")
    try:
        async with ctx.session() as session:
            template = ProvisionTemplate(
                tenant_id=ctx.tenant_id, name=body.name.strip(), body=body.body.model_dump()
            )
            session.add(template)
            await session.flush()
            saved = await _store_secrets(session, state, ctx.tenant_id, template, body)
            await session.flush()
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="template.create",
                target_type="provision_template",
                target_id=str(template.id),
                after={"name": template.name, "body": template.body, "claves_onu": saved},
                source_ip=ctx.client_ip,
            )
            await session.refresh(template)
            return await _template_out(session, state, ctx.tenant_id, template)
    except IntegrityError as exc:
        raise _duplicate(exc) from exc


@router.put("/v1/provision-templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: UUID, body: TemplateIn, ctx: Tenant, state: State
) -> TemplateOut:
    ctx.require("olt:write")
    try:
        async with ctx.session() as session:
            template = await _get_template(session, template_id)
            before = {"name": template.name, "body": template.body}
            saved = await _store_secrets(session, state, ctx.tenant_id, template, body)
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
                after={"name": template.name, "body": template.body, "claves_onu": saved},
                source_ip=ctx.client_ip,
            )
            await session.refresh(template)
            return await _template_out(session, state, ctx.tenant_id, template)
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
        olt, credential, target = await olt_access(session, ctx.tenant_id, state, olt_id)
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
        created_at = await record_plan(
            session, ctx.tenant_id, ctx.actor, olt, plan, [*calls, *verify]
        )
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
    await publish_plan(state, plan)
    return WritePlanOut(
        plan_id=plan.plan_id,
        olt_id=olt_id,
        status="queued",
        created_at=created_at,
        unverified=pending,
    )


def _check_client(template: TemplateBody, body: AuthorizeIn | ConfigureIn) -> None:
    """Lo que falta o no sirve, antes de tocar la OLT y en palabras del operador."""
    if template.wan is not None and not (body.pppoe_user and body.pppoe_password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Este plan configura internet: faltan el usuario y la clave PPPoE",
        )
    if bool(body.wifi_name) != bool(body.wifi_key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Para el WiFi van el nombre y la clave, o ninguno de los dos",
        )
    if body.wifi_name and template.wifi is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este plan no configura WiFi")


def _secrets(body: AuthorizeIn | ConfigureIn) -> dict[str, str]:
    secrets = {}
    if body.pppoe_password is not None:
        secrets["pppoe_password"] = body.pppoe_password.get_secret_value()
    if body.wifi_key is not None:
        secrets["wifi_key"] = body.wifi_key.get_secret_value()
    return secrets


async def _job_out(session: Any, job_id: UUID) -> JobOut:
    job = await session.get(ProvisionJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabajo no encontrado")
    req = job.request or {}
    detail = job.detail or {}
    return JobOut(
        id=job.id,
        olt_id=job.olt_id,
        kind=job.kind,
        status=job.status,
        step=job.step,
        template_name=job.template_name,
        pon=req.get("pon"),
        onu=req.get("onu"),
        serial=req.get("serial"),
        description=req.get("description"),
        pppoe_user=req.get("pppoe_user"),
        wifi_ssid=req.get("wifi_ssid"),
        equipment_id=req.get("equipment_id"),
        phase=detail.get("phase"),
        rx_dbm=detail.get("rx_dbm"),
        unverified=detail.get("unverified", []),
        steps=[JobStep(**step) for step in jobs.steps_view(job)],
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _unverified(olt: Olt, kind: str, template: TemplateBody, request: dict[str, Any]) -> list[str]:
    """Comandos de escritura que el trabajo va a usar y que no tienen captura de laboratorio.

    Se arman con un cliente de muestra los mismos comandos que armará el trabajo, para decirlo
    ANTES de tocar la OLT y no a mitad del alta.
    """
    sample = ClientData(
        pon=request["pon"],
        onu=request.get("onu") or 1,
        serial=request.get("serial") or "VSOL00000000",
        description=request.get("description") or "x",
        equipment_id="VSOLV000",
        pppoe_user="muestra" if template.wan else None,
        pppoe_password=SecretStr("muestra") if template.wan else None,
        wifi_ssid=request.get("wifi_ssid"),
        wifi_key=SecretStr("muestra-wifi") if request.get("wifi_ssid") else None,
    )
    calls = [
        *(base_calls(template, sample) if kind == "authorize" else []),
        *service_calls(template, sample),
        CommandCall("config.save"),
    ]
    return unverified_writes(get_driver(olt.driver), calls, olt.model, olt.firmware)


async def _start(
    olt_id: UUID,
    ctx: Any,
    state: Any,
    kind: str,
    template_id: UUID,
    request: dict[str, Any],
    secrets: dict[str, str],
) -> JobOut:
    ctx.require("onu:write")
    executor_key(state)
    state.require_bus()
    async with ctx.session() as session:
        olt = await session.get(Olt, olt_id)
        if olt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")
        template = await _get_template(session, template_id)
        if template.driver != olt.driver:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "El plan es de otro tipo de OLT")
        name, body = template.name, template.body
        parsed = TemplateBody.model_validate(body)
        pending = _unverified(olt, kind, parsed, request)
        # Las claves de las cuentas de la ONU viajan con el trabajo, cifradas como las del cliente.
        stored = await _template_secrets(session, state, ctx.tenant_id, template)
        needed = parsed.management.secret_fields() if parsed.management is not None else []
        missing = [_SECRET_LABELS[field] for field in needed if not stored.get(field)]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"El plan no tiene guardada {' ni '.join(missing)}: ábrelo y guárdalo con ella",
            )
        secrets = {**secrets, **{field: stored[field] for field in needed}}
    if pending and not state.settings.allow_unverified_writes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Aprovisionar en este modelo y firmware todavía no está validado en laboratorio. "
            "Falta: " + ", ".join(pending),
        )
    job_id = await jobs.create_job(
        state,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        source_ip=ctx.client_ip,
        olt_id=olt_id,
        kind=kind,
        template_name=name,
        template=body,
        request=request,
        secrets=secrets,
    )
    async with ctx.session() as session:
        if pending:
            job = await session.get(ProvisionJob, job_id)
            if job is not None:
                job.detail = {**(job.detail or {}), "unverified": pending}
        return await _job_out(session, job_id)


@router.post(
    "/v1/olts/{olt_id}/onus/authorize", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED
)
async def authorize_onu(olt_id: UUID, body: AuthorizeIn, ctx: Tenant, state: State) -> JobOut:
    """Alta completa de una ONU nueva. Devuelve el trabajo; su avance se consulta con su id.

    Quien aprovisiona da la ONU (de la búsqueda), el nombre del cliente, el plan y su PPPoE/WiFi.
    La posición libre, el Equipment ID, los puertos del modelo y esperar a que la ONU se conecte
    antes de la WAN los resuelve el trabajo (``api/jobs.py``).
    """
    async with ctx.session() as session:
        template = TemplateBody.model_validate(
            (await _get_template(session, body.template_id)).body
        )
    _check_client(template, body)
    description = clean_label(body.customer, limit=64)
    wifi_ssid = clean_label(body.wifi_name, limit=32) if body.wifi_name else None
    request = {
        "pon": body.pon,
        "onu": body.onu,
        "serial": body.serial,
        "description": description,
        "equipment_id": real_equipment_id(body.equipment_id),
        "pppoe_user": body.pppoe_user,
        "wifi_ssid": wifi_ssid,
    }
    return await _start(olt_id, ctx, state, "authorize", body.template_id, request, _secrets(body))


@router.post(
    "/v1/olts/{olt_id}/onus/configure", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED
)
async def configure_onu(olt_id: UUID, body: ConfigureIn, ctx: Tenant, state: State) -> JobOut:
    """Internet (PPPoE) y WiFi de una ONU ya autorizada: espera a que esté conectada y los pone."""
    async with ctx.session() as session:
        template = TemplateBody.model_validate(
            (await _get_template(session, body.template_id)).body
        )
    _check_client(template, body)
    if template.wan is None and not body.wifi_name and template.management is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Este plan no tiene internet, WiFi ni gestión remota que poner",
        )
    request = {
        "pon": body.pon,
        "onu": body.onu,
        "pppoe_user": body.pppoe_user,
        "wifi_ssid": clean_label(body.wifi_name, limit=32) if body.wifi_name else None,
    }
    return await _start(olt_id, ctx, state, "configure", body.template_id, request, _secrets(body))


@router.get("/v1/provision-jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, ctx: Tenant) -> JobOut:
    ctx.require("olt:read")
    async with ctx.session() as session:
        return await _job_out(session, job_id)


@router.get("/v1/olts/{olt_id}/provision-jobs", response_model=list[JobOut])
async def list_jobs(olt_id: UUID, ctx: Tenant) -> list[JobOut]:
    ctx.require("olt:read")
    async with ctx.session() as session:
        ids = (
            (
                await session.execute(
                    select(ProvisionJob.id)
                    .where(ProvisionJob.olt_id == olt_id)
                    .order_by(ProvisionJob.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        return [await _job_out(session, job_id) for job_id in ids]


@router.post(
    "/v1/olts/{olt_id}/onus/scan", response_model=PlanOut, status_code=status.HTTP_202_ACCEPTED
)
async def scan_onus(olt_id: UUID, ctx: Tenant, state: State) -> PlanOut:
    """ONU nuevas en todos los PON y la lista de ONU con su cliente, en una sola lectura.

    Si todavía no se sabe cuántos PON tiene la OLT se buscan 4; la respuesta de
    ``show interface brief`` lo deja guardado para la próxima.
    """
    ctx.require("olt:read")
    key = executor_key(state)
    state.require_bus()
    async with ctx.session() as session:
        olt, credential, target = await olt_access(session, ctx.tenant_id, state, olt_id)
        pons = range(1, (olt.pon_ports or DEFAULT_PON_PORTS) + 1)
        calls = [
            CommandCall("interfaces.brief"),
            *(CommandCall("onu.autofind", {"pon": pon}) for pon in pons),
        ]
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
        verify=[],
        audit_after=where,
    )
