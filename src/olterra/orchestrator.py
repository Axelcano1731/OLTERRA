"""Lado nube del ejecutor: arma planes con el driver y lee lo que vuelve.

El ejecutor solo devuelve texto crudo por paso. Aquí se empareja cada paso con el
comando del catálogo que lo originó (los cambios de modo se saltan) y se pasa por
su parser. Si el parser no reconoce la salida, el resultado lo dice; no adivina.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from olterra.drivers.base import Access, CommandCall, Driver, UnrecognizedOutput
from olterra.drivers.vsol_gpon import parsers as vsol
from olterra.executor.plan import Credential, Plan, PlanResult, Priority, StepResult, Target
from olterra.security.masking import redact
from olterra.security.sealed import seal

Parser = Callable[[str, dict[str, Any]], Any]

PARSERS: dict[str, Parser] = {
    "system.version": lambda text, params: vsol.parse_version(text),
    "onu.list": lambda text, params: vsol.parse_onu_list(text, params.get("pon")),
    "onu.autofind": lambda text, params: vsol.parse_autofind(text, params.get("pon")),
    "onu.rx_power_all": lambda text, params: vsol.parse_rx_power(text, params.get("pon")),
    "onu.optical": lambda text, params: vsol.parse_onu_optical(text),
}


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
        steps=driver.build_cli_steps(calls, model=model, firmware=firmware),
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


def interpret(
    calls: Sequence[dict[str, Any]], step_commands: Sequence[str], result: PlanResult
) -> dict[str, Any]:
    """Resultado listo para guardar: salidas enmascaradas y, si hay parser, datos estructurados."""
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
        parser = PARSERS.get(call["key"])
        if parser is not None and step.ok and step.output is not None:
            try:
                entry["data"] = _jsonable(parser(step.output, call["params"]))
            except UnrecognizedOutput as exc:
                entry["parse_error"] = str(exc)
        outputs.append(entry)
    return {
        "status": result.status,
        "error": result.error,
        "executor": result.executor,
        "host_key": result.host_key,
        "outputs": outputs,
    }
