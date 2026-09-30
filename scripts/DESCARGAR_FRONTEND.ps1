$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $repo 'app\static\vendor'
$leaflet = Join-Path $vendor 'leaflet'
$geoman = Join-Path $vendor 'geoman'
New-Item -ItemType Directory -Force -Path $leaflet,$geoman | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' -OutFile (Join-Path $leaflet 'leaflet.css')
Invoke-WebRequest -UseBasicParsing -Uri 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' -OutFile (Join-Path $leaflet 'leaflet.js')
Invoke-WebRequest -UseBasicParsing -Uri 'https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.3/dist/leaflet-geoman.css' -OutFile (Join-Path $geoman 'leaflet-geoman.css')
Invoke-WebRequest -UseBasicParsing -Uri 'https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.3/dist/leaflet-geoman.min.js' -OutFile (Join-Path $geoman 'leaflet-geoman.min.js')
Write-Host 'Dependencias web locales descargadas. Configure PREDIOS_ASSET_MODE=local.' -ForegroundColor Green

