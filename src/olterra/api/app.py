"""Aplicación FastAPI.

uvicorn --factory olterra.api.app:create_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from olterra import __version__
from olterra.api.deps import State
from olterra.api.routes import me, olts, plans, provisioning, reconciliation, tunnel
from olterra.api.state import AppState, PlanPublisher, ServiceUnavailable
from olterra.config import Settings, get_settings
from olterra.db.session import create_engine, session_factory
from olterra.drivers.base import ParamError
from olterra.executor.bus import NatsBus
from olterra.orchestrator import PlanBuildError
from olterra.security.vault import Vault
from olterra.tunnel.addressing import AddressPlan, AddressPlanError
from olterra.tunnel.routeros import ScriptError

log = logging.getLogger("olterra.api")


async def _consume_results(bus: NatsBus, state: AppState) -> None:
    async for result, msg in bus.results("olterra-api"):
        try:
            await state.store_result(result)
            await msg.ack()
        except Exception:
            log.exception("No se pudo guardar el resultado del plan %s", result.plan_id)
            await msg.nak(delay=5)


def create_app(
    settings: Settings | None = None,
    *,
    bus: PlanPublisher | None = None,
    connect_nats: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        state = AppState(
            settings=settings,
            sessions=session_factory(engine),
            vault=Vault.single(settings.master_key_bytes(), settings.master_key_version)
            if settings.master_key
            else None,
            addresses=AddressPlan.from_strings(
                settings.tunnel_platform_prefix,
                settings.tunnel_peer_pool,
                settings.tunnel_peer_block_prefixlen,
                settings.tunnel_nat_pool,
                settings.tunnel_nat_block_prefixlen,
            ),
            bus=bus,
        )
        nats_bus: NatsBus | None = None
        if bus is None and connect_nats:
            try:
                nats_bus = await asyncio.wait_for(
                    NatsBus.connect(settings.nats_url, name="olterra-api"), 10
                )
                state.bus = nats_bus
                state.background.append(asyncio.create_task(_consume_results(nats_bus, state)))
            except Exception as exc:  # la API arranca igual; /health lo muestra
                log.warning("Sin NATS en %s: %s", settings.nats_url, exc)
        app.state.olterra = state
        try:
            yield
        finally:
            for task in state.background:
                task.cancel()
            await asyncio.gather(*state.background, return_exceptions=True)
            if nats_bus is not None:
                with contextlib.suppress(Exception):
                    await nats_bus.close()
            await engine.dispose()

    app = FastAPI(
        title="Olterra",
        version=__version__,
        description="Gestión de OLT VSOL + mapa FTTH, amarrados al CRM del ISP.",
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ParamError)
    @app.exception_handler(PlanBuildError)
    @app.exception_handler(ScriptError)
    async def _bad_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AddressPlanError)
    async def _no_addresses(request: Request, exc: AddressPlanError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health", tags=["Plataforma"])
    async def health(state: State) -> dict[str, str]:
        database = "ok"
        try:
            async with state.sessions() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            database = "error"
        return {
            "status": "ok" if database == "ok" else "degradado",
            "version": __version__,
            "database": database,
            "nats": "conectado" if state.bus is not None else "sin conexión",
            "vault": "lista" if state.vault is not None else "sin llave maestra",
        }

    for module in (me, olts, plans, provisioning, tunnel, reconciliation):
        app.include_router(module.router)
    return app
