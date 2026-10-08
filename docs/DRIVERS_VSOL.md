# Driver VSOL GPON

> Última actualización: 2026-10-03 · Código: `src/olterra/drivers/vsol_gpon/`

Cubre la serie V1600G (V1600G0-B, V1600G1, V1600G1B, V1600G2, V1600GS…). La sintaxis sale
del manual público y de fuentes abiertas, y cada comando lleva `verified=False` hasta que una
captura real lo confirme (ver [LABORATORIO.md](LABORATORIO.md)). **Desde el 2026-10-03 hay una
captura real: V1600G0-B con firmware V1.4.8R** (`tests/fixtures/vsol-gpon/V1600G0-B/`), que
verifica 26 comandos con la sintaxis del catálogo y 3 con la sintaxis propia de ese modelo
(abajo). El resto, y todos los demás modelos, siguen sin verificar.

## V1600G0-B V1.4.8R: lo que se aprendió en laboratorio (2026-10-03)

**Sintaxis propia del modelo** (overrides en `commands.py`; el manual v2.1 usa otras palabras):

| Llave | Manual | V1600G0-B V1.4.8R |
|---|---|---|
| `onu.optical` | `show onu {onu} optical-info` | `show onu {onu} optical_info` (guion bajo) |
| `onu.description` | `show onu {onu} description` | `show onu {onu} desc` |
| `pon.statistics` | `show pon statistics` | `show pon {pon} statistics` |

**No existe en este firmware** `show profile {kind} all` (`profile.list`): los perfiles están
en modo PON y como `show profile dba`, `show profile onu`, `show profile srv`… (con `id`,
`name` o `running-config`). Queda para la fase 1 (plantillas de autorización).

**La CLI posiciona las columnas con el cursor**: cada celda se imprime como `` + `ESC[<n>C`
(vuelve al inicio y avanza `n` columnas). Quien borre las secuencias ANSI y trate cada ``
como "sobrescribir desde el inicio" pierde todo menos la última celda. Olterra lo emula
(`executor/cli.py`, `_render_line`). Los parsers de `show onu info`, `show onu state` y
`show pon onu all rx-power` dependen de esto.

**Otros hechos de esa OLT:**

- `show version` trae `Olt Serial Number` y `Olt Device Model`: el serial también empieza con
  `V`; el modelo es el segundo.
- Los usuarios de la web y los de la CLI son distintos, y la clave de `enable` es otra más.
- La descripción de cada ONU (el nombre del cliente) se ve en `show interface brief` y en
  `show onu <n> desc`; en `show running-config onu <n>` (`onu.service_config`) viene además el
  usuario PPPoE de la WAN, con la clave tapada (`pwd ******`). Es dato personal: las capturas
  del repo lo reemplazan.
- Comandos nuevos que la ayuda `?` mostró y que el catálogo todavía no usa: `show onu <n> ber`,
  `show onu <n> statistics`, `show pon transceiver-info`, `show pon rx_power`.
- Una OLT con 57 ONU respondió `show onu info` y `show onu state` en menos de un segundo.

## Aprovisionamiento (V1600G0-B)

La receta de un alta sale de cómo la propia OLT guarda sus ONU (`show running-config onu N`):

```
onu add N profile <perfil> sn <serial>
onu N desc <descripción>
onu N profile onu <perfil>                              (solo si difiere del de 'onu add'; la G0-B lo rechaza)
onu N tcont 1 name INTERNET dba <perfil DBA>
onu N gemport 1 tcont 1 gemport_name INTERNET          (la OLT agrega "portid")
onu N gemport 1 traffic-limit downstream <perfil>
onu N service ser_1 gemport 1 vlan <vlan>
onu N service-port 1 gemport 1 uservlan <vlan> vlan <vlan> new_cos 0
onu N pri wan_adv add route                             (WAN PPPoE en la ONU)
onu N pri wan_adv index 1 route mode internet mtu 1492
onu N pri wan_adv index 1 route ipv4 pppoe proxy disable user <u> pwd <clave> mode auto nat enable
onu N pri wan_adv index 1 vlan tag wan_vlan <vlan> 0
onu N pri wan_adv index 1 bind lan1 lan2 … ssid1
onu N pri wifi_ssid 1 name <ssid> hide disable auth_mode wpa2psk encrypt_type tkipaes shared_key <clave> rekey_interval 0
onu N pri firewall level low                            (gestión remota)
onu N pri acl ping control enable lan enable wan enable ipv4_control disable ipv6_control disable
onu N pri acl http control enable lan enable wan enable ipv4_control disable ipv6_control disable
…                                                       (telnet, ftp, https: wan enable o disable)
write
```

