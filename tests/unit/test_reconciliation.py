from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from olterra.devtools.demo_recon import demo_input
from olterra.reconciliation.engine import reconcile
from olterra.reconciliation.model import (
    CrmCustomer,
    Kind,
    OnuRecord,
    PppoeSecret,
    PppoeSession,
    ReconInput,
    Severity,
)
from olterra.reconciliation.report import render_csv, render_html, to_dict
from olterra.reconciliation.sources import SourceError
from olterra.reconciliation.sources.ispwatch import fetch_customers
from olterra.reconciliation.sources.routeros_text import secrets_from_text, sessions_from_text
from olterra.reconciliation.sources.tabular import (
    customers_from_rows,
    onus_from_rows,
    read_rows,
    secrets_from_rows,
)


def kinds(data: ReconInput) -> dict[Kind, int]:
    result: dict[Kind, int] = {}
    for finding in reconcile(data).findings:
        result[finding.kind] = result.get(finding.kind, 0) + 1
    return result


def test_demo_triggers_every_finding_kind() -> None:
    result = reconcile(demo_input())
    assert {f.kind for f in result.findings} == set(Kind)
    # Errores primero.
    severities = [f.severity for f in result.findings]
    assert severities == sorted(
        severities, key=lambda s: [Severity.ERROR, Severity.WARNING, Severity.INFO].index(s)
    )


def test_everything_consistent_has_no_findings() -> None:
    data = ReconInput(
        onus=[
            OnuRecord.build(
                olt="O", pon=1, onu=1, serial="VSOL0000A001", macs=["02:00:00:00:00:01"]
            )
        ],
        secrets=[PppoeSecret("BNG", "cliente1")],
        sessions=[PppoeSession("BNG", "cliente1", "02-00-00-00-00-01")],
        customers=[CrmCustomer("1", "Cliente", "activo", "cliente1", "VSOL0000A001", "fiber")],
    )
    assert reconcile(data).findings == []


def test_typo_reported_once_and_not_as_missing_secret() -> None:
    data = ReconInput(
        secrets=[PppoeSecret("BNG", "DANIELA_PARADA")],
        customers=[CrmCustomer("1", "Daniela", "activo", "DANIELA _PARADA", access="wireless")],
    )
    found = kinds(data)
    assert found == {Kind.PPPOE_DIGITACION: 1}


def test_mac_evidence_links_bridge_onu_and_flags_crossed_serials() -> None:
    data = ReconInput(
        onus=[
            OnuRecord.build(
                olt="O", pon=1, onu=1, serial="VSOL0000A001", macs=["aa:bb:cc:dd:ee:01"]
            )
        ],
        secrets=[PppoeSecret("BNG", "ana"), PppoeSecret("BNG", "beto")],
        sessions=[PppoeSession("BNG", "beto", "AA:BB:CC:DD:EE:01")],
        customers=[
            CrmCustomer("A", "Ana", "activo", "ana", "VSOL0000A001", "fiber"),
            CrmCustomer("B", "Beto", "activo", "beto", None, "fiber"),
        ],
    )
    result = reconcile(data)
    crossed = [f for f in result.findings if f.kind is Kind.SERIAL_CRUZADO]
    assert len(crossed) == 1
    assert crossed[0].refs["customer_id"] == "A" and crossed[0].refs["other_customer_id"] == "B"
    assert any(
        f.kind is Kind.SERIAL_POR_REGISTRAR and f.refs["customer_id"] == "B"
        for f in result.findings
    )


def test_suspended_customer_with_session_is_an_error() -> None:
    data = ReconInput(
        secrets=[PppoeSecret("BNG", "moroso")],
        sessions=[PppoeSession("BNG", "moroso", None)],
        customers=[CrmCustomer("1", "Moroso", "suspendido", "moroso", access="wireless")],
    )
    [finding] = reconcile(data).findings
    assert finding.kind is Kind.SUSPENDIDO_NAVEGANDO and finding.severity is Severity.ERROR


def test_wireless_customers_skip_fiber_checks() -> None:
    data = ReconInput(
        secrets=[PppoeSecret("BNG", "radio1")],
        customers=[CrmCustomer("1", "Radio", "activo", "radio1", access="wireless")],
    )
    assert reconcile(data).findings == []


def test_reports_render() -> None:
    result = reconcile(demo_input())
    html = render_html(result, isp="ISP <Demo>")
    assert "ISP &lt;Demo&gt;" in html  # se escapa
    assert "Seriales cruzados" in html
    csv_text = render_csv(result)
    assert csv_text.splitlines()[0].startswith("severidad,tipo,titulo")
    assert len(csv_text.splitlines()) == len(result.findings) + 1
    assert json.loads(json.dumps(to_dict(result)))["counts"]["hallazgos"] == len(result.findings)


