# Paso a producción

Esta guía instala **Predios Remotos** en un único PC Windows de la entidad. La aplicación y el trabajador quedan como tareas de inicio, los datos permanecen fuera del repositorio y Cloudflare Tunnel publica el servicio local sin abrir puertos entrantes.

## 1. Preparar una versión en el PC de desarrollo

Desde el repositorio:

```powershell
.\PREPARAR_VERSION.ps1
git status
git tag -a v0.3.1 -m "Predios Remotos v0.3.1"
git push origin v0.3.1
```

Use una etiqueta nueva en cada entrega (`v0.1.1`, `v0.2.0`, etc.). Producción se actualiza únicamente a etiquetas revisadas, no a cambios sueltos de una rama.

## 2. Preparar el PC de producción

Instale:

- Git para Windows.
- Python 3.12 de 64 bits.
- QGIS 3.44.3 con GDAL y el controlador necesario para las ortofotos.
- `cloudflared` de 64 bits para Windows.

Defina de antemano:

- Carpeta de aplicación: `C:\PrediosRemotos\app`.
- Carpeta de datos: `D:\PrediosRemotos\datos`.
- Carpeta de ortofotos: `D:\PrediosRemotos\ortofotos`.
- Dominio HTTPS definitivo, por ejemplo `https://predios.entidad.gov.co`.

La carpeta de datos debe estar en un disco con respaldo y espacio suficiente para teselas. No coloque base de datos, ortofotos, teselas ni secretos dentro del checkout de Git.

## 3. Primera instalación

Abra PowerShell **como administrador**, ubique una copia de `INSTALAR_PROD.ps1` y ejecute:

```powershell
.\INSTALAR_PROD.ps1 `
  -RepoUrl 'https://github.com/CulmaDiego/PrediosJEG.git' `
  -InstallDir 'C:\PrediosRemotos\app' `
  -DataDir 'D:\PrediosRemotos\datos' `
  -OrthoDir 'D:\PrediosRemotos\ortofotos' `
  -Version 'v0.3.1'
```

El instalador solicita el dominio HTTPS público y la contraseña inicial del administrador. También crea `production.env`, instala las dependencias fijadas, aplica Alembic y registra `PrediosRemotos-Web` y `PrediosRemotos-Worker`.

Compruebe el origen local antes de publicarlo:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Get-ScheduledTask -TaskName 'PrediosRemotos-*'
```

Las dos primeras respuestas deben indicar `ok` y `ready`.

## 4. Publicar con Cloudflare Tunnel

En el panel de Cloudflare cree un túnel administrado remotamente y agregue una ruta de aplicación publicada:

- Hostname: el dominio definitivo.
- Service URL: `http://localhost:8000`.

En CMD o PowerShell como administrador instale el servicio con el comando que muestra Cloudflare:

```powershell
cloudflared.exe service install <TUNNEL_TOKEN>
```

El token permite ejecutar el túnel: no lo guarde en Git, documentación, capturas ni `production.env`. La guía oficial explica la [creación y publicación del túnel](https://developers.cloudflare.com/tunnel/get-started/) y el [manejo y rotación del token](https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/).

Verifique desde otro dispositivo:

```text
https://predios.entidad.gov.co/health
https://predios.entidad.gov.co/ready
```

Luego pruebe inicio de sesión, creación manual e importación de una persona de ensayo, generación y verificación de teselas, descarga del enlace listo, apertura pública, envío y descarga de la última versión.

## 5. Actualizaciones posteriores

Primero publique una etiqueta nueva desde desarrollo. En el PC de producción, abra PowerShell como administrador:

```powershell
Set-Location C:\PrediosRemotos\app
.\ACTUALIZAR_PROD.ps1 -Version v0.3.2
```

El actualizador exige un checkout limpio, crea y verifica un respaldo completo, detiene web y trabajador, instala la etiqueta, aplica migraciones, reinicia y valida `/health` y `/ready`. Si falla, restaura juntos el commit y el respaldo.

## 6. Eliminación de personas

La eliminación desde el panel es permanente para los datos activos:

- elimina persona, enlace público, versiones de dibujo, teselas y trabajos;
- elimina el archivo de importación asociado;
- invalida y borra las exportaciones generadas, incluido Shapefile;
- impide que la persona vuelva a aparecer en enlaces y reportes futuros.

Los respaldos históricos no se reescriben. Esto permite recuperar una eliminación accidental y evita destruir la cadena de recuperación. Proteja y rote esos respaldos según la política de retención de la entidad.

## Lista final antes de anunciar el servicio

- [ ] `/health` responde `ok` local y públicamente.
- [ ] `/ready` responde `ready` local y públicamente.
- [ ] `production.env` está fuera de Git y usa `PREDIOS_ENV=production`.
- [ ] `PREDIOS_PUBLIC_BASE_URL` coincide con el dominio HTTPS.
- [ ] `PREDIOS_COOKIE_SECURE=true` y `PREDIOS_ASSET_MODE=local`.
- [ ] Las tareas Web y Worker arrancan después de reiniciar Windows.
- [ ] Cloudflare Tunnel aparece conectado.
- [ ] Se hizo una copia de seguridad y una restauración de ensayo.
- [ ] Se probó un flujo completo con datos de ensayo.
