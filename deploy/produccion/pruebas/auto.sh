#!/usr/bin/env bash
# Prueba de la lógica de auto-actualizar.sh, sin Docker ni red: un "origin" local, un servidor
# que lo sigue y un olterra.sh de mentira que solo anota que lo llamaron.
# Hace falta bash, git, flock y python3 (CI y el servidor los traen).
# Las condiciones de `verificar` van entre comillas simples a propósito: se evalúan después, con
# los valores de ese momento (SC2016); y `salida` se lee dentro de esas condiciones (SC2034).
# shellcheck disable=SC2016,SC2034
set -euo pipefail

aqui=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

export GIT_AUTHOR_NAME=prueba GIT_AUTHOR_EMAIL=prueba@example.com
export GIT_COMMITTER_NAME=prueba GIT_COMMITTER_EMAIL=prueba@example.com
export OLTERRA_AUTO_ESTADO="$tmp/estado" STUB_LOG="$tmp/stub.log" OLTERRA_CI_JSON="$tmp/ci.json"
export OLTERRA_CI_REQUERIDOS="pruebas interfaz"
: >"$STUB_LOG"

fallos=0
verificar() { # verificar <descripción> <condición>
    if eval "$2"; then
        echo "  ok  $1"
    else
        echo "  MAL $1"
        fallos=$((fallos + 1))
    fi
}

llamadas() { wc -l <"$STUB_LOG" | tr -d ' '; }

# El CI de GitHub, como lo devuelve /check-runs.
ci() { # ci <pruebas> <interfaz>   (estado: success | failure | pendiente)
    local n=0 nombre estado status conclusion filas=()
    for nombre in pruebas interfaz; do
        n=$((n + 1))
        estado=$([ "$nombre" = pruebas ] && echo "$1" || echo "$2")
        if [ "$estado" = pendiente ]; then
            status=in_progress conclusion=null
        else
            status=completed conclusion="\"$estado\""
        fi
        filas+=("{\"id\": $n, \"name\": \"$nombre\", \"status\": \"$status\", \"conclusion\": $conclusion}")
    done
    printf '{"check_runs": [%s, %s]}\n' "${filas[0]}" "${filas[1]}" >"$OLTERRA_CI_JSON"
}

# --- Un origin, un clon de desarrollo y el "servidor" ---------------------------------------
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/dev" 2>/dev/null
mkdir -p "$tmp/dev/deploy/produccion"
cp "$aqui/auto-actualizar.sh" "$tmp/dev/deploy/produccion/"
cat >"$tmp/dev/deploy/produccion/olterra.sh" <<'EOF'
#!/usr/bin/env bash
# olterra.sh de mentira: solo anota cómo lo llamaron.
echo "actualizar sin_pull=${OLTERRA_SIN_PULL:-} origen=${OLTERRA_ORIGEN:-} bloqueo=${OLTERRA_BLOQUEO:-}" >>"$STUB_LOG"
exit "${STUB_EXIT:-0}"
EOF
chmod +x "$tmp/dev/deploy/produccion/"*.sh
git -C "$tmp/dev" add -A
git -C "$tmp/dev" commit -q -m "inicial"
git -C "$tmp/dev" push -q origin main
git clone -q "$tmp/origin.git" "$tmp/srv" 2>/dev/null
auto="$tmp/srv/deploy/produccion/auto-actualizar.sh"

nuevo_commit() {
    echo "$1" >>"$tmp/dev/cambio.txt"
    git -C "$tmp/dev" add -A
    git -C "$tmp/dev" commit -q -m "$1"
    git -C "$tmp/dev" push -q origin main
}
correr() { "$auto" 2>&1 || echo "[salida $?]"; }

echo "1. Sin commits nuevos: no hace nada"
ci success success
salida=$(correr)
verificar "no llama a olterra.sh" '[ "$(llamadas)" = 0 ]'
verificar "no dice nada" '[ -z "$salida" ]'

