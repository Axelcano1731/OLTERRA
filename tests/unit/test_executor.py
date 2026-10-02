from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import SecretStr

from olterra.drivers.vsol_gpon import SESSION
from olterra.executor.bus import MemoryBus, MemoryDelivery, NatsBus
from olterra.executor.lanes import LaneScheduler
from olterra.executor.plan import (
    CliCommand,
    Credential,
    Plan,
    PlanResult,
    Priority,
    Target,
)
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor
from olterra.security.sealed import generate_keypair, seal


def make_plan(
    olt_id: object | None = None, priority: Priority = Priority.POLL, **kw: object
) -> Plan:
    return Plan(
        tenant_id=uuid4(),
        olt_id=olt_id or uuid4(),  # type: ignore[arg-type]
        priority=priority,
        target=Target(host="127.0.0.1"),
        session=SESSION,
        steps=[CliCommand(command="show version")],
        **kw,  # type: ignore[arg-type]
    )


# --- Colas por OLT ---------------------------------------------------------------------


async def test_priority_within_an_olt() -> None:
    order: list[str] = []
    release = asyncio.Event()

    async def handler(delivery: MemoryDelivery) -> None:  # type: ignore[override]
        if not order:
            await release.wait()
        order.append(delivery.plan.priority.name)

    bus = MemoryBus()
    scheduler = LaneScheduler(handler, max_concurrent=4)  # type: ignore[arg-type]
    olt = uuid4()
    first = MemoryDelivery(make_plan(olt, Priority.POLL), bus)
    scheduler.submit(first)
    await asyncio.sleep(0.01)  # el primero ya está corriendo
    for priority in (Priority.POLL, Priority.PROVISION, Priority.POLL, Priority.USER):
        scheduler.submit(MemoryDelivery(make_plan(olt, priority), bus))
    release.set()
    await scheduler.join(timeout=2)
    assert order == ["POLL", "USER", "PROVISION", "POLL", "POLL"]
    await scheduler.close()


async def test_one_session_per_olt_but_parallel_across_olts() -> None:
    running: dict[object, int] = {}
    max_same_olt = 0
    max_total = 0

    async def handler(delivery: MemoryDelivery) -> None:  # type: ignore[override]
        nonlocal max_same_olt, max_total
        olt = delivery.plan.olt_id
        running[olt] = running.get(olt, 0) + 1
        max_same_olt = max(max_same_olt, running[olt])
        max_total = max(max_total, sum(running.values()))
        await asyncio.sleep(0.02)
        running[olt] -= 1

    bus = MemoryBus()
    scheduler = LaneScheduler(handler, max_concurrent=8)  # type: ignore[arg-type]
    olts = [uuid4() for _ in range(3)]
    for _ in range(4):
        for olt in olts:
            scheduler.submit(MemoryDelivery(make_plan(olt), bus))
    await scheduler.join(timeout=5)
    assert max_same_olt == 1
    assert max_total == 3
    await scheduler.close()


async def test_global_session_limit() -> None:
    active = 0
    peak = 0

    async def handler(delivery: MemoryDelivery) -> None:  # type: ignore[override]
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1

    scheduler = LaneScheduler(handler, max_concurrent=2)  # type: ignore[arg-type]
    for _ in range(6):
        scheduler.submit(MemoryDelivery(make_plan(), MemoryBus()))
    await scheduler.join(timeout=5)
    assert peak == 2
    await scheduler.close()


async def test_heartbeat_keeps_waiting_plans_alive() -> None:
    release = asyncio.Event()

    async def handler(delivery: MemoryDelivery) -> None:  # type: ignore[override]
        await release.wait()

    scheduler = LaneScheduler(handler, heartbeat_interval=0.02)  # type: ignore[arg-type]
    olt = uuid4()
    deliveries = [MemoryDelivery(make_plan(olt), MemoryBus()) for _ in range(2)]
    for delivery in deliveries:
        scheduler.submit(delivery)
    await asyncio.sleep(0.1)
    assert all(d.heartbeats > 0 for d in deliveries)
    release.set()
    await scheduler.join(timeout=2)
    await scheduler.close()


# --- Ejecutor -------------------------------------------------------------------------------


class RecordingRunner(PlanRunner):
    def __init__(self) -> None:
        super().__init__("prueba")
        self.calls: list[tuple[Plan, Credential | None]] = []

    async def run(self, plan: Plan, credential: Credential | None) -> PlanResult:
        self.calls.append((plan, credential))
        now = datetime.now(UTC)
        return PlanResult(
            plan_id=plan.plan_id,
            tenant_id=plan.tenant_id,
            olt_id=plan.olt_id,
            executor=self.executor_id,
            status="ok",
            started_at=now,
            finished_at=now,
        )


