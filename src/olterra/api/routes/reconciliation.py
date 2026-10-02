"""Conciliación OLT ↔ MikroTik ↔ CRM por API (lo que hoy se resuelve con un script)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from olterra.api.deps import Tenant
from olterra.api.schemas import ReconOut, ReconRequest
from olterra.api.state import audit
from olterra.db.models import ReconciliationRun
from olterra.reconciliation.engine import reconcile
from olterra.reconciliation.model import (
    CrmCustomer,
    OnuRecord,
    PppoeSecret,
    PppoeSession,
    ReconInput,
    crm_serial,
    normalize_access,
    normalize_status,
)
from olterra.reconciliation.report import to_dict

router = APIRouter(prefix="/v1/reconciliations", tags=["Conciliación"])


def _input(body: ReconRequest) -> ReconInput:
    return ReconInput(
        onus=[
            OnuRecord.build(
                olt=o.olt,
                pon=o.pon,
                onu=o.onu,
                serial=o.serial,
                state=o.state,
                description=o.description,
                pppoe_user=o.pppoe_user,
                macs=o.macs,
            )
            for o in body.onus
        ],
        secrets=[
            PppoeSecret(s.router, s.name, s.profile, s.disabled, s.comment) for s in body.secrets
        ],
        sessions=[PppoeSession(s.router, s.name, s.caller_id, s.address) for s in body.sessions],
        customers=[
            CrmCustomer(
                id=c.id,
                name=c.name,
                status=normalize_status(c.status),
                pppoe_user=c.pppoe_user,
                onu_serial=crm_serial(c.onu_serial),
                access=normalize_access(c.access),
                nap=c.nap,
                nap_port=c.nap_port,
                router=c.router,
            )
            for c in body.customers
        ],
    )


@router.post("", response_model=ReconOut, status_code=status.HTTP_201_CREATED)
async def run_reconciliation(body: ReconRequest, ctx: Tenant) -> ReconOut:
    ctx.require("recon:write")
    report = to_dict(reconcile(_input(body)))
    async with ctx.session() as session:
        run = ReconciliationRun(
            tenant_id=ctx.tenant_id,
            requested_by=ctx.actor,
            counts=report["counts"],
            findings=report["findings"],
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="reconciliation.run",
            target_type="reconciliation_run",
            target_id=str(run.id),
            after=report["counts"],
            source_ip=ctx.client_ip,
        )
        return ReconOut(
            id=run.id, created_at=run.created_at, counts=run.counts, findings=run.findings
        )


@router.get("/{run_id}", response_model=ReconOut)
async def get_reconciliation(run_id: UUID, ctx: Tenant) -> ReconOut:
    ctx.require("recon:read")
    async with ctx.session() as session:
        run = await session.get(ReconciliationRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conciliación no encontrada")
        return ReconOut(
            id=run.id, created_at=run.created_at, counts=run.counts, findings=run.findings
        )
