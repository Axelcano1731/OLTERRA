#!/usr/bin/env bash
# Actualización automática: el servidor sigue la rama main del repositorio.
#
# La corre un temporizador de systemd cada 5 minutos (./olterra.sh auto activar). No hace
# falta ningún secreto en GitHub ni abrir puertos: el servidor mira el repo (público) y se
# actualiza solo.
#
#   1. Trae main. Si no hay un commit nuevo, no hace nada.
#   2. Espera a que el CI de ese commit termine en verde (pruebas, interfaz, imagen y
#      despliegue). Pendiente: reintenta en el siguiente turno. En rojo: no lo despliega.
#   3. Trae el código (solo avanza, nunca reescribe historia) y corre `./olterra.sh
#      actualizar`: respaldo, imágenes, migraciones y reinicio. Si falla, los contenedores
#      que ya corrían siguen corriendo.
#   4. Un commit que falló no se reintenta; el siguiente sí. Se ve con `./olterra.sh auto estado`.
#
# Variables (todas opcionales):
#   OLTERRA_AUTO_RAMA        rama que sigue (main)
#   OLTERRA_CI_REQUERIDOS    jobs del CI que tienen que estar en verde; vacío = no esperar al CI
#   OLTERRA_AUTO_ESTADO      dónde guarda su estado (/var/lib/olterra-deploy)
#   OLTERRA_AUTO_SIMULAR=1   decide y avisa, pero no despliega
#   OLTERRA_CI_JSON          archivo con la respuesta de GitHub, en vez de consultarla (pruebas)
set -euo pipefail

# Estado fuera del repo: candado, qué commit corre, el que falló y el historial.
dir_estado() {
    local dir="${OLTERRA_AUTO_ESTADO:-/var/lib/olterra-deploy}"
    mkdir -p "$dir" 2>/dev/null || dir="${TMPDIR:-/tmp}/olterra-deploy-$(id -u)"
    mkdir -p "$dir"
    echo "$dir"
}

# Dice algo una sola vez: el temporizador corre cada 5 minutos y no hay que repetirlo 288 veces.
decir() {
    local ultimo=""
    if [ -f "$ESTADO/aviso" ]; then ultimo=$(cat "$ESTADO/aviso"); fi
    if [ "$ultimo" != "$1" ]; then
        echo "$1"
        printf '%s' "$1" >"$ESTADO/aviso"
    fi
}

historial() {
    printf '%s %s auto %s\n' "$(date -u +%FT%TZ)" "${1:0:7}" "$2" >>"$ESTADO/historial"
    tail -n 50 "$ESTADO/historial" >"$ESTADO/historial.tmp"
    mv "$ESTADO/historial.tmp" "$ESTADO/historial"
}

