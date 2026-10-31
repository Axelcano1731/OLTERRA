"""Monitoreo de punta a punta contra el simulador por SSH: estado, potencia, alarmas y caída de PON."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, update

from olterra.api import monitoring
from olterra.db.models import Olt, OnuStatus, PlanRun
from olterra.db.session import create_engine, session_factory
from tests.integration.test_api import (  # noqa: F401 - fixtures y ayudas compartidas
    Env,
    SimulatorThread,
    create_olt,
    env,
)
from tests.integration.test_provision_jobs import run_plans

pytestmark = pytest.mark.postgres


def poll(env: Env, olt_id: str, done: set[UUID]) -> None:  # noqa: F811
    queued = env.client.post(f"/v1/olts/{olt_id}/monitoring/poll", headers=env.headers())
    assert queued.status_code == 202, queued.text
    run_plans(env, done)


def alarms(env: Env, olt_id: str) -> dict[tuple[int, int, str], dict[str, Any]]:  # noqa: F811
    rows = env.client.get("/v1/alarms", params={"olt_id": olt_id}, headers=env.headers()).json()
    return {(a["pon"], a["onu"], a["kind"]): a for a in rows}


async def _owner(env: Env, fn: Any) -> Any:  # noqa: F811
    engine = create_engine(env.pg.owner_url)
    try:
        async with session_factory(engine)() as session, session.begin():
            return await fn(session)
    finally:
        await engine.dispose()


def test_monitoring_end_to_end(env: Env) -> None:  # noqa: F811
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    done: set[UUID] = set()
    with SimulatorThread() as simulator:
        olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port)
        poll(env, olt["id"], done)

        # 1) Estado: en el simulador, 4 ONU; la 1/3 está caída y la 2/1 tiene señal muy baja.
        summary = env.client.get("/v1/monitoring/summary", headers=env.headers()).json()
        mine = next(o for o in summary["olts"] if o["id"] == olt["id"])
        assert (mine["onus"], mine["online"], mine["offline"], mine["low_signal"]) == (4, 3, 1, 1)
        assert mine["monitored_at"] is not None
        onus = env.client.get(
            "/v1/monitoring/onus", params={"olt_id": olt["id"]}, headers=env.headers()
        ).json()
        by_ref = {(o["pon"], o["onu"]): o for o in onus}
        assert by_ref[(1, 2)]["description"] == "demo-cliente-2"
        assert by_ref[(1, 2)]["rx_dbm"] == -24.8
        assert by_ref[(1, 3)]["online"] is False and by_ref[(1, 3)]["phase_label"] == "Desconectada"
        low = env.client.get(
            "/v1/monitoring/onus", params={"filter": "low"}, headers=env.headers()
        ).json()
        assert [(o["pon"], o["onu"]) for o in low] == [(2, 1)]
        found = alarms(env, olt["id"])
        assert set(found) == {(1, 3, "onu_offline"), (2, 1, "rx_low")}
        assert found[(2, 1, "rx_low")]["severity"] == "critical"

        # El sondeo no queda en el historial de consultas de la OLT.
        assert env.client.get(f"/v1/olts/{olt['id']}/plans", headers=env.headers()).json() == []

        # 2) Se corta la fibra del PON 1: caída de PON, con la causa.
        for onu in simulator.sim.config.onus:
            if onu.pon == 1:
                onu.state = "LOS"
        poll(env, olt["id"], done)
        found = alarms(env, olt["id"])
        assert found[(1, 0, "pon_down")]["message"] == (
            "PON 1: 3 de 3 clientes sin servicio (posible corte de fibra o splitter)"
        )
        assert found[(1, 1, "onu_offline")]["severity"] == "critical"
        # Las críticas de PON van primero.
        first = env.client.get("/v1/alarms", headers=env.headers()).json()[0]
        assert first["kind"] == "pon_down" and first["olt_name"] == olt["name"]

        # 3) Vuelve la fibra: las alarmas se cierran solas y quedan en el historial.
        for onu in simulator.sim.config.onus:
            if onu.pon == 1:
                onu.state = "working"
        poll(env, olt["id"], done)
        assert set(alarms(env, olt["id"])) == {(2, 1, "rx_low")}
        closed = env.client.get(
            "/v1/alarms", params={"state": "all", "olt_id": olt["id"]}, headers=env.headers()
        ).json()
        assert any(a["kind"] == "pon_down" and a["cleared_at"] for a in closed)
        history = env.client.get(
            f"/v1/monitoring/onus/{olt['id']}/1/1", headers=env.headers()
        ).json()
        assert [s["online"] for s in history["samples"]] == [True, False, True]
        assert {a["kind"] for a in history["alarms"]} == {"onu_offline"}

        # 4) Sin respuesta de la OLT: alarma de OLT y el estado de las ONU no se toca.
        simulator.sim.config.password = "otra-clave"
        poll(env, olt["id"], done)
        assert (0, 0, "olt_unreachable") in alarms(env, olt["id"])
        simulator.sim.config.password = "olterra-sim"
        poll(env, olt["id"], done)
        assert (0, 0, "olt_unreachable") not in alarms(env, olt["id"])

        # 5) El reloj: nada mientras haya un sondeo reciente; uno nuevo cuando venció.
        async def age(session: Any) -> None:
            await session.execute(
                update(Olt)
                .where(Olt.id == UUID(olt["id"]))
                .values(monitor_requested_at=Olt.monitor_requested_at - timedelta(minutes=30))
            )

        async def pending(session: Any) -> int:
            rows = await session.execute(
                select(PlanRun.id).where(
                    PlanRun.olt_id == UUID(olt["id"]), PlanRun.requested_by == monitoring.ACTOR
                )
            )
            return len(rows.all())

        env.client.portal.call(monitoring.tick, state)  # type: ignore[union-attr]
        assert asyncio.run(_owner(env, pending)) == 0
        asyncio.run(_owner(env, age))
        env.client.portal.call(monitoring.tick, state)  # type: ignore[union-attr]
        assert asyncio.run(_owner(env, pending)) == 1

    # Otro ISP no ve nada.
    other = env.headers(env.key_b)
    assert env.client.get("/v1/alarms", headers=other).json() == []
    assert env.client.get("/v1/monitoring/onus", headers=other).json() == []
    assert env.client.get(f"/v1/monitoring/onus/{olt['id']}/1/1", headers=other).status_code == 404


def test_traffic_end_to_end(env: Env) -> None:  # noqa: F811
    """El sondeo lee los contadores cada tanto y guarda lo que bajó y subió cada hora."""
    done: set[UUID] = set()
    with SimulatorThread() as simulator:
        olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port)
        first = next(o for o in simulator.sim.config.onus if (o.pon, o.onu) == (1, 1))
        first.down_bytes, first.up_bytes = 5_000_000_000, 400_000_000
        poll(env, olt["id"], done)  # primera lectura: solo guarda los contadores
        url = f"/v1/monitoring/onus/{olt['id']}/1/1/traffic"
        traffic = env.client.get(url, headers=env.headers()).json()
        assert traffic["supported"] is True and traffic["hours"] == []

        async def fifteen_minutes_ago(session: Any) -> None:
            await session.execute(
                update(Olt)
                .where(Olt.id == UUID(olt["id"]))
                .values(traffic_requested_at=Olt.traffic_requested_at - timedelta(minutes=15))
            )
            await session.execute(
                update(OnuStatus)
                .where(OnuStatus.olt_id == UUID(olt["id"]))
                .values(traffic_at=OnuStatus.traffic_at - timedelta(minutes=15))
            )

        # 15 minutos después bajó 900 MB y subió 100 MB: 8 Mbps de bajada en promedio.
        first.down_bytes += 900_000_000
        first.up_bytes += 100_000_000
        asyncio.run(_owner(env, fifteen_minutes_ago))
        poll(env, olt["id"], done)
        traffic = env.client.get(url, headers=env.headers()).json()
        assert sum(h["down_bytes"] for h in traffic["hours"]) == 900_000_000
        assert sum(h["up_bytes"] for h in traffic["hours"]) == 100_000_000
        assert 7_800_000 < traffic["down_bps"] < 8_100_000
        assert max(h["peak_down_bps"] for h in traffic["hours"]) == traffic["down_bps"]
        listed = env.client.get(
            "/v1/monitoring/onus", params={"olt_id": olt["id"]}, headers=env.headers()
        ).json()
        assert next(o for o in listed if (o["pon"], o["onu"]) == (1, 1))["down_bps"] > 0

        # Sin que toque leer el tráfico (pasaron menos de 15 minutos) no se suma nada.
        first.down_bytes += 1_000
        poll(env, olt["id"], done)
        again = env.client.get(url, headers=env.headers()).json()
        assert sum(h["down_bytes"] for h in again["hours"]) == 900_000_000

        # La ONU se reinició: el contador volvió a empezar y cuenta desde cero.
        first.down_bytes, first.up_bytes = 50_000_000, 0
        asyncio.run(_owner(env, fifteen_minutes_ago))
        poll(env, olt["id"], done)
        after = env.client.get(url, headers=env.headers()).json()
        assert sum(h["down_bytes"] for h in after["hours"]) == 950_000_000

    # Otro ISP no ve el consumo de este.
    assert env.client.get(url, headers=env.headers(env.key_b)).status_code == 404
