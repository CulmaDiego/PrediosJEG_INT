param([string]$EnvFile = 'D:\PrediosRemotosInt\datos\config\production.env')
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath($PSScriptRoot)
$pattern = [regex]::Escape($repo + '\')
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $runtime = ($_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -match '(SERVICIO_INTERNO|RUN_WEB|RUN_WORKER)\.ps1') -or
               ($_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -match '(\-m uvicorn app\.main:app|\-m app\.worker|gdal_tile\.py.*--serve)')
    $_.ProcessId -ne $PID -and $runtime -and $_.CommandLine -match $pattern
})
# Stop the supervisor before its children so that it cannot restart them.
$processes | Sort-Object @{Expression = { if ($_.CommandLine -match 'SERVICIO_INTERNO.ps1') { 0 } else { 1 } }} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
}
Start-Sleep -Seconds 2
& (Join-Path $repo 'INICIAR_INTERNO.ps1') -EnvFile $EnvFile
