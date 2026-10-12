"""Sesión interactiva con la CLI de una OLT, independiente del transporte.

Las OLT no tienen un canal de comandos con código de salida: se escribe en una
terminal y se lee hasta ver el prompt. Este módulo resuelve lo que eso implica:

- saber cuándo terminó un comando (prompt en la última línea, nunca a mitad);
- paginación (``--More--``) aunque ``terminal length 0`` no exista en ese firmware;
- limpiar lo que una terminal mete: secuencias ANSI, retrocesos, ``\\r`` que
  sobrescribe la línea al borrar el ``--More--``;
- quitar el eco del comando;
- tiempo máximo por comando, devolviendo lo leído hasta ahí.

El transporte real es SSH (``olterra.executor.ssh``); las pruebas usan uno falso.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from typing import Protocol

from olterra.executor.plan import SessionProfile

# Pregunta a mitad de un comando: (patrón de la última línea, respuesta ya resuelta, máximo de
# veces). Ver ``PromptReply`` en el contrato del plan.
Reply = tuple[str, str, int]

_CSI = re.compile(r"\x1b\[([0-9;?]*)[ -/]*([@-~])")  # ESC [ parámetros letra
_OTHER_ESCAPE = re.compile(r"\x1b[()][A-Za-z0-9]|\x1b[=>78DEHM]")


class CliError(Exception):
    """Falla de la sesión CLI (no de un comando en particular)."""


class CliTimeout(CliError):
    def __init__(self, message: str, partial_output: str) -> None:
        super().__init__(message)
        self.partial_output = partial_output


class CliTransport(Protocol):
    async def write(self, data: str) -> None: ...

    async def read(self, timeout: float) -> str:
        """Devuelve lo que haya llegado (``""`` si no llegó nada en ``timeout``).

        Lanza ``EOFError`` si el otro lado cerró la conexión.
        """
        ...

    async def close(self) -> None: ...


def _first_number(params: str, default: int) -> int:
    head = params.split(";")[0].lstrip("?")
    return int(head) if head.isdigit() else default


def _render_line(line: str) -> str:
    """Lo que una terminal dejaría en pantalla en esta línea, con su cursor.

    Hay OLT (la VSOL V1600G0-B) que imprimen cada columna volviendo al inicio con ``\\r`` y
    avanzando el cursor con ``ESC[<n>C``: ``GPON0/1:2 \\r ESC[11C V824 \\r ESC[32C default``. Si se
    borran las secuencias y cada ``\\r`` sobrescribe desde el inicio, solo sobrevive la última
    celda. Aquí el cursor se mueve de verdad: ``\\r`` vuelve a la columna 0, ``ESC[nC`` y
    ``ESC[nD`` avanzan o retroceden, ``ESC[K`` borra hasta el final, ``\\b`` retrocede y lo demás
    se escribe sobre lo que haya en esa columna (las columnas saltadas quedan en blanco).
    """
    if "\x1b" not in line and "\r" not in line and "\b" not in line:
        return line  # vía rápida: casi todas las líneas (una running-config puede pesar MB)
    buffer: list[str] = []
    col = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\x1b":
            csi = _CSI.match(line, i)
            if csi is not None:
                params, final = csi.group(1), csi.group(2)
                if final == "C":
                    col += max(1, _first_number(params, 1))
                elif final == "D":
                    col = max(0, col - max(1, _first_number(params, 1)))
                elif final == "G":
                    col = max(0, _first_number(params, 1) - 1)
                elif final == "K":
                    mode = _first_number(params, 0)
                    if mode == 0:
                        del buffer[col:]
                    elif mode == 1:
                        buffer[:col] = [" "] * min(col, len(buffer))
                    else:
                        buffer.clear()
                i = csi.end()
                continue
            other = _OTHER_ESCAPE.match(line, i)
            i = other.end() if other else i + 1
            continue
        if ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        else:
            if col > len(buffer):
                buffer.extend(" " * (col - len(buffer)))
            if col < len(buffer):
                buffer[col] = ch
            else:
                buffer.append(ch)
            col += 1
        i += 1
    return "".join(buffer)


def normalize_terminal_text(text: str) -> str:
    """Convierte lo recibido de la terminal en texto plano línea por línea."""
    text = text.replace("\r\n", "\n").replace("\x00", "")
    return "\n".join(_render_line(line).rstrip() for line in text.split("\n"))


class CliSession:
    def __init__(
        self,
        transport: CliTransport,
        profile: SessionProfile,
        *,
        read_chunk_timeout: float = 0.5,
        max_output_chars: int = 5_000_000,
    ) -> None:
        self._transport = transport
        self._profile = profile
        self._prompt = re.compile(profile.prompt_pattern)
        self._pager = re.compile(profile.pager_pattern) if profile.pager_pattern else None
        self._enable_password = (
            re.compile(profile.enable_password_pattern) if profile.enable_password_pattern else None
        )
        self._errors = [re.compile(p, re.MULTILINE) for p in profile.error_patterns]
        self._chunk_timeout = read_chunk_timeout
        self._max_output = max_output_chars
        self.prompt: str | None = None

    # --- Lectura ---------------------------------------------------------------

    def _is_prompt(self, line: str) -> bool:
        return self._prompt.fullmatch(line.strip()) is not None

    async def _read_until(
        self,
        timeout: float,
        *,
        extra_stop: re.Pattern[str] | Sequence[re.Pattern[str]] | None = None,
    ) -> tuple[str, str]:
        """Lee hasta el prompt (o ``extra_stop``). Devuelve ``(texto, última_línea)``.

        El texto devuelto no incluye la última línea (el prompt).
        """
        deadline = time.monotonic() + timeout
        if extra_stop is None:
            stops: Sequence[re.Pattern[str]] = ()
        elif isinstance(extra_stop, re.Pattern):
            stops = (extra_stop,)
        else:
            stops = extra_stop
        raw = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CliTimeout(
                    "La OLT no devolvió el prompt a tiempo", normalize_terminal_text(raw)
                )
            chunk = await self._transport.read(min(self._chunk_timeout, remaining))
            if not chunk:
                continue
            raw += chunk
            if len(raw) > self._max_output:
                raise CliError("La salida excede el máximo permitido; se corta la sesión")
            text = normalize_terminal_text(raw)
            head, _, last = text.rpartition("\n")
            stripped = last.strip()
            if self._pager is not None and stripped and self._pager.search(stripped):
                # Se responde a la paginación y se borra el aviso del texto acumulado.
                await self._transport.write(self._profile.pager_response)
                raw = head + "\n" if head else ""
                continue
            if self._is_prompt(last):
                return head, stripped
            if any(stop.search(last) for stop in stops):
                return head, stripped

    # --- Sesión ------------------------------------------------------------------

    async def login(self, enable_password: str | None) -> str:
        """Espera el primer prompt, entra a la CLI y deja la sesión lista. Devuelve el banner."""
        banner, self.prompt = await self._read_until(self._profile.login_timeout_s)
        for command in self._profile.shell_entry_commands:
            await self.run(command, timeout=self._profile.login_timeout_s)

        if self.prompt.endswith(">") and self._profile.enable_command:
            await self._transport.write(self._profile.enable_command + self._profile.newline)
            _, last = await self._read_until(
                self._profile.login_timeout_s, extra_stop=self._enable_password
            )
            if not self._is_prompt(last):
                if enable_password is None:
                    raise CliError("La OLT pidió clave de enable y el plan no trae una")
                await self._transport.write(enable_password + self._profile.newline)
                _, last = await self._read_until(
                    self._profile.login_timeout_s, extra_stop=self._enable_password
                )
                if not self._is_prompt(last):
                    raise CliError("La OLT rechazó la clave de enable")
            self.prompt = last
            if self.prompt.endswith(">"):
                raise CliError("La OLT rechazó el modo privilegiado (enable)")

        # Si un firmware no conoce alguno (p. ej. "terminal length 0"), la salida trae
        # el error y se sigue: la paginación se atiende de todos modos.
        for command in self._profile.setup_commands:
            await self.run(command, timeout=self._profile.login_timeout_s)
        return banner

    async def run(self, command: str, *, timeout: float, replies: Sequence[Reply] = ()) -> str:
        """Ejecuta un comando y devuelve su salida sin eco ni prompt.

        ``replies`` contesta las preguntas que el comando haga antes de volver al prompt (ver
        ``PromptReply``); las respuestas ya vienen resueltas y no se devuelven en la salida.
        """
        if any(ch in command for ch in "\r\n") or any(
            ch in answer for _, answer, _ in replies for ch in "\r\n"
        ):
            raise CliError("Un comando CLI va en una sola línea")
        questions = [re.compile(pattern) for pattern, _, _ in replies]
        asked = [0] * len(replies)
        collected: list[str] = []
        deadline = time.monotonic() + timeout
        await self._transport.write(command + self._profile.newline)
        try:
            while True:
                text, last = await self._read_until(
                    max(deadline - time.monotonic(), 0.001), extra_stop=questions
                )
                collected.append(text)
                if self._is_prompt(last):
                    self.prompt = last
                    break
                index = next(i for i, q in enumerate(questions) if q.search(last))
                if asked[index] >= replies[index][2]:
                    # Ctrl-C: que lo próximo que se escriba no quede como respuesta.
                    await self._transport.write("\x03")
                    raise CliError(f"La OLT volvió a preguntar «{last}»")
                asked[index] += 1
                collected.append(last)
                await self._transport.write(replies[index][1] + self._profile.newline)
        except CliTimeout as exc:
            partial = _join([*collected, exc.partial_output])
            raise CliTimeout(str(exc), _strip_echo(partial, command)) from exc
        return _strip_echo(_join(collected), command)

    def find_error(self, output: str) -> str | None:
        """Primera línea de la salida que calza con un mensaje de error de la CLI."""
        for pattern in self._errors:
            match = pattern.search(output)
            if match:
                line_start = output.rfind("\n", 0, match.start()) + 1
                line_end = output.find("\n", match.end())
                return output[line_start : line_end if line_end != -1 else None].strip()
        return None

    async def close(self) -> None:
        await self._transport.close()


def _join(parts: list[str]) -> str:
    """Une lo leído entre preguntas (un solo trozo, el caso común, queda tal cual)."""
    if len(parts) == 1:
        return parts[0]
    return "\n".join(piece for piece in (part.strip("\n") for part in parts) if piece)


def _strip_echo(text: str, command: str) -> str:
    lines = text.split("\n")
    # Puede venir una línea vacía antes del eco (resto del prompt anterior).
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().endswith(command.strip()):
        lines.pop(0)
    return "\n".join(lines).strip("\n")
