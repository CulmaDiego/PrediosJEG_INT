# Instalación interna

Siga [README](../README.md) para rutas, puerto 8030, arranque, respaldo y Cloudflare.

Instale la etiqueta `v1.0.1` de `CulmaDiego/PrediosJEG_INT`. Las ortofotos deben existir y ser legibles para la cuenta que ejecuta el trabajador. Pueden referenciarse desde la carpeta de originales del proyecto público: la aplicación solo las lee.

El instalador requiere PowerShell como administrador para registrar las tareas de arranque `PrediosRemotosInt-Web` y `PrediosRemotosInt-Worker`. No ejecute simultáneamente esas tareas y el supervisor del inicio de sesión; use un solo mecanismo de arranque.

Confirme `/health` y `/ready`, acceda a `/admin`, revise el catálogo y prepare un registro con coordenadas dentro de una ortofoto. Abra su enlace interno, use “Ver ortofoto completa”, desplácese lejos del punto y vuelva con “Punto inicial”. El modo interno no usa un radio de imagen.

El túnel nuevo debe apuntar a `http://127.0.0.1:8030`. Actualice el hostname HTTPS y cookies seguras en la configuración externa y reinicie solo los procesos internos. Las credenciales del túnel no se guardan en Git.
