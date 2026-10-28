"""Lado nube del ejecutor: arma planes con el driver y lee lo que vuelve.

El ejecutor solo devuelve texto crudo por paso. Aquí se empareja cada paso con el
comando del catálogo que lo originó (los cambios de modo se saltan) y se pasa por
su parser. Si el parser no reconoce la salida, el resultado lo dice; no adivina.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from olterra.drivers import get_driver
from olterra.drivers.base import Access, CommandCall, Driver, Parser, UnrecognizedOutput
from olterra.drivers.vsol_gpon import PARSERS
from olterra.executor.plan import Credential, Plan, PlanResult, Priority, StepResult, Target
from olterra.security.masking import redact
from olterra.security.sealed import seal


class PlanBuildError(ValueError):
    pass


def build_read_plan(
    driver: Driver,
    *,
    tenant_id: UUID,
    olt_id: UUID,
    target: Target,
    calls: Sequence[CommandCall],
    credential: Credential,
    executor_public_key: str,
    model: str | None = None,
    firmware: str | None = None,
    priority: Priority = Priority.USER,
    timeout_s: float = 30.0,
) -> Plan:
    """Plan de solo lectura con la credencial sellada para el ejecutor."""
    for call in calls:
        template = driver.command(call.key, model, firmware)
        if template.access is not Access.READ:
            raise PlanBuildError(f"'{call.key}' escribe en la OLT; este plan es de solo lectura")
    plan = Plan(
        tenant_id=tenant_id,
        olt_id=olt_id,
        priority=priority,
        access="read",
        on_error="continue",
        target=target,
        session=driver.session,
        steps=driver.build_cli_steps(calls, model=model, firmware=firmware, timeout_s=timeout_s),
    )
    sealed = seal(executor_public_key, credential.reveal_json(), plan.seal_context())
    return plan.model_copy(update={"credential": sealed})


def build_credential_change_plan(
    driver: Driver,
    *,
    tenant_id: UUID,
    olt_id: UUID,
    target: Target,
    credential: Credential,
    executor_public_key: str,
    model: str | None = None,
    firmware: str | None = None,
    allow_unverified: bool = False,
) -> Plan:
    """Plan de ESCRITURA que pone claves nuevas al usuario con el que entra Olterra, SIN guardar.

    ``credential`` trae la clave vigente (para entrar) y ``new_password`` / ``new_enable_password``
    (lo que se va a poner). Las claves nuevas viajan solo dentro del sello; los pasos llevan
    marcadores y el ejecutor las escribe cuando la OLT las pregunta.

    No guarda en flash a propósito: quien lo llama abre después otra sesión con la clave nueva y
    solo entonces guarda (``write``) y actualiza la bóveda (``api/jobs.py``). Si esa prueba falla,
    nada quedó guardado y un reinicio de la OLT devuelve la clave anterior.
    """
    return build_write_plan(
        driver,
        tenant_id=tenant_id,
        olt_id=olt_id,
        target=target,
        calls=credential_change_calls(credential),
        credential=credential,
        executor_public_key=executor_public_key,
        model=model,
        firmware=firmware,
        allow_unverified=allow_unverified,
    )


def credential_change_calls(credential: Credential) -> list[CommandCall]:
    """Las llamadas del plan de cambio de claves, según qué claves nuevas trae la credencial."""
    if not credential.username:
        raise PlanBuildError("La credencial no tiene usuario")
    calls: list[CommandCall] = []
    if credential.new_password is not None:
        calls.append(CommandCall("user.set_login_password", {"username": credential.username}))
    if credential.new_enable_password is not None:
        calls.append(CommandCall("user.set_enable_password", {"username": credential.username}))
    if not calls:
        raise PlanBuildError("No hay ninguna clave nueva que poner")
    return calls


def unverified_writes(
    driver: Driver, calls: Sequence[CommandCall], model: str | None, firmware: str | None
) -> list[str]:
    """Llaves de escritura de ``calls`` que no tienen captura de laboratorio para este equipo."""
    keys = []
    for call in calls:
        template = driver.command(call.key, model, firmware)
        if template.access is Access.WRITE and not template.verified:
            keys.append(call.key)
    return list(dict.fromkeys(keys))


def build_write_plan(
    driver: Driver,
    *,
    tenant_id: UUID,
    olt_id: UUID,
    target: Target,
    calls: Sequence[CommandCall],
    credential: Credential,
    executor_public_key: str,
    model: str | None = None,
    firmware: str | None = None,
    verify: Sequence[CommandCall] = (),
    allow_unverified: bool = False,
    priority: Priority = Priority.USER,
) -> Plan:
    """Plan de ESCRITURA: se detiene al primer error y después lee ``verify`` (estado final).

    Se niega si algún comando no está verificado en laboratorio para este modelo y firmware,
    salvo ``allow_unverified`` (solo para validarlos en el laboratorio). Las claves van en la
    credencial sellada; los pasos llevan marcadores ``{{secret:…}}``.
    """
    if not calls:
        raise PlanBuildError("El plan no tiene comandos")
    for call in calls:
        if driver.command(call.key, model, firmware).access is not Access.WRITE:
            raise PlanBuildError(f"'{call.key}' no es de escritura")
    for call in verify:
        if driver.command(call.key, model, firmware).access is not Access.READ:
            raise PlanBuildError(f"'{call.key}' no es de lectura: no sirve para verificar")
    pending = unverified_writes(driver, calls, model, firmware)
    if pending and not allow_unverified:
        raise PlanBuildError(
            "Todavía no se validó en laboratorio con este modelo y firmware: " + ", ".join(pending)
        )
    plan = Plan(
        tenant_id=tenant_id,
        olt_id=olt_id,
        priority=priority,
        access="write",
        on_error="stop",
        target=target,
        session=driver.session,
        steps=driver.build_cli_steps([*calls, *verify], model=model, firmware=firmware),
    )
    sealed = seal(executor_public_key, credential.reveal_json(), plan.seal_context())
    return plan.model_copy(update={"credential": sealed})


def pair_outputs(
    call_commands: Sequence[str], step_commands: Sequence[str], steps: Sequence[StepResult]
) -> list[tuple[int, StepResult]]:
    """``(índice_de_llamada, resultado)`` para cada llamada que tuvo paso."""
    pairs = []
    next_call = 0
    for command, step in zip(step_commands, steps, strict=False):
        if next_call < len(call_commands) and command == call_commands[next_call]:
            pairs.append((next_call, step))
            next_call += 1
    return pairs


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def parsers_for(driver_key: str) -> Mapping[str, Parser]:
    """Los parsers de la marca de la OLT (VSOL si el driver no existe)."""
    try:
        return get_driver(driver_key).parsers
    except KeyError:
        return PARSERS


def interpret(
    calls: Sequence[dict[str, Any]],
    step_commands: Sequence[str],
    result: PlanResult,
    driver_key: str = "vsol-gpon",
) -> dict[str, Any]:
    """Resultado listo para guardar: salidas enmascaradas y, si hay parser, datos estructurados."""
    parsers = parsers_for(driver_key)
    outputs = []
    for index, step in pair_outputs([c["command"] for c in calls], step_commands, result.steps):
        call = calls[index]
        entry: dict[str, Any] = {
            "key": call["key"],
            "params": call["params"],
            "ok": step.ok,
            "error": step.error,
            "output": redact(step.output) if step.output is not None else None,
        }
        parser = parsers.get(call["key"])
        if parser is not None and step.ok and step.output is not None:
            try:
                entry["data"] = _jsonable(parser(step.output, call["params"]))
            except UnrecognizedOutput as exc:
                entry["parse_error"] = str(exc)
        outputs.append(entry)
    # Los pasos de cambio de modo (configure terminal, interface gpon…) no son llamadas del
    # catálogo; si uno falla por su cuenta se muestra: si no, el plan "falla" sin decir dónde.
    paired = {
        id(step)
        for _, step in pair_outputs([c["command"] for c in calls], step_commands, result.steps)
    }
    session_errors = [
        {
            "command": command,
            "error": step.error,
            "output": redact(step.output) if step.output is not None else None,
        }
        for command, step in zip(step_commands, result.steps, strict=False)
        if id(step) not in paired
        and not step.ok
        and not (step.error or "").startswith("No se ejecutó")
    ]
    return {
        "status": result.status,
        "error": result.error,
        "executor": result.executor,
        "host_key": result.host_key,
        "outputs": outputs,
        "session_errors": session_errors,
    }
