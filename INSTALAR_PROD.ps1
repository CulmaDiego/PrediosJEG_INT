param(
    [string]$RepoUrl = 'https://github.com/CulmaDiego/PrediosJEG_INT.git',
    [string]$InstallDir = 'D:\PrediosRemotosInt\app',
    [string]$DataDir = 'D:\PrediosRemotosInt\datos',
    [string]$OrthoDir = 'D:\PrediosRemotosInt\ortofotos',
    [string]$QgisRoot = 'C:\Program Files\QGIS 3.44.13',
    [string]$PythonExe = '',
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$')][string]$Version = 'v1.0.0'
)
$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step falló (código $LASTEXITCODE)." }
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Abra PowerShell como administrador.'
}
foreach ($path in @($InstallDir, $DataDir, $OrthoDir)) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
    if (-not (Test-Path -LiteralPath $root)) { throw "El disco de $path no está disponible: $root" }
    $drive = [IO.DriveInfo]::new($root)
    if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) {
        throw "Use un disco local fijo para $path; no una unidad de red o extraíble."
    }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Instale Git para Windows.' }
if (-not (Test-Path -LiteralPath (Join-Path $QgisRoot 'bin\gdalinfo.exe') -PathType Leaf)) {
    throw "No se encontró GDAL en $QgisRoot. Compruebe la ruta de QGIS y el controlador ECW."
}
$projDirs = @((Join-Path $QgisRoot 'share\proj'),(Join-Path $QgisRoot 'apps\proj\share\proj'))
$projDir = $projDirs | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'proj.db') -PathType Leaf } | Select-Object -First 1
if (-not $projDir) { throw "No se encontró proj.db en $QgisRoot. Compruebe que la instalación de QGIS incluya los datos de PROJ." }
$gdalData = Join-Path $QgisRoot 'apps\gdal\share\gdal'
if (-not (Test-Path -LiteralPath $gdalData -PathType Container)) { throw "No se encontró GDAL_DATA en $gdalData." }
$gdalPlugins = Join-Path $QgisRoot 'apps\gdal\lib\gdalplugins'
if (-not (Test-Path -LiteralPath $gdalPlugins -PathType Container)) { throw "No se encontró la carpeta de complementos GDAL: $gdalPlugins." }
$env:PROJ_DATA = $projDir
$env:PROJ_LIB = $projDir
$env:GDAL_DATA = $gdalData
$env:GDAL_DRIVER_PATH = $gdalPlugins
$env:GTIFF_SRS_SOURCE = 'EPSG'
$env:PATH = "$(Join-Path $QgisRoot 'bin');$env:PATH"
foreach ($tool in @('gdalwarp.exe', 'ogr2ogr.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $QgisRoot "bin\$tool") -PathType Leaf)) {
        throw "No se encontró $tool en $QgisRoot."
    }
}
$tileTools = @((Join-Path $QgisRoot 'bin\gdal2tiles.exe'),(Join-Path $QgisRoot 'bin\gdal2tiles.py'))
$qgisPythonDirs = @(Get-ChildItem -LiteralPath (Join-Path $QgisRoot 'apps') -Directory -Filter 'Python3*')
foreach ($pythonDir in $qgisPythonDirs) {
    if (Test-Path -LiteralPath (Join-Path $pythonDir.FullName 'python.exe') -PathType Leaf) {
        $tileTools += Join-Path $pythonDir.FullName 'Scripts\gdal2tiles.py'
    }
}
if (-not ($tileTools | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })) {
    throw "No se encontró gdal2tiles en $QgisRoot."
}
if (-not (Test-Path -LiteralPath $OrthoDir -PathType Container)) {
    throw "Copie primero las ortofotos a $OrthoDir, conservando las carpetas de municipio."
}
$orthos = @(Get-ChildItem -LiteralPath $OrthoDir -Recurse -File | Where-Object { $_.Extension -in @('.ecw', '.tif', '.tiff') })
if ($orthos.Count -eq 0) { throw "No hay archivos ECW/TIFF en $OrthoDir. No se instalará un catálogo vacío." }
$gdalinfo = Join-Path $QgisRoot 'bin\gdalinfo.exe'
if (@($orthos | Where-Object { $_.Extension -eq '.ecw' }).Count -gt 0) {
    $formats = & $gdalinfo --formats 2>&1
    Assert-NativeSuccess 'Consultar controladores GDAL'
    if ($formats -match 'no database context specified|Cannot find proj\.db') { throw 'GDAL sigue sin encontrar proj.db a pesar de configurar PROJ_DATA.' }
    if (-not ($formats | Select-String -Pattern '^\s*ECW\s')) { throw 'GDAL no tiene el controlador ECW requerido por estas ortofotos.' }
}
foreach ($ortho in $orthos) {
    $metadata = & $gdalinfo -json $ortho.FullName 2>&1
    Assert-NativeSuccess "Leer la ortofoto $($ortho.FullName)"
    if ($metadata -match 'no database context specified|Cannot find proj\.db') {
        throw "GDAL no pudo resolver el sistema de coordenadas de $($ortho.FullName). Revise PROJ_DATA."
    }
}

