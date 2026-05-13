"""
app.py  ·  AgroCredito · MVP de Evaluación Agroclimática
Streamlit front-end — desplegable en Streamlit Cloud sin instalación local.

Tab 0 · Inicio                → inputs + polígono predio + métricas
Tab 1 · Validación Pre-Crédito → A geométrica · B productiva · C infraestructura
                                  D terreno · E scoring riesgo · PDF
Tab 2 · Monitoreo             → NDVI actual + forecast
"""

import streamlit as st
import pandas as pd

CULTIVOS_DISPONIBLES = [
    "Aguacate (Hass)",
    "Cacao",
    "Café",
    "Cebolla",
    "Durazno",
    "Fresa",
    "Granadilla",
    "Guayaba",
    "Gulupa",
    "Limón",
    "Lulo",
    "Maíz",
    "Mango",
    "Maracuyá",
    "Mora",
    "Naranja",
    "Papa",
    "Piña",
    "Plátano",
    "Uchuva",
]
import numpy as np
import folium
from folium.plugins import Fullscreen
from streamlit_folium import st_folium
from datetime import date, timedelta
import plotly.graph_objects as go
import plotly.express as px
import geopandas as gpd

from utils.postgis_client import (
    get_predio_por_punto,
    get_frontera,
    get_valor_potencial,
    get_construcciones,
)
from utils.aptitud_api import get_aptitud_api, CULTIVO_API_MAP, score_to_category
from utils.infraestructura  import get_distancia_centro_urbano, get_distancia_via
from utils.climate_data     import get_historical_climate, monthly_climatology
from utils.risk_indicators  import (
    compute_risk_for_crop, crops_with_matrix,
    needed_extra_hourly,
    score_to_label, score_to_color, aggregate_risk_score,
)
from utils.eosda_terrain  import get_terrain_analysis
from utils.gee_ndvi       import get_ndvi_gee
from utils.eosda_ndvi     import get_productivity_analysis
from utils.risk_scoring   import (
    score_riesgo, INDICADORES, GRUPOS,
    SCORE_LABEL, SCORE_COLOR, SCORE_TEXT,
)
from utils.report_generator import generate_exante_report

@st.cache_data(ttl=3600, show_spinner=False)
def _get_aptitud_cached(_gdf_predio, cultivo: str):
    return get_aptitud_api(_gdf_predio, cultivo)

@st.cache_data(ttl=86400, show_spinner=False)
def _get_climate_cached(lat: float, lon: float, cultivo: str):
    extra = needed_extra_hourly(cultivo)
    return get_historical_climate(lat, lon, n_years=10, extra_hourly=extra)

def _matrix_mtime() -> float:
    """Tiempo de modificación del Excel de vulnerabilidad (cache key dinámico)."""
    import os
    from pathlib import Path
    p = Path(__file__).parent / "datos" / "indicadores" / "matriz_vulnerabilidad_consolidada.xlsx"
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0

@st.cache_data(ttl=86400, show_spinner=False)
def _get_risk_cached(lat: float, lon: float, cultivo: str, mtime: float = 0.0):
    extra = needed_extra_hourly(cultivo)
    df = get_historical_climate(lat, lon, n_years=10, extra_hourly=extra)
    return compute_risk_for_crop(df, cultivo)

@st.cache_data(ttl=86400, show_spinner=False)
def _get_b2_cached(geojson_str: str, cultivo: str):
    import json
    from shapely.geometry import shape
    gdf = gpd.GeoDataFrame(geometry=[shape(json.loads(geojson_str))], crs="EPSG:4326")
    return get_productivity_analysis(gdf, cultivo)

@st.cache_data(ttl=3600, show_spinner=False)
def _get_distancia_centro_cached(lat: float, lon: float, v: int = 2):
    return get_distancia_centro_urbano(lat, lon)

@st.cache_data(ttl=3600, show_spinner=False)
def _get_distancia_via_cached(lat: float, lon: float, v: int = 2):
    return get_distancia_via(lat, lon)

# ── Configuración de página ───────────────────────────────────────────────────
st.set_page_config(
    page_title="AgroCredito · Evaluación de Predios",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; }
  .semaforo-verde  { background:#d1fae5; border-left:5px solid #059669;
                     padding:0.6rem 1rem; border-radius:6px; }
  .semaforo-naranja{ background:#fef3c7; border-left:5px solid #d97706;
                     padding:0.6rem 1rem; border-radius:6px; }
  .semaforo-rojo   { background:#fee2e2; border-left:5px solid #dc2626;
                     padding:0.6rem 1rem; border-radius:6px; }
  .kpi-box { background:#f8fafc; border:1px solid #e2e8f0;
             border-radius:8px; padding:0.8rem; text-align:center; }
  .tag-pendiente { background:#f1f5f9; color:#64748b; font-size:0.72rem;
                   border-radius:4px; padding:1px 6px; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
#  DATOS HARDCODEADOS (MVP)
# ══════════════════════════════════════════════════════════════════════════════

CASOS_ESTUDIO = {
    "Café · Eje Cafetero": {
        "lat": 4.8087, "lon": -75.6906, "cultivo": "café",
        "municipio": "Salento, Quindío",
        "area_total_ha": 12.4,
        "area_pendiente_excluida_ha": 1.8,
        "area_ndvi_bajo_ha": 0.6,
        "area_construcciones_ha": 0.3,
        "area_efectiva_ha": 9.7,
        "frontera_agricola": "Frontera agrícola", "frontera_estado": "verde",
        "aptitud_cultivo": "Alta", "valor_potencial": "Alto",
        "ndvi_promedio_3a": 0.71, "ndvi_umbral": 0.40,
        "construcciones_n": 3, "construcciones_desc": "Casa, bodega, beneficiadero",
        "distancia_urbana_km": 8.2,
        "precip_mensual":    [180,160,210,230,195,140,130,145,220,240,200,175],
        "temp_max_mensual":  [24,25,25,24,23,22,22,23,24,25,24,24],
        "temp_min_mensual":  [14,14,15,15,14,13,13,13,14,15,15,14],
        "ndvi_mensual_hist": [.65,.67,.70,.72,.71,.68,.66,.67,.70,.73,.72,.69],
        "riesgo_sequia": "Bajo", "riesgo_exceso_lluvia": "Medio",
        "riesgo_helada": "Bajo", "riesgo_temp_alta": "Bajo", "riesgo_global": "Bajo",
        "ndvi_actual": 0.69, "ndvi_tendencia": "estable", "alerta_activa": False,
        "forecast_precip_7d": [12,8,15,20,18,10,6],
        "forecast_temp_7d":   [22,22,23,23,22,21,21],
    },
    "Plátano · Urabá": {
        "lat": 7.8833, "lon": -76.6500, "cultivo": "plátano",
        "municipio": "Turbo, Antioquia",
        "area_total_ha": 28.0,
        "area_pendiente_excluida_ha": 0.5,
        "area_ndvi_bajo_ha": 2.1,
        "area_construcciones_ha": 0.8,
        "area_efectiva_ha": 24.6,
        "frontera_agricola": "Frontera agrícola condicionada",
        "frontera_estado": "naranja",
        "condicion_frontera": "Zona de manejo especial · cuenca hídrica",
        "aptitud_cultivo": "Alta", "valor_potencial": "Muy Alto",
        "ndvi_promedio_3a": 0.78, "ndvi_umbral": 0.40,
        "construcciones_n": 5,
        "construcciones_desc": "Casa, dos bodegas, empacadora, generador",
        "distancia_urbana_km": 14.5,
        "precip_mensual":    [280,240,310,350,320,260,220,230,310,360,330,290],
        "temp_max_mensual":  [32,33,33,32,31,31,31,32,32,33,32,32],
        "temp_min_mensual":  [22,22,23,23,22,21,21,21,22,23,23,22],
        "ndvi_mensual_hist": [.74,.76,.79,.81,.80,.77,.75,.76,.79,.82,.81,.78],
        "riesgo_sequia": "Bajo", "riesgo_exceso_lluvia": "Alto",
        "riesgo_helada": "Nulo", "riesgo_temp_alta": "Medio", "riesgo_global": "Medio",
        "ndvi_actual": 0.77, "ndvi_tendencia": "ligero descenso", "alerta_activa": True,
        "alerta_msg": "⚠️ Exceso de precipitación proyectado próximos 5 días",
        "forecast_precip_7d": [35,42,50,48,38,22,15],
        "forecast_temp_7d":   [30,29,29,30,31,32,32],
    },
}

MOCK_NDVI = {"ndvi_promedio": 0.71, "area_ndvi_bajo_ha": 0.6, "umbral_ndvi": 0.40}

# ══════════════════════════════════════════════════════════════════════════════
#  PALETAS Y HELPERS UI
# ══════════════════════════════════════════════════════════════════════════════

COLOR_SEMAFORO   = {"verde":"semaforo-verde","naranja":"semaforo-naranja","rojo":"semaforo-rojo"}
COLORES_FRONTERA = {
    "Frontera Agrícola no condicionada":              "#16a34a",
    "No condicionada":                                "#16a34a",
    "Condicionada":                                   "#d97706",
    "Ambiental":                                      "#d97706",
    "Ambiental/Étnico-Cultural":                      "#d97706",
    "Ambiental/Riesgo de desastres":                  "#d97706",
    "Ambiental/Riesgo de desastres/Étnico-Cultural":  "#d97706",
    "Étnico-Cultural":                                "#d97706",
    "Gestión riesgo de desastres":                    "#d97706",
    "Riesgo de desastres/Étnico-Cultural":            "#d97706",
}
COLORES_APTITUD = {
    "Alta":"#15803d","Media":"#ca8a04","Baja":"#b45309","No apta":"#dc2626",
}

# Niveles de score: 0=Sin riesgo, 1=Bajo, 2=Medio, 3=Alto, 4=Extremo
SCORE_5_LABEL = {0:"🟢 Sin riesgo", 1:"🟢 Bajo", 2:"🟡 Medio", 3:"🔴 Alto", 4:"🚨 Extremo"}
SCORE_5_COLOR = {0:"#d1fae5", 1:"#dcfce7", 2:"#fef9c3", 3:"#fee2e2", 4:"#fce7f3"}
SCORE_5_TEXT  = {0:"#065f46", 1:"#14532d", 2:"#713f12", 3:"#7f1d1d", 4:"#500724"}

# Umbrales estándar 5 niveles por cultivo
# Formato: {cultivo: {id: [sin_riesgo, bajo, medio, alto, extremo]}}
# Para direccion='mayor': valor < sin_riesgo → sin riesgo, valor < bajo → bajo, etc.
# Para direccion='menor': inverso
UMBRALES_5: dict[str, dict[int, list]] = {
    "café": {
        1:  [0,   1,   3,   5,   8  ],   # SWI meses secos
        2:  [0,   1,   3,   5,   8  ],   # SPEI meses déficit
        3:  [90,  80,  60,  50,  35 ],   # WRSI % (menor=peor)
        4:  [0,   1,   3,   5,   8  ],   # Episodios lluvia extrema
        5:  [1,   2,   3,   4,   5  ],   # Susceptibilidad desliz
        6:  [24,  26,  28,  30,  32 ],   # T_máx °C
        7:  [0,   0,   2,   5,   10 ],   # Días helada
        8:  [0,   2,   5,   10,  20 ],   # Días viento fuerte
        9:  [0.65,0.60,0.50,0.40,0.30],  # NDVI (menor=peor)
        10: [0.25,0.20,0.10,0.00,-0.1],  # NDMI (menor=peor)
        11: [0.45,0.40,0.30,0.20,0.10],  # NDRE (menor=peor)
        12: [0,  -1.0,-2.0,-3.0,-4.5],   # VH backscatter (menor=peor)
        13: [1,   2,   3,   4,   5  ],   # VPS clase suelo
        14: [1,   1,   2,   3,   4  ],   # Aptitud clase
        15: [5,   10,  25,  50,  100],   # Distancia urbana km
    },
    "plátano": {
        1:  [0,   0,   2,   4,   6  ],
        2:  [0,   0,   2,   4,   6  ],
        3:  [92,  85,  70,  55,  40 ],
        4:  [1,   2,   4,   7,   10 ],
        5:  [1,   2,   3,   4,   5  ],
        6:  [30,  32,  34,  35,  37 ],
        7:  [0,   0,   1,   3,   5  ],
        8:  [0,   1,   3,   7,   12 ],
        9:  [0.70,0.65,0.55,0.45,0.35],
        10: [0.30,0.25,0.15,0.05,-0.1],
        11: [0.50,0.45,0.35,0.25,0.15],
        12: [0,  -1.0,-2.0,-3.5,-5.0],
        13: [1,   2,   3,   4,   5  ],
        14: [1,   1,   2,   3,   4  ],
        15: [5,   15,  30,  60,  120],
    },
}

def color_ufh(clase):
    try:
        n = int(clase)
        if n <= 4:  return "#15803d"
        if n <= 8:  return "#ca8a04"
        return "#dc2626"
    except Exception:
        return "#94a3b8"

def semaforo(texto, nivel):
    st.markdown(f'<div class="{COLOR_SEMAFORO[nivel]}">{texto}</div>',
                unsafe_allow_html=True)

def kpi(label, valor, unidad=""):
    st.markdown(
        f'<div class="kpi-box">'
        f'<div style="font-size:0.78rem;color:#64748b">{label}</div>'
        f'<div style="font-size:1.4rem;font-weight:700">{valor}'
        f'<span style="font-size:0.85rem;color:#64748b"> {unidad}</span></div></div>',
        unsafe_allow_html=True,
    )

def gauge_riesgo(valor_pct, titulo):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=valor_pct,
        title={"text": titulo, "font": {"size": 13}},
        gauge={"axis":{"range":[0,100]},"bar":{"color":"#3b82f6"},
               "steps":[{"range":[0,33],"color":"#d1fae5"},
                        {"range":[33,66],"color":"#fef3c7"},
                        {"range":[66,100],"color":"#fee2e2"}]},
        number={"suffix":"%"},
    ))
    fig.update_layout(height=200, margin=dict(t=40,b=10,l=10,r=10))
    return fig

