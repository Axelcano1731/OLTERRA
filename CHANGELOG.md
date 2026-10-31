# Cambios

## [Sin publicar]

### Agregado

- **Gestión de ONU en las 3 marcas, probada en producción** (2026-10-31, con una ONU apagada por
  OLT que quedó exactamente como estaba): renombrar, eliminar (desautorizar), autorizar y guardar
  en VSOL, HSGQ y BCD; cambiar perfiles en la HSGQ (`ont modify`). En Monitoreo, el detalle de cada
  ONU tiene **Eliminar de la OLT** (con confirmación), **Cambiar perfiles** (HSGQ) y, al editar el
  nombre, **cambiarlo también en la OLT** (sin espacios ni tildes). API:
  `POST /v1/olts/{id}/onus/description` y `/onus/profiles`.

- **Ver la MAC de los equipos** en las 3 marcas: en Monitoreo, el detalle de cada ONU tiene
  **Ver MAC**, que pregunta a la OLT (`onu.mac`). VSOL: `show mac address-table pon <pon> <onu>`
  (la OLT lista el PON entero y se filtra); HSGQ: `show mac-address ont`; BCD: las MAC que
  aprende el puerto de la ONU (una ONU en modo router no aprende ninguna, y la tabla del PON de
  la BCD no dice de qué ONU es cada MAC). `mac.by_pon` ahora se interpreta en las 3 marcas.
  Verificado con capturas de Tocaima.

- **Usuarios** (menú, solo admin): el admin del ISP crea los usuarios de su ISP con una
  contraseña inicial que Olterra genera (se muestra una vez y obliga a cambiarla), les cambia el
  rol, los deshabilita (cierra sus sesiones) y les restablece la contraseña. Un admin no puede
  quitarse el rol ni deshabilitarse y el ISP no se queda sin admin activo. En la misma pantalla,
  el **nombre del ISP** (el "demo" de Tocaima puede llamarse Colombianet; el identificador interno
  no cambia). Migración `0013`.
- **Nombre del cliente desde el panel**: en Monitoreo, cada ONU tiene **Editar**; el nombre se
  guarda en Olterra (la OLT conserva su descripción), manda en la lista, la búsqueda y las
  alarmas, y se borra solo si en esa posición aparece otra ONU.
- Aprovisionar muestra también las OLT HSGQ y BCD: sus ONU nuevas y sus clientes, y un aviso de
  que el alta se habilita al probarla con una ONU de prueba.

- **Ver cada ONU en HSGQ y BCD**: `onu.detail` (estado, distancia, serial, nombre, perfiles,
  última conexión y caída), `onu.version` (fabricante, modelo, software) y, en la BCD,
  `onu.optical` (Rx, Tx, temperatura, láser). Verificados con capturas de las OLT de Tocaima;
  en Consultar aparecen como comandos por ONU. Los resultados se muestran con nombres en
  español.
- Escrituras de HSGQ (alta con perfiles, nombre, WAN PPPoE y Wi-Fi por OMCI, reinicio,
  borrado, guardar) y de BCD (alta, descripción, reinicio, borrado, guardar), sacadas de la
  ayuda de cada OLT y de su configuración. Quedan **sin verificar** (bloqueadas) hasta probarlas
  con una ONU de prueba. La HSGQ numera las ONU desde 0: los comandos por ONU restan 1.

### Cambiado

- El script del MikroTik agrega la plataforma al "Available From" del SSH también en
  RouterOS 7.24, donde la opción se llama `available-from` y `/ip service` lista además las
  conexiones activas (en Tocaima había que hacerlo a mano en Winbox). Si el SSH está abierto a
  todos (`0.0.0.0/0`), no se toca.
- El script del MikroTik redirige el 22 del túnel al puerto real de su SSH (Viotá lo tiene en
  el 52225): ya no hay que configurar el puerto en Olterra ni saber cuál es.
- El puerto SSH de un MikroTik se corrige en Túnel (**SSH → Cambiar**) sin rotarlo: el
  script ya importado sigue sirviendo (`PATCH /v1/tunnel/routers/{id}`). Pasó con Viotá, que
  tiene el SSH en el 2222.
- **El MikroTik del ISP es la puerta hacia sus OLT**: el script del router (se importa una sola
  vez) crea el usuario `olterra`, que solo entra por SSH desde la plataforma y no puede cambiar
  nada, y habilita el reenvío SSH. Olterra entra por él a la IP real de cada OLT, así que
  agregar, editar o borrar una OLT ya no pide rotar el router ni tocar el concentrador
  (migración `0012`). Los routers con el script viejo aparecen en Túnel como "Rótalo una vez".