- `drivers/vsol_gpon/provisioning.py`: `TemplateBody` (lo común a un plan), `ClientData` (lo de
  cada cliente), `authorize_calls` (la receta) y `parse_onu_running_config` ("copiar una ONU").
  Una prueba regenera el alta de la ONU 3 de la captura y la compara línea por línea con lo que
  la OLT guardó.
- Las claves PPPoE y WiFi van en la credencial sellada; el paso lleva `{{secret:pppoe_password}}`
  y `{{secret:wifi_key}}`. No pueden llevar espacios ni `?` (la CLI abriría la ayuda); el SSID
  tampoco lleva espacios.
- **Dos fases** (laboratorio 2026-10-07): `pri equid` entra con la ONU recién autorizada, pero la
  WAN responde `Unsupport private protocol` hasta que la ONU se conecta. El alta de la interfaz
  autoriza y guarda primero; la WAN y el WiFi van cuando `show onu state` dice `working`.
- Los puertos de la WAN se adaptan al modelo con `show onu N capability` (`Ethernet UNI
  number`): una V422 tiene 2 LAN, una V824 4. `show onu detail-info N` da el `Equipment ID`.
- `show interface brief` da en una lectura los PON de la OLT y cada ONU con su descripción y si
  está arriba (la descripción larga empuja la columna de estado: se lee desde el final).
- **Gestión remota** (sección `management` del plan): nivel de firewall y qué responde desde
  internet. Cada servicio (ping, telnet, ftp, http, https) se manda explícito: `wan enable` si el
  plan lo abre, `wan disable` si no, así un "Internet y WiFi" repetido también cierra lo que se
  quitó. Desde la LAN todo queda abierto. A veces la OLT guarda además `port N` (el puerto por
  defecto); no se manda. El usuario y la contraseña de administración de la ONU todavía no:
  falta la sintaxis de la OLT (`onu N pri ?`).
- La OLT guarda la clave PPPoE tapada (`pwd ******`) pero la WiFi **en claro** (`shared_key`):
  `redact` tapa las dos en cualquier salida.
- Laboratorio 2026-10-07: `onu add` y `onu N desc` entraron bien; `onu N profile onu default`
  dio `% Unknown command` (es lo que ya deja `onu add … profile default`). `show onu auto-find`
  responde `Index / Sn / Equipment ID` con filas `1<TAB>sn:GPON005c9160<TAB><TAB>VSOLV422`.
