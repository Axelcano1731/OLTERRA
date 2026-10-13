"""Reglas del monitoreo: juntar lo que dijo la OLT, qué alarmas abrir y cuándo guardar muestra."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from olterra.api.monitoring import Reading, evaluate, merge_readings, wants_sample


def outputs(*, brief_ok: bool = True) -> list[dict[str, object]]:
    return [
        {
            "key": "interfaces.brief",
            "ok": brief_ok,
            "data": {
                "pons": [1, 2],
                "onus": [
                    {"pon": 1, "onu": 1, "description": "ANA", "up": True},
                    {"pon": 1, "onu": 2, "description": "LUIS", "up": False},
                    {"pon": 2, "onu": 1, "description": "EVA", "up": True},
                ],
            }
            if brief_ok
            else None,
        },
        {
            "key": "onu.state",
            "params": {"pon": 1},
            "ok": True,
            "data": [
                {"pon": 1, "onu": 1, "phase": "working", "serial": "VSOL00000001"},
                {"pon": 1, "onu": 2, "phase": "LOS", "serial": "VSOL00000002"},
            ],
        },
        {
            "key": "onu.rx_power_all",
            "params": {"pon": 1},
            "ok": True,
            "data": [{"pon": 1, "onu": 1, "rx_dbm": -19.5}, {"pon": 1, "onu": 2, "rx_dbm": None}],
        },
        # El PON 2 no respondió a "show onu state": se usa arriba/abajo del brief.
        {"key": "onu.state", "params": {"pon": 2}, "ok": False, "data": None},
    ]


def test_merge_readings() -> None:
    readings, complete = merge_readings(outputs())
    assert complete
    assert readings == [
        Reading(1, 1, True, "working", "VSOL00000001", "ANA", -19.5),
        Reading(1, 2, False, "LOS", "VSOL00000002", "LUIS", None),
        Reading(2, 1, True, "working", None, "EVA", None),
    ]
    _, complete = merge_readings(outputs(brief_ok=False))
    assert not complete  # sin el brief no se sabe si una ONU dejó de existir


def r(
    onu: int, *, online: bool = True, phase: str = "working", rx: float | None = -19.0
) -> Reading:
    return Reading(1, onu, online, phase, None, f"C{onu}", rx)


def test_offline_onus_say_why() -> None:
    alarms = {a.onu: a for a in evaluate([r(1, online=False, phase="LOS", rx=None)])}
    assert alarms[1].kind == "onu_offline" and alarms[1].severity == "critical"
    assert alarms[1].message == "C1: Sin luz (fibra)"
    alarms = {a.onu: a for a in evaluate([r(1, online=False, phase="DyingGasp", rx=None)])}
    assert alarms[1].severity == "warning" and "sin energía" in alarms[1].message


def test_signal_window() -> None:
    found = {(a.onu, a.kind, a.severity) for a in evaluate([
        r(1, rx=-19.0), r(2, rx=-25.5), r(3, rx=-27.8), r(4, rx=-7.2), r(5, rx=None),
    ])}  # fmt: skip
    assert found == {(2, "rx_low", "warning"), (3, "rx_low", "critical"), (4, "rx_high", "warning")}


def test_pon_down_when_most_onus_fall_together() -> None:
    down = [r(i, online=False, phase="LOS", rx=None) for i in range(1, 4)]
    alarms = [a for a in evaluate([*down, r(4)]) if a.kind == "pon_down"]
    assert len(alarms) == 1 and (alarms[0].pon, alarms[0].onu) == (1, 0)
    assert (
        alarms[0].message
        == "PON 1: 3 de 4 clientes sin servicio (posible corte de fibra o splitter)"
    )
    # Dos caídas de cinco no es el PON: son dos clientes.
    few = [r(1, online=False, phase="LOS"), r(2, online=False, phase="LOS"), r(3), r(4), r(5)]
    assert not [a for a in evaluate(few) if a.kind == "pon_down"]


def test_samples_are_hourly_unless_something_changes() -> None:
    now = datetime.now(UTC)
    reading = r(1, rx=-20.0)
    assert wants_sample(None, reading, now)
    assert not wants_sample((now - timedelta(minutes=10), True, -20.4), reading, now)
    assert wants_sample((now - timedelta(minutes=10), True, -21.2), reading, now)  # 1,2 dB
    assert wants_sample((now - timedelta(minutes=10), False, None), reading, now)  # volvió
    assert wants_sample((now - timedelta(minutes=61), True, -20.0), reading, now)
