<#
.SYNOPSIS
  PostgreSQL 17 + PostGIS y NATS con JetStream en Windows, sin Docker, para correr las pruebas.

.DESCRIPTION
  Descarga binarios portátiles a .bin\ (ignorado por git) la primera vez:
    - PostgreSQL 17 de zonky (Maven Central) + el paquete PostGIS de OSGeo
    - nats-server de GitHub
  PostgreSQL escucha en 127.0.0.1:55432 (usuario postgres, sin clave, solo local)
  y NATS en 127.0.0.1:4223, para no chocar con otros servicios.

.EXAMPLE
  .\scripts\servicios-locales.ps1 iniciar
  $env:OLTERRA_TEST_ADMIN_URL = "postgresql://postgres@127.0.0.1:55432/postgres"
  $env:OLTERRA_TEST_NATS_URL  = "nats://127.0.0.1:4223"
  .venv\Scripts\python -m pytest
  .\scripts\servicios-locales.ps1 detener
#>
param(
    [ValidateSet("iniciar", "detener", "estado")]
    [string]$Accion = "estado"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Bin = Join-Path $Root ".bin"
$Pg = Join-Path $Bin "pg"
$PgData = Join-Path $Bin "pgdata"
$NatsExe = Join-Path $Bin "nats\nats-server.exe"

$PgVersion = "17.11.0"
$PostgisZip = "postgis-bundle-pg17-3.6.2x64.zip"
$NatsVersion = "v2.11.9"

function Get-File($Url, $Destination) {
    Write-Host "Descargando $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

function Install-Postgres {
    if (Test-Path (Join-Path $Pg "bin\postgres.exe")) { return }
    New-Item -ItemType Directory -Force $Pg | Out-Null
    $jar = Join-Path $Bin "pg.jar"
    Get-File "https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/$PgVersion/embedded-postgres-binaries-windows-amd64-$PgVersion.jar" $jar
    Expand-Archive -Force $jar (Join-Path $Bin "pg-jar")
    tar -xJf (Join-Path $Bin "pg-jar\postgres-windows-x86_64.txz") -C $Pg
    $zip = Join-Path $Bin $PostgisZip
    Get-File "https://download.osgeo.org/postgis/windows/pg17/$PostgisZip" $zip
    $postgis = Join-Path $Bin "postgis"
    Expand-Archive -Force $zip $postgis
    $bundle = Get-ChildItem $postgis -Directory | Select-Object -First 1
    foreach ($dir in "bin", "lib", "share") {
        Copy-Item -Recurse -Force (Join-Path $bundle.FullName "$dir\*") (Join-Path $Pg $dir)
    }
}

function Install-Nats {
    if (Test-Path $NatsExe) { return }
    $zip = Join-Path $Bin "nats.zip"
    Get-File "https://github.com/nats-io/nats-server/releases/download/$NatsVersion/nats-server-$NatsVersion-windows-amd64.zip" $zip
    $tmp = Join-Path $Bin "nats-tmp"
    Expand-Archive -Force $zip $tmp
    New-Item -ItemType Directory -Force (Split-Path $NatsExe) | Out-Null
    Move-Item -Force (Get-ChildItem $tmp -Recurse -Filter nats-server.exe | Select-Object -First 1).FullName $NatsExe
    Remove-Item -Recurse -Force $tmp
}

function Start-Services {
    New-Item -ItemType Directory -Force $Bin | Out-Null
    Install-Postgres
    Install-Nats
    $pgCtl = Join-Path $Pg "bin\pg_ctl.exe"
    if (-not (Test-Path $PgData)) {
        & (Join-Path $Pg "bin\initdb.exe") -D $PgData -U postgres -A trust -E UTF8 --locale=C | Out-Null
    }
    & $pgCtl -D $PgData status *> $null
    if ($LASTEXITCODE -ne 0) {
        # Start-Process separado: pg_ctl hereda la consola y deja colgada a la terminal si no.
        Start-Process -FilePath $pgCtl -WindowStyle Hidden -ArgumentList @(
            "-D", "`"$PgData`"", "-l", "`"$(Join-Path $Bin 'pg.log')`"",
            "-o", "`"-p 55432 -c listen_addresses=127.0.0.1`"", "start")
    }
    if (-not (Get-Process nats-server -ErrorAction SilentlyContinue)) {
        $store = Join-Path $Bin "jetstream"
        Start-Process -FilePath $NatsExe -WindowStyle Hidden -ArgumentList @("-js", "-a", "127.0.0.1", "-p", "4223", "-sd", "`"$store`"")
    }
    Start-Sleep -Seconds 3
    Show-Status
}

function Stop-Services {
    $pgCtl = Join-Path $Pg "bin\pg_ctl.exe"
    if (Test-Path $pgCtl) { & $pgCtl -D $PgData stop -m fast }
    Get-Process nats-server -ErrorAction SilentlyContinue | Stop-Process -Force
}

function Show-Status {
    $pgCtl = Join-Path $Pg "bin\pg_ctl.exe"
    if (Test-Path $pgCtl) { & $pgCtl -D $PgData status } else { Write-Host "PostgreSQL: no instalado" }
    if (Get-Process nats-server -ErrorAction SilentlyContinue) { Write-Host "NATS: corriendo en 127.0.0.1:4223" } else { Write-Host "NATS: detenido" }
}

switch ($Accion) {
    "iniciar" { Start-Services }
    "detener" { Stop-Services }
    "estado" { Show-Status }
}
