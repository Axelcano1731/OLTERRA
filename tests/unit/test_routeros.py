"""Lecturas del MikroTik: sesiones PPPoE, contadores, colas y cómo se unen con las ONU."""

from __future__ import annotations

from olterra.drivers.routeros import combine, match_key, parse_queues, parse_rate

SESSIONS = (
    "HECTOR_CHAVES;E0:51:D8:11:22:33;100.64.1.10;3d4h12m\n"
    "ana.rojas;70:B6:4F:64:59:85;100.64.1.11;45m10s\n"
)
COUNTERS = "<pppoe-HECTOR_CHAVES>;120000;9800000\n<pppoe-ana.rojas>;500;7000\n<l2tp-otro>;1;1\n"
QUEUES = (
    "<pppoe-HECTOR_CHAVES>;<pppoe-HECTOR_CHAVES>;5M/20M\n"
    "Ana Rojas;100.64.1.11/32;2000000/10000000\n"
    "Sin limite;10.0.0.0/8;0/0\n"
)


def test_sessions_with_counters_and_plan() -> None:
    by_user = {s.user: s for s in combine(SESSIONS, COUNTERS, QUEUES)}
    hector = by_user["HECTOR_CHAVES"]
    # Desde el router, tx es la bajada del cliente y rx su subida.
    assert (hector.down_bytes, hector.up_bytes) == (9_800_000, 120_000)
    assert (hector.plan_down_bps, hector.plan_up_bps) == (20_000_000, 5_000_000)
    assert hector.mac == "E0:51:D8:11:22:33" and hector.uptime == "3d4h12m"
    # La cola de Ana se encuentra por la IP de su sesión.
    assert by_user["ana.rojas"].plan_down_bps == 10_000_000


def test_rates_and_queues() -> None:
    assert parse_rate("20M") == 20_000_000
    assert parse_rate("512k") == 512_000
    assert parse_rate("0") is None
    assert parse_queues("q;a,b/32;1M/2M\n")[0].targets == ("a", "b")


def test_name_match_ignores_accents_spaces_and_case() -> None:
    assert match_key("José Pérez") == match_key("jose_perez") == "joseperez"
    assert match_key(None) == ""


def test_garbage_lines_are_ignored() -> None:
    assert combine("Flags: X\n\n;;;\n", "", "") == []
