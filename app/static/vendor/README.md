# Modo local de Leaflet y Geoman

El repositorio ya incluye las copias fijadas de Leaflet 1.9.4 y
Leaflet-Geoman 2.18.3. El instalador configura `PREDIOS_ASSET_MODE=local` para
servirlas desde esta carpeta, sin depender del CDN durante la operación.
`scripts/DESCARGAR_FRONTEND.ps1` solo se necesita al actualizar intencionalmente
esas dependencias y revisar el cambio en Git.
