# Laboratorio — fase 0

> Última actualización: 2026-10-01 · Cierre de la fase 0: 14 oct 2026

Objetivo: salir de la fase 0 con comandos, salidas y capacidades **confirmados** en equipos
reales, para que la fase 1 se construya sobre hechos y no sobre el manual.

## Equipo

- OLT VSOL GPON (idealmente dos modelos: un V1600G1B y un V1600GS, porque se comportan
  distinto por SNMP; ver [DRIVERS_VSOL.md](DRIVERS_VSOL.md)).
- Splitter 1:8, 4 a 6 ONUs: al menos una en bridge (SFU) y una HGU con WiFi. Las que ya usa
  la operación (V2802DAC, V280XRWT) sirven: en `Documents` y `Downloads` hay firmwares de
  esas ONU.
- MikroTik como BNG (RouterOS **v7**; si solo hay v6, anotarlo: el túnel WireGuard no corre
  ahí, ver ARQUITECTURA §A.4).
- Atenuadores ópticos para simular potencias en el borde (−25 a −29 dBm).

## 1. Preparar la OLT

Desde la consola o SSH, con el usuario de fábrica **solo esta vez** (después se cambia):

```
enable
configure terminal
user add olterra login-password <clave-fuerte>
user role olterra admin
snmp-server community <comunidad-lab> ro
login-access-list permit ssh <ip-del-portatil> 255.255.255.255
login-access-list permit snmp <ip-del-portatil> 255.255.255.255
exit
write
```

Anotar en la tabla de resultados qué de esto falló y con qué mensaje: es exactamente lo que
el asistente de alta va a automatizar. En V1600GS puede hacer falta `snmp-server start` y
`write memory` (guía de LibreNMS).

## 2. Capturar

Con el proyecto instalado (ver [MANUAL_DESARROLLADOR.md](MANUAL_DESARROLLADOR.md)):

```powershell
$env:OLTERRA_CAPTURE_PASSWORD = "<clave del usuario olterra>"
.venv\Scripts\olterra-capture --host 192.168.8.200 --usuario olterra `
    --pon 1 --onu 1:1 --onu 1:2 --snmp-comunidad <comunidad-lab> --anonimizar
