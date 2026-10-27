# Predios Remotos Interno

Aplicación de oficina independiente de Predios Remotos. Repositorio: https://github.com/CulmaDiego/PrediosJEG_INT.git. Versión interna: **1.0.1**.

## Acceso en este equipo

- Administración: https://interno.prediosjeg.site/admin
- Salud: http://127.0.0.1:8030/health y http://127.0.0.1:8030/ready
- Código: `D:\PrediosRemotosInt\app`.
- Base, configuración, caché y registros: `D:\PrediosRemotosInt\datos`.
- Ortofotos: lectura de `D:\PrediosRemotos\ortofotos`, sin modificar ni duplicar los originales.

La instalación interna conserva una copia consistente de las personas, dibujos, catálogo y administrador de la aplicación pública. La contraseña del administrador es la misma que tenía al crear la copia. Los cambios posteriores son independientes. La clave de firma, las sesiones y los enlaces internos son propios; genere los enlaces desde este panel.

## Cobertura completa

Cada registro mantiene su punto de referencia. El mapa inicia centrado allí y permite navegar por **las ortofotos habilitadas y listas asignadas al municipio**, sin radio de recorte ni límite de distancia para el dibujo. Los botones **Punto inicial** y **Ver ortofoto completa** permiten alternar entre ambas vistas. Se conserva la validación de polígonos, coordenadas, vértices y autointersecciones.

El trabajador prepara referencias VRT a los originales, una vista general de toda la cobertura y detalle cercano al punto de referencia. Al navegar, el servidor genera únicamente las teselas necesarias a la resolución original. Los PNG quedan en una caché compartida entre personas que usan los mismos archivos, además de la caché del navegador. Regenerar un registro conserva esa caché. Cambiar el tamaño o fecha de modificación de un original produce una nueva clave de caché al regenerar.

La primera visita a una zona o nivel de detalle todavía no preparado puede requerir lectura de disco. No se descarga anticipadamente la ortofoto completa a resolución máxima ni se promete funcionamiento sin conexión. Esta estrategia permite recorrer imágenes grandes sin saturar la memoria del navegador.

La caché compartida está en `datos/tiles/_full_cache`. No se elimina al borrar una persona. Los respaldos completos incluyen la caché; las versiones antiguas de archivos pueden limpiarse de forma programada con la aplicación detenida, manteniendo las claves referidas por los manifiestos actuales.

## Activar o desactivar ortofotos

En **Administración → Ortofotos**, cada imagen tiene un estado de uso y un botón **Activar/Desactivar**. Desactivarla no elimina ni modifica el archivo original. La selección se conserva al reescanear el catálogo.

Cada cambio prepara de nuevo los mapas del municipio, incluidos los registros con respuesta, conservando sus dibujos, versiones y enlaces. Durante la preparación, el enlace indica que la imagen se está actualizando. Cuando termina, recargue los mapas que ya tenía abiertos. Si ninguna imagen habilitada cubre el punto, se muestra “No hay ortofoto habilitada” con acceso a administración.

Las cuatro ortofotos de las carpetas `Urbano` de esta instalación están desactivadas; pueden reactivarse desde el mismo panel. Esta selección pertenece a la base interna y no afecta al proyecto público.

## Inicio y operación

En este equipo, `INICIAR_INTERNO.ps1` lanza un supervisor oculto que mantiene web y trabajador activos. Hay un acceso en el inicio de sesión de Windows del usuario actual. Ejecutarlo otra vez no crea otro supervisor. Los registros se guardan en `datos/logs/WEB.*.log` y `WORKER.*.log`.

```powershell
Set-Location D:\PrediosRemotosInt\app
.\INICIAR_INTERNO.ps1
```

Los scripts de instalación y actualización usan exclusivamente `PrediosRemotosInt-Web`, `PrediosRemotosInt-Worker` y el puerto **8030**. Para instalar como tareas de arranque del sistema en otro equipo, ejecute PowerShell como administrador:

```powershell
.\INSTALAR_PROD.ps1 -RepoUrl 'https://github.com/CulmaDiego/PrediosJEG_INT.git' `
  -InstallDir 'D:\PrediosRemotosInt\app' -DataDir 'D:\PrediosRemotosInt\datos' `
  -OrthoDir 'D:\PrediosRemotos\ortofotos' -QgisRoot 'C:\Program Files\QGIS 3.44.13' `
  -Version 'v1.0.1'
```

## Cloudflare

Configure la nueva ruta del túnel con Service URL **`http://127.0.0.1:8030`**. No cambie el destino del sitio público, que permanece en 8000. El dominio `interno.prediosjeg.site` ya está conectado al origen interno.

La configuración de esta instalación ya usa el hostname HTTPS y cookies seguras. En otras instalaciones, ajuste fuera de Git `D:\PrediosRemotosInt\datos\config\production.env`:

```dotenv
PREDIOS_PUBLIC_BASE_URL=https://interno.prediosjeg.site
PREDIOS_COOKIE_SECURE=true
PREDIOS_FULL_ORTHOPHOTO=true
```

Reinicie exclusivamente los procesos internos con `REINICIAR_INTERNO.ps1` después de cambiar la configuración. Este script identifica los procesos por la ruta del checkout interno. Para restringir el hostname a la oficina, configure la política de acceso correspondiente en Cloudflare. No guarde tokens del túnel ni secretos en Git.

## Desarrollo y verificación

Python 3.12, FastAPI, SQLite, Leaflet/Geoman locales y GDAL de QGIS 3.44.13. El modo completo se activa por defecto mediante `Settings.from_env()`; `PREDIOS_FULL_ORTHOPHOTO=false` permite probar el comportamiento heredado. Las ventanas de modo `FULL` se preparan mediante el trabajador.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -r requirements-quality.txt
.\scripts\VERIFICAR_CALIDAD.ps1
```

Las pruebas incluyen autenticación, CSRF, importación, exportación, navegación de navegador, rásteres GDAL reales a 20 km del centro, envío de polígonos remotos y conservación de caché al regenerar. Bases, ortofotos, dibujos, secretos, logs, respaldos y teselas quedan fuera del repositorio.
