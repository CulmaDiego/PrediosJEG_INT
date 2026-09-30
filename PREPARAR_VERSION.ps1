$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Ejecute primero .\INICIAR_DEV.ps1 para crear el entorno.' }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Las pruebas fallaron. No prepare una versión todavía.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Hay dependencias incompatibles.' }
git status --short --branch
Write-Host ''
Write-Host 'Revise los cambios anteriores. Cuando esté conforme:' -ForegroundColor Cyan
Write-Host '  git add .'
Write-Host '  git commit -m "Descripción de la versión"'
Write-Host '  git push origin SU_RAMA'
Write-Host '  git tag -a v0.3.0 -m "Predios Remotos v0.3.0"'
Write-Host '  git push origin v0.3.0'
Write-Host 'Este script no publicó ni sobrescribió ningún cambio.' -ForegroundColor Yellow
