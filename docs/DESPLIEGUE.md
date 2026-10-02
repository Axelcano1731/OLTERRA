# Despliegue en producción

> Última actualización: 2026-10-01

Olterra corre en **un servidor con Docker** (un droplet de DigitalOcean) detrás de
**Cloudflare DNS**, como dice el plan. Todo lo de producción está en `deploy/produccion/`
y se maneja con un solo comando, `./olterra.sh`.

```
Internet ──443──▶ Caddy (web) ──/v1──▶ API ──▶ PostgreSQL + PostGIS
                     │                   │
                     └─ interfaz         └──▶ NATS ──▶ ejecutor ──túnel WireGuard──▶ CHR ──▶ MikroTik del ISP ──▶ OLT
```

| Servicio | Qué es | Expuesto |
|---|---|---|
| `web` | Caddy: la interfaz, HTTPS automático (Let's Encrypt) y `/v1`, `/docs`, `/health` hacia la API | 80 y 443 |
| `api` | FastAPI (`OLTERRA_ENV=prod`) | No |
| `executor` | El ejecutor: sesiones SSH y SNMP hacia las OLT | No |
| `db` | PostgreSQL 17 + PostGIS, roles con claves generadas | No |
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

## 4. El día a día

| Comando | Qué hace |
|---|---|
| `./olterra.sh estado` | Contenedores y `/health` |
| `./olterra.sh actualizar` | Respaldo, imágenes nuevas, migraciones y reinicio |
| `./olterra.sh isp <slug> "<Nombre>"` | Un ISP nuevo con su llave |
| `./olterra.sh llave <slug> <nombre>` | Otra llave para un ISP |
| `./olterra.sh respaldo` | Un respaldo ahora (además del diario) |
| `./olterra.sh probar-respaldo` | Restaura el último respaldo en una base aparte y lo revisa |
| `./olterra.sh registros [servicio]` | Últimos registros |

**Publicar una versión**: al mergear a `main`, el workflow `Imágenes` publica las dos imágenes
con la etiqueta `main`; en el servidor, `./olterra.sh actualizar`. Para fijar una versión,
etiquetar `v1.2.3` en git y poner `OLTERRA_VERSION=1.2.3` en `.env`.

La primera vez que el workflow publique, en GitHub → Packages hay que dejar los dos paquetes
como **públicos** (GHCR los crea privados). Si se prefieren privados, el servidor necesita
`docker login ghcr.io` con un token de solo lectura (`read:packages`).

## 5. Respaldos

- El servicio `backup` deja un `pg_dump` diario en `deploy/produccion/respaldos/` (solo
  root) y borra los de más de `OLTERRA_RESPALDO_DIAS` (14).
- **Un respaldo que no se ha restaurado no es un respaldo**: `./olterra.sh probar-respaldo`
  lo restaura en una base aparte, cuenta los ISP y la borra. CI lo hace en cada PR.
- Fuera del servidor: copiar `respaldos/` a DigitalOcean Spaces (u otro lugar) **cifrado**,
  por ejemplo con `rclone` y un remoto `crypt`. El respaldo sin `.env` no sirve: guarda las
  dos cosas, por separado.

Restaurar de verdad (con la API y el ejecutor detenidos):

```bash
docker compose stop api executor web
docker compose exec -T db dropdb -U postgres olterra
docker compose exec -T db createdb -U postgres -O olterra_owner olterra
docker compose exec -T db pg_restore -U postgres -d olterra /respaldos/olterra-AAAAMMDD-HHMMSS.dump
./olterra.sh actualizar
```

## 6. Túnel hacia las OLT

El ejecutor llega a las OLT por el concentrador WireGuard (el CHR) usando la IP única de cada
OLT (`198.19.x.x`). El servidor de Olterra es un peer más del concentrador, con la IP
`198.18.0.2` de la plataforma (la misma que recibe las traps). **Sin probar todavía contra el
CHR real** (pendiente de la fase 0).

1. En el CHR, la configuración inicial del concentrador, una sola vez:

   ```bash
   docker compose run --rm migrate olterra-admin concentrador
   ```

   Pegar el script en el CHR. Su llave pública (`/interface wireguard print`) va a
   `OLTERRA_TUNNEL_HUB_PUBLIC_KEY` y su dirección pública a `OLTERRA_TUNNEL_HUB_HOST` en
   `.env`; luego `./olterra.sh actualizar`.

2. En el servidor, WireGuard hacia el CHR (`sudo apt install wireguard`):

   ```ini
   # /etc/wireguard/olterra.conf  (chmod 600; la llave privada se genera con `wg genkey`)
   [Interface]
   Address = 198.18.0.2/24
   PrivateKey = <llave privada de ESTE servidor>

   [Peer]
   PublicKey = <OLTERRA_TUNNEL_HUB_PUBLIC_KEY>
   Endpoint = <OLTERRA_TUNNEL_HUB_HOST>:13231
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

4. Cada ISP: en la interfaz, **Túnel → Agregar MikroTik**, y pegar el script en su router.

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
