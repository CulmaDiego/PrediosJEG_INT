# Estado del proyecto

Fecha de actualización: 2026-09-30.

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
- [x] Alejamiento hasta encuadrar completo el radio permitido y sobrezoom hasta nivel 24 reutilizando la última tesela nativa, con desplazamiento confinado al recorte de ortofoto.
- [x] Ortofoto sin capa translúcida y pantalla final de “Respuesta recibida” después de cada envío.
- [x] Ortofoto realzada sin velos de relleno; el dibujo a mano muestra el trazo en vivo, se cierra al regresar al inicio y simplifica automáticamente los vértices para facilitar la edición.
- [x] Identidad visual actualizada con la paleta institucional `#096e74`, `#44bec6`, `#bcd3d8`, `#7e9cac`, `#162f3e`, `#35596b`, `#949cac`, `#96a4ac`, `#61808e` y `#4c6674`.
- [x] Logo institucional de Jorge Eliecer Gaitán integrado en administración, acceso, mapa móvil y mensajes públicos, con navegación y componentes visuales refinados.
- [x] Borrador en `sessionStorage` y reintento tras error de red.
- [x] Validación Polygon WGS84, 3–100 vértices, cierre, autointersección, área y distancia máxima.
- [x] Versiones inmutables y una sola vigente.
- [ ] Revisión táctil manual en teléfono físico. No se afirma completada porque no hubo un dispositivo físico disponible en esta sesión.
- Verificación: Playwright + Chrome a 320, 375 y 430 px; carga de tesela privada, encuadre completo del radio permitido, sobrezoom hasta nivel 24 sin perder la imagen, desplazamiento limitado, dibujo a mano alzada, edición, confirmación, fallo de red y reintento. También se comprobó horizontal a 667 × 375 px.

## Etapa 4 — Ortofotos, trabajos y reportes

- [x] Inventario GDAL en lectura y errores por fuente sin detener el catálogo.
- [x] Instalación limpia capaz de descubrir municipios desde las carpetas, programar el catálogo inicial y reasignar nombres equivalentes con o sin tilde.
- [x] Cola SQLite reanudable, reclamo atómico, progreso y errores visibles.
- [x] Radio de ventana de teselas configurable desde el panel administrador, persistente y aplicado a personas nuevas y regeneraciones; las ventanas y dibujos existentes se conservan.
- [x] Generación y regeneración manual por persona, selección o municipio, con verificación física de la tesela central en cada nivel.
- [x] Descarga de enlaces limitada a personas con imágenes verificadas; los enlaces pendientes muestran una pantalla de preparación en lugar de un mapa vacío.
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
