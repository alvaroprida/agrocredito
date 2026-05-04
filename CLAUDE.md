# AgroCredito · Contexto del Proyecto

Plataforma Streamlit de evaluación agroclimática y productiva para decisiones de crédito agrícola en Colombia.

---

## Estructura de archivos

```
app.py                      # Front-end principal Streamlit
utils/
  postgis_client.py         # Consulta PostGIS (modo simulado por defecto)
  eosda_terrain.py          # Análisis de terreno DEM via EOSDA API
  risk_scoring.py           # Scoring 15 indicadores de riesgo (MVP hardcoded)
  report_generator.py       # Generación PDF ejecutivo (reportlab)
requirements.txt
CLAUDE.md                   # Este archivo
```

---

## Tabs de la aplicación

| Tab | Nombre | Estado |
|-----|--------|--------|
| 0 | 🏠 Inicio · Ingreso del Predio | ✅ Funcional |
| 1 | ✅ Validación Pre-Crédito | 🔧 En construcción |
| 2 | 📡 Monitoreo & Forecast | ✅ Funcional (hardcoded) |

### Tab 0 · Inicio
- Input de coordenadas (lat/lon) + tipo de cultivo
- Consulta PostGIS via `get_predio_por_punto(lat, lon)` → devuelve polígono catastral
- Mapa Folium con polígono del predio
- Coordenadas default: lat=5.07013, lon=-73.55157

### Tab 1 · Validación Pre-Crédito (objetivo final)
Fusiona lo que antes eran dos tabs separadas (Eligibilidad + Riesgo Agroclimático).
Estructura objetivo:

```
A · Validación Geométrica y Legal
B · Validación de Continuidad Productiva (NDVI Sentinel-2)
C · Validación de Infraestructura Productiva (construcciones PostGIS)
D · Análisis de Terreno (eosda_terrain.py) ← YA EXISTÍA, NO ELIMINAR
E · Scoring de Riesgo Agroclimático (risk_scoring.py) ← NUEVO
────────────────────────────────────────────────────
Resumen de Validación + Botón descarga PDF ejecutivo
```

**IMPORTANTE:** El análisis de terreno (`eosda_terrain.py`) ya estaba integrado en una versión anterior del app.py y fue eliminado accidentalmente en un refactor. Debe recuperarse y mantenerse.

### Tab 2 · Monitoreo & Forecast
- NDVI actual + tendencia
- Forecast precipitación y temperatura 7 días
- Alertas activas
- Pendiente: reporte de monitoreo PDF

---

## Casos de estudio hardcoded (MVP)

```python
CASOS_ESTUDIO = {
    "Café · Eje Cafetero": {
        "lat": 4.8087, "lon": -75.6906, "cultivo": "café",
        "municipio": "Salento, Quindío",
        ...
    },
    "Plátano · Urabá": {
        "lat": 7.8833, "lon": -76.6500, "cultivo": "plátano",
        "municipio": "Turbo, Antioquia",
        ...
    },
}
```

---

## utils/risk_scoring.py — 15 indicadores de riesgo

### Grupos
1. Déficit Hídrico (indicadores 1–3)
2. Exceso Hídrico / Inundación (4–5)
3. Estrés Térmico (6–7)
4. Daño Mecánico / Viento (8)
5. Salud y Productividad del Predio (9–12)
6. Factores Estructurales del Territorio (13–15)

### Estado de integración por indicador

| ID | Nombre | Fuente | API | Estado MVP |
|----|--------|--------|-----|------------|
| 1 | SWI — Soil Water Index | ERA5-Land · Open-Meteo | ✅ | ⏳ Hardcoded |
| 2 | SPEI-3/6 | ERA5 · Open-Meteo | ✅ | ⏳ Hardcoded |
| 3 | WRSI | ERA5 + FAO-56 | ✅ | ⏳ Hardcoded |
| 4 | Precip. acumulada 7d | ERA5 · Open-Meteo | ✅ | ⏳ Hardcoded |
| 5 | Susceptibilidad deslizamientos | SGC / IRAKA | ❌ | ⏳ Hardcoded |
| 6 | T_máx media anual | ERA5 · Open-Meteo | ✅ | ✅ Calculado desde series |
| 7 | Días T_mín < umbral | ERA5 · Open-Meteo | ✅ | ✅ Calculado desde series |
| 8 | Días viento > umbral | ERA5 · Open-Meteo | ✅ | ⏳ Hardcoded |
| 9 | NDVI anomalía | Sentinel-2 · EOSDA/GEE | ✅ | ✅ Desde ndvi_promedio_3a |
| 10 | NDMI estrés hídrico | Sentinel-2 · EOSDA/GEE | ✅ | ⏳ Hardcoded |
| 11 | NDRE clorosis | Sentinel-2 · EOSDA/GEE | ✅ | ⏳ Hardcoded |
| 12 | VH backscatter SAR | Sentinel-1 · GEE | ✅ | ⏳ Hardcoded |
| 13 | Valor Potencial Suelo | UPRA / SIPRA | ❌ | ⏳ Hardcoded |
| 14 | Aptitud agroclimática | UPRA / SIPRA | ❌ | ⏳ Hardcoded |
| 15 | Distancia urbana | IGAC / OSM | ❌ | ✅ Desde distancia_urbana_km |

