"""Lecturas del MikroTik del ISP: sesiones PPPoE, sus contadores y las colas (el plan).

Olterra entra con el usuario ``olterra`` (solo lectura, solo SSH desde el túnel, A.20) y manda un
comando por canal SSH (``SessionProfile.exec_commands``): la consola interactiva de RouterOS
detecta la terminal y pinta con colores. Cada lectura es un ``:foreach … :put`` que imprime una
línea ``a;b;c`` por elemento, igual en RouterOS 6 y 7 y sin depender de cómo se dibujan las tablas
(probado en el concentrador RouterOS 7.24.5, 2026-11-04).

- El tráfico de un cliente PPPoE es el de su interfaz ``<pppoe-usuario>``: desde el router, ``tx``
  es lo que le manda al cliente (su bajada) y ``rx`` lo que recibe (su subida). La interfaz nace
  con cada sesión, así que su contador vuelve a cero cuando el cliente se reconecta.
- El plan sale de la cola simple del cliente: ``max-limit`` es ``subida/bajada``. La cola se
  encuentra por su objetivo (``<pppoe-usuario>`` o la IP de la sesión) o por su nombre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from olterra.executor.plan import SessionProfile

SESSION = SessionProfile(
    prompt_pattern=r".*",  # sin terminal: no hay prompt que esperar
    exec_commands=True,
    error_patterns=(
        r"(?i)syntax error",
        r"(?i)expected end of command",
        r"(?i)bad command name",
        r"(?i)no such item",
        r"(?i)not enough permissions",
        r"(?im)^\s*failure:",
    ),
)

PPPOE_SESSIONS = (
    ":foreach i in=[/ppp active find where service=pppoe] do={:put ([/ppp active get $i name]"
    '.";".[/ppp active get $i caller-id].";".[/ppp active get $i address].";"'
    ".[/ppp active get $i uptime])}"
)
PPPOE_COUNTERS = (
    ':foreach i in=[/interface find where type="pppoe-in"] do={:put ([/interface get $i name]'
    '.";".[/interface get $i rx-byte].";".[/interface get $i tx-byte])}'
)
QUEUES = (
    ":foreach i in=[/queue simple find] do={:put ([/queue simple get $i name]"
    '.";".[/queue simple get $i target].";".[/queue simple get $i max-limit])}'
)
COMMANDS = {
    "router.pppoe": PPPOE_SESSIONS,
    "router.counters": PPPOE_COUNTERS,
    "router.queues": QUEUES,
}


@dataclass(frozen=True)
class PppoeSession:
    user: str
    mac: str | None
    address: str | None
    uptime: str | None
    # Contadores de su interfaz: bajada (tx del router) y subida (rx).
    down_bytes: int | None = None
    up_bytes: int | None = None
    # El plan, de su cola: bajada y subida en bit/s.
    plan_down_bps: int | None = None
    plan_up_bps: int | None = None


def _rows(text: str, fields: int) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(";")
        if len(parts) == fields and parts[0]:
            rows.append([part.strip() for part in parts])
    return rows


_IFACE = re.compile(r"^<pppoe-(.+)>$")
_RATE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*$")
_UNIT = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}


def parse_rate(text: str) -> int | None:
    """``20M`` → 20 000 000; ``0`` (sin límite) → None."""
    match = _RATE.match(text)
    if match is None:
        return None
    value = int(float(match.group(1)) * _UNIT[match.group(2).lower()])
    return value or None


def parse_sessions(text: str) -> dict[str, PppoeSession]:
    return {
        user: PppoeSession(
            user=user, mac=mac or None, address=address or None, uptime=uptime or None
        )
        for user, mac, address, uptime in _rows(text, 4)
    }


def parse_counters(text: str) -> dict[str, tuple[int, int]]:
    """Usuario → (bajada, subida) desde ``<pppoe-usuario>;rx;tx``."""
    counters = {}
    for name, rx, tx in _rows(text, 3):
        match = _IFACE.match(name)
        if match and rx.isdigit() and tx.isdigit():
            counters[match.group(1)] = (int(tx), int(rx))
    return counters


@dataclass(frozen=True)
class Queue:
    name: str
    targets: tuple[str, ...]
    up_bps: int | None
    down_bps: int | None


def parse_queues(text: str) -> list[Queue]:
    queues = []
    for name, target, limit in _rows(text, 3):
        up, _, down = limit.partition("/")
        queues.append(
            Queue(
                name=name,
                targets=tuple(
                    t.strip().removesuffix("/32") for t in target.split(",") if t.strip()
                ),
                up_bps=parse_rate(up),
                down_bps=parse_rate(down or up),
            )
        )
    return queues


def combine(sessions_text: str, counters_text: str, queues_text: str) -> list[PppoeSession]:
    """Cada sesión con sus contadores y su plan."""
    sessions = parse_sessions(sessions_text)
    counters = parse_counters(counters_text)
    queues = parse_queues(queues_text)
    combined = []
    for user, session in sessions.items():
        queue = next(
            (
                q
                for q in queues
                if f"<pppoe-{user}>" in q.targets
                or (session.address and session.address in q.targets)
                or q.name in (user, f"<pppoe-{user}>")
            ),
            None,
        )
        down, up = counters.get(user, (None, None))
        combined.append(
            PppoeSession(
                user=user,
                mac=session.mac,
                address=session.address,
                uptime=session.uptime,
                down_bytes=down,
                up_bytes=up,
                plan_down_bps=queue.down_bps if queue else None,
                plan_up_bps=queue.up_bps if queue else None,
            )
        )
    return combined


def match_key(text: str | None) -> str:
    """Para unir una ONU con su usuario PPPoE por el nombre: "José Pérez" = "jose_perez"."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", plain.lower())
