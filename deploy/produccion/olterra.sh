#!/usr/bin/env bash
# Olterra en producción. Se corre en el servidor, desde deploy/produccion/.
#
#   ./olterra.sh instalar <dominio>           primera vez: claves nuevas en .env, base,
#                                             migraciones y todo arriba
#   ./olterra.sh actualizar                   respaldo, imágenes nuevas, migraciones, reinicio
#   ./olterra.sh dominio <dominio>            cambia el dominio (Caddy pide su certificado)
#   ./olterra.sh isp <slug> "<Nombre>"        crea un ISP y su primera llave de API
#   ./olterra.sh llave <slug> <nombre>        otra llave de API para un ISP
#   ./olterra.sh respaldo                     un respaldo ahora (además del diario)
#   ./olterra.sh probar-respaldo              restaura el último respaldo aparte y lo revisa
#   ./olterra.sh estado                       contenedores y /health
#   ./olterra.sh registros [servicio]         últimos registros
#
# Con OLTERRA_CONSTRUIR=1 (al instalar; queda en .env) las imágenes se construyen aquí desde
# el repo en vez de bajarlas de GHCR, y `actualizar` trae el código nuevo con git pull.
set -euo pipefail
cd "$(dirname "$0")"

SERVICIOS=(api executor web backup)
REQUERIDAS=(
    OLTERRA_DOMINIO POSTGRES_PASSWORD OLTERRA_OWNER_PASSWORD OLTERRA_APP_PASSWORD
    OLTERRA_MASTER_KEY OLTERRA_EXECUTOR_PRIVATE_KEY OLTERRA_EXECUTOR_PUBLIC_KEY
)

die() {
    echo "Error: $*" >&2
    exit 1
}

compose() { docker compose "$@"; }

requisitos() {
    command -v docker >/dev/null || die "falta Docker (ver docs/DESPLIEGUE.md)"
    docker compose version >/dev/null 2>&1 || die "falta el plugin docker compose"
    command -v openssl >/dev/null || die "falta openssl"
}

leer() { sed -n "s/^$1=//p" .env | tail -n 1; }

