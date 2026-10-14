[CmdletBinding()]
param(
    [string]$Python,
    [switch]$InstallDependencies
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        throw 'Ejecute primero .\INICIAR_DEV.ps1 para crear el entorno.'
    }
}

Push-Location -LiteralPath $repo
try {
    if ($InstallDependencies) {
        & $Python -m pip install -r requirements-quality.txt
        if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias de calidad.' }
    }

    & $Python -c "from importlib.metadata import version; expected = {'ruff': '0.13.2', 'pip-audit': '2.9.0'}; mismatches = [f'{name}: {version(name)} (se requiere {wanted})' for name, wanted in expected.items() if version(name) != wanted]; print('\n'.join(mismatches) or 'Herramientas de calidad verificadas'); raise SystemExit(bool(mismatches))"
    if ($LASTEXITCODE -ne 0) {
        throw 'Instale las versiones de CI con -InstallDependencies antes de verificar.'
    }

    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Hay dependencias incompatibles. No publique los cambios.' }

    & $Python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw 'Ruff encontró errores. No publique los cambios.' }

    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Las pruebas fallaron. No publique los cambios.' }

    & $Python -m pip_audit -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'La auditoría de dependencias falló. No publique los cambios.' }

    Write-Host 'Calidad verificada: dependencias, Ruff, pruebas y auditoría.' -ForegroundColor Green
} finally {
    Pop-Location
}
