# Estado del proyecto

Fecha de actualización: 2026-09-29.

## Etapa 1 — Base local y seguridad

- [x] FastAPI/Jinja2/SQLAlchemy/SQLite WAL con versiones fijadas.
- [x] Alembic y configuración separada fuera del checkout.
- [x] Administrador único, Argon2id, sesión, CSRF, CSP, cookies seguras y rate limiting.
- [x] Scripts sencillos para desarrollo, versión, instalación y actualización en Windows.
- Verificación: compilación de Python, migración y pruebas automatizadas.

## Etapa 2 — Municipios, personas e importación

- [x] Municipios activos/inactivos y listado paginado de personas.
- [x] Validación completa CSV/XLSX, errores por fila y confirmación atómica.
- [x] Celular normalizado único, enlaces preservados al actualizar y coordenadas protegidas si hay dibujo.
- [x] Tablero, filtros, estados, detalle e historial.
- Verificación: pruebas HTTP de autenticación, XSS, SQL injection y archivos inválidos.

## Etapa 3 — Flujo público móvil

- [x] Token aleatorio de 256 bits almacenado solo como hash, expiración y revocación.
- [x] Mapa móvil vertical/horizontal, dibujo por puntos y a mano alzada, edición, borrado, deshacer y confirmación.
- [x] Sobrezoom hasta nivel 24 reutilizando la última tesela nativa, con desplazamiento confinado al recorte de ortofoto para evitar áreas vacías.
- [x] Identidad visual actualizada con la paleta institucional `#096e74`, `#44bec6`, `#bcd3d8`, `#7e9cac`, `#162f3e`, `#35596b`, `#949cac`, `#96a4ac`, `#61808e` y `#4c6674`.
- [x] Borrador en `sessionStorage` y reintento tras error de red.
- [x] Validación Polygon WGS84, 3–100 vértices, cierre, autointersección, área y distancia máxima.
- [x] Versiones inmutables y una sola vigente.
- [ ] Revisión táctil manual en teléfono físico. No se afirma completada porque no hubo un dispositivo físico disponible en esta sesión.
- Verificación: Playwright + Chrome a 320, 375 y 430 px; carga de tesela privada, sobrezoom hasta nivel 24 sin perder la imagen, desplazamiento limitado, dibujo a mano alzada, edición, confirmación, fallo de red y reintento. También se comprobó horizontal a 667 × 375 px.

## Etapa 4 — Ortofotos, trabajos y reportes

- [x] Inventario GDAL en lectura y errores por fuente sin detener el catálogo.
- [x] Cola SQLite reanudable, reclamo atómico, progreso y errores visibles.
- [x] Ventana configurable de 350 m y teselas XYZ limitadas a esa ventana.
- [x] Autorización de cada tesela contra persona/enlace.
- [x] Exportaciones vigentes GeoJSON, GeoPackage, Shapefile y Excel.
- [x] Inventario de las ocho fuentes reales en `docs/ORTOFOTOS.md`.
- Verificación: el trabajador catalogó las 8 fuentes con GDAL; una ventana real del TIFF de San Rafael produjo 52 teselas (zoom 15–18) y quedó en estado `LISTO`.

## Etapa 5 — Producción

- [x] Instalación y tareas automáticas de Windows.
- [x] Actualización por etiqueta, respaldo verificado, comprobaciones y restauración completa ante fallo.
- [x] Guía de Cloudflare Tunnel sin secretos en Git.
- Verificación local: copia consistente, `PRAGMA integrity_check` y restauración conjunta de SQLite y teselas pasaron la prueba automatizada.
- [ ] Ensayo en el PC de empresa: instalación inicial, actualización desde una etiqueta real, restauración y flujo público completo.

Producción no se considera lista hasta completar los dos puntos manuales pendientes: teléfono físico y ensayo real en el PC de empresa.
