"""Bus de planes sobre NATS JetStream real (``OLTERRA_TEST_NATS_URL``, con -js)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from olterra.drivers.vsol_gpon import SESSION
from olterra.executor.bus import NatsBus
from olterra.executor.plan import CliCommand, Credential, Plan, PlanResult, Target
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor

pytestmark = pytest.mark.nats


class EchoRunner(PlanRunner):
    def __init__(self) -> None:
        super().__init__("prueba-nats")
        self.ran: list[Plan] = []

    async def run(self, plan: Plan, credential: Credential | None) -> PlanResult:
        self.ran.append(plan)
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


def plan() -> Plan:
    return Plan(
        tenant_id=uuid4(),
        olt_id=uuid4(),
        target=Target(host="198.19.0.10"),
        session=SESSION,
        steps=[CliCommand(command="show version")],
    )


async def test_plans_flow_through_jetstream_with_dedup() -> None:
    url = os.environ["OLTERRA_TEST_NATS_URL"]
    group = f"t{uuid4().hex[:8]}"
    cloud = await NatsBus.connect(url, name="prueba-nube")
    edge = await NatsBus.connect(url, name="prueba-ejecutor")
    runner = EchoRunner()
    executor = Executor(bus=edge, runner=runner, group=group, private_key=None)
    serve = asyncio.create_task(executor.serve())
    try:
        first, second = plan(), plan()
        await cloud.publish_plan(first, group)
        await cloud.publish_plan(first, group)  # reintento de la nube: JetStream lo descarta
        await cloud.publish_plan(second, group)

        wanted = {first.plan_id, second.plan_id}
        received: dict[object, PlanResult] = {}

        async def collect() -> None:
            async for result, msg in cloud.results(f"resultados-{group}"):
                await msg.ack()
                if result.plan_id in wanted:
                    received[result.plan_id] = result
                    if len(received) == len(wanted):
                        return

        await asyncio.wait_for(collect(), 20)
        assert set(received) == wanted
        assert all(r.status == "ok" for r in received.values())
        assert sorted(p.plan_id for p in runner.ran) == sorted(wanted)  # cada plan una sola vez
    finally:
        serve.cancel()
        await asyncio.gather(serve, return_exceptions=True)
        await executor.scheduler.close()
        await edge.close()
        await cloud.close()