if (-not (Test-Path -LiteralPath $InstallDir)) {
    git clone $RepoUrl $InstallDir
    Assert-NativeSuccess 'Clonar el repositorio'
}
$resolvedInstall = (Resolve-Path -LiteralPath $InstallDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedInstall '.git'))) { throw "$resolvedInstall no es un checkout Git." }
Set-Location -LiteralPath $resolvedInstall
$origin = (git remote get-url origin).Trim()
Assert-NativeSuccess 'Leer el origen Git'
if ($origin -notmatch 'github\.com[:/]CulmaDiego/PrediosJEG_INT(\.git)?$') { throw "El origen Git no es el esperado: $origin" }
$changes = git status --porcelain
Assert-NativeSuccess 'Comprobar el checkout'
if ($changes) { throw 'El checkout de instalación contiene cambios locales. Deténgase y revíselos.' }
git fetch --tags --prune origin
Assert-NativeSuccess 'Descargar etiquetas'
git rev-parse --verify "refs/tags/$Version" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "La etiqueta $Version no existe en GitHub." }
git checkout --detach $Version
Assert-NativeSuccess "Seleccionar $Version"

foreach ($asset in @('leaflet\leaflet.css', 'leaflet\leaflet.js', 'geoman\leaflet-geoman.css', 'geoman\leaflet-geoman.min.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedInstall "app\static\vendor\$asset") -PathType Leaf)) {
        throw "Falta el recurso web local $asset en $Version."
    }
}
New-Item -ItemType Directory -Force -Path $DataDir,(Join-Path $DataDir 'config'),(Join-Path $DataDir 'logs'),(Join-Path $DataDir 'backups') | Out-Null
$envFile = Join-Path $DataDir 'config\production.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    $publicUrl = (Read-Host 'URL pública HTTPS definitiva, por ejemplo https://predios.entidad.gov.co').Trim().TrimEnd('/')
    $uri = $null
    if (-not [Uri]::TryCreate($publicUrl, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or
        $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or $uri.UserInfo) {
        throw 'La URL debe ser un dominio HTTPS sin ruta, parámetros ni credenciales.'
    }
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = [Convert]::ToBase64String($bytes)
    $lines = @('PREDIOS_ENV=production',"PREDIOS_DATA_DIR=$DataDir","PREDIOS_ORTHOPHOTO_ROOT=$OrthoDir","PREDIOS_SECRET_KEY=$secret","PREDIOS_PUBLIC_BASE_URL=$publicUrl",'PREDIOS_COOKIE_SECURE=true',"PREDIOS_QGIS_ROOT=$QgisRoot",'PREDIOS_ASSET_MODE=local','PREDIOS_WINDOW_RADIUS_M=2000')
    [IO.File]::WriteAllLines($envFile, $lines)
}
. (Join-Path $resolvedInstall 'scripts\Cargar-Entorno.ps1') -Path $envFile
if ($env:PREDIOS_ENV -ne 'production') { throw "$envFile no está configurado para producción." }
if ($env:PREDIOS_DATA_DIR -ne $DataDir -or $env:PREDIOS_ORTHOPHOTO_ROOT -ne $OrthoDir) {
    throw "Las rutas de $envFile no coinciden con los parámetros de instalación. Revise el archivo antes de continuar."
}
if ($env:PREDIOS_QGIS_ROOT -ne $QgisRoot) {
    throw "PREDIOS_QGIS_ROOT en $envFile no coincide con $QgisRoot. Corrija el archivo antes de continuar."
}
$candidates = @()
if ($PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "No existe Python: $PythonExe" }
    $candidates += $PythonExe
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $launcherCandidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += ($launcherCandidate | Select-Object -First 1).Trim() }
    }
    foreach ($commandName in @('python', 'python3.12')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
}
$bootstrap = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    $compatible = & $candidate -c 'import sys,struct; print(sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8)' 2>$null
    if ($LASTEXITCODE -eq 0 -and $compatible -eq 'True') { $bootstrap = $candidate; break }
}
if (-not $bootstrap) { throw 'Se requiere Python 3.12 de 64 bits. Instálelo o indique su python.exe mediante -PythonExe.' }
$python = Join-Path $resolvedInstall '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrap -m venv (Join-Path $resolvedInstall '.venv')
    Assert-NativeSuccess 'Crear el entorno Python'
}
& $python -m pip install -r (Join-Path $resolvedInstall 'requirements.lock')
Assert-NativeSuccess 'Instalar dependencias'
& $python -m alembic -c (Join-Path $resolvedInstall 'alembic.ini') upgrade head
Assert-NativeSuccess 'Migrar la base de datos'
& $python -m app.cli has-admin
if ($LASTEXITCODE -eq 1) {
    $secure = Read-Host 'Contraseña inicial del administrador (mínimo 12 caracteres)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:ADMIN_INITIAL_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        & $python -m app.cli init-admin --username admin
        Assert-NativeSuccess 'Crear el administrador'
    } finally {
        Remove-Item Env:ADMIN_INITIAL_PASSWORD -ErrorAction SilentlyContinue
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
} else { Assert-NativeSuccess 'Comprobar el administrador' }