def _colorscale_bar(label, colors, ticks, units=""):
    gradient = ", ".join(colors)
    n = len(ticks)
    tick_html = "".join(
        f'<span style="flex:1;text-align:{"left" if i==0 else "right" if i==n-1 else "center"};'
        f'font-size:0.75rem;color:#475569">{t}</span>'
        for i,t in enumerate(ticks)
    )
    return (
        f'<div style="margin:6px 0 14px 0">'
        f'<div style="font-size:0.78rem;color:#64748b;margin-bottom:3px"><b>{label}</b> {units}</div>'
        f'<div style="height:16px;border-radius:4px;border:1px solid #e2e8f0;'
        f'background:linear-gradient(to right,{gradient})"></div>'
        f'<div style="display:flex;margin-top:2px">{tick_html}</div>'
        f'</div>'
    )

# ── Scoring 5 niveles ─────────────────────────────────────────────────────────

def _score5(valor, umbrales, direccion):
    """Devuelve 0–4 (sin riesgo→extremo)."""
    u = umbrales  # [sin_riesgo, bajo, medio, alto, extremo]
    if direccion == "mayor":
        if valor < u[0]: return 0
        if valor < u[1]: return 1
        if valor < u[2]: return 2
        if valor < u[3]: return 3
        return 4
    else:  # menor = peor
        if valor > u[0]: return 0
        if valor > u[1]: return 1
        if valor > u[2]: return 2
        if valor > u[3]: return 3
        return 4

DECISION_5 = {
    0: "Sin restricción.",
    1: "Seguimiento anual estándar.",
    2: "Cláusula de seguimiento trimestral.",
    3: "Seguro agrícola obligatorio.",
    4: "Evaluar viabilidad del proyecto.",
}

def calcular_scoring_5(datos, umbrales_custom=None):
    """Calcula scoring con 5 niveles de riesgo sobre los 15 indicadores."""
    from utils.risk_scoring import VALORES_HARDCODED
    cultivo = datos.get("cultivo","café")
    valores = VALORES_HARDCODED.get(cultivo, VALORES_HARDCODED["café"]).copy()

    # Sobreescribir con valores calculables desde series
    tmax = datos.get("temp_max_mensual",[])
    tmin = datos.get("temp_min_mensual",[])
    dist = datos.get("distancia_urbana_km")
    ndvi = datos.get("ndvi_promedio_3a")
    if tmax:   valores[6]  = round(sum(tmax)/len(tmax),1)
    if tmin:
        umb_helada = 10 if cultivo=="café" else 15
        valores[7] = sum(1 for t in tmin if t < umb_helada)
    if dist is not None: valores[15] = dist
    if ndvi is not None: valores[9]  = ndvi

    umb_base = umbrales_custom or UMBRALES_5.get(cultivo, UMBRALES_5["café"])
    resultados = []
    scores_por_grupo = {g: [] for g in GRUPOS}

    for ind in INDICADORES:
        iid   = ind["id"]
        valor = valores.get(iid, 0.0)
        umb   = umb_base.get(iid, UMBRALES_5["café"][iid])
        sc    = _score5(valor, umb, ind["direccion"])

        resultados.append({
            "id":        iid,
            "grupo":     ind["grupo"],
            "nombre":    ind["nombre"],
            "metrica":   ind["metrica"],
            "fuente":    ind["fuente"],
            "pendiente": ind["pendiente"],
            "unidad":    ind["unidad"],
            "valor":     valor,
            "umbrales":  umb,
            "score":     sc,
            "label":     SCORE_5_LABEL[sc],
            "color":     SCORE_5_COLOR[sc],
            "text":      SCORE_5_TEXT[sc],
            "decision":  DECISION_5[sc],
        })
        scores_por_grupo[ind["grupo"]].append(sc)

    por_grupo = {g: max(vs) if vs else 0 for g, vs in scores_por_grupo.items()}
    score_global = max(por_grupo.values()) if por_grupo else 0

    return {
        "resultados":    resultados,
        "por_grupo":     por_grupo,
        "score_global":  score_global,
        "label_global":  SCORE_5_LABEL[score_global],
        "color_global":  SCORE_5_COLOR[score_global],
        "text_global":   SCORE_5_TEXT[score_global],
        "n_extremo": sum(1 for r in resultados if r["score"]==4),
        "n_alto":    sum(1 for r in resultados if r["score"]==3),
        "n_medio":   sum(1 for r in resultados if r["score"]==2),
        "n_bajo":    sum(1 for r in resultados if r["score"]==1),
        "n_sin":     sum(1 for r in resultados if r["score"]==0),
    }

# ── Mapas ─────────────────────────────────────────────────────────────────────

def _calc_zoom(gdf):
    b = gdf.geometry.iloc[0].bounds
    span = max(b[2]-b[0], b[3]-b[1])
    if span < 0.001: return 18
    if span < 0.003: return 17
    if span < 0.007: return 16
    if span < 0.015: return 15
    if span < 0.03:  return 14
    if span < 0.07:  return 13
    return 12

def _base_map(gdf):
    g = gdf.geometry.iloc[0]
    m = folium.Map(location=[g.centroid.y, g.centroid.x],
                   zoom_start=_calc_zoom(gdf), tiles="Esri.WorldImagery")
    Fullscreen().add_to(m)
    return m

def _add_predio(m, gdf):
    folium.GeoJson(
        data=gdf.to_json(), name="Predio",
        style_function=lambda _: {"fillColor":"#22c55e","color":"#16a34a",
                                   "weight":2.5,"fillOpacity":0.15},
        tooltip=folium.GeoJsonTooltip(
            fields=["codigo","departamento","area_ha"],
            aliases=["Código","Departamento","Área (ha)"],
        ),
    ).add_to(m)

def _fit(m, gdf):
    b = gdf.geometry.iloc[0].bounds
    m.fit_bounds([[b[1],b[0]],[b[3],b[2]]])

def _colored_mask_png(mask_arr, r, g, b, alpha=0.55):
    """Converts a boolean mask to a colored PNG base64 string for ImageOverlay."""
    from io import BytesIO
    import base64
    from PIL import Image as _PILImg
    h, w = mask_arr.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[mask_arr] = [r, g, b, int(alpha * 255)]
    img = _PILImg.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def _rasterize_gdf_to_mask(gdf, bounds_wgs84, shape):
    """Rasterizes a GeoDataFrame to a boolean mask grid aligned to bounds_wgs84."""
    try:
        import rasterio.features
        from rasterio.transform import from_bounds
        minx, miny, maxx, maxy = bounds_wgs84
        h, w = shape
        transform = from_bounds(minx, miny, maxx, maxy, w, h)
        gdf_4326 = gdf.to_crs("EPSG:4326")
        shapes_iter = [
            (geom.__geo_interface__, 1)
            for geom in gdf_4326.geometry
            if geom is not None and not geom.is_empty
        ]
        if not shapes_iter:
            return np.zeros(shape, dtype=bool)
        burned = rasterio.features.rasterize(
            shapes_iter, out_shape=shape, transform=transform, dtype=np.uint8
        )
        return burned > 0
    except Exception:
        return np.zeros(shape, dtype=bool)

def mapa_predio_simple(lat, lon, predio):
    m = _base_map(predio["gdf"])
    _add_predio(m, predio["gdf"])
    folium.Marker([lat,lon], tooltip="Punto ingresado",
                  icon=folium.Icon(color="red",icon="map-marker",prefix="fa")).add_to(m)
    _fit(m, predio["gdf"])
    return m

def mapa_capa(gdf_predio, gdf_capa=None, mostrar_predio=True, mostrar_capa=True,
              estilo_capa_fn=None, campos_tooltip=None, aliases_tooltip=None,
              nombre_capa="Capa"):
    m = _base_map(gdf_predio)
    if mostrar_predio: _add_predio(m, gdf_predio)
    if mostrar_capa and gdf_capa is not None and len(gdf_capa) > 0:
        folium.GeoJson(
            data=gdf_capa.to_json(), name=nombre_capa,
            style_function=estilo_capa_fn or (
                lambda _: {"fillColor":"#3b82f6","color":"#2563eb",
                           "weight":1.5,"fillOpacity":0.45}),
            tooltip=folium.GeoJsonTooltip(
                fields=campos_tooltip or [], aliases=aliases_tooltip or [],
            ) if campos_tooltip else folium.GeoJsonTooltip(fields=[]),
        ).add_to(m)
    _fit(m, gdf_predio)
    return m

# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════
c1, c2 = st.columns([1,8])
with c1: st.markdown("## 🌿")
with c2:
    st.markdown("## AgroCredito · Plataforma de Evaluación de Predios")
    st.caption("Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia")
st.divider()

# ══════════════════════════════════════════════════════════════════════════════
#  TABS  (3 tabs — Riesgo fusionado en Validación)
# ══════════════════════════════════════════════════════════════════════════════
tab_inicio, tab_validacion, tab_monitoreo, tab_metodologia = st.tabs([
    "🏠 Inicio · Ingreso del Predio",
    "✅ Validación Pre-Crédito",
    "📡 Monitoreo & Forecast",
    "📖 Metodología",
])

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 0 · INICIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_inicio:
    st.subheader("Datos del predio a evaluar")

    c1, c2, c3 = st.columns(3)
    with c1: lat_input  = st.number_input("Latitud",  value=5.07013,  format="%.6f")
    with c2: lon_input  = st.number_input("Longitud", value=-73.55157, format="%.6f")
    with c3: cultivo_in = st.selectbox("Tipo de cultivo", CULTIVOS_DISPONIBLES,
                                         index=CULTIVOS_DISPONIBLES.index("Café"))

    if st.button("🔍 Analizar predio", type="primary", use_container_width=True):
        _cultivo_lower = cultivo_in.lower()
        caso_m = ("Plátano · Urabá" if _cultivo_lower == "plátano"
                  else "Café · Eje Cafetero")
        st.session_state.update({
            "lat": lat_input, "lon": lon_input, "cultivo": cultivo_in,
            "analizado": True,
            "datos": {**CASOS_ESTUDIO[caso_m], "lat": lat_input, "lon": lon_input},
        })

    st.markdown("---")
    if not st.session_state.get("analizado"):
        st.info("Introduce las coordenadas del predio y pulsa **Analizar predio**.")
        st.stop()

    lat     = st.session_state["lat"]
    lon     = st.session_state["lon"]
    cultivo = st.session_state.get("cultivo","café")

    with st.spinner("Consultando base catastral..."):
        predio = get_predio_por_punto(lat, lon)

    if predio is None:
        st.warning("No se encontró ningún predio en las coordenadas indicadas.")
        st.stop()

    st.session_state["predio"] = predio

    st.markdown("#### 🗺️ Identificación del predio catastral")
    c1,c2,c3,c4 = st.columns(4)
    with c1: st.metric("Código catastral", predio["codigo"])
    with c2: st.metric("Departamento",     predio.get("departamento","—"))
    with c3: st.metric("Área catastral",   f"{predio.get('area_ha','—')} ha")
    with c4: st.metric("Cultivo",          cultivo.capitalize())

    st_folium(mapa_predio_simple(lat, lon, predio),
              width=750, height=450, returned_objects=[])
    st.caption("🟢 Polígono del predio catastral  ·  🔴 Punto ingresado")

    import json as _json
    st.download_button(
        label="⬇️ Descargar GeoJSON del predio",
        data=_json.dumps(predio["geojson"], ensure_ascii=False, indent=2),
        file_name=f"predio_{predio['codigo']}.geojson",
        mime="application/geo+json",
    )

    st.markdown("---")
    st.markdown("👉 Navega a **Validación Pre-Crédito** para el análisis detallado.")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 1 · VALIDACIÓN PRE-CRÉDITO
