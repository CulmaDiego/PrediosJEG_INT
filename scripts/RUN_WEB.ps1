param([Parameter(Mandatory=$true)][string]$RepoDir,[Parameter(Mandatory=$true)][string]$EnvFile)
$ErrorActionPreference = 'Stop'
. (Join-Path $RepoDir 'scripts\Cargar-Entorno.ps1') -Path $EnvFile
Set-Location -LiteralPath $RepoDir
& (Join-Path $RepoDir '.venv\Scripts\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1

