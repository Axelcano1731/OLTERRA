"""Monitoreo de las ONU: sondeo periódico de estado y potencia, historial y alarmas.

Cada ``OLTERRA_MONITOR_EVERY_MINUTES`` (5) se pide a cada OLT que ya respondió alguna vez un plan
de solo lectura con prioridad de sondeo (lo que pida un usuario va primero):

    show interface brief          todas las ONU con su cliente (descripción)
    show onu state         (PON)  fase de cada ONU: working, LOS, dyingGasp, offline…
    show pon onu all rx-power (PON) potencia recibida de cada ONU

El resultado se procesa y se borra: el estado queda en ``onu_status``, el historial de potencia en
``onu_samples`` (sin guardar cada sondeo) y las alarmas en ``alarms``. Ni el historial de consultas
de la OLT ni la base compartida se llenan con sondeos.

Las reglas (``evaluate``) son funciones puras: ventana de recepción GPON clase B+ de −8 a −27 dBm
(ARQUITECTURA, modelo de la red), aviso desde −25 dBm, y una caída de PON cuando la mayoría de sus
ONU están caídas a la vez (el ancestro común más probable es el PON, el splitter o la fibra).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from olterra.api.routes.olts import executor_key, olt_access, publish_plan, record_plan
from olterra.api.state import AppState
from olterra.db.models import Alarm, Olt, OnuSample, OnuStatus, PlanRun
from olterra.db.session import tenant_session
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.executor.plan import PlanResult, Priority
from olterra.orchestrator import build_read_plan

log = logging.getLogger(__name__)

ACTOR = "olterra:monitoreo"
DEFAULT_PONS = 4  # hasta que "show interface brief" diga cuántos tiene

RX_LOW_CRITICAL = -27.0  # ventana GPON clase B+
RX_LOW_WARNING = -25.0  # aviso antes de que el cliente se quede sin servicio
RX_HIGH = -8.0  # saturación del receptor
PON_DOWN_MIN = 3  # ONU caídas a la vez en un PON para sospechar del PON
PON_DOWN_RATIO = 0.7

SAMPLE_EVERY = timedelta(hours=1)
SAMPLE_DELTA_DB = 1.0

PHASES = {
    "working": "En línea",
    "los": "Sin luz (fibra)",
    "dyinggasp": "Apagada (sin energía)",
    "offline": "Desconectada",
    "authfailed": "Autenticación fallida",
    "syncmib": "Conectándose",
}


def phase_label(phase: str | None) -> str:
    if not phase:
        return "Sin dato"
    return PHASES.get(phase.lower(), phase)


# --- Reglas (puras) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """Lo que dijo la OLT de una ONU en un sondeo."""

    pon: int
    onu: int
    online: bool
    phase: str | None = None
    serial: str | None = None
    description: str | None = None
    rx_dbm: float | None = None


@dataclass(frozen=True)
class AlarmSpec:
    pon: int
    onu: int
    kind: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.pon, self.onu, self.kind)


def merge_readings(outputs: list[dict[str, Any]]) -> tuple[list[Reading], bool]:
    """Junta las salidas del sondeo por ONU. Devuelve ``(lecturas, lista_completa)``.

    ``lista_completa`` es True si ``show interface brief`` respondió: entonces una ONU que no
    aparece ya no existe en la OLT.
    """
    brief: dict[tuple[int, int], dict[str, Any]] = {}
    states: dict[tuple[int, int], dict[str, Any]] = {}
    powers: dict[tuple[int, int], float | None] = {}
    complete = False
    for output in outputs:
        data = output.get("data")
        if not output.get("ok") or data is None:
            continue
        key = output.get("key")
        if key == "interfaces.brief" and isinstance(data, dict):
            complete = True
            for row in data.get("onus") or []:
                brief[(int(row["pon"]), int(row["onu"]))] = row
        elif key == "onu.state" and isinstance(data, list):
            for row in data:
                pon = row.get("pon") or (output.get("params") or {}).get("pon")
                if pon is not None:
                    states[(int(pon), int(row["onu"]))] = row
        elif key == "onu.rx_power_all" and isinstance(data, list):
            for row in data:
                pon = row.get("pon") or (output.get("params") or {}).get("pon")
                if pon is not None:
                    powers[(int(pon), int(row["onu"]))] = row.get("rx_dbm")
    readings = []
    for pon, onu in sorted(set(brief) | set(states)):
        state = states.get((pon, onu))
        port = brief.get((pon, onu))
        phase: str | None
        if state is not None:
            phase = str(state.get("phase") or "") or None
            online = (phase or "").lower() == "working"
        else:  # el PON no respondió a "show onu state": se usa arriba/abajo del brief
            online = bool(port and port.get("up"))
            phase = "working" if online else None
        rx = powers.get((pon, onu))
        readings.append(
            Reading(
                pon=pon,
                onu=onu,
                online=online,
                phase=phase,
                serial=(state or {}).get("serial"),
                description=(port or {}).get("description"),
                rx_dbm=float(rx) if rx is not None and online else None,
            )
        )
    return readings, complete


def _dbm(value: float) -> str:
    return f"{value:.2f} dBm".replace(".", ",")  # como lo escribe la interfaz (es-CO)


def _who(reading: Reading) -> str:
    return reading.description or f"ONU {reading.pon}/{reading.onu}"


def evaluate(readings: list[Reading]) -> list[AlarmSpec]:
    """Las alarmas que deben estar abiertas según este sondeo."""
    alarms: list[AlarmSpec] = []
    by_pon: dict[int, list[Reading]] = {}
    for reading in readings:
        by_pon.setdefault(reading.pon, []).append(reading)
        base = {"serial": reading.serial, "description": reading.description}
        if not reading.online:
            phase = (reading.phase or "").lower()
            alarms.append(
                AlarmSpec(
                    reading.pon,
                    reading.onu,
                    "onu_offline",
                    "critical" if phase == "los" else "warning",
                    f"{_who(reading)}: {phase_label(reading.phase)}",
                    {**base, "phase": reading.phase},
                )
            )
            continue
        rx = reading.rx_dbm
        if rx is None:
            continue
        if rx < RX_LOW_WARNING:
            critical = rx < RX_LOW_CRITICAL
            alarms.append(
                AlarmSpec(
                    reading.pon,
                    reading.onu,
                    "rx_low",
                    "critical" if critical else "warning",
                    f"{_who(reading)}: señal {'muy ' if critical else ''}baja ({_dbm(rx)})",
                    {**base, "rx_dbm": rx},
                )
            )
        elif rx > RX_HIGH:
            alarms.append(
                AlarmSpec(
                    reading.pon,
                    reading.onu,
                    "rx_high",
                    "warning",
                    f"{_who(reading)}: señal demasiado alta ({_dbm(rx)}), satura la ONU",
                    {**base, "rx_dbm": rx},
                )
            )
    for pon, rows in sorted(by_pon.items()):
        down = [r for r in rows if not r.online]
        if len(down) < PON_DOWN_MIN or len(down) / len(rows) < PON_DOWN_RATIO:
            continue
        causes = Counter((r.phase or "").lower() for r in down)
        cause = causes.most_common(1)[0][0]
        hint = {
            "los": "posible corte de fibra o splitter",
            "dyinggasp": "posible corte de energía en la zona",
        }.get(cause, "revisa el PON")
        alarms.append(
            AlarmSpec(
                pon,
                0,
                "pon_down",
                "critical",
                f"PON {pon}: {len(down)} de {len(rows)} clientes sin servicio ({hint})",
                {"down": len(down), "total": len(rows), "cause": cause or None},
            )
        )
    return alarms


def wants_sample(
    previous: tuple[datetime, bool, float | None] | None, reading: Reading, now: datetime
) -> bool:
    """Una muestra por hora, o antes si cambió la señal 1 dB o más o la ONU cayó o volvió."""
    if previous is None:
        return True
    at, online, rx = previous
    if online != reading.online or now - at >= SAMPLE_EVERY:
        return True
    if rx is None or reading.rx_dbm is None:
        return rx != reading.rx_dbm
    return abs(rx - reading.rx_dbm) >= SAMPLE_DELTA_DB


# --- Sondeo -----------------------------------------------------------------------------------


def poll_calls(pons: int) -> list[CommandCall]:
    calls = [CommandCall("interfaces.brief")]
    for pon in range(1, pons + 1):
        calls += [
            CommandCall("onu.state", {"pon": pon}),
            CommandCall("onu.rx_power_all", {"pon": pon}),
        ]
    return calls


async def start_poll(state: AppState, tenant_id: UUID, olt_id: UUID, priority: Priority) -> bool:
    """Pide un sondeo de la OLT. False si no se pudo ni pedir (sin IP, sin credencial…)."""
    plan = None
    async with tenant_session(state.sessions, tenant_id) as session:
        olt = await session.get(Olt, olt_id)
        if olt is None:
            return False
        olt.monitor_requested_at = datetime.now(UTC)
        try:
            olt, credential, target = await olt_access(session, tenant_id, state, olt_id)
            calls = poll_calls(olt.pon_ports or DEFAULT_PONS)
            plan = build_read_plan(
                get_driver(olt.driver),
                tenant_id=tenant_id,
                olt_id=olt.id,
                target=target,
                calls=calls,
                credential=credential,
                executor_public_key=executor_key(state),
                model=olt.model,
                firmware=olt.firmware,
                priority=priority,
            )
            await record_plan(session, tenant_id, ACTOR, olt, plan, calls)
            olt.monitor_plan_id = plan.plan_id
        except HTTPException as exc:
            log.info("Sin sondeo para la OLT %s: %s", olt_id, exc.detail)
            olt.monitor_plan_id = None
            return False
        except Exception:
            log.exception("No se pudo preparar el sondeo de la OLT %s", olt_id)
            olt.monitor_plan_id = None
            return False
    try:
        await publish_plan(state, plan)
    except HTTPException:
        async with tenant_session(state.sessions, tenant_id) as session:
            failed = await session.get(Olt, olt_id)
            if failed is not None:
                failed.monitor_plan_id = None
        return False
    return True


async def _sync_alarms(
    session: AsyncSession,
    tenant_id: UUID,
    olt_id: UUID,
    wanted: list[AlarmSpec],
    now: datetime,
    kinds: set[str],
) -> None:
    """Abre las que faltan, actualiza las que siguen y cierra las que ya no están (de ``kinds``)."""
    current = {
        (a.pon, a.onu, a.kind): a
        for a in (
            await session.execute(
                select(Alarm).where(
                    Alarm.olt_id == olt_id, Alarm.cleared_at.is_(None), Alarm.kind.in_(kinds)
                )
            )
        ).scalars()
    }
    for spec in wanted:
        alarm = current.pop(spec.key, None)
        if alarm is None:
            session.add(
                Alarm(
                    tenant_id=tenant_id,
                    olt_id=olt_id,
                    pon=spec.pon,
                    onu=spec.onu,
                    kind=spec.kind,
                    severity=spec.severity,
                    message=spec.message,
                    detail=spec.detail,
                    started_at=now,
                    last_seen_at=now,
                )
            )
        else:
            alarm.severity, alarm.message, alarm.detail = spec.severity, spec.message, spec.detail
            alarm.last_seen_at = now
    for alarm in current.values():
        alarm.cleared_at = now


ONU_KINDS = {"onu_offline", "rx_low", "rx_high", "pon_down"}


async def on_result(state: AppState, result: PlanResult) -> None:
    """Si el plan era un sondeo, actualiza el estado de las ONU, el historial y las alarmas."""
    async with tenant_session(state.sessions, result.tenant_id) as session:
        olt = (
            await session.execute(select(Olt).where(Olt.monitor_plan_id == result.plan_id))
        ).scalar_one_or_none()
        if olt is None:
            return
        olt.monitor_plan_id = None
        now = datetime.now(UTC)
        run = await session.get(PlanRun, result.plan_id)
        outputs: list[dict[str, Any]] = list(
            ((run.result if run else None) or {}).get("outputs", [])
        )
        if run is not None:
            await session.delete(run)  # el sondeo no queda en el historial de consultas
        answered = any(step.ok for step in result.steps)
        if not answered:
            if result.status in ("expired", "rejected"):
                return
            error = result.error or next(
                (s.error for s in result.steps if s.error), "sin respuesta"
            )
            await _sync_alarms(
                session,
                result.tenant_id,
                olt.id,
                [
                    AlarmSpec(
                        0, 0, "olt_unreachable", "critical", f"{olt.name} no responde: {error}"
                    )
                ],
                now,
                {"olt_unreachable"},
            )
            return
        await _sync_alarms(session, result.tenant_id, olt.id, [], now, {"olt_unreachable"})
        readings, complete = merge_readings(outputs)
        await _store_readings(session, state, result.tenant_id, olt.id, readings, complete, now)
        await _sync_alarms(session, result.tenant_id, olt.id, evaluate(readings), now, ONU_KINDS)
        olt.monitored_at = now


async def _store_readings(
    session: AsyncSession,
    state: AppState,
    tenant_id: UUID,
    olt_id: UUID,
    readings: list[Reading],
    complete: bool,
    now: datetime,
) -> None:
    current = {
        (s.pon, s.onu): s
        for s in (
            await session.execute(select(OnuStatus).where(OnuStatus.olt_id == olt_id))
        ).scalars()
    }
    last = {
        (row.pon, row.onu): (
            row.at,
            row.online,
            float(row.rx_dbm) if row.rx_dbm is not None else None,
        )
        for row in (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (pon, onu) pon, onu, at, online, rx_dbm FROM onu_samples"
                    " WHERE olt_id = :olt ORDER BY pon, onu, at DESC"
                ),
                {"olt": olt_id},
            )
        ).all()
    }
    seen = set()
    for reading in readings:
        key = (reading.pon, reading.onu)
        seen.add(key)
        rx = Decimal(f"{reading.rx_dbm:.2f}") if reading.rx_dbm is not None else None
        status = current.get(key)
        if status is None:
            status = OnuStatus(
                tenant_id=tenant_id,
                olt_id=olt_id,
                pon=reading.pon,
                onu=reading.onu,
                online=reading.online,
                status_since=now,
            )
            session.add(status)
        elif status.online != reading.online:
            status.status_since = now
        status.online = reading.online
        status.phase = reading.phase
        status.serial = reading.serial or status.serial
        status.description = reading.description or status.description
        status.rx_dbm = rx
        status.updated_at = now
        if reading.online:
            status.last_online_at = now
        if wants_sample(last.get(key), reading, now):
            session.add(
                OnuSample(
                    tenant_id=tenant_id,
                    olt_id=olt_id,
                    pon=reading.pon,
                    onu=reading.onu,
                    at=now,
                    online=reading.online,
                    rx_dbm=rx,
                )
            )
    if complete:
        for key, status in current.items():
            if key not in seen:  # la ONU ya no existe en la OLT
                await session.delete(status)
    keep = timedelta(days=state.settings.monitor_retention_days)
    await session.execute(
        delete(OnuSample).where(OnuSample.olt_id == olt_id, OnuSample.at < now - keep)
    )


async def tick(state: AppState) -> int:
    """Pide los sondeos que tocan. Devuelve cuántos pidió."""
    minutes = state.settings.monitor_every_minutes
    if minutes <= 0 or state.bus is None or not state.settings.executor_public_key:
        return 0
    async with state.sessions() as session:
        due = (
            await session.execute(
                text("SELECT tenant_id, olt_id FROM olterra_due_monitoring(:every)"),
                {"every": timedelta(minutes=minutes)},
            )
        ).all()
    started = 0
    for tenant_id, olt_id in due:
        try:
            started += await start_poll(state, tenant_id, olt_id, Priority.POLL)
        except Exception:
            log.exception("No se pudo pedir el sondeo de la OLT %s", olt_id)
    return started


async def run_scheduler(state: AppState, every: float = 30.0) -> None:
    while True:
        try:
            await tick(state)
        except Exception:
            log.exception("Reloj del monitoreo")
        await asyncio.sleep(every)
