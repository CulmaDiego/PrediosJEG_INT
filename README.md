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
- QGIS 3.44.x con GDAL. El desarrollo se validó con 3.44.3; producción admite 3.44.13 indicando `-QgisRoot`. Para ECW, confirme que exista `gdal_ECW_JP2ECW.dll` y que PROJ encuentre `proj.db`.
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
  -InstallDir 'D:\PrediosRemotos\app' `
  -DataDir 'D:\PrediosRemotos\datos' `
  -OrthoDir 'D:\PrediosRemotos\ortofotos' `
  -QgisRoot 'C:\Program Files\QGIS 3.44.13' `
  -Version 'v0.3.6'
```

Antes de activar Internet, compruebe localmente:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Después instale `cloudflared`, cree un túnel hacia `http://127.0.0.1:8000` y guarde el token en el servicio de Windows de Cloudflare. No copie el token al código, `.env` del repositorio ni GitHub. La URL pública debe usar HTTPS.

## Actualizar producción a una versión elegida

En el PC de empresa, dentro de `D:\PrediosRemotos\app`:

```powershell
.\ACTUALIZAR_PROD.ps1 -Version v0.3.6
```

El script exige un checkout limpio y una etiqueta existente; crea y verifica un respaldo consistente, detiene servicios, instala esa versión, migra la base, reinicia y comprueba `/health` y `/ready`. Si algo falla, detiene procesos y restaura el commit y el respaldo completo.

Una migración irreversible no se “deshace” con una promesa automática. La recuperación segura es restaurar juntos el código anterior y la copia completa de datos. Consulte [operación y recuperación](docs/OPERACION_PRODUCCION.md).

## Ortofotos, teselas y modo sin CDN

El catálogo usa `gdalinfo` sin cargar rásteres completos. El radio inicial de la ventana es 2000 m, pero el administrador puede cambiarlo en **Configuración de teselas** entre 100 y 50.000 m. El valor se guarda en la base de datos y se aplica a cada persona nueva y a las regeneraciones posteriores; las ventanas existentes y las personas que ya enviaron su dibujo se conservan.

La aplicación conserva las ortofotos completas en su formato y resolución originales. La preparación crea referencias privadas a sus bandas RGB, sin recortar, reproyectar ni reducir los originales, y verifica una tesela en el punto de referencia antes de habilitar el enlace. El servidor lee solo las zonas visibles, prioriza la fuente de mayor resolución cuando se superponen y guarda PNG sin pérdida en una caché por ventana. Cada tesela lleva una máscara circular; los píxeles fuera del radio autorizado se borran en RGB y transparencia en el servidor. Antes de habilitar el enlace, el trabajador prepara la cobertura general y una zona cercana al punto en cada nivel de zoom. Al abrir el mapa, el navegador descarga y decodifica esa precarga antes de habilitar las herramientas; también adelanta las vistas de zoom cercanas al navegar. Los lectores GDAL permanecen abiertos con memoria limitada para cargar otras zonas sin iniciar un proceso por tesela. Cada petición valida el enlace, y el zoom máximo se calcula a partir de la resolución real de la fuente. Las ventanas anteriores siguen siendo compatibles; regenerarlas activa el modo ORIGINAL.

Las personas pueden registrarse individualmente desde **Personas → Crear una persona** o cargarse de forma masiva con Excel/CSV. Al crear o importar un registro con coordenadas se agenda la preparación de su imagen. También puede seleccionar personas o usar el botón de un municipio para regenerar y verificar sus teselas. La descarga masiva de enlaces incluye las personas cuya fuente privada esté lista; las teselas detalladas se completan bajo demanda.

Desde **Personas → Informe de seguimiento** se descarga un Excel con resumen de respuestas, aperturas, pendientes y estado de imágenes, más una hoja detallada por persona. El mismo informe puede limitarse a un municipio.

La interfaz administrativa y el flujo público incorporan la identidad visual de **Jorge Eliecer Gaitán Ingeniería, Consultoría y Valoración**. El isotipo se presenta sin deformación junto al nombre legible en escritorio, celular, acceso administrativo y mensajes públicos. En el mapa, el usuario puede alejarse hasta encuadrar completo el radio permitido y acercarse hasta el nivel correspondiente a la resolución de la ortofoto.

La interfaz fija Leaflet 1.9.4 y Leaflet-Geoman 2.18.3. Ambos ya están incluidos en el repositorio; el instalador configura `PREDIOS_ASSET_MODE=local`, sin depender del CDN. El inventario real está en [docs/ORTOFOTOS.md](docs/ORTOFOTOS.md).

## Seguridad y alcance

- Administrador único con Argon2id, sesión vencible, cookies `HttpOnly`, `Secure` en producción y `SameSite=Strict`.
- CSRF, CSP, CORS no abierto, rate limiting, ORM, rutas generadas por servidor y filtros permitidos.
- Tokens públicos aleatorios de 256 bits; solo se almacena SHA-256. Regenerar revoca el anterior.
- Polygon WGS84 con 3–100 vértices, anillo cerrado, sin autointersecciones, área positiva y todos los vértices dentro del radio de la ventana de ortofoto de esa persona.
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

## Aplicar la corrección de ortofotos en esta instalación

Con el código actualizado, ejecute desde PowerShell **como administrador**:

```powershell
D:\PrediosRemotos\app\APLICAR_CORRECCION_ORTOFOTOS.ps1
```

El script respalda y verifica los datos, identifica los procesos de este checkout, reinicia web y trabajador y vuelve a agendar las imágenes fallidas. Conserva las URLs y los dibujos enviados. Este permiso de Windows es necesario cuando los procesos actuales se iniciaron como administrador. No descarga código ni cambia los originales.

El dibujo por puntos conserva sus vértices originales (hasta el límite de 100 del servidor), incluso al restaurar el borrador, editar o deshacer. Solo el trazo a mano se simplifica, con tolerancia inicial de 1,25 píxeles y hasta 40 vértices para conservar curvas.
