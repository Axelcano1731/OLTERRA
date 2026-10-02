"""Motor de conciliación: cruza las cuatro fuentes y devuelve hallazgos con acción sugerida.

Cómo se liga una ONU a un cliente, de la evidencia más fuerte a la más débil:

1. ``crm``: el CRM tiene registrado ese serial para el cliente.
2. ``wan``: la ONU (en modo router) tiene configurado el usuario PPPoE del cliente.
3. ``mac``: una MAC aprendida detrás de la ONU es el caller-id de la sesión PPPoE
   del cliente (sirve con ONU en bridge, donde la OLT no sabe del PPPoE).
4. ``descripcion``: la descripción de la ONU en la OLT contiene el usuario PPPoE.

La evidencia de red (2 y 3) manda sobre lo que diga el CRM: si contradice al CRM,
eso es un hallazgo (seriales cruzados), no algo que se corrige solo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from olterra.identifiers import loose_key
from olterra.reconciliation.model import (
    ACTIVE_STATES,
    CLOSED_STATES,
    CUSTOMER_STATES,
    SEVERITY_ORDER,
    CrmCustomer,
    Finding,
    Kind,
    OnuRecord,
    ReconInput,
    Severity,
)

NETWORK_EVIDENCE = ("wan", "mac")


@dataclass
class Link:
    onu: OnuRecord
    customer: CrmCustomer
    evidence: set[str] = field(default_factory=set)

    @property
    def network_backed(self) -> bool:
        return any(e in self.evidence for e in NETWORK_EVIDENCE)


@dataclass
class ReconResult:
    findings: list[Finding]
    links: list[Link]
    counts: dict[str, int]

    def by_kind(self) -> dict[Kind, list[Finding]]:
        grouped: dict[Kind, list[Finding]] = defaultdict(list)
        for finding in self.findings:
            grouped[finding.kind].append(finding)
        return dict(grouped)


def _onu_key(onu: OnuRecord) -> tuple[str, int | None, int | None]:
    return (onu.olt, onu.pon, onu.onu)


def _index[T](items: Iterable[T], key: Callable[[T], str | None]) -> dict[str, list[T]]:
    result: dict[str, list[T]] = defaultdict(list)
    for item in items:
        value = key(item)
        if value:
            result[value].append(item)
    return dict(result)


class _Recon:
    def __init__(self, data: ReconInput) -> None:
        self.data = data
        self.findings: list[Finding] = []
        self.secrets_by_name = _index(data.secrets, lambda s: s.name)
        self.secrets_by_loose = _index(data.secrets, lambda s: loose_key(s.name))
        self.sessions_by_name = _index(data.sessions, lambda s: s.name)
        self.sessions_by_mac = _index(data.sessions, lambda s: s.mac)
        self.customers_by_pppoe = _index(data.customers, lambda c: c.pppoe_user)
        self.customers_by_loose = _index(
            data.customers, lambda c: loose_key(c.pppoe_user) if c.pppoe_user else None
        )
        self.customers_by_serial = _index(data.customers, lambda c: c.onu_serial)
        self.onus_by_serial = _index(data.onus, lambda o: o.serial)
        self._typo_pairs: set[tuple[str, str]] = set()

    def add(
        self, kind: Kind, severity: Severity, detail: str, suggestion: str, **refs: object
    ) -> None:
        clean = {k: str(v) for k, v in refs.items() if v not in (None, "")}
        self.findings.append(Finding(kind, severity, detail, suggestion, clean))

    # --- Vínculos ONU ↔ cliente --------------------------------------------------

    def build_links(self) -> list[Link]:
        links: dict[tuple[tuple[str, int | None, int | None], str], Link] = {}

        def link(onu: OnuRecord, customer: CrmCustomer, evidence: str) -> None:
            key = (_onu_key(onu), customer.id)
            links.setdefault(key, Link(onu, customer)).evidence.add(evidence)

        for onu in self.data.onus:
            if onu.serial:
                for customer in self.customers_by_serial.get(onu.serial, []):
                    link(onu, customer, "crm")
            if onu.pppoe_user:
                for customer in self.customers_by_pppoe.get(onu.pppoe_user, []):
                    link(onu, customer, "wan")
            for mac in onu.macs:
                for session in self.sessions_by_mac.get(mac, []):
                    for customer in self.customers_by_pppoe.get(session.name, []):
                        link(onu, customer, "mac")
            if onu.description:
                tokens = set(re.split(r"[\s,;|/]+", onu.description))
                for token in tokens:
                    for customer in self.customers_by_pppoe.get(token, []):
                        link(onu, customer, "descripcion")
        return list(links.values())

    # --- Chequeos ------------------------------------------------------------------

    def check_duplicate_serials(self) -> None:
        for serial, onus in self.onus_by_serial.items():
            positions = {_onu_key(o) for o in onus}
            if len(positions) > 1:
                where = "; ".join(sorted(o.position for o in onus))
                self.add(
                    Kind.SERIAL_DUPLICADO,
                    Severity.ERROR,
                    f"El serial {serial} está autorizado en {len(positions)} posiciones: {where}.",
                    "Desautorizar la posición vieja: la ONU se movió sin borrarla donde estaba.",
                    serial=serial,
                )

    def check_onus(self, links: list[Link]) -> None:
        by_onu: dict[tuple[str, int | None, int | None], list[Link]] = defaultdict(list)
        for item in links:
            by_onu[_onu_key(item.onu)].append(item)
        for onu in self.data.onus:
            onu_links = by_onu.get(_onu_key(onu), [])
            strong = [x for x in onu_links if x.evidence - {"descripcion"}]
            if not strong:
                hint = ""
                weak = [x for x in onu_links if "descripcion" in x.evidence]
                if weak:
                    hint = f" Por la descripción podría ser de {weak[0].customer.label}."
                self.add(
                    Kind.ONU_SIN_CLIENTE,
                    Severity.WARNING,
                    f"La ONU {onu.serial or 's/n'} en {onu.position} no está ligada a ningún cliente.{hint}",
                    "Ligarla a su cliente o, si es un retiro o un equipo de prueba, desautorizarla.",
                    serial=onu.serial,
                    olt=onu.olt,
                    pon=onu.pon,
                    onu=onu.onu,
                )
            for item in onu_links:
                customer = item.customer
                if customer.status in CLOSED_STATES:
                    self.add(
                        Kind.BAJA_CON_ONU,
                        Severity.WARNING,
                        f"{customer.label} está {customer.status} y su ONU {onu.serial or ''} sigue"
                        f" autorizada en {onu.position}.",
                        "Desautorizar la ONU y agendar la recuperación del equipo.",
                        serial=onu.serial,
                        customer_id=customer.id,
                        olt=onu.olt,
                        pon=onu.pon,
                        onu=onu.onu,
                    )
            if onu.pppoe_user and onu.pppoe_user not in self.secrets_by_name:
                near = self._near_secret(onu.pppoe_user)
                if near:
                    self._typo(
                        onu.pppoe_user, near, f"la WAN de la ONU {onu.serial} ({onu.position})"
                    )
                else:
                    self.add(
                        Kind.ONU_PPPOE_SIN_SECRETO,
                        Severity.ERROR,
                        f"La ONU {onu.serial} ({onu.position}) marca con el usuario '{onu.pppoe_user}',"
                        " que no existe en ningún MikroTik.",
                        "Crear el secreto o corregir el usuario en la ONU: así nunca autentica.",
                        serial=onu.serial,
                        pppoe=onu.pppoe_user,
                    )

    def check_crossed_serials(self, links: list[Link]) -> None:
        for item in links:
            if not item.network_backed or not item.onu.serial:
                continue
            for owner in self.customers_by_serial.get(item.onu.serial, []):
                if owner.id != item.customer.id:
                    via = " y ".join(sorted(e for e in item.evidence if e in NETWORK_EVIDENCE))
                    self.add(
                        Kind.SERIAL_CRUZADO,
                        Severity.ERROR,
                        f"El CRM asigna la ONU {item.onu.serial} a {owner.label}, pero en la red"
                        f" (por {via}) la usa {item.customer.label}.",
                        "Revisar en sitio cuál es el equipo de cada cliente y corregir el CRM.",
                        serial=item.onu.serial,
                        customer_id=owner.id,
                        other_customer_id=item.customer.id,
                    )

    def check_customers(self, links: list[Link]) -> None:
        by_customer: dict[str, list[Link]] = defaultdict(list)
        for item in links:
            by_customer[item.customer.id].append(item)
        for customer in self.data.customers:
            if customer.status not in CUSTOMER_STATES or not customer.is_fiber:
                continue
            network = [x for x in by_customer.get(customer.id, []) if x.network_backed]
            if customer.onu_serial:
                if customer.onu_serial not in self.onus_by_serial:
                    other = f" En la red usa la ONU {network[0].onu.serial}." if network else ""
                    suggestion = (
                        f"Actualizar el serial en el CRM a {network[0].onu.serial}."
                        if network
                        else "Confirmar si la ONU se cambió o si falta autorizarla."
                    )
                    self.add(
                        Kind.CLIENTE_SIN_ONU,
                        Severity.WARNING,
                        f"{customer.label} tiene la ONU {customer.onu_serial} en el CRM y no está"
                        f" autorizada en ninguna OLT.{other}",
                        suggestion,
                        customer_id=customer.id,
                        serial=customer.onu_serial,
                    )
            elif network:
                onu = network[0].onu
                self.add(
                    Kind.SERIAL_POR_REGISTRAR,
                    Severity.INFO,
                    f"{customer.label} usa la ONU {onu.serial} ({onu.position}) y el CRM no tiene"
                    " su serial.",
                    f"Registrar el serial {onu.serial} en la ficha del cliente.",
                    customer_id=customer.id,
                    serial=onu.serial,
                )
            elif not by_customer.get(customer.id):
                self.add(
                    Kind.FIBRA_SIN_ONU,
                    Severity.WARNING,
                    f"{customer.label} es de fibra y no tiene ONU identificable: ni serial en el CRM"
                    " ni coincidencia por PPPoE o MAC.",
                    "Registrar el serial de la ONU en el CRM (o la descripción en la OLT).",
                    customer_id=customer.id,
                    pppoe=customer.pppoe_user,
                )

    def check_pppoe(self) -> None:
        crm_names = set(self.customers_by_pppoe)
        for name, secrets in self.secrets_by_name.items():
            if name in crm_names:
                continue
            near = self._near_customer(name)
            if near:
                self._typo(name, near, f"el MikroTik {secrets[0].router}")
                continue
            self.add(
                Kind.SECRETO_SIN_CLIENTE,
                Severity.WARNING,
                f"El secreto '{name}' existe en {', '.join(sorted({s.router for s in secrets}))}"
                " y no corresponde a ningún cliente del CRM.",
                "Borrarlo si es un cliente viejo, o crear el cliente si es una venta sin registrar.",
                pppoe=name,
                router=secrets[0].router,
            )
        for customer in self.data.customers:
            user = customer.pppoe_user
            if not user or customer.status not in ACTIVE_STATES:
                continue
            if user not in self.secrets_by_name:
                if self._near_secret(user):
                    continue  # ya reportado como error de digitación
                self.add(
                    Kind.CLIENTE_SIN_SECRETO,
                    Severity.WARNING,
                    f"{customer.label} está {customer.status} con el usuario '{user}' y no hay"
                    " secreto PPPoE con ese nombre.",
                    "Crear el secreto en el MikroTik (o corregir el usuario en el CRM).",
                    customer_id=customer.id,
                    pppoe=user,
                )

    def check_service_state(self) -> None:
        for customer in self.data.customers:
            name = customer.pppoe_user
            if not name or customer.status is None:
                continue
            secrets = self.secrets_by_name.get(name, [])
            sessions = self.sessions_by_name.get(name, [])
            if customer.status not in ACTIVE_STATES and sessions:
                self.add(
                    Kind.SUSPENDIDO_NAVEGANDO,
                    Severity.ERROR,
                    f"{customer.label} está {customer.status} en el CRM y tiene una sesión PPPoE"
                    f" activa en {sessions[0].router}.",
                    "Aplicar el corte en el MikroTik y revisar por qué no se aplicó.",
                    customer_id=customer.id,
                    pppoe=name,
                    router=sessions[0].router,
                )
            elif customer.status in ACTIVE_STATES and secrets and all(s.disabled for s in secrets):
                self.add(
                    Kind.ACTIVO_SIN_SERVICIO,
                    Severity.WARNING,
                    f"{customer.label} está {customer.status} en el CRM y su secreto '{name}' está"
                    " deshabilitado.",
                    "Habilitar el secreto o revisar si quedó un corte sin reconexión.",
                    customer_id=customer.id,
                    pppoe=name,
                )

    # --- Errores de digitación -----------------------------------------------------

    def _near_secret(self, name: str) -> str | None:
        candidates = [
            s.name for s in self.secrets_by_loose.get(loose_key(name), []) if s.name != name
        ]
        return candidates[0] if candidates else None

    def _near_customer(self, name: str) -> str | None:
        candidates = [
            c.pppoe_user
            for c in self.customers_by_loose.get(loose_key(name), [])
            if c.pppoe_user and c.pppoe_user != name
        ]
        return candidates[0] if candidates else None

    def _typo(self, found: str, expected: str, where: str) -> None:
        pair = (min(found, expected), max(found, expected))
        if pair in self._typo_pairs:
            return
        self._typo_pairs.add(pair)
        self.add(
            Kind.PPPOE_DIGITACION,
            Severity.ERROR,
            f"En {where} aparece '{found}' y el valor parecido es '{expected}'. Difieren en"
            " espacios, mayúsculas, tildes o caracteres invisibles: así el PPPoE no autentica.",
            f"Dejar el mismo usuario exacto en el CRM, el MikroTik y la ONU ('{expected}' o"
            f" '{found}', el que sea el correcto).",
            pppoe=found,
            expected=expected,
        )


def _count(data: ReconInput, findings: list[Finding]) -> dict[str, int]:
    counts = {
        "onus": len(data.onus),
        "secretos": len(data.secrets),
        "sesiones": len(data.sessions),
        "clientes": len(data.customers),
        "hallazgos": len(findings),
    }
    for severity in Severity:
        counts[severity.value] = sum(1 for f in findings if f.severity is severity)
    return counts


def reconcile(data: ReconInput) -> ReconResult:
    recon = _Recon(data)
    links = recon.build_links()
    recon.check_duplicate_serials()
    recon.check_onus(links)
    recon.check_crossed_serials(links)
    recon.check_customers(links)
    recon.check_pppoe()
    recon.check_service_state()
    findings = sorted(recon.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.kind.value))
    return ReconResult(findings=findings, links=links, counts=_count(data, findings))
