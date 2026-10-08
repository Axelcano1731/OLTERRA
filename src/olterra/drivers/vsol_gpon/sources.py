"""De dónde sale cada dato del driver. Ningún comando está verificado en laboratorio todavía."""

MANUAL_V21 = "Manual GPON OLT CLI v2.1 de VSOL (2021-04-25)"
LIBRENMS_19368 = "LibreNMS PR #19368 (sin fusionar; probado en V1600GS fw V1.2.0/V4.0.0)"
LIBRENMS_19850 = "LibreNMS PR #19850 (sin fusionar; snmprec de V1600G1B fw V1.4.4R)"
LAB_G0B = (
    "Laboratorio 2026-10-03: V1600G0-B V1.4.8R (ayuda '?' de la propia OLT y captura en "
    "tests/fixtures/vsol-gpon/V1600G0-B)"
)
LIBRENMS_MAIN = "LibreNMS main, definición vsolution (V1600D EPON)"
PUBLIC_GUIDES = "Guías públicas (technicalafnan.com, yusufmiahbd.blogspot.com)"
RUNNING_G0B = (
    "Configuración guardada de ONU reales en la V1600G0-B V1.4.8R ('show running-config onu N', "
    "2026-10-03): la OLT la escribe con la misma sintaxis con que se teclea; falta ejecutarla"
)
HELP_G0B = (
    "Ayuda '?' de la V1600G0-B V1.4.8R (2026-10-08, "
    "tests/fixtures/vsol-gpon/V1600G0-B/V1.4.8R/20261008/ayuda): sintaxis completa, falta ejecutarla"
)
INFERRED = "Inferido del patrón 'no onu …' del manual; confirmar en laboratorio"


def manual(section: str) -> str:
    return f"{MANUAL_V21} §{section}"
