"""Conciliación OLT ↔ MikroTik ↔ CRM por API (lo que hoy se resuelve con un script).

Tres entradas para el mismo motor: JSON (integraciones), archivos subidos (la interfaz:
los mismos CSV y textos de RouterOS que acepta ``olterra-conciliar``) y la demo con datos
sintéticos. De los archivos se guarda el nombre y cuántos registros salieron; el
contenido no.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePath
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from olterra.api.deps import Tenant, TenantContext
from olterra.api.schemas import ReconOut, ReconRequest, ReconSummary
from olterra.api.state import audit
from olterra.db.models import ReconciliationRun
from olterra.devtools.demo_recon import demo_input
from olterra.reconciliation.engine import ReconResult, reconcile
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
from olterra.reconciliation.sources import SourceError
from olterra.reconciliation.sources.files import (
    SourceFile,
    customers_from_files,
    onus_from_files,
    secrets_from_files,
    sessions_from_files,
)

router = APIRouter(prefix="/v1/reconciliations", tags=["Conciliación"])

# Entre todos los archivos de una corrida. Un export de 20.000 clientes pesa unos 3 MB.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

T = TypeVar("T")
Source = Literal["api", "upload", "demo"]


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


async def _store(
    ctx: TenantContext,
    result: ReconResult,
    *,
    source: Source,
    files: list[dict[str, Any]] | None = None,
) -> ReconOut:
    report = to_dict(result)
    async with ctx.session() as session:
        run = ReconciliationRun(
            tenant_id=ctx.tenant_id,
            requested_by=ctx.actor,
            counts=report["counts"],
            findings=report["findings"],
            source=source,
            files=files or [],
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
            after={**report["counts"], "source": source},
            source_ip=ctx.client_ip,
        )
        return ReconOut.model_validate(run)


@router.get("", response_model=list[ReconSummary])
async def list_reconciliations(
    ctx: Tenant, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[ReconSummary]:
    """Las corridas más recientes, sin sus hallazgos."""
    ctx.require("recon:read")
    async with ctx.session() as session:
        rows = await session.execute(
            select(
                ReconciliationRun.id,
                ReconciliationRun.created_at,
                ReconciliationRun.requested_by,
                ReconciliationRun.source,
                ReconciliationRun.files,
                ReconciliationRun.counts,
            )
            .order_by(ReconciliationRun.created_at.desc())
            .limit(limit)
        )
        return [ReconSummary.model_validate(row) for row in rows]


@router.post("", response_model=ReconOut, status_code=status.HTTP_201_CREATED)
async def run_reconciliation(body: ReconRequest, ctx: Tenant) -> ReconOut:
    ctx.require("recon:write")
    return await _store(ctx, reconcile(_input(body)), source="api")


async def _read_uploads(groups: dict[str, list[UploadFile] | None]) -> dict[str, list[SourceFile]]:
    total = 0
    files: dict[str, list[SourceFile]] = {}
    for kind, uploads in groups.items():
        files[kind] = []
        for upload in uploads or []:
            data = await upload.read(MAX_UPLOAD_BYTES - total + 1)
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"Los archivos pasan de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB en total",
                )
            name = PurePath(upload.filename or f"{kind}.csv").name[:200]
            files[kind].append(SourceFile(name, data))
    if not any(files.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Suba al menos un archivo")
    return files


def _reconcile_files(
    files: dict[str, list[SourceFile]], router_name: str
) -> tuple[ReconResult, list[dict[str, Any]]]:
    manifest: list[dict[str, Any]] = []

    def load(kind: str, loader: Callable[[list[SourceFile]], list[T]]) -> list[T]:
        records: list[T] = []
        for file in files[kind]:
            found = loader([file])
            manifest.append({"kind": kind, "name": file.name, "records": len(found)})
            records += found
        return records

    data = ReconInput(
        onus=load("onus", onus_from_files),
        secrets=load("secrets", lambda f: secrets_from_files(f, router_name)),
        sessions=load("sessions", lambda f: sessions_from_files(f, router_name)),
        customers=load("customers", customers_from_files),
    )
    return reconcile(data), manifest


@router.post("/files", response_model=ReconOut, status_code=status.HTTP_201_CREATED)
async def run_from_files(
    ctx: Tenant,
    onus: Annotated[list[UploadFile] | None, File(description="CSV de ONUs")] = None,
    secrets: Annotated[
        list[UploadFile] | None, File(description="CSV o '/ppp secret export'")
    ] = None,
    sessions: Annotated[
        list[UploadFile] | None, File(description="CSV o '/ppp active print terse'")
    ] = None,
    customers: Annotated[list[UploadFile] | None, File(description="CSV del CRM")] = None,
    router_name: Annotated[
        str, Form(max_length=64, description="MikroTik de los textos copiados")
    ] = "",
) -> ReconOut:
    """Concilia con los mismos archivos que acepta ``olterra-conciliar correr``."""
    ctx.require("recon:write")
    files = await _read_uploads(
        {"onus": onus, "secrets": secrets, "sessions": sessions, "customers": customers}
    )
    try:
        # Leer y cruzar 200.000 filas toma segundos: fuera del loop de la API.
        result, manifest = await run_in_threadpool(_reconcile_files, files, router_name.strip())
    except SourceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return await _store(ctx, result, source="upload", files=manifest)


@router.post("/demo", response_model=ReconOut, status_code=status.HTTP_201_CREATED)
async def run_demo(ctx: Tenant) -> ReconOut:
    """Corre el motor con los datos sintéticos de la demo: un caso por tipo de hallazgo."""
    ctx.require("recon:write")
    return await _store(ctx, reconcile(demo_input()), source="demo")


@router.get("/{run_id}", response_model=ReconOut)
async def get_reconciliation(run_id: UUID, ctx: Tenant) -> ReconOut:
    ctx.require("recon:read")
    async with ctx.session() as session:
        run = await session.get(ReconciliationRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conciliación no encontrada")
        return ReconOut.model_validate(run)
