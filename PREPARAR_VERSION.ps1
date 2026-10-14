$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Ejecute primero .\INICIAR_DEV.ps1 para crear el entorno.' }
& (Join-Path $repo 'scripts\VERIFICAR_CALIDAD.ps1') -Python $python -InstallDependencies
git status --short --branch
$lastTag = git tag --list 'v*' --sort=-version:refname | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron consultar las etiquetas Git.' }
$nextTag = 'v0.1.0'
if ($lastTag -match '^v(\d+)\.(\d+)\.(\d+)$') {
    $nextTag = "v$($Matches[1]).$($Matches[2]).$([int]$Matches[3] + 1)"
}
Write-Host ''
Write-Host 'Revise los cambios anteriores. Cuando esté conforme:' -ForegroundColor Cyan
Write-Host '  git add .'
Write-Host '  git commit -m "Descripción de la versión"'
Write-Host '  git push origin SU_RAMA'
Write-Host "  git tag -a $nextTag -m `"Predios Remotos $nextTag`""
Write-Host "  git push origin $nextTag"
Write-Host 'Este script no publicó ni sobrescribió ningún cambio.' -ForegroundColor Yellow
