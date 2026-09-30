# Inventario de ortofotos

Inspección de solo lectura realizada con GDAL 3.12.0 incluido en QGIS 3.44.3. Se usó `gdalinfo -json`, que lee cabeceras y metadatos; no se cargaron rásteres completos en memoria ni se modificó ningún archivo.

Todas las fuentes declaran MAGNA-SIRGAS / Origen-Nacional (EPSG:9377).

| Municipio | Zona | Archivo | Formato | Tamaño (px) | Resolución | Cobertura EPSG:9377 (UL → LR) | Tamaño |
|---|---|---|---|---:|---:|---|---:|
| Cocorná | Rural | `Orto20_05197_20240717.ecw` | ECW, 5 bandas | 121773 × 149071 | 0.20 m | 4748539.680, 2235577.255 → 4772894.280, 2205763.055 | 9.37 GB |
| Cocorná | Urbano | `Orto10_05197000_20240315.ecw` | ECW, 5 bandas | 16236 × 19669 | 0.10 m | 4757478.512, 2228839.912 → 4759102.112, 2226873.012 | 251.81 MB |
| Guatapé | Rural | `orto20_05321_20230503.ecw` | ECW, 5 bandas | 44695 × 82955 | 0.20 m | 4757099.000, 2256925.000 → 4766038.000, 2240334.000 | 1.55 GB |
| Guatapé | Urbano | `orto10_05321000_20221117_U.ecw` | ECW, 5 bandas | 29702 × 13534 | 0.10 m | 4759773.637, 2248135.899 → 4762743.837, 2246782.499 | 104.90 MB |
| San Carlos | Rural | `Orto20_05649_20240526-007.ecw` | ECW, 5 bandas | 231310 × 168290 | 0.20 m | 4764770.536, 2259266.224 → 4811032.536, 2225608.224 | 24.58 GB |
| San Carlos | Urbano | `SanCarlos_Urbano_EPSG9377.ecw` | ECW, 5 bandas | 37497 × 17493 | 0.10 m | 4778232.871, 2242934.880 → 4781982.571, 2241185.580 | 601.23 MB |
| San Rafael | Rural | `orto50_05667_20230504.ecw` | ECW, 5 bandas | 70830 × 43491 | 0.50 m | 4762494.000, 2266327.500 → 4797909.000, 2244582.000 | 1.66 GB |
| San Rafael | Urbano | `Orto10_05667000_20221003.tif` | GeoTIFF, 4 bandas | 19563 × 14865 | 0.10 m | 4774729.200, 2254494.400 → 4776685.500, 2253007.900 | 1.57 GB |

## Hallazgos

- El complemento ECW sí existe en `C:\Program Files\QGIS 3.44.3\apps\gdal\lib\gdalplugins\gdal_ECW_JP2ECW.dll`; GDAL necesita `GDAL_DRIVER_PATH` apuntando a esa carpeta. La aplicación lo configura para cada proceso.
- Sin ese ajuste, GDAL informa exactamente: `plugin gdal_ECW_JP2ECW.dll is not available in your installation` y sugiere instalar `gdal-ecw`. El catálogo conserva el mensaje exacto y continúa con las demás fuentes.
- El GeoTIFF de San Rafael contiene claves que difieren de la definición EPSG vigente. GDAL advierte que debe elegirse `GTIFF_SRS_SOURCE=EPSG` o `GEOKEYS` antes de reproyectar. Para la aplicación se conserva el código declarado EPSG:9377; este archivo debe validarse visualmente contra un punto conocido antes de producción.
- Las carpetas permiten inferir municipio y zona, pero el panel permite corregir la asignación sin renombrar archivos.
- Los nombres `Cocorna` y `Guatape` no llevan tilde; el catálogo conserva la ruta original y la asignación administrativa puede usar `Cocorná` y `Guatapé`.

