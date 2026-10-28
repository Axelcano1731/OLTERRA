# Drivers HSGQ GPON y BCD GPON

> Última actualización: 2026-10-28 · Código: `src/olterra/drivers/hsgq_gpon.py`,
> `src/olterra/drivers/bcd_gpon.py`

Los dos salieron de las OLT de Tocaima en producción, por Telnet desde la red del ISP: la ayuda
`?` de cada modo (sin mandar nada que configure) y las salidas reales de cada comando. Las
capturas están en `tests/fixtures/hsgq-gpon/` y `tests/fixtures/bcd-gpon/`, con los nombres de
clientes reemplazados por `CLIENTE_NNN`, el operador por `OPERADOR`, seriales y MAC
anonimizados y las comunidades SNMP tapadas.

**Solo lectura.** Olterra consulta, monitorea (estado y señal de cada ONU, alarmas) y saca
respaldos de la configuración. Aprovisionar, cambiar la clave de la OLT y las demás escrituras
todavía no existen en estas marcas: la API responde 400 ("Olterra todavía no sabe hacer …") y
la interfaz no muestra esos botones.

Se elige con **Marca** al agregar o editar la OLT (`driver` en la API: `hsgq-gpon`,
`bcd-gpon`). Las credenciales de fábrica del servidor son las de VSOL, así que en estas marcas
hay que escribir usuario y clave.

## HSGQ-G008, software V1.3.12C (`hsgq-gpon`)

OLT de 172.17.1.2: 8 PON, 48 ONU en los PON 1, 2, 3 y 5. Entra por Telnet (`Username:`,
`Password:`), el prompt es `OLT_3>` / `OLT_3#` / `OLT_3(config)#` y `enable` no pide clave.

| Llave | Comando | Modo |
| --- | --- | --- |
| `system.version` | `show version` | privilegiado |
| `system.running_config` | `show running-config` | `configure` |
| `interfaces.brief` | `show ont-info all` | `configure` |
| `onu.list` | `show ont-info {pon} all` | `configure` |
| `onu.state` | `show ont-info {pon} all` | `configure` |
| `onu.rx_power_all` | `show ont-optical gpon {pon}` | `configure` |
| `onu.autofind` | `show ont-autofind {pon}` | `configure` |

- Se configura con `configure` (no `configure terminal`) y el PON va como parámetro: ninguna
  consulta entra a `interface gpon N`.
- **Numera las ONU desde 0** (`1/0`). Olterra las cuenta desde 1, porque en las alarmas la ONU 0
  es "todo el PON": la `1/0` de la OLT es la ONU 1 en Olterra, la `1/1` la 2, etc.
  (`ONU_OFFSET`). Si algún día hay comandos por ONU, la plantilla tiene que restar 1.
- `show ont-info` es una tabla de columnas fijas y el nombre de la ONU (última columna) puede
  llevar espacios: se corta por la posición de cada columna en el encabezado.
- Los errores empiezan con `vty%` (`vty% Command incomplete.`, `vty% … Unknown command`).
- `show ont-info all` no lista los PON sin ONU: el número de PON sale del modelo
  (`HSGQ-G008` → 8) para que el monitoreo los recorra todos.
- `terminal length 0` existe; el ejecutor lo manda al entrar.

## BCD-1508GS, firmware V2.1.6_181108 (`bcd-gpon`)

OLT de 172.16.0.2: 8 PON, 116 ONU en 7 de ellos. `show device` dice "BCD-1508GS" y de
fabricante "GPON OLT"; la CLI (`ont` con perfiles de línea y de servicio, `>>User name:`) es la
de C-Data. Prompts `OLT>`, `OLT#`, `OLT(config)#`, `OLT(config-interface-gpon)#`.

| Llave | Comando | Modo |
| --- | --- | --- |
| `system.version` | `show version` | privilegiado (solo el firmware) |
| `system.device` | `show device` | privilegiado (modelo y serial) |
| `system.running_config` | `show current-config` | `config` |
| `interfaces.brief` | `show ont info all` | `config` |
| `onu.list` | `show ont info {pon} all` | `config` |
| `onu.state` | `show ont info {pon} all` | `config` |
| `onu.rx_power_all` | `show ont optical-info {pon} all` | `interface gpon` |
| `onu.autofind` | `show ont autofind {pon}` | `config` |

- Configuración con `config`; la interfaz GPON es una sola (`interface gpon`, sin número) y el
  PON va como parámetro. La potencia óptica solo se consulta dentro de esa interfaz.
- `show ont info` trae dos tablas: estado (puerto, ONT, serial, control, run, config, match) y
  después las descripciones. El parser las junta por puerto y ONT.
- Numera las ONU desde 1, igual que Olterra.
- `Error: The automatically found ONTs does not exist.` es un PON sin ONU nuevas, no una falla;
  cualquier otro `Error:` sí lo es.
- No tiene `terminal length`: el ejecutor atiende el `--More ( Press 'Q' to quit )--`.
- El modelo sale de `show device` y el firmware de `show version` (la captura usa los dos).
- Como en la HSGQ, los PON vacíos no aparecen: `BCD-1508GS` → 8 PON.

## Qué falta

- Aprovisionar (alta de ONU, perfiles, WAN/Wi-Fi), cambiar la clave de la OLT y reiniciar ONU:
  hay que explorar la ayuda de esos modos y validar cada escritura en una ONU de prueba.
- Comandos por ONU (`onu.optical`, distancia, descripción, configuración de servicio).
- Otros modelos y firmwares de estas marcas: cada uno necesita su captura.
