"""Servicio del ejecutor: toma planes de NATS, los corre y publica los resultados.

olterra-executor            # lee la configuración de OLTERRA_*
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import UUID

from olterra.config import ConfigError, get_settings
from olterra.executor.bus import Delivery, MemoryBus, NatsBus
from olterra.executor.lanes import LaneScheduler
from olterra.executor.plan import Credential, Plan, PlanResult
from olterra.executor.runner import PlanRunner
from olterra.security.sealed import SealError, unseal

log = logging.getLogger("olterra.executor")

Bus = NatsBus | MemoryBus


class Executor:
    def __init__(
        self,
        *,
        bus: Bus,
        runner: PlanRunner,
        group: str,
        private_key: str | None,
        max_sessions: int = 32,
        remember_results: int = 1024,
    ) -> None:
        self._bus = bus
        self._runner = runner
        self._group = group
        self._private_key = private_key
        self.scheduler = LaneScheduler(self.handle, max_concurrent=max_sessions)
        # Si JetStream reentrega un plan ya corrido (el ejecutor murió antes del acuse),
        # se republica el resultado en vez de repetir una escritura.
        self._done: OrderedDict[UUID, PlanResult] = OrderedDict()
        self._remember = remember_results

    def _open_credential(self, plan: Plan) -> Credential | None:
        if plan.credential is None:
            return None
        if self._private_key is None:
            raise SealError("Este ejecutor no tiene llave privada para abrir credenciales")
        plaintext = unseal(self._private_key, plan.credential, plan.seal_context())
        return Credential.model_validate_json(plaintext)

    def _rejected(self, plan: Plan, reason: str) -> PlanResult:
        now = datetime.now(UTC)
        return PlanResult(
            plan_id=plan.plan_id,
            tenant_id=plan.tenant_id,
            olt_id=plan.olt_id,
            executor=self._runner.executor_id,
            status="rejected",
            error=reason,
            started_at=now,
            finished_at=now,
        )

    async def handle(self, delivery: Delivery) -> None:
        plan = delivery.plan
        cached = self._done.get(plan.plan_id)
        if cached is not None:
            await self._bus.publish_result(cached)
            await delivery.ack()
            return
        try:
            credential = self._open_credential(plan)
        except (SealError, ValueError) as exc:
            result = self._rejected(plan, f"Credencial inválida para este ejecutor: {exc}")
        else:
            try:
                result = await self._runner.run(plan, credential)
            except Exception:
                log.exception("Falla inesperada corriendo el plan %s", plan.plan_id)
                await delivery.nak(delay=10)
                return
        self._done[plan.plan_id] = result
        while len(self._done) > self._remember:
            self._done.popitem(last=False)
        await self._bus.publish_result(result)
        await delivery.ack()
        log.info(
            "plan=%s olt=%s prioridad=%s estado=%s",
            plan.plan_id,
            plan.olt_id,
            plan.priority.name,
            result.status,
        )

    async def serve(self) -> None:
        async for delivery in self._bus.deliveries(self._group):
            self.scheduler.submit(delivery)


async def _serve(nats_url: str, group: str) -> None:
    settings = get_settings()
    private_key = (
        settings.executor_private_key.get_secret_value() if settings.executor_private_key else None
    )
    if private_key is None:
        log.warning("Sin OLTERRA_EXECUTOR_PRIVATE_KEY: se rechazará todo plan con credencial")
    executor_id = f"{group}@{socket.gethostname()}"
    bus = await NatsBus.connect(nats_url, name=executor_id)
    executor = Executor(
        bus=bus,
        runner=PlanRunner(executor_id),
        group=group,
        private_key=private_key,
        max_sessions=settings.executor_max_sessions,
    )
    log.info("Ejecutor %s escuchando planes en %s", executor_id, nats_url)
    try:
        await executor.serve()
    finally:
        await executor.scheduler.close()
        await bus.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="olterra-executor", description="Ejecutor de planes de Olterra"
    )
    parser.add_argument("--nats-url", help="Por defecto OLTERRA_NATS_URL")
    parser.add_argument("--group", help="Grupo de ejecución; por defecto OLTERRA_EXECUTOR_GROUP")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = get_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuración inválida: {exc}") from exc
    asyncio.run(_serve(args.nats_url or settings.nats_url, args.group or settings.executor_group))


if __name__ == "__main__":
    main()