validar_dominio() {
    [[ "$1" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "dominio inválido: $1"
}

# Pone el valor de una variable en .env. Los valores son hex o base64: nunca | ni & ni \.
poner() {
    local nombre="$1" valor="$2"
    [ -n "$valor" ] || die "no se pudo generar $nombre"
    case "$valor" in *[\|\&\\[:space:]]*) die "valor inesperado para $nombre" ;; esac
    sed -i "s|^$nombre=.*|$nombre=$valor|" .env
}

verificar_env() {
    [ -f .env ] || die "no hay .env: primero ./olterra.sh instalar <dominio>"
    local falta=() nombre
    for nombre in "${REQUERIDAS[@]}"; do
        [ -n "$(leer "$nombre")" ] || falta+=("$nombre")
    done
    [ ${#falta[@]} -eq 0 ] || die "faltan en .env: ${falta[*]}"
}

construye_aqui() { [ "${OLTERRA_CONSTRUIR:-$(leer OLTERRA_CONSTRUIR)}" = "1" ]; }

imagenes() {
    if construye_aqui; then
        # Una a la vez: en un droplet chico las dos juntas no caben en memoria.
        compose build api
        compose build web
    else
        compose pull --quiet
    fi
}

arrancar() {
    compose up -d --wait db nats
    compose run --rm migrate
    compose up -d --wait --remove-orphans "${SERVICIOS[@]}"
}

instalar() {
    local dominio="${1:-}"
    [ -n "$dominio" ] || die "uso: ./olterra.sh instalar <dominio>"
    validar_dominio "$dominio"
    [ ! -e .env ] || die ".env ya existe y no se pisan claves. Para actualizar: ./olterra.sh actualizar"
    requisitos

    umask 077
    cat >.env <<EOF
# Olterra en producción. Generado por ./olterra.sh instalar el $(date -u +%F).
# Tiene TODAS las claves: no va a git ni se comparte. Guárdalo también en un gestor de
# claves: sin OLTERRA_MASTER_KEY las credenciales guardadas no se pueden leer.
OLTERRA_DOMINIO=$dominio
OLTERRA_VERSION=${OLTERRA_VERSION:-main}
OLTERRA_CONSTRUIR=${OLTERRA_CONSTRUIR:-0}
POSTGRES_PASSWORD=$(openssl rand -hex 24)
OLTERRA_OWNER_PASSWORD=$(openssl rand -hex 24)
OLTERRA_APP_PASSWORD=$(openssl rand -hex 24)
OLTERRA_MASTER_KEY=
OLTERRA_EXECUTOR_PRIVATE_KEY=
OLTERRA_EXECUTOR_PUBLIC_KEY=
OLTERRA_RESPALDO_DIAS=14

# Concentrador WireGuard (CHR). Completar cuando esté listo y correr ./olterra.sh actualizar.
OLTERRA_TUNNEL_HUB_HOST=
OLTERRA_TUNNEL_HUB_PORT=13231
OLTERRA_TUNNEL_HUB_PUBLIC_KEY=
# SSTP para RouterOS v6: la CA del concentrador en una línea (docs/DESPLIEGUE.md, Túnel).
OLTERRA_TUNNEL_SSTP_PORT=4443
OLTERRA_TUNNEL_SSTP_CA=
EOF

    echo "Imágenes…"
    imagenes
    echo "Llaves (llave maestra de la bóveda y par del ejecutor)…"
    local maestra llaves
    maestra=$(compose run --rm --no-deps -T migrate olterra-admin generar-llave-maestra)
    llaves=$(compose run --rm --no-deps -T migrate olterra-admin generar-llaves-ejecutor)
    poner OLTERRA_MASTER_KEY "$maestra"
    poner OLTERRA_EXECUTOR_PRIVATE_KEY \
        "$(sed -n 's/^OLTERRA_EXECUTOR_PRIVATE_KEY=\([^ ]*\).*/\1/p' <<<"$llaves")"
    poner OLTERRA_EXECUTOR_PUBLIC_KEY \
        "$(sed -n 's/^OLTERRA_EXECUTOR_PUBLIC_KEY=\([^ ]*\).*/\1/p' <<<"$llaves")"
    chmod 600 .env
    verificar_env

    echo "Base, migraciones y servicios…"
    arrancar
    cat <<EOF

Olterra quedó arriba en https://$dominio (cuando el DNS apunte a este servidor).

Siguiente:
  1. Crear el primer ISP y su llave:   ./olterra.sh isp mi-isp "Mi ISP"
  2. Guardar .env en un gestor de claves (sin la llave maestra no se leen las credenciales).
  3. Conectar el túnel hacia las OLT:  docs/DESPLIEGUE.md, sección "Túnel".
EOF
}

actualizar() {
    requisitos
    verificar_env
    if compose ps --status running --services 2>/dev/null | grep -qx backup; then
        echo "Respaldo antes de actualizar…"
        respaldo
    fi
    if construye_aqui; then
        # Solo si el repo está en una rama que sigue a una remota; si no (un commit suelto,
        # como en CI), se construye el código tal como está.
        if git -C ../.. rev-parse -q --verify '@{upstream}' >/dev/null 2>&1; then
            echo "Código nuevo…"
            git -C ../.. pull --ff-only
        else
            echo "Sin rama remota que seguir: se construye el código tal como está."
        fi
    fi
    imagenes
    arrancar
    docker image prune -f >/dev/null
    estado
}

dominio() {
    local nuevo="${1:-}"
    [ -n "$nuevo" ] || die "uso: ./olterra.sh dominio <dominio>"
    validar_dominio "$nuevo"
    verificar_env
    poner OLTERRA_DOMINIO "$nuevo"
    compose up -d --wait web
    echo "Listo: https://$nuevo (el DNS tiene que apuntar a este servidor; Caddy ya pidió el certificado)."
}

isp() {
    local slug="${1:-}" nombre="${2:-}"
    if [ -z "$slug" ] || [ -z "$nombre" ]; then
        die 'uso: ./olterra.sh isp <slug> "<Nombre>"'
    fi
    [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{1,31}$ ]] ||
        die "el slug va en minúsculas, números y guiones (2 a 32): $slug"
    verificar_env
    compose run --rm -T migrate olterra-admin crear-tenant --slug "$slug" --nombre "$nombre"
    llave "$slug" principal
}

llave() {
    local slug="${1:-}" nombre="${2:-}"
    if [ -z "$slug" ] || [ -z "$nombre" ]; then
        die "uso: ./olterra.sh llave <slug> <nombre>"
    fi
    verificar_env
    compose run --rm -T migrate olterra-admin crear-llave --tenant "$slug" --nombre "$nombre"
}

respaldo() {
    compose exec -T backup sh /respaldo.sh
}

probar_respaldo() {
    verificar_env
    local ultimo
    # shellcheck disable=SC2012  # nombres con fecha, sin espacios
    ultimo=$(ls -1t respaldos/olterra-*.dump 2>/dev/null | head -n 1 || true)
    [ -n "$ultimo" ] || die "no hay respaldos en respaldos/ (./olterra.sh respaldo)"
    echo "Restaurando $(basename "$ultimo") en una base aparte (olterra_prueba)…"
    # shellcheck disable=SC2016  # las variables se expanden dentro del contenedor
    compose exec -T db sh -eu -c '
        dropdb -U postgres --if-exists olterra_prueba
        createdb -U postgres olterra_prueba
        pg_restore -U postgres --no-owner --exit-on-error -d olterra_prueba "/respaldos/$1"
        isps=$(psql -U postgres -d olterra_prueba -tAc "SELECT count(*) FROM tenants")
        migracion=$(psql -U postgres -d olterra_prueba -tAc "SELECT version_num FROM alembic_version")
        dropdb -U postgres olterra_prueba
        echo "El respaldo se restaura bien: $isps ISP, migración $migracion."
    ' sh "$(basename "$ultimo")"
}

estado() {
    compose ps
    echo
    compose exec -T api python -c \
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())" ||
        echo "La API no responde"
}

accion="${1:-}"
[ $# -gt 0 ] && shift
case "$accion" in
instalar) instalar "$@" ;;
actualizar) actualizar ;;
dominio) dominio "$@" ;;
isp) isp "$@" ;;
llave) llave "$@" ;;
respaldo) respaldo ;;
probar-respaldo) probar_respaldo ;;
estado) estado ;;
registros) compose logs --tail 200 "$@" ;;
*)
    sed -n '2,16p' "$0"
    exit 1
    ;;
esac
