"""Enmascarado de secretos en comandos y salidas antes de guardarlos o mostrarlos.

Dos capas:

1. Secretos conocidos: el ejecutor sabe la clave con la que entró y la borra de
   cualquier salida (una OLT puede repetirla en el eco o en ``show running-config``).
2. Patrones: el valor que sigue a palabras como ``password`` o ``community`` en
   la misma línea. Cubre claves que no conocemos (las de otros usuarios en un
   respaldo de configuración, por ejemplo).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "******"

# El valor va en la MISMA línea ([ \t]+, no \s+): un "Password:" de un prompt no
# debe tragarse la primera palabra de la línea siguiente.
_KEYWORD_VALUE = re.compile(
    r"(?i)\b(login-password|enable-password|password|passwd|community|key-string"
    r"|pre-shared-key|psk|passphrase|wpa-?key|secret|private-key"
    # VSOL en la configuración de la ONU: clave WiFi (shared_key) y PPPoE (pwd).
    r"|shared_key|pwd)"
    r"([ \t]*[=:]?[ \t]+|=)"
    r"(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s]+)"
)

# Palabras que pueden seguir a la palabra clave sin ser el secreto.
_NOT_A_VALUE = {"ro", "rw", "encrypted", "0", "7", "admin", "normal"}


def _mask_keyword_values(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(3)
        if value.lower() in _NOT_A_VALUE:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{MASK}"

    return _KEYWORD_VALUE.sub(replace, text)


def redact(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Devuelve ``text`` con los secretos conocidos y los patrones sensibles tapados."""
    # Primero los conocidos, del más largo al más corto, para no dejar restos de
    # un secreto que contiene a otro.
    for secret in sorted({s for s in known_secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return _mask_keyword_values(text)
