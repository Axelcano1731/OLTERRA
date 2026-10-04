"""Corre un plan contra una OLT y arma el resultado.

Lo usan el ejecutor (con la credencial recién abierta del sello) y la herramienta
de captura del laboratorio (con la credencial escrita a mano).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from olterra.executor.cli import CliError, CliSession, CliTimeout
from olterra.executor.plan import (
    CliCommand,
    Credential,
    Plan,
    PlanResult,
    PlanStatus,
    SnmpWalk,
    StepResult,
)
from olterra.executor.snmp import SnmpClient, SnmpError
from olterra.executor.ssh import SshConnectOptions, open_ssh_transport
from olterra.security.masking import redact

log = logging.getLogger(__name__)

# Recibe opciones y devuelve un transporte CLI; si además expone ``host_key``,
# el resultado reporta la llave para fijarla (confianza al primer uso).
TransportFactory = Callable[[SshConnectOptions], Awaitable[Any]]


def _now() -> datetime:
    return datetime.now(UTC)


def _status(steps: list[StepResult]) -> PlanStatus:
    if steps and all(step.ok for step in steps):
        return "ok"
    if any(step.ok for step in steps):
        return "partial"
    return "failed"


class PlanRunner:
    def __init__(
        self,
        executor_id: str,
        *,
        snmp: SnmpClient | None = None,
        transport_factory: TransportFactory = open_ssh_transport,
    ) -> None:
        self.executor_id = executor_id
        self._snmp = snmp
        self._transport_factory = transport_factory

    def _result(
        self, plan: Plan, status: PlanStatus, started: datetime, **extra: Any
    ) -> PlanResult:
        return PlanResult(
            plan_id=plan.plan_id,
            tenant_id=plan.tenant_id,
            olt_id=plan.olt_id,
            executor=self.executor_id,
            status=status,
            started_at=started,
            finished_at=_now(),
            **extra,
        )

    async def run(self, plan: Plan, credential: Credential | None) -> PlanResult:
        started = _now()
        if started > plan.deadline:
            return self._result(
                plan, "expired", started, error="El plan venció antes de ejecutarse"
            )
        if plan.needs_cli() and (
            plan.session is None or credential is None or not credential.username
        ):
            return self._result(
                plan,
                "rejected",
                started,
                error="Plan con pasos CLI sin perfil de sesión o sin usuario",
            )

        secrets = credential.secret_values() if credential else []
        session: CliSession | None = None
        transport: Any = None
        host_key: str | None = None
        results: list[StepResult] = []
        stop_reason: str | None = None

        try:
            for index, step in enumerate(plan.steps):
                if stop_reason is not None:
                    results.append(StepResult(index=index, ok=False, error=stop_reason))
                    continue
                t0 = time.monotonic()
                if isinstance(step, CliCommand):
                    if session is None:
                        try:
                            session, transport = await self._open_cli(plan, credential)
                            if plan.target.ssh_host_key is None:
                                host_key = getattr(transport, "host_key", None)
                        except CliError as exc:
                            results.append(
                                StepResult(index=index, ok=False, error=redact(str(exc), secrets))
                            )
                            stop_reason = "No se ejecutó: no hubo sesión CLI"
                            continue
                    result = await self._run_cli(session, step, index, secrets, credential)
                else:
                    result = await self._run_snmp(plan, step, index, credential, secrets)
                result.elapsed_ms = int((time.monotonic() - t0) * 1000)
                results.append(result)
                if not result.ok and plan.on_error == "stop":
                    stop_reason = f"No se ejecutó: falló el paso {index}"
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception:  # cerrar nunca debe tapar el resultado
                    log.debug("Error al cerrar la sesión CLI", exc_info=True)

        return self._result(plan, _status(results), started, steps=results, host_key=host_key)

    async def _open_cli(self, plan: Plan, credential: Credential | None) -> tuple[CliSession, Any]:
        if plan.session is None or credential is None or not credential.username:
            raise CliError("Plan sin perfil de sesión o sin usuario")
        password = credential.password.get_secret_value() if credential.password else ""
        options = SshConnectOptions(
            host=plan.target.host,
            port=plan.target.ssh_port,
            username=credential.username,
            password=password,
            known_host_key=plan.target.ssh_host_key,
            legacy_algorithms=plan.session.legacy_ssh_algorithms,
            terminal_width=plan.session.terminal_width,
        )
        transport = await self._transport_factory(options)
        session = CliSession(transport, plan.session)
        enable = credential.enable_password or credential.password
        try:
            await session.login(enable.get_secret_value() if enable else None)
        except (CliError, EOFError) as exc:
            await session.close()
            raise CliError(f"No se pudo entrar a la CLI: {exc}") from exc
        return session, transport

    @staticmethod
    async def _run_cli(
        session: CliSession,
        step: CliCommand,
        index: int,
        secrets: list[str],
        credential: Credential | None,
    ) -> StepResult:
        try:
            command = step.command
            if step.uses_secrets():
                if credential is None:
                    raise ValueError("El paso lleva claves y el plan no trae credencial")
                command = credential.resolve_secrets(step.command)
            output = await session.run(command, timeout=step.timeout_s)
        except ValueError as exc:
            return StepResult(index=index, ok=False, error=redact(str(exc), secrets))
        except CliTimeout as exc:
            return StepResult(
                index=index, ok=False, output=redact(exc.partial_output, secrets), error=str(exc)
            )
        except (CliError, EOFError) as exc:
            return StepResult(index=index, ok=False, error=redact(str(exc), secrets))
        output = redact(output, secrets)
        error = session.find_error(output)
        return StepResult(index=index, ok=error is None, output=output, error=error)

    async def _run_snmp(
        self,
        plan: Plan,
        step: SnmpWalk,
        index: int,
        credential: Credential | None,
        secrets: list[str],
    ) -> StepResult:
        if credential is None:
            return StepResult(index=index, ok=False, error="El plan pide SNMP y no trae credencial")
        if self._snmp is None:
            self._snmp = SnmpClient()
        try:
            varbinds = await self._snmp.walk(
                plan.target.host,
                plan.target.snmp_port,
                credential,
                step.oid,
                max_repetitions=step.max_repetitions,
                timeout=step.timeout_s,
                retries=step.retries,
            )
        except SnmpError as exc:
            return StepResult(index=index, ok=False, error=redact(str(exc), secrets))
        return StepResult(index=index, ok=True, varbinds=varbinds)
