"""Anonimización de capturas antes de versionarlas.

Cambia seriales GPON y MAC por valores falsos pero consistentes dentro de la
misma captura (el mismo serial real da siempre el mismo falso), así los parsers
y las pruebas siguen viendo relaciones correctas. Usa una sal aleatoria por
captura: los valores falsos no se pueden revertir.

No detecta nombres de clientes en descripciones ni direcciones: eso se revisa a
mano antes de copiar la captura a ``tests/fixtures`` (Ley 1581 de 2012).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from olterra.identifiers import normalize_gpon_serial

_SERIAL = re.compile(r"\b([A-Z0-9]{4})([0-9A-Fa-f]{8})\b")
_SERIAL_HEX = re.compile(r"\b(?:0x)?[0-9A-Fa-f]{16}\b")
_MAC = re.compile(
    r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b|\b[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\b"
)


class Anonymizer:
    def __init__(self, salt: bytes | None = None) -> None:
        self._salt = salt or os.urandom(16)

    def _digest(self, kind: str, value: str, length: int) -> str:
        mac = hmac.new(self._salt, f"{kind}|{value}".encode(), hashlib.sha256)
        return mac.hexdigest()[:length].upper()

    def serial(self, canonical: str) -> str:
        return canonical[:4] + self._digest("serial", canonical, 8)

    def _replace_serial(self, match: re.Match[str]) -> str:
        canonical = normalize_gpon_serial(match.group(0))
        if canonical is None or not re.search(r"[A-Z]", match.group(1)):
            return match.group(0)  # p. ej. un número cualquiera de 12 cifras
        return self.serial(canonical)

    def _replace_serial_hex(self, match: re.Match[str]) -> str:
        canonical = normalize_gpon_serial(match.group(0))
        if canonical is None:
            return match.group(0)
        fake = self.serial(canonical)
        return fake[:4].encode().hex().upper() + fake[4:]

    def _replace_mac(self, match: re.Match[str]) -> str:
        digits = re.sub(r"[^0-9A-Fa-f]", "", match.group(0)).upper()
        # Prefijo localmente administrado (02) para que nunca coincida con un fabricante real.
        fake = "02" + self._digest("mac", digits, 10)
        separator = ":" if ":" in match.group(0) else "-" if "-" in match.group(0) else None
        if separator is None:
            return ".".join(fake[i : i + 4] for i in range(0, 12, 4)).lower()
        return separator.join(fake[i : i + 2] for i in range(0, 12, 2))

    def text(self, text: str) -> str:
        text = _SERIAL_HEX.sub(self._replace_serial_hex, text)
        text = _SERIAL.sub(self._replace_serial, text)
        return _MAC.sub(self._replace_mac, text)
