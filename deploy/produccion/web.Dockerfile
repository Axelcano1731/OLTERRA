# La interfaz: se compila con Node y la sirve Caddy, que además pone HTTPS y pasa /v1 a la API.
# Se construye desde la raíz del repo: docker build -f deploy/produccion/web.Dockerfile .
FROM node:24-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM caddy:2.11-alpine
COPY deploy/produccion/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
