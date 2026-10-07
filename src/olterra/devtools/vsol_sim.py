"""Simulador de la CLI de una OLT VSOL GPON por SSH, para desarrollo y pruebas.

OJO: no reproduce un firmware real. Sigue la sintaxis del manual CLI v2.1 y
responde con salidas SINTÉTICAS en un formato plausible. Sirve para probar el
ejecutor de punta a punta (SSH, prompts, enable, paginación, errores) sin hardware.

Con ``--capturas DIR`` responde con las salidas reales que dejó ``olterra-capture``
en el laboratorio: así se puede desarrollar contra lo que de verdad dice la OLT.

    python -m olterra.devtools.vsol_sim --puerto 2222
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh

log = logging.getLogger(__name__)


@dataclass
class SimOnu:
    pon: int
    onu: int
    serial: str
    state: str = "working"
    model: str = "V2802DAC"
    profile: str = "HGU"
    description: str = ""
    rx_dbm: float = -19.5
    # Lo que devuelve "show running-config onu N": se llena con cada comando de aprovisionamiento.
    config: list[str] = field(default_factory=list)


@dataclass
class SimConfig:
    username: str = "admin"
    password: str = "olterra-sim"  # noqa: S105 - clave del simulador local, no de un equipo
    enable_password: str = "olterra-sim"  # noqa: S105
    hostname: str = "gpon-olt"
    model: str = "V1600G1"
    firmware: str = "SIMULADO-1.0"
    pon_ports: int = 8
    page_lines: int = 20
    # Algunos firmwares no conocen "terminal length 0": la salida pagina igual.
    terminal_length_supported: bool = True
    onus: list[SimOnu] = field(default_factory=list)
    autofind: list[tuple[int, str]] = field(default_factory=list)
    # (comando, pon) -> salida grabada. pon None = cualquier modo.
    replay: dict[tuple[str, int | None], str] = field(default_factory=dict)


def load_replay(directory: Path) -> dict[tuple[str, int | None], str]:
    """Lee el ``manifest.json`` de ``olterra-capture`` y arma la tabla de respuestas."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    replay: dict[tuple[str, int | None], str] = {}
    for entry in manifest["commands"]:
        if entry.get("file") and entry.get("command"):
            text = (directory / entry["file"]).read_text(encoding="utf-8")
            replay[(entry["command"], entry.get("pon"))] = text
    return replay


def default_config() -> SimConfig:
    return SimConfig(
        onus=[
            SimOnu(
                1,
                1,
                "VSOL0008D09C",
                description="demo-cliente-1",
                # Como la guarda una V1600G0-B real (tests/fixtures), para "copiar una ONU".
                config=[
                    "onu add 1 profile default sn VSOL0008D09C",
                    "onu 1 desc demo-cliente-1",
                    "onu 1 profile onu default",
                    "onu 1 tcont 1 name INTERNET dba default1",
                    "onu 1 gemport 1 tcont 1 gemport_name INTERNET portid 129",
                    "onu 1 gemport 1 traffic-limit downstream default",
                    "onu 1 service ser_1 gemport 1 vlan 100",
                    "onu 1 service-port 1 gemport 1 uservlan 100 vlan 100 new_cos 0",
                    "onu 1 pri wan_adv add route",
                    "onu 1 pri wan_adv index 1 route mode internet mtu 1492",
                    "onu 1 pri wan_adv index 1 route ipv4 pppoe proxy disable user demo1 "
                    "pwd ****** mode auto nat enable",
                    "onu 1 pri wan_adv index 1 vlan tag wan_vlan 100 0",
                    "onu 1 pri wan_adv index 1 bind lan1 lan2 lan3 lan4 ssid1",
                    "onu 1 pri wifi_ssid 1 name DEMO-1 hide disable auth_mode wpa2psk "
                    "encrypt_type tkipaes shared_key ****** rekey_interval 0",
                    "onu 1 pri firewall level low",
                ],
            ),
            SimOnu(1, 2, "VSOL00A1B2C3", rx_dbm=-24.8, description="demo-cliente-2"),
            SimOnu(1, 3, "HWTC1F2E3D4C", state="offline", model="HG8310M", profile="SFU"),
            SimOnu(2, 1, "VSOL00C0FFEE", rx_dbm=-28.4),
        ],
        autofind=[(1, "VSOL00BEEF01"), (2, "ZTEGC0A1B2C3")],
    )


