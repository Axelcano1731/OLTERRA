"""Trabajos de alta: el aprovisionamiento de una ONU de punta a punta.

Quien aprovisiona solo dice qué ONU (de la lista de nuevas), el nombre del cliente, el plan y su
PPPoE/WiFi. Lo técnico lo resuelve el trabajo:

    discover     lee las ONU del PON y el autofind: elige la posición libre y el Equipment ID
    authorize    autoriza y configura el servicio (T-CONT, GEM, VLAN) y guarda
    wait_online  espera a que la ONU quede "working" (cada 15 s, hasta 4 min) y lee sus puertos
    configure    WAN PPPoE y WiFi con los puertos que tiene ese modelo, y guarda
    verify       estado final y potencia

Un trabajo ``configure`` (ONU ya autorizada) empieza en ``wait_online``.

Un trabajo ``olt_password`` cambia la clave con la que Olterra entra a la OLT:

    change   pone la clave nueva (la OLT la pregunta después del Enter), SIN guardar
    check    entra con la clave nueva en OTRA sesión y solo entonces guarda (``write``)
    store    actualiza la credencial cifrada de Olterra

Si no se puede entrar con la nueva, ``recover`` prueba la anterior para decir en qué quedó: como
nada se guardó, un reinicio de la OLT devuelve la clave anterior.

En la V1600G0-B la WAN y el WiFi (comandos privados "pri") responden "Unsupport private
protocol" si la ONU todavía no se conectó: por eso van en una segunda fase.

Avanza con cada resultado de plan (``on_result``) y con un reloj (``tick``) que despierta los
que esperan. Las claves del cliente quedan cifradas con la DEK del tenant solo mientras el
trabajo las necesita.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from olterra.api.routes.olts import olt_access, publish_plan, record_plan
from olterra.api.state import AppState, audit
from olterra.db.models import Credential as CredentialRow
from olterra.db.models import Olt, PlanRun, ProvisionJob
from olterra.db.session import tenant_session
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall, ParamError
from olterra.drivers.vsol_gpon.parsers import parse_equipment_id, real_equipment_id
from olterra.drivers.vsol_gpon.provisioning import (
    ClientData,
    OnuServiceData,
    TemplateBody,
    adapt_binds,
    base_calls,
    service_calls,
)
from olterra.executor.plan import Credential, Plan, PlanResult, Priority
from olterra.orchestrator import (
    PlanBuildError,
    build_credential_change_plan,
    build_read_plan,
    build_write_plan,
    credential_change_calls,
)
from olterra.security.vault import Vault

log = logging.getLogger(__name__)

POLL_EVERY = timedelta(seconds=15)
MAX_WAIT_POLLS = 16  # ~4 minutos
STALE_AFTER = timedelta(minutes=10)  # igual que en olterra_due_provision_jobs()

STEPS = {
    "authorize": ("discover", "authorize", "wait_online", "configure", "verify"),
    "configure": ("wait_online", "configure", "verify"),
    "olt_password": ("change", "check", "store"),
}
LABELS = {
    "discover": "Buscar una posición libre en el PON",
    "authorize": "Autorizar la ONU y configurar el servicio",
    "wait_online": "Esperar a que la ONU se conecte",
    "configure": "Configurar internet (PPPoE) y WiFi",
    "verify": "Comprobar la señal",
    "change": "Poner la clave nueva en la OLT (sin guardar todavía)",
    "check": "Entrar con la clave nueva y guardarla en la OLT",
    "store": "Guardar la clave nueva en Olterra",
}


class JobError(Exception):
    """Un paso no se pudo hacer: el trabajo termina con este mensaje para el operador."""


def _aad(job_id: UUID) -> str:
    return f"provision_job:{job_id}"


def _log(job: ProvisionJob, step: str, message: str, ok: bool = True) -> None:
    entries = list((job.detail or {}).get("log", []))
    entries.append(
        {"step": step, "ok": ok, "message": message, "at": datetime.now(UTC).isoformat()}
    )
    job.detail = {**(job.detail or {}), "log": entries}


def _set_detail(job: ProvisionJob, **values: Any) -> None:
    job.detail = {**(job.detail or {}), **values}


def _finish(job: ProvisionJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    job.finished_at = datetime.now(UTC)
    job.secrets = None  # las claves del cliente no se quedan guardadas
    job.current_plan_id = None
    job.next_run_at = None
    if error:
        _log(job, job.step, error, ok=False)


def _wants_private(job: ProvisionJob) -> bool:
    template = TemplateBody.model_validate(job.template)
    req = job.request
    return template.wan is not None or (template.wifi is not None and bool(req.get("wifi_ssid")))


async def create_job(
    state: AppState,
    *,
    tenant_id: UUID,
    actor: str,
    source_ip: str | None,
    olt_id: UUID,
    kind: str,
    template_name: str | None,
    template: dict[str, Any],
    request: dict[str, Any],
    secrets: dict[str, str],
) -> UUID:
    job_id = uuid4()
    async with tenant_session(state.sessions, tenant_id) as session:
        job = ProvisionJob(
            id=job_id,
            tenant_id=tenant_id,
            olt_id=olt_id,
            kind=kind,
            status="running",
            step=STEPS[kind][0],
            template=template,
            template_name=template_name,
            request=request,
            detail={"log": []},
            requested_by=actor,
            next_run_at=datetime.now(UTC),
        )
        if secrets:
            dek = await state.tenant_dek(session, tenant_id)
            job.secrets = Vault.encrypt(tenant_id, dek, _aad(job_id), json.dumps(secrets).encode())
        session.add(job)
        await session.flush()
        await audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action="olt.password_change_start" if kind == "olt_password" else f"onu.{kind}",
            target_type="olt",
            target_id=str(olt_id),
            # Nunca claves: qué ONU, para quién y con qué plan (o qué usuario de la OLT).
            after={"job_id": str(job_id), "template": template_name, **request},
            source_ip=source_ip,
        )
    await advance(state, tenant_id, job_id)
    return job_id


async def advance(
    state: AppState, tenant_id: UUID, job_id: UUID, result: PlanResult | None = None
) -> None:
    """Lleva el trabajo al siguiente paso: con un resultado, o cuando le toca (sin resultado)."""
    plan: Plan | None = None
    async with tenant_session(state.sessions, tenant_id) as session:
        job = await session.get(ProvisionJob, job_id, with_for_update=True)
        if job is None or job.status != "running":
            return
        now = datetime.now(UTC)
        try:
            if result is None:
                if job.current_plan_id is not None:
                    if job.updated_at < now - STALE_AFTER:
                        raise JobError(
                            "La OLT no respondió a tiempo (revisa el túnel o el ejecutor)"
                        )
                    return
                if job.next_run_at is not None and job.next_run_at > now:
                    return
                plan = await _start_step(session, state, job)
            else:
                if job.current_plan_id != result.plan_id:
                    return
                job.current_plan_id = None
                plan = await _after_step(session, state, job, result)
        except JobError as exc:
            _finish(job, "failed", str(exc))
            plan = None
        job.updated_at = now
    if plan is not None:
        try:
            await publish_plan(state, plan)
        except HTTPException:
            async with tenant_session(state.sessions, tenant_id) as session:
                failed = await session.get(ProvisionJob, job_id, with_for_update=True)
                if failed is not None and failed.status == "running":
                    _finish(failed, "failed", "No se pudo enviar el trabajo a la OLT (NATS)")


def _client(job: ProvisionJob) -> ClientData:
    req = job.request
    return ClientData(
        pon=req["pon"],
        onu=req["onu"],
        serial=req["serial"],
        description=req["description"],
        equipment_id=req.get("equipment_id"),
        pppoe_user=req.get("pppoe_user"),
        wifi_ssid=req.get("wifi_ssid"),
    )


def _secrets(state: AppState, job: ProvisionJob, dek: bytes) -> dict[str, str]:
    if job.secrets is None:
        return {}
    return dict(json.loads(Vault.decrypt(job.tenant_id, dek, _aad(job.id), job.secrets)))


def template_wan_index(job: ProvisionJob) -> int | None:
    """El índice de la WAN que pone el plan del trabajo, o None si no tiene WAN."""
    wan = (job.template or {}).get("wan")
    return int(wan.get("index", 1)) if wan else None


async def _start_step(session: AsyncSession, state: AppState, job: ProvisionJob) -> Plan:
    """Arma, guarda y deja listo para publicar el plan del paso actual."""
    try:
        olt, credential, target = await olt_access(session, job.tenant_id, state, job.olt_id)
    except HTTPException as exc:
        raise JobError(str(exc.detail)) from exc
    driver = get_driver(olt.driver)
    key = state.settings.executor_public_key
    if not key:
        raise JobError("Falta OLTERRA_EXECUTOR_PUBLIC_KEY en el servidor")
    common: dict[str, Any] = {
        "tenant_id": job.tenant_id,
        "olt_id": olt.id,
        "target": target,
        "executor_public_key": key,
        "model": olt.model,
        "firmware": olt.firmware,
    }
    if job.kind == "olt_password":
        return await _start_password_step(session, state, job, olt, credential, common)
    template = TemplateBody.model_validate(job.template)
    req = job.request
    pon, onu = req["pon"], req.get("onu")
    try:
        if job.step == "discover":
            calls = [
                CommandCall("onu.list", {"pon": pon}),
                CommandCall("onu.autofind", {"pon": pon}),
            ]
            plan = build_read_plan(driver, calls=calls, credential=credential, **common)
        elif job.step == "authorize":
            calls = [*base_calls(template, _client(job)), CommandCall("config.save")]
            plan = build_write_plan(
                driver,
                calls=calls,
                credential=credential,
                allow_unverified=state.settings.allow_unverified_writes,
                priority=Priority.PROVISION,
                **common,
            )
        elif job.step == "wait_online":
            where = {"pon": pon, "onu": onu}
            calls = [
                CommandCall("onu.state", {"pon": pon}),
                CommandCall("onu.capability", where),
                CommandCall("onu.detail", where),
                # Para saber si ya tiene la WAN del plan (un "Internet y WiFi" repetido).
                CommandCall("onu.service_config", where),
            ]
            plan = build_read_plan(driver, calls=calls, credential=credential, **common)
        elif job.step == "configure":
            dek = await state.tenant_dek(session, job.tenant_id)
            secrets = _secrets(state, job, dek)
            data = OnuServiceData(
                pon=pon,
                onu=int(req["onu"]),
                equipment_id=req.get("equipment_id"),
                pppoe_user=req.get("pppoe_user"),
                pppoe_password=SecretStr(secrets["pppoe_password"])
                if secrets.get("pppoe_password")
                else None,
                wifi_ssid=req.get("wifi_ssid"),
                wifi_key=SecretStr(secrets["wifi_key"]) if secrets.get("wifi_key") else None,
            )
            needed = template.management.secret_fields() if template.management else []
            if any(not secrets.get(field) for field in needed):
                raise JobError(
                    "Faltan las contraseñas de las cuentas de la ONU: vuelve a guardar el plan"
                )
            detail = job.detail or {}
            binds = (
                adapt_binds(
                    template.wan.binds, detail.get("ethernet_ports"), detail.get("wifi_ports")
                )
                if template.wan is not None
                else None
            )
            if binds is not None:
                _set_detail(job, binds=binds)
            add_wan = not detail.get("wan_exists")
            calls = [
                *service_calls(template, data, binds, add_wan=add_wan),
                CommandCall("config.save"),
            ]
            plan = build_write_plan(
                driver,
                calls=calls,
                credential=credential.model_copy(
                    update={
                        "pppoe_password": data.pppoe_password,
                        "wifi_key": data.wifi_key,
                        **{field: SecretStr(secrets[field]) for field in needed},
                    }
                ),
                allow_unverified=state.settings.allow_unverified_writes,
                priority=Priority.PROVISION,
                **common,
            )
        elif job.step == "verify":
            calls = [
                CommandCall("onu.state", {"pon": pon}),
                CommandCall("onu.optical", {"pon": pon, "onu": onu}),
            ]
            plan = build_read_plan(driver, calls=calls, credential=credential, **common)
        else:
            raise JobError(f"Paso desconocido: {job.step}")
    except (ParamError, PlanBuildError, ValueError) as exc:
        raise JobError(str(exc)) from exc
    await record_plan(session, job.tenant_id, job.requested_by, olt, plan, calls)
    job.current_plan_id = plan.plan_id
    return plan


def _outputs(run: PlanRun | None) -> list[dict[str, Any]]:
    return list(((run.result if run else None) or {}).get("outputs", []))


def _first(outputs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((o for o in outputs if o.get("key") == key), None)


def _failure(outputs: list[dict[str, Any]], run: PlanRun | None) -> str | None:
    """El primer error real del plan (no los "No se ejecutó" que lo siguen)."""
    for output in outputs:
        error = output.get("error") or ""
        if not output.get("ok") and not error.startswith("No se ejecutó"):
            return f"{error or 'falló'} (en {output.get('key')})"
    session_errors = ((run.result if run else None) or {}).get("session_errors") or []
    if session_errors:
        first = session_errors[0]
        return f"{first.get('error')} (en «{first.get('command')}»)"
    plan_error = ((run.result if run else None) or {}).get("error")
    return str(plan_error) if plan_error else None


async def _after_step(
    session: AsyncSession, state: AppState, job: ProvisionJob, result: PlanResult
) -> Plan | None:
    run = await session.get(PlanRun, result.plan_id)
    outputs = _outputs(run)
    if job.kind == "olt_password":
        return await _after_password_step(session, state, job, result, run, outputs)
    req = job.request
    pon, onu = req["pon"], req.get("onu")

    if job.step == "discover":
        found = _first(outputs, "onu.autofind")
        listed = _first(outputs, "onu.list")
        if not found or not found.get("ok") or not isinstance(found.get("data"), list):
            raise JobError(f"No se pudo leer las ONU nuevas del PON {pon}")
        if not listed or not listed.get("ok") or not isinstance(listed.get("data"), list):
            raise JobError(f"No se pudo leer qué posiciones están ocupadas en el PON {pon}")
        row = next((r for r in found["data"] if r.get("serial") == req["serial"]), None)
        taken = {int(r["onu"]): r.get("serial") for r in listed["data"] if r.get("onu")}
        if row is None:
            already = next((n for n, serial in taken.items() if serial == req["serial"]), None)
            if already is not None:
                raise JobError(f"Esa ONU ya está autorizada en el PON {pon}, posición {already}")
            raise JobError(
                f"La ONU {req['serial']} ya no aparece como nueva en el PON {pon}: revisa que "
                "siga conectada"
            )
        index = req.get("onu") or next((n for n in range(1, 129) if n not in taken), None)
        if index is None:
            raise JobError(f"El PON {pon} está lleno (128 ONU)")
        if index in taken:
            raise JobError(f"La posición {index} del PON {pon} ya está ocupada")
        job.request = {
            **req,
            "onu": index,
            "equipment_id": real_equipment_id(req.get("equipment_id"))
            or real_equipment_id(row.get("model")),
        }
        _log(job, "discover", f"PON {pon}, posición {index}")
        job.step = "authorize"
        return await _start_step(session, state, job)

    if job.step == "authorize":
        failure = _failure(outputs, run)
        if failure is not None or result.status != "ok":
            raise JobError(f"La OLT rechazó el alta: {failure or result.error or 'sin detalle'}")
        _log(job, "authorize", "Autorizada, con su servicio guardado en la OLT")
        job.step = "wait_online"
        job.attempts = 0
        job.next_run_at = datetime.now(UTC) + POLL_EVERY
        return None

    if job.step == "wait_online":
        states = _first(outputs, "onu.state")
        rows = states.get("data") if states and states.get("ok") else None
        mine = next((r for r in rows or [] if r.get("onu") == onu), None)
        working = mine is not None and str(mine.get("phase", "")).lower() == "working"
        if not working:
            job.attempts += 1
            if job.attempts >= MAX_WAIT_POLLS:
                saved = " El servicio quedó guardado: cuando se conecte, usa «Configurar internet»."
                raise JobError(
                    "La ONU no se conectó en 4 minutos: revisa la fibra, el conector y la "
                    "potencia." + (saved if job.kind == "authorize" else "")
                )
            phase = mine.get("phase") if mine else "sin registrar"
            _set_detail(job, phase=phase)
            job.next_run_at = datetime.now(UTC) + POLL_EVERY
            return None
        capability = _first(outputs, "onu.capability")
        cap = capability.get("data") if capability and capability.get("ok") else None
        detail_out = _first(outputs, "onu.detail")
        equipment = parse_equipment_id(detail_out.get("output") or "") if detail_out else None
        # Con la ONU conectada su detalle dice el modelo real: manda sobre el del autofind, que
        # puede venir vacío ("NULL") o de otra ONU.
        if equipment and equipment != req.get("equipment_id"):
            job.request = {**req, "equipment_id": equipment}
        running = _first(outputs, "onu.service_config")
        wan_index = template_wan_index(job)
        wan_exists = bool(
            running
            and running.get("ok")
            and wan_index is not None
            and re.search(
                rf"(?m)^\s*onu {onu} pri wan_adv index {wan_index} route\b",
                running.get("output") or "",
            )
        )
        _set_detail(
            job,
            phase="working",
            ethernet_ports=(cap or {}).get("ethernet_ports"),
            wifi_ports=(cap or {}).get("wifi_ports"),
            onu_type=(cap or {}).get("onu_type"),
            wan_exists=wan_exists,
        )
        _log(job, "wait_online", "La ONU se conectó")
        job.step = "configure" if _wants_private(job) else "verify"
        return await _start_step(session, state, job)

    if job.step == "configure":
        failure = _failure(outputs, run)
        if failure is not None or result.status != "ok":
            raise JobError(
                f"No se pudo configurar internet/WiFi: {failure or result.error or 'sin detalle'}"
            )
        job.secrets = None
        _log(job, "configure", "Internet y WiFi configurados y guardados")
        job.step = "verify"
        return await _start_step(session, state, job)

    if job.step == "verify":
        states = _first(outputs, "onu.state")
        rows = states.get("data") if states and states.get("ok") else None
        mine = next((r for r in rows or [] if r.get("onu") == onu), None)
        optical = _first(outputs, "onu.optical")
        rx = (optical.get("data") or {}).get("rx_dbm") if optical and optical.get("ok") else None
        _set_detail(job, phase=(mine or {}).get("phase"), rx_dbm=rx)
        message = "Lista" + (f": señal {rx} dBm" if rx is not None else "")
        _log(job, "verify", message)
        _finish(job, "done")
        return None

    raise JobError(f"Paso desconocido: {job.step}")


# --- Cambio de clave de la OLT -----------------------------------------------------------------


def _effective_credential(
    current: Credential, job: ProvisionJob, secrets: dict[str, str]
) -> Credential:
    """Con qué se entra después del cambio: la clave nueva donde la OLT la aceptó."""
    changed = (job.detail or {}).get("changed", {})
    update: dict[str, Any] = {}
    if current.enable_password is None:
        # Sin clave de enable propia, el ejecutor usa la de acceso. Si solo cambia la de acceso,
        # la de enable de la OLT sigue siendo la anterior: se deja explícita.
        update["enable_password"] = current.password
    if changed.get("login"):
        update["password"] = SecretStr(secrets["new_password"])
    if changed.get("enable"):
        update["enable_password"] = SecretStr(secrets["new_enable_password"])
    return current.model_copy(update=update)


async def _start_password_step(
    session: AsyncSession,
    state: AppState,
    job: ProvisionJob,
    olt: Olt,
    credential: Credential,
    common: dict[str, Any],
) -> Plan:
    driver = get_driver(olt.driver)
    dek = await state.tenant_dek(session, job.tenant_id)
    secrets = _secrets(state, job, dek)
    allow = state.settings.allow_unverified_writes
    try:
        if job.step == "change":
            with_new = credential.model_copy(
                update={
                    "new_password": SecretStr(secrets["new_password"])
                    if secrets.get("new_password")
                    else None,
                    "new_enable_password": SecretStr(secrets["new_enable_password"])
                    if secrets.get("new_enable_password")
                    else None,
                }
            )
            calls = credential_change_calls(with_new)
            plan = build_credential_change_plan(
                driver, credential=with_new, allow_unverified=allow, **common
            )
        elif job.step == "check":
            # Entrar ya es la prueba: si la clave nueva no sirve, el plan no pasa del login.
            calls = [CommandCall("config.save"), CommandCall("system.version")]
            plan = build_write_plan(
                driver,
                calls=calls[:1],
                verify=calls[1:],
                credential=_effective_credential(credential, job, secrets),
                allow_unverified=allow,
                **common,
            )
        elif job.step == "recover":
            calls = [CommandCall("system.version")]
            plan = build_read_plan(driver, calls=calls, credential=credential, **common)
        else:
            raise JobError(f"Paso desconocido: {job.step}")
    except (KeyError, ParamError, PlanBuildError, ValueError) as exc:
        raise JobError(str(exc)) from exc
    await record_plan(session, job.tenant_id, job.requested_by, olt, plan, calls)
    job.current_plan_id = plan.plan_id
    return plan


async def _after_password_step(
    session: AsyncSession,
    state: AppState,
    job: ProvisionJob,
    result: PlanResult,
    run: PlanRun | None,
    outputs: list[dict[str, Any]],
) -> Plan | None:
    req = job.request
    failure = _failure(outputs, run) or result.error

    if job.step == "change":
        login = _first(outputs, "user.set_login_password")
        enable = _first(outputs, "user.set_enable_password")
        changed = {
            "login": bool(login and login.get("ok")),
            "enable": bool(enable and enable.get("ok")),
        }
        _set_detail(job, changed=changed)
        if not changed["login"] and not changed["enable"]:
            # Puede no haber cambiado nada o haber quedado a medias: la anterior lo dice.
            _set_detail(
                job,
                failed_at="change",
                problem=f"La OLT no aceptó la clave nueva: {failure or 'sin detalle'}",
            )
            _log(job, "change", str((job.detail or {})["problem"]), ok=False)
            job.step = "recover"
            return await _start_step(session, state, job)
        _log(job, "change", "La OLT aceptó la clave nueva (todavía sin guardar)")
        job.step = "check"
        return await _start_step(session, state, job)

    if job.step == "check":
        logged_in = any(step.output is not None for step in result.steps)
        if not logged_in:
            _set_detail(
                job,
                failed_at="check",
                problem=f"No se pudo entrar con la clave nueva: {failure or 'sin detalle'}",
            )
            _log(job, "check", str((job.detail or {})["problem"]), ok=False)
            job.step = "recover"
            return await _start_step(session, state, job)
        save = _first(outputs, "config.save")
        saved = bool(save and save.get("ok"))
        dek = await state.tenant_dek(session, job.tenant_id)
        olt = await session.get(Olt, job.olt_id)
        row = await session.get(CredentialRow, olt.credential_id) if olt else None
        if olt is None or row is None:
            raise JobError("La OLT o su credencial ya no existen")
        aad = f"credential:{row.id}"
        current = Credential.model_validate_json(
            Vault.decrypt(job.tenant_id, dek, aad, row.ciphertext)
        )
        effective = _effective_credential(current, job, _secrets(state, job, dek))
        row.ciphertext = Vault.encrypt(job.tenant_id, dek, aad, effective.reveal_json())
        changed = (job.detail or {}).get("changed", {})
        names = [name for name in ("login", "enable") if changed.get(name)]
        await audit(
            session,
            tenant_id=job.tenant_id,
            actor=job.requested_by,
            action="olt.password_change",
            target_type="olt",
            target_id=str(job.olt_id),
            # Cuáles cambiaron, nunca su valor.
            after={
                "job_id": str(job.id),
                "username": req.get("username"),
                "changed": names,
                "saved": saved,
            },
        )
        if not saved:
            raise JobError(
                "Olterra ya entra con la clave nueva, pero la OLT no la guardó en su memoria "
                f"({failure or 'sin detalle'}): si se reinicia antes de guardarla, vuelve la anterior"
            )
        _log(job, "check", "Entró con la clave nueva y la OLT la guardó")
        _log(job, "store", "Olterra ya usa la clave nueva")
        job.step = "store"
        missing = [
            label
            for name, label in (("login", "de acceso"), ("enable", "de enable"))
            if req.get(name) and not changed.get(name)
        ]
        if missing:
            _finish(
                job,
                "failed",
                f"La clave {' ni la '.join(missing)} no se cambió (la OLT no la aceptó); "
                "la otra sí quedó cambiada y guardada",
            )
        else:
            _finish(job, "done")
        return None

    if job.step == "recover":
        problem = str((job.detail or {}).get("problem") or "No se pudo cambiar la clave")
        if result.status == "ok":
            raise JobError(
                f"{problem}. La OLT sigue aceptando la clave anterior y Olterra la sigue usando"
            )
        raise JobError(
            f"{problem}. Tampoco se pudo entrar con la anterior ({failure or 'sin detalle'}). "
            "Nada quedó guardado en la OLT: al reiniciarla vuelve la clave anterior"
        )

    raise JobError(f"Paso desconocido: {job.step}")


async def on_result(state: AppState, result: PlanResult) -> None:
    """Si el plan era de un trabajo de alta, lo avanza."""
    async with tenant_session(state.sessions, result.tenant_id) as session:
        job_id = (
            await session.execute(
                select(ProvisionJob.id).where(ProvisionJob.current_plan_id == result.plan_id)
            )
        ).scalar_one_or_none()
    if job_id is not None:
        await advance(state, result.tenant_id, job_id, result)


async def tick(state: AppState) -> int:
    """Despierta los trabajos que esperan (y cierra los que se quedaron sin respuesta)."""
    async with state.sessions() as session:
        due = (
            await session.execute(
                text("SELECT tenant_id, job_id FROM olterra_due_provision_jobs()")
            )
        ).all()
    for tenant_id, job_id in due:
        try:
            await advance(state, tenant_id, job_id)
        except Exception:
            log.exception("No se pudo avanzar el trabajo de alta %s", job_id)
    return len(due)


async def run_ticker(state: AppState, every: float = 5.0) -> None:
    while True:
        try:
            await tick(state)
        except Exception:
            log.exception("Reloj de trabajos de alta")
        await asyncio.sleep(every)


def steps_view(job: ProvisionJob) -> list[dict[str, Any]]:
    """Los pasos del trabajo para la interfaz: hecho, en curso, pendiente o falló."""
    order = STEPS[job.kind]
    # "recover" no es un paso visible: se muestra en el que falló.
    shown = job.step if job.step in order else (job.detail or {}).get("failed_at", order[0])
    current = order.index(shown) if shown in order else 0
    entries = (job.detail or {}).get("log", [])
    view = []
    for index, step in enumerate(order):
        message = next((e["message"] for e in reversed(entries) if e["step"] == step), None)
        if job.status == "done" or index < current:
            status = "done"
        elif index == current:
            status = "failed" if job.status == "failed" else "running"
        else:
            status = "pending"
        if (
            step == "configure"
            and job.kind != "olt_password"
            and job.status == "done"
            and not _wants_private(job)
        ):
            status = "skipped"
        view.append({"key": step, "label": LABELS[step], "status": status, "message": message})
    return view