- **Sin verificar todavía**: hasta ejecutarlos en la OLT del laboratorio. `onu.delete` (`no onu N`)
  es inferido del manual; confirmar cuál usa la V1600G0-B.

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
| `onu.state` | `show onu state` | PON | lectura | Laboratorio V1600G0-B (no está en el manual) |
| `onu.distance` | `show onu {onu} distance` | PON | lectura | Laboratorio V1600G0-B (no está en el manual) |
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
| `onu.bind_onu_profile` | `onu {onu} profile onu {profile}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.gemport_limit_down` | `onu {onu} gemport {gemport} traffic-limit downstream {profile}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.pri_equid` | `onu {onu} pri equid {equipment_id}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B. Va antes de la WAN y el WiFi: sin ella la OLT responde `Unsupport private protocol` (alta real, 2026-10-07). El Equipment ID sale del autofind |
| `onu.wan_add_route` | `onu {onu} pri wan_adv add route` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.wan_route_mode` | `onu {onu} pri wan_adv index {wan} route mode internet mtu {mtu}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.wan_pppoe` | `onu {onu} pri wan_adv index {wan} route ipv4 pppoe proxy disable user {pppoe_user} pwd {pppoe_password} mode auto nat {nat}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún). Clave en `{{secret:pppoe_password}}` |
| `onu.wan_vlan` | `onu {onu} pri wan_adv index {wan} vlan tag wan_vlan {vlan} {cos}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.wan_bind` | `onu {onu} pri wan_adv index {wan} bind {binds}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún) |
| `onu.wifi_ssid` | `onu {onu} pri wifi_ssid {ssid_index} name {ssid} hide disable auth_mode wpa2psk encrypt_type tkipaes shared_key {wifi_key} rekey_interval 0` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B (sin ejecutar aún). Clave en `{{secret:wifi_key}}` |
| `onu.firewall` | `onu {onu} pri firewall level {level}` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B. `level`: low, middle o high |
| `onu.acl` | `onu {onu} pri acl {service} control enable lan enable wan {wan_access} ipv4_control disable ipv6_control disable` | PON | escritura | Configuración guardada de ONU reales en la V1600G0-B. `service`: ping, telnet, ftp, http, https; `wan_access`: enable o disable |
| `config.save` | `write` | privilegiado | escritura | Manual v2.1 §22.2.1. VSOL pierde lo no guardado al reiniciar |
| `snmp.set_community` | `snmp-server community {community} ro` (lleva clave) | config | escritura | Manual v2.1 §24.4.1 |
| `snmp.add_trap_host` | `snmp-server host {host} version 2c community {community}` (lleva clave) | config | escritura | Manual v2.1 §24.4.2 |
| `snmp.enable_traps` | `snmp-server enable traps snmp` | config | escritura | Manual v2.1 §24.4.2 |
| `snmp.start` | `snmp-server start` | config | escritura | LibreNMS PR #19368 |
| `access.permit` | `login-access-list permit {service} {host} {mask}` | config | escritura | LibreNMS PR #19368 y guías públicas |
| `user.add` | `user add {username} login-password {password}` (lleva clave) | config | escritura | Manual v2.1 §23.4 |
| `user.role_admin` | `user role {username} admin` | config | escritura | Manual v2.1 §23.4. Sintaxis ambigua en el manual |
| `user.delete` | `user delete {username}` | config | escritura | Manual v2.1 §23.6 |
| `user.set_login_password` | `user login-password {username} {password}` | config | escritura | Ayuda de la V1600G0-B (`user ?`). **Sin verificar**: falta confirmar si la clave va en la misma línea. La clave nueva viaja sellada (`{{secret:new_password}}`) |
| `user.set_enable_password` | `user enable-password {username} {password}` | config | escritura | Ídem. Clave nueva en `{{secret:new_enable_password}}` |

Sintaxis distinta por modelo (`CommandOverride`):

| Llave | Modelo | Comando | Fuente |
|---|---|---|---|
| `onu.optical` | `V1600G0*` | `show onu {onu} optical_info` | Laboratorio V1600G0-B (verificado) |
| `onu.description` | `V1600G0*` | `show onu {onu} desc` | Laboratorio V1600G0-B (verificado) |
| `pon.statistics` | `V1600G0*` | `show pon {pon} statistics` | Laboratorio V1600G0-B (verificado) |
| `onu.set_description` | `V1600G0*` | `onu {onu} desc {description}` | Configuración guardada de la V1600G0-B |
| `onu.tcont` | `V1600G0*` | `onu {onu} tcont {tcont} name {tcont_name} dba {profile}` | Ídem |
| `onu.gemport` | `V1600G0*` | `onu {onu} gemport {gemport} tcont {tcont} gemport_name {gemport_name}` | Ídem |
| `onu.service_port` | `V1600G0*` | `onu {onu} service-port {service_port} gemport {gemport} uservlan {user_vlan} vlan {vlan} new_cos {cos}` | Ídem |
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
