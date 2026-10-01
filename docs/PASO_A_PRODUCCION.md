# Paso a producción en el disco D:

Esta guía instala **Predios Remotos v0.3.4** en un único PC Windows de la oficina. Código, base de datos, teselas y ortofotos quedan en `D:`; QGIS y Python pueden seguir instalados en `C:`. No conecte el dominio público hasta completar las comprobaciones locales.

## 1. Preparar el equipo de oficina

1. Confirme que `D:` es un **disco local fijo**, disponible después de reiniciar Windows, con espacio suficiente para los rásteres originales, teselas, exportaciones y respaldos. No use una unidad de red mapeada: las tareas se ejecutan como `SYSTEM`.
2. Instale Git para Windows, Python **3.12 de 64 bits** (la versión 3.12.2 sirve), y QGIS **3.44.13** con GDAL y soporte ECW. El instalador acepta tanto el lanzador `py` como `python` en PATH; si ninguno apunta al ejecutable correcto, use `-PythonExe`. Si QGIS está en otra ruta, use `-QgisRoot`.
3. Reserve un dominio ya administrado en Cloudflare, por ejemplo `https://predios.entidad.gov.co`. El instalador pedirá la URL real. No use `127.0.0.1` como URL pública.
4. Obtenga las ortofotos desde el almacenamiento autorizado de la entidad y cópielas **antes de instalar** a `D:\PrediosRemotos\ortofotos`, conservando las subcarpetas de municipio y los archivos `.ecw`/`.tif`. No copie `Imagenes` dentro de `app` ni agregue los rásteres a Git. Consulte [el inventario esperado](ORTOFOTOS.md). El instalador rechaza una carpeta vacía.
5. Compruebe que el cortafuegos/antivirus permita ejecutar Python y GDAL localmente, y que el equipo pueda llegar a GitHub y al índice de paquetes de Python durante la instalación. Para el túnel necesitará salida HTTPS hacia Cloudflare; no abra el puerto 8000 a Internet.

Compruebe Python y la ubicación de QGIS en PowerShell:

```powershell
python --version
python -c 'import struct; print(struct.calcsize("P") * 8)'
$qgis = 'C:\Program Files\QGIS 3.44.13'
Test-Path "$qgis\bin\gdalinfo.exe"
Test-Path "$qgis\share\proj\proj.db"
```

Ambas rutas deben existir. Para una prueba manual de ECW, configure antes `PROJ_DATA` y `GDAL_DRIVER_PATH` en **esa misma sesión**; `gdalinfo --formats` puede enumerar ECW y a la vez emitir errores `PROJ: ... no database context specified` si se omite `PROJ_DATA`:

```powershell
$env:PROJ_DATA = "$qgis\share\proj"
$env:PROJ_LIB = $env:PROJ_DATA
$env:GDAL_DATA = "$qgis\apps\gdal\share\gdal"
$env:GDAL_DRIVER_PATH = "$qgis\apps\gdal\lib\gdalplugins"
$env:PATH = "$qgis\bin;$env:PATH"
& "$qgis\bin\gdalinfo.exe" --formats | Select-String '^\s*ECW\s'
```

El instalador configura estas rutas automáticamente, verifica que exista `proj.db` y prueba la lectura de cada ortofoto. Si ECW no aparece, resuelva el controlador antes de importar personas. Las fuentes ráster son de solo lectura para la aplicación.

## 2. Instalar la versión publicada

La etiqueta `v0.3.4` ya está publicada: **no la vuelva a crear ni la mueva**. Descargue una copia del repositorio o al menos `INSTALAR_PROD.ps1` de esa etiqueta en una carpeta temporal. Abra PowerShell **como administrador** en esa carpeta y ejecute:

```powershell
.\INSTALAR_PROD.ps1 `
  -RepoUrl 'https://github.com/CulmaDiego/PrediosJEG.git' `
  -InstallDir 'D:\PrediosRemotos\app' `
  -DataDir 'D:\PrediosRemotos\datos' `
  -OrthoDir 'D:\PrediosRemotos\ortofotos' `
  -QgisRoot 'C:\Program Files\QGIS 3.44.13' `
  -Version 'v0.3.4'
```

Si el repositorio exige autenticación, Git se la pedirá; no incorpore credenciales en `-RepoUrl`. El instalador clonará la etiqueta, comprobará los recursos web locales, creará `D:\PrediosRemotos\datos\config\production.env`, solicitará el dominio HTTPS y una contraseña **nueva** de al menos 12 caracteres para `admin`, migrará la base y registrará las tareas `PrediosRemotos-Web` y `PrediosRemotos-Worker`. Se detendrá si falla algún paso. La contraseña de pruebas no se traslada automáticamente.