- Olterra da de alta sola los MikroTik en el concentrador por la API de RouterOS
  (`OLTERRA_TUNNEL_HUB_API_PASSWORD`; el usuario lo crea `olterra-admin concentrador`). Sin esa
  variable queda el bloque para pegarlo a mano.
- Túnel pide el puerto SSH del MikroTik; la lista de OLT dice por qué MikroTik llega cada una.
- Se quitaron las reglas NAT por OLT de los scripts y `OLTERRA_TUNNEL_TRAP_RECEIVER`.

### Corregido

- VSOL: renombrar una ONU usa `onu N desc …`; `description` (la del manual) da "Unknown command"
  en las dos OLT reales.
- Un usuario deshabilitado ya no puede iniciar sesión (antes recibía token y luego cada
  petición lo rechazaba).
- Si la OLT no responde al abrir SSH o Telnet, el error dice "no respondió en 15 s" y sugiere
  el router del túnel (antes terminaba en "…:23:" sin explicación). Al agregar una OLT con IP
  privada y "Conexión directa", la interfaz avisa que sin túnel Olterra no llega a ella.
- OLT de 8 PON de Tocaima (V1.1.9R): `show onu state` con índice `1/1/<pon>:<onu>` ya se lee
  (el monitoreo veía 0 ONU), `0.00` en `show pon onu all rx-power` es "sin lectura" y no una
  alarma de señal alta, y "Copiar como plan" acepta la configuración de ONU sin línea
  `onu add` y servicios con `cos N/A`. `show pon <n> statistics` vacío ya no es un error.
- `onu.optical`, `onu.description`, `pon.statistics` y `profile.list` usan la sintaxis que
  aceptan las OLT reales (`optical_info`, `desc`, `show pon <n> statistics`,
  `show profile <tipo>`), no la del manual; verificados con capturas de dos OLT.

### Agregado

- OLT **HSGQ** (HSGQ-G008, V1.3.12C) y **BCD** (BCD-1508GS, V2.1.6, CLI tipo C-Data), de
  solo lectura: consultas, monitoreo (estado y señal de cada ONU) y respaldos. Se elige con
  **Marca** al agregar o editar la OLT (`driver` en la API; `/v1/olts/defaults` lista las
  marcas y cuáles admiten escrituras). Aprovisionar y "Cambiar clave" no aparecen en esas
  OLT y la API responde 400 si se piden. La HSGQ numera las ONU desde 0: Olterra le suma 1.
  Capturas de las OLT de Tocaima con nombres de clientes reemplazados; ver
  `docs/DRIVERS_HSGQ_BCD.md`. Cada driver trae sus parsers (`Driver.parsers`) y
  `olterra-capture` arma la captura desde el catálogo de cualquier driver.
- Acceso a la CLI de cada OLT por SSH o **Telnet** ("Acceso a la CLI" al agregar o editar la
  OLT; migración `0011`). Para las VSOL que no aceptan a los usuarios de la CLI por SSH, como
  la de 8 PON de Tocaima. El ejecutor entra por Telnet (negociación, `Login:`/`Password:` y
  aviso claro si rechaza la clave) y el script del router abre el puerto de la CLI de cada OLT
  (antes siempre el 22, aunque la OLT usara otro puerto SSH). El simulador también atiende
  Telnet. Ver DESPLIEGUE 6.1.1.
- `olterra-capture --telnet`: captura de laboratorio por Telnet desde la red del ISP cuando el
  SSH no responde.

- Mapa FTTH (pantalla **Mapa FTTH**, ya activa en el menú), con MapLibre y teselas de
  OpenFreeMap: ubicar la OLT, agregar cajas NAP, empalmes (CEO) y ODF con puertos, splitter, OLT
  y PON, dibujar cables (con su longitud) y postes, y poner cada cliente (ONU) en su puerto NAP.
  Cada caja se pinta según sus clientes en el monitoreo (caídos, llena, con puertos libres) y
  muestra la señal de cada uno. **Factibilidad**: cajas con puertos libres a menos de un radio de
  un punto. `/v1/map` (cajas, puertos, postes, cables, OLT y factibilidad). El técnico también
  edita el mapa (`map:write`). Migración `0010`. La CSP abre solo el dominio de las teselas
  (`OLTERRA_MAPA_ORIGEN`).

