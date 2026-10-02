"""De dónde sale cada dato del driver. Ningún comando está verificado en laboratorio todavía."""

MANUAL_V21 = "Manual GPON OLT CLI v2.1 de VSOL (2021-04-25)"
LIBRENMS_19368 = "LibreNMS PR #19368 (sin fusionar; probado en V1600GS fw V1.2.0/V4.0.0)"
LIBRENMS_19850 = "LibreNMS PR #19850 (sin fusionar; snmprec de V1600G1B fw V1.4.4R)"
LIBRENMS_MAIN = "LibreNMS main, definición vsolution (V1600D EPON)"
PUBLIC_GUIDES = "Guías públicas (technicalafnan.com, yusufmiahbd.blogspot.com)"
INFERRED = "Inferido del patrón 'no onu …' del manual; confirmar en laboratorio"


def manual(section: str) -> str:
    return f"{MANUAL_V21} §{section}"