echo "2. Commit nuevo con el CI sin terminar: espera"
nuevo_commit "uno"
ci success pendiente
salida=$(correr)
verificar "no despliega" '[ "$(llamadas)" = 0 ]'
verificar "dice qué job falta" '[[ "$salida" == *"no terminó (interfaz)"* ]]'
salida=$(correr)
verificar "no repite el aviso en el siguiente turno" '[ -z "$salida" ]'

echo "3. El CI falla: no lo despliega"
ci success failure
salida=$(correr)
verificar "no despliega" '[ "$(llamadas)" = 0 ]'
verificar "dice que falló" '[[ "$salida" == *"falló (interfaz)"* ]]'

echo "4. CI en verde: despliega"
ci success success
salida=$(correr)
verificar "llama a olterra.sh una vez" '[ "$(llamadas)" = 1 ]'
verificar "sin git pull, de origen auto y con el candado tomado" \
    'grep -q "sin_pull=1 origen=auto bloqueo=1" "$STUB_LOG"'
verificar "el servidor quedó en el commit de origin" \
    '[ "$(git -C "$tmp/srv" rev-parse HEAD)" = "$(git -C "$tmp/dev" rev-parse HEAD)" ]'
verificar "anota lo desplegado" '[ "$(cat "$OLTERRA_AUTO_ESTADO/desplegado")" = "$(git -C "$tmp/dev" rev-parse HEAD)" ]'
verificar "deja historial" 'grep -q " auto ok" "$OLTERRA_AUTO_ESTADO/historial"'

echo "5. Al día otra vez: no repite"
correr >/dev/null
verificar "sigue en una llamada" '[ "$(llamadas)" = 1 ]'

echo "6. El despliegue falla: no se reintenta el mismo commit, el siguiente sí"
nuevo_commit "dos"
salida=$(STUB_EXIT=1 correr)
verificar "falla visible (salida distinta de 0)" '[[ "$salida" == *"[salida 1]"* ]]'
verificar "anota el commit que falló" '[ -f "$OLTERRA_AUTO_ESTADO/fallida" ]'
verificar "lo intentó una vez" '[ "$(llamadas)" = 2 ]'
correr >/dev/null
verificar "no reintenta el mismo" '[ "$(llamadas)" = 2 ]'
nuevo_commit "tres"
correr >/dev/null
verificar "con un commit nuevo vuelve a intentar" '[ "$(llamadas)" = 3 ]'
verificar "y limpia lo que falló" '[ ! -f "$OLTERRA_AUTO_ESTADO/fallida" ]'

echo "7. Simulación: decide pero no despliega"
nuevo_commit "cuatro"
salida=$(OLTERRA_AUTO_SIMULAR=1 correr)
verificar "dice que desplegaría" '[[ "$salida" == *"SIMULACIÓN: desplegaría"* ]]'
verificar "no llama a olterra.sh" '[ "$(llamadas)" = 3 ]'

echo "8. Candado ocupado: espera"
exec 8>"$OLTERRA_AUTO_ESTADO/lock"
flock -n 8
salida=$(correr)
exec 8>&-
verificar "avisa que hay otra actualización" '[[ "$salida" == *"otra actualización en curso"* ]]'
verificar "no despliega" '[ "$(llamadas)" = 3 ]'

echo "9. Servidor en otra rama: no toca nada"
git -C "$tmp/srv" checkout -q -b otra
salida=$(correr)
verificar "explica cómo pasar a main" '[[ "$salida" == *"./olterra.sh rama main"* ]]'
verificar "no despliega" '[ "$(llamadas)" = 3 ]'
git -C "$tmp/srv" checkout -q main

echo "10. El servidor tiene un commit que main no tiene: no avanza"
echo local >"$tmp/srv/local.txt"
git -C "$tmp/srv" add local.txt
git -C "$tmp/srv" commit -q -m "cambio local"
salida=$(correr)
verificar "no despliega" '[ "$(llamadas)" = 3 ]'
verificar "avisa que revise a mano" '[[ "$salida" == *"revisar a mano"* ]]'

echo
if [ "$fallos" -eq 0 ]; then
    echo "Todo bien."
else
    echo "$fallos comprobación(es) fallaron."
    exit 1
fi
