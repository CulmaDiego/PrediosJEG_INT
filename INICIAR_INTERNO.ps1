param([string]$EnvFile = 'D:\PrediosRemotosInt\datos\config\production.env')
$ErrorActionPreference = 'Stop'
$service = Join-Path $PSScriptRoot 'scripts\SERVICIO_INTERNO.ps1'
Start-Process powershell.exe -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -EnvFile "{1}"' -f $service, $EnvFile) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Host 'Predios Remotos Interno: http://127.0.0.1:8030/admin'