def sealed_plan(public_key: str) -> Plan:
    plan = make_plan()
    credential = Credential(username="olterra", password=SecretStr("Clave#2026"))
    return plan.model_copy(
        update={"credential": seal(public_key, credential.reveal_json(), plan.seal_context())}
    )


async def test_executor_opens_sealed_credential() -> None:
    private, public = generate_keypair()
    bus, runner = MemoryBus(), RecordingRunner()
    executor = Executor(bus=bus, runner=runner, group="cloud", private_key=private)
    delivery = await bus.publish_plan(sealed_plan(public))
    await executor.handle(delivery)
    [(_, credential)] = runner.calls
    assert credential is not None and credential.password is not None
    assert credential.password.get_secret_value() == "Clave#2026"
    assert delivery.acked
    assert bus.results[0].status == "ok"


async def test_executor_rejects_credential_sealed_for_someone_else() -> None:
    private, _ = generate_keypair()
    _, other_public = generate_keypair()
    bus, runner = MemoryBus(), RecordingRunner()
    executor = Executor(bus=bus, runner=runner, group="cloud", private_key=private)
    delivery = await bus.publish_plan(sealed_plan(other_public))
    await executor.handle(delivery)
    assert runner.calls == []
    assert bus.results[0].status == "rejected"
    assert delivery.acked  # reintentarlo no lo arregla


async def test_executor_rejects_credential_copied_into_another_plan() -> None:
    private, public = generate_keypair()
    bus, runner = MemoryBus(), RecordingRunner()
    executor = Executor(bus=bus, runner=runner, group="cloud", private_key=private)
    stolen = sealed_plan(public).credential
    forged = make_plan().model_copy(update={"credential": stolen})
    await executor.handle(await bus.publish_plan(forged))
    assert runner.calls == []
    assert bus.results[0].status == "rejected"


async def test_redelivered_plan_is_not_executed_twice() -> None:
    private, public = generate_keypair()
    bus, runner = MemoryBus(), RecordingRunner()
    executor = Executor(bus=bus, runner=runner, group="cloud", private_key=private)
    plan = sealed_plan(public)
    await executor.handle(await bus.publish_plan(plan))
    await executor.handle(await bus.publish_plan(plan))  # JetStream lo reentrega
    assert len(runner.calls) == 1
    assert [r.plan_id for r in bus.results] == [plan.plan_id, plan.plan_id]


async def test_runner_expires_stale_plans_and_rejects_incomplete_ones() -> None:
    runner = PlanRunner("prueba")
    stale = make_plan(deadline=datetime.now(UTC) - timedelta(seconds=1))
    assert (await runner.run(stale, None)).status == "expired"
    no_session = make_plan().model_copy(update={"session": None})
    result = await runner.run(no_session, Credential(username="x"))
    assert result.status == "rejected"


# --- Bus NATS: la cola vacía no tumba a nadie ------------------------------------------


class _FakeMsg:
    subject = "olterra.prueba"

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def ack(self) -> None: ...

    async def term(self) -> None: ...


class _FakeSubscription:
    """fetch() que vence como lo hace nats-py y después entrega un mensaje."""

    def __init__(self, *outcomes: BaseException | list[_FakeMsg]) -> None:
        self.outcomes = list(outcomes)

    async def fetch(self, batch: int, timeout: float) -> list[_FakeMsg]:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeNats:
    def __init__(self, subscription: _FakeSubscription) -> None:
        self.subscription = subscription

    def jetstream(self) -> _FakeNats:
        return self

    async def pull_subscribe(self, *args: object, **kwargs: object) -> _FakeSubscription:
        return self.subscription


async def test_nats_consumers_survive_empty_fetches() -> None:
    # Con la cola vacía, nats-py lanza su TimeoutError o, si el plazo se agota entre el
    # pedido sin espera y el que espera, el de asyncio. Antes solo se atrapaba el primero:
    # el ejecutor y el consumidor de resultados de la API morían estando ociosos.
    from nats.errors import TimeoutError as NatsTimeout

    plan = make_plan()
    now = datetime.now(UTC)
    result = PlanResult(
        plan_id=plan.plan_id,
        tenant_id=plan.tenant_id,
        olt_id=plan.olt_id,
        executor="prueba",
        status="ok",
        started_at=now,
        finished_at=now,
    )

    def timeouts_then(data: bytes) -> _FakeNats:
        return _FakeNats(_FakeSubscription(TimeoutError(), NatsTimeout(), [_FakeMsg(data)]))

    plans = NatsBus(timeouts_then(plan.model_dump_json().encode())).deliveries("cloud")
    delivery = await anext(plans)
    assert delivery.plan.plan_id == plan.plan_id

    results = NatsBus(timeouts_then(result.model_dump_json().encode())).results("api")
    received, _ = await anext(results)
    assert received.plan_id == plan.plan_id