# ══════════════════════════════════════════════════════════════════════════════
with tab_validacion:
    predio  = st.session_state.get("predio")
    d       = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    cultivo = st.session_state.get("cultivo", d.get("cultivo","café"))

    if predio is None:
        st.info("Primero analiza un predio en el tab **Inicio**.")
        st.stop()

    municipio_real    = predio.get("municipio","")
    departamento_real = predio.get("departamento","")
    ubicacion_label   = (
        f"{municipio_real}, {departamento_real}"
        if municipio_real and departamento_real
        else municipio_real or departamento_real or d.get("municipio","")
    )
    st.subheader(f"Validación Pre-Crédito · {cultivo.capitalize()} · {ubicacion_label}")

    MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    # ════════════════════════════════════════════════════════════════════
    #  A · VALIDACIÓN GEOMÉTRICA Y LEGAL
    # ════════════════════════════════════════════════════════════════════
    st.markdown("### 📐 A · Validación Geométrica y Legal")

    with st.expander("🌿 A1 · Zona Agrícola (Frontera)", expanded=True):
        with st.spinner("Cargando frontera agrícola..."):
            gdf_frontera = get_frontera(predio["gdf"])
        st.session_state["gdf_frontera"] = gdf_frontera

        c1,c2 = st.columns(2)
        with c1: ver_predio_a2   = st.checkbox("🟢 Predio",            value=True, key="a2_predio")
        with c2: ver_frontera_a2 = st.checkbox("🟩 Frontera agrícola", value=True, key="a2_front")

        def estilo_frontera(feature):
            color = COLORES_FRONTERA.get(
                feature["properties"].get("tipo_condi",""), "#d97706")
            return {"fillColor":color,"color":color,"weight":2,"fillOpacity":0.40}

        st_folium(mapa_capa(
            predio["gdf"], gdf_frontera,
            mostrar_predio=ver_predio_a2, mostrar_capa=ver_frontera_a2,
            estilo_capa_fn=estilo_frontera,
            campos_tooltip=["tipo_condi","area_ha","pct_predio"],
            aliases_tooltip=["Tipo","Área (ha)","% predio"],
            nombre_capa="Frontera agrícola",
        ), width=700, height=380, returned_objects=[], key="map_a2")

        if gdf_frontera is not None and len(gdf_frontera) > 0:
            df_front = gdf_frontera.groupby("tipo_condi").agg(
                area_ha=("area_ha","sum"), pct_predio=("pct_predio","sum")
            ).reset_index().rename(columns={"tipo_condi":"Tipo de zona",
                                            "area_ha":"Área (ha)","pct_predio":"% del predio"})

            # Área fuera de frontera agrícola (no intersecta ninguna capa)
            _pct_total   = float(gdf_frontera["pct_predio"].sum())
            _pct_outside = round(max(0.0, 100.0 - _pct_total), 1)
            _area_outside = round(_pct_outside / 100 * float(predio.get("area_ha", 0)), 4)
            if _pct_outside > 2:
                _row_out = pd.DataFrame([{
                    "Tipo de zona": "⛔ Fuera de Frontera Agrícola",
                    "Área (ha)":    _area_outside,
                    "% del predio": _pct_outside,
                }])
                df_front = pd.concat([df_front, _row_out], ignore_index=True)

            st.dataframe(df_front, use_container_width=True, hide_index=True)

            tipos = gdf_frontera["tipo_condi"].unique().tolist()
            _tipos_cond = [t for t in tipos if t != "Frontera Agrícola no condicionada"]

            if _pct_outside > 2:
                nivel = "rojo"
                _msg  = (f"⛔ **{_pct_outside:.1f}% del predio ({_area_outside} ha) cae fuera de "
                         f"la Frontera Agrícola** — zona de exclusión legal. "
                         f"Áreas dentro: {', '.join(tipos)}.")
            elif _tipos_cond:
                nivel = "naranja"
                _msg  = (f"⚠️ Parte del predio en **Frontera Agrícola condicionada**: "
                         f"{', '.join(_tipos_cond)}. "
                         f"Verificar restricción específica antes de aprobación.")
            else:
                nivel = "verde"
                _msg  = "✅ Todo el predio dentro de **Frontera Agrícola no condicionada**."

            semaforo(_msg, nivel)
            st.session_state["a1_nivel"] = nivel
        else:
            st.warning("No se encontró información de frontera agrícola para este predio.")
            st.session_state["a1_nivel"] = "gris"


    # ── A2 · Área Efectiva Cultivable ─────────────────────────────────────────
    # Fragments defined at tab level — not nested inside any expander

    @st.fragment
    def _terrain_fragment():
        _pr = st.session_state.get("predio")
        if _pr is None:
            return
        st.markdown("#### 🏔️ A2-A · Análisis del Terreno (EOSDA API)")
        st.caption("Datos de pendiente utilizados en el cálculo del Área Efectiva.")

        _slope_thr = st.slider(
            "Umbral de pendiente no cultivable (%)",
            min_value=3, max_value=75, value=25, step=1, key="slope_threshold",
        )

        if st.button("🔄 Calcular terreno", type="primary", key="btn_terrain"):
            _ok = False
            with st.spinner("Descargando DEM y calculando terreno..."):
                try:
                    _t = get_terrain_analysis(_pr["gdf"], float(_slope_thr))
                    st.session_state["terrain"] = _t
                    st.session_state["area_pendiente_excluida_ha"] = _t["stats"]["area_no_cultivable_ha"]
                    _ok = True
                except Exception as e:
                    st.error(f"❌ Error al obtener datos de terreno: {e}")
            if _ok:
                st.rerun(scope="app")

        _t = st.session_state.get("terrain")
        if _t is None:
            st.info("Pulsa **Calcular terreno** para descargar el DEM desde EOSDA API.")
        else:
            _s    = _t["stats"]
            _maps = _t["maps"]
            st.markdown("**Estadísticas del predio**")
            c1,c2,c3,c4,c5 = st.columns(5)
            with c1: kpi("Elevación mínima",  f"{_s['elev_min']:.0f}",   "m")
            with c2: kpi("Elevación media",   f"{_s['elev_mean']:.0f}",  "m")
            with c3: kpi("Elevación máxima",  f"{_s['elev_max']:.0f}",   "m")
            with c4: kpi("Pendiente media",   f"{_s['slope_mean']:.1f}", "%")
            with c5: kpi("Aspecto dominante", _s["aspect_dominant"])
            st.markdown("---")

            c1,c2 = st.columns(2)
            with c1:
                st.markdown("**🏔️ Elevación (DEM)**")
                st_folium(_maps["dem_map"], width=420, height=340,
                          returned_objects=[], key="map_dem")
                st.markdown(_colorscale_bar(
                    "Elevación", units="m s.n.m.",
                    colors=["#006837","#1a9850","#66bd63","#d9ef8b",
                            "#fee08b","#fdae61","#f46d43","#a50026"],
                    ticks=[f"{_s['elev_min']:.0f}m",
                           f"{_s['elev_min']+(_s['elev_max']-_s['elev_min'])*0.33:.0f}m",
                           f"{_s['elev_min']+(_s['elev_max']-_s['elev_min'])*0.66:.0f}m",
                           f"{_s['elev_max']:.0f}m"],
                ), unsafe_allow_html=True)
            with c2:
                st.markdown("**📐 Pendiente (Slope)**")
                st_folium(_maps["slope_map"], width=420, height=340,
                          returned_objects=[], key="map_slope")
                st.markdown(_colorscale_bar(
                    "Pendiente", units="%",
                    colors=["#1a9850","#91cf60","#d9ef8b","#fee08b","#fc8d59","#d73027"],
                    ticks=["0%","10%","20%","30%","40%","50%+"],
                ), unsafe_allow_html=True)

            c3,c4 = st.columns(2)
            with c3:
                st.markdown("**🧭 Aspecto (Orientación)**")
                st_folium(_maps["aspect_map"], width=420, height=340,
                          returned_objects=[], key="map_aspect")
                st.markdown(_colorscale_bar(
                    "Orientación", units="",
                    colors=["#ff0000","#ff8800","#ffff00","#00cc00",
                            "#0000ff","#8800ff","#ff0088","#ff0000"],
                    ticks=["N","NE","E","SE","S","SO","O","NO","N"],
                ), unsafe_allow_html=True)
            with c4:
                st.markdown(f"**🌱 Zona cultivable (pendiente < {_slope_thr}%)**")
                st_folium(_maps["cultiv_map"], width=420, height=340,
                          returned_objects=[], key="map_cultiv")
                st.markdown(
                    '<div style="display:flex;gap:1.5rem;margin-top:8px">'
                    '<div style="display:flex;align-items:center;gap:6px">'
                    '<div style="width:16px;height:16px;border-radius:3px;background:#16a34a"></div>'
                    f'<span style="font-size:0.82rem">Cultivable · {_s["area_cultivable_ha"]} ha ({_s["pct_cultivable"]}%)</span></div>'
                    '<div style="display:flex;align-items:center;gap:6px">'
                    '<div style="width:16px;height:16px;border-radius:3px;background:#dc2626"></div>'
                    f'<span style="font-size:0.82rem">No cultivable · {_s["area_no_cultivable_ha"]} ha ({100-_s["pct_cultivable"]:.1f}%)</span></div>'
                    '</div>', unsafe_allow_html=True,
                )

            st.markdown("---")
            st.markdown("**Distribución de clases de pendiente**")
            _clases  = list(_s["slope_classes"].keys())
            _valores = list(_s["slope_classes"].values())
            _fig_cls = go.Figure(go.Bar(
                x=_clases, y=_valores,
                marker_color=["#2ecc71","#f1c40f","#e67e22","#e74c3c","#8e44ad"],
                text=[f"{v:.1f}%" for v in _valores], textposition="outside",
            ))
            _fig_cls.update_layout(height=260, margin=dict(t=20,b=60,l=10,r=10),
                                   yaxis=dict(title="% del área", range=[0,max(_valores)*1.2]),
                                   xaxis=dict(tickangle=-20), showlegend=False)
            st.plotly_chart(_fig_cls, use_container_width=True)
            st.session_state["area_pendiente_excluida_ha"] = _s["area_no_cultivable_ha"]


    @st.fragment
    def _ndvi_fragment():
        _pr = st.session_state.get("predio")
        if _pr is None:
            return
        st.markdown("#### 🛰️ A2-C · Análisis de Actividad Productiva (NDVI)")
        st.caption(
            "Sentinel-2 L2A COG · P25 real por píxel · 3 años de historia · "
            "Filtro de nubosidad por SCL dentro del predio"
        )

        _ndvi_thr = st.slider(
            "Umbral NDVI mínimo productivo",
            min_value=0.10, max_value=0.60, value=0.25, step=0.05,
            format="%.2f", key="ndvi_threshold",
        )

        if st.button("🔄 Calcular NDVI histórico (GEE)", type="primary", key="btn_ndvi"):
            _prog_bar  = st.progress(0.0)
            _prog_text = st.empty()

            def _progress(done, total, msg):
                _prog_bar.progress(min(done / max(total, 1), 1.0))
                _prog_text.caption(msg)

            _ok = False
            try:
                _res = get_ndvi_gee(
                    _pr["gdf"],
                    ndvi_threshold=_ndvi_thr,
                    n_years=3,
                    max_cloud_pct=20.0,
                    progress_cb=_progress,
                )
                st.session_state["ndvi_result"]       = _res
                st.session_state["area_ndvi_bajo_ha"] = _res["area_low_ha"]
                st.session_state["ndvi_low_mask"]     = _res["low_ndvi_mask"]
                _ok = True
            except Exception as e:
                st.error(f"❌ Error al obtener NDVI: {e}")
            finally:
                _prog_bar.empty()
                _prog_text.empty()
            if _ok:
                st.rerun(scope="app")

        _res = st.session_state.get("ndvi_result")
        if _res is None:
            st.info("Pulsa **Calcular NDVI histórico** para descargar datos de Sentinel-2.")
        else:
            _p25_sc = _res.get("ndvi_p25_mean")
            _n_used = _res.get("n_scenes_used", _res.get("n_scenes", "—"))
            _n_tot  = _res.get("n_scenes_total")
            c1,c2,c3,c4 = st.columns(4)
            with c1: kpi("NDVI P25 medio",  f"{_p25_sc:.3f}" if _p25_sc is not None else "—")
            with c2: kpi("NDVI P25 mínimo", f"{_res['ndvi_min']:.3f}" if _res['ndvi_min'] else "—")
            with c3: kpi("NDVI P25 máximo", f"{_res['ndvi_max']:.3f}" if _res['ndvi_max'] else "—")
            with c4: kpi("Escenas válidas",
                         f"{_n_used}/{_n_tot}" if _n_tot else str(_n_used))
            st.markdown("---")
            c1,c2 = st.columns(2)
            with c1:
                st.markdown("**🛰️ NDVI histórico (P25 anual)**")
                st_folium(_res["maps"]["ndvi_map"], width=420, height=340,
                          returned_objects=[], key="map_ndvi")
                st.markdown(_colorscale_bar(
                    "NDVI", units="",
                    colors=["#d73027","#fc8d59","#fee08b","#d9ef8b","#91cf60","#1a9850"],
                    ticks=["-0.1","0.1","0.3","0.5","0.65","0.8+"],
                ), unsafe_allow_html=True)
            with c2:
                st.markdown(f"**🌱 Zona productiva (NDVI ≥ {_ndvi_thr:.2f})**")
                st_folium(_res["maps"]["prod_map"], width=420, height=340,
                          returned_objects=[], key="map_ndvi_prod")
                st.markdown(
                    '<div style="display:flex;gap:1.5rem;margin-top:8px">'
                    '<div style="display:flex;align-items:center;gap:6px">'
                    '<div style="width:16px;height:16px;border-radius:3px;background:#16a34a"></div>'
                    f'<span style="font-size:0.82rem">Productivo · NDVI ≥ {_ndvi_thr:.2f}</span></div>'
                    '<div style="display:flex;align-items:center;gap:6px">'
                    '<div style="width:16px;height:16px;border-radius:3px;background:#dc2626"></div>'
                    f'<span style="font-size:0.82rem">Bajo umbral · {_res["area_low_ha"]} ha ({_res["pct_low"]}%)</span></div>'
                    '</div>', unsafe_allow_html=True,
                )
            _scene_stats = _res.get("scene_stats") or _res.get("stats")
            if _scene_stats:
                df_ts  = pd.DataFrame(_scene_stats).sort_values("date")
                _y_col = "mean_ndvi" if "mean_ndvi" in df_ts.columns else "median"
                fig_ts = go.Figure()
                fig_ts.add_trace(go.Scatter(
                    x=df_ts["date"], y=df_ts[_y_col],
                    mode="lines+markers", name="NDVI medio predio",
                    line=dict(color="#16a34a", width=2), marker=dict(size=4),
                ))
                fig_ts.add_hline(y=_ndvi_thr, line_dash="dash", line_color="#dc2626",
                                 annotation_text=f"Umbral {_ndvi_thr:.2f}")
                fig_ts.update_layout(
                    title=f"Serie temporal NDVI medio en el predio · {len(df_ts)} escenas útiles",
                    height=280, margin=dict(t=40,b=20),
                    xaxis=dict(title="Fecha"),
                    yaxis=dict(title="NDVI medio", range=[-0.1, 1.0]),
                )
                st.plotly_chart(fig_ts, use_container_width=True)

            st.session_state["area_ndvi_bajo_ha"] = _res["area_low_ha"]
            st.session_state["ndvi_low_mask"]     = _res["low_ndvi_mask"]


    @st.fragment
    def _area_ef_fragment():
        from PIL import Image as _Im

        _pr       = st.session_state.get("predio")
        _d        = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
        if _pr is None:
            return

        st.markdown("#### 📊 Resultado: Área Efectiva Cultivable")

        _terrain  = st.session_state.get("terrain")
        _ndvi_res = st.session_state.get("ndvi_result")
        if _terrain is None and _ndvi_res is None:
            st.info("Calcula primero el **Análisis del Terreno (A2-A)** y/o el **NDVI (A2-C)**.")
            return

        _missing = []
        if _terrain  is None: _missing.append("Terreno (A2-A)")
        if _ndvi_res is None: _missing.append("NDVI (A2-C)")
        if _missing:
            st.caption(f"ℹ️ Falta calcular: {', '.join(_missing)} — resultado será parcial.")

        if st.button("🔄 Calcular Área Efectiva Cultivable", type="primary", key="btn_area_ef"):
            st.session_state["area_ef_computed"] = True

        if not st.session_state.get("area_ef_computed"):
            st.info("Pulsa **Calcular Área Efectiva** para obtener el resultado.")
            return

        # ── Layer toggles ─────────────────────────────────────────────
        c1,c2,c3,c4 = st.columns(4)
        with c1: ver_predio_a1 = st.checkbox("🟢 Predio",           value=True, key="a1_predio")
        with c2: ver_pendiente = st.checkbox("🔴 Pendiente >umbral", value=True, key="a1_pend")
        with c3: ver_ndvi_bajo = st.checkbox("🟡 NDVI bajo umbral",  value=True, key="a1_ndvi")
        with c4: ver_const_a1  = st.checkbox("🟠 Construcciones",    value=True, key="a1_const")

        area_total = _pr.get("area_ha", _d["area_total_ha"])
        area_pend  = st.session_state.get("area_pendiente_excluida_ha", _d["area_pendiente_excluida_ha"])
        area_ndvi  = st.session_state.get("area_ndvi_bajo_ha",          0.0)
        area_const = st.session_state.get("area_construcciones_ha",     _d["area_construcciones_ha"])
        _slope_pct = st.session_state.get("slope_threshold",  25)
        _ndvi_thr  = st.session_state.get("ndvi_threshold",   0.25)
        _gdf_const = st.session_state.get("gdf_construcciones")
        _s_mask    = _terrain.get("no_cultivable_mask") if _terrain  else None
        _n_mask    = _ndvi_res.get("low_ndvi_mask")     if _ndvi_res else None
        _s_bounds  = _terrain.get("bounds_wgs84")       if _terrain  else None

        # ── Union of non-cultivable masks (pixel-level, avoids double-counting) ──
        if _n_mask is not None:
            _pmask   = ~np.isnan(_ndvi_res["ndvi_p25"])
            h, w     = _n_mask.shape
            n_predio = max(int(_pmask.sum()), 1)

            _union    = _n_mask.copy()
            area_ndvi = float((_n_mask & _pmask).sum() / n_predio * area_total)

            if _s_mask is not None:
                _sr       = np.array(_Im.fromarray(_s_mask.astype(np.uint8))
                                        .resize((w, h), _Im.NEAREST)).astype(bool)
                _union    = _union | _sr
                area_pend = float((_sr & _pmask).sum() / n_predio * area_total)

            _ndvi_bounds = _ndvi_res.get("bounds_wgs84")
            _has_b = False
            if _gdf_const is not None and len(_gdf_const) > 0 and _ndvi_bounds:
                _b_mask    = _rasterize_gdf_to_mask(_gdf_const, _ndvi_bounds, (h, w))
                _union     = _union | _b_mask
                _has_b     = True
                area_const = float((_b_mask & _pmask).sum() / n_predio * area_total)

            layers = (["pendiente", "NDVI"] if _s_mask is not None else ["NDVI"])
            if _has_b:
                layers.append("construcciones")
            metodo = "exacto (unión " + " + ".join(layers) + ")"

            n_excluido    = int((_union & _pmask).sum())
            area_excluida = float(n_excluido / n_predio * area_total)

        elif _s_mask is not None:
            _pmask_s      = ~np.isnan(_terrain.get("dem", np.array([[np.nan]])))
            n_predio_s    = max(int(_pmask_s.sum()), 1)
            area_pend     = float((_s_mask & _pmask_s).sum() / n_predio_s * area_total)
            area_excluida = area_pend + area_const
            metodo        = "parcial (solo pendiente + construcciones; calcula NDVI para resultado exacto)"
        else:
            area_excluida = area_pend + area_ndvi + area_const
            metodo        = "aproximado (calcula A2-A y A2-C para resultado exacto)"

        area_ef = round(max(area_total - area_excluida, 0), 2)
        pct_ef  = round(area_ef / area_total * 100) if area_total > 0 else 0

        # ── Map ───────────────────────────────────────────────────────
        m_a1 = _base_map(_pr["gdf"])
        if ver_predio_a1: _add_predio(m_a1, _pr["gdf"])
        if ver_pendiente and _terrain is not None and _s_bounds is not None:
            slope_png = _colored_mask_png(_terrain["no_cultivable_mask"], 220, 38, 38)
            bx_s = [[_s_bounds[1], _s_bounds[0]], [_s_bounds[3], _s_bounds[2]]]
            folium.raster_layers.ImageOverlay(
                image=slope_png, bounds=bx_s, opacity=1.0,
                name=f"Pendiente >{_slope_pct}%",
            ).add_to(m_a1)
        if ver_ndvi_bajo and _n_mask is not None:
            _ndvi_bounds = _ndvi_res.get("bounds_wgs84") if _ndvi_res else None
            if _ndvi_bounds is None:
                _ndvi_bounds = _pr["gdf"].to_crs("EPSG:4326").total_bounds.tolist()
            ndvi_png = _colored_mask_png(_n_mask, 234, 179, 8)
            bx_n = [[_ndvi_bounds[1], _ndvi_bounds[0]], [_ndvi_bounds[3], _ndvi_bounds[2]]]
            folium.raster_layers.ImageOverlay(
                image=ndvi_png, bounds=bx_n, opacity=1.0,
                name=f"NDVI < {_ndvi_thr:.2f}",
            ).add_to(m_a1)
        if ver_const_a1 and _gdf_const is not None and len(_gdf_const) > 0:
            folium.GeoJson(
                data=_gdf_const.to_json(),
                style_function=lambda _: {"fillColor":"#f97316","color":"#ea580c",
                                           "weight":1.5,"fillOpacity":0.70},
                tooltip="Construcción",
            ).add_to(m_a1)
        _fit(m_a1, _pr["gdf"])
        st_folium(m_a1, width=700, height=380, returned_objects=[], key="map_a1")

        # ── Table + gauge ─────────────────────────────────────────────
        c_left, c_right = st.columns([2, 1])
        with c_left:
            solapamiento = round(area_pend + area_ndvi + area_const - area_excluida, 3)
            _componentes = ["Área total del predio",
                            f"− Pendiente >{_slope_pct}% (A2-A)",
                            f"− NDVI < {_ndvi_thr:.2f} (A2-C)",
                            "− Construcciones (A2-B)"]
            _hectareas   = [area_total, -area_pend, -area_ndvi, -area_const]
            if solapamiento > 0:
                _componentes.append(f"  ↳ Solapamiento recuperado ({metodo})")
                _hectareas.append(solapamiento)
            _componentes.append("✅ Área efectiva cultivable")
            _hectareas.append(area_ef)
            df_area = pd.DataFrame({"Componente": _componentes, "Hectáreas": _hectareas})
            st.dataframe(
                df_area.style.apply(lambda x: [
                    "font-weight:bold;background:#d1fae5" if "✅" in str(v) else
                    "color:#64748b;font-style:italic"     if "↳"  in str(v) else ""
                    for v in x], axis=1),
                use_container_width=True, hide_index=True,
            )
        with c_right:
            st.plotly_chart(gauge_riesgo(pct_ef, "% Área efectiva"), use_container_width=True)
            kpi("Área efectiva", area_ef, "ha")

        st.session_state["area_ef_result"] = {
            "area_ef":   area_ef,
            "pct_ef":    pct_ef,
            "area_pend": area_pend,
            "area_ndvi": area_ndvi,
            "area_const": area_const,
            "slope_pct": _slope_pct,
            "ndvi_thr":  _ndvi_thr,
        }


    with st.expander("📐 A2A · Análisis del Terreno", expanded=True):
        _terrain_fragment()

    with st.expander("🏗️ A2B · Análisis de Construcciones", expanded=True):

        st.markdown("#### 🏗️ A2-B · Análisis de Construcciones")
        with st.spinner("Cargando construcciones..."):
            gdf_const = get_construcciones(predio["gdf"])
        st.session_state["gdf_construcciones"] = gdf_const

        if gdf_const is None or len(gdf_const) == 0:
            st.info("No se han identificado construcciones dentro del predio.")
            area_const_real = 0.0
        else:
            df_const = gdf_const[["codigo","identifica","tipo_const","numero_pis","area_ha"]].copy()
            df_const.columns = ["Código","Uso / Identificación","Tipo","Pisos","Área (ha)"]
            st.dataframe(df_const, use_container_width=True, hide_index=True)
            area_const_real = float(gdf_const["area_ha"].sum())
            kpi("Área total construida", f"{area_const_real:.4f}", "ha")
            c1,c2 = st.columns(2)
            with c1: ver_predio_c = st.checkbox("🟢 Predio",         value=True, key="c_predio")
            with c2: ver_const_c  = st.checkbox("🟠 Construcciones", value=True, key="c_const")
            st_folium(mapa_capa(
                predio["gdf"], gdf_const,
                mostrar_predio=ver_predio_c, mostrar_capa=ver_const_c,
                estilo_capa_fn=lambda _: {"fillColor":"#f97316","color":"#ea580c",
                                           "weight":1.5,"fillOpacity":0.70},
                campos_tooltip=["identifica","tipo_const","area_ha"],
                aliases_tooltip=["Identificación","Tipo","Área (ha)"],
                nombre_capa="Construcciones",
            ), width=700, height=350, returned_objects=[], key="map_const")

        st.session_state["area_construcciones_ha"] = (
            area_const_real if gdf_const is not None else 0.0)


    with st.expander("🛰️ A2C · Análisis de Actividad Productiva (NDVI)", expanded=True):
        _ndvi_fragment()

    with st.expander("📊 A2 · Área Efectiva Cultivable", expanded=True):
        _area_ef_fragment()

    # ════════════════════════════════════════════════════════════════════
    #  B · CONTINUIDAD PRODUCTIVA
    # ════════════════════════════════════════════════════════════════════
    st.markdown("### 🌱 B · Validación de Continuidad Productiva")

    with st.expander(f"🌾 B1 · Aptitud al Cultivo ({cultivo})", expanded=True):
        with st.spinner("Calculando aptitud vía API datos.gov.co …"):
            apt_result = _get_aptitud_cached(predio["gdf"], cultivo)

        if apt_result is None:
            cultivos_con_api = ", ".join(CULTIVO_API_MAP.keys())
            st.warning(
                f"No hay API de aptitud disponible para **{cultivo}** en datos.gov.co.\n\n"
                f"Cultivos con API disponible: {cultivos_con_api}."
            )
            gdf_aptitud = None

        elif apt_result["error"]:
            st.error(f"❌ Error consultando API de aptitud: {apt_result['error']}")
            gdf_aptitud = None

        else:
            gdf_aptitud = apt_result["gdf"]
            score       = apt_result["score"]
            category    = apt_result["category"]

            # ── Métricas de aptitud ───────────────────────────────────────
            color_cat = {"Alta": "🟢", "Media": "🟡", "Baja": "🔴"}.get(category, "⚪")
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Score de Aptitud", f"{score:.2f}",
                          help="Score ponderado por área: 0 = No apta · 1 = Alta aptitud")
            with mc2:
                st.metric("Categoría de Aptitud", f"{color_cat} {category}")
            with mc3:
                st.metric("Fuente", "UPRA · datos.gov.co")

        st.session_state["gdf_aptitud"] = gdf_aptitud

        c1, c2 = st.columns(2)
        with c1: ver_predio_b1  = st.checkbox("🟢 Predio",  value=True, key="b1_predio")
        with c2: ver_aptitud_b1 = st.checkbox("🟦 Aptitud", value=True, key="b1_apt")

        def estilo_aptitud(feature):
            color = COLORES_APTITUD.get(feature["properties"].get("aptitud", ""), "#3b82f6")
            return {"fillColor": color, "color": color, "weight": 1.5, "fillOpacity": 0.45}

        st_folium(mapa_capa(
            predio["gdf"], gdf_aptitud,
            mostrar_predio=ver_predio_b1, mostrar_capa=ver_aptitud_b1,
            estilo_capa_fn=estilo_aptitud,
            campos_tooltip=["aptitud", "area_ha", "pct_predio"],
            aliases_tooltip=["Aptitud", "Área (ha)", "% predio"],
            nombre_capa="Aptitud cultivo",
        ), width=700, height=380, returned_objects=[], key="map_b1")

        if gdf_aptitud is not None and len(gdf_aptitud) > 0:
            df_apt = (
                gdf_aptitud
                .groupby("aptitud")
                .agg(area_ha=("area_ha", "sum"), pct_predio=("pct_predio", "sum"))
                .reset_index()
                .rename(columns={"aptitud": "Aptitud",
                                 "area_ha": "Área (ha)", "pct_predio": "% del predio"})
            )
            st.dataframe(df_apt, use_container_width=True, hide_index=True)
            st.caption(
                "Pesos por categoría: Alta → 1.00 · Media → 0.67 · Baja → 0.33 · No apta → 0  |  "
                "Alta ≥ 0.70 · Media ≥ 0.40 · Baja < 0.40"
            )
        elif apt_result is not None and apt_result.get("error") is None:
            st.warning("No se encontró información de aptitud para este predio.")

    with st.expander("📊 B2 · Actividad Productiva (NDVI)", expanded=True):
        st.caption(
            "Sentinel-2 vía EOSDA Statistics API · Últimos 3 años · "
            "Confirma que el predio ha tenido actividad vegetativa activa"
        )

        _B2_STYLE = {
            "verde":    {"bg":"#d1fae5","bd":"#059669","tx":"#065f46",
                         "label":"🟢 Actividad productiva confirmada"},
            "amarillo": {"bg":"#fef9c3","bd":"#ca8a04","tx":"#713f12",
                         "label":"🟡 Verificación recomendada"},
            "rojo":     {"bg":"#fee2e2","bd":"#dc2626","tx":"#7f1d1d",
                         "label":"🔴 Actividad productiva no confirmada"},
        }
        _SEM_BADGE = {
            "verde":    '<span style="background:#d1fae5;color:#065f46;border-radius:4px;padding:1px 7px;font-size:0.78rem">🟢 Confirmado</span>',
            "amarillo": '<span style="background:#fef9c3;color:#713f12;border-radius:4px;padding:1px 7px;font-size:0.78rem">🟡 Parcial</span>',
            "rojo":     '<span style="background:#fee2e2;color:#7f1d1d;border-radius:4px;padding:1px 7px;font-size:0.78rem">🔴 No confirmado</span>',
        }

        import json as _json
        _geo = _json.dumps(
            predio["gdf"].to_crs("EPSG:4326").geometry.iloc[0].__geo_interface__
        )
        if st.button("🔄 Calcular actividad productiva (NDVI)", type="primary", key="btn_b2"):
            st.session_state["b2_result"] = None
            with st.spinner("Descargando serie NDVI histórica (EOSDA · 3 años)…"):
                try:
                    st.session_state["b2_result"] = _get_b2_cached(_geo, cultivo)
                except Exception as _e:
                    st.error(f"❌ No se pudo obtener la serie NDVI: {_e}")

        b2     = st.session_state.get("b2_result")
        b2_ok  = b2 is not None
        if not b2_ok:
            st.info("Pulsa **Calcular actividad productiva** para descargar la serie NDVI. "
                    "El resultado queda en caché 24 h.")

        if b2_ok:
            s        = b2["semaforo"]
            sty      = _B2_STYLE[s]
            s_thr    = b2["scene_threshold"]
            p_thr    = b2["peak_threshold"]
            df_ts    = pd.DataFrame(b2["stats"]).sort_values("date")
            n_scenes = len(df_ts)

            st.caption("ℹ️ Fuentes de datos, hipótesis y tablas de decisión en la tab **📖 Metodología**.")

            # ── KPIs ─────────────────────────────────────────────────────
            c1,c2,c3,c4 = st.columns(4)
            with c1: kpi("Escenas activas",
                         f"{b2['pct_active']:.0f}%",
                         f"NDVI ≥ {s_thr:.2f}")
            with c2: kpi("Pico NDVI anual",
                         f"{b2['years_with_peak']}/{b2['n_years']} años calendario",
                         f"pico ≥ {p_thr:.2f}")
            with c3: kpi("NDVI mediano global", f"{b2['overall_median']:.3f}")
            with c4: kpi("Escenas analizadas", n_scenes)

            st.markdown("---")

            # ── Semáforo ─────────────────────────────────────────────────
            peak_rows = "".join(
                f'<span style="margin-right:1rem"><b>{yr}</b>: {v:.2f} '
                f'{"✅" if v >= p_thr else "⚠️"}</span>'
                for yr, v in sorted(b2["peak_by_year"].items())
            )
            st.markdown(
                f'<div style="background:{sty["bg"]};border-left:5px solid {sty["bd"]};'
                f'padding:1rem 1.2rem;border-radius:8px;margin-bottom:1rem">'
                f'<div style="font-size:1.05rem;font-weight:700;color:{sty["tx"]};'
                f'margin-bottom:0.4rem">{sty["label"]}</div>'
                f'<div style="font-size:0.88rem;color:{sty["tx"]};margin-bottom:0.6rem">'
                f'{b2["decision"]}</div>'
                f'<div style="display:flex;gap:2rem;font-size:0.82rem;color:{sty["tx"]};'
                f'flex-wrap:wrap">'
                f'<div>Escenas activas ({s_thr:.2f}): '
                f'<b>{b2["pct_active"]:.0f}%</b> &nbsp;{_SEM_BADGE[b2["semaforo_pct"]]}</div>'
                f'<div>Pico anual ({p_thr:.2f}): '
                f'<b>{b2["years_with_peak"]}/{b2["n_years"]} años</b> '
                f'&nbsp;{_SEM_BADGE[b2["semaforo_peak"]]}</div>'
                f'</div>'
                f'<div style="margin-top:0.5rem;font-size:0.80rem;color:{sty["tx"]}">'
                f'Picos por año: {peak_rows}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── Serie temporal ────────────────────────────────────────────
            if "median" in df_ts.columns:
                df_ts["above"] = df_ts["median"] >= s_thr
                # Pico por año
                peak_map = b2["peak_by_year"]
                df_ts["year"] = df_ts["date"].str[:4].astype(int)
                df_ts["is_peak"] = df_ts.apply(
                    lambda r: abs(r["median"] - peak_map.get(r["year"], -99)) < 1e-6,
                    axis=1,
                )

                fig_b2 = go.Figure()

                # Línea de conexión tenue
                fig_b2.add_trace(go.Scatter(
                    x=df_ts["date"], y=df_ts["median"],
                    mode="lines", line=dict(color="#cbd5e1", width=1),
                    showlegend=False,
                ))
                # Puntos: verde / rojo según umbral
                for above, color, name in [
                    (True,  "#16a34a", f"NDVI ≥ {s_thr:.2f}"),
                    (False, "#dc2626", f"NDVI < {s_thr:.2f}"),
                ]:
                    mask = df_ts["above"] == above
                    fig_b2.add_trace(go.Scatter(
                        x=df_ts[mask]["date"], y=df_ts[mask]["median"],
                        mode="markers", marker=dict(color=color, size=5),
                        name=name,
                    ))
                # Estrellas: pico anual
                df_peak = df_ts[df_ts["is_peak"]]
                fig_b2.add_trace(go.Scatter(
                    x=df_peak["date"], y=df_peak["median"],
                    mode="markers",
                    marker=dict(symbol="star", size=12, color="#f59e0b",
                                line=dict(color="#92400e", width=1)),
                    name="Pico anual",
                ))

                fig_b2.add_hline(
                    y=s_thr, line_dash="dash", line_color="#dc2626",
                    annotation_text=f"Umbral escena {s_thr:.2f}",
                    annotation_position="top left",
                )
                fig_b2.update_layout(
                    title=f"Serie NDVI mediano por escena · {n_scenes} escenas · últimos 3 años",
                    height=300, margin=dict(t=45, b=20),
                    xaxis=dict(title="Fecha"),
                    yaxis=dict(title="NDVI mediano", range=[-0.05, 1.0]),
                    legend=dict(orientation="h", y=-0.18),
                )
                st.plotly_chart(fig_b2, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════
    #  C · INFRAESTRUCTURA
    # ════════════════════════════════════════════════════════════════════
    with st.expander("🏗️ C · Validación de Infraestructura Productiva", expanded=False):

        st.caption(
            "Evalúa la conectividad física del predio con los mercados y la red vial. "
            "El semáforo global refleja el peor de los dos indicadores: un predio cerca "
            "de la ciudad pero sin acceso a carretera tiene el mismo riesgo logístico "
            "que uno alejado con buena vía."
        )

        _centroid = predio["gdf"].geometry.iloc[0].centroid
        c_lat, c_lon = _centroid.y, _centroid.x

        with st.spinner("Calculando distancias de infraestructura vía OSM / OSRM …"):
            infra_centro = _get_distancia_centro_cached(c_lat, c_lon)
            infra_via    = _get_distancia_via_cached(c_lat, c_lon)

        # ── Semáforo global (peor caso) ───────────────────────────────
        _COLOR_RANK = {"verde": 0, "naranja": 1, "rojo": 2}
        _color_cu  = (
            "verde" if infra_centro and infra_centro["distancia_km"] < 10 else
            "naranja" if infra_centro and infra_centro["distancia_km"] < 25 else
            "rojo"
        )
        _color_via = (
            "verde" if infra_via and infra_via["distancia_m"] < 500 else
            "naranja" if infra_via and infra_via["distancia_m"] < 2000 else
            "rojo"
        )
        _color_global = max([_color_cu, _color_via], key=lambda c: _COLOR_RANK[c])
        _label_global = {"verde": "Acceso adecuado", "naranja": "Acceso medio", "rojo": "Acceso bajo"}[_color_global]
        if _color_global == "verde":
            _detalle_global = "Ambos indicadores en rango adecuado."
        else:
            _limitantes = []
            if _COLOR_RANK[_color_cu] == _COLOR_RANK[_color_global] and infra_centro:
                _limitantes.append(f"centro urbano ({infra_centro['distancia_km']} km por carretera)")
            if _COLOR_RANK[_color_via] == _COLOR_RANK[_color_global] and infra_via:
                _limitantes.append(f"vía transitable ({infra_via['distancia_m']:.0f} m en línea recta)")
            _detalle_global = "Limitante: " + " · ".join(_limitantes) + "."
        semaforo(f"**{_label_global}** · {_detalle_global}", _color_global)

        st.markdown("---")
        c1, c2 = st.columns(2)

        # ── C1 · Distancia al centro urbano más cercano ───────────────
        with c1:
            st.markdown("#### 🏙️ Centro urbano más cercano")
            if infra_centro is None:
                st.warning("No se encontró ruta a ningún centro urbano en un radio de 80 km.")
            else:
                dist_cu = infra_centro["distancia_km"]
                kpi("Distancia por carretera", dist_cu, "km")
                kpi("Duración estimada", infra_centro["duracion_min"], "min")
                st.caption(
                    f"**{infra_centro['nombre']}** ({infra_centro['tipo']}) · "
                    f"{infra_centro['dist_recta_km']} km en línea recta"
                )
                semaforo(
                    f"{'< 10 km' if dist_cu < 10 else '10–25 km' if dist_cu < 25 else '> 25 km'} "
                    f"por carretera ({dist_cu} km).",
                    _color_cu,
                )

        # ── C2 · Distancia a la vía transitable más cercana ──────────
        with c2:
            st.markdown("#### 🛣️ Vía transitable más cercana")
            if infra_via is None:
                st.warning("No se encontró ninguna vía transitable en un radio de 5 km.")
            else:
                dist_via_m  = infra_via["distancia_m"]
                dist_via_km = infra_via["distancia_km"]
                kpi("Distancia en línea recta", dist_via_m, "m")
                st.caption(
                    f"**{infra_via['nombre']}** · tipo: `{infra_via['tipo']}` · "
                    f"{dist_via_km} km"
                )
                semaforo(
                    f"{'< 500 m' if dist_via_m < 500 else '500 m – 2 km' if dist_via_m < 2000 else '> 2 km'} "
                    f"a vía transitable ({dist_via_m:.0f} m).",
                    _color_via,
                )

        # ── Leyenda de umbrales ───────────────────────────────────────
        st.markdown("""
<table style="width:100%;border-collapse:collapse;font-size:0.82rem;margin-top:0.8rem">
<thead><tr style="background:#f1f5f9;font-weight:600;text-align:center">
  <td style="padding:5px 10px">Condición</td>
  <td style="padding:5px 10px">Centro urbano</td>
  <td style="padding:5px 10px">Vía transitable</td>
</tr></thead>
<tr style="background:#d1fae5;text-align:center">
  <td style="padding:6px 10px">🟢 Acceso adecuado</td>
  <td style="padding:6px 10px">&lt; 10 km por carretera</td>
  <td style="padding:6px 10px">&lt; 500 m en línea recta</td>
</tr>
<tr style="background:#fef3c7;text-align:center">
  <td style="padding:6px 10px">🟡 Acceso medio</td>
  <td style="padding:6px 10px">10 – 25 km</td>
  <td style="padding:6px 10px">500 m – 2 km</td>
</tr>
<tr style="background:#fee2e2;text-align:center">
  <td style="padding:6px 10px">🔴 Acceso bajo</td>
  <td style="padding:6px 10px">&gt; 25 km</td>
  <td style="padding:6px 10px">&gt; 2 km</td>
</tr>
</table>
<p style="font-size:0.75rem;color:#64748b;margin-top:4px">
  El semáforo global toma el peor de los dos indicadores.
</p>
""", unsafe_allow_html=True)

        # ── Mapa de infraestructura ───────────────────────────────────
        st.markdown("---")
        m_infra = _base_map(predio["gdf"])
        _add_predio(m_infra, predio["gdf"])

        # Marcador del centroide real del predio
        folium.CircleMarker(
            location=[c_lat, c_lon], radius=8,
            color="#16a34a", fill=True, fill_color="#16a34a", fill_opacity=0.9,
            tooltip="Centroide del predio",
        ).add_to(m_infra)

        if infra_centro is not None:
            folium.PolyLine(
                locations=infra_centro["coords"],
                color="#3b82f6", weight=3, opacity=0.85,
                tooltip=f"🔵 Ruta carretera: {infra_centro['distancia_km']} km · {infra_centro['duracion_min']} min",
            ).add_to(m_infra)
            cu_lat = infra_centro.get("lat")
            cu_lon = infra_centro.get("lon")
            if cu_lat is None and infra_centro["coords"]:
                cu_lat, cu_lon = infra_centro["coords"][-1]
            if cu_lat is not None:
                folium.Marker(
                    location=[cu_lat, cu_lon],
                    tooltip=f"🏙️ {infra_centro['nombre']} ({infra_centro['tipo']})",
                    icon=folium.Icon(color="blue", icon="home", prefix="fa"),
                ).add_to(m_infra)

        if infra_via is not None:
            n_lat = infra_via.get("nearest_lat")
            n_lon = infra_via.get("nearest_lon")
            if n_lat is not None and n_lon is not None:
                folium.PolyLine(
                    locations=[[c_lat, c_lon], [n_lat, n_lon]],
                    color="#dc2626", weight=2.5, opacity=1.0, dash_array="6 6",
                    tooltip=f"Distancia a vía: {infra_via['distancia_m']:.0f} m (línea recta)",
                ).add_to(m_infra)
                folium.CircleMarker(
                    location=[n_lat, n_lon],
                    radius=6, color="#dc2626", fill=True,
                    fill_color="#dc2626", fill_opacity=0.9,
                    tooltip=f"Punto en vía · {infra_via['nombre']} ({infra_via['tipo']})",
                ).add_to(m_infra)

        _fit(m_infra, predio["gdf"])
        st_folium(m_infra, width=None, height=420,
                  returned_objects=[], key="map_infra")

    # ════════════════════════════════════════════════════════════════════
    #  D · ANÁLISIS DE RIESGO AGROCLIMÁTICO
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🌧️ D · Análisis de Riesgo Agroclimático")
    st.caption(
        "Datos históricos diarios ERA5 (Open-Meteo, 10 años) · "
        "Indicadores calculados según matriz de vulnerabilidad por cultivo · "
        "Score de riesgo P80 anual"
    )

    # ── Descarga de datos climáticos ─────────────────────────────────
    _clima_ok = False
    try:
        with st.spinner("Descargando datos climáticos históricos (Open-Meteo ERA5) …"):
            df_clima   = _get_climate_cached(c_lat, c_lon, cultivo)
            df_monthly = monthly_climatology(df_clima)
        _clima_ok = True
    except Exception as _e_clima:
        st.warning(f"No se pudo descargar datos climáticos: {_e_clima}")
        df_clima, df_monthly = None, None

    MESES_ES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    with st.expander("📊 D1 · Datos Climáticos Históricos (ERA5 · 10 años)", expanded=True):
        if not _clima_ok or df_monthly is None:
            st.info("No se pudieron cargar los datos climáticos históricos.")
        else:
            n_yrs = df_clima["year"].nunique()
            st.caption(
                f"Fuente: Open-Meteo ERA5 · {n_yrs} años · lat {c_lat:.4f}, lon {c_lon:.4f} · "
                "Valores promediados por mes sobre el período histórico completo."
            )
            c1, c2 = st.columns(2)
            with c1:
                fig_p = px.bar(
                    x=MESES_ES, y=df_monthly["pr_mean"].round(1),
                    labels={"x": "Mes", "y": "mm"},
                    title="Precipitación media mensual (mm)",
                    color_discrete_sequence=["#3b82f6"],
                )
                fig_p.update_layout(height=260, margin=dict(t=40, b=20))
                st.plotly_chart(fig_p, use_container_width=True)
            with c2:
                fig_t = go.Figure()
                fig_t.add_trace(go.Scatter(
                    x=MESES_ES, y=df_monthly["tmax_mean"].round(1),
                    name="T máx", line=dict(color="#ef4444", width=2),
                ))
                fig_t.add_trace(go.Scatter(
                    x=MESES_ES, y=df_monthly["tmin_mean"].round(1),
                    name="T mín", line=dict(color="#3b82f6", width=2),
                    fill="tonexty", fillcolor="rgba(59,130,246,0.1)",
                ))
                fig_t.add_trace(go.Scatter(
                    x=MESES_ES, y=df_monthly["tavg_mean"].round(1),
                    name="T media", line=dict(color="#f59e0b", width=1.5, dash="dot"),
                ))
                fig_t.update_layout(
                    title="Temperatura mensual (°C)",
                    height=260, margin=dict(t=40, b=20),
                    yaxis_title="°C",
                )
                st.plotly_chart(fig_t, use_container_width=True)

            c3, c4 = st.columns(2)
            with c3:
                fig_rh = px.line(
                    x=MESES_ES, y=df_monthly["rh_mean"].round(1),
                    labels={"x": "Mes", "y": "%"},
                    title="Humedad relativa media mensual (%)",
                    color_discrete_sequence=["#8b5cf6"],
                )
                fig_rh.add_hline(y=80, line_dash="dash", line_color="#dc2626",
                                 annotation_text="80%")
                fig_rh.update_layout(height=240, margin=dict(t=40, b=20))
                st.plotly_chart(fig_rh, use_container_width=True)
            with c4:
                fig_wd = px.bar(
                    x=MESES_ES, y=df_monthly["pr_days"].round(0),
                    labels={"x": "Mes", "y": "días"},
                    title="Media de días con lluvia > 1 mm / mes",
                    color_discrete_sequence=["#0ea5e9"],
                )
                fig_wd.update_layout(height=240, margin=dict(t=40, b=20))
                st.plotly_chart(fig_wd, use_container_width=True)

    # ── D2 · Indicadores de Riesgo Agroclimático ─────────────────────
    _cultivos_con_matriz = crops_with_matrix()
    _cultivo_tiene_matriz = cultivo in _cultivos_con_matriz

    with st.expander(
        f"🌡️ D2 · Indicadores de Riesgo Agroclimático · {cultivo}",
        expanded=True,
    ):
        st.caption(
            "Score de riesgo calculado para cada indicador de la matriz de vulnerabilidad. "
            "Se usa el **percentil 80 anual** (escenario adverso 1 de cada 5 años) como "
            "referencia para el análisis crediticio."
        )

        if not _cultivo_tiene_matriz:
            st.warning(
                f"No hay matriz de vulnerabilidad disponible para **{cultivo}**. "
                f"Cultivos con matriz: {', '.join(_cultivos_con_matriz)}."
            )
        elif not _clima_ok:
            st.warning("Se necesitan datos climáticos para calcular los indicadores (ver D1).")
        else:
            with st.spinner("Calculando indicadores de riesgo …"):
                df_risk = _get_risk_cached(c_lat, c_lon, cultivo, mtime=_matrix_mtime())

            if df_risk.empty:
                st.info("No se pudieron calcular indicadores para este cultivo.")
            else:
                # ── Semáforo global ──────────────────────────────────
                _COLOR_BG = {
                    "verde":    "#d1fae5", "amarillo": "#dcfce7",
                    "naranja":  "#fef9c3", "rojo":     "#fed7aa",
                    "granate":  "#fee2e2", "gris":     "#f1f5f9",
                }
                _COLOR_BD = {
                    "verde":    "#22c55e", "amarillo": "#86efac",
                    "naranja":  "#eab308", "rojo":     "#f97316",
                    "granate":  "#ef4444", "gris":     "#94a3b8",
                }
                _EMOJI_5 = {
                    "verde":   "🟢", "amarillo": "🟡",
                    "naranja": "🟠", "rojo":     "🔴",
                    "granate": "⛔", "gris":     "⚪",
                }
                _agg_score = aggregate_risk_score(df_risk)
                if not np.isnan(_agg_score):
                    _gl = score_to_label(_agg_score)
                    _gc = score_to_color(_agg_score)
                    _n_cat = df_risk["Categoría_riesgo"].nunique()
                    st.markdown(
                        f'<div style="background:{_COLOR_BG[_gc]};border-left:6px solid '
                        f'{_COLOR_BD[_gc]};padding:0.8rem 1.2rem;border-radius:6px;margin-bottom:1rem">'
                        f'<b style="font-size:1.05rem">Riesgo agroclimático global: {_gl}</b>'
                        f'<span style="font-size:0.82rem;margin-left:1rem;color:#475569">'
                        f'Score agregado: {_agg_score:.2f} '
                        f'(media del peor indicador por categoría · {_n_cat} categorías)'
                        f'</span></div>',
                        unsafe_allow_html=True,
                    )

                # ── Tabla de indicadores por categoría ───────────────
                _UNIT_ES = {"day": "días", "days": "días", "month": "meses", "months": "meses", "date": "fecha"}
                _ROW_BG = {
                    "verde":   "#d1fae5", "amarillo": "#dcfce7",
                    "naranja": "#fef9c3", "rojo":     "#fed7aa",
                    "granate": "#fee2e2", "gris":     "#f8fafc",
                }
                _LABEL_COLOR = {
                    "verde":   "#166534", "amarillo": "#166534",
                    "naranja": "#713f12", "rojo":     "#7c2d12",
                    "granate": "#7f1d1d", "gris":     "#475569",
                }

                def _doy_to_mmdd(doy: float) -> str:
                    from datetime import date, timedelta
                    try:
                        d = date(2001, 1, 1) + timedelta(days=int(round(doy)) - 1)
                        return d.strftime("%d/%m")
                    except Exception:
                        return str(round(doy))

                for cat in df_risk["Categoría_riesgo"].unique():
                    df_cat = df_risk[df_risk["Categoría_riesgo"] == cat]
                    st.markdown(f"**{cat}**")
                    rows_html = ""
                    for _, r in df_cat.iterrows():
                        color   = r.get("riesgo_color", "gris")
                        bg      = _ROW_BG.get(color, "#f8fafc")
                        fg      = _LABEL_COLOR.get(color, "#1e293b")
                        em      = _EMOJI_5.get(color, "⚪")
                        is_date = str(r["Unidad"]) == "date"
                        unidad  = _UNIT_ES.get(str(r["Unidad"]), r["Unidad"])
                        def _fmt(v, _is_date=is_date, _u=unidad):
                            if v is None: return "—"
                            return _doy_to_mmdd(v) if _is_date else f"{v} {_u}"
                        score_str = f"{r['score_p80']:.2f}" if r["score_p80"] is not None else "—"
                        rows_html += (
                            f'<tr style="background:{bg}">'
                            f'<td style="padding:5px 8px;color:{fg};font-weight:600;white-space:nowrap">'
                            f'{r["riesgo_label"]}</td>'
                            f'<td style="padding:5px 8px">{r["Nombre_indicador"]}</td>'
                            f'<td style="padding:5px 8px;text-align:right;white-space:nowrap">{_fmt(r["valor_medio"])}</td>'
                            f'<td style="padding:5px 8px;text-align:right;white-space:nowrap">{_fmt(r["valor_p80"])}</td>'
                            f'<td style="padding:5px 8px;text-align:right">{score_str}</td>'
                            f'</tr>'
                        )
                    st.markdown(
                        '<table style="width:100%;border-collapse:collapse;font-size:0.84rem;margin-bottom:0.8rem">'
                        '<thead><tr style="background:#f1f5f9;font-weight:600;text-align:left">'
                        '<td style="padding:5px 8px">Riesgo</td>'
                        '<td style="padding:5px 8px">Indicador</td>'
                        '<td style="padding:5px 8px;text-align:right">Valor medio</td>'
                        '<td style="padding:5px 8px;text-align:right">Valor P80</td>'
                        '<td style="padding:5px 8px;text-align:right">Score P80</td>'
                        f'</tr></thead><tbody>{rows_html}</tbody></table>',
                        unsafe_allow_html=True,
                    )

                # ── Curvas de vulnerabilidad ──────────────────────────
                st.markdown("---")
                st.markdown("**Curvas de vulnerabilidad por indicador**")
                st.caption(
                    "Valores de referencia que delimitan cada nivel de riesgo. "
                    "El score P80 es el usado para la clasificación."
                )
                cols_curva = ["Nombre_indicador", "Unidad",
                              "Sin_riesgo_0", "Riesgo_bajo_0.25",
                              "Riesgo_medio_0.5", "Riesgo_alto_0.75",
                              "Riesgo_extremo_1", "Forma_curva"]
                df_curva = df_risk[cols_curva].copy()
                df_curva["Unidad"] = df_curva["Unidad"].map(
                    lambda u: "fecha (DD/MM)" if str(u) == "date" else _UNIT_ES.get(str(u), u)
                )
                df_curva = df_curva.rename(columns={
                    "Nombre_indicador":  "Indicador",
                    "Sin_riesgo_0":      "Sin riesgo",
                    "Riesgo_bajo_0.25":  "Bajo (0.25)",
                    "Riesgo_medio_0.5":  "Medio (0.50)",
                    "Riesgo_alto_0.75":  "Alto (0.75)",
                    "Riesgo_extremo_1":  "Extremo (1.0)",
                    "Forma_curva":       "Curva",
                })
                for _col in ["Sin riesgo", "Bajo (0.25)", "Medio (0.50)", "Alto (0.75)", "Extremo (1.0)"]:
                    if _col in df_curva.columns:
                        df_curva[_col] = df_curva[_col].astype(str)
                st.dataframe(df_curva, use_container_width=True, hide_index=True)
                st.caption(
                    "Score interpolado linealmente entre umbrales. "
                    "Se usa el percentil 80 anual (escenario adverso 1 de cada 5 años). "
                    "Colores: sin riesgo · bajo · medio · alto · extremo."
                )


    # ════════════════════════════════════════════════════════════════════
    #  RESUMEN VALIDACIÓN PRE-CRÉDITO
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📋 Resumen de Validación Pre-Crédito")
    st.caption("Semáforo global por indicador · Ver criterios detallados en la tab **📖 Metodología**.")

    _SEM_BG  = {"verde":"#d1fae5","amarillo":"#fef9c3","naranja":"#fef3c7","rojo":"#fee2e2","gris":"#f1f5f9"}
    _SEM_BD  = {"verde":"#059669","amarillo":"#ca8a04","naranja":"#d97706","rojo":"#dc2626","gris":"#94a3b8"}
    _SEM_TX  = {"verde":"#065f46","amarillo":"#713f12","naranja":"#713f12","rojo":"#7f1d1d","gris":"#475569"}
    _SEM_EM  = {"verde":"🟢","amarillo":"🟡","naranja":"🟡","rojo":"🔴","gris":"⚪"}
    _SEM_ACT = {
        "verde":    "Sin restricción",
        "amarillo": "Documentación adicional recomendada",
        "naranja":  "Documentación adicional recomendada",
        "rojo":     "Inspección técnica / verificación presencial",
        "gris":     "Pendiente de cálculo",
    }

    # ── Collect semáforos ─────────────────────────────────────────────
    _gdf_front = st.session_state.get("gdf_frontera")
    _apt_res   = apt_result if "apt_result" in dir() else None

    # A1: read nivel already computed in the A1 expander (stored in session_state)
    _sem_a1 = st.session_state.get("a1_nivel", "gris")
    if _gdf_front is not None and len(_gdf_front) > 0:
        _tipos_a1   = _gdf_front["tipo_condi"].unique().tolist()
        _pct_tot_a1 = float(_gdf_front["pct_predio"].sum())
        _pct_out_a1 = round(max(0.0, 100.0 - _pct_tot_a1), 1)
        _tipos_cond_a1 = [t for t in _tipos_a1 if t != "Frontera Agrícola no condicionada"]
        if _pct_out_a1 > 2:
            _res_a1 = f"{_pct_out_a1:.1f}% fuera de Frontera Agrícola"
        elif _tipos_cond_a1:
            _res_a1 = f"Condicionada: {', '.join(_tipos_cond_a1)}"
        else:
            _res_a1 = "Todo en Frontera Agrícola no condicionada"
    else:
        _res_a1 = "—"

    _a2r    = st.session_state.get("area_ef_result", {})
    _a2_pct = _a2r.get("pct_ef", None)
    _a2_ha  = _a2r.get("area_ef", None)
    _sem_a2 = ("verde" if _a2_pct is not None and _a2_pct >= 70 else
               "amarillo" if _a2_pct is not None and _a2_pct >= 40 else
               "rojo" if _a2_pct is not None else "gris")
    _res_a2 = f"{_a2_ha} ha ({_a2_pct:.0f}% del predio)" if _a2_pct is not None else "—"

    _apt_cat   = (_apt_res.get("category") if _apt_res and not _apt_res.get("error") else None)
    _apt_score = (_apt_res.get("score")    if _apt_res and not _apt_res.get("error") else None)
    _sem_b1 = ("verde" if _apt_cat == "Alta" else "amarillo" if _apt_cat == "Media"
               else "rojo" if _apt_cat in ("Baja","No apta") else "gris")
    _res_b1 = (f"{_apt_cat} (score {_apt_score:.2f})" if _apt_cat else "—")

    _b2_sum = st.session_state.get("b2_result")
    _sem_b2 = (_b2_sum["semaforo"] if _b2_sum else "gris")
    _b2_sem_map = {"verde":"verde","amarillo":"amarillo","rojo":"rojo"}
    _sem_b2 = _b2_sem_map.get(_sem_b2, "gris")
    _res_b2 = (f"{_b2_sum['pct_active']:.0f}% escenas activas · "
               f"{_b2_sum['years_with_peak']}/{_b2_sum['n_years']} años con pico"
               if _b2_sum else "—")

    _sem_c = _color_global          # computed in C section
    _res_c = _detalle_global

    _sem_d, _res_d = "gris", "—"
    if _clima_ok and _cultivo_tiene_matriz:
        try:
            _d_risk = _get_risk_cached(c_lat, c_lon, cultivo, mtime=_matrix_mtime())
            if not _d_risk.empty:
                _d_agg = aggregate_risk_score(_d_risk)
                if not np.isnan(_d_agg):
                    _d_lbl = score_to_label(_d_agg)
                    _d_col = score_to_color(_d_agg)
                    _sem_d = ("verde" if _d_col in ("verde","amarillo") else
                              "naranja" if _d_col == "naranja" else "rojo")
                    _res_d = f"{_d_lbl} (score P80: {_d_agg:.2f})"
        except Exception:
            pass

    # ── Render table ──────────────────────────────────────────────────
    _summary_rows = [
        ("A1", "Zona Agrícola · Frontera", "PostGIS / IGAC",          _sem_a1, _res_a1),
        ("A2", "Área Efectiva Cultivable",  "DEM · NDVI · Catastro",   _sem_a2, _res_a2),
        ("B1", "Aptitud al Cultivo",        "UPRA · datos.gov.co",     _sem_b1, _res_b1),
        ("B2", "Actividad Productiva NDVI", "EOSDA · Sentinel-2",      _sem_b2, _res_b2),
        ("C",  "Infraestructura / Acceso",  "OSM · OSRM",              _sem_c,  _res_c),
        ("D",  "Riesgo Agroclimático",      "ERA5 · Open-Meteo · P80", _sem_d,  _res_d),
    ]
    _tbl_rows = ""
    for _code, _name, _src, _sem, _res in _summary_rows:
        _bg  = _SEM_BG[_sem]; _bd = _SEM_BD[_sem]; _tx = _SEM_TX[_sem]
        _em  = _SEM_EM[_sem]; _ac = _SEM_ACT[_sem]
        _tbl_rows += (
            f'<tr style="background:{_bg}">'
            f'<td style="padding:6px 10px;font-weight:700;color:{_tx};white-space:nowrap">{_code}</td>'
            f'<td style="padding:6px 10px;font-weight:600;color:{_tx}">{_name}</td>'
            f'<td style="padding:6px 10px;color:#475569;font-size:0.80rem">{_src}</td>'
            f'<td style="padding:6px 10px;text-align:center;font-size:1.1rem">{_em}</td>'
            f'<td style="padding:6px 10px;color:{_tx};font-size:0.83rem">{_res}</td>'
            f'<td style="padding:6px 10px;color:{_tx};font-size:0.80rem;font-style:italic">{_ac}</td>'
            f'</tr>'
        )
    st.markdown(
        '<table style="width:100%;border-collapse:collapse;font-size:0.84rem">'
        '<thead><tr style="background:#f1f5f9;font-weight:600;text-align:left">'
        '<td style="padding:6px 10px">Bloque</td>'
        '<td style="padding:6px 10px">Indicador</td>'
        '<td style="padding:6px 10px">Fuente</td>'
        '<td style="padding:6px 10px;text-align:center">Score</td>'
        '<td style="padding:6px 10px">Resultado</td>'
        '<td style="padding:6px 10px">Acción recomendada</td>'
        f'</tr></thead><tbody>{_tbl_rows}</tbody></table>',
        unsafe_allow_html=True,
    )

    # ════════════════════════════════════════════════════════════════════
    #  PDF
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📄 Reporte Ex-Ante PDF")

    c_info, c_btn = st.columns([3,2])
    with c_info:
        cod_rpt = predio.get("codigo","predio")
        st.caption(
            f"📄 Incluye: ficha del predio · dictamen ejecutivo · resumen de indicadores · "
            f"detalle A (frontera + áreas) · B (aptitud + NDVI) · "
            f"D (riesgo agroclimático) · C (infraestructura) · "
            f"documentación requerida · firmas.\n\n"
            f"**Archivo:** `reporte_exante_{cod_rpt}.pdf`"
        )

    with c_btn:
        if st.button("🔄 Generar PDF ejecutivo", type="primary",
                     use_container_width=True, key="gen_pdf"):
            with st.spinner("Generando PDF..."):
                try:
                    # Collect risk data if available
                    _pdf_risk_df = None
                    _pdf_risk_score = None
                    _pdf_risk_label = "—"
                    if _clima_ok and _cultivo_tiene_matriz:
                        try:
                            _pdf_risk_df = _get_risk_cached(
                                c_lat, c_lon, cultivo, mtime=_matrix_mtime())
                            if not _pdf_risk_df.empty:
                                _pdf_risk_score = float(aggregate_risk_score(_pdf_risk_df))
                                _pdf_risk_label = score_to_label(_pdf_risk_score)
                        except Exception:
                            pass

                    _pdf_analisis = {
                        "a1_nivel":     st.session_state.get("a1_nivel", "gris"),
                        "gdf_frontera": st.session_state.get("gdf_frontera"),
                        "area_ef":      st.session_state.get("area_ef_result", {}).get("area_ef", 0),
                        "pct_ef":       st.session_state.get("area_ef_result", {}).get("pct_ef", 0),
                        "area_pend":    st.session_state.get("area_ef_result", {}).get("area_pend", 0),
                        "area_ndvi":    st.session_state.get("area_ef_result", {}).get("area_ndvi", 0),
                        "area_const":   st.session_state.get("area_ef_result", {}).get("area_const", 0),
                        "slope_thr":    st.session_state.get("area_ef_result", {}).get("slope_pct", 25),
                        "ndvi_thr":     st.session_state.get("area_ef_result", {}).get("ndvi_thr", 0.25),
                        "apt_result":   apt_result if "apt_result" in dir() else None,
                        "b2_result":    st.session_state.get("b2_result"),
                        "infra_centro": infra_centro,
                        "infra_via":    infra_via,
                        "infra_nivel":  _color_global,
                        "cultivo":      cultivo,
                        "municipio":    ubicacion_label,
                        "c_lat":        c_lat,
                        "c_lon":        c_lon,
                        "df_risk":      _pdf_risk_df,
                        "risk_score":   _pdf_risk_score,
                        "risk_label":   _pdf_risk_label,
                    }
                    pdf_bytes = generate_exante_report(
                        datos=d, predio=predio, analisis=_pdf_analisis,
                    )
                    st.session_state["pdf_bytes"] = pdf_bytes
                    st.session_state["pdf_name"]  = f"reporte_exante_{cod_rpt}.pdf"
                    st.success("✅ PDF listo.")
                except Exception as e:
                    import traceback
                    st.error(f"❌ Error generando PDF: {e}")
                    st.code(traceback.format_exc())

        if "pdf_bytes" in st.session_state:
            st.download_button(
                label="⬇️ Descargar PDF",
                data=st.session_state["pdf_bytes"],
                file_name=st.session_state["pdf_name"],
                mime="application/pdf",
                key="dl_pdf",
                use_container_width=True,
            )

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 2 · MONITOREO & FORECAST
# ══════════════════════════════════════════════════════════════════════════════
with tab_monitoreo:
    d = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    st.subheader("Monitoreo en Tiempo Real y Forecast")
    st.caption("Módulo activo durante el ciclo de vida del crédito.")

    if d.get("alerta_activa"):
        st.error(d.get("alerta_msg","⚠️ Alerta climática activa"))
    else:
        st.success("✅ Sin alertas climáticas activas en este momento.")

    c1,c2,c3 = st.columns(3)
    with c1:
        kpi("NDVI actual", round(d["ndvi_actual"],2))
        st.caption(f"Tendencia: {d['ndvi_tendencia']}")
    with c2: kpi("Precipitación forecast (hoy)", f"{d['forecast_precip_7d'][0]}", "mm")
    with c3: kpi("Temperatura forecast (hoy)",   f"{d['forecast_temp_7d'][0]}",   "°C")

    st.markdown("---")
    dias = [(date.today()+timedelta(days=i)).strftime("%d %b") for i in range(7)]
    c1,c2 = st.columns(2)
    with c1:
        fig_fp = px.bar(x=dias, y=d["forecast_precip_7d"],
                        title="Precipitación · Forecast 7 días (mm)",
                        labels={"x":"","y":"mm"},
                        color_discrete_sequence=["#3b82f6"])
        fig_fp.update_layout(height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_fp, use_container_width=True)
    with c2:
        fig_ft = px.line(x=dias, y=d["forecast_temp_7d"],
                         title="Temperatura · Forecast 7 días (°C)",
                         labels={"x":"","y":"°C"},
                         color_discrete_sequence=["#ef4444"])
        fig_ft.update_layout(height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_ft, use_container_width=True)

    st.markdown("---")
    st.markdown("### 📈 Evolución NDVI (últimos 12 meses)")
    MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    fig_nm = px.line(x=MESES, y=d["ndvi_mensual_hist"],
                     labels={"x":"Mes","y":"NDVI"},
                     color_discrete_sequence=["#16a34a"])
    fig_nm.add_scatter(x=[MESES[-1]], y=[d["ndvi_actual"]],
                       mode="markers", marker=dict(size=10,color="#dc2626"),
                       name="NDVI actual")
    fig_nm.update_layout(height=260, margin=dict(t=20,b=20))
    st.plotly_chart(fig_nm, use_container_width=True)

    st.info("**Próximas funcionalidades:** Alertas automáticas · "
            "Umbrales por fase fenológica · Reporte de monitoreo PDF.", icon="🔜")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 3 · METODOLOGÍA
# ══════════════════════════════════════════════════════════════════════════════
with tab_metodologia:
    _cult_m  = st.session_state.get("cultivo", "Café").lower().split("(")[0].strip()
    _b2_m    = st.session_state.get("b2_result")
    _thr_s   = f"{_b2_m['scene_threshold']:.2f}" if _b2_m else "0.35–0.50*"
    _thr_p   = f"{_b2_m['peak_threshold']:.2f}"  if _b2_m else "0.45–0.60*"
    _ndvi_th = st.session_state.get("ndvi_threshold", 0.25)

    st.subheader("📖 Metodología y Criterios de Evaluación")
    st.caption(
        "Fuentes de datos, hipótesis, umbrales y tablas de decisión para cada bloque de la validación. "
        "Los campos marcados con * varían según el cultivo seleccionado."
    )

    # ─── A · VALIDACIÓN GEOMÉTRICA Y LEGAL ───────────────────────────────────
    with st.expander("📐 A · Validación Geométrica y Legal", expanded=True):

        st.markdown("#### 🌿 A1 · Zona Agrícola — Frontera Agrícola Nacional")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
**Fuente de datos**
Base de Frontera Agrícola Nacional (UPRA / IGAC) almacenada en PostGIS.
La geometría del predio se intersecta con las capas de frontera para determinar
en qué tipo de zona se ubica el suelo.

**Hipótesis**
Un predio dentro de la frontera agrícola estricta tiene menor riesgo de
restricciones ambientales o legales que afecten la recuperación del crédito.
Zonas condicionadas o excluidas aumentan el riesgo de incumplimiento o expropiación.
""")
        with c2:
            st.markdown("""
**Tabla de decisión**

| Situación | Semáforo | Acción recomendada |
|-----------|----------|--------------------|
| Todo el predio en Frontera Agrícola **no condicionada** | 🟢 Verde | Sin restricción |
| Al menos una parte en Frontera Agrícola **condicionada** | 🟡 Amarillo | Verificar restricción específica (ambiental, étnico-cultural, riesgo de desastres) y exigir plan de manejo |
| Al menos una parte **fuera** de Frontera Agrícola | 🔴 Rojo | Zona de exclusión legal — no procede el crédito sin autorización ambiental expresa |

**Tipos de condición en la capa**

| Condición | Descripción |
|-----------|-------------|
| Frontera Agrícola no condicionada | Sin restricción legal |
| Ambiental | Restricción por ecosistema frágil o área de protección ambiental |
| Étnico-Cultural | Territorio colectivo, resguardo indígena o comunidad afro |
| Gestión riesgo de desastres | Zona con amenaza por inundación, deslizamiento u otro evento |
| Combinaciones (ej. Ambiental/Étnico-Cultural) | Coexistencia de múltiples restricciones |
| Fuera de frontera | No pertenece a ninguna categoría de Frontera Agrícola — exclusión legal total |

La lógica es conservadora: la presencia de **cualquier fracción** del predio fuera de frontera activa el semáforo rojo.
""")

        st.markdown("---")
        st.markdown("#### 📏 A2 · Área Efectiva Cultivable")
        st.markdown("""
El área efectiva es el área total del predio menos la superficie no cultivable por tres fuentes de exclusión.
Cuando A2-A (pendiente) y A2-C (NDVI) están calculados, se hace la **unión exacta píxel a píxel**,
evitando el doble conteo de zonas que coinciden en múltiples capas.
""")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""
**A2-A · Pendiente (DEM)**

- Fuente: EOSDA API — DEM SRTM 30 m
- Umbral configurable (default **25 %**)
- Se excluyen los píxeles con pendiente superior al umbral

| Pendiente | Clasificación |
|-----------|--------------|
| < umbral  | Cultivable   |
| ≥ umbral  | Excluida     |
""")
        with c2:
            st.markdown(f"""
**A2-C · NDVI histórico (Sentinel-2)**

- Fuente: Sentinel-2 L2A COG · Element84 Earth Search (sin API key)
- Período: últimos **3 años**; filtro nubosidad SCL < 20 % dentro del predio
- Estadístico: **P25 por píxel** — percentil 25 de todas las escenas válidas
- Umbral actual: P25 ≥ **{_ndvi_th:.2f}** → productivo

| NDVI P25 | Clasificación |
|----------|--------------|
| ≥ {_ndvi_th:.2f} | Productivo |
| < {_ndvi_th:.2f} | Excluida del área efectiva |
""")
        with c3:
            st.markdown("""
**A2-B · Construcciones (Catastro)**

- Fuente: IGAC · catastro nacional (PostGIS)
- Las construcciones registradas se excluyen del área productiva
- Se integran en la unión de máscaras para evitar solapamiento

**Nota**: si la base catastral no tiene construcciones registradas, el área construida se suma directamente como exclusión sin rasterizar.
""")
        st.markdown(f"""
**Semáforo de Área Efectiva**

| % Área efectiva / Total | Semáforo | Acción recomendada |
|-------------------------|----------|--------------------|
| ≥ 70 % | 🟢 Verde | Sin restricción |
| 40 – 70 % | 🟡 Amarillo | Revisar estructura de costos del proyecto; área disponible puede limitar el volumen de producción |
| < 40 % | 🔴 Rojo | Viabilidad productiva comprometida; solicitar plan de uso alternativo del suelo |
""")

    # ─── B · CONTINUIDAD PRODUCTIVA ───────────────────────────────────────────
    with st.expander("🌱 B · Continuidad Productiva", expanded=True):

        st.markdown("#### 🌾 B1 · Aptitud al Cultivo")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"""
**Fuente de datos**
API de zonificación de aptitud por cultivo de la UPRA (datos.gov.co).
Disponible para: café, cacao, aguacate, plátano, cebolla y otros cultivos priorizados.

**Metodología**
La intersección geométrica del predio con las zonas de aptitud genera un
**score ponderado por área**:

| Clase de aptitud | Peso |
|-----------------|------|
| Alta    | 1.00 |
| Media   | 0.67 |
| Baja    | 0.33 |
| No apta | 0.00 |

Score = Σ (área_clase × peso_clase) / área_total
""")
        with c2:
            st.markdown(f"""
**Hipótesis**
Un predio con alta aptitud agrológica para el cultivo declarado tiene
menor riesgo de pérdida de rendimiento por factores edáficos o climáticos
estructurales, lo que mejora la capacidad de repago del crédito.

**Tabla de decisión**

| Score ponderado | Categoría | Semáforo | Acción |
|----------------|-----------|----------|--------|
| ≥ 0.70 | Alta    | 🟢 Verde    | Sin restricción |
| 0.40 – 0.69 | Media | 🟡 Amarillo | Documentar plan de manejo agrícola |
| < 0.40 | Baja / No apta | 🔴 Rojo | Evaluar viabilidad técnica del proyecto |
""")

        st.markdown("---")
        st.markdown("#### 📊 B2 · Actividad Productiva (NDVI histórico)")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"""
**Fuente de datos**
Sentinel-2 L2A · EOSDA Field Analytics API.
Solo se usan escenas con nubosidad **< 20 % dentro del predio** (filtro AOI, no por tile completo).
Período: últimos **3 años**, 3 peticiones en paralelo de 1 año cada una.

**Hipótesis**
Un predio productivo activo debería mostrar de forma recurrente valores
de NDVI por encima del umbral fenológico del cultivo. La persistencia anual
del pico máximo confirma que ha habido al menos un ciclo productivo por año.

**Umbrales para *{_cult_m}*** *(calculados al ejecutar el análisis)*

| Parámetro | Umbral aplicado |
|-----------|----------------|
| NDVI mínimo por escena (actividad activa) | ≥ {_thr_s} |
| Pico NDVI anual (actividad estacional confirmada) | ≥ {_thr_p} |

*Los umbrales varían por cultivo: hoja densa (plátano, aguacate) → mayor umbral;
ciclo corto (papa, cebolla) → menor umbral.*
""")
        with c2:
            st.markdown(f"""
**Indicador 1 · % de escenas activas** (NDVI mediano ≥ {_thr_s})

De todas las escenas del período, ¿qué fracción supera el umbral mínimo?
Un valor alto indica vegetación activa recurrente.

| % escenas activas | Semáforo |
|-------------------|----------|
| ≥ 40 %    | 🟢 Verde    |
| 20 – 40 % | 🟡 Amarillo |
| < 20 %    | 🔴 Rojo     |

**Indicador 2 · Pico anual** (máximo NDVI del año ≥ {_thr_p})

¿En cuántos años del período se registró al menos un pico productivo?

| Años con pico | Semáforo |
|---------------|----------|
| Todos los años | 🟢 Verde    |
| Todos menos 1  | 🟡 Amarillo |
| < mitad de años | 🔴 Rojo   |

**Semáforo final B2** = peor de los dos indicadores.

| Color | Acción recomendada |
|-------|--------------------|
| 🟢 Verde    | Sin restricción adicional |
| 🟡 Amarillo | Solicitar documentación de soporte (facturas, registros ICA, certificados de cosecha) |
| 🔴 Rojo     | Inspección técnica presencial antes de aprobación del crédito |
""")

    # ─── C · INFRAESTRUCTURA ──────────────────────────────────────────────────
    with st.expander("🏗️ C · Infraestructura Productiva", expanded=True):

        st.markdown("""
**Hipótesis**
Un predio sin acceso vial o muy alejado de centros urbanos enfrenta mayores costos
de transporte y riesgo de inaccesibilidad en épocas de lluvias, lo que reduce
la rentabilidad y la capacidad de repago.
""")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
**C1 · Distancia al centro urbano más cercano**

