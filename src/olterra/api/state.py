"""Estado compartido de la API: base de datos, bóveda, bus hacia el ejecutor."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from olterra.config import Settings
from olterra.db.models import AuditLog, Olt, PlanRun, TenantKey
from olterra.db.session import tenant_session
from olterra.executor.plan import Plan, PlanResult
from olterra.orchestrator import interpret
from olterra.security.vault import Vault
from olterra.tunnel.addressing import AddressPlan

log = logging.getLogger(__name__)


class PlanPublisher(Protocol):
    async def publish_plan(self, plan: Plan, group: str = "cloud") -> Any: ...


class ServiceUnavailable(RuntimeError):
    pass


@dataclass
class AppState:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    vault: Vault | None
    addresses: AddressPlan
    bus: PlanPublisher | None = None
    background: list[Any] = field(default_factory=list)

    def require_vault(self) -> Vault:
        if self.vault is None:
            raise ServiceUnavailable("La bóveda no está configurada (OLTERRA_MASTER_KEY)")
        return self.vault

    def require_bus(self) -> PlanPublisher:
        if self.bus is None:
            raise ServiceUnavailable(
                "No hay conexión con NATS: el ejecutor no puede recibir planes"
            )
        return self.bus

    async def tenant_dek(self, session: AsyncSession, tenant_id: UUID) -> bytes:
        wrapped = (
            await session.execute(
                select(TenantKey.wrapped_dek).where(TenantKey.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if wrapped is None:
            raise ServiceUnavailable("El tenant no tiene llave de cifrado")
        return self.require_vault().unwrap_tenant_key(tenant_id, wrapped)

    async def store_result(self, result: PlanResult) -> None:
        """Guarda el resultado de un plan (lo llama el consumidor de resultados de NATS)."""
        async with tenant_session(self.sessions, result.tenant_id) as session:
            run = await session.get(PlanRun, result.plan_id)
            if run is None:
                log.warning("Resultado de un plan desconocido: %s", result.plan_id)
                return
            run.result = json.loads(
                json.dumps(interpret(run.calls, run.commands, result), default=str)
            )
            run.status = result.status
            run.finished_at = datetime.now(UTC)
            status = olt_status_after(result)
            if status is not None:
                olt = await session.get(Olt, result.olt_id)
                if olt is not None:
                    olt.status = status
                    if status == "online":
                        olt.last_seen_at = run.finished_at


def olt_status_after(result: PlanResult) -> str | None:
    """Lo que un plan dice de la OLT: respondió (online) o no se pudo entrar (unreachable).

    Un plan vencido o rechazado antes de salir no dice nada de la OLT.
    """
    if result.status in ("expired", "rejected"):
        return None
    if any(step.ok for step in result.steps):
        return "online"
    return "unreachable"


async def audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    after: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            source_ip=source_ip,
        )
    )
