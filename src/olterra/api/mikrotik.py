"""Sondeo del MikroTik del ISP: sesiones PPPoE, sus contadores y el plan de su cola.

Va junto al sondeo de las OLT: cuando se sondea una OLT que entra por un MikroTik puerta (A.20) y
ese router no se leyó en ``OLTERRA_MONITOR_ROUTER_EVERY_MINUTES`` (5), se le manda además un plan
de solo lectura con tres comandos (``drivers/routeros.py``). El resultado se procesa y se borra:

- ``pppoe_sessions``: las sesiones activas del router, reescritas.
- Cada ONU de las OLT de ese router se une con su usuario PPPoE: el que se puso en la ficha o, si
  no tiene, el que se llama igual que el cliente (sin tildes, espacios ni mayúsculas).
- En las OLT que no dan el tráfico por ONU (BCD), los contadores de la sesión hacen de contadores
  de la ONU: la velocidad y el consumo de la ficha salen del MikroTik.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from olterra.api.state import AppState
from olterra.db.models import Olt, OnuRate, OnuStatus, PlanRun, PppoeSessionRow, TunnelRouter
from olterra.db.session import tenant_session
from olterra.drivers import get_driver
from olterra.drivers.routeros import COMMANDS, SESSION, PppoeSession, combine, match_key
from olterra.executor.plan import (
    CliCommand,
    Credential,
    Gateway,
    Plan,
    PlanResult,
    Priority,
    Target,
)
from olterra.security.sealed import seal

log = logging.getLogger(__name__)

ACTOR = "olterra:mikrotik"


def router_plan(
    tenant_id: UUID, olt_id: UUID, via: Gateway, password: SecretStr, executor_public_key: str
) -> Plan:
    plan = Plan(
        tenant_id=tenant_id,
        olt_id=olt_id,
        priority=Priority.POLL,
        access="read",
        on_error="continue",
        target=Target(host=via.host, ssh_port=via.port, cli_protocol="ssh"),
        session=SESSION,
        steps=[CliCommand(command=command, timeout_s=30) for command in COMMANDS.values()],
    )
    credential = Credential(username=via.username, password=password)
    sealed = seal(executor_public_key, credential.reveal_json(), plan.seal_context())
    return plan.model_copy(update={"credential": sealed})


async def maybe_start(
    session: AsyncSession,
    state: AppState,
    tenant_id: UUID,
    olt: Olt,
    via: Gateway | None,
    password: SecretStr | None,
    executor_public_key: str,
) -> Plan | None:
    """El plan del router de esta OLT si toca leerlo; None si no (lo publica quien llama)."""
    every = state.settings.monitor_router_every_minutes
    if every <= 0 or via is None or password is None or olt.router_id is None:
        return None
    router = await session.get(TunnelRouter, olt.router_id)
    if router is None:
        return None
    now = datetime.now(UTC)
    last = router.monitor_requested_at
    if last is not None and now - last < timedelta(minutes=every) - timedelta(seconds=30):
        return None
    plan = router_plan(tenant_id, olt.id, via, password, executor_public_key)
    session.add(
        PlanRun(
            id=plan.plan_id,
            tenant_id=tenant_id,
            olt_id=olt.id,
            requested_by=ACTOR,
            priority=int(plan.priority),
            access=plan.access,
            calls=[
                {"key": key, "params": {}, "command": command} for key, command in COMMANDS.items()
            ],
            commands=list(COMMANDS.values()),
        )
    )
    router.monitor_requested_at = now
    router.monitor_plan_id = plan.plan_id
    return plan


async def on_result(state: AppState, result: PlanResult) -> None:
    """Si el plan era un sondeo de router: guarda sus sesiones y las une con las ONU."""
    async with tenant_session(state.sessions, result.tenant_id) as session:
        router = (
            await session.execute(
                select(TunnelRouter).where(TunnelRouter.monitor_plan_id == result.plan_id)
            )
        ).scalar_one_or_none()
        if router is None:
            return
        router.monitor_plan_id = None
        run = await session.get(PlanRun, result.plan_id)
        outputs = {
            o.get("key"): o for o in ((run.result if run else None) or {}).get("outputs", [])
        }
        if run is not None:
            await session.delete(run)  # no queda en el historial de consultas
        texts = []
        for key in COMMANDS:
            output = outputs.get(key) or {}
            if not output.get("ok"):
                log.info(
                    "Sondeo del router %s: %s falló (%s)", router.name, key, output.get("error")
                )
                return
            texts.append(output.get("output") or "")
        await apply_sessions(session, state, result.tenant_id, router, combine(*texts))


async def apply_sessions(
    session: AsyncSession,
    state: AppState,
    tenant_id: UUID,
    router: TunnelRouter,
    sessions: list[PppoeSession],
) -> None:
    from olterra.api.monitoring import _account_traffic, reads_traffic

    now = datetime.now(UTC)
    await session.execute(delete(PppoeSessionRow).where(PppoeSessionRow.router_id == router.id))
    for item in sessions:
        session.add(
            PppoeSessionRow(
                tenant_id=tenant_id,
                router_id=router.id,
                pppoe_user=item.user,
                mac=item.mac,
                address=item.address,
                uptime=item.uptime,
                down_bytes=item.down_bytes,
                up_bytes=item.up_bytes,
                plan_down_bps=item.plan_down_bps,
                plan_up_bps=item.plan_up_bps,
                seen_at=now,
            )
        )
    by_user = {item.user: item for item in sessions}
    by_key: dict[str, PppoeSession] = {}
    for item in sessions:
        by_key.setdefault(match_key(item.user), item)
    olts = (await session.execute(select(Olt).where(Olt.router_id == router.id))).scalars().all()
    hourly: list[dict[str, Any]] = []
    rates: list[OnuRate] = []
    for olt in olts:
        from_router = not reads_traffic(get_driver(olt.driver))
        statuses = (
            await session.execute(select(OnuStatus).where(OnuStatus.olt_id == olt.id))
        ).scalars()
        for status in statuses:
            if status.pppoe_user_source != "manual":
                found = next(
                    (
                        by_key[k]
                        for k in (match_key(status.customer_name), match_key(status.description))
                        if k and k in by_key
                    ),
                    None,
                )
                if found is not None:
                    status.pppoe_user, status.pppoe_user_source = found.user, "nombre"
            current = by_user.get(status.pppoe_user or "")
            if not from_router or current is None:
                continue
            if current.down_bytes is None or current.up_bytes is None:
                continue
            counters = (current.down_bytes, current.up_bytes)
            hourly += _account_traffic(
                tenant_id, olt.id, status, (status.pon, status.onu), counters, now
            )
            if status.down_bps is not None and status.up_bps is not None:
                rates.append(
                    OnuRate(
                        tenant_id=tenant_id,
                        olt_id=olt.id,
                        pon=status.pon,
                        onu=status.onu,
                        at=now,
                        down_bps=status.down_bps,
                        up_bps=status.up_bps,
                    )
                )
    if rates:
        session.add_all(rates)
    if hourly:
        from olterra.api.monitoring import save_hourly

        await save_hourly(session, hourly)