class _Auth(asyncssh.SSHServer):
    def __init__(self, config: SimConfig) -> None:
        self._config = config

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == self._config.username and password == self._config.password


# La sintaxis con que la V1600G0-B guarda sus ONU (drivers/vsol_gpon/provisioning.py).
_PROVISION = re.compile(
    r"onu (\d+) (?:"
    r"desc \S+"
    r"|profile onu \S+"
    r"|tcont \d+ name \S+ dba \S+"
    r"|gemport \d+ tcont \d+ gemport_name \S+"
    r"|gemport \d+ traffic-limit downstream \S+"
    r"|service \S+ gemport \d+ vlan \d+"
    r"|service-port \d+ gemport \d+ uservlan \d+ vlan \d+ new_cos \d"
    r"|pri wan_adv add route"
    r"|pri wan_adv index \d+ route mode internet mtu \d+"
    r"|pri wan_adv index \d+ route ipv4 pppoe proxy disable user \S+ pwd \S+ mode auto nat \S+"
    r"|pri wan_adv index \d+ vlan tag wan_vlan \d+ \d"
    r"|pri wan_adv index \d+ bind [a-z0-9 ]+"
    r"|pri wifi_ssid \d+ name \S+ hide disable auth_mode wpa2psk encrypt_type tkipaes"
    r" shared_key \S+ rekey_interval 0"
    r")"
)

# Escribe un aviso (p. ej. "Password:") y lee una línea sin eco.
Ask = Callable[[str], Awaitable[str | None]]