- Fuente: OpenStreetMap (OSM) + OSRM routing engine
- Métrica: distancia por carretera (km) y tiempo estimado de conducción (min)
- Búsqueda en radio de 80 km

| Distancia | Semáforo | Acción recomendada |
|-----------|----------|--------------------|
| < 10 km   | 🟢 Verde    | Sin restricción |
| 10 – 25 km | 🟡 Amarillo | Verificar costos de transporte en estructura del proyecto |
| > 25 km   | 🔴 Rojo     | Riesgo logístico alto; verificar acceso a mercados y precios en finca |
""")
        with c2:
            st.markdown("""
**C2 · Distancia a vía transitable más cercana**

- Fuente: OpenStreetMap (OSM)
- Métrica: distancia en línea recta al punto más cercano en vía clasificada
- Búsqueda en radio de 5 km

| Distancia | Semáforo | Acción recomendada |
|-----------|----------|--------------------|
| < 500 m    | 🟢 Verde    | Sin restricción |
| 500 m – 2 km | 🟡 Amarillo | Verificar condición de la vía en temporada de lluvias |
| > 2 km     | 🔴 Rojo     | Riesgo de inaccesibilidad; costos del primer tramo pueden inviabilizar el negocio |

**Semáforo global C** = peor resultado entre C1 y C2.
""")

    # ─── D · RIESGO AGROCLIMÁTICO ─────────────────────────────────────────────
    with st.expander("🌧️ D · Riesgo Agroclimático", expanded=True):

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"""
**Fuente de datos**
ERA5 reanalysis vía Open-Meteo · **10 años** de historia diaria.
Variables: precipitación, temperatura máx/mín/media, humedad relativa, velocidad del viento.