- Monitoreo (pantalla **Monitoreo**, ya activa en el menú): cada `OLTERRA_MONITOR_EVERY_MINUTES`
  (5) se consulta cada OLT con prioridad baja (`show interface brief`, `show onu state` y
  `show pon onu all rx-power` por PON) y se guarda el estado de cada ONU, su señal (una muestra
  por hora o al cambiar; 30 días) y las alarmas: ONU caída (sin luz, sin energía, desconectada),
  señal baja (−25 aviso, −27 crítica) o alta (−8), caída de PON con su causa probable y OLT sin
  respuesta. Se cierran solas. Gráfico de señal de 7 días por ONU. `GET /v1/monitoring/summary`,
  `/v1/monitoring/onus`, `/v1/monitoring/onus/{olt}/{pon}/{onu}`, `GET /v1/alarms` y
  `POST /v1/olts/{id}/monitoring/poll`. Migración `0009`.

- Respaldos de la configuración de la OLT (`show running-config`): solos cada
  `OLTERRA_BACKUP_EVERY_HOURS` (24) en cada OLT que ya respondió, y a pedido con **Respaldar
  ahora**. Se guardan comprimidos y cifrados con la llave del ISP, con las claves tapadas; un
  respaldo sin cambios no guarda otra copia. Pantalla **Respaldos** con la diferencia entre
  versiones y descarga. `GET/POST /v1/olts/{id}/backups`, `GET /v1/backups/{id}` y
  `/v1/backups/{id}/diff`. Migración `0008`.
- Bitácora en la interfaz (**Bitácora**, solo admin): quién hizo qué, cuándo y desde dónde, con
  el antes y el después; filtros por OLT y tipo. `GET /v1/audit` (permiso `audit:read`).
- El simulador VSOL responde `show running-config` completo.

- Cambiar la clave de la OLT desde Olterra (pantalla **Cambiar clave** en el detalle de la OLT,
  `POST /v1/olts/{id}/password`): la de acceso, la de enable o las dos. Es un trabajo que pone la
  clave nueva sin guardar, entra con ella en otra sesión y solo entonces guarda (`write`) y
  actualiza la credencial cifrada; si no puede entrar con la nueva no guarda nada y comprueba si
  la anterior sigue sirviendo. Las claves nuevas viajan selladas y se tapan en cualquier salida;
  la bitácora anota cuáles cambiaron, nunca su valor. Migración `0007`.
- Ejecutor: un paso CLI puede contestar preguntas que la OLT hace a mitad del comando
  (`PromptReply`: patrón de la pregunta, respuesta que puede ser un `{{secret:campo}}` y máximo de
  veces). Si la pregunta vuelve más veces, aborta con Ctrl-C. La V1600G0-B pide la clave nueva
  después de `user login-password <usuario>` (y su confirmación): `user.set_login_password` y
  `user.set_enable_password` pasan a esa forma. Siguen sin validar (modo laboratorio).
- El simulador VSOL entiende `user login-password` y `user enable-password` con la pregunta y la
  confirmación.

- Gestión remota de la ONU en el plan: usuario y contraseña de administración de la ONU (y la
  cuenta normal del cliente), nivel de firewall (o apagado), responder ping desde internet y qué
  servicios (HTTP, HTTPS, SSH, Telnet, FTP, TFTP) se abren desde la WAN; lo no marcado se cierra
  explícitamente. Se aplica en el alta y en "Internet y WiFi", y "Copiar como plan" la trae de
  una ONU que ya la tiene (sin las claves). Las contraseñas de la ONU se guardan cifradas en el
  plan (migración `0006`), viajan selladas al ejecutor y se tapan en cualquier salida. Comandos
  `onu.account_admin`, `onu.account_admin_user`, `onu.firewall` y `onu.acl`, con la sintaxis de
  la ayuda y la configuración guardada de la V1600G0-B.
- Usuarios: entrar con usuario y contraseña (scrypt, contraseña inicial que se cambia al entrar,
  bloqueo tras 5 intentos, sesiones que vencen, roles admin/tecnico/lectura).
  `./olterra.sh usuario` y `./olterra.sh clave`; migración `0005`. La llave de API queda para
  integraciones.
