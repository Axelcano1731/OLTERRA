"""Una cola por OLT, con prioridad, y una sola sesión a la vez contra cada OLT.

- Dentro de una OLT: acción de usuario > aprovisionamiento > monitoreo; a igual
  prioridad, en orden de llegada.
- Nunca dos planes a la vez contra la misma OLT: la CPU de una VSOL es poca y la
  CLI no está pensada para sesiones de escritura concurrentes.
- Entre OLT distintas sí hay paralelismo, con un tope global de sesiones.
- Mientras un plan espera o corre, se le avisa a JetStream que sigue en proceso
  para que no lo reentregue a otro ejecutor.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from olterra.executor.bus import Delivery

log = logging.getLogger(__name__)

Handler = Callable[[Delivery], Awaitable[None]]


@dataclass
class _Lane:
    queue: asyncio.PriorityQueue[tuple[int, int, Delivery]] = field(
        default_factory=asyncio.PriorityQueue
    )
    task: asyncio.Task[None] | None = None


class LaneScheduler:
    def __init__(
        self,
        handler: Handler,
        *,
        max_concurrent: int = 32,
        idle_timeout: float = 30.0,
        heartbeat_interval: float = 20.0,
    ) -> None:
        self._handler = handler
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._idle_timeout = idle_timeout
        self._heartbeat_interval = heartbeat_interval
        self._lanes: dict[UUID, _Lane] = {}
        self._pending: set[Delivery] = set()
        self._seq = itertools.count()
        self._idle = asyncio.Event()
        self._idle.set()
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def lane_count(self) -> int:
        return len(self._lanes)

    def submit(self, delivery: Delivery) -> None:
        olt_id = delivery.plan.olt_id
        lane = self._lanes.get(olt_id)
        if lane is None:
            lane = _Lane()
            self._lanes[olt_id] = lane
            lane.task = asyncio.create_task(self._run_lane(olt_id, lane), name=f"lane-{olt_id}")
        lane.queue.put_nowait((int(delivery.plan.priority), next(self._seq), delivery))
        self._pending.add(delivery)
        self._idle.clear()
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="lane-heartbeat")

    async def _run_lane(self, olt_id: UUID, lane: _Lane) -> None:
        while True:
            try:
                _, _, delivery = await asyncio.wait_for(lane.queue.get(), self._idle_timeout)
            except TimeoutError:
                if lane.queue.empty():
                    self._lanes.pop(olt_id, None)
                    return
                continue
            try:
                async with self._semaphore:
                    await self._handler(delivery)
            except Exception:
                log.exception("El manejador falló con el plan %s", delivery.plan.plan_id)
            finally:
                self._pending.discard(delivery)
                if not self._pending:
                    self._idle.set()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            for delivery in list(self._pending):
                try:
                    await delivery.in_progress()
                except Exception:
                    log.debug("No se pudo avisar progreso del plan %s", delivery.plan.plan_id)

    async def join(self, timeout: float | None = None) -> None:
        """Espera a que no quede ningún plan pendiente."""
        await asyncio.wait_for(self._idle.wait(), timeout)

    async def close(self) -> None:
        tasks = [lane.task for lane in self._lanes.values() if lane.task is not None]
        if self._heartbeat_task is not None:
            tasks.append(self._heartbeat_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._lanes.clear()
