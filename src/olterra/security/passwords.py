"""Contraseñas de usuarios: scrypt (biblioteca estándar) con sal por usuario.

Formato guardado: ``scrypt$<n>$<r>$<p>$<sal b64>$<hash b64>``. Llevar los parámetros en el
texto permite subirlos después sin invalidar las contraseñas que ya existen
(``needs_rehash`` dice cuándo volver a calcular una al entrar).

Las llaves de API usan SHA-256 porque su secreto tiene 256 bits al azar; una contraseña la
escribe una persona y necesita un hash lento.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**15, 8, 1
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024

# Contraseñas que no se aceptan como nuevas (las primeras de cualquier diccionario de ataque).
_COMMON = {
    "123456",
    "1234567",
    "12345678",
    "123456789",
    "1234567890",
    "password",
    "contraseña",
    "contrasena",
    "qwerty",
    "admin",
    "admin123",
    "olterra",
}
MIN_LENGTH = 10


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_DKLEN
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, N, R, P)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    try:
        scheme, n, r, p, *_ = stored.split("$")
        return scheme != "scrypt" or (int(n), int(r), int(p)) != (N, R, P)
    except ValueError:
        return True


# Para que un usuario que no existe tarde lo mismo que uno con contraseña equivocada.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str, username: str) -> str | None:
    """Por qué una contraseña nueva no sirve, o None si sirve."""
    if len(password) < MIN_LENGTH:
        return f"La contraseña nueva necesita al menos {MIN_LENGTH} caracteres"
    if len(password) > 128:
        return "La contraseña nueva es demasiado larga (máximo 128)"
    if password.lower() in _COMMON or password.lower() == username.lower():
        return "Esa contraseña es de las primeras que prueba un atacante: elige otra"
    if len(set(password)) < 4:
        return "La contraseña nueva repite demasiado los mismos caracteres"
    return None
