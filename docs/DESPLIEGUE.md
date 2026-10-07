# Despliegue en producción

> Última actualización: 2026-10-03

Olterra corre en **un servidor con Docker** (un droplet de DigitalOcean) detrás de
**Cloudflare DNS**, como dice el plan. Todo lo de producción está en `deploy/produccion/`
y se maneja con un solo comando, `./olterra.sh`.

```
Internet ──443──▶ Caddy (web) ──/v1──▶ API ──▶ PostgreSQL + PostGIS (propio o Supabase)
                     │                   │
                     └─ interfaz         └──▶ NATS ──▶ ejecutor ──túnel WireGuard──▶ CHR ──▶ MikroTik del ISP ──▶ OLT
```

| Servicio | Qué es | Expuesto |
|---|---|---|
| `web` | Caddy: la interfaz, HTTPS automático (Let's Encrypt) y `/v1`, `/docs`, `/health` hacia la API | 80 y 443 |
| `api` | FastAPI (`OLTERRA_ENV=prod`) | No |
| `executor` | El ejecutor: sesiones SSH y SNMP hacia las OLT | No |
| `db` | PostgreSQL 17 + PostGIS propio, roles con claves generadas. **Solo con la base propia** (perfil `db-local`); con una compartida, como la de Supabase, no corre (3.1) | No |
| `nats` | NATS JetStream (sin cuentas todavía: no se expone) | No |
| `backup` | `pg_dump` al arrancar y cada 24 h en `respaldos/`; guarda 14 días | No |
| `migrate` | Migraciones y `olterra-admin` (no queda corriendo) | No |

Cada servicio recibe solo sus claves: la llave privada del ejecutor no llega a la API y la
llave maestra no llega al ejecutor.

## 1. El servidor

1. **Droplet**: Ubuntu 24.04 con Docker Engine (repositorio oficial de Docker). **1 GB de RAM
   con 2 GB de swap** alcanza para el piloto: probado el 2026-10-02, todo corriendo usa ~600 MB
   y construye las imágenes en el servidor. Con ISP reales, 2 GB para tener holgura. En la
   **misma región que el CHR** del túnel, para que la latencia hacia las OLT sea la menor.
2. **Firewall de DigitalOcean** (Networking → Firewalls), entrada: TCP 22 solo desde las IP
   de administración; TCP 80 y 443 y UDP 443 desde todos. Salida: todo.
3. **Respaldos del droplet** activados (semanales): son la segunda red, no la única.
4. Un usuario con `sudo` en el grupo `docker`; nada de trabajar como root por SSH.

## 2. El dominio (Cloudflare)

1. Registro `A` del dominio (por ejemplo `olterra.tuisp.co`) hacia la IP del droplet.
2. Empezar en **"DNS only"** (nube gris): así Caddy obtiene el certificado de Let's Encrypt
   sin intermediarios.
3. Si después se quiere el proxy de Cloudflare (nube naranja), SSL/TLS en **Full (strict)**.
   La bitácora guardará entonces las IP de Cloudflare a menos que se configure
   `trusted_proxies` en el `Caddyfile`.

## 3. Instalar

```bash
git clone https://github.com/Axelcano1731/OLTERRA.git /opt/olterra
cd /opt/olterra/deploy/produccion
./olterra.sh instalar olterra.tuisp.co
```

`instalar`:

1. Genera `.env` con permisos 600: claves de PostgreSQL al azar, **llave maestra** de la
   bóveda y par de llaves del ejecutor (con `olterra-admin`). Nunca pisa un `.env` existente.
2. Baja las imágenes de GHCR (`ghcr.io/axelcano1731/olterra-api` y `olterra-web`, etiqueta
   `main`). Con `OLTERRA_CONSTRUIR=1` las construye en el servidor desde el repo: queda en
   `.env` y `actualizar` trae el código nuevo con `git pull`.
3. Arranca la base (que crea sus roles en el primer arranque), corre las migraciones y
   levanta todo.

Después:

```bash
./olterra.sh isp mi-isp "Mi ISP"     # crea el ISP y su primera llave de API (se muestra una vez)
```

Con esa llave se entra a `https://olterra.tuisp.co`.

> **Guarda `.env` en un gestor de claves** apenas se cree. Si se pierde `OLTERRA_MASTER_KEY`,
> las credenciales guardadas (OLT, PPPoE) no se pueden volver a leer, ni desde un respaldo.

### 3.1 Base de datos compartida (Supabase)

ISPWatch y Converza ya usan **la misma base de Supabase**, cada uno en su esquema
(`ispwatch_dev`, `converza`…). Olterra se suma con el suyo, `olterra`, y dos roles propios,
sin tocar nada de lo demás. Es la decisión de partida; separar las bases queda para después
(ARQUITECTURA, A.13).

**Qué crea** `deploy/produccion/base-compartida.sql`, una sola vez:

| Qué | Para qué |
|---|---|
| Esquema `olterra` | Todas las tablas de Olterra. Sin acceso para PUBLIC ni para `anon` y `authenticated` (los roles que Supabase expone por su API) |
| Rol `olterra_owner` | Dueño del esquema: migraciones, `olterra-admin` y respaldos. Tiene `BYPASSRLS` como en la base propia, pero **no tiene permiso sobre ninguna tabla de otro sistema** |
| Rol `olterra_app` | La API. Sin `BYPASSRLS`: todo pasa por las políticas de cada ISP |
| `search_path = olterra, public` | Las tablas caen en `olterra`; `public` queda solo para PostGIS |
| Tope de conexiones (6 y 12) | La base permite 60 en total y ISPWatch y Converza ya usan una parte |

La clave del administrador (`postgres` de Supabase) **se usa una vez y no se guarda en ningún
archivo ni en el servidor**: el servidor solo conoce `olterra_owner` y `olterra_app`. Si el
servidor se compromete, ISPWatch y Converza no quedan expuestos.

```bash
psql "host=aws-0-us-east-1.pooler.supabase.com port=5432 dbname=postgres user=postgres.<proyecto> sslmode=require" \
  -v owner_password="$(openssl rand -hex 24)" -v app_password="$(openssl rand -hex 24)" \
  -f deploy/produccion/base-compartida.sql
```

(Guardar las dos claves: van al `.env`. Para generarlas aparte y verlas, usar variables y no
la línea de comandos.) Después, en `.env`:

```
OLTERRA_DB_HOST=aws-0-us-east-1.pooler.supabase.com
OLTERRA_DB_PORT=5432
OLTERRA_DB_NAME=postgres
OLTERRA_DB_QUERY=?ssl=require
OLTERRA_DB_SSLMODE=require
OLTERRA_DB_APP_USER=olterra_app.<proyecto>
OLTERRA_DB_OWNER_USER=olterra_owner.<proyecto>
OLTERRA_DB_SCHEMA=olterra
OLTERRA_APP_PASSWORD=<la de olterra_app>
OLTERRA_OWNER_PASSWORD=<la de olterra_owner>
```

Con `OLTERRA_DB_HOST` apuntando afuera, el contenedor `db` no corre. Después,
`./olterra.sh actualizar` aplica las migraciones en el esquema `olterra`.

Cosas que importan:

- **Puerto 5432 del pooler** (modo sesión), no el 6543 (modo transacción): la API usa
  sentencias preparadas. Los usuarios del pooler llevan el proyecto: `olterra_app.<proyecto>`.
- **Conexiones**: cada proceso abre pocas (`OLTERRA_DB_POOL_SIZE=3` y
  `OLTERRA_DB_MAX_OVERFLOW=2`). Antes de subirlas, mirar cuántas usan ISPWatch y Converza.
- **El esquema `olterra` no debe aparecer en "Exposed schemas"** de la API de Supabase
  (Settings → API). Hoy no aparece, y aunque apareciera `anon` no tiene permisos; pero no hay
  por qué exponerlo.
- La conexión va con TLS al pooler (`sslmode=require`, que falla si no hay cifrado).

**Pasar una instalación que ya corre con base propia a la compartida** (se hizo el
2026-10-03; la base propia queda parada, con su volumen, para volver atrás):

1. Crear el esquema y los roles (arriba) y correr las migraciones desde cualquier lado con
   `OLTERRA_MIGRATIONS_DATABASE_URL` del dueño nuevo: `olterra-admin migrar`.
2. Respaldo (`./olterra.sh respaldo`) y copia del `.env` (`cp -p .env .env.antes`).
3. Detener la API y el ejecutor. Volcar **solo los datos** de la base propia, pasarlos al
   esquema nuevo y cargarlos en **una transacción**:

   ```bash
   docker exec olterra-db-1 pg_dump -U olterra_owner -d olterra --data-only --column-inserts \
       --no-owner --exclude-table=alembic_version \
     | sed -E "/^SET transaction_timeout/d; s/^INSERT INTO public\./INSERT INTO olterra./; s/^(SELECT pg_catalog\.setval\(')public\./\1olterra./" \
     | docker run --rm -i -e PGPASSWORD postgis/postgis:17-3.5 psql -q -v ON_ERROR_STOP=1 \
         --single-transaction "host=… user=olterra_owner.<proyecto> dbname=postgres sslmode=require"
   ```

   (`transaction_timeout` es de PostgreSQL 17; Supabase tiene 15.) Comparar `count(*)` de cada
   tabla en las dos bases.
4. Poner las variables de arriba en `.env` y correr `./olterra.sh actualizar`.
5. Comprobar con una escritura por la API y mirando las conexiones de cada base. Detener el
   contenedor `db` (`docker stop olterra-db-1`).

**Volver a la base propia**: `cp -p .env.antes .env`, `docker start olterra-db-1` y
`./olterra.sh actualizar`. Ojo: lo escrito en Supabase después del cambio no vuelve.

## 4. El día a día

| Comando | Qué hace |
|---|---|
| `./olterra.sh estado` | Contenedores y `/health` |
| `./olterra.sh actualizar` | Respaldo, imágenes nuevas, migraciones y reinicio |
| `./olterra.sh rama <rama>` | El servidor pasa a seguir esa rama (`main`) y actualiza |
| `./olterra.sh auto activar` / `desactivar` / `estado` | Actualización automática desde `main` (sección 4.1) |
| `./olterra.sh dominio <dominio>` | Cambia el dominio: el DNS tiene que apuntar al servidor; Caddy pide el certificado |
| `./olterra.sh isp <slug> "<Nombre>"` | Un ISP nuevo con su llave |
| `./olterra.sh llave <slug> <nombre>` | Otra llave para un ISP |
| `./olterra.sh respaldo` | Un respaldo ahora (además del diario) |
| `./olterra.sh probar-respaldo` | Restaura el último respaldo en una base aparte y lo revisa |
| `./olterra.sh registros [servicio]` | Últimos registros |

**Publicar una versión**: mergear el PR a `main`. Con la actualización automática (sección
4.1) el servidor se actualiza solo; sin ella, `./olterra.sh actualizar`. El workflow `Imágenes`
además publica las dos imágenes en GHCR con la etiqueta `main`, que se usan cuando el servidor
no construye (`OLTERRA_CONSTRUIR=0`). Para fijar una versión, etiquetar `v1.2.3` en git y poner
`OLTERRA_VERSION=1.2.3` en `.env`.

La primera vez que el workflow publique, en GitHub → Packages hay que dejar los dos paquetes
como **públicos** (GHCR los crea privados). Si se prefieren privados, el servidor necesita
`docker login ghcr.io` con un token de solo lectura (`read:packages`).

### 4.1 Actualización automática desde `main`

Cada 5 minutos el servidor mira `main` y, si hay un commit nuevo, se actualiza solo. Es el
servidor quien consulta, no GitHub quien entra: **no hay ninguna llave del servidor en GitHub
ni puertos nuevos abiertos**.

1. Trae `main`. Sin commit nuevo, no hace nada.
2. **Espera al CI de ese commit**: `pruebas`, `interfaz`, `imagen` y `despliegue` en verde.
   Si no terminó, espera; si falló, no lo despliega. Así un PR que rompa algo no llega a
   producción por mergearlo.
3. Trae el código (solo avanza, nunca reescribe historia) y corre `./olterra.sh actualizar`:
   respaldo, build, migraciones y reinicio. Si el build o la migración fallan, los
   contenedores que ya corrían siguen corriendo.
4. Un commit que falló **no se reintenta**; el siguiente sí. Una actualización manual y la
   automática no se pisan (comparten un candado).

Activarla, una vez, en el servidor:

```bash
cd /opt/olterra/deploy/produccion
./olterra.sh rama main        # si el servidor seguía otra rama; luego actualiza
sudo ./olterra.sh auto activar
./olterra.sh auto estado      # temporizador, qué corre, historial y últimos registros
```

Para pararla: `sudo ./olterra.sh auto desactivar`. Mientras el servidor siga una rama que no es
`main`, la automática no hace nada (lo dice en `auto estado`).

Lo que **no** hace: avisar si una actualización falla. Hoy se ve en `auto estado` y con
`systemctl status olterra-auto`; un aviso a Telegram o WhatsApp queda para cuando haya ISP
reales dependiendo de esto.

> Cuidado: las migraciones corren solas. Antes de cada actualización se hace un respaldo, pero
> una migración que borra datos no se deshace sola: revisarlas en el PR como cualquier cambio
> de esquema.

## 5. Respaldos

- El servicio `backup` deja un `pg_dump` diario en `deploy/produccion/respaldos/` (solo
  root) y borra los de más de `OLTERRA_RESPALDO_DIAS` (14). Con la base compartida solo lleva
  el esquema `olterra` (`OLTERRA_DB_SCHEMA`): lo de ISPWatch y Converza no es de Olterra y
  tiene su propio respaldo en Supabase.
- **Un respaldo que no se ha restaurado no es un respaldo**: `./olterra.sh probar-respaldo`
  lo restaura en un PostgreSQL desechable (un contenedor que se borra), cuenta los ISP y
  comprueba la migración. Sirve con cualquiera de las dos bases. CI lo hace en cada PR.
- Fuera del servidor: copiar `respaldos/` a DigitalOcean Spaces (u otro lugar) **cifrado**,
  por ejemplo con `rclone` y un remoto `crypt`. El respaldo sin `.env` no sirve: guarda las
  dos cosas, por separado.

Restaurar de verdad con la **base propia** (con la API y el ejecutor detenidos):

```bash
docker compose stop api executor web
docker compose exec -T db dropdb -U postgres olterra
docker compose exec -T db createdb -U postgres -O olterra_owner olterra
docker compose exec -T db pg_restore -U postgres -d olterra /respaldos/olterra-AAAAMMDD-HHMMSS.dump
./olterra.sh actualizar
```

Con la **base compartida** se restauran solo las tablas de Olterra, con el dueño y sin tocar lo
demás. Esta restauración sobre Supabase **no se ha ensayado todavía**: lo que sí está probado
es que el respaldo se restaura entero en una base desechable (`probar-respaldo`). Antes de
una restauración real, hacer un respaldo nuevo y ensayarla con el esquema vacío.

```bash
docker compose stop api executor web
set -a; . ./.env; set +a
PGPASSWORD="$OLTERRA_OWNER_PASSWORD" docker run --rm -e PGPASSWORD -v "$PWD/respaldos:/r:ro" \
  postgis/postgis:17-3.5 pg_restore --clean --if-exists --no-owner --no-acl \
  -d "host=$OLTERRA_DB_HOST port=$OLTERRA_DB_PORT user=$OLTERRA_DB_OWNER_USER dbname=$OLTERRA_DB_NAME sslmode=require" \
  /r/olterra-AAAAMMDD-HHMMSS.dump
./olterra.sh actualizar
```

## 6. Túnel hacia las OLT

El ejecutor llega a las OLT por el concentrador (el CHR) usando la IP única de cada OLT
(`198.19.x.x`). Los MikroTik de los ISP entran por **WireGuard** (RouterOS 7) o por **SSTP**
(RouterOS 6, que no tiene WireGuard; TLS sobre TCP 4443). El servidor de Olterra es un peer más del concentrador, con la IP
`198.18.0.2` de la plataforma (la misma que recibe las traps). **Probado el 2026-10-02** contra
el CHR real, compartido con ISPWatch: 1,3 ms del servidor al concentrador en la misma región.

1. En el CHR, la configuración inicial del concentrador, una sola vez. Si el CHR ya tiene otro
   WireGuard en el 13231 (el de ISPWatch), poner otro puerto en `.env`, por ejemplo
   `OLTERRA_TUNNEL_HUB_PORT=13232`, antes de generar el script:

   ```bash
   docker compose run --rm migrate olterra-admin concentrador
   ```

   Subir el script al CHR (Files) y correrlo con `/import`: pegar bloques largos en una consola
   SSH de RouterOS los corrompe. Todo queda con el comentario `olterra-hub` y no toca lo demás:
   una interfaz y un puerto propios, rutas a `198.18/16` y `198.19/16`, y reglas que solo
   actúan sobre ese túnel (incluida una que no deja a los ISP entrar al concentrador).
   La llave pública del concentrador (`/interface wireguard print`) va a
   `OLTERRA_TUNNEL_HUB_PUBLIC_KEY` y su IP pública a `OLTERRA_TUNNEL_HUB_HOST` en `.env`; luego
   `./olterra.sh actualizar`.

   Con `OLTERRA_TUNNEL_HUB_HOST` en `.env`, el script también deja el **servidor SSTP** para
   RouterOS 6: su CA y su certificado (con la IP pública), el perfil PPP y la regla del
   puerto 4443. Hay que correrlo de nuevo después de poner la IP (rehace solo lo suyo). La CA
   es pública y va a `.env` en una sola línea:

   ```
   /certificate export-certificate olterra-ca type=pem
   :put [/file get [find where name~"olterra-ca.crt"] contents]
   ```

   Las líneas entre `BEGIN` y `END`, pegadas sin saltos, van a `OLTERRA_TUNNEL_SSTP_CA`; luego
   `./olterra.sh actualizar` y borrar el archivo exportado en el CHR.

   > Ojo con los firewalls que bloquean por intento: el del CHR de ISPWatch manda a
   > `BLACKLIST` por 30 días a quien toque SSH o Winbox sin estar en `ALLOWED_MGMT`. No probar
   > puertos del CHR desde el servidor de Olterra.

2. En el servidor, WireGuard hacia el CHR (`sudo apt install wireguard`):

   ```ini
   # /etc/wireguard/olterra.conf  (la privada en olterra.key: umask 077; wg genkey > olterra.key)
   [Interface]
   Address = 198.18.0.2/24
   PostUp = wg set %i private-key /etc/wireguard/olterra.key

   [Peer]
   PublicKey = <OLTERRA_TUNNEL_HUB_PUBLIC_KEY>
   Endpoint = <OLTERRA_TUNNEL_HUB_HOST>:<OLTERRA_TUNNEL_HUB_PORT>
   AllowedIPs = 198.18.0.0/16, 198.19.0.0/16
   PersistentKeepalive = 25
   ```

   `sudo systemctl enable --now wg-quick@olterra`. Los contenedores salen por esa interfaz
   gracias al NAT que ya hace Docker: el ejecutor no necesita nada más.

3. En el CHR, el servidor como peer de la plataforma:

   ```
   /interface wireguard peers add interface=olterra-hub public-key="<pública del servidor>" \
       allowed-address=198.18.0.2/32 comment="olterra: plataforma"
   ```

   Prueba: desde el servidor, `ping 198.18.0.1` y `wg show olterra` (handshake reciente).

4. Cada ISP: en la interfaz, **Túnel → Agregar MikroTik** con su versión de RouterOS. El
   script se descarga, se sube al router (Files) y se corre con `/import`. El bloque "Alta en
   el concentrador" se aplica en el CHR. Si un router se dio de alta con la versión
   equivocada, **Rotar o cambiar versión** lo pasa al otro transporte.

### 6.1 Clave de fábrica de las OLT VSOL

Cuando el cliente agrega una OLT VSOL que nunca se ha configurado por SSH, deja usuario y clave
vacíos y Olterra usa los de fábrica del servidor. Se configuran en `.env` (nunca en el repo):

```
OLTERRA_VSOL_DEFAULT_USERNAME=admin
OLTERRA_VSOL_DEFAULT_PASSWORD='la-clave-de-fabrica'
```

Entre comillas simples si trae `#` o `$`. Sin `OLTERRA_VSOL_DEFAULT_PASSWORD` la clave es
obligatoria al agregar una OLT. La API nunca devuelve esa clave (`GET /v1/olts/defaults` solo
dice si está configurada) y la bitácora solo anota que se usó. La clave de **enable** no tiene
valor de fábrica: es la que el cliente configuró y se escribe al agregar la OLT. Cambiar las
claves en la OLT desde Olterra todavía no existe.

### 6.2 Modo laboratorio de escrituras

Cada comando que escribe en una OLT necesita una captura de laboratorio de ese modelo y
firmware (`verified=True`); mientras no la tenga, la API se niega a correrlo y dice cuál falta.
Para validarlos contra una OLT de prueba se enciende, **solo mientras dura la prueba**:

```
OLTERRA_ALLOW_UNVERIFIED_WRITES=true
```

y `./olterra.sh actualizar`. Cada plan que corre así lo dice en la pantalla y en la bitácora
(`sin_verificar`). Al terminar se borra la línea (o `false`) y se vuelve a actualizar.

## 7. Seguridad, en corto

- Solo 80 y 443 abiertos. La base y NATS no publican puertos.
- `.env` con permisos 600; las claves nunca en la línea de comandos ni en los registros.
- La interfaz va con CSP estricta (solo recursos propios), HSTS y `nosniff`.
- Subidas a la API de 30 MB como máximo (lo corta Caddy).
- Mientras no haya usuarios (ver INTERFAZ.md), la llave de API es la llave de la casa: una
  por persona. Todavía no hay comando para revocar; hoy es por SQL:

  ```bash
  docker compose exec -T db psql -U postgres -d olterra -c "UPDATE api_keys SET revoked_at = now()
    WHERE name = 'la-llave' AND tenant_id = (SELECT id FROM tenants WHERE slug = 'mi-isp')"
  ```
