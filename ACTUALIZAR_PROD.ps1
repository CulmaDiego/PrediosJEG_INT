param(
    [Parameter(Mandatory=$true)][ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$')][string]$Version,
    [string]$EnvFile = 'D:\PrediosRemotosInt\datos\config\production.env'
)
$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step falló (código $LASTEXITCODE)." }
}

$repo = $PSScriptRoot
Set-Location -LiteralPath $repo
. (Join-Path $repo 'scripts\Cargar-Entorno.ps1') -Path $EnvFile
if ($env:PREDIOS_ENV -ne 'production') { throw 'La configuración no corresponde a producción.' }
if ($env:PREDIOS_WINDOW_RADIUS_M -ne '2000') { throw 'Ajuste PREDIOS_WINDOW_RADIUS_M=2000 en production.env antes de actualizar.' }
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "No existe el entorno Python: $python" }
$changes = git status --porcelain
Assert-NativeSuccess 'Comprobar el checkout'
if ($changes) { throw 'El checkout de producción tiene cambios locales. Deténgase y revíselos.' }
$origin = (git remote get-url origin).Trim()
Assert-NativeSuccess 'Leer el origen Git'
if ($origin -notmatch 'github\.com[:/]CulmaDiego/PrediosJEG_INT(\.git)?$') { throw "El origen Git no es el repositorio esperado: $origin" }
git fetch --tags --prune origin
Assert-NativeSuccess 'Descargar etiquetas'
git rev-parse --verify "refs/tags/$Version" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "La etiqueta $Version no existe en GitHub." }
$oldCommit = (git rev-parse HEAD).Trim()
Assert-NativeSuccess 'Identificar la versión anterior'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:PREDIOS_DATA_DIR "backups\antes-$Version-$stamp"
$backupVerified = $false

try {
    schtasks /End /TN 'PrediosRemotosInt-Web' 2>$null | Out-Null
    schtasks /End /TN 'PrediosRemotosInt-Worker' 2>$null | Out-Null
    Start-Sleep -Seconds 2
    & $python -m app.cli backup --destination $backup
    Assert-NativeSuccess 'Crear el respaldo'
    & $python -m app.cli verify-backup --source $backup
    Assert-NativeSuccess 'Verificar el respaldo'
    $backupVerified = $true
    git checkout --detach $Version
    Assert-NativeSuccess "Seleccionar $Version"
    & $python -m pip install -r (Join-Path $repo 'requirements.lock')
    Assert-NativeSuccess 'Instalar dependencias'
    & $python -m alembic -c (Join-Path $repo 'alembic.ini') upgrade head
    Assert-NativeSuccess 'Migrar la base de datos'
    schtasks /Run /TN 'PrediosRemotosInt-Worker'
    Assert-NativeSuccess 'Iniciar el trabajador'
    schtasks /Run /TN 'PrediosRemotosInt-Web'
    Assert-NativeSuccess 'Iniciar la web'
    $verified = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 2
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8030/health' -TimeoutSec 3
            $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8030/ready' -TimeoutSec 3
            if ($health.status -eq 'ok' -and $health.version -eq $Version.TrimStart('v') -and $ready.status -eq 'ready') {
                $verified = $true
                break
            }
        } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $verified) { throw 'Las comprobaciones de salud no pasaron para la versión nueva.' }
    $tasks = @(Get-ScheduledTask -TaskName 'PrediosRemotosInt-Web','PrediosRemotosInt-Worker')
    if ($tasks.Count -ne 2 -or @($tasks | Where-Object { $_.State -ne 'Running' }).Count -gt 0) {
        throw 'Una tarea de producción no sigue en ejecución después de actualizar.'
    }
    Write-Host "Producción actualizada a $Version y verificada. Respaldo: $backup" -ForegroundColor Green
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    schtasks /End /TN 'PrediosRemotosInt-Web' 2>$null | Out-Null
    schtasks /End /TN 'PrediosRemotosInt-Worker' 2>$null | Out-Null
    Start-Sleep -Seconds 2
    try {
        git checkout --detach $oldCommit
        Assert-NativeSuccess 'Restaurar el código anterior'
        & $python -m pip install -r (Join-Path $repo 'requirements.lock')
        Assert-NativeSuccess 'Restaurar dependencias'
    } catch { $rollbackErrors += "código/dependencias: $($_.Exception.Message)" }
    if ($backupVerified) {
        try {
            & $python -m app.cli restore-backup --source $backup --confirm RESTAURAR_RESPALDO_COMPLETO
            Assert-NativeSuccess 'Restaurar el respaldo'
        } catch { $rollbackErrors += "datos: $($_.Exception.Message)" }
    }
    try {
        schtasks /Run /TN 'PrediosRemotosInt-Worker'
        Assert-NativeSuccess 'Reiniciar el trabajador anterior'
        schtasks /Run /TN 'PrediosRemotosInt-Web'
        Assert-NativeSuccess 'Reiniciar la web anterior'
    } catch { $rollbackErrors += "servicios: $($_.Exception.Message)" }
    if ($rollbackErrors.Count -gt 0) {
        throw "Actualización fallida: $failure. La recuperación NO quedó completa ($($rollbackErrors -join '; ')). No publique enlaces; revise el respaldo $backup."
    }
    throw "Actualización fallida: $failure. Se reinició la versión anterior$(if ($backupVerified) { ' y se restauró el respaldo' }). Compruebe /health y /ready antes de continuar."
}
