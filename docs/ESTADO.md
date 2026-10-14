# Estado del proyecto

Fecha de actualización: 2026-10-14.

## Etapa 1 — Base local y seguridad

- [x] FastAPI/Jinja2/SQLAlchemy/SQLite WAL con versiones fijadas.
- [x] Alembic y configuración separada fuera del checkout.
- [x] Administrador único, Argon2id, sesión, CSRF, CSP, cookies seguras y rate limiting.
- [x] Scripts sencillos para desarrollo, versión, instalación y actualización en Windows.
- Verificación: compilación de Python, migración y pruebas automatizadas.

## Etapa 2 — Municipios, personas e importación

- [x] Municipios activos/inactivos y listado paginado de personas.
- [x] Validación completa CSV/XLSX, errores por fila y confirmación atómica.
- [x] Alta manual de una persona con las mismas reglas de nombre, celular y coordenadas usadas por la importación.
- [x] Celular normalizado único, enlaces preservados al actualizar y coordenadas protegidas si hay dibujo.
- [x] Enlace público estable por persona, visible y copiable desde el detalle; descargas XLSX masivas para todos o por municipio.
- [x] Informe XLSX de seguimiento global o por municipio, con resumen de respuestas e imágenes y detalle por persona.
- [x] Reinicio administrativo de la respuesta sin cambiar el enlace: elimina sus versiones y devuelve el estado a pendiente.
- [x] Tablero, filtros, estados, detalle e historial.
- Verificación: pruebas HTTP de autenticación, XSS, SQL injection y archivos inválidos.

## Etapa 3 — Flujo público móvil

- [x] Token aleatorio de 256 bits almacenado solo como hash, expiración y revocación.
- [x] Mapa móvil vertical/horizontal, dibujo por puntos y a mano alzada, edición, borrado, deshacer y confirmación.
- [x] Alejamiento hasta encuadrar completo el radio permitido; zoom máximo calculado según la resolución real de la ortofoto y desplazamiento limitado a su área autorizada.
- [x] Ortofoto sin capa translúcida y pantalla final de “Respuesta recibida” después de cada envío.
- [x] Ortofoto realzada sin velos de relleno; el dibujo a mano muestra el trazo en vivo, se cierra al regresar al inicio y simplifica automáticamente los vértices para facilitar la edición.
- [x] Identidad visual actualizada con la paleta institucional `#096e74`, `#44bec6`, `#bcd3d8`, `#7e9cac`, `#162f3e`, `#35596b`, `#949cac`, `#96a4ac`, `#61808e` y `#4c6674`.
- [x] Logo institucional de Jorge Eliecer Gaitán integrado en administración, acceso, mapa móvil y mensajes públicos, con navegación y componentes visuales refinados.
- [x] Borrador en `sessionStorage` y reintento tras error de red.
- [x] Validación Polygon WGS84, 3–100 vértices, cierre, autointersección, área y distancia máxima.
- [x] Versiones inmutables y una sola vigente.
- [ ] Revisión táctil manual en teléfono físico. No se afirma completada porque no hubo un dispositivo físico disponible en esta sesión.
- Verificación: Playwright + Edge a 320, 375 y 430 px; carga de tesela privada, encuadre completo del radio permitido, zoom limitado al detalle real, desplazamiento limitado, dibujo a mano alzada, edición, confirmación, fallo de red y reintento. También se comprobó horizontal a 667 × 375 px.

## Etapa 4 — Ortofotos, trabajos y reportes

- [x] Inventario GDAL en lectura y errores por fuente sin detener el catálogo.
- [x] Instalación limpia capaz de descubrir municipios desde las carpetas, programar el catálogo inicial y reasignar nombres equivalentes con o sin tilde.
- [x] Cola SQLite reanudable, reclamo atómico, progreso y errores visibles.
- [x] Radio de ventana configurable desde el panel administrador, persistente y ampliable hasta 50 km; las ventanas y dibujos existentes se conservan.
- [x] Preparación rápida de un VRT privado por persona y teselas PNG bajo demanda con caché, para que radios grandes no generen toda la pirámide antes de publicar el enlace.
- [x] Descarga de enlaces limitada a VRTs o pirámides heredadas verificadas; los enlaces pendientes muestran una pantalla de preparación en lugar de un mapa vacío.
- [x] Autorización de cada tesela contra persona/enlace.
- [x] Exportaciones vigentes GeoJSON, GeoPackage, Shapefile y Excel.
- [x] Las exportaciones masivas, incluido Shapefile, seleccionan únicamente la última versión de cada persona.
- [x] Inventario de las ocho fuentes reales en `docs/ORTOFOTOS.md`.
- Verificación: el trabajador catalogó las 8 fuentes con GDAL; una ventana real del TIFF de San Rafael produjo 52 teselas (zoom 15–18) y quedó en estado `LISTO`.

## Etapa 5 — Producción