# --- Fuentes ---------------------------------------------------------------------------


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def test_csv_semicolon_cp1252_spanish_headers(tmp_path: Path) -> None:
    path = write(
        tmp_path / "clientes.csv",
        "Id;Nombre;Apellido;Estado;Usuario PPPoE;Serial ONU;Tecnología\n"
        "7;María;Pérez;Activo;maria.perez;vsol-0008d09c;Fibra\n"
        "8;José;;Cortado;jose;;Inalámbrico\n",
        encoding="cp1252",
    )
    customers = customers_from_rows(read_rows(path))
    assert customers[0] == CrmCustomer(
        id="7",
        name="María Pérez",
        status="activo",
        pppoe_user="maria.perez",
        onu_serial="VSOL0008D09C",
        access="fiber",
    )
    assert customers[1].status == "suspendido" and customers[1].access == "wireless"


def test_csv_onus_and_secrets(tmp_path: Path) -> None:
    onus = onus_from_rows(
        read_rows(
            write(
                tmp_path / "onus.csv",
                "olt,onu,serial,estado,macs\nOLT-1,GPON0/1:5,VSOL0008D09C,working,aa:bb:cc:dd:ee:01;aa:bb:cc:dd:ee:02\n",
            )
        )
    )
    assert (onus[0].pon, onus[0].onu, onus[0].serial, len(onus[0].macs)) == (
        1,
        5,
        "VSOL0008D09C",
        2,
    )
    secrets = secrets_from_rows(
        read_rows(
            write(tmp_path / "s.csv", "router,usuario,deshabilitado\nBNG,ana,si\nBNG,beto,no\n")
        )
    )
    assert [(s.name, s.disabled) for s in secrets] == [("ana", True), ("beto", False)]


def test_csv_missing_required_column(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="serial"):
        onus_from_rows(read_rows(write(tmp_path / "onus.csv", "olt,pon\nA,1\n")))


def test_routeros_export_and_terse() -> None:
    export = (
        "# sep/30/2026 by RouterOS 7.16\n"
        "/ppp secret\n"
        'add name=ana password=Secreta123 profile=20M service=pppoe comment="Ana \\"la\\" vecina"\n'
        "add disabled=yes name=beto password=x profile=10M service=pppoe\n"
        "add name=larga password=y \\\n    profile=50M service=pppoe\n"
        "add name=ovpn password=z service=ovpn\n"
    )
    secrets = secrets_from_text(export, "BNG")
    assert [(s.name, s.profile, s.disabled) for s in secrets] == [
        ("ana", "20M", False),
        ("beto", "10M", True),
        ("larga", "50M", False),
    ]
    assert secrets[0].comment == 'Ana "la" vecina'
    assert all("Secreta123" not in repr(s) for s in secrets)  # la clave no se guarda

    terse = (
        " 0 R name=ana service=pppoe caller-id=AA:BB:CC:DD:EE:01 address=10.0.0.2 uptime=1d\n"
        " 1 R name=beto service=pppoe caller-id=AA:BB:CC:DD:EE:02 address=10.0.0.3 uptime=2h\n"
    )
    sessions = sessions_from_text(terse, "BNG")
    assert [(s.name, s.mac) for s in sessions] == [
        ("ana", "AA:BB:CC:DD:EE:01"),
        ("beto", "AA:BB:CC:DD:EE:02"),
    ]


def test_ispwatch_partner_api_pagination() -> None:
    pages = {
        0: {
            "data": [
                {
                    "id": 1,
                    "name": "Ana",
                    "last_name": "Ruiz",
                    "service_status": "activo",
                    "pppoe_username": "ana",
                    "is_fiber": True,
                    "sectorial": "NAP-1",
                },
                {
                    "id": 2,
                    "name": "Beto",
                    "service_status": "suspendido",
                    "pppoe_username": None,
                    "is_fiber": False,
                },
            ],
            "meta": {"has_more": True, "next_after_id": 2},
        },
        2: {
            "data": [{"id": 3, "name": "Caro", "service_status": "retirado"}],
            "meta": {"has_more": False, "next_after_id": 3},
        },
    }
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["Authorization"])
        assert request.url.path == "/api/v1/partner/customers"
        return httpx.Response(200, json=pages[int(request.url.params["after_id"])])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    customers = fetch_customers("https://isp.ispwatch.co", "llave", client=client)
    assert [c.id for c in customers] == ["1", "2", "3"]
    assert (
        customers[0].name == "Ana Ruiz"
        and customers[0].access == "fiber"
        and customers[0].nap == "NAP-1"
    )
    assert customers[1].status == "suspendido" and customers[1].access == "wireless"
    assert customers[2].access is None
    assert seen_auth == ["Bearer llave", "Bearer llave"]


def test_ispwatch_rejects_plain_http_and_bad_key() -> None:
    with pytest.raises(SourceError, match="HTTPS"):
        fetch_customers("http://isp.ispwatch.co", "llave")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(SourceError, match="rechazó"):
        fetch_customers("https://isp.ispwatch.co", "mala", client=client)