class _Cli:
    def __init__(self, config: SimConfig, seen: list[str]) -> None:
        self.c = config
        self.mode = "user"
        self.pon: int | None = None
        self.paging = True
        self.seen = seen

    def prompt(self) -> str:
        h = self.c.hostname
        return {
            "user": f"{h}>",
            "enable": f"{h}#",
            "config": f"{h}(config)#",
            "pon": f"{h}(config-pon-0/{self.pon})#",
        }[self.mode]

    # --- Salidas sintéticas ---------------------------------------------------------

    def _onus(self) -> list[SimOnu]:
        return [o for o in self.c.onus if o.pon == self.pon]

    def _onu_info(self) -> str:
        lines = [
            f"{'Onuindex':<12}{'Model':<12}{'Profile':<10}{'Mode':<6}{'AuthInfo':<16}State",
            "-" * 70,
        ]
        for o in self._onus():
            lines.append(
                f"{f'GPON0/{o.pon}:{o.onu}':<12}{o.model:<12}{o.profile:<10}{'sn':<6}{o.serial:<16}{o.state}"
            )
        return "\n".join(lines)

    def _autofind(self) -> str:
        rows = [s for p, s in self.c.autofind if p == self.pon]
        lines = [f"{'Onuindex':<12}{'Sn':<16}State", "-" * 40]
        lines += [f"{f'GPON0/{self.pon}:{i}':<12}{s:<16}unknown" for i, s in enumerate(rows, 1)]
        return "\n".join(lines)

    def _rx_power(self) -> str:
        lines = [f"{'Onuindex':<12}Rx Power(dBm)", "-" * 30]
        for o in self._onus():
            value = "N/A" if o.state != "working" else f"{o.rx_dbm:.2f}"
            lines.append(f"{f'GPON0/{o.pon}:{o.onu}':<12}{value}")
        return "\n".join(lines)

    def _optical(self, onu: int) -> str | None:
        match = next((o for o in self._onus() if o.onu == onu), None)
        if match is None:
            return None
        return (
            f"Rx optical power(dBm)  : {match.rx_dbm:.2f}\n"
            "Tx optical power(dBm)  : 2.15\n"
            "Temperature(C)         : 41.00\n"
            "Voltage(V)             : 3.28\n"
            "Bias current(mA)       : 13.20"
        )

    # --- Intérprete --------------------------------------------------------------------

    async def execute(self, line: str, ask: Ask) -> tuple[str, bool]:
        """Devuelve ``(salida, cerrar_sesión)``."""
        cmd = " ".join(line.split())
        if not cmd:
            return "", False
        self.seen.append(cmd)
        replayed = self.c.replay.get((cmd, self.pon)) or self.c.replay.get((cmd, None))
        if replayed is not None and cmd.startswith("show"):
            return replayed, False
        if self.mode == "user":
            if cmd == "enable":
                password = await ask("Password:")
                if password == self.c.enable_password:
                    self.mode = "enable"
                    return "", False
                return "% Bad passwords", False
            if cmd in ("exit", "quit"):
                return "", True
            return "% Unknown command.", False
        if cmd == "terminal length 0" and self.c.terminal_length_supported:
            self.paging = False
            return "", False
        if cmd in ("configure terminal", "config terminal"):
            self.mode, self.pon = "config", None
            return "", False
        if cmd == "end":
            self.mode, self.pon = "enable", None
            return "", False
        if cmd == "exit":
            if self.mode == "pon":
                self.mode, self.pon = "config", None
                return "", False
            if self.mode == "config":
                self.mode = "enable"
                return "", False
            return "", True
        if cmd in ("write", "write memory"):
            return "Saving current configuration...\nOK!", False
        if cmd == "show version":
            return (
                f"Device Type          : {self.c.model}\n"
                "Hardware Version     : V2.0\n"
                f"Software Version     : {self.c.firmware}\n"
                "Compile Time         : 2026-01-01 00:00:00"
            ), False
        match = re.fullmatch(r"interface gpon 0/(\d+)", cmd)
        if match and self.mode in ("config", "pon"):
            pon = int(match.group(1))
            if not 1 <= pon <= self.c.pon_ports:
                return "% Parameter out of range.", False
            self.mode, self.pon = "pon", pon
            return "", False
        if self.mode == "pon":
            return self._pon_command(cmd), False
        return "% Unknown command.", False

    def _running_config(self, onu: int) -> str:
        match = next((o for o in self._onus() if o.onu == onu), None)
        if match is None:
            return "Error: onu is not exist"
        lines = match.config or [f"onu add {onu} profile {match.profile} sn {match.serial}"]
        return "\n".join([f"----------onu {onu} running-config----------", *lines])

    def _onu_state(self) -> str:
        lines = [
            f"{'OnuIndex':<12}{'Admin State':<15}{'OMCC State':<14}{'Phase State':<15}Serial Number",
            "-" * 63,
        ]
        for o in self._onus():
            omcc = "enable" if o.state == "working" else "disable"
            lines.append(
                f"{f'GPON0/{o.pon}:{o.onu}':<12}{'enable':<15}{omcc:<14}{o.state:<15}{o.serial}"
            )
        return "\n".join(lines)

    def _provision(self, cmd: str) -> str | None:
        """Comandos de aprovisionamiento de la V1600G0-B: se guardan en la config de la ONU."""
        match = _PROVISION.fullmatch(cmd)
        if match is None:
            return None
        onu = next((o for o in self._onus() if o.onu == int(match.group(1))), None)
        if onu is None:
            return "Error: onu is not exist"
        # Como la OLT real: la clave PPPoE se guarda tapada.
        saved = re.sub(r" pwd \S+", " pwd ******", cmd)
        if cmd.startswith(f"onu {onu.onu} desc "):
            onu.description = cmd.split(" desc ", 1)[1]
        onu.config.append(saved)
        return ""

    def _pon_command(self, cmd: str) -> str:
        if cmd == "show onu info":
            return self._onu_info()
        if cmd == "show onu state":
            return self._onu_state()
        match = re.fullmatch(r"show running-config onu (\d+)", cmd)
        if match:
            return self._running_config(int(match.group(1)))
        provisioned = self._provision(cmd)
        if provisioned is not None:
            return provisioned
        if cmd == "show onu auto-find":
            return self._autofind()
        if cmd == "show pon onu all rx-power":
            return self._rx_power()
        match = re.fullmatch(r"show onu (\d+) optical-info", cmd)
        if match:
            return self._optical(int(match.group(1))) or "Error: onu is not exist"
        match = re.fullmatch(r"onu add (\d+) profile (\S+) sn (\S+)", cmd)
        if match:
            onu, profile, serial = int(match.group(1)), match.group(2), match.group(3)
            if any(o.onu == onu for o in self._onus()) or self.pon is None:
                return f"Error: onu {onu} is already exist"
            self.c.onus.append(
                SimOnu(self.pon, onu, serial, profile=profile, config=[cmd], state="working")
            )
            self.c.autofind = [(p, s) for p, s in self.c.autofind if s != serial]
            return ""
        match = re.fullmatch(r"no onu (\d+)", cmd)
        if match:
            before = len(self.c.onus)
            self.c.onus = [
                o for o in self.c.onus if not (o.pon == self.pon and o.onu == int(match.group(1)))
            ]
            return "" if len(self.c.onus) < before else "Error: onu is not exist"
        if re.fullmatch(r"onu (\d+) (reboot|activate|deactivate)", cmd):
            return ""
        return "% Unknown command."