### Lógica de scoring
- Cada indicador devuelve 0 (Bajo), 1 (Medio), 2 (Alto)
- Score por grupo = máximo del grupo (criterio conservador)
- Score global = máximo entre grupos
- Umbrales predefinidos por cultivo (café / plátano) o editables por el analista

### Función principal
```python
from utils.risk_scoring import score_riesgo
resultado = score_riesgo(datos, umbrales_custom=None)
# resultado["resultados"]        → lista de 15 dicts con valor, score, decisión
# resultado["por_grupo"]         → {grupo: score_num}
# resultado["score_global"]      → 0/1/2
# resultado["score_global_label"]→ "🟢 Bajo" / "🟡 Medio" / "🔴 Alto"
```

---

## utils/report_generator.py — PDF ejecutivo

### Función principal
```python
from utils.report_generator import generate_exante_report
pdf_bytes = generate_exante_report(datos, predio, fmt="pdf", scoring=scoring)
```

### Secciones del PDF
1. Header/footer en cada página (barra oscura + numeración)
2. Ficha del predio (código catastral, cultivo, municipio, área, fecha)
3. Dictamen global (caja coloreada verde/naranja/rojo)
4. KPIs clave (5 métricas en fila)
5. Sección A — Validación geométrica
6. Sección B — Continuidad productiva
7. Sección C — Infraestructura
8. Sección D — Scoring 15 indicadores (si scoring != None)
9. Serie climática mensual (tabla 12 meses)
10. Sección de firmas (Analista / Riesgo / Gerente)
11. Nota legal

### Dependencia
```
reportlab>=4.0
```

---

## utils/eosda_terrain.py — Análisis de terreno

### Función principal
```python
from utils.eosda_terrain import get_terrain_analysis, build_terrain_maps
terrain = get_terrain_analysis(gdf_predio, slope_threshold=15.0)
```

### Output
```python
terrain["stats"]              # dict con elev_min/max/mean, slope_mean, pct_cultivable, etc.
terrain["maps"]["dem_map"]    # Folium map con overlay DEM
terrain["maps"]["slope_map"]  # Folium map con overlay pendiente
terrain["maps"]["aspect_map"] # Folium map con overlay aspecto
terrain["maps"]["cultiv_map"] # Folium map verde/rojo cultivable vs no cultivable
terrain["cultivable_mask"]    # np.ndarray bool
terrain["no_cultivable_mask"] # np.ndarray bool
```

### Requiere
- `EOSDA_API_KEY` en Streamlit secrets o variable de entorno
- `gdf_predio` en EPSG:4326 (viene de `postgis_client.get_predio_por_punto`)

---

## Paleta de colores y semáforos

```python
# Semáforos CSS (clases ya definidas en app.py)
"semaforo-verde"   → background #d1fae5, border #059669
"semaforo-naranja" → background #fef3c7, border #d97706
"semaforo-rojo"    → background #fee2e2, border #dc2626

# Scores de riesgo
SCORE_COLOR = {0: "#d1fae5", 1: "#fef3c7", 2: "#fee2e2"}
SCORE_TEXT  = {0: "#065f46", 1: "#78350f", 2: "#7f1d1d"}
SCORE_LABEL = {0: "🟢 Bajo", 1: "🟡 Medio", 2: "🔴 Alto"}
```

---

## Secrets necesarios (Streamlit Cloud)

```toml
DATABASE_URL   = "postgresql://..."   # PostGIS (opcional, USE_REAL_DB=False por defecto)
EOSDA_API_KEY  = "..."                # Para análisis de terreno real
```

---

## Pendientes próximas iteraciones

### Tab 1 · Validación Pre-Crédito
- [ ] Reintegrar análisis de terreno (`eosda_terrain.py`) como marco D
- [ ] Mover scoring de riesgo a marco E
- [ ] Conectar Open-Meteo para indicadores 1–4, 8 (reemplazar hardcoded)
- [ ] Añadir Word y Excel al generador de reportes

### Tab 2 · Monitoreo & Forecast
- [ ] Reporte de monitoreo PDF con forecast vs umbrales de alerta por indicador
- [ ] Alertas automáticas email/SMS
- [ ] Comparación NDVI con umbrales por fase fenológica

### Infraestructura
- [ ] Conectar PostGIS real (USE_REAL_DB = True)
- [ ] GEE para NDVI/NDMI/NDRE/SAR reales (indicadores 9–12)
- [ ] UPRA/SIPRA para VPS y aptitud (indicadores 13–14)
