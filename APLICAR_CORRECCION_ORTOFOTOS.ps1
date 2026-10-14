param(
    [string]$EnvFile = 'D:\PrediosRemotos\datos\config\production.env',
    [switch]$SoloReiniciar
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abra PowerShell como administrador y ejecute este script de nuevo. Windows exige ese permiso para reiniciar los procesos actuales.'
}
$repo = $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
Set-Location -LiteralPath $repo
. (Join-Path $repo 'scripts\Cargar-Entorno.ps1') -Path $EnvFile
if ($env:PREDIOS_ENV -ne 'production') { throw 'Se requiere la configuración de producción.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'No se encontró el Python de la aplicación.' }

function Assert-Exit([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step falló (código $LASTEXITCODE)." }
}
function Start-App([string]$kind) {
    $taskName = "PrediosRemotos-$kind"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $taskName
    } else {
        $script = Join-Path $repo "scripts\RUN_$($kind.ToUpper()).ps1"
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RepoDir "{1}" -EnvFile "{2}"' -f $script, $repo, $EnvFile
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    }
}

$backup = 'No requerido para reiniciar procesos'
if (-not $SoloReiniciar) {
    $backup = Join-Path $env:PREDIOS_DATA_DIR ('backups\antes-ortofoto-original-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    & $python -m app.cli backup --destination $backup
    Assert-Exit 'Crear respaldo'
    & $python -m app.cli verify-backup --source $backup
    Assert-Exit 'Verificar respaldo'
    Write-Host "Respaldo verificado: $backup"
}

# Stop only this checkout's web and worker, including Python launcher children.
$processes = @(Get-CimInstance Win32_Process)
$targets = @($processes | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -like "*$repo*" -and
    $_.CommandLine -match '(-m\s+app\.worker|uvicorn\s+app\.main:app)'
})
$targetIds = [Collections.Generic.HashSet[int]]::new()
foreach ($process in $targets) { [void]$targetIds.Add([int]$process.ProcessId) }
do {
    $added = $false
    foreach ($process in $processes) {
        if ($process.Name -eq 'python.exe' -and $targetIds.Contains([int]$process.ParentProcessId)) {
            if ($targetIds.Add([int]$process.ProcessId)) { $added = $true }
        }
    }
} while ($added)
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener -and -not $targetIds.Contains([int]$listener.OwningProcess)) {
    throw 'El puerto 8000 pertenece a un proceso que no se pudo identificar como esta aplicación. No se detuvo.'
}
try {
    foreach ($kind in 'Web','Worker') {
        $task = Get-ScheduledTask -TaskName "PrediosRemotos-$kind" -ErrorAction SilentlyContinue
        if ($task) { Stop-ScheduledTask -TaskName $task.TaskName }
    }
    foreach ($processId in $targetIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) { Stop-Process -InputObject $process -Force }
    }
    if (-not $SoloReiniciar) {
        & $python -m app.cli retry-failed-images
        Assert-Exit 'Volver a preparar imágenes fallidas'
        & $python -m app.cli warm-images
        Assert-Exit 'Preparar imagen y niveles de zoom'
    }
} finally {
    try { Start-App 'Worker' } finally { Start-App 'Web' }
}
$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 3
        $state = Invoke-RestMethod 'http://127.0.0.1:8000/ready' -TimeoutSec 3
        if ($health.status -eq 'ok' -and $state.status -eq 'ready') { $ready = $true; break }
    } catch { }
}
if (-not $ready) { throw "No se confirmó el inicio de la aplicación. Respaldo: $backup" }
Write-Host 'Aplicativo reiniciado. Web y trabajador disponibles.' -ForegroundColor Green