class VsolSimulator:
    def __init__(self, config: SimConfig | None = None) -> None:
        self.config = config or default_config()
        self.commands_seen: list[str] = []
        self._server: asyncssh.SSHAcceptor | None = None
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")

    @property
    def public_host_key(self) -> str:
        return self.host_key.export_public_key("openssh").decode("ascii").strip()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncssh.create_server(
            lambda: _Auth(self.config),
            host,
            port,
            server_host_keys=[self.host_key],
            process_factory=self._handle,
            line_editor=False,
            encoding="utf-8",
        )
        sockets = self._server.sockets
        return int(sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, process: Any) -> None:
        cli = _Cli(self.config, self.commands_seen)
        last = ""

        async def readline(echo: bool) -> str | None:
            nonlocal last
            buffer = ""
            while True:
                ch = await process.stdin.read(1)
                if ch == "":
                    return None
                if ch == "\n" and last == "\r":
                    last = ch
                    continue
                last = ch
                if ch in "\r\n":
                    process.stdout.write("\r\n")
                    return buffer
                if ch in "\x7f\b":
                    buffer = buffer[:-1]
                    continue
                buffer += ch
                if echo:
                    process.stdout.write(ch)

        async def write_paged(text: str) -> None:
            lines = text.split("\n")
            for i, line in enumerate(lines, start=1):
                process.stdout.write(line + "\r\n")
                if cli.paging and i % self.config.page_lines == 0 and i < len(lines):
                    process.stdout.write(" --More-- ")
                    if await process.stdin.read(1) == "":
                        return
                    process.stdout.write("\r" + " " * 10 + "\r")

        try:
            process.stdout.write("\r\nOlterra - simulador VSOL GPON (salidas sinteticas)\r\n\r\n")
            process.stdout.write(cli.prompt())

            async def ask(text: str) -> str | None:
                process.stdout.write(text)
                return await readline(False)

            while True:
                line = await readline(True)
                if line is None:
                    break
                output, close = await cli.execute(line, ask)
                if output:
                    await write_paged(output)
                if close:
                    break
                process.stdout.write(cli.prompt())
        except (
            asyncssh.BreakReceived,
            asyncssh.TerminalSizeChanged,
            BrokenPipeError,
            ConnectionError,
        ):
            pass
        finally:
            process.exit(0)


async def _serve(args: argparse.Namespace) -> None:
    config = default_config()
    config.password = config.enable_password = args.clave
    if args.capturas:
        config.replay = load_replay(Path(args.capturas))
    simulator = VsolSimulator(config)
    port = await simulator.start(args.host, args.puerto)
    print(f"Simulador VSOL escuchando en {args.host}:{port} (usuario {config.username})")
    print(f"Llave de host: {simulator.public_host_key}")
    await asyncio.Event().wait()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Simulador de CLI VSOL GPON por SSH")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=2222)
    parser.add_argument("--clave", default="olterra-sim")
    parser.add_argument(
        "--capturas", help="Directorio de olterra-capture para responder con salidas reales"
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
