param([string]$EnvFile = 'D:\PrediosRemotosInt\datos\config\production.env')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Cargar-Entorno.ps1') -Path $EnvFile
$mutex = [Threading.Mutex]::new($false, 'Local\PrediosRemotosInt-Supervisor')
if (-not $mutex.WaitOne(0)) { exit 0 }
$children = @{}
try {
    $listener = Get-NetTCPConnection -LocalPort 8030 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw 'El puerto 8030 ya está ocupado. No se inició otra instancia.' }
    $logs = Join-Path $env:PREDIOS_DATA_DIR 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    while ($true) {
        foreach ($kind in @('WEB', 'WORKER')) {
            if (-not $children[$kind] -or $children[$kind].HasExited) {
                $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RepoDir "{1}" -EnvFile "{2}"' -f (Join-Path $PSScriptRoot "RUN_$kind.ps1"), $repo, $EnvFile
                $children[$kind] = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "$kind.out.log") -RedirectStandardError (Join-Path $logs "$kind.err.log")
            }
        }
        Start-Sleep -Seconds 10
    }
} finally {
    foreach ($child in $children.Values) {
        if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue }
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
