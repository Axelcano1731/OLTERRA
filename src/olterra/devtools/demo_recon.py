"""Datos SINTÉTICOS para la demo de conciliación con pilotos.

Cada caso dispara un tipo de hallazgo distinto, así la demo muestra todo lo que
el motor detecta sin exponer datos de un ISP real.
"""

from __future__ import annotations

from olterra.reconciliation.model import (
    CrmCustomer,
    OnuRecord,
    PppoeSecret,
    PppoeSession,
    ReconInput,
)


def _mac(n: int) -> str:
    return f"02:00:00:00:00:{n:02X}"


def demo_input() -> ReconInput:
    olt = "OLT-CENTRO"
    onus = [
        # Todo cuadra: serial en el CRM, PPPoE y MAC de la sesión detrás de la ONU.
        OnuRecord.build(
            olt=olt, pon=1, onu=1, serial="VSOL0000A001", state="working", macs=[_mac(1)]
        ),
        # El CRM dice que es de C002, pero detrás está la sesión de C003.
        OnuRecord.build(
            olt=olt, pon=1, onu=2, serial="VSOL0000A002", state="working", macs=[_mac(3)]
        ),
        # ONU en modo router con el usuario PPPoE mal escrito (espacio de más).
        OnuRecord.build(
            olt=olt, pon=1, onu=3, serial="VSOL0000A003", state="working", pppoe_user="maria _demo"
        ),
        # ONU en modo router con un usuario que no existe en el MikroTik.
        OnuRecord.build(
            olt=olt,
            pon=1,
            onu=4,
            serial="VSOL0000A004",
            state="working",
            pppoe_user="demo.fantasma",
        ),
        # Nadie la reclama.
        OnuRecord.build(
            olt=olt, pon=2, onu=1, serial="VSOL0000B001", state="offline", description="bodega"
        ),
        # El mismo serial de PON 1 ONU 1: se movió sin borrarla.
        OnuRecord.build(olt=olt, pon=2, onu=2, serial="VSOL0000A001", state="offline"),
        # Cliente retirado con la ONU todavía autorizada.
        OnuRecord.build(olt=olt, pon=2, onu=3, serial="VSOL0000B003", state="working"),
        # Solo la descripción apunta a un cliente.
        OnuRecord.build(
            olt=olt, pon=2, onu=4, serial="VSOL0000B004", state="working", description="demo.c007"
        ),
    ]
    customers = [
        CrmCustomer(
            "C001", "Cliente Demo 01", "activo", "demo.c001", "VSOL0000A001", "fiber", "NAP-01", "1"
        ),
        CrmCustomer(
            "C002", "Cliente Demo 02", "activo", "demo.c002", "VSOL0000A002", "fiber", "NAP-01", "2"
        ),
        CrmCustomer("C003", "Cliente Demo 03", "activo", "demo.c003", None, "fiber", "NAP-01", "3"),
        CrmCustomer(
            "C004",
            "Cliente Demo 04",
            "activo",
            "maria_demo",
            "VSOL0000A003",
            "fiber",
            "NAP-02",
            "1",
        ),
        CrmCustomer(
            "C005", "Cliente Demo 05", "activo", "demo.c005", "VSOL0000C005", "fiber", "NAP-02", "2"
        ),
        CrmCustomer(
            "C006",
            "Cliente Demo 06",
            "retirado",
            "demo.c006",
            "VSOL0000B003",
            "fiber",
            "NAP-03",
            "1",
        ),
        CrmCustomer("C007", "Cliente Demo 07", "activo", "demo.c007", None, "fiber", "NAP-03", "2"),
        CrmCustomer("C008", "Cliente Demo 08", "suspendido", "demo.c008", None, "wireless"),
        CrmCustomer("C009", "Cliente Demo 09", "activo", "demo.c009", None, "wireless"),
        CrmCustomer("C010", "Cliente Demo 10", "activo", "demo.c010", None, "wireless"),
        CrmCustomer("C011", "Cliente Demo 11", "activo", "demo.c011", None, "fiber", "NAP-04", "1"),
    ]
    secrets = [
        PppoeSecret("BNG-CENTRO", name)
        for name in (
            "demo.c001",
            "demo.c002",
            "demo.c003",
            "maria_demo",
            "demo.c005",
            "demo.c006",
            "demo.c007",
            "demo.c008",
            "demo.c011",
            "demo.viejo",
        )
    ] + [PppoeSecret("BNG-CENTRO", "demo.c009", disabled=True)]
    sessions = [
        PppoeSession("BNG-CENTRO", "demo.c001", _mac(1)),
        PppoeSession("BNG-CENTRO", "demo.c003", _mac(3)),
        PppoeSession("BNG-CENTRO", "demo.c008", _mac(8)),
    ]
    return ReconInput(onus=onus, secrets=secrets, sessions=sessions, customers=customers)
