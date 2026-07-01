# AgroCredito · Plataforma de Evaluación Agroclimática

Aplicación **Streamlit** para la evaluación de predios agrícolas en decisiones de
crédito en Colombia: validación pre-crédito (geometría, continuidad productiva,
infraestructura y riesgo agroclimático) y monitoreo de portafolio.

## Estructura

```
app.py                  # Aplicación principal (3 pestañas)
utils/                  # Módulos de cálculo (NDVI, terreno, clima, riesgo, PDF…)
datos/indicadores/      # Tablas de referencia (matriz de vulnerabilidad, altitudes)
requirements.txt        # Dependencias Python
.streamlit/
  secrets.toml.example  # Plantilla de credenciales (copiar a secrets.toml)
deploy_guide.md         # 👉 Guía paso a paso para desplegar la app
```

## Puesta en marcha

Sigue **`deploy_guide.md`**. En resumen: subir el código a GitHub, configurar
Google Earth Engine y Supabase, desplegar en Streamlit Community Cloud y añadir
los *Secrets*.

## Fuentes de datos

- **Sentinel-2** (NDVI) vía **Google Earth Engine**
- **DEM SRTM** vía **AWS Terrain Tiles** (Terrarium, sin API key)
- **Clima ERA5** vía **Open-Meteo** (sin API key)
- **Predios / construcciones** vía **PostGIS / Supabase**
- **OSM / OSRM** (infraestructura), **UPRA / datos.gov.co** (aptitud)

> El único proveedor con credenciales obligatorias es **Google Earth Engine**.
> Supabase es opcional (modo demo disponible con `USE_REAL_DB = False`).