Si ya intentó una instalación anterior y existe `production.env`, **no lo borre**: confirme que `PREDIOS_QGIS_ROOT=C:\Program Files\QGIS 3.44.13`. El instalador conservará la clave secreta y los datos existentes. Si `D:\PrediosRemotos\app` ya contiene un checkout Git limpio, también puede reutilizarlo.

**Guarde una copia segura de `production.env` fuera del PC**, con acceso restringido. Contiene `PREDIOS_SECRET_KEY`: cambiar esa clave invalida los enlaces públicos estables. Nunca suba el archivo a Git ni lo envíe por chat o correo.

## 3. Verificar antes de publicar

En el PC de oficina:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Get-ScheduledTask -TaskName 'PrediosRemotos-*' | Select-Object TaskName,State
```

Espere `status: ok`, `version: 0.3.4` y `status: ready`. Entre a `http://127.0.0.1:8000/admin` con `admin` y la contraseña recién creada. En **Ortofotos**, pulse **Actualizar catálogo** y confirme que aparezcan los municipios y que los rásteres sean legibles. Si no aparecen, revise las carpetas de `D:\PrediosRemotos\ortofotos`, el controlador GDAL y los mensajes del trabajador; **no distribuya enlaces todavía**. Cree una persona de prueba con coordenada dentro de una ortofoto, genere/verifique sus teselas y confirme que su enlace esté listo. Las ventanas usan **600 m de radio** alrededor del punto; el límite de vértices del polígono es una regla separada.

Reinicie Windows una vez y repita las comprobaciones: las dos tareas deben arrancar con el disco `D:` disponible.

## 4. Publicar mediante Cloudflare Tunnel

En el panel de Cloudflare cree un **túnel administrado remotamente**. Instale `cloudflared` como servicio de Windows usando exactamente el comando/token que el panel le muestra para ese túnel. Configure una **ruta de aplicación publicada** con el hostname definitivo y Service URL `http://localhost:8000`. Espere a que el túnel indique conexión y compruebe desde otro dispositivo:

```text
https://predios.entidad.gov.co/health
https://predios.entidad.gov.co/ready
```

Use su dominio real. El token del túnel es secreto: no lo guarde en Git, `production.env`, capturas ni documentación. Cloudflare publica el hostname para cualquiera que conozca el enlace; si requiere restringir el panel de administración, diseñe esa política sin bloquear las rutas públicas `/p/...` y las teselas de cada persona. Consulte la [guía oficial del túnel](https://developers.cloudflare.com/tunnel/get-started/).

Pruebe desde un celular externo: apertura del enlace, visualización de la ortofoto, dibujo, envío, pantalla de recibido y descarga de la última versión desde el panel. No comparta enlaces reales hasta que la imagen de cada persona figure como **LISTO**.

## 5. ¿Se deben mover datos del PC de pruebas?

**Instalación nueva (recomendada):** no copie la base de desarrollo ni su contraseña. Importe el Excel/CSV real o cree personas en producción y genere/verifique las teselas allí. Los enlaces de prueba no son los enlaces definitivos.

**Continuar datos/enlaces existentes:** no copie solo `predios.db`. Se requiere una copia consistente de la base, los directorios operativos y la **misma** `PREDIOS_SECRET_KEY`, con sus rutas de ortofotos adaptadas al nuevo equipo. Haga esto como migración supervisada y verificada en una copia de ensayo antes de abrir el servicio público. Consulte [respaldo y recuperación](OPERACION_PRODUCCION.md). El instalador de primera vez no migra automáticamente datos del PC de desarrollo.

## 6. Actualizaciones posteriores

Cuando se publique una **nueva** etiqueta, en PowerShell como administrador:

```powershell
Set-Location D:\PrediosRemotos\app
.\ACTUALIZAR_PROD.ps1 -Version v0.3.5
```

`v0.3.5` es solo un ejemplo de etiqueta futura: use la que realmente esté publicada. El actualizador exige un checkout limpio, crea y verifica un respaldo completo, cambia el código, aplica migraciones, reinicia y valida `/health` y `/ready`. Conserve copias de seguridad también fuera de `D:`; un fallo físico de ese disco puede afectar app y datos a la vez.

## Lista de salida

- [ ] Ortofotos originales presentes en `D:\PrediosRemotos\ortofotos` y municipios visibles en el catálogo.
- [ ] `D:\PrediosRemotos\datos\config\production.env` fuera de Git y respaldado de forma segura.
- [ ] `/health` y `/ready` correctos local y públicamente, incluso tras reiniciar.
- [ ] Las tareas Web y Worker activas; túnel conectado.
- [ ] Una persona de prueba ve imagen, puede enviar y produce exportación de última versión.
- [ ] Respaldo probado y custodiado fuera del disco `D:`.
