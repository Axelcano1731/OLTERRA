# Plataforma OLT VSOL + Mapa FTTH — Arquitectura y plan de implementación

> 30 sept 2026 · Axel Cano · Última actualización: 2026-10-01 (Anexo A: fase 0)
>
> El cuerpo de este documento es el plan original. Lo que cambió o se aprendió al
> implementar la fase 0 está en el [Anexo A](#anexo-a--decisiones-y-hallazgos-de-la-fase-0),
> sin reescribir el plan.

## Resumen ejecutivo

**Recomendación:** un producto propio que una la gestión de OLT VSOL y el mapa FTTH en un solo modelo de datos. Sería la tercera pieza del ecosistema ISPWatch + Converza, sobre el mismo stack (Python/FastAPI, PostgreSQL, DigitalOcean, Cloudflare). *(Ver A.1: ISPWatch y Converza son Laravel.)*

AdminOLT gestiona la OLT y OZmap documenta la red. La apuesta es tener ambas cosas amarradas entre sí y al CRM: cada ONU ligada a su cliente, a su caja NAP y puerto, y al trazado de fibra que la alimenta.

Lo que esa unión permite:

- Autorizar una ONU y dejarla, en el mismo paso, vinculada al cliente de ISPWatch y a su caja NAP y puerto.
- Ante un corte, marcar en el mapa el tramo o splitter probable y listar los clientes afectados.
- Detectar documentación errada: potencia medida vs. calculada, y distancia de ranging de la OLT vs. longitud óptica del mapa.
- Factibilidad por WhatsApp: el prospecto envía su ubicación a Converza y recibe si hay caja con puertos libres cerca.
- Conciliar OLT ↔ MikroTik ↔ CRM de forma continua, lo que hoy resuelves con un script.

**Decisiones clave:**

- Monolito modular en FastAPI; PostgreSQL + PostGIS para inventario y mapa; VictoriaMetrics para potencias y tráfico; NATS JetStream para comandos y eventos.
- Conexión a las OLT sin abrir puertos: túnel WireGuard generado como script para el MikroTik del ISP (MVP) y agente saliente on-premise (fase 4).
- La lógica por modelo VSOL (comandos y parsers) vive en la nube; el ejecutor es genérico y casi nunca hay que actualizarlo donde el cliente.
- MVP solo VSOL GPON; la capa de drivers queda lista para EPON, HSGQ y TP-Link.
- MVP comercial en la semana 12, mapa FTTH en la 20, ingeniería de fibra y app de campo en la 30.

## Alcance funcional por módulos

La fase 1 cubre lo que un ISP le pide hoy a AdminOLT para VSOL GPON, más conciliación con MikroTik y CRM. El mapa FTTH entra en la fase 2 y la ingeniería de fibra en la 3.

| Módulo | Qué incluye | Fase |
|---|---|---|
| Conexión y descubrimiento | Alta de OLT por túnel o agente; lectura de modelo, firmware y puertos PON; importación de ONUs ya autorizadas, perfiles y VLANs | 1 |
| Autorización de ONUs | ONUs sin configurar (autofind), plantillas por plan (VLAN, perfil, bridge o router), autorizar, desautorizar, reiniciar, mover de puerto, acciones masivas | 1 |
| Credenciales | WiFi y WAN/PPPoE de la ONU; secretos PPPoE en el MikroTik BNG; claves de la OLT con rotación desde la plataforma | 1 |
| Monitoreo y alarmas | Estado, potencia RX/TX, distancia de ranging, tráfico por PON y uplink, CPU y temperatura; reglas de alarma a Telegram, correo y WhatsApp | 1 |
| Respaldo y auditoría | Backup diario de la configuración con diferencias entre versiones; bitácora de quién cambió qué, con antes y después | 1 |
| Conciliación | ONU sin cliente, cliente sin ONU, secreto PPPoE sin ONU, seriales cruzados entre OLT, MikroTik y CRM | 1 |
| Mapa FTTH | Postes, cables, cajas CEO y NAP, splitters, ODF; importar y exportar KMZ; ocupación de NAP; vínculo ONU ↔ puerto NAP; factibilidad | 2 |
| Ingeniería de fibra | Diagrama de fusiones por caja, trazado de hilos, presupuesto óptico, correlación de fallas, ubicador de eventos OTDR | 3 |
| App de campo | Instalación guiada: escanear serial, autorizar, medir potencia, foto de la caja, cerrar la orden; funciona sin señal | 3 |
| Escala | TR-069 con GenieACS, agente on-premise, drivers EPON, HSGQ y TP-Link, API pública y webhooks, WispHub y Mikrowisp | 4 |

El onboarding de un ISP debe tomar menos de una hora: conectar la OLT, importar lo existente y cruzar ONUs con clientes por serial o usuario PPPoE. Si importar cuesta trabajo, nadie migra.

## Conexión con las OLT VSOL

La plataforma nunca pide abrir la OLT a internet: todo entra por un túnel o un agente que sale desde la red del ISP.

- **Túnel (MVP):** la plataforma genera un script para el MikroTik del ISP que levanta WireGuard contra un concentrador propio; puede partir del CHR que ya corren en DigitalOcean. *(Ver A.4: RouterOS v6.)*
- **NAT 1:1 obligatorio:** el puerto AUX de toda VSOL sale de fábrica con 192.168.8.200 (VSOL). El script traduce cada OLT a una IP única del rango del ISP para que dos clientes no choquen. *(Ver A.3.)*
- **Agente (fase 4):** el mismo ejecutor en Docker dentro de la red del ISP, con conexión saliente TLS a NATS. Es para ISPs sin MikroTik o que no quieren tocar su router.

| Uso | Protocolo | Frecuencia inicial |
|---|---|---|
| Estado de ONUs | SNMP walk por tabla + traps | 60 s, y al instante por trap |
| Potencias RX/TX y distancia | SNMP walk por tabla | 5 min *(ver A.2: no en todos los modelos)* |
| Tráfico por PON, uplink y ONU | SNMP (contadores) | 5 min |
| Autorizar, perfiles, WAN, reinicios | CLI por SSH | A demanda |
| Configuración de la ONU (WAN, WiFi, claves) | OMCI vía OLT o TR-069 | A demanda |
| Respaldo | CLI: `show running-config` | Diario |

VSOL trae SSH2 activo y Telnet y SNMP bloqueados de fábrica, con listas de acceso por servicio y dos roles de usuario: Admin y Normal, de solo lectura (VSOL). El asistente de alta crea un usuario dedicado, habilita SNMP con comunidad propia o v3 y restringe SSH y SNMP a la IP del túnel.

Las ONU VSOL aceptan OMCI desde la OLT, TR-069 y protocolos privados (VSOL). Qué parámetros exactos (WiFi, WAN, clave de administración) se pueden tocar por OMCI en cada firmware se confirma en el laboratorio.

**Drivers en la nube, ejecutor genérico:**

- Un driver es plantillas de comandos + parsers + matriz de capacidades por modelo y firmware.
- El ejecutor solo abre sesiones SSH (prompts, paginación, timeouts), hace SNMP bulkwalk y recibe traps. Recibe planes y devuelve salida cruda.
- Corregir un parser o sumar un modelo es un despliegue en la nube; nada que actualizar donde el ISP.
- Cada escritura termina leyendo el estado para verificar y guardando en flash, porque VSOL pierde lo no guardado al reiniciar. Los guardados se agrupan para no escribir la flash en cada cambio.
- Una cola por OLT con prioridad: acción del usuario > aprovisionamiento > polling, y una sola sesión de escritura a la vez.

Mercado extra casi gratis: ISPbills maneja HIOSO y Syrotech con el mismo manejador SNMP y CLI de VSOL (ISPbills). Vale validarlo en el laboratorio.

**Laboratorio (fase 0):** una OLT VSOL GPON, un splitter 1:8, 4 a 6 ONUs (bridge y HGU con WiFi), un MikroTik como BNG y atenuadores ópticos para simular potencias. Cada salida de CLI capturada por modelo y firmware se vuelve una prueba automática de su parser.

## Modelo de la red FTTH

El mapa es un grafo guardado en PostgreSQL + PostGIS. Cada puerto y cada extremo de hilo es un nodo; hilos, fusiones, patch cords y salidas de splitter son aristas con su pérdida. Trazar una ONU hasta su puerto PON es una consulta recursiva sobre ese grafo.

| Entidad | Geometría | Atributos clave |
|---|---|---|
| Poste | Punto | Dueño (operador eléctrico, para conciliar el arriendo), altura, fotos, estado |
| Cable | Línea sobre postes | Modelo: hilos, tubos, código de colores TIA-598 o ABNT; longitud geográfica y óptica |
| Reserva técnica | Punto sobre el cable | Metros enrollados; se suman a la longitud óptica |
| Caja CEO o NAP | Punto | Tipo, capacidad, puertos, splitters internos, fotos, código QR |
| Splitter | Dentro de una caja | Balanceado 1:N o desbalanceado X/Y, pérdida por salida |
| ODF | En la cabecera | Puertos y patch cords hacia los puertos PON de la OLT |
| Acometida | Línea caja → cliente | Puerto NAP, ONU, cliente de ISPWatch |

Presupuesto óptico que el sistema calcula para cada ONU:

$$P_{RX} = P_{TX} - \left( \alpha L + \sum S_i + n_f F + n_c C \right)$$

α es la atenuación por km, L la longitud óptica, S cada splitter del camino, F cada fusión y C cada conector. Valores iniciales del catálogo, aproximados y editables por cada ISP:

| Componente | Pérdida de referencia |
|---|---|
| Fibra G.652D | 0,35 dB/km a 1310 nm; 0,25 dB/km a 1490 nm |
| Fusión | 0,1 dB |
| Conector SC/APC | 0,5 dB |
| Splitter PLC 1:2 · 1:4 · 1:8 · 1:16 | 3,7 · 7,3 · 10,5 · 13,8 dB *(ver A.10: faltan 1:32 y 1:64)* |

Cruces con los datos vivos de la OLT, que ningún mapa aislado puede hacer:

- **Medida vs. calculada:** más de 3 dB de diferencia marca conector sucio, fusión mala o curvatura.
- **Ranging vs. mapa:** si la distancia que reporta la OLT difiere mucho de la longitud óptica, la ruta está mal documentada.
- **Puerto PON:** si el mapa y la OLT no coinciden, la ONU queda marcada como inconsistente.
- **Correlación de fallas:** si varias ONUs caen a la vez, el sistema busca el ancestro común más bajo del árbol (splitter, caja o tramo) y lo marca como punto probable, con la lista de clientes afectados.
- **Ubicador OTDR:** el técnico ingresa la distancia del evento medida desde el ODF; el sistema recorre el trazado con longitudes ópticas, reservas incluidas, y marca el punto.
- **Alarma por defecto:** RX fuera de −8 a −27 dBm, la ventana de recepción de GPON clase B+.

El mapa entra por un importador KMZ de Google Earth con un asistente que convierte carpetas y estilos en postes, cajas y cables. También exporta a KMZ, para que nadie se sienta atrapado.

## Seguridad, multi-tenant y credenciales

Cada ISP es un tenant aislado en todas las capas, y ninguna clave de OLT, PPPoE o WiFi llega nunca al navegador en texto plano.

- **Base de datos:** Row Level Security de PostgreSQL por tenant en todas las tablas; la API filtra y la base vuelve a filtrar.
- **Mensajería:** una cuenta NATS por ISP; su ejecutor o agente solo ve sus propios canales.
- **Métricas:** el backend inyecta la etiqueta de tenant en cada consulta a VictoriaMetrics; el frontend nunca la consulta directo.
- **Túnel:** un peer WireGuard por ISP y firewall sin ruteo entre ISPs en el concentrador.
- **Usuarios:** roles Admin ISP, NOC, Soporte, Técnico y Solo lectura, con alcance por OLT o zona; 2FA obligatorio para quien escribe.
- **Bitácora:** quién, qué, cuándo y desde dónde, con antes y después; cada comando CLI guarda su salida con los secretos enmascarados.

| Credencial | Dónde vive | Cómo se cambia | Fase |
|---|---|---|---|
| Usuario y clave de la OLT | Bóveda de la plataforma | CLI, con rotación programada | 1 |
| WiFi de la ONU (SSID y clave) | ONU en modo router (HGU) | OMCI vía OLT si el firmware lo permite; si no, TR-069 | 1 o 4 |
| WAN PPPoE de la ONU | ONU en modo router | OMCI (WAN por protocolo privado VSOL) o TR-069 | 1 |
| Secreto PPPoE en modo bridge | MikroTik BNG | API de RouterOS | 1 |
| Clave web de la ONU | ONU | OMCI o TR-069 | 1 |

**Rotación de la clave de la OLT:** crear la nueva, probar login en una sesión aparte, guardar en flash y solo entonces actualizar la bóveda. Si la prueba falla, se revierte y se alerta.

Si el laboratorio muestra que el WiFi de las ONU VSOL no se puede cambiar por OMCI, GenieACS (TR-069) se adelanta a la fase 2.

**Bóveda:** cifrado por sobre con una llave por tenant y la llave maestra fuera de la base. El ejecutor recibe la credencial descifrada solo en memoria y solo para el comando en curso.

**Datos personales:** los clientes del ISP (nombre, dirección, ubicación) caen bajo la Ley 1581 de 2012. El ISP es responsable y la plataforma es encargada; hace falta política de tratamiento y contrato de transmisión con cada cliente.

## Integración con ISPWatch y Converza

La integración es el diferencial: el producto sabe quién es el cliente, en qué estado está su cuenta y por dónde le llega la fibra. Cada flujo usa la API pública y webhooks, así que también funciona con WispHub o Mikrowisp.

| Flujo | Disparador | Qué pasa |
|---|---|---|
| Alta de cliente | Venta en ISPWatch | Orden de instalación con la NAP sugerida; el técnico autoriza la ONU desde la app con plan, VLAN y PPPoE ya llenos; queda ligada a cliente y puerto NAP |
| Corte y reconexión | Mora o pago en ISPWatch | Suspende o reactiva el secreto PPPoE en el MikroTik o el servicio en la ONU; todo queda en la bitácora |
| Falla masiva | La correlación detecta un splitter o tramo caído | Ticket automático en ISPWatch y aviso por WhatsApp solo a los clientes afectados, con tiempo estimado |
| Cambio de clave WiFi | El cliente lo pide por WhatsApp | El bot de Converza valida identidad, recibe la nueva clave y la aplica en la ONU |
| Factibilidad | Un prospecto envía su ubicación por WhatsApp | Converza consulta la API: si hay NAP con puertos libres en el radio definido, agenda la visita |
| Soporte nivel 1 | "No tengo internet" por WhatsApp | El agente ve en Converza el estado de la ONU, su potencia y las alarmas de la zona antes de responder |
| Conciliación | Cada noche | Descuadres OLT ↔ MikroTik ↔ CRM con acción sugerida para cada uno |

Converza ya corre sobre n8n, así que los flujos de WhatsApp llaman la API del producto sin acoplamiento interno. Identidad única para los tres productos y el combo pasa de dos piezas a tres.

No amarrar el producto a ISPWatch: un ISP que factura con WispHub debe poder comprarlo solo. La integración con ISPWatch es la mejor, no la única, y cada venta suelta abre la puerta al combo.

## Riesgos y mitigación

El mayor riesgo no es técnico: VSOL ya tiene su propia nube de gestión, VSOL INCE. Integra EMS, ACS TR-069 y MQTT, soporta OLT de terceros, tiene región suramericana y pago por uso, y VSOL reporta más de 26.000 OLT gestionadas.

| Riesgo | Mitigación |
|---|---|
| INCE cubre la gestión básica de OLT y ONU | No competir ahí: vender mapa FTTH de planta, integración con CRM, MikroTik y WhatsApp, soporte local y cobro en COP. Probar INCE a fondo en la fase 0 como referencia |
| Un firmware nuevo cambia comandos o salidas | Matriz de capacidades por firmware, pruebas con salidas reales y alerta cuando un parser no reconoce una respuesta |
| El polling sobrecarga la CPU de la OLT | SNMP por tabla, una sola sesión de escritura, límites por OLT e intervalos que se alargan si la OLT responde lento |
| Una acción masiva equivocada tumba un PON | Vista previa de comandos y conteo antes de ejecutar, permisos por rol y respaldo automático antes de cada cambio masivo |
| Depender de un solo fabricante | Drivers HSGQ y TP-Link en la fase 4. Vigilar además la estrategia de VSOL: en septiembre de 2026 publicó un comunicado sobre la adquisición de una participación de control por parte de Star-net |
| ISPs sin documentación de red | Valor sin documentar todo: cargar primero las cajas NAP y ligar ONUs; importar KMZ; capturar el resto en campo con la app |
| Capacidad del equipo fundador | Alcance estrecho (solo VSOL GPON en el MVP), app móvil hasta la fase 3, mismo stack que ISPWatch *(ver A.1)* |
| Una brecha da control sobre redes de muchos ISPs | Todo lo de la sección de seguridad, más pentest externo antes de abrir ventas |
| Licencia del mapa satelital | Usar un proveedor licenciado (MapTiler o Esri); las teselas de Google no se pueden usar fuera de su API |

## Nombres

Recomiendo **Olterra**: OLT + tierra, que es justo lo que el producto une, y se pronuncia igual en español, portugués e inglés. Frase de marca: "Tu OLT, tu fibra y tus clientes en un solo mapa".

| Nombre | Idea | A favor | En contra |
|---|---|---|---|
| Olterra | OLT + terreno | Inventado, fácil de registrar, sirve para Brasil | Hay que explicarlo una vez |
| Fibranza | Fibra + confianza | Suena hermano de Converza | Menos obvio que es para OLT |
| Trazo | Trazar la fibra de punta a punta | Corto, español, evoca el mapa | Palabra común; registrar como "Trazo Fibra" |
| Lumbra | De vislumbrar: ver toda la red | Evoca luz y visibilidad | Poco descriptivo |
| Vigía | El "watch" de ISPWatch en español | Familia con ISPWatch | Suena a monitoreo, no a mapa |
| Hilo | Cada hilo, cada cliente | Memorable | Muy usado; registro difícil |
| PONWatch | Familia directa con ISPWatch | SEO inmediato | Descriptivo, marca débil y amarrada a ISPWatch |

Antes de decidir: búsqueda de antecedentes en la SIC (clases 9 y 42), dominios .com y .co, y usuarios en Instagram, TikTok y YouTube.

## Próximos pasos

La fase 0 cierra el 14 oct 2026 con el laboratorio andando, INCE evaluado y tres pilotos confirmados. El estado de cada punto y cómo hacerlo está en [LABORATORIO.md](LABORATORIO.md).

- [ ] Montar el laboratorio: OLT VSOL GPON, splitter, 4 a 6 ONUs (bridge y HGU), MikroTik como BNG y atenuadores.
- [ ] Capturar salidas reales de CLI y SNMP por modelo y firmware: autofind, lista de ONUs, potencias, WAN y WiFi. *(Herramienta lista: `olterra-capture`.)*
- [ ] Confirmar qué se cambia por OMCI en ONUs VSOL (WiFi, WAN, clave web) y decidir si GenieACS se adelanta.
- [ ] Crear cuenta en VSOL INCE, conectar la OLT de laboratorio y anotar qué hace y qué no.
- [x] Prototipo del script WireGuard + NAT 1:1 para MikroTik contra el CHR. *(Generador listo y probado; falta correrlo contra el CHR y un MikroTik reales.)*
- [x] Esqueleto FastAPI + PostGIS con RLS por tenant y el ejecutor conectado a NATS.
- [x] Convertir el script de conciliación OLT ↔ MikroTik ↔ CRM en el primer demo para pilotos. *(`olterra-conciliar`; ver A.9.)*
- [ ] Cerrar 3 ISPs piloto con VSOL, idealmente clientes de ISPWatch, con acuerdo de datos firmado.
- [ ] Elegir nombre y hacer la búsqueda en la SIC y de dominios.

---

## Anexo A — Decisiones y hallazgos de la fase 0

Lo que se decidió o se descubrió al construir el esqueleto (1 oct 2026). Cada punto dice
qué cambia respecto al plan.

### A.1 El stack no es "el mismo de ISPWatch"

ISPWatch y Converza son **Laravel (PHP) + Vue**. El único precedente en Python es el monitor
de The Dude (`AUTOMATIZACION-REPORTE`), con un gateway FastAPI. Python sigue siendo la
elección correcta para Olterra: SSH interactivo (asyncssh), SNMP (pysnmp), NATS y la
criptografía son mucho más sólidos que en PHP. Pero la mitigación del riesgo "capacidad del
equipo fundador" es menor de lo que dice la tabla: es un stack nuevo en producción. Conviene
que la integración con ISPWatch siga siendo por su API de socios (contrato publicado) y no
por base compartida.

### A.2 El monitoreo SNMP por ONU depende del modelo y del firmware

Fuentes públicas (sin laboratorio aún):

- **V1600GS** (fw V1.2.0/V4.0.0): expone por SNMP estado, serial, potencias y distancia de
  cada ONU bajo `.1.3.6.1.4.1.37950.1.1.6.1.1`, y una interfaz por ONU en IF-MIB
  (`GPON01ONU1`, alias `GPON0/1:1`). Fuente: LibreNMS PR #19368 (sin fusionar).
- **V1600G1B** (fw V1.4.4R): ese subárbol no existe; un recorrido completo no trae filas.
  Las potencias por ONU solo salen por CLI (`show pon onu all rx-power`). Fuente: snmprec
  de LibreNMS PR #19850.

Consecuencia: la fila "Potencias RX/TX y distancia → SNMP cada 5 min" no vale para todos. El
driver tiene una matriz de capacidades por modelo y firmware (`drivers/vsol_gpon/capabilities.py`)
y el alta de cada OLT (fase 1) debe **sondear** qué tablas responde y guardarlo. Donde no hay
SNMP por ONU, el sondeo por CLI cuesta CPU de la OLT: intervalos más largos y una sola sesión.

### A.3 La IP de fábrica no es una sola

El plan dice 192.168.8.200 (y así lo dicen las guías públicas); el manual CLI v2.1 de VSOL
dice 192.168.8.100 para el puerto de gestión fuera de banda. El generador del túnel **nunca
supone la IP**: la toma como dato de cada OLT.

### A.4 RouterOS v6 no tiene WireGuard

WireGuard existe desde RouterOS 7.1. El script verifica la versión primero, con sintaxis que
también corre en v6, y se detiene con un mensaje claro. ISPWatch ya vivió esto (transporte
dual: WireGuard en v7, L2TP/IPsec endurecido en v6).

**Decidido (2026-10-02): RouterOS 6 va por SSTP.** Muchos ISP tienen routers v6 y no se les
puede exigir actualizar. No se copia el L2TP/IPsec de ISPWatch porque el concentrador es el
mismo CHR: un router v6 que ya tiene el L2TP de ISPWatch no puede abrir un segundo L2TP al
mismo servidor (las políticas IPsec chocan), y Olterra tendría que repartir la clave IPsec
de ISPWatch. SSTP es PPP sobre TLS por TCP (4443; el 443 del CHR ya está ocupado): es solo de
Olterra, convive con el L2TP de ISPWatch, pasa NAT y CGNAT, y cada router tiene su usuario y
clave. La CA de Olterra va dentro del script, así el router solo habla con el concentrador
real. El aislamiento entre ISP se hace sobre una lista de interfaces que reúne WireGuard y
cada túnel SSTP. Un router dado de alta con la versión equivocada cambia de transporte al
rotar su credencial, sin perder su IP en el túnel.

Otras lecciones de ISPWatch que el script ya aplica: el `listen-port` del router no se fija
(13231 lo usa Back To Home), las llaves las genera la plataforma (no hay huevo y gallina), y
correrlo dos veces no duplica nada.

### A.5 Plan de direcciones

- Plataforma (concentrador, ejecutores, receptor de traps): `198.18.0.0/24`.
- Routers de cada ISP: un `/28` por tenant dentro de `198.18.0.0/16`.
- IP única por OLT (NAT 1:1): un `/26` por tenant dentro de `198.19.0.0/16`.

198.18.0.0/15 es el rango de pruebas de RFC 2544: casi ningún ISP lo enruta, a diferencia de
10/8 (pools), 100.64/10 (CGNAT), 172.16/12 (gestión; el overlay de ISPWatch usa 172.18.x) y
192.168/16 (las OLT de fábrica). Así se puede compartir el CHR con ISPWatch sin choques. Los
bloques se derivan de un `net_index` por tenant: las colisiones son imposibles por
construcción y no hace falta mirar datos de otros tenants. Límite actual: 1024 tenants, 14
routers y 64 OLT por tenant (configurable).

En el concentrador, la plataforma llega a todos los ISP y los ISP no se ven entre sí (reglas
de `olterra-admin concentrador`). Las traps salen de cada OLT con su IP única (src-nat en el
MikroTik), así el receptor sabe de qué OLT vienen aunque todas sean 192.168.8.x.

### A.6 Credenciales selladas para el ejecutor

Los planes quedan guardados en JetStream (en disco) hasta que el ejecutor los toma; mandar la
clave de la OLT en claro ahí contradice "descifrada solo en memoria". Cada credencial va
**sellada con la llave pública X25519 del ejecutor** y amarrada al plan (el id del plan es
parte del cifrado): copiarla a otro plan no sirve. Esto mismo sirve para los agentes
on-premise de la fase 4: cada uno con su par de llaves.

### A.7 Aislamiento en la base

- Dos roles: `olterra_owner` (migraciones y administración) y `olterra_app` (API y workers,
  **sin** BYPASSRLS).
- El tenant se fija por transacción (`olterra.tenant_id`); sin él no se ve nada.
- Las **llaves foráneas no respetan RLS**: con una FK simple, una OLT del ISP A podía apuntar
  al router del ISP B si alguien adivinaba el UUID. Todas las FK entre tablas de tenant son
  compuestas `(tenant_id, id)`.
- La bitácora es solo-anexar para la aplicación (sin UPDATE ni DELETE).
- Las llaves de API llevan el tenant adentro: se verifican ya dentro de RLS, sin funciones que
  se salten el aislamiento.

Todo esto está probado contra PostgreSQL 17 + PostGIS real (`tests/integration/test_rls.py`).

### A.8 La llave privada WireGuard del router no se guarda

La plataforma la genera, la entrega en el script una vez y guarda solo la pública. Si hace
falta el script otra vez, se rotan las llaves (`POST /v1/tunnel/routers/{id}/script`).

### A.9 Conciliación

- El script de conciliación que se usa hoy no estaba en este equipo; el motor se escribió
  de cero. Si existe en otro lado, vale compararlo con `reconciliation/engine.py`.
- La API de socios de ISPWatch **no expone seriales de ONU** (por diseño). Con ISPWatch, el
  vínculo ONU ↔ cliente sale de la red: el usuario PPPoE configurado en la ONU (HGU) o la MAC
  que el MikroTik ve como caller-id detrás de la ONU (bridge). El hallazgo
  `serial_por_registrar` deja el serial listo para cargarlo en el CRM.
- Detecta usuarios PPPoE casi iguales (espacios, mayúsculas, tildes, caracteres invisibles):
  el caso real de la guía de migración a PPPoE, donde `DANIELA _PARADA` no autenticaba contra
  `DANIELA_PARADA`.

### A.10 Pendientes que el plan no cubría

- Splitters 1:32 y 1:64 en el catálogo de pérdidas (comunes en FTTH de dos niveles: 1:8 × 1:8).
- Verificar después de escribir y agrupar los guardados en flash: entra con las primeras
  escrituras (fase 1). Hoy la API solo expone lecturas a la OLT.
- Identidad única entre los tres productos: falta decidir quién es el proveedor de identidad.
  La fase 0 usa llaves de API por tenant.
- Las credenciales de fábrica de la OLT están en el manual público: el alta debe cambiarlas.

### A.11 Interfaz web

- **Vue 3 + Vite + Tailwind 4**, como Converza, para que el equipo y los componentes se
  reutilicen entre productos. Es una SPA aparte (`web/`) que consume la API; en producción va
  bajo el mismo dominio, sin CORS. Detalle en [INTERFAZ.md](INTERFAZ.md).
- Mientras no haya proveedor de identidad (A.10), se entra con la llave de API del ISP. Es lo
  más débil de hoy: la llave vive en el navegador. Los usuarios con roles y 2FA del plan
  esperan esa decisión.
- MapLibre entra con el mapa FTTH (fase 2), no antes: sin datos de planta no hay qué mostrar.
- Al conectar la interfaz con un ejecutor real apareció un error de la fase 0: el ejecutor y el
  consumidor de resultados de la API morían a los pocos segundos sin trabajo (nats-py a veces
  lanza el `TimeoutError` de asyncio y solo se atrapaba el suyo). Corregido y con prueba.

### A.12 Despliegue

- **Un droplet con Docker Compose**, no Kubernetes: un servidor alcanza para los pilotos y
  cabe en lo que el equipo ya opera. Todo con `deploy/produccion/olterra.sh`
  ([DESPLIEGUE.md](DESPLIEGUE.md)).
- **Caddy** sirve la interfaz y la API bajo un dominio, con HTTPS automático: sin CORS y con
  una sola cosa expuesta. La base y NATS no publican puertos.
- Las imágenes se publican en **GHCR** desde `main`; el servidor solo las baja. Se pueden
  construir en el servidor (`OLTERRA_CONSTRUIR=1`) si hace falta.
- Los respaldos se prueban restaurándolos (`./olterra.sh probar-respaldo`), y CI instala el
  stack de producción completo en cada PR: así un cambio que rompa el despliegue no llega a
  `main`.
- El ejecutor de la nube llega a las OLT como un peer más del concentrador (`198.18.0.2`).
  Probado el 2026-10-02 contra el CHR de ISPWatch, que se comparte: Olterra usa su propia
  interfaz y el puerto 13232 (ISPWatch tiene el 13231), y sus reglas solo actúan sobre ese
  túnel. De ahí salió una regla nueva del concentrador: los ISP del túnel no entran a sus
  servicios (el firewall de ese CHR solo descartaba lo que llega por la WAN). Falta el
  primer MikroTik de un ISP.

### A.14 Aprovisionamiento de ONU

- **Plantillas copiadas de una ONU real.** El ISP ya tiene ONU funcionando; Olterra lee la
  configuración de una (`show running-config onu N`), separa lo común (perfiles, VLAN, WAN, WiFi)
  de lo del cliente y lo guarda como plantilla. También se pueden llenar a mano. La plantilla no
  guarda nada del cliente ni claves.
- **Escrituras con freno.** Un plan de escritura se detiene al primer paso fallido, guarda en
  flash (`write`) y termina leyendo el estado del PON. Un comando sin captura de laboratorio
  (`verified=False`) no corre, salvo en modo laboratorio (`OLTERRA_ALLOW_UNVERIFIED_WRITES`), y
  en ese caso queda dicho en la respuesta y en la bitácora.
- **Claves del cliente selladas.** PPPoE y WiFi viajan en la credencial sellada al ejecutor; los
  pasos llevan `{{secret:campo}}`, que el ejecutor resuelve justo al escribir en la OLT. Ni NATS,
  ni `plan_runs`, ni la bitácora, ni la API las ven.
- **El operador no ve nada técnico.** Un alta es un trabajo (`provision_jobs`, `api/jobs.py`)
  que avanza solo: busca la posición libre y el Equipment ID, autoriza y guarda el servicio,
  espera a que la ONU se conecte (en la V1600G0-B la WAN y el WiFi responden "Unsupport private
  protocol" si no), lee cuántos puertos tiene ese modelo, pone PPPoE y WiFi y comprueba la señal.
  Lo empuja cada resultado de plan y un reloj en la API; si la API se reinicia, el reloj lo
  retoma. El nombre del cliente y del WiFi se limpian solos (tildes y espacios).
- Pendiente: acciones masivas, mover de puerto, ligar la ONU al cliente de ISPWatch y a su NAP.

### A.15 Usuarios

- Usuario y contraseña propios de Olterra mientras no haya identidad común con ISPWatch y
  Converza (A.10). El usuario es único en toda la plataforma: quien entra no escribe su ISP.
- Contraseñas con scrypt; contraseña inicial que se cambia al entrar; bloqueo por intentos;
  sesiones `ols_…` con el tenant adentro (como las llaves) y vencimiento.
- Las personas entran con usuario; las llaves de API quedan para integraciones.

### A.13 Base de datos compartida con ISPWatch y Converza

- **Decisión (2026-10-03):** por ahora Olterra usa la misma base de Supabase que ISPWatch y
  Converza, en su propio esquema (`olterra`) y con dos roles propios. Es lo que ya hacen esos dos
  productos entre sí (`ispwatch_dev`, `converza`). Separar las bases queda para cuando haga falta.
- **Por qué es seguro hacerlo así:** la separación es por permisos, no por buena voluntad. Los
  roles de Olterra no leen ninguna tabla de otro sistema, y los roles que Supabase expone por su
  API no entran al esquema. El runtime del servidor solo conoce `olterra_app`; la clave de
  administrador se usó una vez y no se guardó.
- **Lo que se paga:** las conexiones son compartidas (60 en total; ISPWatch y Converza usaban 20),
  así que el pool de Olterra es chico; un problema de rendimiento en una de las apps se siente en
  las otras; y las migraciones de Olterra corren sobre una base con datos de producción de otros.
  Mientras tanto, el respaldo de Olterra es propio (solo su esquema).
- **Separar después** es mover un esquema: `pg_dump -n olterra` a un proyecto nuevo y cambiar el
  `.env`. Las tablas ya están aisladas, y por eso el costo de separar es bajo.
