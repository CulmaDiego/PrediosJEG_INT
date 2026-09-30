param([Parameter(Mandatory=$true)][ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$')][string]$Version,[string]$EnvFile='D:\PrediosRemotos\datos\config\production.env')
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo
. (Join-Path $repo 'scripts\Cargar-Entorno.ps1') -Path $EnvFile
if ($env:PREDIOS_ENV -ne 'production') { throw 'La configuración no corresponde a producción.' }
if (git status --porcelain) { throw 'El checkout de producción tiene cambios locales. Deténgase y revíselos.' }
$origin = (git remote get-url origin).Trim()
if ($origin -notmatch 'github\.com[:/]CulmaDiego/PrediosJEG(\.git)?$') { throw "El origen Git no es el repositorio esperado: $origin" }
git fetch --tags --prune origin
git rev-parse --verify "refs/tags/$Version" 2>$null
if ($LASTEXITCODE -ne 0) { throw "La etiqueta $Version no existe en GitHub." }
$oldCommit = (git rev-parse HEAD).Trim()
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:PREDIOS_DATA_DIR "backups\antes-$Version-$stamp"
$python = Join-Path $repo '.venv\Scripts\python.exe'
schtasks /End /TN 'PrediosRemotos-Web' 2>$null
schtasks /End /TN 'PrediosRemotos-Worker' 2>$null
try {
    & $python -m app.cli backup --destination $backup
    & $python -m app.cli verify-backup --source $backup
    git checkout --detach $Version
    & $python -m pip install -r (Join-Path $repo 'requirements.lock')
    & $python -m alembic upgrade head
    schtasks /Run /TN 'PrediosRemotos-Worker'
    schtasks /Run /TN 'PrediosRemotos-Web'
    Start-Sleep -Seconds 5
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 15
    $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/ready' -TimeoutSec 15
    if ($health.status -ne 'ok' -or $ready.status -ne 'ready') { throw 'Las comprobaciones de salud no pasaron.' }
    Write-Host "Producción actualizada a $Version y verificada." -ForegroundColor Green
} catch {
    schtasks /End /TN 'PrediosRemotos-Web' 2>$null
    schtasks /End /TN 'PrediosRemotos-Worker' 2>$null
    git checkout --detach $oldCommit
    & $python -m pip install -r (Join-Path $repo 'requirements.lock')
    if (Test-Path -LiteralPath (Join-Path $backup 'manifest.json')) {
        & $python -m app.cli restore-backup --source $backup --confirm RESTAURAR_RESPALDO_COMPLETO
    }
    schtasks /Run /TN 'PrediosRemotos-Worker'
    schtasks /Run /TN 'PrediosRemotos-Web'
    throw "La actualización falló. Se restauraron el código anterior y el respaldo completo. Error: $($_.Exception.Message)"
}