**Indicadores por cultivo**
Los indicadores de riesgo son **específicos al cultivo** seleccionado como input.
Cada cultivo tiene su propia selección de indicadores, meses de cálculo y curvas de vulnerabilidad,
calibradas según su fenología y sus umbrales agronómicos de tolerancia.
El cultivo activo actualmente es: **{_cult_m.capitalize()}**.

**Metodología**
1. Se calculan indicadores de riesgo anualmente para cada año del período.
2. Para la evaluación crediticia se usa el **percentil 80 (P80)** de cada indicador —
   el escenario adverso que ocurre **1 de cada 5 años**.
3. El score por indicador se interpola linealmente entre los umbrales de la
   **curva de vulnerabilidad** (0 = sin riesgo, 1 = extremo).
4. El score global por categoría toma el **peor indicador** de esa categoría.
5. El score global D es la **media de los peores por categoría**.

**Hipótesis**
Usar el P80 captura el riesgo latente de años adversos que históricamente
han causado pérdidas de cultivo, sin sobredimensionar los años normales.
""")
        with c2:
            st.markdown("""
**Tabla de decisión global D**

| Score P80 | Nivel | Acción recomendada |
|-----------|-------|--------------------|
| 0.00 – 0.10 | 🟢 Sin riesgo | Sin restricción |
| 0.10 – 0.25 | 🟢 Bajo | Seguimiento anual estándar |
| 0.25 – 0.50 | 🟡 Medio | Cláusula de seguimiento trimestral |
| 0.50 – 0.75 | 🔴 Alto | Seguro agrícola obligatorio |
| 0.75 – 1.00 | 🚨 Extremo | Evaluar viabilidad del proyecto |