repo_github() {
    local url
    url=$(git -C "$REPO" remote get-url origin 2>/dev/null || true)
    url=${url#https://github.com/}
    url=${url#git@github.com:}
    url=${url%.git}
    url=${url%/}
    if [[ "$url" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then echo "$url"; fi
}

# ok | pendiente: <jobs> | fallida: <jobs> | sin-api: <motivo>
estado_ci() {
    local sha="$1" json gh
    if [ -n "${OLTERRA_CI_JSON:-}" ]; then
        json=$(cat "$OLTERRA_CI_JSON")
    else
        gh=$(repo_github)
        if [ -z "$gh" ]; then
            echo "sin-api: el origen no es un repositorio de GitHub"
            return
        fi
        json=$(curl -fsS -m 20 -H "Accept: application/vnd.github+json" \
            "https://api.github.com/repos/$gh/commits/$sha/check-runs?per_page=100") || {
            echo "sin-api: GitHub no respondió"
            return
        }
    fi
    # Si un job se re-corrió, vale el último intento.
    python3 -c '
import json, sys

required = sys.argv[1].split()
runs = {}
for run in json.load(sys.stdin).get("check_runs", []):
    old = runs.get(run["name"])
    if old is None or run["id"] > old["id"]:
        runs[run["name"]] = run
failed, pending = [], []
for name in required:
    run = runs.get(name)
    if run is None or run["status"] != "completed":
        pending.append(name)
    elif run["conclusion"] not in ("success", "skipped", "neutral"):
        failed.append(name)
if failed:
    print("fallida: " + " ".join(failed))
elif pending:
    print("pendiente: " + " ".join(pending))
else:
    print("ok")
' "$REQUERIDOS" <<<"$json" || echo "sin-api: respuesta ilegible de GitHub"
}

desplegar() {
    local sha="$1"
    # Sin set -e dentro de un `if`: cada paso se comprueba a mano.
    git -C "$REPO" merge --ff-only -q "$sha" || return 1
    (cd "$AQUI" && OLTERRA_ORIGEN=auto OLTERRA_SIN_PULL=1 ./olterra.sh actualizar) || return 1
}

main() {
    local rama_actual actual remoto desplegado fallida ci
    AQUI=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
    REPO=$(git -C "$AQUI" rev-parse --show-toplevel 2>/dev/null) || {
        echo "Error: esto no es un repositorio git" >&2
        return 1
    }
    RAMA="${OLTERRA_AUTO_RAMA:-main}"
    REQUERIDOS="${OLTERRA_CI_REQUERIDOS-pruebas interfaz imagen despliegue}"
    ESTADO=$(dir_estado)

    # Una sola actualización a la vez; ./olterra.sh comparte este candado.
    exec 9>"$ESTADO/lock"
    if ! flock -n 9; then
        echo "Hay otra actualización en curso; reintento en el siguiente turno."
        return 0
    fi
    export OLTERRA_BLOQUEO=1

    rama_actual=$(git -C "$REPO" branch --show-current)
    if [ "$rama_actual" != "$RAMA" ]; then
        decir "El servidor sigue la rama '${rama_actual:-(commit suelto)}', no $RAMA: no se actualiza solo. Para seguir $RAMA: ./olterra.sh rama $RAMA"
        return 0
    fi
    if ! git -C "$REPO" fetch -q origin "$RAMA"; then
        decir "No pude traer $RAMA de origin (¿sin red?); reintento en el siguiente turno."
        return 0
    fi

    actual=$(git -C "$REPO" rev-parse HEAD)
    remoto=$(git -C "$REPO" rev-parse "origin/$RAMA")
    desplegado=$(cat "$ESTADO/desplegado" 2>/dev/null || true)
    if [ -z "$desplegado" ]; then desplegado="$actual"; fi
    fallida=$(cat "$ESTADO/fallida" 2>/dev/null || true)

    if [ "$remoto" = "$desplegado" ]; then
        rm -f "$ESTADO/aviso"
        return 0
    fi
    if [ "$remoto" = "$fallida" ]; then
        decir "El commit ${remoto:0:7} falló al desplegarse; espero uno nuevo (o ./olterra.sh actualizar a mano)."
        return 0
    fi
    if ! git -C "$REPO" merge-base --is-ancestor "$actual" "$remoto"; then
        decir "El servidor tiene commits que $RAMA no tiene: no avanzo (revisar a mano)."
        return 0
    fi

    if [ -n "$REQUERIDOS" ]; then
        ci=$(estado_ci "$remoto")
        case "$ci" in
        ok) ;;
        pendiente*)
            decir "El CI de ${remoto:0:7} no terminó (${ci#*: }); espero."
            return 0
            ;;
        fallida*)
            decir "El CI de ${remoto:0:7} falló (${ci#*: }): no lo despliego."
            return 0
            ;;
        *)
            decir "No pude ver el CI de ${remoto:0:7} (${ci#*: }); espero."
            return 0
            ;;
        esac
    fi

    if [ "${OLTERRA_AUTO_SIMULAR:-0}" = "1" ]; then
        echo "SIMULACIÓN: desplegaría ${remoto:0:7} ($RAMA)."
        return 0
    fi

    echo "Desplegando ${remoto:0:7} ($RAMA)…"
    if desplegar "$remoto"; then
        printf '%s\n' "$remoto" >"$ESTADO/desplegado"
        rm -f "$ESTADO/fallida" "$ESTADO/aviso"
        historial "$remoto" "ok"
        echo "Listo: ${remoto:0:7} en producción."
    else
        printf '%s\n' "$remoto" >"$ESTADO/fallida"
        historial "$remoto" "FALLÓ"
        echo "Error: no se pudo desplegar ${remoto:0:7}. Los contenedores que ya corrían siguen corriendo." >&2
        return 1
    fi
}

# En una sola línea: bash no vuelve a leer el archivo, que el `git merge` puede haber cambiado.
main "$@"; exit $?
