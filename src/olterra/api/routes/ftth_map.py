"""Mapa FTTH: OLT, cajas con sus puertos, postes, cables y factibilidad.

Las posiciones se guardan en PostGIS (SRID 4326) y salen como ``lat``/``lng``. Cada caja dice
cuántos de sus clientes están caídos o con señal baja según el monitoreo (``onu_status``): así el
mapa muestra dónde está el problema. La factibilidad busca cajas con puertos libres cerca de un
punto, por distancia real (``geography``).

Se lee con ``olt:read``; se edita con ``map:write`` (admin y técnico). Cada cambio va a la
bitácora.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.monitoring import RX_LOW_WARNING, phase_label
from olterra.api.schemas import (
    Feasibility,
    FeasibilityOption,
    LatLng,
    MapBoxDetail,
    MapBoxIn,
    MapBoxOut,
    MapBoxPatch,
    MapCableIn,
    MapCableOut,
    MapCablePatch,
    MapConfig,
    MapData,
    MapOlt,
    MapPoleIn,
    MapPoleOut,
    MapPortOut,
    PortAssignIn,
)
from olterra.api.state import audit
from olterra.db.models import MapBox, MapBoxPort, MapCable, MapPole, Olt, OnuStatus

router = APIRouter(prefix="/v1/map", tags=["Mapa FTTH"])


def _point(lat: float, lng: float) -> Any:
    return func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)


def _line(coords: list[tuple[float, float]]) -> Any:
    geojson = json.dumps({"type": "LineString", "coordinates": [list(c) for c in coords]})
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(geojson), 4326)


async def _audit(ctx: Any, session: Any, action: str, target_id: str, **after: Any) -> None:
    await audit(
        session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=action,
        target_type="map",
        target_id=target_id,
        after=after or None,
        source_ip=ctx.client_ip,
    )


async def _boxes(session: Any, *where: Any) -> list[MapBoxOut]:
    """Cajas con su ocupación y cuántos de sus clientes están caídos o con señal baja."""
    ports = (
        select(
            MapBoxPort.box_id.label("box_id"),
            func.count().label("used"),
            func.count().filter(OnuStatus.online.is_(False)).label("down"),
            func.count().filter(OnuStatus.online, OnuStatus.rx_dbm < RX_LOW_WARNING).label("low"),
        )
        .outerjoin(
            OnuStatus,
            and_(
                OnuStatus.olt_id == MapBoxPort.olt_id,
                OnuStatus.pon == MapBoxPort.pon,
                OnuStatus.onu == MapBoxPort.onu,
            ),
        )
        .group_by(MapBoxPort.box_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            MapBox,
            func.ST_Y(MapBox.location).label("lat"),
            func.ST_X(MapBox.location).label("lng"),
            func.coalesce(ports.c.used, 0).label("used"),
            func.coalesce(ports.c.down, 0).label("down"),
            func.coalesce(ports.c.low, 0).label("low"),
        )
        .outerjoin(ports, ports.c.box_id == MapBox.id)
        .where(*where)
        .order_by(MapBox.name)
    )
    return [
        MapBoxOut(
            id=box.id,
            kind=box.kind,
            name=box.name,
            lat=lat,
            lng=lng,
            ports=box.ports,
            used=used,
            splitter=box.splitter,
            olt_id=box.olt_id,
            pon=box.pon,
            notes=box.notes,
            down=down,
            low_signal=low,
        )
        for box, lat, lng, used, down, low in rows.all()
    ]


async def _box_detail(session: Any, box_id: UUID) -> MapBoxDetail:
    found = await _boxes(session, MapBox.id == box_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Caja no encontrada")
    rows = await session.execute(
        select(MapBoxPort, OnuStatus)
        .outerjoin(
            OnuStatus,
            and_(
                OnuStatus.olt_id == MapBoxPort.olt_id,
                OnuStatus.pon == MapBoxPort.pon,
                OnuStatus.onu == MapBoxPort.onu,
            ),
        )
        .where(MapBoxPort.box_id == box_id)
        .order_by(MapBoxPort.port)
    )
    ports = [
        MapPortOut(
            port=port.port,
            olt_id=port.olt_id,
            pon=port.pon,
            onu=port.onu,
            description=onu.description if onu else None,
            online=onu.online if onu else None,
            phase_label=phase_label(onu.phase) if onu else None,
            rx_dbm=float(onu.rx_dbm) if onu and onu.rx_dbm is not None else None,
        )
        for port, onu in rows.all()
    ]
    return MapBoxDetail(**found[0].model_dump(), port_list=ports)


async def _cables(session: Any, *where: Any) -> list[MapCableOut]:
    rows = await session.execute(
        select(
            MapCable,
            func.ST_AsGeoJSON(MapCable.path).label("geojson"),
            func.ST_Length(func.geography(MapCable.path)).label("length_m"),
        )
        .where(*where)
        .order_by(MapCable.name)
    )
    return [
        MapCableOut(
            id=cable.id,
            name=cable.name,
            fibers=cable.fibers,
            notes=cable.notes,
            coords=[tuple(c) for c in json.loads(geojson)["coordinates"]],
            length_m=round(float(length), 1),
        )
        for cable, geojson, length in rows.all()
    ]


async def _check_olt(session: Any, olt_id: UUID | None) -> None:
    if olt_id is not None and await session.get(Olt, olt_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")


@router.get("/config", response_model=MapConfig)
async def map_config(ctx: Tenant, state: State) -> MapConfig:
    ctx.require("olt:read")
    return MapConfig(style_url=state.settings.map_style_url)


@router.get("", response_model=MapData)
async def map_data(ctx: Tenant) -> MapData:
    """Todo lo del mapa del ISP en una sola respuesta."""
    ctx.require("olt:read")
    async with ctx.session() as session:
        olts = await session.execute(
            select(
                Olt.id,
                Olt.name,
                Olt.status,
                func.ST_Y(Olt.location).label("lat"),
                func.ST_X(Olt.location).label("lng"),
            ).order_by(Olt.name)
        )
        poles = await session.execute(
            select(MapPole, func.ST_Y(MapPole.location).label("lat"), func.ST_X(MapPole.location))
        )
        return MapData(
            olts=[MapOlt(id=i, name=n, status=s, lat=lat, lng=lng) for i, n, s, lat, lng in olts],
            boxes=await _boxes(session),
            poles=[
                MapPoleOut(
                    id=pole.id, name=pole.name, owner=pole.owner, notes=pole.notes, lat=la, lng=ln
                )
                for pole, la, ln in poles.all()
            ],
            cables=await _cables(session),
        )


# --- Cajas ------------------------------------------------------------------------------------


@router.post("/boxes", response_model=MapBoxOut, status_code=status.HTTP_201_CREATED)
async def create_box(body: MapBoxIn, ctx: Tenant) -> MapBoxOut:
    ctx.require("map:write")
    async with ctx.session() as session:
        await _check_olt(session, body.olt_id)
        box = MapBox(
            tenant_id=ctx.tenant_id,
            kind=body.kind,
            name=body.name.strip(),
            location=_point(body.lat, body.lng),
            ports=body.ports,
            splitter=body.splitter,
            olt_id=body.olt_id,
            pon=body.pon,
            notes=body.notes,
        )
        session.add(box)
        await session.flush()
        await _audit(ctx, session, "map.box.create", str(box.id), name=box.name, kind=box.kind)
        return (await _boxes(session, MapBox.id == box.id))[0]


@router.get("/boxes/{box_id}", response_model=MapBoxDetail)
async def get_box(box_id: UUID, ctx: Tenant) -> MapBoxDetail:
    ctx.require("olt:read")
    async with ctx.session() as session:
        return await _box_detail(session, box_id)


@router.patch("/boxes/{box_id}", response_model=MapBoxDetail)
async def update_box(box_id: UUID, body: MapBoxPatch, ctx: Tenant) -> MapBoxDetail:
    ctx.require("map:write")
    changes = body.model_fields_set
    async with ctx.session() as session:
        box = await session.get(MapBox, box_id)
        if box is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Caja no encontrada")
        for name in ("kind", "ports", "splitter", "olt_id", "pon", "notes"):
            if name in changes:
                setattr(box, name, getattr(body, name))
        if "olt_id" in changes:
            await _check_olt(session, body.olt_id)
        if body.name is not None:
            box.name = body.name.strip()
        if body.lat is not None and body.lng is not None:
            box.location = _point(body.lat, body.lng)
        used = (
            await session.execute(
                select(func.max(MapBoxPort.port)).where(MapBoxPort.box_id == box_id)
            )
        ).scalar_one_or_none()
        if used is not None and box.ports < used:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"El puerto {used} tiene un cliente: la caja no puede tener menos puertos",
            )
        box.updated_at = func.now()
        await session.flush()
        await _audit(ctx, session, "map.box.update", str(box_id), name=box.name)
        return await _box_detail(session, box_id)


@router.delete("/boxes/{box_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_box(box_id: UUID, ctx: Tenant) -> None:
    ctx.require("map:write")
    async with ctx.session() as session:
        box = await session.get(MapBox, box_id)
        if box is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Caja no encontrada")
        await session.delete(box)
        await _audit(ctx, session, "map.box.delete", str(box_id), name=box.name)


@router.put("/boxes/{box_id}/ports/{port}", response_model=MapBoxDetail)
async def assign_port(box_id: UUID, port: int, body: PortAssignIn, ctx: Tenant) -> MapBoxDetail:
    """Pone una ONU (cliente) en un puerto. Si estaba en otro puerto u otra caja, la mueve."""
    ctx.require("map:write")
    async with ctx.session() as session:
        box = await session.get(MapBox, box_id)
        if box is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Caja no encontrada")
        if not 1 <= port <= box.ports:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"La caja tiene {box.ports} puertos")
        await _check_olt(session, body.olt_id)
        await session.execute(
            delete(MapBoxPort).where(
                MapBoxPort.olt_id == body.olt_id,
                MapBoxPort.pon == body.pon,
                MapBoxPort.onu == body.onu,
            )
        )
        await session.execute(
            delete(MapBoxPort).where(MapBoxPort.box_id == box_id, MapBoxPort.port == port)
        )
        session.add(
            MapBoxPort(
                tenant_id=ctx.tenant_id,
                box_id=box_id,
                port=port,
                olt_id=body.olt_id,
                pon=body.pon,
                onu=body.onu,
            )
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ese puerto ya cambió") from exc
        await _audit(
            ctx, session, "map.port.assign", str(box_id), name=box.name, port=port,
            pon=body.pon, onu=body.onu,
        )  # fmt: skip
        return await _box_detail(session, box_id)


@router.delete("/boxes/{box_id}/ports/{port}", response_model=MapBoxDetail)
async def release_port(box_id: UUID, port: int, ctx: Tenant) -> MapBoxDetail:
    ctx.require("map:write")
    async with ctx.session() as session:
        box = await session.get(MapBox, box_id)
        if box is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Caja no encontrada")
        await session.execute(
            delete(MapBoxPort).where(MapBoxPort.box_id == box_id, MapBoxPort.port == port)
        )
        await _audit(ctx, session, "map.port.release", str(box_id), name=box.name, port=port)
        return await _box_detail(session, box_id)


# --- Postes, cables y OLT ---------------------------------------------------------------------


@router.post("/poles", response_model=MapPoleOut, status_code=status.HTTP_201_CREATED)
async def create_pole(body: MapPoleIn, ctx: Tenant) -> MapPoleOut:
    ctx.require("map:write")
    async with ctx.session() as session:
        pole = MapPole(
            tenant_id=ctx.tenant_id,
            name=body.name,
            owner=body.owner,
            notes=body.notes,
            location=_point(body.lat, body.lng),
        )
        session.add(pole)
        await session.flush()
        await _audit(ctx, session, "map.pole.create", str(pole.id), name=body.name)
        return MapPoleOut(id=pole.id, **body.model_dump())


@router.delete("/poles/{pole_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pole(pole_id: UUID, ctx: Tenant) -> None:
    ctx.require("map:write")
    async with ctx.session() as session:
        pole = await session.get(MapPole, pole_id)
        if pole is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Poste no encontrado")
        await session.delete(pole)
        await _audit(ctx, session, "map.pole.delete", str(pole_id), name=pole.name)


@router.post("/cables", response_model=MapCableOut, status_code=status.HTTP_201_CREATED)
async def create_cable(body: MapCableIn, ctx: Tenant) -> MapCableOut:
    ctx.require("map:write")
    async with ctx.session() as session:
        cable = MapCable(
            tenant_id=ctx.tenant_id,
            name=body.name.strip(),
            fibers=body.fibers,
            notes=body.notes,
            path=_line(body.coords),
        )
        session.add(cable)
        await session.flush()
        created = (await _cables(session, MapCable.id == cable.id))[0]
        await _audit(
            ctx, session, "map.cable.create", str(cable.id), name=cable.name,
            fibers=cable.fibers, length_m=created.length_m,
        )  # fmt: skip
        return created


@router.patch("/cables/{cable_id}", response_model=MapCableOut)
async def update_cable(cable_id: UUID, body: MapCablePatch, ctx: Tenant) -> MapCableOut:
    ctx.require("map:write")
    async with ctx.session() as session:
        cable = await session.get(MapCable, cable_id)
        if cable is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cable no encontrado")
        if body.name is not None:
            cable.name = body.name.strip()
        if body.fibers is not None:
            cable.fibers = body.fibers
        if "notes" in body.model_fields_set:
            cable.notes = body.notes
        cable.updated_at = func.now()
        await session.flush()
        await _audit(ctx, session, "map.cable.update", str(cable_id), name=cable.name)
        return (await _cables(session, MapCable.id == cable_id))[0]


@router.delete("/cables/{cable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cable(cable_id: UUID, ctx: Tenant) -> None:
    ctx.require("map:write")
    async with ctx.session() as session:
        cable = await session.get(MapCable, cable_id)
        if cable is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cable no encontrado")
        await session.delete(cable)
        await _audit(ctx, session, "map.cable.delete", str(cable_id), name=cable.name)


@router.put("/olts/{olt_id}/location", response_model=MapOlt)
async def place_olt(olt_id: UUID, body: LatLng, ctx: Tenant) -> MapOlt:
    """Ubica la OLT en el mapa (la cabecera)."""
    ctx.require("map:write")
    async with ctx.session() as session:
        olt = await session.get(Olt, olt_id)
        if olt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")
        olt.location = _point(body.lat, body.lng)
        await _audit(ctx, session, "map.olt.place", str(olt_id), name=olt.name)
        return MapOlt(id=olt.id, name=olt.name, status=olt.status, lat=body.lat, lng=body.lng)


# --- Factibilidad -----------------------------------------------------------------------------


@router.get("/feasibility", response_model=Feasibility)
async def feasibility(
    ctx: Tenant,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius_m: int = Query(300, ge=10, le=5000),
    limit: int = Query(5, ge=1, le=20),
) -> Feasibility:
    """Cajas con puertos libres a menos de ``radius_m`` del punto, de la más cercana a la más lejana.

    Es lo que Converza le preguntará a Olterra cuando un prospecto mande su ubicación.
    """
    ctx.require("olt:read")
    here = func.geography(_point(lat, lng))
    async with ctx.session() as session:
        rows = await session.execute(
            select(MapBox.id, func.ST_Distance(func.geography(MapBox.location), here))
            .where(
                MapBox.ports > 0,
                func.ST_DWithin(func.geography(MapBox.location), here, radius_m),
            )
            .order_by(func.ST_Distance(func.geography(MapBox.location), here))
        )
        distances = {box_id: float(distance) for box_id, distance in rows.all()}
        boxes = await _boxes(session, MapBox.id.in_(list(distances))) if distances else []
    options = sorted(
        (
            FeasibilityOption(
                box=box, distance_m=round(distances[box.id], 1), free_ports=box.ports - box.used
            )
            for box in boxes
            if box.ports > box.used
        ),
        key=lambda option: option.distance_m,
    )
    return Feasibility(radius_m=radius_m, options=options[:limit])
