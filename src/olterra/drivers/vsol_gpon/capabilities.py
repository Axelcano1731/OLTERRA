"""Matriz de capacidades por modelo y firmware de VSOL GPON.

Hallazgo de la fase 0 (fuentes públicas, sin laboratorio todavía): el monitoreo
por SNMP de cada ONU NO es igual en todos los modelos.

- V1600GS: expone tablas por ONU (estado, serial, potencias, distancia) bajo
  ``.37950.1.1.6`` y además una interfaz por ONU en IF-MIB (``GPON01ONU1``).
- V1600G1B con firmware V1.4.4R: el subárbol por ONU no existe. Las potencias
  por ONU solo salen por CLI (``show pon onu all rx-power``), que cuesta CPU a
  la OLT y obliga a espaciar más el sondeo.

Esta matriz es el punto de partida. El alta de cada OLT (fase 1) debe sondear las
tablas y guardar lo que de verdad responde esa OLT.
"""

from __future__ import annotations

from olterra.drivers.base import Capability as Cap
from olterra.drivers.base import CapabilityFact, CapabilityRule, Support
from olterra.drivers.vsol_gpon.sources import LIBRENMS_19368, LIBRENMS_19850, manual


def _facts(support: Support, source: str, *caps: Cap) -> dict[Cap, CapabilityFact]:
    return {cap: CapabilityFact(support, source) for cap in caps}


YES, NO, UNKNOWN = Support.YES, Support.NO, Support.UNKNOWN

RULES = [
    CapabilityRule(
        "*",
        "*",
        {
            Cap.CLI_AUTOFIND: CapabilityFact(YES, manual("19.2.1")),
            Cap.CLI_AUTHORIZE: CapabilityFact(YES, manual("19.2.6")),
            Cap.CLI_ONU_OPTICAL: CapabilityFact(YES, manual("18.2.3") + " y §19.3.1"),
            Cap.ONU_WAN_VIA_OLT: CapabilityFact(
                UNKNOWN,
                manual("19.4.8") + ": el manual configura la WAN en la web de la ONU; "
                "el protocolo privado de VSOL se confirma en laboratorio",
            ),
            Cap.ONU_WIFI_VIA_OLT: CapabilityFact(
                UNKNOWN, "No aparece en el manual v2.1; si no se puede, GenieACS se adelanta"
            ),
        },
    ),
    CapabilityRule(
        "V1600GS*",
        "*",
        {
            **_facts(
                YES,
                LIBRENMS_19368,
                Cap.SNMP_ONU_STATUS,
                Cap.SNMP_ONU_SERIAL,
                Cap.SNMP_ONU_OPTICAL,
                Cap.SNMP_ONU_DISTANCE,
                Cap.SNMP_ONU_TRAFFIC,
                Cap.SNMP_PON_OPTICAL,
            ),
            Cap.SSH_LANDS_IN_SHELL: CapabilityFact(
                UNKNOWN, LIBRENMS_19368 + ": su guía entra por SSH y usa 'vtysh -c'"
            ),
        },
    ),
    CapabilityRule(
        "V1600G1B*",
        "*V1.4.4*",
        {
            **_facts(
                NO,
                LIBRENMS_19850 + ": sin filas en .37950.1.1.6",
                Cap.SNMP_ONU_STATUS,
                Cap.SNMP_ONU_SERIAL,
                Cap.SNMP_ONU_OPTICAL,
                Cap.SNMP_ONU_DISTANCE,
            ),
            Cap.SNMP_PON_OPTICAL: CapabilityFact(
                UNKNOWN,
                "PR #19850 lee potencias por PON en .37950.1.1.5.10.13.7.1; PR #19368 "
                "identifica esa tabla como la de los SFP de uplink",
            ),
        },
    ),
]
