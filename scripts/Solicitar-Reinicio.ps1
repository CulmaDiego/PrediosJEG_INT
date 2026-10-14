$ErrorActionPreference = 'Stop'
$restartScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\APLICAR_CORRECCION_ORTOFOTOS.ps1'))
if (-not (Test-Path -LiteralPath $restartScript -PathType Leaf)) {
    throw 'No se encontro el script para reiniciar el aplicativo.'
}
Write-Host 'Windows solicitara permiso de administrador para reiniciar Predios Remotos.'
Write-Host 'Se cerraran y arrancaran solamente los procesos del aplicativo.'
$restartArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -SoloReiniciar' -f $restartScript
try {
    $restartProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList $restartArguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($restartProcess.ExitCode -ne 0) {
        throw "El reinicio termino con codigo $($restartProcess.ExitCode)."
    }
} catch {
    Write-Host ('Windows no pudo completar el reinicio: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Abra PowerShell como administrador y ejecute:'
    Write-Host ('& "{0}" -SoloReiniciar' -f $restartScript)
    exit 1
}
Write-Host 'Aplicativo reiniciado y verificado.' -ForegroundColor Green
