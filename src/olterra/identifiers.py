"""Normalización de identificadores que llegan escritos de mil formas.

Un mismo serial GPON aparece como ``VSOL0008D09C`` en la OLT, ``vsol-0008d09c``
en el CRM y ``56534F4C0008D09C`` en SNMP. Una MAC, como ``aa:bb:..``,
``AA-BB-..`` o ``aabb.ccdd.eeff``. Cruzar fuentes exige una forma canónica.
"""

from __future__ import annotations

import re
import unicodedata

_SERIAL_SEPARATORS = re.compile(r"[\s\-:._]")
_SERIAL_CANONICAL = re.compile(r"^[A-Z0-9]{4}[0-9A-F]{8}$")
_HEX16 = re.compile(r"^[0-9A-F]{16}$")
_VENDOR = re.compile(r"^[A-Z0-9]{4}$")
_HEX = re.compile(r"[0-9a-fA-F]")
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD],  # anchos cero, BOM, guion suave
)


def normalize_gpon_serial(value: str | None) -> str | None:
    """Forma canónica ``VVVVXXXXXXXX`` (4 caracteres de fabricante + 8 hex), o ``None``."""
    if not value:
        return None
    text = value.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    text = _SERIAL_SEPARATORS.sub("", text).upper()
    if _SERIAL_CANONICAL.fullmatch(text):
        return text
    if _HEX16.fullmatch(text):
        vendor = bytes.fromhex(text[:8]).decode("ascii", errors="replace")
        if _VENDOR.fullmatch(vendor):
            return vendor + text[8:]
    return None


def normalize_mac(value: str | None) -> str | None:
    """Forma canónica ``AA:BB:CC:DD:EE:FF``, o ``None`` si no hay 12 dígitos hex."""
    if not value:
        return None
    text = value.strip()
    if not re.fullmatch(r"[0-9A-Fa-f:\-.\s]+", text):
        return None
    digits = "".join(_HEX.findall(text))
    if len(digits) != 12:
        return None
    digits = digits.upper()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def clean_text(value: str) -> str:
    """Quita caracteres invisibles y espacios raros (NBSP) sin tocar lo demás."""
    text = value.translate(_INVISIBLE).replace("\u00a0", " ")
    return text.strip()


def loose_key(value: str) -> str:
    """Llave laxa para detectar errores de digitación.

    ``"DANIELA _PARADA"``, ``"daniela_parada"`` y ``"Daniéla_Parada "`` dan la misma
    llave. Sirve para SOSPECHAR que dos valores son el mismo, nunca para darlos
    por iguales: un usuario PPPoE con un espacio de más no autentica.
    """
    text = unicodedata.normalize("NFKD", clean_text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", "", text).casefold()
