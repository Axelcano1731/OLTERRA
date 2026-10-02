"""Credenciales del túnel SSTP (RouterOS v6): usuario PPP, clave y la CA de Olterra.

Como con WireGuard, la plataforma genera la clave del router, la entrega en el script una
vez y NO la guarda: queda solo en el router y en el secreto PPP del concentrador. Si se
necesita el script otra vez, se rota la clave.

La CA es la del concentrador (la crea ``olterra-admin concentrador`` en el CHR). Es pública:
va dentro del script para que el router verifique que habla con el concentrador real.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import string

from cryptography import x509

_USER = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_PASSWORD = re.compile(r"^[A-Za-z0-9]{24,64}$")
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+=*$")
_PEM_MARKS = re.compile(r"-----(?:BEGIN|END) CERTIFICATE-----")

# RouterOS v6 no deja escribir más de 4096 bytes en un archivo desde un script.
MAX_CA_PEM = 4000


class InvalidSstp(ValueError):
    pass


def ppp_user(tenant: str, router: str) -> str:
    """Usuario PPP del router: único entre ISP porque lleva el tenant."""
    return validate_user(f"olterra-{tenant}-{router}")


def validate_user(user: str) -> str:
    if not _USER.fullmatch(user):
        raise InvalidSstp("El usuario SSTP solo admite letras, números, '.', '-' y '_'")
    return user


def generate_password() -> str:
    """32 letras y números: sin símbolos que RouterOS tenga que escapar."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


def validate_password(password: str) -> str:
    if not _PASSWORD.fullmatch(password):
        raise InvalidSstp("La clave SSTP debe tener de 24 a 64 letras o números")
    return password


def normalize_ca(value: str) -> str:
    """CA en PEM, o solo su base64 en una línea (como cabe en .env), a PEM de 64 columnas."""
    body = "".join(_PEM_MARKS.sub("", value).split())
    if not body or not _BASE64.fullmatch(body):
        raise InvalidSstp("El certificado de la CA no es un PEM válido")
    try:
        der = base64.b64decode(body, validate=True)
        cert = x509.load_der_x509_certificate(der)
    except (binascii.Error, ValueError) as exc:
        raise InvalidSstp("El certificado de la CA no es un PEM válido") from exc
    if not _is_ca(cert):
        raise InvalidSstp("El certificado no es de una CA (no puede firmar certificados)")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"
    if len(pem) > MAX_CA_PEM:
        raise InvalidSstp("El certificado de la CA es demasiado grande para RouterOS v6")
    return pem


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return True
    except x509.ExtensionNotFound:
        pass
    try:
        return bool(cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign)
    except x509.ExtensionNotFound:
        return False
