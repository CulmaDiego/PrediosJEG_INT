param([Parameter(Mandatory=$true)][string]$RepoDir,[Parameter(Mandatory=$true)][string]$EnvFile)
$ErrorActionPreference = 'Stop'
. (Join-Path $RepoDir 'scripts\Cargar-Entorno.ps1') -Path $EnvFile
Set-Location -LiteralPath $RepoDir
& (Join-Path $RepoDir '.venv\Scripts\python.exe') -m app.worker

