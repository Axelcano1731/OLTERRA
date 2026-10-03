#!/bin/sh
# Respaldo de la base (corre dentro del contenedor `backup`, ver compose.yml).
#
#   respaldo.sh             un respaldo ahora
#   respaldo.sh --cada-dia  uno al arrancar y después uno cada 24 horas
#
# pg_dump en formato custom (se restaura con pg_restore) como olterra_owner, que tiene
# BYPASSRLS: el respaldo lleva a todos los ISP. Con OLTERRA_DB_SCHEMA (base compartida) solo
# lleva ese esquema: lo de ISPWatch y Converza no es de Olterra y tiene su propio respaldo. Se borran los de más de OLTERRA_RESPALDO_DIAS.
# Las credenciales van cifradas con la llave de cada ISP, pero el archivo igual es sensible:
# queda solo para root (umask 077) y fuera del servidor debe ir cifrado.
set -eu
umask 077

respaldar() {
    stamp=$(date -u +%Y%m%d-%H%M%S)
    destino="/respaldos/olterra-$stamp.dump"
    if [ -n "${OLTERRA_DB_SCHEMA:-}" ]; then
        pg_dump --format=custom --schema="$OLTERRA_DB_SCHEMA" --file="$destino.tmp"
    else
        pg_dump --format=custom --file="$destino.tmp"
    fi
    mv "$destino.tmp" "$destino"
    find /respaldos -name 'olterra-*.dump' -mtime +"${OLTERRA_RESPALDO_DIAS:-14}" -delete
    echo "Respaldo listo: $(basename "$destino") ($(du -h "$destino" | cut -f1))"
}

if [ "${1:-}" = "--cada-dia" ]; then
    while true; do
        respaldar || echo "El respaldo falló; se reintenta en 24 horas" >&2
        sleep 86400
    done
else
    respaldar
fi