- Aprovisionamiento sin nada técnico: la pantalla busca sola las ONU nuevas en todos los PON y
  lista los clientes (`show interface brief`). Autorizar pide nombre del cliente, plan, PPPoE y
  WiFi; un trabajo de alta (`provision_jobs`) busca la posición libre, autoriza y guarda, espera
  a que la ONU se conecte, pone WAN y WiFi con los puertos de ese modelo y comprueba la señal.
  "Internet y WiFi", "Reiniciar", "Copiar como plan" y "Desautorizar" desde la lista de clientes.
- Alta en la V1600G0-B con WAN y WiFi: el alta manda `onu N pri equid <Equipment ID>` antes de
  los comandos `pri` (sin él la OLT respondía `Unsupport private protocol` y el paso salía
  «OK»). `POST /v1/olts/{id}/onus/configure` configura WAN y WiFi de una ONU ya autorizada sin
  volver a autorizarla. El alta y el borrado ya no leen el estado en el mismo plan; el resultado
  muestra los pasos de cambio de modo que fallan (`session_errors`).
- Estado de la OLT: cada consulta o escritura lo actualiza (responde / sin respuesta) y guarda
  cuándo respondió por última vez (`last_seen_at` en `OltOut`). Accesos directos **Consultar** y
  **Aprovisionar** en la lista de OLT.
- Aprovisionamiento de ONU (V1600G0-B): plantillas que se copian de una ONU que ya funciona o
  se llenan a mano (`/v1/provision-templates`, migración `0004`); alta con VLAN, PPPoE en la
  ONU y WiFi (`POST /v1/olts/{id}/onus/authorize`), reinicio y borrado. Las claves PPPoE y WiFi
  viajan solo selladas. Planes de escritura que se detienen al primer error, guardan y verifican;
  sin captura de laboratorio no corren, salvo `OLTERRA_ALLOW_UNVERIFIED_WRITES`. Pantallas
  `/plantillas` y `/olts/:id/aprovisionar`. El simulador entiende la sintaxis de aprovisionamiento.
- Interfaz web (`web/`, Vue 3 + Vite + Tailwind): panel, alta y consultas de OLT con su
  historial, túnel (scripts que se muestran una sola vez), conciliación desde archivos o con la
  demo. Ver `docs/INTERFAZ.md`.
- API: `GET /v1/me`, `GET /v1/olts/{id}/commands`, `GET /v1/olts/{id}/plans`,
  `GET /v1/reconciliations`, `POST /v1/reconciliations/files` y `POST /v1/reconciliations/demo`.
- Migración `0002`: historial de consultas por OLT y origen de cada conciliación.
- Despliegue de producción (`deploy/produccion/`): Caddy con HTTPS automático y CSP, roles de
  la base con claves generadas, respaldo diario con prueba de restauración y `./olterra.sh`
  para instalar, actualizar y crear ISP. Imágenes publicadas en GHCR. Ver
  `docs/DESPLIEGUE.md`.

- Concentrador: regla que no deja a los ISP del túnel entrar a los servicios del CHR (Winbox,
  SSH, API) por la IP del túnel.
- Túnel SSTP para RouterOS 6 (no tiene WireGuard): script del router con la CA de Olterra,
  servidor SSTP en el concentrador y aislamiento por lista de interfaces para los dos
  transportes. Al rotar, un router puede cambiar de versión y de transporte. Migración `0003`.

- Base de datos compartida: Olterra puede vivir en un esquema propio (`olterra`) de la base de
  Supabase de ISPWatch y Converza, con dos roles que no ven nada de lo demás
  (`deploy/produccion/base-compartida.sql`). El contenedor `db` pasa a ser opcional, el respaldo
  lleva solo ese esquema y `probar-respaldo` restaura en una base desechable. Pool de conexiones
  chico y configurable. Ver `docs/DESPLIEGUE.md`, 3.1.
- Actualización automática desde `main` (`./olterra.sh auto activar`): cada 5 minutos el
  servidor mira `main`, espera al CI en verde y se actualiza solo, sin llaves en GitHub ni
  puertos nuevos. Un commit que falla no se reintenta; las manuales y la automática comparten
  un candado. `./olterra.sh rama <rama>` pasa el servidor a seguir otra rama.
- Eliminar OLT (`DELETE /v1/olts/{id}`): borra la OLT, su credencial cifrada y su historial de
  consultas; la bitácora conserva quién la borró y cómo era. En la lista de OLT, **Editar** y
  **Eliminar** en cada fila (antes Editar solo estaba dentro del detalle).
