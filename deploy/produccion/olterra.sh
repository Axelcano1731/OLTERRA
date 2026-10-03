#!/usr/bin/env bash
# Olterra en producción. Se corre en el servidor, desde deploy/produccion/.
#
#   ./olterra.sh instalar <dominio>           primera vez: claves nuevas en .env, base,
#                                             migraciones y todo arriba
#   ./olterra.sh actualizar                   respaldo, imágenes nuevas, migraciones, reinicio
#   ./olterra.sh dominio <dominio>            cambia el dominio (Caddy pide su certificado)
#   ./olterra.sh rama <rama>                  el servidor pasa a seguir esa rama (main) y actualiza
#   ./olterra.sh auto activar|desactivar|estado   actualización automática desde main (cada 5 min)
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

# Estado de las actualizaciones (candado, qué commit corre, el que falló, historial). Está
# fuera del repo y lo comparte la actualización automática (auto-actualizar.sh).
dir_estado() {
    local dir="${OLTERRA_AUTO_ESTADO:-/var/lib/olterra-deploy}"
    mkdir -p "$dir" 2>/dev/null || dir="${TMPDIR:-/tmp}/olterra-deploy-$(id -u)"
    mkdir -p "$dir"
    echo "$dir"
}

# Una sola actualización a la vez. La automática ya tiene el candado y lo avisa por entorno.
bloquear() {
    if [ "${OLTERRA_BLOQUEO:-0}" = "1" ] || ! command -v flock >/dev/null; then return 0; fi
    exec 9>"$(dir_estado)/lock"
    flock -n 9 || die "hay otra actualización en curso (la automática corre cada 5 minutos)"
}

# Anota qué commit quedó corriendo: así la automática no repite lo que ya hiciste a mano.
registrar_desplegado() {
    local dir sha
    dir=$(dir_estado)
    sha=$(git -C ../.. rev-parse HEAD 2>/dev/null) || return 0
    printf '%s\n' "$sha" >"$dir/desplegado"
    rm -f "$dir/fallida"
    printf '%s %s %s ok\n' "$(date -u +%FT%TZ)" "${sha:0:7}" "${OLTERRA_ORIGEN:-manual}" >>"$dir/historial"
    tail -n 50 "$dir/historial" >"$dir/historial.tmp"
    mv "$dir/historial.tmp" "$dir/historial"
}

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
    bloquear
    if compose ps --status running --services 2>/dev/null | grep -qx backup; then
        echo "Respaldo antes de actualizar…"
        respaldo
    fi
    if construye_aqui; then
        # Solo si el repo está en una rama que sigue a una remota; si no (un commit suelto,
        # como en CI), se construye el código tal como está. La actualización automática ya
        # trajo el commit que verificó (OLTERRA_SIN_PULL).
        if [ "${OLTERRA_SIN_PULL:-0}" = "1" ]; then
            echo "Código ya traído: se construye tal como está."
        elif git -C ../.. rev-parse -q --verify '@{upstream}' >/dev/null 2>&1; then
            echo "Código nuevo…"
            git -C ../.. pull --ff-only
        else
            echo "Sin rama remota que seguir: se construye el código tal como está."
        fi
    fi
    imagenes
    arrancar
    docker image prune -f >/dev/null
    registrar_desplegado
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

# Pasa el servidor a seguir otra rama del repo (por ejemplo main) y actualiza.
rama() {
    local nombre="${1:-}"
    if [ -z "$nombre" ]; then die "uso: ./olterra.sh rama <rama>"; fi
    [[ "$nombre" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$ ]] || die "nombre de rama inválido: $nombre"
    git -C ../.. rev-parse --git-dir >/dev/null 2>&1 || die "esto no es un repositorio git"
    if [ -n "$(git -C ../.. status --porcelain --untracked-files=no)" ]; then
        die "hay cambios sin guardar en el repo del servidor: no cambio de rama"
    fi
    git -C ../.. fetch -q origin "$nombre" || die "no existe la rama $nombre en origin"
    git -C ../.. cat-file -e "origin/$nombre:deploy/produccion/olterra.sh" 2>/dev/null ||
        die "la rama $nombre no trae deploy/produccion/olterra.sh"
    git -C ../.. checkout -q -B "$nombre" --track "origin/$nombre"
    echo "El servidor sigue ahora la rama $nombre ($(git -C ../.. log --oneline -1))."
    # exec: el script cambió con el checkout y bash no debe seguir leyéndolo.
    exec ./olterra.sh actualizar
}

