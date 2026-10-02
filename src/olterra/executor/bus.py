"""Transporte de planes y resultados: NATS JetStream en serio, memoria en pruebas.

Sujetos:

- planes: ``olterra.plan.<grupo>.<tenant>.<olt>`` — stream ``OLTERRA_PLANS`` en modo
  *work queue* (cada plan lo toma un solo ejecutor) y con deduplicación por
  ``plan_id`` (``Nats-Msg-Id``), para que un reintento de la nube no duplique una
  escritura.
- resultados: ``olterra.result.<tenant>.<olt>`` — stream ``OLTERRA_RESULTS``.

El tenant va en el sujeto para que los permisos por cuenta NATS (agentes on-premise,
fase 4) se puedan expresar con comodines: un agente solo ve ``olterra.plan.<su grupo>.>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from olterra.executor.plan import Plan, PlanResult

log = logging.getLogger(__name__)

PLAN_STREAM = "OLTERRA_PLANS"
RESULT_STREAM = "OLTERRA_RESULTS"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_group(group: str) -> str:
    if not _TOKEN.fullmatch(group):
        raise ValueError("El grupo de ejecución solo admite letras, números, '-' y '_'")
    return group


def plan_subject(group: str, tenant_id: UUID, olt_id: UUID) -> str:
    return f"olterra.plan.{_check_group(group)}.{tenant_id.hex}.{olt_id.hex}"


def result_subject(tenant_id: UUID, olt_id: UUID) -> str:
    return f"olterra.result.{tenant_id.hex}.{olt_id.hex}"


class Delivery(Protocol):
    """Un plan entregado al ejecutor, con su acuse pendiente."""

    plan: Plan

    async def ack(self) -> None: ...

    async def nak(self, delay: float | None = None) -> None: ...

    async def in_progress(self) -> None: ...


# --- En memoria (pruebas y modo local) ------------------------------------------


class MemoryDelivery:
    def __init__(self, plan: Plan, bus: MemoryBus) -> None:
        self.plan = plan
        self._bus = bus
        self.acked = False
        self.naks = 0
        self.heartbeats = 0

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.naks += 1
        await self._bus._plans.put(self)

    async def in_progress(self) -> None:
        self.heartbeats += 1


class MemoryBus:
    def __init__(self) -> None:
        self._plans: asyncio.Queue[MemoryDelivery] = asyncio.Queue()
        self.results: list[PlanResult] = []
        self._result_added = asyncio.Condition()

    async def publish_plan(self, plan: Plan, group: str = "cloud") -> MemoryDelivery:
        delivery = MemoryDelivery(plan, self)
        await self._plans.put(delivery)
        return delivery

    async def deliveries(self, group: str) -> AsyncIterator[Delivery]:
        while True:
            yield await self._plans.get()

    async def publish_result(self, result: PlanResult) -> None:
        async with self._result_added:
            self.results.append(result)
            self._result_added.notify_all()

    async def wait_results(self, count: int, timeout: float = 5.0) -> list[PlanResult]:
        async with self._result_added:
            await asyncio.wait_for(
                self._result_added.wait_for(lambda: len(self.results) >= count), timeout
            )
            return list(self.results)

    async def close(self) -> None:
        return None


# --- NATS JetStream ---------------------------------------------------------------


class NatsDelivery:
    def __init__(self, plan: Plan, msg: Any) -> None:
        self.plan = plan
        self._msg = msg

    async def ack(self) -> None:
        await self._msg.ack()

    async def nak(self, delay: float | None = None) -> None:
        await self._msg.nak(delay=delay)

    async def in_progress(self) -> None:
        await self._msg.in_progress()


class NatsBus:
    def __init__(self, nc: Any) -> None:
        self._nc = nc
        self._js = nc.jetstream()

    @classmethod
    async def connect(cls, url: str, *, name: str) -> NatsBus:
        import nats

        nc = await nats.connect(url, name=name, max_reconnect_attempts=-1)
        bus = cls(nc)
        await bus.ensure_streams()
        return bus

    async def ensure_streams(self) -> None:
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import BadRequestError

        configs = [
            StreamConfig(
                name=PLAN_STREAM,
                subjects=["olterra.plan.>"],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.FILE,
                max_age=3600,  # un plan de hace una hora ya no sirve
                duplicate_window=120,
            ),
            StreamConfig(
                name=RESULT_STREAM,
                subjects=["olterra.result.>"],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                max_age=24 * 3600,
            ),
        ]
        for config in configs:
            try:
                await self._js.add_stream(config=config)
            except BadRequestError:
                await self._js.update_stream(config=config)

    async def publish_plan(self, plan: Plan, group: str = "cloud") -> None:
        await self._js.publish(
            plan_subject(group, plan.tenant_id, plan.olt_id),
            plan.model_dump_json().encode(),
            headers={"Nats-Msg-Id": str(plan.plan_id)},
        )

    async def deliveries(self, group: str, *, batch: int = 16) -> AsyncIterator[Delivery]:
        from nats.errors import TimeoutError as NatsTimeout
        from nats.js.api import AckPolicy, ConsumerConfig

        subscription = await self._js.pull_subscribe(
            f"olterra.plan.{_check_group(group)}.>",
            durable=f"executor-{group}",
            stream=PLAN_STREAM,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=60,
                max_deliver=5,
                max_ack_pending=256,
            ),
        )
        while True:
            try:
                messages = await subscription.fetch(batch, timeout=5)
            except NatsTimeout:
                continue
            for msg in messages:
                try:
                    plan = Plan.model_validate_json(msg.data)
                except ValidationError:
                    log.error("Plan ilegible en %s; se descarta sin reintento", msg.subject)
                    await msg.term()
                    continue
                yield NatsDelivery(plan, msg)

    async def publish_result(self, result: PlanResult) -> None:
        await self._js.publish(
            result_subject(result.tenant_id, result.olt_id),
            result.model_dump_json().encode(),
            headers={"Nats-Msg-Id": f"result-{result.plan_id}"},
        )

    async def results(
        self, durable: str, *, batch: int = 64
    ) -> AsyncIterator[tuple[PlanResult, Any]]:
        """Lado nube: resultados con su mensaje (para acusarlo tras guardarlo)."""
        from nats.errors import TimeoutError as NatsTimeout

        subscription = await self._js.pull_subscribe(
            "olterra.result.>", durable=durable, stream=RESULT_STREAM
        )
        while True:
            try:
                messages = await subscription.fetch(batch, timeout=5)
            except NatsTimeout:
                continue
            for msg in messages:
                try:
                    yield PlanResult.model_validate_json(msg.data), msg
                except (ValidationError, json.JSONDecodeError):
                    log.error("Resultado ilegible en %s; se descarta", msg.subject)
                    await msg.term()

    async def close(self) -> None:
        await self._nc.drain()
