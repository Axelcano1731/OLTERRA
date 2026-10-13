"""Monitoreo: resumen, estado de cada ONU, historial de potencia, alarmas y sondear ahora.

La lógica (sondeo, reglas, historial) está en ``api/monitoring.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from olterra.api import monitoring
from olterra.api.deps import State, Tenant
from olterra.api.routes.olts import executor_key
from olterra.api.schemas import (
    AlarmOut,
    MonitoringSummary,
    OltHealth,
    OnuSampleOut,
    OnuStatusOut,
)
from olterra.db.models import Alarm, Olt, OnuSample, OnuStatus
from olterra.executor.plan import Priority

router = APIRouter(tags=["Monitoreo"])

LOW = monitoring.RX_LOW_WARNING


def _status_out(row: OnuStatus) -> OnuStatusOut:
    return OnuStatusOut(
        olt_id=row.olt_id,
        pon=row.pon,
        onu=row.onu,
        serial=row.serial,
        description=row.description,
        phase=row.phase,
        phase_label=monitoring.phase_label(row.phase),
        online=row.online,
        rx_dbm=float(row.rx_dbm) if row.rx_dbm is not None else None,
        status_since=row.status_since,
        last_online_at=row.last_online_at,
        updated_at=row.updated_at,
    )


@router.get("/v1/monitoring/summary", response_model=MonitoringSummary)
async def summary(ctx: Tenant, state: State) -> MonitoringSummary:
    ctx.require("olt:read")
    async with ctx.session() as session:
        olts = (await session.execute(select(Olt).order_by(Olt.name))).scalars().all()
        counts = {
            row.olt_id: row
            for row in (
                await session.execute(
                    select(
                        OnuStatus.olt_id,
                        func.count().label("total"),
                        func.count().filter(OnuStatus.online).label("online"),
                        func.count().filter(OnuStatus.online, OnuStatus.rx_dbm < LOW).label("low"),
                    ).group_by(OnuStatus.olt_id)
                )
            ).all()
        }
        open_alarms = (
            await session.execute(
                select(Alarm.olt_id, Alarm.severity, func.count())
                .where(Alarm.cleared_at.is_(None))
                .group_by(Alarm.olt_id, Alarm.severity)
            )
        ).all()
    per_olt: dict[UUID, int] = {}
    severity = {"critical": 0, "warning": 0}
    for olt_id, level, count in open_alarms:
        per_olt[olt_id] = per_olt.get(olt_id, 0) + count
        severity[level] = severity.get(level, 0) + count
    health = []
    for olt in olts:
        row = counts.get(olt.id)
        total, online, low = (row.total, row.online, row.low) if row else (0, 0, 0)
        health.append(
            OltHealth(
                id=olt.id,
                name=olt.name,
                status=olt.status,
                monitored_at=olt.monitored_at,
                onus=total,
                online=online,
                offline=total - online,
                low_signal=low,
                alarms=per_olt.get(olt.id, 0),
            )
        )
    return MonitoringSummary(
        every_minutes=state.settings.monitor_every_minutes,
        onus=sum(h.onus for h in health),
        online=sum(h.online for h in health),
        offline=sum(h.offline for h in health),
        low_signal=sum(h.low_signal for h in health),
        critical=severity["critical"],
        warning=severity["warning"],
        olts=health,
    )


@router.get("/v1/monitoring/onus", response_model=list[OnuStatusOut])
async def list_onus(
    ctx: Tenant,
    olt_id: UUID | None = None,
    pon: int | None = Query(None, ge=1, le=16),
    filter: Literal["all", "online", "offline", "low"] = "all",
    q: str | None = Query(None, max_length=64, description="Cliente o serial"),
    limit: int = Query(2000, ge=1, le=5000),
) -> list[OnuStatusOut]:
    ctx.require("olt:read")
    query = select(OnuStatus).order_by(OnuStatus.olt_id, OnuStatus.pon, OnuStatus.onu).limit(limit)
    if olt_id is not None:
        query = query.where(OnuStatus.olt_id == olt_id)
    if pon is not None:
        query = query.where(OnuStatus.pon == pon)
    if filter == "online":
        query = query.where(OnuStatus.online)
    elif filter == "offline":
        query = query.where(OnuStatus.online.is_(False))
    elif filter == "low":
        query = query.where(OnuStatus.online, OnuStatus.rx_dbm < LOW)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(OnuStatus.description.ilike(like), OnuStatus.serial.ilike(like)))
    async with ctx.session() as session:
        return [_status_out(row) for row in (await session.execute(query)).scalars()]


class OnuHistory(BaseModel):
    status: OnuStatusOut
    samples: list[OnuSampleOut]
    alarms: list[AlarmOut]


@router.get("/v1/monitoring/onus/{olt_id}/{pon}/{onu}", response_model=OnuHistory)
async def onu_history(
    olt_id: UUID,
    pon: int,
    onu: int,
    ctx: Tenant,
    hours: int = Query(168, ge=1, le=24 * 30),
) -> OnuHistory:
    """Estado, historial de potencia (por defecto 7 días) y alarmas recientes de una ONU."""
    ctx.require("olt:read")
    since = datetime.now(UTC) - timedelta(hours=hours)
    async with ctx.session() as session:
        row = await session.get(OnuStatus, (ctx.tenant_id, olt_id, pon, onu))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa ONU no está en el monitoreo")
        samples = (
            await session.execute(
                select(OnuSample)
                .where(
                    OnuSample.olt_id == olt_id,
                    OnuSample.pon == pon,
                    OnuSample.onu == onu,
                    OnuSample.at >= since,
                )
                .order_by(OnuSample.at)
            )
        ).scalars()
        alarms = (
            await session.execute(
                select(Alarm)
                .where(
                    Alarm.olt_id == olt_id,
                    Alarm.pon == pon,
                    Alarm.onu == onu,
                    or_(Alarm.cleared_at.is_(None), Alarm.started_at >= since),
                )
                .order_by(Alarm.started_at.desc())
                .limit(50)
            )
        ).scalars()
        return OnuHistory(
            status=_status_out(row),
            samples=[OnuSampleOut.model_validate(s) for s in samples],
            alarms=[AlarmOut.model_validate(a) for a in alarms],
        )


@router.get("/v1/alarms", response_model=list[AlarmOut])
async def list_alarms(
    ctx: Tenant,
    state_filter: Literal["open", "all"] = Query("open", alias="state"),
    olt_id: UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[AlarmOut]:
    """Alarmas abiertas (o todas, las más recientes primero). Las críticas van primero."""
    ctx.require("olt:read")
    query = (
        select(Alarm, Olt.name)
        .join(Olt, Olt.id == Alarm.olt_id)
        .order_by(
            Alarm.cleared_at.is_not(None),
            (Alarm.severity != "critical"),
            Alarm.kind.notin_(["olt_unreachable", "pon_down"]),
            Alarm.started_at.desc(),
        )
        .limit(limit)
    )
    if state_filter == "open":
        query = query.where(Alarm.cleared_at.is_(None))
    if olt_id is not None:
        query = query.where(Alarm.olt_id == olt_id)
    async with ctx.session() as session:
        rows = (await session.execute(query)).all()
    return [
        AlarmOut.model_validate(alarm).model_copy(update={"olt_name": name}) for alarm, name in rows
    ]


class PollOut(BaseModel):
    queued: bool


@router.post(
    "/v1/olts/{olt_id}/monitoring/poll",
    response_model=PollOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def poll_now(olt_id: UUID, ctx: Tenant, state: State) -> PollOut:
    """Sondea ya la OLT (estado y potencia de sus ONU), con prioridad de usuario."""
    ctx.require("olt:read")
    executor_key(state)
    state.require_bus()
    async with ctx.session() as session:
        olt = await session.get(Olt, olt_id)
        if olt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")
        requested = olt.monitor_requested_at
        recent = requested is not None and requested > datetime.now(UTC) - timedelta(minutes=2)
        if olt.monitor_plan_id is not None and recent:
            return PollOut(queued=True)  # ya hay uno en camino
    queued = await monitoring.start_poll(state, ctx.tenant_id, olt_id, Priority.USER)
    if not queued:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No se pudo sondear: la OLT no tiene IP o credencial"
        )
    return PollOut(queued=True)