```

- `--pon` se repite por cada puerto con ONUs; `--onu PON:ONU` toma muestras para los comandos
  por ONU (una bridge y una HGU).
- Corre **solo comandos de lectura** del catálogo y, con `--snmp-comunidad`, recorre las tablas
  SNMP del MIB V1600G.
- Deja todo en `lab-captures/vsol-gpon/<modelo>/<firmware>/<fecha>/` (ignorado por git) con
  un `manifest.json` y un resumen en pantalla de qué comandos **no existen** en ese firmware.
- Si la OLT solo habla algoritmos SSH viejos: `--ssh-legacy`.
- Para probar la herramienta sin OLT: `python -m olterra.devtools.vsol_sim --puerto 2222` en
  otra terminal y `--host 127.0.0.1 --puerto 2222 --usuario admin` (clave `olterra-sim`).

Repetir la captura en tres momentos: sin ONUs nuevas, con una ONU en autofind (conectada sin
autorizar) y con una ONU con atenuador (potencia baja). Así quedan muestras de cada caso.

## 3. Revisar y versionar

1. `--anonimizar` cambia seriales y MAC por valores falsos consistentes. **No** detecta
   nombres de clientes ni direcciones en descripciones o en `show running-config`: revisarlos
   a mano (Ley 1581). Si una OLT de producción del ISP piloto se captura, esto es obligatorio.
2. Copiar la carpeta revisada a `tests/fixtures/vsol-gpon/<modelo>/<firmware>/<fecha>/`.
3. `pytest tests/unit/test_fixtures.py`: si un parser no reconoce la salida, falla y muestra
   cuál. Corregir el parser con esa salida como caso.
4. Marcar `verified=True` en los comandos respaldados y actualizar DRIVERS_VSOL.md.

## 4. Preguntas que el laboratorio responde

| # | Pregunta | Cómo se responde | Decide |
|---|---|---|---|
| 1 | ¿Qué devuelve `show onu info` y `show onu auto-find` en cada firmware? | Captura | Parsers de la fase 1 |
| 2 | ¿El V1600G1B de la operación expone tablas por ONU por SNMP? | Captura con `--snmp-comunidad`; ver filas de `gOnuOpticalInfoTable` | Si el sondeo de potencias va por SNMP o CLI |
| 3 | Unidad de `gOnuRttDistance` (¿metros?) | Comparar con la distancia de `show onu detail-info` y con el largo real del rollo | Cruce ranging vs. mapa |
| 4 | ¿Se puede configurar la WAN PPPoE de una HGU desde la OLT (OMCI / protocolo privado)? | Probar en la CLI (`onu <n> ?` en modo PON) y en el manual del firmware actual | Si la fase 1 trae WAN por OLT o espera a TR-069 |
| 5 | ¿Y el WiFi (SSID y clave)? | Igual | Si GenieACS se adelanta a la fase 2 |
| 6 | ¿`terminal length 0` existe? ¿Cómo se ve el aviso de paginación? | Captura (el resumen lo muestra) | Perfil de sesión |
| 7 | ¿El SSH del V1600GS cae en la CLI o en una shell (vtysh)? | Entrar a mano | Capacidad `ssh_lands_in_shell` |
| 8 | ¿Cuánto tarda y cuánta CPU sube `show pon onu all rx-power` con 64+ ONUs? | `show sys cpu-usage` antes y durante | Intervalo del sondeo por CLI |
| 9 | ¿Qué traps manda la OLT y con qué varbinds? | `snmp-server host` apuntando al portátil + `snmptrapd` o Wireshark | Receptor de traps de la fase 1 |
| 10 | ¿Qué IP de gestión trae de fábrica cada modelo? | Mirar antes de cambiarla | Documentación del alta (ARQUITECTURA §A.3) |

## 5. Túnel contra el CHR

1. En el CHR (una sola vez): `olterra-admin concentrador` y pegar el script. Anotar la llave
   pública que RouterOS generó para `olterra-hub` en `OLTERRA_TUNNEL_HUB_PUBLIC_KEY`.
2. `POST /v1/tunnel/routers` (o desde la API de pruebas) → pegar `isp_script` en el MikroTik
   de laboratorio y `hub_script` en el CHR.
3. Verificar: `last-handshake` reciente en ambos lados; desde el ejecutor, `ssh` a la IP NAT de
   la OLT; correr el script dos veces y confirmar que no duplica reglas.
4. Probar en un MikroTik **v6** que el script se detiene con el mensaje claro.

Si se comparte el CHR de ISPWatch: confirmar antes que nada use 198.18.0.0/15 y que las
reglas nuevas de forward queden después de las de ISPWatch que deban seguir aplicando.

## 6. VSOL INCE

| Qué mirar | Resultado |
|---|---|
| Alta de la OLT de laboratorio: cuánto tarda, qué pide abrir | |
| Autorización de ONU y plantillas | |
| Potencias, alarmas y a dónde notifica | |
| Cambio de WiFi / WAN de la ONU (TR-069 propio) | |
| Mapa o documentación de planta | |
| Integración con CRM, MikroTik, WhatsApp | |
| Precio por OLT / por ONU, moneda | |
| Dónde quedan los datos (región) y qué contrato de datos ofrece | |

La pregunta de fondo: qué NO hace INCE que un ISP colombiano pague (ARQUITECTURA, riesgos).

## Resultados

Llenar a medida que se avanza (modelo, firmware, fecha, quién):

| Fecha | Modelo / firmware | Qué se probó | Resultado | Captura |
|---|---|---|---|---|
| | | | | |