- Editar OLT (`PATCH /v1/olts/{id}` y su pantalla): modelo, firmware, IP, puertos y
  credenciales, incluida la clave de enable. Las claves se vuelven a cifrar; la bitácora anota
  cuáles cambiaron, nunca su valor.
- Base para cambiar las claves de una OLT desde Olterra: un paso CLI puede llevar
  `{{secret:campo}}` y el ejecutor lo reemplaza con la credencial sellada (`new_password`,
  `new_enable_password`) justo al escribir en la OLT; la clave nueva no viaja en claro por NATS ni
  queda en el plan, y se tapa en cualquier salida. `build_credential_change_plan` arma el plan de
  escritura (se detiene al primer error, guarda con `write`) y se niega mientras la sintaxis no esté
  verificada en laboratorio. Todavía sin endpoint ni pantalla.
- Alta de OLT con la clave de fábrica de VSOL: si el cliente deja usuario y clave vacíos, el
  servidor usa `OLTERRA_VSOL_DEFAULT_USERNAME/PASSWORD` (solo en `.env`) y avisa que conviene
  cambiarla; `GET /v1/olts/defaults` dice si está configurada sin revelarla. La clave de enable
  la sigue pidiendo (es la que configura el cliente).
- Driver VSOL V1600G0-B V1.4.8R validado en laboratorio: comandos propios de `onu.optical`,
  `onu.description` y `pon.statistics` (el manual de otros modelos no aplica), nuevos
  `onu.state` y `onu.distance`, captura con `--modelo/--firmware-olt` y captura anonimizada en
  `tests/fixtures/vsol-gpon/V1600G0-B`. Ver `docs/DRIVERS_VSOL.md`.
- Alta de OLT: el modelo se escribe como sale en la web de la OLT (sugerencias con V1600G0-B)
  y el driver lo compara sin guiones (V1600G1-B = V1600G1B).

### Corregido

- Alta con una ONU cuyo modelo la OLT todavía no sabía (`NULL` en el autofind): se mandaba
  `pri equid NULL` y la ONU no tomaba la WAN. Ahora se usa el Equipment ID del detalle de la ONU
  conectada. Un "Internet y WiFi" repetido ya no crea una segunda WAN.
- El navegador rellenaba el usuario y la clave PPPoE con un usuario y clave que tenía guardados
  (una ONU real quedó con PPPoE `admin`): las claves PPPoE y WiFi del alta van ahora a la vista,
  sin campo de contraseña, y las claves de los formularios de OLT no se ofrecen para guardar
  (`autocomplete="new-password"`). El resultado del alta muestra el usuario PPPoE puesto.

- Alta en la V1600G0-B: no se manda `onu N profile onu` cuando repite el perfil de `onu add`
  (la OLT lo rechaza) y el autofind de ese modelo (`sn:` y tabulaciones) se interpreta.
- La salida de la OLT perdía columnas: la V1600G0-B coloca cada columna con el cursor
  (`
` + `ESC[nC`) y el normalizador las pisaba, así que `show onu info`, `state` y
  `rx_power` no se interpretaban. Ahora se emula el cursor.
- `olterra.sh` ya no puede ejecutar basura al final de `actualizar`: el `git pull` que trae una
  versión nueva del propio script cambiaba el archivo mientras bash lo leía.

- El ejecutor y el consumidor de resultados de la API morían a los pocos segundos sin
  trabajo: nats-py a veces lanza el `TimeoutError` de asyncio en vez del suyo y solo se
  atrapaba el suyo.

## [0.1.0] — sin publicar

Esqueleto de la fase 0.

- Conciliación OLT ↔ MikroTik ↔ CRM (`olterra-conciliar`) con 13 tipos de hallazgo, reportes
  HTML/CSV/JSON y fuentes CSV, RouterOS (texto y API), ISPWatch (API de socios) y capturas de OLT.
- Driver VSOL GPON: catálogo de comandos con fuente, parsers, OIDs SNMP y matriz de
  capacidades por modelo y firmware.
- Ejecutor genérico (SSH + SNMP) con colas por OLT, credenciales selladas y NATS JetStream.
- Captura de laboratorio (`olterra-capture`) y simulador de OLT VSOL por SSH.
- Generador del túnel WireGuard + NAT 1:1 para MikroTik y del concentrador.
- API FastAPI sobre PostgreSQL + PostGIS con Row Level Security por tenant y bóveda por sobre.
