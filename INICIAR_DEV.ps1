$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo
$env:PREDIOS_ENV = 'development'
if (-not $env:PREDIOS_DATA_DIR) { $env:PREDIOS_DATA_DIR = [IO.Path]::GetFullPath((Join-Path $repo '..\PrediosRemotos_data_dev')) }
if (-not $env:PREDIOS_ORTHOPHOTO_ROOT) { $env:PREDIOS_ORTHOPHOTO_ROOT = Join-Path $repo 'Imagenes' }
$configDir = Join-Path $env:PREDIOS_DATA_DIR 'config'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$secretFile = Join-Path $configDir 'dev-secret.txt'
if (-not (Test-Path -LiteralPath $secretFile)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($bytes))
}
$env:PREDIOS_SECRET_KEY = [IO.File]::ReadAllText($secretFile).Trim()
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $bootstrap = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $bootstrap = $candidate }
    }
    if (-not $bootstrap) { throw 'Instale Python 3.12 de 64 bits antes de continuar. QGIS se usa para GDAL, no como servidor Python.' }
    & $bootstrap -m venv (Join-Path $repo '.venv')
}
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $repo 'requirements.lock')
& $venvPython -m alembic upgrade head
& $venvPython -m app.cli has-admin
if ($LASTEXITCODE -ne 0) {
    $secure = Read-Host 'Cree la contraseña del administrador (mínimo 12 caracteres)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:ADMIN_INITIAL_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    & $venvPython -m app.cli init-admin --username admin
    Remove-Item Env:ADMIN_INITIAL_PASSWORD -ErrorAction SilentlyContinue
}
$logDir = Join-Path $env:PREDIOS_DATA_DIR 'logs'
$runDir = Join-Path $env:PREDIOS_DATA_DIR 'run'
New-Item -ItemType Directory -Force -Path $logDir,$runDir | Out-Null
$worker = Start-Process -FilePath $venvPython -ArgumentList '-m','app.worker' -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'worker.out.log') -RedirectStandardError (Join-Path $logDir 'worker.err.log')
[IO.File]::WriteAllText((Join-Path $runDir 'worker.pid'), [string]$worker.Id)
Write-Host 'Predios Remotos: http://127.0.0.1:8000' -ForegroundColor Green
try { & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload } finally { if (-not $worker.HasExited) { Stop-Process -Id $worker.Id -Force } }
