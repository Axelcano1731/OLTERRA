"""Clientes desde la API de socios de ISPWatch (``/api/v1/partner``, solo lectura).

Se recorre ``/customers`` completo con el cursor ``after_id``, como indica el
contrato. La API de socios no expone seriales de ONU por diseño, así que con
ISPWatch el vínculo ONU ↔ cliente sale de la red (PPPoE de la ONU, MAC) o de la
descripción en la OLT; el hallazgo ``serial_por_registrar`` lo deja anotado.
"""

from __future__ import annotations

from typing import Any

import httpx

from olterra.reconciliation.model import CrmCustomer, normalize_status
from olterra.reconciliation.sources import SourceError


def customer_from_partner(item: dict[str, Any]) -> CrmCustomer:
    name = " ".join(str(x) for x in (item.get("name"), item.get("last_name")) if x)
    is_fiber = item.get("is_fiber")
    return CrmCustomer(
        id=str(item["id"]),
        name=name or None,
        status=normalize_status(item.get("service_status")),
        pppoe_user=item.get("pppoe_username") or None,
        onu_serial=None,
        access=None if is_fiber is None else ("fiber" if is_fiber else "wireless"),
        nap=item.get("sectorial") or None,
        router=item.get("router") or None,
    )


def fetch_customers(
    base_url: str,
    token: str,
    *,
    per_page: int = 100,
    client: httpx.Client | None = None,
    max_pages: int = 10_000,
) -> list[CrmCustomer]:
    if not base_url.startswith("https://") and not base_url.startswith("http://localhost"):
        raise SourceError("La API de ISPWatch solo se consulta por HTTPS")
    owns_client = client is None
    http = client or httpx.Client(timeout=30)
    url = base_url.rstrip("/") + "/api/v1/partner/customers"
    customers: list[CrmCustomer] = []
    after_id = 0
    try:
        for _ in range(max_pages):
            response = http.get(
                url,
                params={"after_id": after_id, "per_page": per_page},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if response.status_code in (401, 403):
                raise SourceError("ISPWatch rechazó la llave (revise el permiso read:customers)")
            response.raise_for_status()
            payload = response.json()
            customers.extend(customer_from_partner(item) for item in payload.get("data", []))
            meta = payload.get("meta", {})
            if not meta.get("has_more"):
                break
            next_after = int(meta.get("next_after_id", after_id))
            if next_after == after_id:
                break
            after_id = next_after
    except httpx.HTTPError as exc:
        raise SourceError(f"No se pudo leer ISPWatch: {exc}") from exc
    finally:
        if owns_client:
            http.close()
    return customers
