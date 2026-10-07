"""Driver de OLT VSOL GPON (serie V1600G: V1600G1, V1600G1B, V1600G2, V1600GS…)."""

from __future__ import annotations

from olterra.drivers.base import Driver
from olterra.drivers.vsol_gpon.capabilities import RULES
from olterra.drivers.vsol_gpon.commands import COMMANDS, OVERRIDES
from olterra.executor.plan import SessionProfile

# Prompts del manual: "gpon-olt>", "gpon-olt#", "gpon-olt(config)#",
# "gpon-olt(config-pon-0/1)#", "gpon-olt(profile-onu:10)#". El hostname es
# configurable (empieza con letra). También se acepta un prompt de shell Linux
# (usuario@equipo:~$) por los modelos cuyo SSH cae en una shell.
PROMPT = (
    r"(?:[A-Za-z][\w.\-]*\s?(?:\([^()\r\n]*\))?[>#]"
    r"|[\w.\-]+@[\w.\-]+(?::[^\s$#]*)?\s?[$#])"
)

# Mensajes de error del manual (§2.3.4) y de una shell si el SSH no cae en la CLI.
ERRORS = (
    r"(?i)^\s*%?\s*(?:unknown command|command incomplete|too many parameters|ambiguous command)",
    r"(?i)^\s*%\s*(?:error|invalid)",
    r"(?i)^\s*error:",
    r"(?i)command not found",
    # La V1600G0-B responde así a un comando "pri" que la ONU no acepta (2026-10-07): no es éxito.
    r"(?i)unsupport(?:ed)?\b",
)

SESSION = SessionProfile(
    prompt_pattern=PROMPT,
    # El manual (§2.3.2) dice que la salida pausa por pantalla y sigue con cualquier
    # tecla, sin mostrar el texto exacto del aviso. Se cubren los habituales.
    pager_pattern=r"(?i)-{2,}\s*more\b.*-{2,}|<-+\s*more\s*-+>|press any key|\(q to quit\)",
    pager_response=" ",
    enable_command="enable",
    enable_password_pattern=r"(?i)password:\s*$",  # noqa: S106 - es el texto del prompt, no una clave
    setup_commands=("terminal length 0",),
    error_patterns=ERRORS,
)

VSOL_GPON = Driver(
    key="vsol-gpon",
    vendor="VSOL",
    family="GPON",
    session=SESSION,
    commands=COMMANDS,
    overrides=OVERRIDES,
    capability_rules=RULES,
)
