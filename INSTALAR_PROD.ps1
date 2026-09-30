param(
    [string]$RepoUrl = 'https://github.com/CulmaDiego/PrediosJEG.git',
    [string]$InstallDir = 'C:\PrediosRemotos\app',
    [string]$DataDir = 'D:\PrediosRemotos\datos',
    [string]$OrthoDir = 'D:\PrediosRemotos\ortofotos',
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$')][string]$Version = 'v0.1.0'
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Instale Git para Windows.' }
if (-not (Test-Path -LiteralPath 'C:\Program Files\QGIS 3.44.3\bin\gdalinfo.exe')) { throw 'Instale QGIS 3.44.3 con GDAL y el controlador ECW.' }
if (-not (Test-Path -LiteralPath $InstallDir)) { git clone $RepoUrl $InstallDir }
$resolvedInstall = (Resolve-Path -LiteralPath $InstallDir).Path
Set-Location -LiteralPath $resolvedInstall
if (git status --porcelain) { throw 'El checkout de instalación contiene cambios locales. Deténgase y revíselos.' }
git fetch --tags --prune origin
git rev-parse --verify "refs/tags/$Version" 2>$null
if ($LASTEXITCODE -ne 0) { throw "La etiqueta $Version no existe en GitHub." }
git checkout --detach $Version
New-Item -ItemType Directory -Force -Path $DataDir,$OrthoDir,(Join-Path $DataDir 'config'),(Join-Path $DataDir 'logs'),(Join-Path $DataDir 'backups') | Out-Null
$envFile = Join-Path $DataDir 'config\production.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = [Convert]::ToBase64String($bytes)
    $publicUrl = Read-Host 'URL pública HTTPS que usará Cloudflare Tunnel'
    $lines = @('PREDIOS_ENV=production',"PREDIOS_DATA_DIR=$DataDir","PREDIOS_ORTHOPHOTO_ROOT=$OrthoDir","PREDIOS_SECRET_KEY=$secret","PREDIOS_PUBLIC_BASE_URL=$publicUrl",'PREDIOS_COOKIE_SECURE=true','PREDIOS_QGIS_ROOT=C:\Program Files\QGIS 3.44.3','PREDIOS_ASSET_MODE=local')
    [IO.File]::WriteAllLines($envFile, $lines)
}
. (Join-Path $resolvedInstall 'scripts\Cargar-Entorno.ps1') -Path $envFile
$bootstrap = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0) { $bootstrap = $candidate }
}
if (-not $bootstrap) { throw 'Instale Python 3.12 de 64 bits antes de continuar.' }
$python = Join-Path $resolvedInstall '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { & $bootstrap -m venv (Join-Path $resolvedInstall '.venv') }
& $python -m pip install -r (Join-Path $resolvedInstall 'requirements.lock')
& (Join-Path $resolvedInstall 'scripts\DESCARGAR_FRONTEND.ps1')
& $python -m alembic -c (Join-Path $resolvedInstall 'alembic.ini') upgrade head
& $python -m app.cli has-admin
if ($LASTEXITCODE -ne 0) {
    $secure = Read-Host 'Contraseña inicial del administrador (mínimo 12 caracteres)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:ADMIN_INITIAL_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    & $python -m app.cli init-admin --username admin
    Remove-Item Env:ADMIN_INITIAL_PASSWORD -ErrorAction SilentlyContinue
}
$webScript = Join-Path $resolvedInstall 'scripts\RUN_WEB.ps1'
$workerScript = Join-Path $resolvedInstall 'scripts\RUN_WORKER.ps1'
schtasks /Create /TN 'PrediosRemotos-Web' /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$webScript`" -RepoDir `"$resolvedInstall`" -EnvFile `"$envFile`"" /F
schtasks /Create /TN 'PrediosRemotos-Worker' /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$workerScript`" -RepoDir `"$resolvedInstall`" -EnvFile `"$envFile`"" /F
schtasks /Run /TN 'PrediosRemotos-Web'
schtasks /Run /TN 'PrediosRemotos-Worker'
Write-Host 'Aplicación local instalada. Verifique http://127.0.0.1:8000/health.' -ForegroundColor Green
Write-Host 'Instale cloudflared y cree un túnel hacia http://127.0.0.1:8000. Guarde su token en el servicio de Windows de cloudflared, nunca en este repositorio.' -ForegroundColor Yellow