- [x] Instalación y tareas automáticas de Windows.
- [x] Actualización por etiqueta, respaldo verificado, comprobaciones y restauración completa ante fallo.
- [x] Guía de Cloudflare Tunnel sin secretos en Git.
- [x] Eliminación completa individual, por selección y por municipio, con confirmación e invalidación de exportaciones.
- [x] Guía operativa paso a paso para primera instalación y actualización de producción.
- Verificación local: copia consistente, `PRAGMA integrity_check` y restauración conjunta de SQLite y teselas pasaron la prueba automatizada.
- [ ] Ensayo en el PC de empresa: instalación inicial, actualización desde una etiqueta real, restauración y flujo público completo.

Producción no se considera lista hasta completar los dos puntos manuales pendientes: teléfono físico y ensayo real en el PC de empresa.

## Corrección de ortofotos originales — 2026-10-14

- [x] Eliminada la inserción duplicada de TileWindow al preparar por primera vez; actualización SQLite atómica y regeneración sin duplicados.
- [x] Fuentes originales completas, referencias RGB sin remuestreo previo y lectura de las teselas visibles con GDAL de QGIS.
- [x] Fuentes superpuestas ordenadas por resolución, con cobertura rural y detalle urbano; selección exige cobertura del punto de referencia.
- [x] Máscara circular en el servidor: RGB y alfa se borran fuera del radio EPSG:9377. Prueba con GDAL real verifica transparencia, eliminación de RGB y detalle de ambas fuentes.
- [x] Verificación de imagen válida en el punto antes de habilitar la ventana; máscaras ausentes bloquean el acceso.
- [x] Zoom máximo calculado a partir de metros por píxel; control de carga y botón Reintentar, y caché del navegador diferenciada por generación.
- [x] Script APLICAR_CORRECCION_ORTOFOTOS.ps1 con respaldo, identificación de procesos del checkout, reinicio y reintento de imágenes fallidas; conserva enlaces y dibujos enviados.
- Verificación: 43 pruebas pasaron, incluido navegador Edge en tamaños móviles. Prueba aislada con una copia de la base real y el ECW rural de San Carlos: ventana LISTO con radio 10.000 m y zoom 12–20; página y teselas HTTP 200, petición fuera de área HTTP 404. No se alteraron la base productiva ni las ortofotos originales.
- [x] Modo ORIGINAL activo en la instancia actual: se verificó una ventana LISTO con radio 5.000 m antes de preparar esta actualización.

## Precarga, zoom y dibujo — 2026-10-14

- [x] Preparación de cobertura general y zona central en todos los niveles antes de habilitar nuevos enlaces. Precarga limitada por nivel para mantener viable un radio grande.
- [x] Lectores GDAL persistentes, caché de fuentes limitada, cierre en el ciclo de vida del servidor y exclusión por grupos de teselas con memoria acotada.
- [x] El navegador descarga y decodifica la precarga antes de habilitar el mapa, con progreso y reintento; anticipa el zoom cercano durante la navegación. Las áreas lejanas nuevas conservan carga bajo demanda.
- [x] Zoom en niveles enteros, sin animaciones intermedias de carga; límites de imagen calculados en el servidor con la misma proyección que valida las teselas.
- [x] Polígono por puntos, borradores, ediciones y deshacer conservan sus vértices y precisión. Solo la finalización a mano reduce puntos, con hasta 40 vértices y tolerancia adaptativa para curvas.
- [x] Aplicación de cambios mediante cierre y reinicio exclusivamente de web/trabajador y sus lectores, con respaldo y precarga de ventanas actuales.
- Evidencia local aislada con ECW real y radio 5.000 m: 198 teselas preparadas en 39,64 s antes de habilitar; navegador móvil listo en 2,25 s, ocho acercamientos hasta zoom 20 entre 0,12 y 0,16 s cada uno, cero errores HTTP de imágenes y cero errores JavaScript. Estas medidas corresponden a este PC y conexión local.

- Verificación final de esta actualización: 45 pruebas pasaron, incluida la comprobación de conservación de vértices, curvas a mano, precarga autenticada y navegación móvil.

- Compatibilidad durante la actualización: la plantilla acepta el servidor anterior y precarga sus vistas de zoom cuando el endpoint nuevo aún no existe; la página productiva se verificó con HTTP 200. Windows reportó cancelada la elevación para reiniciar los procesos actuales, por lo que el reinicio de los lectores persistentes queda pendiente.

- Aplicación activa: precarga de 198 teselas completada en la ventana actual, navegador listo en 1,69 s y zoom 20 sin fallos de imagen ni JavaScript. Se conservó el último punto distinto del trazo a mano en el cierre, comprobado en navegador. Respaldo completo antes-precarga-20261014-f030efc creado y verificado con SQLite íntegra.

- El usuario confirmó que no vio el aviso de elevación. Se agregó REINICIAR_APLICATIVO.cmd para solicitar el permiso desde su escritorio, con un modo SoloReiniciar que evita repetir el respaldo y la precarga. Ambos scripts PowerShell pasaron la validación sintáctica. El reinicio sigue pendiente de ejecución elevada por el usuario.
