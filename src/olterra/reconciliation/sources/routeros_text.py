"""Texto copiado de un MikroTik: ``/ppp secret export`` o ``/ppp active print terse``.

Es la forma más fácil de que un ISP comparta sus datos sin dar acceso al router.
Las claves de los secretos se descartan al leer: nunca se guardan ni se muestran.
"""

from __future__ import annotations

import re

from olterra.reconciliation.model import PppoeSecret, PppoeSession

_PAIR = re.compile(r'([\w.-]+)=("(?:[^"\\]|\\.)*"|\S*)')


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        return re.sub(r"\\(.)", r"\1", inner)
    return value


def _logical_lines(text: str) -> list[str]:
    # El export parte las líneas largas con "\" al final.
    joined = re.sub(r"\\\r?\n\s*", "", text)
    return [line.strip() for line in joined.splitlines() if line.strip()]


def parse_pairs(text: str) -> list[dict[str, str]]:
    """Cada línea con pares ``clave=valor`` se vuelve un diccionario."""
    entries = []
    for line in _logical_lines(text):
        if line.startswith(("#", "/")):
            continue
        pairs = {key: _unquote(value) for key, value in _PAIR.findall(line)}
        if pairs:
            entries.append(pairs)
    return entries


def secrets_from_text(text: str, router: str = "MikroTik") -> list[PppoeSecret]:
    secrets = []
    for entry in parse_pairs(text):
        name = entry.get("name")
        if not name:
            continue
        if entry.get("service") not in (None, "", "pppoe", "any"):
            continue
        secrets.append(
            PppoeSecret(
                router=router,
                name=name,
                profile=entry.get("profile"),
                disabled=entry.get("disabled") in ("yes", "true"),
                comment=entry.get("comment"),
            )
        )
    return secrets


def sessions_from_text(text: str, router: str = "MikroTik") -> list[PppoeSession]:
    sessions = []
    for entry in parse_pairs(text):
        name = entry.get("name")
        if not name or entry.get("service") not in (None, "", "pppoe"):
            continue
        sessions.append(
            PppoeSession(
                router=router,
                name=name,
                caller_id=entry.get("caller-id"),
                address=entry.get("address"),
            )
        )
    return sessions