$webScript = Join-Path $resolvedInstall 'scripts\RUN_WEB.ps1'
$workerScript = Join-Path $resolvedInstall 'scripts\RUN_WORKER.ps1'
schtasks /Create /TN 'PrediosRemotosInt-Web' /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$webScript`" -RepoDir `"$resolvedInstall`" -EnvFile `"$envFile`"" /F
Assert-NativeSuccess 'Registrar la tarea web'
schtasks /Create /TN 'PrediosRemotosInt-Worker' /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$workerScript`" -RepoDir `"$resolvedInstall`" -EnvFile `"$envFile`"" /F
Assert-NativeSuccess 'Registrar la tarea del trabajador'
schtasks /Run /TN 'PrediosRemotosInt-Web'
Assert-NativeSuccess 'Iniciar la tarea web'
schtasks /Run /TN 'PrediosRemotosInt-Worker'
Assert-NativeSuccess 'Iniciar la tarea del trabajador'
$verified = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8030/health' -TimeoutSec 3
        $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8030/ready' -TimeoutSec 3
        if ($health.status -eq 'ok' -and $health.version -eq $Version.TrimStart('v') -and $ready.status -eq 'ready') {
            $verified = $true
            break
        }
    } catch { Start-Sleep -Milliseconds 200 }
}
if (-not $verified) { throw 'Las tareas se registraron, pero /health o /ready no respondió correctamente. Revise el Visor de eventos antes de publicar el túnel.' }
$tasks = @(Get-ScheduledTask -TaskName 'PrediosRemotosInt-Web','PrediosRemotosInt-Worker')
if ($tasks.Count -ne 2 -or @($tasks | Where-Object { $_.State -ne 'Running' }).Count -gt 0) {
    throw 'La web responde, pero una tarea no sigue en ejecución. Revise PrediosRemotosInt-Web y PrediosRemotosInt-Worker antes de publicar.'
}
Write-Host "Predios Remotos $Version instalado y verificado en http://127.0.0.1:8030." -ForegroundColor Green
Write-Host 'Configure Cloudflare Tunnel hacia http://127.0.0.1:8030. Nunca guarde el token en Git.' -ForegroundColor Yellow
