# Operación, respaldo y recuperación

## Verificar un respaldo

Cada actualización crea una carpeta `backups\antes-<versión>-<fecha>` con la base copiada mediante la API de respaldo en línea de SQLite, el manifiesto y los datos operativos. El script ejecuta automáticamente `PRAGMA integrity_check` sobre la copia.

Puede repetir la verificación sin detener la aplicación:

```powershell
.\scripts\Cargar-Entorno.ps1 -Path 'D:\PrediosRemotos\datos\config\production.env'
.\.venv\Scripts\python.exe -m app.cli verify-backup --source 'D:\PrediosRemotos\datos\backups\antes-v0.3.0-AAAAMMDD-HHMMSS'
```

Además, compruebe periódicamente que la carpeta contenga `manifest.json`, `predios.db` y las carpetas de teselas/importaciones/exportaciones esperadas. Haga una restauración de ensayo en otro directorio y nunca sobre producción activa.

## Restaurar manualmente

1. Detenga `PrediosRemotos-Web` y `PrediosRemotos-Worker`.
2. Lleve el código al commit compatible que figura en el registro de la actualización.
3. Cargue el archivo de entorno de producción.
4. Restaure la copia completa, no solo tablas sueltas.
5. Reinstale las dependencias de ese commit y reinicie ambos procesos.
6. Compruebe `/health`, `/ready`, inicio de sesión y un enlace público de prueba.

`PREDIOS_SECRET_KEY` debe conservarse entre reinstalaciones y restauraciones: además de proteger las sesiones, determina los enlaces públicos estables. Cambiarla invalida esos enlaces.

```powershell
schtasks /End /TN 'PrediosRemotos-Web'
schtasks /End /TN 'PrediosRemotos-Worker'
git checkout --detach <commit-anterior>
.\.venv\Scripts\python.exe -m app.cli restore-backup `
  --source 'D:\PrediosRemotos\datos\backups\antes-v0.3.0-AAAAMMDD-HHMMSS' `
  --confirm RESTAURAR_RESPALDO_COMPLETO
schtasks /Run /TN 'PrediosRemotos-Worker'
schtasks /Run /TN 'PrediosRemotos-Web'
```

Una migración que elimina o transforma datos puede ser irreversible. Alembic no garantiza recuperar esos datos mediante `downgrade`; por eso `ACTUALIZAR_PROD.ps1` restaura código y respaldo completo juntos.

## Cloudflare Tunnel

El origen es exclusivamente `http://127.0.0.1:8000`. Cloudflare entrega HTTPS público y la aplicación marca sus cookies como `Secure`. Instale el túnel como servicio de Windows bajo una cuenta restringida. El token pertenece a la configuración del servicio y nunca al repositorio.

## Carga prevista y SQLite

El alcance es un servidor de oficina, un administrador y una carga pública moderada. La prueba automatizada ejecuta lectores concurrentes mientras escribe eventos y confirma WAL + `busy_timeout`. Antes de ampliar a varios trabajadores web o campañas masivas, mida con la concurrencia real. Si aparecen esperas sostenidas, `database is locked` o latencias fuera del objetivo, conserve evidencia (tasa, percentiles, errores) y planifique una migración posterior a un servidor de base de datos; no agregue otro servidor solo por anticipación.
