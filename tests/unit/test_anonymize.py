from __future__ import annotations

from olterra.capture.anonymize import Anonymizer


def test_mac_in_every_format_becomes_a_consistent_fake() -> None:
    anon = Anonymizer(salt=b"sal-de-prueba")
    text = (
        "aa:bb:cc:dd:ee:01 AA-BB-CC-DD-EE-01 aabb.ccdd.ee01 "
        "b464:153a:35b1 "  # V1600G0-B V1.4.8R: tres grupos de cuatro con dos puntos
        "hora 05:38:17"
    )
    out = anon.text(text)
    assert "aa:bb" not in out and "b464:153a:35b1" not in out
    colon, dash, dotted, groups4, *_ = out.split()
    assert colon.startswith("02:") and dash.startswith("02-") and dotted.startswith("02")
    assert len(groups4) == 14 and groups4.count(":") == 2  # conserva el formato
    assert "05:38:17" in out  # una hora no es una MAC
    assert anon.text("b464:153a:35b1") == anon.text("b464:153a:35b1")  # consistente


def test_serials_are_replaced_and_keep_their_prefix() -> None:
    anon = Anonymizer(salt=b"sal-de-prueba")
    out = anon.text("GPON0/1:2 V824 default sn GPON00323e8c")
    assert "GPON00323e8c" not in out
    assert "GPON" in out and out.split()[-1].startswith("GPON")
