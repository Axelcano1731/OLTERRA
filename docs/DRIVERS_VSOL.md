# Driver VSOL GPON

> Última actualización: 2026-10-01 · Código: `src/olterra/drivers/vsol_gpon/`

Cubre la serie V1600G (V1600G1, V1600G1B, V1600G2, V1600GS…). **Ningún comando está
verificado en laboratorio todavía**: la sintaxis sale del manual público y de fuentes
abiertas, y cada uno lleva `verified=False` hasta que una captura real de ese modelo y
firmware lo confirme (ver [LABORATORIO.md](LABORATORIO.md)).

## Fuentes

| Fuente | Qué aporta | Confianza |
|---|---|---|
| Manual "GPON OLT CLI User Manual" v2.1 de VSOL (2021-04-25), 293 páginas | Sintaxis de casi todos los comandos, modos de la CLI, mensajes de error (§2.3.4), paginación (§2.3.2) | Media: firmware de 2021; el mismo manual se contradice (`uservlan` vs `user-vlan`) |
| MIB V1600G del fabricante (incluido en LibreNMS PR #19368) | OIDs de las tablas por ONU | Alta para los OIDs; que existan depende del firmware |
| LibreNMS PR #19368 (V1600GS fw V1.2.0/V4.0.0, sin fusionar) | Qué tablas SNMP responde un V1600GS real; `write memory`, `snmp-server start`, `snmp-server trap-host`, `login-access-list` | Media-alta: probado en equipo real por su autor |
| LibreNMS PR #19850 (V1600G1B fw V1.4.4R, sin fusionar) | Que el V1600G1B no expone tablas por ONU | Media: un solo equipo |
| Guías públicas (technicalafnan.com, yusufmiahbd.blogspot.com) | Prompts reales, `show interface brief`, rangos `onu 6-20 activate` | Baja-media |

No se encontró en ninguna fuente pública una **salida** real de `show onu info` ni de
`show onu auto-find`. Por eso los parsers no suponen nombres de columna: reconocen la tabla
por su forma y las columnas por nombre aproximado o por contenido, y si no entienden algo lo
dicen (`UnrecognizedOutput`) en vez de devolver datos a medias.

## Sesión CLI

| Aspecto | Valor | Fuente |
|---|---|---|
| Prompts | `gpon-olt>`, `gpon-olt#`, `gpon-olt(config)#`, `gpon-olt(config-pon-0/1)#`, `gpon-olt(profile-onu:10)#` | Manual §2.2 y guías |
| Modo privilegiado | `enable` + clave | Manual §1 |
| Paginación | Pausa por pantalla, cualquier tecla sigue; `terminal length 0` la apaga (§22.4.2). El ejecutor responde a `--More--` aunque ese comando no exista | Manual §2.3.2 |
| Errores | `Unknown command`, `Command incomplete`, `Too many parameters`, `Ambiguous command` | Manual §2.3.4 |
| Guardar | `write` (V1600GS: `write memory`) | Manual §22.2.1, LibreNMS |
| Interfaz PON | `interface gpon 0/<pon>` | Manual §18 |
| Usuarios | Admin y Normal (solo lectura) | Manual §23.2 |

El V1600GS podría dejar la sesión SSH en una shell y no en la CLI (la guía de LibreNMS usa
`vtysh -c`). El patrón de prompt acepta los dos casos y la capacidad `ssh_lands_in_shell`
queda como desconocida hasta confirmarlo.

## Catálogo de comandos

Generado desde `commands.py`. Los parámetros se validan antes de armar el comando (rangos,
serial canónico, sin espacios ni saltos de línea): nada que venga de un usuario puede
inyectar otro comando en la OLT.

| Llave | Comando | Modo | Tipo | Fuente |
|---|---|---|---|---|
| `system.version` | `show version` | config | lectura | Manual v2.1 §22.3.2 |
| `system.running_time` | `show sys running-time` | config | lectura | Manual v2.1 §22.3.3 |
| `system.cpu` | `show sys cpu-usage` | config | lectura | Manual v2.1 §22.3.1 |
| `system.memory` | `show sys mem` | config | lectura | Manual v2.1 §22.3.1 |
| `system.fan` | `show fan` | config | lectura | Manual v2.1 §22.5.8. Incluye la temperatura del equipo |
| `system.running_config` | `show running-config` | config | lectura | Manual v2.1 §22.2.4 |
| `system.startup_config` | `show startup-config` | config | lectura | Manual v2.1 §22.2.3 |
| `system.alarm_config` | `show alarm configuration` | config | lectura | Manual v2.1 §25.3.1 |
| `system.syslog_major` | `show syslog level major` | config | lectura | Manual v2.1 §26.3.1 |
| `system.users` | `user list` | config | lectura | Manual v2.1 §23.5 |
| `interfaces.brief` | `show interface brief` | config | lectura | Guías públicas |
| `snmp.communities` | `show snmp-server community` | config | lectura | Manual v2.1 §24.4.1 |
| `snmp.trap_hosts` | `show snmp-server targetaddress` | config | lectura | Manual v2.1 §24.4.2 |
| `profile.list` | `show profile {kind} all` | config | lectura | Manual v2.1 §20.9 |
| `mac.by_pon` | `show mac address-table interface gpon 0/{pon}` | config | lectura | Manual v2.1 §9.3.1. Sintaxis de la interfaz sin confirmar |
| `pon.info` | `show pon info` | PON | lectura | Manual v2.1 §18.3.1 |
| `pon.optical` | `show pon optical transceiver` | PON | lectura | Manual v2.1 §18.2.2 |
| `pon.statistics` | `show pon statistics` | PON | lectura | Manual v2.1 §18.2.1 |
| `onu.autolearn` | `show onu auto-learn` | PON | lectura | Manual v2.1 §17.1 |
| `onu.autofind` | `show onu auto-find` | PON | lectura | Manual v2.1 §19.2.1 |
| `onu.autofind_detail` | `show onu auto-find detail-info` | PON | lectura | Manual v2.1 §19.2.1 |
| `onu.list` | `show onu info` | PON | lectura | Manual v2.1 §19.2.3. El manual escribe `Show onuinfo` |
| `onu.rx_power_all` | `show pon onu all rx-power` | PON | lectura | Manual v2.1 §18.2.3 |
| `onu.detail` | `show onu detail-info {onu}` | PON | lectura | Manual v2.1 §19.2.4 |
| `onu.optical` | `show onu {onu} optical-info` | PON | lectura | Manual v2.1 §19.3.1 |
| `onu.capability` | `show onu {onu} capability` | PON | lectura | Manual v2.1 §19.3.12 |
| `onu.service_config` | `show running-config onu {onu}` | PON | lectura | Manual v2.1 §19.3.11 |
| `onu.description` | `show onu {onu} description` | PON | lectura | Manual v2.1 §19.2.7 |
| `onu.authorize` | `onu add {onu} profile {profile} sn {serial}` | PON | escritura | Manual v2.1 §19.2.6 y ejemplo §19.4.7 |
| `onu.delete` | `no onu {onu}` | PON | escritura | Inferido del patrón `no onu …`; confirmar |
| `onu.reboot` | `onu {onu} reboot` | PON | escritura | Manual v2.1 §19.3.4 |
| `onu.activate` | `onu {onu} activate` | PON | escritura | Manual v2.1 §19.2.5 |
| `onu.deactivate` | `onu {onu} deactivate` | PON | escritura | Manual v2.1 §19.2.5 |
| `onu.set_description` | `onu {onu} description {description}` | PON | escritura | Manual v2.1 §19.2.7 |
| `onu.bind_line_profile` | `onu {onu} profile line {profile}` | PON | escritura | Manual v2.1 §20.2 |
| `onu.bind_srv_profile` | `onu {onu} profile srv {profile}` | PON | escritura | Manual v2.1 §20.2 |
| `onu.tcont` | `onu {onu} tcont {tcont} dba {profile}` | PON | escritura | Manual v2.1 §19.3.5 |
| `onu.gemport` | `onu {onu} gemport {gemport} tcont {tcont}` | PON | escritura | Manual v2.1 §19.3.6 |
| `onu.service` | `onu {onu} service {service} gemport {gemport} vlan {vlan}` | PON | escritura | Manual v2.1 §19.3.7 |
| `onu.service_port` | `onu {onu} service-port {service_port} gemport {gemport} uservlan {user_vlan} vlan {vlan}` | PON | escritura | Manual v2.1 §19.3.8. La tabla dice `uservlan` y el ejemplo `user-vlan` |
| `onu.portvlan_tag` | `onu {onu} portvlan {uni_kind} {uni} mode tag vlan {vlan}` | PON | escritura | Manual v2.1 §19.3.9 |
| `onu.portvlan_transparent` | `onu {onu} portvlan {uni_kind} {uni} mode transparent` | PON | escritura | Manual v2.1 §19.3.9 |
| `config.save` | `write` | privilegiado | escritura | Manual v2.1 §22.2.1. VSOL pierde lo no guardado al reiniciar |
| `snmp.set_community` | `snmp-server community {community} ro` (lleva clave) | config | escritura | Manual v2.1 §24.4.1 |
| `snmp.add_trap_host` | `snmp-server host {host} version 2c community {community}` (lleva clave) | config | escritura | Manual v2.1 §24.4.2 |
| `snmp.enable_traps` | `snmp-server enable traps snmp` | config | escritura | Manual v2.1 §24.4.2 |
| `snmp.start` | `snmp-server start` | config | escritura | LibreNMS PR #19368 |
| `access.permit` | `login-access-list permit {service} {host} {mask}` | config | escritura | LibreNMS PR #19368 y guías públicas |
| `user.add` | `user add {username} login-password {password}` (lleva clave) | config | escritura | Manual v2.1 §23.4 |
| `user.role_admin` | `user role {username} admin` | config | escritura | Manual v2.1 §23.4. Sintaxis ambigua en el manual |
| `user.delete` | `user delete {username}` | config | escritura | Manual v2.1 §23.6 |

Sintaxis distinta por modelo (`CommandOverride`):

| Llave | Modelo | Comando | Fuente |
|---|---|---|---|
| `config.save` | `V1600GS*` | `write memory` | LibreNMS PR #19368 |
| `snmp.add_trap_host` | `V1600GS*` | `snmp-server trap-host {host} community {community}` | LibreNMS PR #19368 |

## SNMP

Empresa VSOL: `1.3.6.1.4.1.37950`. Índice de las tablas por ONU: `<columna>.<pon>.<onu>`.
Los valores ópticos llegan como texto decimal (`"-10.47"`); `0.00` significa sin lectura.

| Tabla | OID | Columnas que usa Olterra |
|---|---|---|
| `gOnuStaInfoTable` | `.37950.1.1.6.1.1.1` | 3 admin, 5 fase (`working(3)`, `los(1)`, `dyingGasp(4)`, `offLine(6)`…), 7 descripción, 8-10 registro/desregistro |
| `gOnuAuthInfoTable` | `.37950.1.1.6.1.1.2` | 3 perfil, 4 modo de autenticación, 5 serial, 6 modelo |
| `gOnuOpticalInfoTable` | `.37950.1.1.6.1.1.3` | 3 temperatura, 4 voltaje, 5 bias, 6 TX, 7 RX en la ONU, 8 RX en la OLT |
| `gOnuDetailInfoTable` | `.37950.1.1.6.1.1.4` | 3 fabricante, 5 serial, 17 modelo, 25 versión |
| `gOnuRttTable` | `.37950.1.1.6.1.1.12` | 3 distancia (unidad por confirmar) |
| `ponTransceiverTable` | `.37950.1.1.5.10.13.1.1` | 2 temperatura, 3 voltaje, 4 bias, 5 TX del puerto PON |

`sysObjectID` es el mismo en V1600GS y V1600G1B (`.37950.1.1.5.10.14.1`): el modelo sale de
`sysDescr`.

## Matriz de capacidades

Punto de partida, por modelo y firmware (`capabilities.py`); el alta de cada OLT debe sondear
y guardar lo que de verdad responde.

El modelo se compara sin guiones ni espacios y sin importar mayúsculas: la web de la OLT dice
"V1600G1-B" (Device Model) y otras fuentes "V1600G1B", y son el mismo. Un modelo sin reglas
propias, como la **V1600G0-B** (firmware V1.4.8R) del laboratorio, recibe las de la columna
"Por defecto" hasta que sus capturas digan otra cosa.

| Capacidad | Por defecto | V1600GS | V1600G1B (V1.4.4R) |
|---|---|---|---|
| Estado, serial, potencias y distancia por ONU vía SNMP | desconocido | sí | **no** |
| Tráfico por ONU (IF-MIB, `GPON01ONU1`) | desconocido | sí | desconocido |
| Potencias por ONU vía CLI (`show pon onu all rx-power`) | sí (manual) | sí | sí |
| Autofind y autorización por CLI | sí (manual) | sí | sí |
| WAN de la ONU desde la OLT | desconocido: el manual la configura en la web de la ONU | — | — |
| WiFi de la ONU desde la OLT | desconocido: si no se puede, GenieACS se adelanta | — | — |

## Cómo se suma o corrige un comando

1. Captura de laboratorio con `olterra-capture` (ver LABORATORIO.md), revisada y copiada a
   `tests/fixtures/vsol-gpon/<modelo>/<firmware>/`.
2. Si el parser no la reconoce, `tests/unit/test_fixtures.py` falla: se corrige el parser.
3. Si la sintaxis cambia en ese modelo o firmware, se agrega un `CommandOverride`.
4. Se marca `verified=True` y se actualiza esta página en el mismo cambio.