**Actualización de curvas**
Las curvas de vulnerabilidad están almacenadas en el fichero Excel:
`datos/indicadores/matriz_vulnerabilidad_consolidada.xlsx`

Para modificar umbrales o añadir cultivos: editar el Excel y actualizar el backend
(redeploy de la aplicación). Los cambios se aplican automáticamente en el
siguiente cálculo de riesgo para ese cultivo.
""")

        st.markdown("---")
        st.markdown("#### 📊 Indicadores por cultivo — curvas de vulnerabilidad")
        st.caption(
            "Tabla completa de indicadores de la matriz de vulnerabilidad. "
            "Filtra por cultivo para ver los indicadores activos y sus umbrales."
        )

        try:
            from pathlib import Path as _Path
            _mx_path = _Path(__file__).parent / "datos" / "indicadores" / "matriz_vulnerabilidad_consolidada.xlsx"
            _df_mx = pd.read_excel(_mx_path)
            _df_mx = _df_mx[_df_mx["Cultivo_app"].str.contains("sin equivalente") == False].copy()

            _mx_cols = [
                "Cultivo_app", "Categoría_riesgo", "Nombre_indicador",
                "Definición", "Meses_cálculo", "Unidad",
                "Sin_riesgo_0", "Riesgo_bajo_0.25", "Riesgo_medio_0.5",
                "Riesgo_alto_0.75", "Riesgo_extremo_1", "Forma_curva",
            ]
            _df_show = _df_mx[_mx_cols].rename(columns={
                "Cultivo_app":       "Cultivo",
                "Categoría_riesgo":  "Categoría",
                "Nombre_indicador":  "Indicador",
                "Definición":        "Definición",
                "Meses_cálculo":     "Meses",
                "Unidad":            "Unidad",
                "Sin_riesgo_0":      "Sin riesgo",
                "Riesgo_bajo_0.25":  "Bajo (0.25)",
                "Riesgo_medio_0.5":  "Medio (0.50)",
                "Riesgo_alto_0.75":  "Alto (0.75)",
                "Riesgo_extremo_1":  "Extremo (1.0)",
                "Forma_curva":       "Curva",
            })

            for _col in ["Sin riesgo", "Bajo (0.25)", "Medio (0.50)", "Alto (0.75)", "Extremo (1.0)"]:
                if _col in _df_show.columns:
                    _df_show[_col] = _df_show[_col].astype(str)
            _cultivos_mx = sorted(_df_show["Cultivo"].unique().tolist())
            _default_cult = (
                _cult_m.capitalize()
                if _cult_m.capitalize() in _cultivos_mx
                else _cultivos_mx[0]
            )
            _sel_cult = st.selectbox(
                "Filtrar por cultivo",
                options=["Todos"] + _cultivos_mx,
                index=_cultivos_mx.index(_default_cult) + 1
                if _default_cult in _cultivos_mx else 0,
                key="met_cult_filter",
            )
            _df_filt = (
                _df_show if _sel_cult == "Todos"
                else _df_show[_df_show["Cultivo"] == _sel_cult]
            )
            st.dataframe(
                _df_filt.reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Definición":  st.column_config.TextColumn(width="large"),
                    "Indicador":   st.column_config.TextColumn(width="medium"),
                    "Meses":       st.column_config.TextColumn(width="small"),
                    "Curva":       st.column_config.TextColumn(width="medium"),
                },
            )
            st.caption(
                f"📁 Fuente: `datos/indicadores/matriz_vulnerabilidad_consolidada.xlsx` · "
                f"{len(_df_filt)} indicadores mostrados · "
                "Para modificar umbrales o añadir cultivos, editar el Excel y actualizar el backend."
            )
        except Exception as _e_mx:
            st.warning(f"No se pudo cargar la matriz de vulnerabilidad: {_e_mx}")
