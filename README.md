# Predios Remotos

Aplicación local para que una entidad organice personas por municipio, prepare una ventana privada de ortofoto para cada una y reciba polígonos dibujados desde el celular.

La solución usa una sola aplicación FastAPI, HTML/Jinja2, Leaflet + Geoman, SQLAlchemy, SQLite en modo WAL, Alembic y un trabajador local. No requiere Docker, Node como servidor, PostgreSQL ni servicios de almacenamiento en la nube.

## El flujo habitual

```text
PC desarrollo: cambiar código → probar → confirmar en Git → subir a GitHub → crear etiqueta.
PC empresa: ejecutar ACTUALIZAR_PROD.ps1 con esa etiqueta → respaldo → actualización → verificación.
```

## Desarrollo en Windows

Requisitos:

- Git para Windows.
- Python 3.12 de 64 bits.
- QGIS 3.44.3 con GDAL. Para ECW, confirme que exista `gdal_ECW_JP2ECW.dll`.
- Las ortofotos dentro de `Imagenes/`. La aplicación solo las lee.

Abra PowerShell en el repositorio y ejecute:

```powershell
.\INICIAR_DEV.ps1
```

El primer inicio crea `..\PrediosRemotos_data_dev`, instala versiones fijadas, aplica migraciones, solicita la contraseña del administrador e inicia web y trabajador. QGIS aporta GDAL, pero no se usa como servidor Python. Abra `http://127.0.0.1:8000`. Nunca usa datos de producción por defecto.

Los enlaces públicos son estables: reiniciar una respuesta elimina sus dibujos y devuelve la persona a pendiente, pero conserva la misma URL. El panel permite copiar un enlace individual y descargar en Excel todos los enlaces o los de un municipio. Cada envío crea una versión nueva; las exportaciones GIS y Excel incluyen solo la última versión vigente de cada persona.

Los datos quedan fuera del checkout:

```text
PrediosRemotos_data_dev/
  predios.db
  imports/  exports/  tiles/  logs/  backups/  config/
```

Para preparar una versión:

```powershell
.\PREPARAR_VERSION.ps1
```

El script ejecuta pruebas, comprueba dependencias y muestra el estado de Git. No publica ni sobrescribe cambios. Después muestra los comandos de commit, push y etiqueta para que usted los revise.

## Primera instalación en el PC de empresa

Inicie PowerShell como administrador y ejecute `INSTALAR_PROD.ps1` desde una copia del repositorio o descargue primero el archivo. El proceso comprueba Git/QGIS, clona el repositorio privado, crea el entorno, guarda secretos fuera de Git, aplica Alembic, crea el administrador y registra dos tareas de inicio de Windows.

```powershell
.\INSTALAR_PROD.ps1 `
  -RepoUrl 'https://github.com/CulmaDiego/PrediosJEG.git' `
  -InstallDir 'C:\PrediosRemotos\app' `
  -DataDir 'D:\PrediosRemotos\datos' `
  -OrthoDir 'D:\PrediosRemotos\ortofotos' `
  -Version 'v0.1.0'
```

Antes de activar Internet, compruebe localmente:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Después instale `cloudflared`, cree un túnel hacia `http://127.0.0.1:8000` y guarde el token en el servicio de Windows de Cloudflare. No copie el token al código, `.env` del repositorio ni GitHub. La URL pública debe usar HTTPS.

## Actualizar producción a una versión elegida

En el PC de empresa, dentro de `C:\PrediosRemotos\app`:

```powershell
.\ACTUALIZAR_PROD.ps1 -Version v0.1.0
```

El script exige un checkout limpio y una etiqueta existente; crea y verifica un respaldo consistente, detiene servicios, instala esa versión, migra la base, reinicia y comprueba `/health` y `/ready`. Si algo falla, detiene procesos y restaura el commit y el respaldo completo.

Una migración irreversible no se “deshace” con una promesa automática. La recuperación segura es restaurar juntos el código anterior y la copia completa de datos. Consulte [operación y recuperación](docs/OPERACION_PRODUCCION.md).

## Ortofotos, teselas y modo sin CDN

El catálogo usa `gdalinfo` sin cargar rásteres completos. Las ventanas de 350 m se reproyectan a Web Mercator y solo se generan teselas XYZ de los niveles configurados, fuera de `Imagenes/`. Cada petición de tesela vuelve a validar el enlace de esa persona.

La interfaz fija Leaflet 1.9.4 y Leaflet-Geoman 2.18.3. En producción ejecute una vez:

```powershell
.\scripts\DESCARGAR_FRONTEND.ps1
```

y configure `PREDIOS_ASSET_MODE=local`; así no depende del CDN. El inventario real está en [docs/ORTOFOTOS.md](docs/ORTOFOTOS.md).

## Seguridad y alcance

- Administrador único con Argon2id, sesión vencible, cookies `HttpOnly`, `Secure` en producción y `SameSite=Strict`.
- CSRF, CSP, CORS no abierto, rate limiting, ORM, rutas generadas por servidor y filtros permitidos.
- Tokens públicos aleatorios de 256 bits; solo se almacena SHA-256. Regenerar revoca el anterior.
- Polygon WGS84 con 3–100 vértices, anillo cerrado, sin autointersecciones, área positiva y todos los vértices a 300 m o menos del punto.
- SQLite con WAL, `busy_timeout=30000`, transacciones cortas e índices para la carga prevista de un único servidor.

No ponga bases, Excel reales, teselas, exportaciones, respaldos, logs, secretos ni ortofotos en Git. `.gitignore` los excluye desde el inicio.

## Comandos técnicos útiles

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.worker --once
```

El avance y la evidencia de verificación se mantienen en [docs/ESTADO.md](docs/ESTADO.md).

Para instalar o actualizar el PC definitivo, siga la [guía paso a paso de producción](docs/PASO_A_PRODUCCION.md).