AUTO_UNIDAD=olterra-auto

auto() {
    case "${1:-}" in
    activar) auto_activar ;;
    desactivar) auto_desactivar ;;
    estado) auto_estado ;;
    *) die "uso: ./olterra.sh auto activar|desactivar|estado" ;;
    esac
}

auto_activar() {
    local aqui rama_actual dir
    [ "$(id -u)" = "0" ] || die "activar la actualización automática pide root (sudo)"
    command -v systemctl >/dev/null || die "falta systemd"
    command -v flock >/dev/null || die "falta flock (paquete util-linux)"
    command -v python3 >/dev/null || die "falta python3"
    verificar_env
    aqui=$(pwd)
    case "$aqui" in *[[:space:]]*) die "la ruta $aqui tiene espacios: no se puede crear la unidad de systemd" ;; esac
    chmod +x "$aqui/auto-actualizar.sh"
    dir=$(dir_estado)
    # Se asume que lo que corre hoy es lo que está en el repo.
    if [ ! -f "$dir/desplegado" ]; then git -C ../.. rev-parse HEAD >"$dir/desplegado"; fi

    cat >"/etc/systemd/system/$AUTO_UNIDAD.service" <<EOF
[Unit]
Description=Olterra: actualización automática desde main
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$aqui/auto-actualizar.sh
TimeoutStartSec=45min
Nice=10
EOF
    cat >"/etc/systemd/system/$AUTO_UNIDAD.timer" <<EOF
[Unit]
Description=Olterra: mira main cada 5 minutos

[Timer]
OnBootSec=3min
OnUnitInactiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now "$AUTO_UNIDAD.timer" >/dev/null
    rama_actual=$(git -C ../.. branch --show-current)
    echo "Actualización automática activa: cada 5 minutos mira main y, con el CI en verde, se actualiza."
    if [ "$rama_actual" != "main" ]; then
        echo "Ojo: el servidor sigue '${rama_actual:-(commit suelto)}' y la automática solo actualiza main." >&2
        echo "Para seguir main: ./olterra.sh rama main" >&2
    fi
    echo "Para verla: ./olterra.sh auto estado"
}

auto_desactivar() {
    [ "$(id -u)" = "0" ] || die "desactivar la actualización automática pide root (sudo)"
    systemctl disable --now "$AUTO_UNIDAD.timer" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$AUTO_UNIDAD.service" "/etc/systemd/system/$AUTO_UNIDAD.timer"
    systemctl daemon-reload
    echo "Actualización automática desactivada. Se sigue con ./olterra.sh actualizar a mano."
}

auto_estado() {
    local dir
    dir=$(dir_estado)
    echo "Temporizador: $(systemctl is-active "$AUTO_UNIDAD.timer" 2>/dev/null || true)"
    systemctl list-timers "$AUTO_UNIDAD.timer" --no-pager --no-legend 2>/dev/null || true
    echo "Rama del servidor: $(git -C ../.. branch --show-current) · commit del repo: $(git -C ../.. log --oneline -1)"
    if [ -f "$dir/desplegado" ]; then echo "Corriendo (último desplegado): $(cut -c1-7 "$dir/desplegado")"; fi
    if [ -f "$dir/fallida" ]; then echo "Último que falló: $(cut -c1-7 "$dir/fallida") (no se reintenta hasta que llegue otro)"; fi
    if [ -f "$dir/historial" ]; then
        echo "Historial:"
        tail -n 10 "$dir/historial" | sed 's/^/  /'
    fi
    echo "Último registro:"
    journalctl -u "$AUTO_UNIDAD.service" -n 6 --no-pager 2>/dev/null | sed 's/^/  /' || true
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

despachar() {
    local accion="${1:-}"
    if [ $# -gt 0 ]; then shift; fi
    case "$accion" in
    instalar) instalar "$@" ;;
    actualizar) actualizar ;;
    dominio) dominio "$@" ;;
    rama) rama "$@" ;;
    auto) auto "$@" ;;
    isp) isp "$@" ;;
    llave) llave "$@" ;;
    respaldo) respaldo ;;
    probar-respaldo) probar_respaldo ;;
    estado) estado ;;
    registros) compose logs --tail 200 "$@" ;;
    *)
        sed -n '2,18p' "$0"
        exit 1
        ;;
    esac
}

# En una sola línea, a propósito (ver arriba).
despachar "$@"; exit $?
