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
from folium.plugins import Fullscreen, Draw
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
from utils.gee_ndvi       import get_ndvi_gee, get_productivity_analysis_gee
from utils.risk_scoring   import (
    score_riesgo, INDICADORES, GRUPOS,
    SCORE_LABEL, SCORE_COLOR, SCORE_TEXT,
)
from utils.report_generator import generate_exante_report, generate_monitoring_report
from utils.monitoring_climate     import get_monitoring_series
from utils.monitoring_indicators  import (
    compute_all_indicators, HORIZONS,
    SEM_ICON, SEM_BG, SEM_BD, SEM_TEXT, SEM_ORDER,
)
import io
import json

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
    return get_productivity_analysis_gee(gdf, cultivo)

@st.cache_data(ttl=3600, show_spinner=False)
def _get_monitoring_cached(lat: float, lon: float):
    return get_monitoring_series(lat, lon, n_hist_years=5)

@st.cache_data(ttl=86400, show_spinner=False)
def _get_monitoring_ndvi_cached(geojson_str: str, _v: int = 4):
    """
    GEE: escenas individuales (último 1 año) para el gráfico + todas las escenas
    de los últimos 10 años descargadas en una sola llamada para calcular en Python
    la media y desv. estándar mensual por mes de calendario.
    Retorna {"scenes": [{date, median}],
             "hist_monthly": {1..12: {"mean": float, "std": float}}}.
    Completamente serializable por st.cache_data (sin mapas ni arrays).
    """
    import ee as _ee
    import numpy as _np
    from datetime import datetime as _dt, timedelta as _td
    from collections import defaultdict as _dd
    from utils.gee_ndvi import _init_gee, _mask_s2_clouds

    _init_gee()

    _roi   = _ee.Geometry(json.loads(geojson_str))
    _now   = _dt.utcnow()
    _d1    = _now.strftime("%Y-%m-%d")
    _d_1yr = (_now - _td(days=365)).strftime("%Y-%m-%d")
    _d_5yr = (_now - _td(days=365 * 10)).strftime("%Y-%m-%d")

    def _s2_col(d_start, d_end):
        return (
            _ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(_roi)
            .filterDate(d_start, d_end)
            .filter(_ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
            .map(_mask_s2_clouds)
            .map(lambda img: img.normalizedDifference(["B8", "B4"])
                                .rename("NDVI")
                                .copyProperties(img, ["system:time_start"]))
        )

    def _scene_feat(img):
        val = img.reduceRegion(_ee.Reducer.mean(), _roi, 10, maxPixels=1e8).get("NDVI")
        return _ee.Feature(None, {
            "date":  img.date().format("YYYY-MM-dd"),
            "month": img.date().get("month"),
            "ndvi":  val,
        })

    # ── 1. Escenas individuales — último 1 año (puntos del gráfico) ──────────
    _raw_1yr = _s2_col(_d_1yr, _d1).map(_scene_feat).getInfo()["features"]
    _scenes  = sorted(
        [{"date": f["properties"]["date"],
          "median": round(float(f["properties"]["ndvi"]), 4)}
         for f in _raw_1yr if f["properties"].get("ndvi") is not None],
        key=lambda x: x["date"],
    )

    # ── 2. Escenas 10 años → stats mensuales en Python (evita complejidad GEE) ─
    # ee.List.reduce() devuelve un dict, no un escalar → cálculo en Python.
    _raw_5yr = _s2_col(_d_5yr, _d1).map(_scene_feat).getInfo()["features"]
    _monthly_vals: dict = _dd(list)
    for _f in _raw_5yr:
        _pr = _f["properties"]
        _m, _v = _pr.get("month"), _pr.get("ndvi")
        if _m is not None and _v is not None:
            _monthly_vals[int(_m)].append(float(_v))

    _hist_monthly: dict = {}
    for _m, _vals in _monthly_vals.items():
        if _vals:
            _hist_monthly[_m] = {
                "mean": round(float(_np.mean(_vals)), 4),
                "std":  round(float(_np.std(_vals, ddof=1)) if len(_vals) > 1 else 0.0, 4),
            }

    return {"scenes": _scenes, "hist_monthly": _hist_monthly}


def _ndvi_indicators(ndvi_result: dict) -> dict:
    """Calcula A1 y A2 a partir del resultado de _get_monitoring_ndvi_cached."""
    scenes = ndvi_result.get("scenes", [])
    hist   = ndvi_result.get("hist_monthly", {})
    empty  = {"n_scenes": 0, "last_date": None, "last_ndvi": None,
               "prev_date": None, "prev_ndvi": None,
               "a1_pct": None, "a1_sem": "verde", "a2_val": None, "a2_sem": "verde"}
    if not scenes:
        return empty
    n    = len(scenes)
    last = scenes[-1]
    prev = scenes[-2] if n >= 2 else None
    last_ndvi  = last["median"]
    last_month = int(last["date"][5:7])
    _hentry    = hist.get(last_month, {})
    hist_m     = _hentry.get("mean") if isinstance(_hentry, dict) else _hentry

    a1_pct = ((last_ndvi - hist_m) / hist_m * 100) if (hist_m and hist_m > 0) else None
    a1_sem = ("verde" if a1_pct is None or a1_pct > -10
               else "amarillo" if a1_pct > -25 else "rojo")
    a2_val = round(last_ndvi - prev["median"], 4) if prev else None
    a2_sem = ("verde" if a2_val is None or a2_val >= -0.02
               else "amarillo" if a2_val >= -0.05 else "rojo")
    return {
        "n_scenes":  n,
        "last_date": last["date"],
        "last_ndvi": last_ndvi,
        "prev_date": prev["date"] if prev else None,
        "prev_ndvi": prev["median"] if prev else None,
        "a1_pct":    round(a1_pct, 1) if a1_pct is not None else None,
        "a1_sem":    a1_sem,
        "a2_val":    a2_val,
        "a2_sem":    a2_sem,
    }

@st.cache_data(ttl=3600, show_spinner=False)
def _get_predio_monitoring_cached(lat: float, lon: float):
    return get_predio_por_punto(lat, lon)

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

# Portafolio de demostración — 5 predios agrícolas en Cundinamarca
PORTFOLIO_DEFAULT = [
    {"nombre_predio": "Finca La Esperanza", "lat": 4.3368, "lon": -74.3639,
     "cultivo": "Café",           "fecha_desembolso": "2025-02-15"},
    {"nombre_predio": "Predio El Mirador",  "lat": 5.0163, "lon": -74.4733,
     "cultivo": "Plátano",        "fecha_desembolso": "2025-03-01"},
    {"nombre_predio": "Finca San Carlos",   "lat": 5.3153, "lon": -73.8226,
     "cultivo": "Papa",           "fecha_desembolso": "2025-01-20"},
    {"nombre_predio": "Predio Los Naranjos","lat": 4.5500, "lon": -74.5333,
     "cultivo": "Cacao",          "fecha_desembolso": "2025-04-10"},
    {"nombre_predio": "Hda. El Aguacate",   "lat": 4.6349, "lon": -74.4600,
     "cultivo": "Aguacate (Hass)","fecha_desembolso": "2024-12-05"},
]

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

def _polygonize_mask(mask, bounds_wgs84, src_crs="EPSG:4326"):
    """Poligoniza una máscara booleana → GeoDataFrame con la unión de las
    celdas True.

    `bounds_wgs84` (minx,miny,maxx,maxy) delimitan el array en WGS84.
    `src_crs` es el CRS real del grid de píxeles; las geometrías se devuelven
    en ese CRS (p.ej. el grid de terreno está en EPSG:3857, no en 4326)."""
    try:
        import rasterio.features
        import rasterio.warp
        from rasterio.transform import from_bounds
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union

        mask = np.asarray(mask, dtype=bool)
        if not mask.any() or bounds_wgs84 is None:
            return gpd.GeoDataFrame(geometry=[], crs=src_crs)

        h, w = mask.shape
        if str(src_crs).upper() != "EPSG:4326":
            minx, miny, maxx, maxy = rasterio.warp.transform_bounds(
                "EPSG:4326", src_crs, *bounds_wgs84)
        else:
            minx, miny, maxx, maxy = bounds_wgs84
        transform = from_bounds(minx, miny, maxx, maxy, w, h)

        geoms = [
            _shape(geo)
            for geo, val in rasterio.features.shapes(
                mask.astype(np.uint8), mask=mask, transform=transform)
            if val == 1
        ]
        if not geoms:
            return gpd.GeoDataFrame(geometry=[], crs=src_crs)
        return gpd.GeoDataFrame(geometry=[unary_union(geoms)], crs=src_crs)
    except Exception:
        return gpd.GeoDataFrame(geometry=[], crs=src_crs)


def _metric_crs(predio_gdf):
    """CRS métrico local (UTM) para áreas exactas. Determinista para un mismo
    predio, así todas las capas se proyectan al mismo sistema."""
    try:
        return predio_gdf.estimate_utm_crs()
    except Exception:
        return "EPSG:3857"


def _clip_geom_to_predio(gdf_src, predio_gdf, area_total, metric_crs=None):
    """Recorta una geometría (capa no cultivable) al polígono del predio y
    calcula su área DENTRO del predio.

    `gdf_src` puede venir de poligonizar un raster o ser vector (construcciones).
    Devuelve dict {geom_metric, metric_crs, gdf_4326, area_ha} o None si la
    intersección con el predio es vacía. El área se escala al área catastral
    mediante el ratio respecto al predio proyectado (la distorsión se cancela)."""
    from shapely.ops import unary_union
    if gdf_src is None or len(gdf_src) == 0:
        return None
    METRIC   = metric_crs or _metric_crs(predio_gdf)
    predio_g = predio_gdf.to_crs(METRIC).geometry.iloc[0]
    predio_a = predio_g.area
    try:
        geom = unary_union(gdf_src.to_crs(METRIC).geometry.values.tolist())
        geom = geom.intersection(predio_g)
    except Exception:
        return None
    if geom.is_empty:
        return None
    area_ha = area_total * (geom.area / predio_a) if predio_a > 0 else 0.0
    return {
        "geom_metric": geom,
        "metric_crs":  str(METRIC),
        "gdf_4326":    gpd.GeoDataFrame(geometry=[geom], crs=METRIC).to_crs("EPSG:4326"),
        "area_ha":     round(area_ha, 4),
    }


def _poly_from_mask(mask, bounds_wgs84, src_crs, predio_gdf, area_total, metric_crs=None):
    """Poligoniza una máscara raster y la recorta al predio (atajo combinado)."""
    return _clip_geom_to_predio(
        _polygonize_mask(mask, bounds_wgs84, src_crs=src_crs),
        predio_gdf, area_total, metric_crs=metric_crs,
    )


def _area_efectiva_from_polys(predio_gdf, area_total, polys):
    """Área efectiva = area_total − área(unión de capas no cultivables ya
    recortadas al predio). `polys` es un dict nombre→dict (de _clip_geom_to_predio).
    La unión evita el doble conteo de solapamientos entre A2A, A2B y A2C."""
    from shapely.ops import unary_union
    geoms = [p["geom_metric"] for p in polys.values() if p]
    if not geoms:
        area_no_cult = 0.0
    else:
        METRIC   = _metric_crs(predio_gdf)
        predio_a = predio_gdf.to_crs(METRIC).geometry.iloc[0].area
        union    = unary_union(geoms)
        area_no_cult = area_total * (union.area / predio_a) if predio_a > 0 else 0.0
    area_ef = round(max(area_total - area_no_cult, 0.0), 2)
    pct_ef  = round(area_ef / area_total * 100) if area_total > 0 else 0
    return {
        "area_no_cultivable": round(area_no_cult, 4),
        "area_ef":            area_ef,
        "pct_ef":             pct_ef,
    }

def _geojson_to_geom(gj):
    """Extrae una geometría (Polygon/MultiPolygon) de un GeoJSON que puede ser
    FeatureCollection, Feature o geometría. Devuelve shapely geom (EPSG:4326) o None."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    if not isinstance(gj, dict):
        return None
    try:
        t = gj.get("type")
        if t == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in gj.get("features", [])
                     if f.get("geometry")]
            return unary_union(geoms) if geoms else None
        if t == "Feature":
            return shape(gj["geometry"]) if gj.get("geometry") else None
        return shape(gj)   # objeto geometría directo
    except Exception:
        return None


def _build_predio_from_geom(geom):
    """Construye el dict de predio (mismo formato que get_predio_por_punto) a
    partir de una geometría arbitraria (dibujada o subida). Calcula el área e
    identifica si el CENTROIDE cae dentro de un predio catastral (existencia)."""
    from shapely.geometry import mapping
    if geom is None or geom.is_empty:
        return None
    gdf4 = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    try:
        area_ha = float(gdf4.to_crs(_metric_crs(gdf4)).geometry.iloc[0].area / 10_000)
    except Exception:
        area_ha = 0.0
    cen = geom.centroid
    cad = None
    try:
        cad = get_predio_por_punto(cen.y, cen.x)
    except Exception:
        cad = None
    en_cat = cad is not None
    codigo = cad["codigo"]               if en_cat else "Definido manualmente"
    dep    = cad.get("departamento","—") if en_cat else "—"
    mun    = cad.get("municipio","—")    if en_cat else "—"
    gdf = gpd.GeoDataFrame(
        [{"codigo": codigo, "departamento": dep, "municipio": mun,
          "area_ha": round(area_ha, 2)}],
        geometry=[geom], crs="EPSG:4326",
    )
    return {
        "codigo": codigo, "departamento": dep, "municipio": mun,
        "area_ha": round(area_ha, 2), "geojson": mapping(geom),
        "gdf": gdf, "en_catastro": en_cat,
        "centroide": (cen.y, cen.x),
    }


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

    # Nueva consulta
    _col_nc, _ = st.columns([1, 3])
    with _col_nc:
        if st.button("🔄 Nueva consulta", key="btn_nueva_consulta"):
            for _k in ["analizado","lat","lon","cultivo","datos","predio","terrain",
                       "ndvi_result","area_ef_result","area_ef_computed","b2_result",
                       "gdf_frontera","gdf_aptitud","gdf_construcciones",
                       "a1_nivel","a2_nivel","area_pendiente_excluida_ha",
                       "area_ndvi_bajo_ha","area_construcciones_ha",
                       "poly_pendiente","poly_ndvi","poly_const","ndvi_low_mask",
                       "auto_analysis_for","_drawn_geojson","_uploaded_geojson",
                       "existencia_texto"]:
                st.session_state.pop(_k, None)
            st.rerun()

    cultivo_in = st.selectbox("Tipo de cultivo", CULTIVOS_DISPONIBLES,
                              index=CULTIVOS_DISPONIBLES.index("Café"))

    _M_PUNTO = "📍 Cruce con catastro (punto)"
    _M_DRAW  = "✏️ Dibujar polígono en el mapa"
    _M_GEOJ  = "📤 Subir GeoJSON"
    _metodo = st.radio(
        "Método de definición del polígono del predio",
        [_M_PUNTO, _M_DRAW, _M_GEOJ],
        horizontal=True, key="predio_metodo",
    )

    _lat_in = _lon_in = None
    _puede_analizar = False

    if _metodo == _M_PUNTO:
        c1, c2 = st.columns(2)
        with c1: _lat_in = st.number_input("Latitud",  value=5.07013,  format="%.6f", key="in_lat_pt")
        with c2: _lon_in = st.number_input("Longitud", value=-73.55157, format="%.6f", key="in_lon_pt")
        st.caption("Se usará el polígono catastral que contiene el punto.")
        _puede_analizar = True

    elif _metodo == _M_DRAW:
        c1, c2 = st.columns(2)
        with c1: _lat_in = st.number_input("Latitud (centro del mapa)",  value=5.07013,  format="%.6f", key="in_lat_dr")
        with c2: _lon_in = st.number_input("Longitud (centro del mapa)", value=-73.55157, format="%.6f", key="in_lon_dr")
        st.caption("Centra el mapa con las coordenadas, dibuja el polígono haciendo "
                   "clic en sus vértices (herramienta de polígono ⬠) y pulsa Analizar.")
        _md = folium.Map(location=[_lat_in, _lon_in], zoom_start=16, tiles="Esri.WorldImagery")
        Fullscreen().add_to(_md)
        folium.Marker([_lat_in, _lon_in], tooltip="Centro",
                      icon=folium.Icon(color="red", icon="map-marker", prefix="fa")).add_to(_md)
        Draw(export=False,
             draw_options={"polyline": False, "circle": False, "circlemarker": False,
                           "marker": False, "rectangle": False, "polygon": True},
             edit_options={"edit": True, "remove": True}).add_to(_md)
        _out = st_folium(_md, width=750, height=480, key="draw_map",
                         returned_objects=["last_active_drawing"])
        if _out and _out.get("last_active_drawing"):
            st.session_state["_drawn_geojson"] = _out["last_active_drawing"].get("geometry")
        if st.session_state.get("_drawn_geojson"):
            st.success("✅ Polígono dibujado capturado.")
            _puede_analizar = True
        else:
            st.info("Dibuja un polígono en el mapa para habilitar el análisis.")

    else:  # Subir GeoJSON
        _up = st.file_uploader("Archivo GeoJSON del predio", type=["geojson", "json"],
                               key="geojson_upload")
        if _up is not None:
            try:
                import json as _j
                st.session_state["_uploaded_geojson"] = _j.load(_up)
            except Exception as _e:
                st.error(f"No se pudo leer el GeoJSON: {_e}")
                st.session_state.pop("_uploaded_geojson", None)
        _gj_up = st.session_state.get("_uploaded_geojson")
        if _gj_up is not None and _geojson_to_geom(_gj_up) is not None:
            st.success("✅ GeoJSON cargado.")
            _puede_analizar = True
        elif _gj_up is not None:
            st.error("El GeoJSON no contiene una geometría poligonal válida.")
        else:
            st.info("Sube un GeoJSON para habilitar el análisis.")

    if st.button("🔍 Analizar predio", type="primary", use_container_width=True,
                 disabled=not _puede_analizar):
        caso_m = ("Plátano · Urabá" if cultivo_in.lower() == "plátano"
                  else "Café · Eje Cafetero")
        _predio = None
        if _metodo == _M_PUNTO:
            with st.spinner("Consultando base catastral…"):
                _predio = get_predio_por_punto(_lat_in, _lon_in)
            if _predio is not None:
                _predio["en_catastro"] = True
            _cen_lat, _cen_lon = _lat_in, _lon_in
        elif _metodo == _M_DRAW:
            with st.spinner("Procesando polígono dibujado…"):
                _predio = _build_predio_from_geom(_geojson_to_geom(
                    st.session_state.get("_drawn_geojson")))
            _cen_lat, _cen_lon = (_predio["centroide"] if _predio else (_lat_in, _lon_in))
        else:
            with st.spinner("Procesando GeoJSON…"):
                _predio = _build_predio_from_geom(_geojson_to_geom(
                    st.session_state.get("_uploaded_geojson")))
            _cen_lat, _cen_lon = (_predio["centroide"] if _predio else (_lat_in, _lon_in))

        st.session_state["analizado"] = True
        st.session_state["predio"]    = _predio
        # Invalidar cualquier análisis previo: al (re)definir el predio en Inicio,
        # Validación Pre-Crédito debe recalcular sobre la última lat/lon definida.
        for _k_inv in ["terrain","ndvi_result","ndvi_low_mask","area_ef_result",
                       "area_ef_computed","b2_result","gdf_frontera","gdf_aptitud",
                       "gdf_construcciones","a2_nivel","area_pendiente_excluida_ha",
                       "area_ndvi_bajo_ha","area_construcciones_ha","poly_pendiente",
                       "poly_ndvi","poly_const","b3_nivel","b3_elev","b3_alt_min",
                       "b3_alt_max","auto_analysis_for"]:
            st.session_state.pop(_k_inv, None)
        if _predio is not None:
            _en_cat = _predio.get("en_catastro", False)
            _ex_txt = (("Polígono catastral identificado" if _metodo == _M_PUNTO
                        else "Centroide dentro de predio catastral") if _en_cat
                       else "Polígono definido manualmente · centroide fuera del catastro")
            st.session_state.update({
                "lat": _cen_lat, "lon": _cen_lon, "cultivo": cultivo_in,
                "datos": {**CASOS_ESTUDIO[caso_m], "lat": _cen_lat, "lon": _cen_lon},
                "a1_nivel": "verde" if _en_cat else "naranja",
                "existencia_texto": _ex_txt,
            })

    st.markdown("---")
    if not st.session_state.get("analizado"):
        st.info("Define el polígono del predio (punto · dibujo · GeoJSON) y pulsa **Analizar predio**.")
    else:
        lat     = st.session_state.get("lat")
        lon     = st.session_state.get("lon")
        cultivo = st.session_state.get("cultivo","café")
        predio  = st.session_state.get("predio")

        if predio is None:
            st.warning(
                "No se encontró ningún predio catastral que contenga el punto. "
                "Prueba a **dibujar el polígono** o **subir un GeoJSON**."
            )
        else:
            _en_cat = predio.get("en_catastro", True)
            st.markdown("#### 🗺️ Identificación del predio")
            c1,c2,c3,c4 = st.columns(4)
            with c1: st.metric("Código catastral", str(predio.get("codigo","—")))
            with c2: st.metric("Municipio",        predio.get("municipio","—"))
            with c3: st.metric("Área",             f"{predio.get('area_ha','—')} ha")
            with c4: st.metric("Cultivo",          cultivo.capitalize())

            # Existencia: ¿el punto/centroide cae dentro del catastro?
            _ex_txt = st.session_state.get("existencia_texto",
                                           "Polígono catastral identificado")
            try:
                _cen = predio["gdf"].geometry.iloc[0].centroid
                _cen_str = f"  ·  Coordenadas centroide: [{_cen.y:.5f}, {_cen.x:.5f}]"
            except Exception:
                _cen_str = ""
            if _en_cat:
                st.success(
                    f"✅ Existencia · {_ex_txt} (Depto: {predio.get('departamento','—')}).{_cen_str}"
                )
            else:
                st.warning(
                    f"⚠️ Existencia · {_ex_txt}.{_cen_str} El análisis se ejecutará sobre el "
                    "polígono definido, pero el predio no figura en el catastro."
                )

            st_folium(mapa_predio_simple(lat, lon, predio),
                      width=750, height=450, returned_objects=[])
            st.caption("🟢 Polígono del predio  ·  🔴 Punto / centroide")

            import json as _json, re as _re
            _safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", str(predio.get("codigo","predio")))
            st.download_button(
                label="⬇️ Descargar GeoJSON del predio",
                data=_json.dumps(predio["geojson"], ensure_ascii=False, indent=2),
                file_name=f"predio_{_safe}.geojson",
                mime="application/geo+json",
            )

            st.markdown("---")
            st.markdown("#### 👁️ Validación inicial de unidad productiva")
            st.caption("Confirmación visual por el asesor basada en imágenes satelitales / visita de campo.")
            _obs_opts = [
                "Se observa unidad productiva / cultivo / área agropecuaria aparente",
                "No se observa unidad productiva clara",
                "Requiere validación manual",
            ]
            _obs_sel = st.radio("Observación del asesor:", _obs_opts, key="obs_unidad_productiva", index=0)
            _obs_color = {"Se observa unidad productiva / cultivo / área agropecuaria aparente": "verde",
                          "No se observa unidad productiva clara": "rojo",
                          "Requiere validación manual": "naranja"}[_obs_sel]
            st.session_state["obs_unidad_productiva_nivel"] = _obs_color
            st.session_state["obs_unidad_productiva_texto"] = _obs_sel

            st.markdown("---")
            st.markdown("👉 Navega a **Validación Pre-Crédito** para el análisis detallado.")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 2 · MONITOREO DE PORTAFOLIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_monitoreo:
    st.subheader("Monitoreo de Portafolio · Fase de Cobranza")
    st.caption(
        "Indicadores climáticos y productivos en tiempo real por predio activo. "
        "Detecta señales de estrés antes de que se materialicen en mora."
    )

    # ── Gestión del portafolio ────────────────────────────────────────────────
    with st.expander("📂 Gestión del Portafolio", expanded=True):
        col_up, col_dl = st.columns([3, 1])
        with col_up:
            uploaded_port = st.file_uploader(
                "Cargar portafolio (Excel)",
                type=["xlsx"],
                help="Columnas requeridas: nombre_predio · latitud · longitud · cultivo",
                key="portfolio_upload",
            )
        with col_dl:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            _tmpl_buf = io.BytesIO()
            _tmpl_df = (
                pd.DataFrame(PORTFOLIO_DEFAULT)
                .rename(columns={"lat": "latitud", "lon": "longitud"})
                [["nombre_predio", "latitud", "longitud", "cultivo"]]
            )
            _tmpl_df.to_excel(_tmpl_buf, index=False)
            st.download_button(
                "📥 Descargar Template",
                data=_tmpl_buf.getvalue(),
                file_name="template_portafolio.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    # ── Carga del portafolio ──────────────────────────────────────────────────
    if uploaded_port is not None:
        try:
            _df_port = pd.read_excel(uploaded_port)
            _missing = {"nombre_predio", "latitud", "longitud", "cultivo"} - set(_df_port.columns)
            if _missing:
                st.error(f"Columnas faltantes: {', '.join(_missing)}")
                _portfolio = PORTFOLIO_DEFAULT
            else:
                # El template usa latitud/longitud; el resto del código usa lat/lon
                _df_port = _df_port.rename(columns={"latitud": "lat", "longitud": "lon"})
                _portfolio = _df_port.fillna("").to_dict("records")
                st.success(f"Portafolio cargado: {len(_portfolio)} predios")
        except Exception as _e:
            st.error(f"Error al leer el archivo: {_e}")
            _portfolio = PORTFOLIO_DEFAULT
    else:
        _portfolio = PORTFOLIO_DEFAULT

    # ── Botón de cálculo ──────────────────────────────────────────────────────
    _col_btn, _ = st.columns([1, 3])
    with _col_btn:
        _calc_btn = st.button(
            "📊 Calcular indicadores",
            type="primary",
            use_container_width=True,
            key="btn_calc_monitoring",
        )

    if _calc_btn:
        _results_map: dict = {}
        _total = len(_portfolio)
        _prog  = st.progress(0, text="Iniciando…")
        for _i, _p in enumerate(_portfolio):
            _nm = _p["nombre_predio"]
            _prog.progress(_i / (_total * 2),
                           text=f"[{_i+1}/{_total}] Clima · {_nm}…")
            try:
                _series = _get_monitoring_cached(float(_p["lat"]), float(_p["lon"]))
                _clima  = compute_all_indicators(
                    _series["combined_df"], str(_p["cultivo"]),
                    _series["ytd_clim"], _series["today"],
                    hist_df=_series["hist_df"],
                )
            except Exception as _e:
                _clima = {"error": str(_e)}

            _prog.progress((_i * 2 + 1) / (_total * 2),
                           text=f"[{_i+1}/{_total}] NDVI (GEE) · {_nm}…")
            _predio_d = _get_predio_monitoring_cached(float(_p["lat"]), float(_p["lon"]))
            if _predio_d and _predio_d.get("geojson"):
                _gjson_p = json.dumps(_predio_d["geojson"])
            else:
                _dlt = 0.0025
                _lt, _ln = float(_p["lat"]), float(_p["lon"])
                _gjson_p = json.dumps({"type": "Polygon", "coordinates": [[
                    [_ln - _dlt, _lt - _dlt], [_ln + _dlt, _lt - _dlt],
                    [_ln + _dlt, _lt + _dlt], [_ln - _dlt, _lt + _dlt],
                    [_ln - _dlt, _lt - _dlt],
                ]]})
            try:
                _ndvi_raw = _get_monitoring_ndvi_cached(_gjson_p)
                _ndvi     = _ndvi_indicators(_ndvi_raw)
                _ndvi["scenes"]       = _ndvi_raw.get("scenes", [])
                _ndvi["hist_monthly"] = _ndvi_raw.get("hist_monthly", {})
            except Exception as _e:
                _ndvi = {"error": str(_e)}

            _results_map[_nm] = {"clima": _clima, "ndvi": _ndvi}

        _prog.progress(1.0, text="✅ Listo.")
        st.session_state["mon_results"]   = _results_map
        st.session_state["mon_portfolio"] = _portfolio

    _results_map = st.session_state.get("mon_results", {})
    _portfolio   = st.session_state.get("mon_portfolio", _portfolio)

    # ── Tabla resumen del portafolio ──────────────────────────────────────────
    if _results_map:
        st.markdown("---")
        st.markdown("### 🗂️ Portafolio Activo · Resumen de Indicadores")

        # Nombres de fenómenos por indicador (para la tabla de resumen)
        _IND_NOMBRES = {
            "B1": "Déficit hídrico",
            "B2": "Sequía",
            "B3": "Lluvia excesiva",
            "C1": "Calor",
            "C2": "Frío/helada",
            "D1": "Enfermedad",
            "E1": "Viento",
        }

        def _veg_cell(ndv: dict) -> str:
            if "error" in ndv:
                return "❌ Error GEE"
            _a1s = ndv.get("a1_sem") or "verde"
            _a2s = ndv.get("a2_sem") or "verde"
            _a1p = ndv.get("a1_pct")
            _a2v = ndv.get("a2_val")
            _lv  = ndv.get("last_ndvi")
            _sem = max([_a1s, _a2s], key=lambda s: SEM_ORDER.get(s, 0))
            if not ndv.get("last_date"):
                return "— Sin escenas"
            if _sem == "verde":
                return SEM_ICON["verde"]
            _parts = []
            if _lv is not None and _a1p is not None:
                _parts.append(f"Valor NDVI: {_lv:.3f} ({_a1p:+.1f}%)")
            elif _a1p is not None:
                _parts.append(f"Valor NDVI: ({_a1p:+.1f}%)")
            if _a2v is not None:
                _parts.append(f"Tendencia NDVI: {_a2v:+.4f}")
            return f"{SEM_ICON[_sem]} " + (" · ".join(_parts) if _parts else "—")

        def _clima_cell(clim: dict, horizon: str) -> str:
            if "error" in clim:
                return "❌"
            _h   = clim.get(horizon, {})
            _g   = _h.get("global", "verde")
            _ico = SEM_ICON.get(_g, "⚪")
            if _g == "verde":
                return _ico
            _alerts = [
                _IND_NOMBRES.get(_iid, _iid)
                for _iid, _ind in sorted(_h.items())
                if _iid != "global" and _ind is not None
                and SEM_ORDER.get(_ind.get("semaforo", "verde"), 0) > 0
            ]
            return f"{_ico} {', '.join(_alerts)}" if _alerts else _ico

        def _global_level(clim: dict, ndv: dict) -> str:
            """Peor estado ACTUAL entre Vegetación (Hoy) y Clima (Hoy)."""
            _ns = (max([ndv.get("a1_sem","verde"), ndv.get("a2_sem","verde")],
                       key=lambda s: SEM_ORDER.get(s, 0))
                   if not ndv.get("error") and ndv.get("last_date") else "verde")
            _cs = clim.get("Hoy", {}).get("global", "verde") if not clim.get("error") else "verde"
            return max([_ns, _cs], key=lambda s: SEM_ORDER.get(s, 0))

        st.caption(
            "🟢 Sin alerta  ·  🟡 Precaución — contacto proactivo  ·  "
            "🔴 Alerta — intervención recomendada"
        )
        st.markdown(
            "<div style='font-size:0.82rem;color:#475569;margin:-4px 0 10px 0;line-height:1.4'>"
            "La <b>🎯 Alerta Global</b> es el <b>peor estado actual</b> entre "
            "<b>🌱 Vegetación (Hoy)</b> y <b>🌡️ Clima (Hoy)</b>. "
            "El <b>forecast</b> (+7 / +14 días) es anticipación temprana y "
            "<u>no altera</u> la alerta de hoy.</div>",
            unsafe_allow_html=True,
        )

        _GL_BG  = {"verde":"#dcfce7","amarillo":"#fef9c3","rojo":"#fee2e2","gris":"#f1f5f9"}
        _GL_TX  = {"verde":"#166534","amarillo":"#713f12","rojo":"#7f1d1d","gris":"#475569"}
        _GL_TXT = {"verde":"Normal","amarillo":"Precaución","rojo":"Alerta","gris":"—"}
        _ACT_BG = "#f8fafc"   # estado actual (importante)
        _FC_BG  = "#fbfbfd"   # forecast (atenuado)
        _BB     = "border-bottom:1px solid #e2e8f0"

        _body = ""
        for _p in _portfolio:
            _nm   = _p["nombre_predio"]
            _rec  = _results_map.get(_nm, {})
            _clim = _rec.get("clima", {})
            _ndv  = _rec.get("ndvi",  {})
            _gl   = _global_level(_clim, _ndv)
            _body += (
                "<tr>"
                f"<td style='padding:7px 10px;font-weight:600;{_BB}'>{_nm}</td>"
                f"<td style='padding:7px 10px;color:#475569;{_BB}'>{_p['cultivo']}</td>"
                f"<td style='padding:7px 10px;text-align:center;font-weight:700;{_BB};"
                f"background:{_GL_BG[_gl]};color:{_GL_TX[_gl]};"
                f"border-left:3px solid {_GL_TX[_gl]};border-right:3px solid {_GL_TX[_gl]}'>"
                f"{SEM_ICON.get(_gl,'⚪')} {_GL_TXT[_gl]}</td>"
                f"<td style='padding:7px 10px;background:{_ACT_BG};{_BB}'>{_veg_cell(_ndv)}</td>"
                f"<td style='padding:7px 10px;background:{_ACT_BG};{_BB}'>{_clima_cell(_clim,'Hoy')}</td>"
                f"<td style='padding:7px 10px;background:{_FC_BG};color:#64748b;font-size:0.82rem;{_BB}'>{_clima_cell(_clim,'+7 días')}</td>"
                f"<td style='padding:7px 10px;background:{_FC_BG};color:#64748b;font-size:0.82rem;{_BB}'>{_clima_cell(_clim,'+14 días')}</td>"
                "</tr>"
            )

        st.markdown(
            "<table style='width:100%;border-collapse:collapse;font-size:0.86rem'>"
            "<thead>"
            "<tr style='background:#1e293b;color:#fff'>"
            "<th rowspan='2' style='padding:6px 10px;text-align:center'>Predio</th>"
            "<th rowspan='2' style='padding:6px 10px;text-align:center'>Cultivo</th>"
            "<th rowspan='2' style='padding:6px 10px;text-align:center;"
            "border-left:3px solid #fff;border-right:3px solid #fff'>🎯 Alerta<br>Global</th>"
            "<th colspan='2' style='padding:5px 10px;text-align:center;background:#334155'>"
            "Estado actual · <span style='font-weight:400;font-size:0.8rem'>determina la alerta</span></th>"
            "<th colspan='2' style='padding:5px 10px;text-align:center;background:#64748b;color:#e2e8f0'>"
            "Forecast climático · <span style='font-weight:400;font-size:0.8rem'>anticipación</span></th>"
            "</tr>"
            "<tr style='background:#334155;color:#fff'>"
            "<th style='padding:5px 10px;text-align:center'>🌱 Vegetación (Hoy)</th>"
            "<th style='padding:5px 10px;text-align:center'>🌡️ Clima (Hoy)</th>"
            "<th style='padding:5px 10px;text-align:center;background:#64748b;color:#e2e8f0;font-weight:500'>Clima +7d</th>"
            "<th style='padding:5px 10px;text-align:center;background:#64748b;color:#e2e8f0;font-weight:500'>Clima +14d</th>"
            "</tr>"
            "</thead>"
            f"<tbody>{_body}</tbody></table>",
            unsafe_allow_html=True,
        )

        # ── Panel de detalle ──────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 🔍 Detalle por Predio")

        _sel_name = st.selectbox(
            "Seleccionar predio", [_p["nombre_predio"] for _p in _portfolio],
            key="mon_sel_predio",
        )
        _sel_p   = next(_p for _p in _portfolio if _p["nombre_predio"] == _sel_name)
        _sel_rec = _results_map.get(_sel_name, {})
        _sel_clim = _sel_rec.get("clima", {})
        _sel_ndv  = _sel_rec.get("ndvi",  {})

        # Ficha
        _dc1, _dc2, _dc3, _dc4 = st.columns(4)
        with _dc1: kpi("Cultivo",    _sel_p["cultivo"])
        with _dc2: kpi("Lat / Lon",  f"{float(_sel_p['lat']):.4f}, {float(_sel_p['lon']):.4f}")
        with _dc3: kpi("Desembolso", str(_sel_p.get("fecha_desembolso","—"))[:10])
        with _dc4: kpi("N img. (1a)", str(_sel_ndv.get("n_scenes","—")))

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        # Bloque A · NDVI ─────────────────────────────────────────────────────
        _A_ACT = {
            "A1": {"verde": "Sin acción.", "amarillo": "Llamada al agricultor; registrar en expediente.",
                   "rojo":  "Solicitar fotos + visita técnica; activar documentación seguro."},
            "A2": {"verde": "Sin acción.", "amarillo": "Monitorear próxima imagen Sentinel-2 con prioridad.",
                   "rojo":  "Si A1 también rojo: activar protocolo de alivio."},
        }
        with st.expander("🛰️ Bloque A · Vegetación NDVI (Sentinel-2 · GEE)", expanded=True):
            if "error" in _sel_ndv:
                st.error(f"Error GEE: {_sel_ndv['error']}")
            elif not _sel_ndv.get("last_date"):
                st.warning("Sin escenas válidas (nubes <40%) en el último año.")
            else:
                _ld   = _sel_ndv["last_date"]
                _lv   = _sel_ndv["last_ndvi"]
                _pd_  = _sel_ndv.get("prev_date")
                _pv   = _sel_ndv.get("prev_ndvi")
                _a1p  = _sel_ndv.get("a1_pct")
                _a1s  = _sel_ndv.get("a1_sem") or "verde"
                _a2v  = _sel_ndv.get("a2_val")
                _a2s  = _sel_ndv.get("a2_sem") or "verde"
                _lag  = (date.today() - date.fromisoformat(_ld)).days

                st.caption(
                    f"Última escena: **{_ld}** · rezago {_lag} días · "
                    f"penúltima: **{_pd_ or '—'}** · {_sel_ndv.get('n_scenes',0)} escenas válidas en 1 año"
                )

                _ca1, _ca2 = st.columns(2)
                for _acol, _aid, _albl, _adisp, _asem in [
                    (_ca1, "A1", "A1 · Anomalía NDVI vs. normal histórica mes",
                     (f"{_lv:.3f}  ({_a1p:+.1f}%)" if _a1p is not None else f"{_lv:.3f}  (sin normal)"),
                     _a1s),
                    (_ca2, "A2", "A2 · Tendencia (última vs. penúltima escena)",
                     (f"{_a2v:+.4f}" if _a2v is not None else "— (solo 1 escena)"),
                     _a2s),
                ]:
                    with _acol:
                        st.markdown(
                            f'<div style="background:{SEM_BG[_asem]};border-left:4px solid {SEM_BD[_asem]};'
                            f'border-radius:6px;padding:7px 10px;margin-bottom:7px">'
                            f'<div style="font-size:0.72rem;font-weight:600;color:{SEM_TEXT[_asem]}">'
                            f'{SEM_ICON[_asem]} {_albl}</div>'
                            f'<div style="font-size:1rem;font-weight:700;margin:2px 0 3px 0">{_adisp}</div>'
                            f'<div style="font-size:0.68rem;color:#6b7280">'
                            f'→ {_A_ACT[_aid][_asem]}</div></div>',
                            unsafe_allow_html=True,
                        )

                # Gráfico NDVI
                _scenes_plot = _sel_ndv.get("scenes", [])
                _hist_plot   = _sel_ndv.get("hist_monthly", {})

                if not _scenes_plot:
                    st.info("Sin datos de escenas para el gráfico. Vuelve a calcular el portafolio.")
                else:
                    _df_plot = pd.DataFrame(_scenes_plot)
                    _df_plot["date"] = pd.to_datetime(_df_plot["date"])

                    _fig = go.Figure()

                    # Banda ±1σ y media: una shape por mes natural del rango
                    for _ms in pd.date_range(
                        _df_plot["date"].min().to_period("M").to_timestamp(),
                        _df_plot["date"].max().to_period("M").to_timestamp(),
                        freq="MS",
                    ):
                        _he = _hist_plot.get(_ms.month)
                        if not isinstance(_he, dict) or _he.get("mean") is None:
                            continue
                        _mn = float(_he["mean"])
                        _sd = float(_he.get("std") or 0)
                        _me = _ms + pd.offsets.MonthEnd(1)
                        _fig.add_shape(
                            type="rect", layer="below",
                            x0=str(_ms.date()), x1=str(_me.date()),
                            y0=_mn - _sd, y1=_mn + _sd,
                            fillcolor="rgba(22,163,74,0.18)", line_width=0,
                        )
                        _fig.add_shape(
                            type="line", layer="below",
                            x0=str(_ms.date()), x1=str(_me.date()),
                            y0=_mn, y1=_mn,
                            line=dict(color="rgba(22,163,74,0.7)", dash="dash", width=1.5),
                        )

                    # Puntos y línea: escenas del último año
                    _fig.add_trace(go.Scatter(
                        x=_df_plot["date"],
                        y=_df_plot["median"],
                        mode="markers+lines",
                        marker=dict(size=8, color="#15803d"),
                        line=dict(color="#15803d", width=1.5),
                        name="NDVI escenas (último año)",
                        hovertemplate="%{x|%d %b %Y} — NDVI: %{y:.4f}<extra></extra>",
                    ))

                    _fig.update_layout(
                        height=300,
                        margin=dict(t=30, b=20, l=10, r=10),
                        xaxis_title="",
                        yaxis_title="NDVI",
                        showlegend=False,
                    )
                    st.plotly_chart(_fig, use_container_width=True)
                    st.caption(
                        f"Puntos: {len(_scenes_plot)} escenas Sentinel-2 último año (nubes <40%)  ·  "
                        f"Banda sombreada: ±1σ histórico 10 años por mes  ·  "
                        f"Línea discontinua: media histórica mensual"
                    )

        # Bloque B · Indicadores climáticos ──────────────────────────────────
        with st.expander("🌦️ Bloque B · Indicadores Climáticos (B1–E1)", expanded=True):
            if "error" in _sel_clim:
                st.error(f"Error indicadores climáticos: {_sel_clim['error']}")
            else:
                _hoy_g = _sel_clim.get("Hoy",     {}).get("global", "verde")
                _p7_g  = _sel_clim.get("+7 días", {}).get("global", "verde")
                _p14_g = _sel_clim.get("+14 días",{}).get("global", "verde")

                _bc1, _bc2, _bc3 = st.columns(3)
                for _bcol, _hl, _hg in [(_bc1,"Hoy",_hoy_g),(_bc2,"+7 días",_p7_g),(_bc3,"+14 días",_p14_g)]:
                    with _bcol:
                        st.markdown(
                            f'<div style="background:{SEM_BG[_hg]};border:2px solid {SEM_BD[_hg]};'
                            f'border-radius:8px;padding:6px 12px;text-align:center;margin-bottom:8px">'
                            f'<b style="color:{SEM_TEXT[_hg]}">{SEM_ICON[_hg]} {_hl}</b></div>',
                            unsafe_allow_html=True,
                        )

                _ic1, _ic2, _ic3 = st.columns(3)
                for _icol, _hl in [(_ic1,"Hoy"),(_ic2,"+7 días"),(_ic3,"+14 días")]:
                    _h_res = _sel_clim.get(_hl, {})
                    with _icol:
                        for _iid, _ind in _h_res.items():
                            if _iid == "global" or _ind is None:
                                continue
                            _s = _ind.get("semaforo", "verde")
                            st.markdown(
                                f'<div style="background:{SEM_BG[_s]};border-left:4px solid {SEM_BD[_s]};'
                                f'border-radius:6px;padding:7px 10px;margin-bottom:6px">'
                                f'<div style="font-size:0.72rem;font-weight:600;color:{SEM_TEXT[_s]}">'
                                f'{SEM_ICON[_s]} {_ind.get("label","")}</div>'
                                f'<div style="font-size:1rem;font-weight:700;margin:2px 0 2px 0">'
                                f'{_ind.get("display","")}</div>'
                                f'<div style="font-size:0.68rem;color:#6b7280">'
                                f'→ {_ind.get("action","")}</div></div>',
                                unsafe_allow_html=True,
                            )
        # ── Alerta Global del Predio (subsección dentro de Detalle por Predio) ─────
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(f"#### 🎯 Alerta Global · {_sel_name}")

        _ndv_sem_g = (
            max([_sel_ndv.get("a1_sem", "verde"), _sel_ndv.get("a2_sem", "verde")],
                key=lambda s: SEM_ORDER.get(s, 0))
            if not _sel_ndv.get("error") and _sel_ndv.get("last_date")
            else "verde"
        )
        _cli_sem_g = (
            _sel_clim.get("Hoy", {}).get("global", "verde")
            if not _sel_clim.get("error") else "verde"
        )
        _global_sem_g = max([_ndv_sem_g, _cli_sem_g], key=lambda s: SEM_ORDER.get(s, 0))

        _GLOBAL_LABEL  = {"verde": "Normal", "amarillo": "Precaución", "rojo": "Alerta Productiva"}
        _GLOBAL_ACTION = {
            "verde":    "Sin acción requerida. Mantener seguimiento periódico del portafolio.",
            "amarillo": "Contacto proactivo con el agricultor en los próximos 5 días hábiles. Registrar observación en el expediente de crédito.",
            "rojo":     "Intervención inmediata (< 48 h). Solicitar fotos de campo y evaluación técnica presencial. Evaluar opciones de reestructuración o alivio del crédito.",
        }
        st.markdown(
            f'<div style="background:{SEM_BG[_global_sem_g]};border-left:8px solid {SEM_BD[_global_sem_g]};'
            f'border-radius:8px;padding:1.1rem 1.4rem;margin:0.5rem 0 1rem 0">'
            f'<div style="font-size:1.3rem;font-weight:800;color:{SEM_TEXT[_global_sem_g]};margin-bottom:0.3rem">'
            f'{SEM_ICON[_global_sem_g]} Alerta Global · '
            f'<span style="font-size:1.5rem">{_GLOBAL_LABEL[_global_sem_g]}</span></div>'
            f'<div style="font-size:0.92rem;font-weight:600;color:{SEM_TEXT[_global_sem_g]};margin-bottom:0.4rem">'
            f'→ {_GLOBAL_ACTION[_global_sem_g]}</div>'
            f'<div style="font-size:0.75rem;color:{SEM_TEXT[_global_sem_g]};opacity:0.85">'
            f'Vegetación (A): {SEM_ICON[_ndv_sem_g]} · Clima — Hoy (B–E): {SEM_ICON[_cli_sem_g]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Reporte PDF de monitoreo ──────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 📄 Reporte de Monitoreo (PDF)")

        def _veg_lvl_text(ndv):
            if ndv.get("error") or not ndv.get("last_date"):
                return "gris", "Sin escenas NDVI"
            _lvl = max([ndv.get("a1_sem","verde"), ndv.get("a2_sem","verde")],
                       key=lambda s: SEM_ORDER.get(s, 0))
            _lv, _a1p = ndv.get("last_ndvi"), ndv.get("a1_pct")
            if _lvl == "verde":
                return _lvl, (f"NDVI {_lv:.2f} · normal" if _lv is not None else "Normal")
            if _lv is not None and _a1p is not None:
                return _lvl, f"NDVI {_lv:.2f} ({_a1p:+.0f}%)"
            return _lvl, (f"NDVI {_lv:.2f}" if _lv is not None else "Deterioro")

        def _clima_lvl_text(clim, hz):
            if clim.get("error"):
                return "gris", "Error"
            _h   = clim.get(hz, {})
            _lvl = _h.get("global", "verde")
            if _lvl == "verde":
                return _lvl, "Normal"
            _causas = [_IND_NOMBRES.get(_k, _k) for _k, _v in _h.items()
                       if _k != "global" and _v
                       and SEM_ORDER.get(_v.get("semaforo", "verde"), 0) > 0]
            return _lvl, (", ".join(_causas) if _causas else "Alerta")

        _IND_ORDER = ["B1", "B2", "B3", "C1", "C2", "D1", "E1"]
        _rep_predios = []
        for _rp in _portfolio:
            _nm   = _rp["nombre_predio"]
            _rec  = _results_map.get(_nm, {})
            _clim = _rec.get("clima", {})
            _ndv  = _rec.get("ndvi",  {})
            _h0, _h7, _h14 = (_clima_lvl_text(_clim, "Hoy"),
                              _clima_lvl_text(_clim, "+7 días"),
                              _clima_lvl_text(_clim, "+14 días"))
            _vl, _vt = _veg_lvl_text(_ndv)

            _acc = []
            if not _clim.get("error"):
                for _k in _IND_ORDER:
                    _ind = _clim.get("Hoy", {}).get(_k)
                    if _ind and SEM_ORDER.get(_ind.get("semaforo", "verde"), 0) > 0:
                        _acc.append(f"{_ind.get('label', _k)} — {_ind.get('action', '')}")
            if not _ndv.get("error") and _ndv.get("last_date"):
                if SEM_ORDER.get(_ndv.get("a1_sem", "verde"), 0) > 0:
                    _acc.append(f"NDVI (anomalía vs. histórico) — {_A_ACT['A1'][_ndv['a1_sem']]}")
                if SEM_ORDER.get(_ndv.get("a2_sem", "verde"), 0) > 0:
                    _acc.append(f"NDVI (tendencia a la baja) — {_A_ACT['A2'][_ndv['a2_sem']]}")

            _ndd = None
            if not _ndv.get("error") and _ndv.get("last_date"):
                _ndd = {
                    "ndvi":  round(_ndv["last_ndvi"], 3) if _ndv.get("last_ndvi") is not None else None,
                    "anom":  round(_ndv["a1_pct"], 1)    if _ndv.get("a1_pct")    is not None else None,
                    "tend":  round(_ndv["a2_val"], 4)    if _ndv.get("a2_val")    is not None else None,
                    "fecha": str(_ndv.get("last_date", ""))[:10],
                    "n":     _ndv.get("n_scenes"),
                }

            _ci = []
            if not _clim.get("error"):
                for _k in _IND_ORDER:
                    if not _clim.get("Hoy", {}).get(_k):
                        continue
                    _row = {"label": _clim["Hoy"][_k].get("label", _k)}
                    for _hz in ["Hoy", "+7 días", "+14 días"]:
                        _ih = _clim.get(_hz, {}).get(_k, {})
                        _row[_hz] = {"sem": _ih.get("semaforo"),
                                     "display": _ih.get("display", "—")}
                    _ci.append(_row)

            _rep_predios.append({
                "nombre": _nm, "cultivo": _rp.get("cultivo", "—"),
                "lat": _rp.get("lat"), "lon": _rp.get("lon"),
                "global": _global_level(_clim, _ndv),
                "veg": {"nivel": _vl, "text": _vt},
                "hoy": {"nivel": _h0[0], "text": _h0[1]},
                "f7":  {"nivel": _h7[0], "text": _h7[1]},
                "f14": {"nivel": _h14[0], "text": _h14[1]},
                "acciones": _acc,
                "ndvi_detalle": _ndd,
                "clima_indicadores": _ci,
            })

        _c1m, _c2m = st.columns([3, 2])
        with _c1m:
            st.caption("Pág. 1: resumen del portafolio · acciones requeridas · firmas. "
                       "Pág. 2+: detalle NDVI y clima por predio.")
        with _c2m:
            if st.button("🔄 Generar PDF de monitoreo", type="primary",
                         use_container_width=True, key="gen_pdf_mon"):
                with st.spinner("Generando PDF…"):
                    try:
                        from datetime import date as _date_mon
                        st.session_state["mon_pdf_bytes"] = generate_monitoring_report({
                            "fecha": _date_mon.today().strftime("%d/%m/%Y"),
                            "predios": _rep_predios,
                        })
                        st.success("✅ PDF listo.")
                    except Exception as _e:
                        import traceback
                        st.error(f"❌ Error generando PDF: {_e}")
                        st.code(traceback.format_exc())
            if "mon_pdf_bytes" in st.session_state:
                st.download_button(
                    "⬇️ Descargar PDF", data=st.session_state["mon_pdf_bytes"],
                    file_name="reporte_monitoreo_portafolio.pdf",
                    mime="application/pdf", key="dl_pdf_mon", use_container_width=True,
                )

    else:
        st.markdown("---")
        st.info("Pulsa **Calcular indicadores** para analizar el portafolio.", icon="👆")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB 3 · METODOLOGÍA
# ══════════════════════════════════════════════════════════════════════════════
with tab_metodologia:
    _cult_m  = st.session_state.get("cultivo", "Café").lower().split("(")[0].strip()
    _b2_m    = st.session_state.get("b2_result")
    _thr_s   = f"{_b2_m['scene_threshold']:.2f}" if _b2_m else "0.35–0.50*"
    _thr_p   = f"{_b2_m['peak_threshold']:.2f}"  if _b2_m else "0.45–0.60*"
    _ndvi_th = st.session_state.get("ndvi_threshold", 0.25)

    st.subheader("📖 Validación Pre-Crédito")
    st.caption(
        "Fuentes de datos, hipótesis, umbrales y tablas de decisión para cada bloque de la validación. "
        "Los campos marcados con * varían según el cultivo seleccionado."
    )

    # ─── A · VALIDACIÓN GEOMÉTRICA Y LEGAL ───────────────────────────────────
    with st.expander("📐 A · Validación Geométrica y Legal", expanded=True):

        st.markdown("#### 🏛️ A1 · Existencia del Predio")
        st.markdown("""
**Descripción**
Verifica que las coordenadas ingresadas correspondan a un polígono catastral registrado en la base IGAC almacenada en PostGIS.

| Resultado | Semáforo | Acción recomendada |
|-----------|----------|--------------------|
| Polígono catastral identificado con geometría validada | 🟢 Verde | Sin restricción — continuar análisis |
| Coordenadas fuera de cualquier predio catastral | 🔴 Rojo | Verificación manual con imágenes satelitales y fotos del solicitante |
""")

        st.markdown("---")
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

- Fuente: AWS Terrain Tiles (Terrarium) — DEM SRTM 30 m
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

- Fuente: Sentinel-2 SR Harmonized · **Google Earth Engine (GEE)**
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
Sentinel-2 SR Harmonized · **Google Earth Engine (GEE)**.
Solo se usan escenas con nubosidad **< 20 % dentro del predio** (filtro AOI, no por tile completo).
Período: últimos **3 años**; serie de mediana NDVI por escena calculada server-side en GEE.

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
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
**C1 · Centro urbano (carretera)**

- Fuente: OpenStreetMap (OSM) + OSRM routing engine
- Métrica: distancia por carretera (km) y tiempo de conducción (min)
- Búsqueda en radio de 80 km

| Distancia | Semáforo |
|-----------|----------|
| < 10 km   | 🟢 Verde    |
| 10 – 25 km | 🟡 Amarillo |
| > 25 km   | 🔴 Rojo     |
""")
        with c2:
            st.markdown("""
**C3 · Centro urbano (línea recta)**

- Fuente: OpenStreetMap (OSM)
- Métrica: distancia geodésica directa al centro urbano más cercano
- Aproxima el acceso al mercado independiente de la red vial

| Distancia | Semáforo |
|-----------|----------|
| < 5 km    | 🟢 Verde    |
| 5 – 15 km | 🟡 Amarillo |
| > 15 km   | 🔴 Rojo     |
""")
        with c3:
            st.markdown("""
**C2 · Vía transitable más cercana**

- Fuente: OpenStreetMap (OSM)
- Métrica: distancia en línea recta a la vía clasificada más cercana
- Búsqueda en radio de 5 km

| Distancia | Semáforo |
|-----------|----------|
| < 500 m    | 🟢 Verde    |
| 500 m – 2 km | 🟡 Amarillo |
| > 2 km     | 🔴 Rojo     |
""")
        st.markdown("**Semáforo global C** = peor resultado entre C1, C2 y C3.")

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
2. Para la evaluación crediticia se usa el percentil del **año adverso** (1 de
   cada 5 años) de cada indicador:
   - **P80** en indicadores de curva **creciente** (a mayor valor, mayor riesgo).
   - **P20** en indicadores de curva **decreciente** (a menor valor, mayor riesgo)
     — **excepción** de la categoría **'Lluvia - Necesidades hídricas'** (índice de
     sequía): la precipitación acumulada disminuye al aumentar el riesgo, por lo que
     el año adverso está en la cola baja de la serie.
3. El score por indicador se interpola linealmente entre los umbrales de la
   **curva de vulnerabilidad** (0 = sin riesgo, 1 = extremo).
4. El score global por categoría toma el **peor indicador** de esa categoría.
5. El score global D es la **media de los peores por categoría**.

**Hipótesis**
Usar el percentil del año adverso (P80 / P20 según la dirección de la curva) captura
el riesgo latente de años extremos que históricamente han causado pérdidas de cultivo,
sin sobredimensionar los años normales.
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

    # ─── SCORE GLOBAL CONSOLIDADO PRE-CRÉDITO ────────────────────────────────
    with st.expander("🎯 E · Score Global Consolidado · Validación Pre-Crédito", expanded=True):
        st.markdown("""
El score final consolida los resultados de todos los bloques en una única calificación de riesgo para la decisión de crédito.
""")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
**Metodología de agregación**

Cada bloque aporta un sub-score numérico en escala 1–4 (1 = sin restricción, 4 = riesgo crítico),
ponderado por su peso relativo en la decisión crediticia.
El score final es la **media ponderada** de los bloques calculados,
redondeada al entero más cercano.

Si un bloque no pudo calcularse (dato no disponible), su peso se redistribuye
proporcionalmente entre los bloques disponibles.

**Tabla de pesos**

| Bloque | Indicador | Peso |
|--------|-----------|------|
| Existencia del predio | PostGIS / IGAC | 15 % |
| A1 · Zona Agrícola (Frontera) | UPRA / IGAC | 15 % |
| A2 · Área Efectiva Cultivable | DEM · NDVI · Catastro | 10 % |
| B1 · Aptitud al Cultivo | UPRA · datos.gov.co | 15 % |
| B2 · Actividad Productiva NDVI | GEE · Sentinel-2 | 15 % |
| B3 · Altitud vs. Cultivo | DEM Terrarium | 0 % *(informativo)* |
| C · Infraestructura / Acceso | OSM · OSRM | 15 % |
| D · Riesgo Agroclimático | ERA5 · Open-Meteo · P80 | 15 % |
| **Total** | | **100 %** |
""")
        with c2:
            st.markdown("""
**Sub-scores por bloque**

| Bloque | Score 1 | Score 2 | Score 3 | Score 4 |
|--------|---------|---------|---------|---------|
| Existencia | Encontrado | — | — | No encontrado |
| Frontera A1 | 🟢 No condicionada | 🟡 Condicionada | — | 🔴 Fuera de frontera |
| Área A2 | ≥ 70 % efectiva | 40–70 % | — | < 40 % |
| Aptitud B1 | Score ≥ 0.70 | 0.40–0.69 | < 0.40 | — |
| NDVI B2 | ≥ 40 % escenas activas | 20–40 % | < 20 % | — |
| Infraestructura C | 🟢 Acceso adecuado | 🟡 Acceso medio | 🔴 Acceso bajo | — |
| Riesgo D | Score P80 < 0.25 | 0.25–0.50 | 0.50–0.75 | ≥ 0.75 |

**Tabla de decisión final**

| Score Final | Dictamen | Acción recomendada |
|-------------|----------|-------------------|
| **1** ✅ | Apto sin restricciones relevantes | Proceder con el crédito bajo condiciones estándar |
| **2** 🟡 | Apto con validaciones adicionales | Exigir documentación de soporte; seguimiento trimestral |
| **3** ⚠️ | Requiere revisión manual | Análisis técnico adicional antes de aprobación; posible visita al predio |
| **4** ⛔ | No recomendable bajo criterios actuales | Denegar o suspender; comunicar las razones específicas al solicitante |
""")

    # ═══════════════════════════════════════════════════════════════════════════
    # B · MONITOREO & FORECAST
    # ═══════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("📡 Monitoreo & Forecast")
    st.caption(
        "Metodología del sistema de monitoreo continuo de predios durante la vida del crédito. "
        "Detecta señales tempranas de deterioro productivo para activar acciones preventivas "
        "antes de que el agricultor entre en mora."
    )

    with st.expander("🏗️ Arquitectura · Fuentes de Datos y Horizontes Temporales", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
**Propósito**
Detectar en tiempo real si las condiciones agroclimáticas o de vegetación
de un predio en cartera se están deteriorando respecto a su comportamiento
histórico normal, anticipando posibles problemas de producción y repago.

**Fuentes de datos**

| Fuente | Variable | Cobertura | Latencia |
|--------|----------|-----------|----------|
| ERA5 Reanalysis · Open-Meteo | Precipitación, temperatura, humedad, viento | Global, 0.25° | 6 días |
| Open-Meteo Forecast API | Mismas variables + pronóstico | Global | Tiempo real |
| Sentinel-2 L2A · GEE | NDVI por polígono del predio | 10 m | 5–16 días (revisita) |

**Arquitectura de la serie climática**

Para cubrir el rezago de 6 días de ERA5 y añadir pronóstico a 14 días,
se usa la **Forecast API de Open-Meteo** con `past_days=30`:

```
ERA5 histórico (5 años) → climatología YTD y normal histórica mensual
ERA5 reciente (hasta t−6d) + Forecast (t−6d a t+14d) → serie combinada continua
```

La serie combinada es continua sin saltos y cubre todos los horizontes de análisis.
""")
        with c2:
            st.markdown("""
**Tres horizontes temporales**

Cada indicador climático se evalúa sobre tres ventanas:

| Horizonte | Datos usados | Interpretación |
|-----------|-------------|----------------|
| **Hoy** | ERA5 reciente + past_days | ¿Qué estrés ha sufrido el cultivo? |
| **+7 días** | ERA5 + pronóstico 7d | Si se cumple el pronóstico, ¿cómo estará? |
| **+14 días** | ERA5 + pronóstico 14d | ¿Y en 2 semanas? |

El horizonte retrospectivo (**Hoy**) es el más fiable (datos observados).
Los horizontes de pronóstico (+7d, +14d) tienen incertidumbre creciente
y se interpretan como señales de alerta temprana, no como certezas.

**NDVI: solo horizonte retrospectivo**
Los indicadores de vegetación (Bloque A) no tienen componente de pronóstico
porque no existe modelo de forecast satelital. Se calcula sobre la escena
más reciente disponible, independientemente de su fecha.
""")

    with st.expander("🛰️ A · Vegetación NDVI", expanded=True):
        st.markdown("""
**Fuente**: Sentinel-2 L2A armonizado · Google Earth Engine (GEE)
**Filtro de nubes**: escenas con cobertura nubosa < 40 % dentro del predio
**Historial**: 10 años de escenas para calcular la normal mensual histórica (media ± desviación estándar por mes de calendario)
""")
        st.markdown("""
| ID | Riesgo | Variable | Definición | 🟢 Verde | 🟡 Amarillo | 🔴 Rojo |
|----|--------|----------|------------|----------|-------------|---------|
| **A1** | Deterioro productivo | NDVI mediano por escena (0–1) | Anomalía de la escena más reciente respecto a la **media histórica del mismo mes de calendario** (10 años GEE) | > −10 % vs. media | −10 % a −25 % | < −25 % |
| **A2** | Deterioro productivo | NDVI mediano (0–1) | Variación del NDVI entre la escena más reciente y la escena inmediatamente anterior (Δ absoluto) | ≥ −0.02 | −0.02 a −0.05 | < −0.05 |
""")
        st.markdown("""
**Semáforo del Bloque A** = peor resultado entre A1 y A2.

**Notas operativas**
- Se muestra la fecha de la última escena y el rezago en días desde esa fecha.
- Si han transcurrido más de 45 días sin escena válida, el bloque se marca como "Sin dato fiable".
- La banda sombreada en el gráfico representa ± 1 desviación estándar histórica; la línea discontinua es la media mensual.

| Semáforo | Acción recomendada |
|----------|--------------------|
| 🟢 Verde | Sin acción. |
| 🟡 Amarillo | Llamada al agricultor; registrar en expediente. |
| 🔴 Rojo | Solicitar fotos de campo + visita técnica; activar documentación para seguro si corresponde. |
""")

    with st.expander("🌧️ B · Estrés Hídrico", expanded=True):
        st.markdown("""
**Fuente**: ERA5 + Open-Meteo Forecast API · Variable: precipitación diaria (mm)
""")
        st.markdown("""
| ID | Riesgo | Definición | 🟢 Verde | 🟡 Amarillo | 🔴 Rojo |
|----|--------|------------|----------|-------------|---------|
| **B1** | Déficit / exceso hídrico estacional | Precipitación acumulada desde el 1 de enero (YTD) vs. **normal histórica YTD** del mismo día calendario (5 años ERA5). Detecta si el año en curso está siendo anormalmente seco o húmedo. | 70–130 % de la normal | 40–70 % ó 130–200 % | < 40 % ó > 200 % |
| **B2** | Sequía aguda | Máximo número de **días consecutivos con precipitación < 1 mm** en ventana de 30 días. Umbral por cultivo. | < 50 % del umbral | 50–100 % del umbral | > umbral |
| **B3** | Exceso hídrico / encharcamiento | Máximo número de **días consecutivos con precipitación > 30 mm/día** en ventana de 30 días. | 0–1 días | 2–3 días | ≥ 4 días |
""")
        st.markdown("""
**Umbrales B2 por cultivo (días consecutivos secos)**

| Café | Cacao | Papa | Plátano | Aguacate | Maíz |
|------|-------|------|---------|----------|------|
| 45 d | 20 d | 15 d | 20 d | 30 d | 25 d |

| Semáforo | B1 | B2 | B3 |
|----------|----|----|----|
| 🟢 Verde | Sin acción | Sin acción | Sin acción |
| 🟡 Amarillo | Verificar estrés hídrico con agricultor; monitorear próxima quincena | Contacto preventivo; alertar sobre riesgo de sequía | Alerta de posible daño por exceso de humedad |
| 🔴 Rojo | Activar protocolo de alivio si hay pérdida verificable | Verificar disponibilidad de riego; evaluar extensión de plazo | Documentar evento para seguro; solicitar fotos de campo |
""")

    with st.expander("🌡️ C · Estrés Térmico", expanded=True):
        st.markdown("""
**Fuente**: ERA5 + Open-Meteo Forecast API · Variables: temperatura máxima y mínima diaria (°C)
""")
        st.markdown("""
| ID | Riesgo | Definición | 🟢 Verde | 🟡 Amarillo | 🔴 Rojo |
|----|--------|------------|----------|-------------|---------|
| **C1** | Estrés térmico por calor | **Media de temperatura máxima** en ventana de 14 días vs. umbral fisiológico por cultivo. Detecta estrés térmico acumulado sostenido. | < umbral cultivo | Umbral a umbral + 3 °C | > umbral + 3 °C |
| **C2** | Estrés térmico por frío | Número de días con **temperatura mínima por debajo del umbral de frío** del cultivo en ventana de 30 días. | 0 días | 1–2 días | ≥ 3 días |
""")
        st.markdown("""
**Umbrales por cultivo**

| Cultivo | Umbral calor C1 | Umbral frío C2 (Tmin) |
|---------|----------------|----------------------|
| Café | 32 °C | < 10 °C |
| Cacao | 35 °C | < 16 °C |
| Papa | 25 °C | < −2 °C |
| Plátano | 38 °C | < 12 °C |
| Aguacate | 35 °C | < 5 °C |
| Maíz | 35 °C | < 5 °C |

| Semáforo | C1 | C2 |
|----------|----|-----|
| 🟢 Verde | Sin acción | Sin acción |
| 🟡 Amarillo | Registro y seguimiento | Alertar sobre riesgo de frío o helada |
| 🔴 Rojo | Si NDVI también en alerta → escalar alerta global | Activar documentación seguro; proponer plan de pago diferido |
""")

    with st.expander("🦠 D · Riesgo Fitosanitario", expanded=True):
        st.markdown("""
**Fuente**: ERA5 + Open-Meteo Forecast API · Variables: temperatura media, humedad relativa, precipitación (según cultivo)

**Metodología**: En lugar de umbrales absolutos de días favorables — que en Colombia tropical
generan alertas permanentes porque las condiciones basales ya son favorables para hongos —
se calcula la **anomalía respecto a la normal histórica** del mismo período del año.

El semáforo se activa cuando el mes actual es *más favorable de lo habitual* para la enfermedad,
no simplemente cuando hay condiciones favorables.

**Cálculo de la normal**: media de días favorables para la enfermedad en la misma ventana de 30 días
del calendario, sobre 5 años de ERA5 histórico.
""")
        st.markdown("""
| Cultivo | Enfermedad | Condición favorable |
|---------|-----------|---------------------|
| **Café** | Roya (*Hemileia vastatrix*) | Tavg 22–27 °C **Y** rh > 80 % |
| **Cacao** | Moniliophthora *roreri* | rh > 90 % **Y** Tavg 22–26 °C |
| **Cacao** | *Phytophthora palmivora* | rh > 85 % **Y** Tavg 24–28 °C **Y** pr > 15 mm |
| **Papa** | Gota (*Phytophthora infestans*) | Tavg 10–20 °C **Y** pr > 0 mm |
| **Plátano** | Sigatoka negra | Tavg > 24 °C **Y** rh > 80 % |
| **Maíz** | *Aspergillus flavus* | Tmax 30–37 °C **Y** rh > 80 % *(solo abril–junio)* |

Para **Cacao**, el indicador registra días en que se cumplen las condiciones
de cualquiera de las dos enfermedades (lógica OR).

**Tabla de semáforo D1**

| Semáforo | Criterio (umbral absoluto) | Criterio (umbral relativo) | Acción recomendada |
|----------|----------------------------|----------------------------|--------------------|
| 🟢 Verde | ≤ normal + 3 días | ≤ 115 % de la media | Sin acción. |
| 🟡 Amarillo | normal + 3 a + 7 días | 115–140 % de la media | Informar al agricultor; recomendar revisión del cultivo. |
| 🔴 Rojo | > normal + 7 días | > 140 % de la media | Verificar pérdidas reportadas; activar protocolo de alivio si se documenta impacto. |

**Combinación de los dos umbrales — se toma el color MENOS alarmante (mínimo)**

El semáforo **sólo escala de color cuando se superan *ambos* umbrales a la vez** (el absoluto en días **Y** el relativo en %). Basta con que uno de los dos indique verde para que el resultado se quede en verde. Formalmente, el color final es el `mínimo` (menos severo) del color que daría cada criterio por separado:

```
color_final = min( color_absoluto , color_relativo )
```

**Por qué el doble umbral y por qué el mínimo** — ninguna de las dos métricas es fiable por sí sola, porque la normal histórica varía muchísimo entre cultivos y zonas de Colombia:

- **Sólo el % de la media** falla cuando la normal es *baja*. Ej.: normal = 2 días; un mes con 5 días es el **250 %** → 🔴 de pánico, pero son sólo 3 días extra, epidemiológicamente irrelevantes. El umbral absoluto (+3 / +7 días) veta esa falsa alarma.
- **Sólo los días absolutos** fallan cuando la normal es *alta*. Ej.: normal = 22 días; pasar a 27 son +5 días, pero es sólo **+23 %**, una fluctuación de fondo normal. El umbral porcentual (115 % / 140 %) veta esa falsa alarma.

**Ejemplo (Café · Roya, normal = 10 días):**

| Días este mes | Δ absoluto → color | % media → color | Semáforo final (mín.) |
|---------------|--------------------|-----------------|-----------------------|
| 12 | +2 → 🟢 | 120 % → 🟡 | **🟢** (el +2 real es ruido de fondo) |
| 18 | +8 → 🔴 | 180 % → 🔴 | **🔴** (ambos coinciden en alarma real) |

> **Nota de diseño**: esta regla prioriza *no generar ruido* sobre la detección precoz. El sistema es deliberadamente **específico, no sensible**: en normales muy bajas, un brote incipiente real puede quedar en verde.
""")

    with st.expander("💨 E · Viento (cultivos susceptibles)", expanded=True):
        st.markdown("""
**Fuente**: ERA5 + Open-Meteo Forecast API · Variable: ráfaga máxima diaria a 10 m (`wind_gusts_10m_max`, km/h)

El indicador solo se calcula para cultivos con riesgo estructural de daño mecánico por viento.
Los umbrales fueron validados mediante backtesting de eventos de daño (Plátano) o estimados
a partir de literatura agronómica (Aguacate, Maíz — pendientes de calibración con datos de siniestros).
""")
        st.markdown("""
| Cultivo | Umbral | 🟢 Verde | 🟡 Amarillo | 🔴 Rojo |
|---------|--------|----------|-------------|---------|
| Plátano | > 65 km/h | 0 días / 30d | 1–4 días | ≥ 5 días |
| Aguacate | > 54 km/h | 0 días / 30d | 1–4 días | ≥ 5 días |
| Maíz | > 43 km/h | 0 días / 30d | 1–4 días | ≥ 5 días |

| Semáforo | Acción recomendada |
|----------|--------------------|
| 🟢 Verde | Sin acción. |
| 🟡 Amarillo | Contacto preventivo con el agricultor. |
| 🔴 Rojo | Verificar daños físicos en el cultivo; activar documentación para seguro. |
""")

    with st.expander("🎯 F · Alerta Global · Monitoreo", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
**Metodología**

La alerta global del predio es el peor semáforo entre
los **dos componentes principales**:

1. **Componente de Vegetación (Bloque A)**: peor entre A1 y A2
2. **Componente Climático (Hoy)**: peor indicador entre B1, B2, B3, C1, C2, D1 y E1 en el horizonte actual

```
Alerta Global = max(Bloque A, Clima Hoy)
```

Esta lógica es conservadora: si cualquiera de los dos componentes
detecta deterioro, la alerta global se activa.

**Consolidación en el portafolio**

La tabla de portafolio muestra la alerta global de cada predio,
permitiendo priorizar la atención del equipo de gestión de cartera
sin necesidad de revisar el detalle de cada predio individualmente.
""")
        with c2:
            st.markdown("""
**Tabla de decisión global**

| Alerta | Criterio | Acción | Plazo |
|--------|----------|--------|-------|
| 🟢 **Normal** | Todos los indicadores en verde | Sin acción requerida. Mantener seguimiento periódico. | — |
| 🟡 **Precaución** | ≥ 1 indicador en amarillo, ninguno en rojo | Contacto proactivo con el agricultor. Registrar en expediente. | 5 días hábiles |
| 🔴 **Alerta Productiva** | ≥ 1 indicador climático o NDVI en rojo | Intervención inmediata. Solicitar fotos + visita técnica. Evaluar reestructuración del crédito. | < 48 horas |

**Alineación con el sistema Pre-Crédito**

Los indicadores de Monitoreo son metodológicamente consistentes
con los de Validación Pre-Crédito (Tab 1):
- Mismas variables climáticas (ERA5 / Open-Meteo)
- Mismas condiciones agronómicas de enfermedad (matriz de vulnerabilidad)
- La diferencia es el horizonte: Tab 1 evalúa riesgo estructural histórico (P80 anual),
  Tab 2 detecta deterioro en tiempo real (ventanas de 14–30 días y pronóstico).
""")

#  TAB 1 · VALIDACIÓN PRE-CRÉDITO
# ══════════════════════════════════════════════════════════════════════════════
with tab_validacion:
    predio  = st.session_state.get("predio")
    d       = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    cultivo = st.session_state.get("cultivo", d.get("cultivo","café"))

    if predio is None:
        st.info(
            "👆 Ingresa las coordenadas y el cultivo en la pestaña "
            "**🏠 Inicio · Ingreso del Predio** y pulsa **Analizar predio**."
        )
        st.stop()  # último bloque — st.stop() ya no bloquea otras pestañas

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
    #  AUTO-ANÁLISIS CON VALORES POR DEFECTO (una vez por predio)
    #  Al identificar un predio en Inicio y entrar aquí, se lanza el cálculo
    #  completo con los umbrales por defecto (A2-A pendiente 25%, A2-C NDVI
    #  0.25, A2 área efectiva, B2 actividad). El usuario puede luego ajustar
    #  los sliders y re-lanzar cada bloque con su botón para actualizar.
    # ════════════════════════════════════════════════════════════════════
    _pcode = predio.get("codigo", "")
    if st.session_state.get("auto_analysis_for") != _pcode:
        _gdf_auto = predio["gdf"]
        with st.spinner("Ejecutando análisis con valores por defecto "
                        "(terreno · NDVI · actividad productiva)…"):
            # A2-A · Terreno (umbral de pendiente 25 %)
            if not st.session_state.get("terrain"):
                try:
                    _t_auto = get_terrain_analysis(_gdf_auto, 25.0)
                    st.session_state["terrain"] = _t_auto
                    st.session_state["area_pendiente_excluida_ha"] = \
                        _t_auto["stats"]["area_no_cultivable_ha"]
                except Exception as _e_auto:
                    st.warning(f"⚠️ Terreno (A2-A) no se calculó automáticamente: {_e_auto}")
            # A2-C · NDVI histórico (umbral 0.25)
            if not st.session_state.get("ndvi_result"):
                try:
                    _r_auto = get_ndvi_gee(_gdf_auto, ndvi_threshold=0.25,
                                           n_years=3, max_cloud_pct=20.0)
                    st.session_state["ndvi_result"]   = _r_auto
                    st.session_state["ndvi_low_mask"] = _r_auto["low_ndvi_mask"]
                except Exception as _e_auto:
                    st.warning(f"⚠️ NDVI (A2-C) no se calculó automáticamente: {_e_auto}")
            # B2 · Actividad productiva (NDVI histórico)
            if not st.session_state.get("b2_result"):
                try:
                    import json as _json_auto
                    _geo_auto = _json_auto.dumps(
                        _gdf_auto.to_crs("EPSG:4326").geometry.iloc[0].__geo_interface__)
                    st.session_state["b2_result"] = _get_b2_cached(_geo_auto, cultivo)
                except Exception as _e_auto:
                    st.warning(f"⚠️ Actividad productiva (B2) no se calculó automáticamente: {_e_auto}")
            # A2 · Área efectiva → marcar calculada; los polígonos y el área se
            # computan al renderizarse los fragments con los resultados anteriores.
            st.session_state["area_ef_computed"] = True
        st.session_state["auto_analysis_for"] = _pcode
        st.rerun()

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
            st.session_state["a2_nivel"] = nivel
        else:
            st.warning("No se encontró información de frontera agrícola para este predio.")
            st.session_state["a2_nivel"] = "gris"


    # ── A2 · Área Efectiva Cultivable ─────────────────────────────────────────
    # Fragments defined at tab level — not nested inside any expander

    @st.fragment
    def _terrain_fragment():
        _pr = st.session_state.get("predio")
        if _pr is None:
            return
        st.markdown("#### 🏔️ A2-A · Análisis del Terreno (AWS Terrarium)")
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
            st.info("Pulsa **Calcular terreno** para descargar el DEM desde AWS Terrain Tiles (Terrarium).")
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

            # ── Polígono no cultivable (pendiente) recortado al predio ──────
            _at_p   = _pr.get("area_ha") or float(
                _pr["gdf"].to_crs(_metric_crs(_pr["gdf"])).geometry.iloc[0].area / 10_000)
            _poly_p = _poly_from_mask(
                _t["no_cultivable_mask"], _t.get("bounds_wgs84"),
                "EPSG:3857", _pr["gdf"], _at_p)
            st.session_state["poly_pendiente"] = _poly_p
            st.session_state["area_pendiente_excluida_ha"] = (
                _poly_p["area_ha"] if _poly_p else 0.0)

            st.markdown("---")
            st.markdown(f"**🔻 Polígono no cultivable (pendiente ≥ {_slope_thr}%) · recortado al predio**")
            _m_p = _base_map(_pr["gdf"]); _add_predio(_m_p, _pr["gdf"])
            if _poly_p is not None:
                folium.GeoJson(
                    data=_poly_p["gdf_4326"].to_json(),
                    style_function=lambda _: {"fillColor":"#dc2626","color":"#b91c1c",
                                               "weight":1.5,"fillOpacity":0.55},
                    name="Pendiente ≥ umbral",
                ).add_to(_m_p)
            _fit(_m_p, _pr["gdf"])
            st_folium(_m_p, width=700, height=360, returned_objects=[], key="map_pend_poly")
            kpi("Área no cultivable por pendiente (dentro del predio)",
                f"{(_poly_p['area_ha'] if _poly_p else 0.0):.4f}", "ha")
            st.caption(
                "Área obtenida poligonizando la máscara de pendiente y recortándola "
                "exactamente al polígono del predio — no cuenta fracciones de píxel "
                "que caen fuera del predio. Es el valor usado en el Área Efectiva (A2)."
            )


    @st.fragment
    def _ndvi_fragment():
        _pr = st.session_state.get("predio")
        if _pr is None:
            return
        st.markdown("#### 🛰️ A2-C · Análisis de Actividad Productiva (NDVI)")
        st.caption(
            "Sentinel-2 SR Harmonized · Google Earth Engine · P25 real por píxel · "
            "3 años de historia · Filtro de nubosidad por SCL dentro del predio"
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

            st.session_state["ndvi_low_mask"] = _res["low_ndvi_mask"]

            # ── Polígono no productivo (NDVI bajo) recortado al predio ──────
            _at_n   = _pr.get("area_ha") or float(
                _pr["gdf"].to_crs(_metric_crs(_pr["gdf"])).geometry.iloc[0].area / 10_000)
            _poly_n = _poly_from_mask(
                _res["low_ndvi_mask"], _res.get("bounds_wgs84"),
                "EPSG:4326", _pr["gdf"], _at_n)
            st.session_state["poly_ndvi"] = _poly_n
            st.session_state["area_ndvi_bajo_ha"] = (
                _poly_n["area_ha"] if _poly_n else 0.0)

            st.markdown("---")
            st.markdown(f"**🔻 Polígono no productivo (NDVI < {_ndvi_thr:.2f}) · recortado al predio**")
            _m_n = _base_map(_pr["gdf"]); _add_predio(_m_n, _pr["gdf"])
            if _poly_n is not None:
                folium.GeoJson(
                    data=_poly_n["gdf_4326"].to_json(),
                    style_function=lambda _: {"fillColor":"#eab308","color":"#ca8a04",
                                               "weight":1.5,"fillOpacity":0.55},
                    name="NDVI < umbral",
                ).add_to(_m_n)
            _fit(_m_n, _pr["gdf"])
            st_folium(_m_n, width=700, height=360, returned_objects=[], key="map_ndvi_poly")
            kpi("Área no productiva por NDVI (dentro del predio)",
                f"{(_poly_n['area_ha'] if _poly_n else 0.0):.4f}", "ha")
            st.caption(
                "Área obtenida poligonizando la máscara de NDVI bajo y recortándola "
                "exactamente al polígono del predio. Es el valor usado en el Área Efectiva (A2)."
            )


    @st.fragment
    def _area_ef_fragment():
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
        # Umbrales tomados del RESULTADO calculado (no del slider vivo) para que
        # la etiqueta coincida siempre con la máscara realmente usada.
        _slope_pct = (_terrain.get("slope_threshold")
                      if _terrain else st.session_state.get("slope_threshold", 25))
        _ndvi_thr  = (_ndvi_res.get("ndvi_threshold")
                      if _ndvi_res else st.session_state.get("ndvi_threshold", 0.25))

        # Polígonos no cultivables YA recortados al predio en A2-A/A2-B/A2-C.
        # Aquí solo los unimos (la unión evita el doble conteo de solapamientos).
        _poly_p = st.session_state.get("poly_pendiente")
        _poly_n = st.session_state.get("poly_ndvi")
        _poly_c = st.session_state.get("poly_const")

        _polys = {}
        if _poly_p: _polys["pendiente"]      = _poly_p
        if _poly_c: _polys["construcciones"] = _poly_c
        if _poly_n: _polys["ndvi"]           = _poly_n

        _ef          = _area_efectiva_from_polys(_pr["gdf"], area_total, _polys)
        area_no_cult = _ef["area_no_cultivable"]
        area_ef      = _ef["area_ef"]
        pct_ef       = _ef["pct_ef"]

        area_pend  = _poly_p["area_ha"] if _poly_p else 0.0
        area_ndvi  = _poly_n["area_ha"] if _poly_n else 0.0
        area_const = _poly_c["area_ha"] if _poly_c else 0.0

        # ── Map: predio + capas no cultivables (polígonos recortados) ──
        m_a1 = _base_map(_pr["gdf"])
        if ver_predio_a1: _add_predio(m_a1, _pr["gdf"])
        if ver_pendiente and _poly_p:
            folium.GeoJson(
                data=_poly_p["gdf_4326"].to_json(),
                style_function=lambda _: {"fillColor":"#dc2626","color":"#b91c1c",
                                           "weight":1.5,"fillOpacity":0.55},
                name=f"Pendiente ≥ {_slope_pct}%",
            ).add_to(m_a1)
        if ver_ndvi_bajo and _poly_n:
            folium.GeoJson(
                data=_poly_n["gdf_4326"].to_json(),
                style_function=lambda _: {"fillColor":"#eab308","color":"#ca8a04",
                                           "weight":1.5,"fillOpacity":0.55},
                name=f"NDVI < {_ndvi_thr:.2f}",
            ).add_to(m_a1)
        if ver_const_a1 and _poly_c:
            folium.GeoJson(
                data=_poly_c["gdf_4326"].to_json(),
                style_function=lambda _: {"fillColor":"#f97316","color":"#ea580c",
                                           "weight":1.5,"fillOpacity":0.70},
                name="Construcciones",
            ).add_to(m_a1)
        _fit(m_a1, _pr["gdf"])
        st_folium(m_a1, width=700, height=380, returned_objects=[], key="map_a1")

        # ── Table + gauge ─────────────────────────────────────────────
        c_left, c_right = st.columns([2, 1])
        with c_left:
            df_area = pd.DataFrame({
                "Componente": [
                    "Área total del predio",
                    "− Área no cultivable (unión de A2A + A2B + A2C)",
                    "✅ Área efectiva cultivable",
                ],
                "Hectáreas": [round(area_total, 4), -round(area_no_cult, 4), area_ef],
            })
            st.dataframe(
                df_area.style.apply(lambda x: [
                    "font-weight:bold;background:#d1fae5" if "✅" in str(v) else ""
                    for v in x], axis=1),
                use_container_width=True, hide_index=True,
            )
            _suma = round(area_pend + area_ndvi + area_const, 4)
            st.caption(
                "Áreas individuales recortadas al predio (informativas, pueden solaparse): "
                f"pendiente {area_pend:.4f} ha · construcciones {area_const:.4f} ha · "
                f"NDVI bajo {area_ndvi:.4f} ha. Suma simple = {_suma:.4f} ha; "
                f"**unión sin doble conteo = {area_no_cult:.4f} ha** (valor restado)."
            )
        with c_right:
            st.plotly_chart(gauge_riesgo(pct_ef, "% Área efectiva"), use_container_width=True)
            kpi("Área efectiva", area_ef, "ha")

        st.session_state["area_ef_result"] = {
            "area_ef":            area_ef,
            "pct_ef":             pct_ef,
            "area_no_cultivable": area_no_cult,
            "area_pend":          area_pend,
            "area_ndvi":          area_ndvi,
            "area_const":         area_const,
            "slope_pct":          _slope_pct,
            "ndvi_thr":           _ndvi_thr,
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

        # ── Construcciones recortadas al predio (para Área Efectiva A2) ──
        _at_c = predio.get("area_ha") or float(
            predio["gdf"].to_crs(_metric_crs(predio["gdf"])).geometry.iloc[0].area / 10_000)
        _poly_c = _clip_geom_to_predio(gdf_const, predio["gdf"], _at_c)
        st.session_state["poly_const"] = _poly_c
        st.session_state["area_construcciones_ha"] = (
            _poly_c["area_ha"] if _poly_c else 0.0)


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
            "Sentinel-2 vía Google Earth Engine · Últimos 3 años · "
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

        # B2 se calcula automáticamente al entrar a Validación Pre-Crédito.
        b2     = st.session_state.get("b2_result")
        b2_ok  = b2 is not None
        if not b2_ok:
            st.info("La serie NDVI de actividad productiva se calcula automáticamente "
                    "al abrir esta pestaña. Si no aparece, vuelve a **Inicio** y "
                    "relanza el análisis del predio.")

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

    with st.expander("🏔️ B3 · Altitud del predio vs. cultivo declarado", expanded=True):
        import os as _os_alt
        _ALT_XLSX = _os_alt.path.join(_os_alt.path.dirname(__file__), "datos", "indicadores", "rangos_altitud_cultivos.xlsx")
        try:
            _df_alt = pd.read_excel(_ALT_XLSX)
        except Exception as _e_alt:
            st.warning(f"No se pudo cargar la tabla de rangos de altitud: {_e_alt}")
            _df_alt = None

        _terrain_b3 = st.session_state.get("terrain")
        _elev_mean  = (_terrain_b3["stats"]["elev_mean"] if _terrain_b3 else None)

        if _df_alt is not None:
            _row_alt = _df_alt[_df_alt["Cultivo"].str.lower() == cultivo.lower()]
            if _row_alt.empty:
                _row_alt = _df_alt[_df_alt["Cultivo"].str.lower().str.contains(cultivo.lower().split()[0])]
            if not _row_alt.empty:
                _alt_min = int(_row_alt.iloc[0]["Altitud mínima (m)"])
                _alt_max = int(_row_alt.iloc[0]["Altitud máxima (m)"])
                _alt_desc = _row_alt.iloc[0]["Descripción / Justificación"]
                st.caption(f"Rango óptimo para **{cultivo}**: **{_alt_min} – {_alt_max} m.s.n.m.** · {_alt_desc}")

                if _elev_mean is not None:
                    if _alt_min <= _elev_mean <= _alt_max:
                        _b3_nivel = "verde"
                        _b3_msg   = (f"✅ Altitud media del predio ({_elev_mean:.0f} m) dentro del rango "
                                     f"óptimo para {cultivo} ({_alt_min}–{_alt_max} m).")
                    else:
                        _b3_nivel = "rojo"
                        _b3_msg   = (f"⚠️ Altitud media del predio ({_elev_mean:.0f} m) fuera del rango "
                                     f"óptimo para {cultivo} ({_alt_min}–{_alt_max} m). "
                                     f"Revisar capacidad productiva real del predio y solicitar "
                                     f"justificación agronómica al solicitante.")
                    semaforo(_b3_msg, _b3_nivel)
                    st.session_state["b3_nivel"] = _b3_nivel
                    st.session_state["b3_elev"]  = _elev_mean
                    st.session_state["b3_alt_min"] = _alt_min
                    st.session_state["b3_alt_max"] = _alt_max
                    # KPIs
                    _kb1, _kb2, _kb3 = st.columns(3)
                    with _kb1: kpi("Elevación media predio", f"{_elev_mean:.0f}", "m")
                    with _kb2: kpi("Altitud mínima cultivo", str(_alt_min), "m")
                    with _kb3: kpi("Altitud máxima cultivo", str(_alt_max), "m")
                else:
                    st.info("ℹ️ Calcula primero el **Análisis del Terreno (A2A)** para obtener la altitud del predio.")
                    st.session_state["b3_nivel"] = "gris"
            else:
                st.warning(f"No hay rango de altitud definido para el cultivo **{cultivo}** en la tabla de referencia.")
                st.session_state["b3_nivel"] = "gris"
        else:
            st.session_state["b3_nivel"] = "gris"

    # ════════════════════════════════════════════════════════════════════
    #  C · INFRAESTRUCTURA
    # ════════════════════════════════════════════════════════════════════
    with st.expander("🏗️ C · Validación de Infraestructura Productiva", expanded=False):

        st.caption(
            "Evalúa la conectividad física del predio con los mercados y la red vial. "
            "El semáforo global refleja el peor de los tres indicadores: distancia al "
            "centro urbano por carretera, distancia al centro urbano en línea recta y "
            "distancia a la vía transitable más cercana."
        )

        _centroid = predio["gdf"].geometry.iloc[0].centroid
        c_lat, c_lon = _centroid.y, _centroid.x

        with st.spinner("Calculando distancias de infraestructura vía OSM / OSRM …"):
            infra_centro = _get_distancia_centro_cached(c_lat, c_lon)
            infra_via    = _get_distancia_via_cached(c_lat, c_lon)

        # ── Semáforos por indicador y global (peor de los 3) ──────────
        _COLOR_RANK = {"verde": 0, "naranja": 1, "rojo": 2}
        _color_cu  = (
            "verde" if infra_centro and infra_centro["distancia_km"] < 10 else
            "naranja" if infra_centro and infra_centro["distancia_km"] < 25 else
            "rojo"
        )
        _dist_recta  = infra_centro["dist_recta_km"] if infra_centro else None
        _color_recta = (
            "verde" if _dist_recta is not None and _dist_recta < 5 else
            "naranja" if _dist_recta is not None and _dist_recta < 15 else
            "rojo"
        )
        _color_via = (
            "verde" if infra_via and infra_via["distancia_m"] < 500 else
            "naranja" if infra_via and infra_via["distancia_m"] < 2000 else
            "rojo"
        )
        _color_global = max([_color_cu, _color_recta, _color_via], key=lambda c: _COLOR_RANK[c])
        _label_global = {"verde": "Acceso adecuado", "naranja": "Acceso medio", "rojo": "Acceso bajo"}[_color_global]
        if _color_global == "verde":
            _detalle_global = "Los tres indicadores en rango adecuado."
        else:
            _limitantes = []
            if _COLOR_RANK[_color_cu] == _COLOR_RANK[_color_global] and infra_centro:
                _limitantes.append(f"centro urbano ({infra_centro['distancia_km']} km por carretera)")
            if _COLOR_RANK[_color_recta] == _COLOR_RANK[_color_global] and _dist_recta is not None:
                _limitantes.append(f"centro urbano ({_dist_recta} km en línea recta)")
            if _COLOR_RANK[_color_via] == _COLOR_RANK[_color_global] and infra_via:
                _limitantes.append(f"vía transitable ({infra_via['distancia_m']:.0f} m en línea recta)")
            _detalle_global = "Limitante: " + " · ".join(_limitantes) + "."
        semaforo(f"**{_label_global}** · {_detalle_global}", _color_global)

        st.markdown("---")
        c1, c2, c3 = st.columns(3)

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

        # ── C3 · Distancia en línea recta al centro urbano ────────────
        with c3:
            st.markdown("#### 📏 Centro urbano (línea recta)")
            if infra_centro is None or _dist_recta is None:
                st.warning("No se encontró centro urbano en un radio de 80 km.")
            else:
                kpi("Distancia en línea recta", _dist_recta, "km")
                st.caption(
                    f"**{infra_centro['nombre']}** ({infra_centro['tipo']}) · "
                    f"acceso directo a mercado"
                )
                semaforo(
                    f"{'< 5 km' if _dist_recta < 5 else '5–15 km' if _dist_recta < 15 else '> 15 km'} "
                    f"en línea recta ({_dist_recta} km).",
                    _color_recta,
                )

        # ── Leyenda de umbrales ───────────────────────────────────────
        st.markdown("""
<table style="width:100%;border-collapse:collapse;font-size:0.82rem;margin-top:0.8rem">
<thead><tr style="background:#f1f5f9;font-weight:600;text-align:center">
  <td style="padding:5px 10px">Condición</td>
  <td style="padding:5px 10px">Centro urbano<br>(carretera)</td>
  <td style="padding:5px 10px">Centro urbano<br>(línea recta)</td>
  <td style="padding:5px 10px">Vía transitable</td>
</tr></thead>
<tr style="background:#d1fae5;text-align:center">
  <td style="padding:6px 10px">🟢 Acceso adecuado</td>
  <td style="padding:6px 10px">&lt; 10 km</td>
  <td style="padding:6px 10px">&lt; 5 km</td>
  <td style="padding:6px 10px">&lt; 500 m</td>
</tr>
<tr style="background:#fef3c7;text-align:center">
  <td style="padding:6px 10px">🟡 Acceso medio</td>
  <td style="padding:6px 10px">10 – 25 km</td>
  <td style="padding:6px 10px">5 – 15 km</td>
  <td style="padding:6px 10px">500 m – 2 km</td>
</tr>
<tr style="background:#fee2e2;text-align:center">
  <td style="padding:6px 10px">🔴 Acceso bajo</td>
  <td style="padding:6px 10px">&gt; 25 km</td>
  <td style="padding:6px 10px">&gt; 15 km</td>
  <td style="padding:6px 10px">&gt; 2 km</td>
</tr>
</table>
<p style="font-size:0.75rem;color:#64748b;margin-top:4px">
  El semáforo global toma el peor de los tres indicadores.
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
        st.info(
            "ℹ️ **Excepción · indicadores de curva decreciente** (categoría "
            "**'Lluvia - Necesidades hídricas'**, índice de sequía): la precipitación "
            "acumulada **disminuye** a medida que aumenta el riesgo, por lo que el año "
            "adverso está en la cola baja de la serie. Para estos indicadores se usa el "
            "**percentil 20 (P20)** en lugar del P80. En la tabla se marcan con `P20` "
            "junto al valor.",
            icon="💧",
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
                        _pct_ref = int(r.get("percentil_ref", 80))
                        _pmark   = ('<span style="color:#0369a1;font-size:0.7rem"> P20</span>'
                                    if _pct_ref == 20 else "")
                        rows_html += (
                            f'<tr style="background:{bg}">'
                            f'<td style="padding:5px 8px;color:{fg};font-weight:600;white-space:nowrap">'
                            f'{r["riesgo_label"]}</td>'
                            f'<td style="padding:5px 8px">{r["Nombre_indicador"]}</td>'
                            f'<td style="padding:5px 8px;text-align:right;white-space:nowrap">{_fmt(r["valor_medio"])}</td>'
                            f'<td style="padding:5px 8px;text-align:right;white-space:nowrap">{_fmt(r["valor_p80"])}{_pmark}</td>'
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
                    "El score del año adverso es el usado para la clasificación "
                    "(P80 en curvas crecientes; P20 en curvas decrecientes, marcadas con `P20`)."
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

    # A1: Existencia del predio — refleja si el punto/centroide cae en el catastro
    _sem_a1 = st.session_state.get("a1_nivel", "verde")
    _res_a1 = st.session_state.get(
        "existencia_texto",
        "Polígono catastral identificado" if _sem_a1 == "verde"
        else "Centroide fuera del catastro · polígono definido manualmente",
    )

    # A2: Frontera agrícola
    _sem_a2 = st.session_state.get("a2_nivel", "gris")
    if _gdf_front is not None and len(_gdf_front) > 0:
        _tipos_a2   = _gdf_front["tipo_condi"].unique().tolist()
        _pct_tot_a2 = float(_gdf_front["pct_predio"].sum())
        _pct_out_a2 = round(max(0.0, 100.0 - _pct_tot_a2), 1)
        _tipos_cond_a2 = [t for t in _tipos_a2 if t != "Frontera Agrícola no condicionada"]
        if _pct_out_a2 > 2:
            _res_a2 = f"{_pct_out_a2:.1f}% fuera de Frontera Agrícola"
        elif _tipos_cond_a2:
            _res_a2 = f"Condicionada: {', '.join(_tipos_cond_a2)}"
        else:
            _res_a2 = "Todo en Frontera Agrícola no condicionada"
    else:
        _res_a2 = "Sin información de frontera agrícola para este predio"

    # A3: Área Efectiva Cultivable
    _a3r    = st.session_state.get("area_ef_result", {})
    _a3_pct = _a3r.get("pct_ef", None)
    _a3_ha  = _a3r.get("area_ef", None)
    _sem_a3 = ("verde" if _a3_pct is not None and _a3_pct >= 70 else
               "amarillo" if _a3_pct is not None and _a3_pct >= 40 else
               "rojo" if _a3_pct is not None else "gris")
    _res_a3 = f"{_a3_ha} ha ({_a3_pct:.0f}% del predio)" if _a3_pct is not None else "—"

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
    _d_agg_val = None
    if _clima_ok and _cultivo_tiene_matriz:
        try:
            _d_risk = _get_risk_cached(c_lat, c_lon, cultivo, mtime=_matrix_mtime())
            if not _d_risk.empty:
                _d_agg = aggregate_risk_score(_d_risk)
                if not np.isnan(_d_agg):
                    _d_agg_val = float(_d_agg)
                    _d_lbl = score_to_label(_d_agg)
                    _d_col = score_to_color(_d_agg)
                    _sem_d = ("verde" if _d_col in ("verde","amarillo") else
                              "naranja" if _d_col == "naranja" else "rojo")
                    _res_d = f"{_d_lbl} (score P80: {_d_agg:.2f})"
        except Exception:
            pass

    # ── Render table ──────────────────────────────────────────────────
    _sem_b3 = st.session_state.get("b3_nivel", "gris")
    _b3_elev    = st.session_state.get("b3_elev")
    _b3_alt_min = st.session_state.get("b3_alt_min")
    _b3_alt_max = st.session_state.get("b3_alt_max")
    _res_b3 = (f"{_b3_elev:.0f} m (rango {_b3_alt_min}–{_b3_alt_max} m)"
               if _b3_elev is not None else "—")

    _summary_rows = [
        ("",   "Existencia del Predio",       "PostGIS / IGAC",          _sem_a1, _res_a1),
        ("A1", "Zona Agrícola · Frontera",    "PostGIS / IGAC",          _sem_a2, _res_a2),
        ("A2", "Área Efectiva Cultivable",    "DEM · NDVI · Catastro",   _sem_a3, _res_a3),
        ("B1", "Aptitud al Cultivo",          "UPRA · datos.gov.co",     _sem_b1, _res_b1),
        ("B2", "Actividad Productiva NDVI",   "GEE · Sentinel-2",        _sem_b2, _res_b2),
        ("B3", "Altitud vs. Cultivo",         "DEM Terrarium · Ref. UPRA", _sem_b3, _res_b3),
        ("C",  "Infraestructura / Acceso",    "OSM · OSRM",              _sem_c,  _res_c),
        ("D",  "Riesgo Agroclimático",        "ERA5 · Open-Meteo · P80", _sem_d,  _res_d),
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
    #  SCORE AGREGADO FINAL
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🎯 Score Final Consolidado")

    # ── Sub-puntuaciones por indicador ───────────────────────────────
    def _sub_frontera(sem):
        return {"verde": 1, "amarillo": 2, "naranja": 2, "rojo": 4}.get(sem, None)

    def _sub_area(pct):
        if pct is None: return None
        return 1 if pct >= 70 else 2 if pct >= 40 else 4

    def _sub_aptitud(score_apt):
        if score_apt is None: return None
        return 1 if score_apt >= 0.70 else 2 if score_apt >= 0.40 else 3

    def _sub_ndvi(b2):
        if b2 is None: return None
        pct_a = b2.get("pct_active", 0)
        # Alineado con el semáforo B2 y la metodología: ≥40 verde · 20–40 amarillo · <20 rojo
        return 1 if pct_a >= 40 else 2 if pct_a >= 20 else 3

    def _sub_infra(nivel_global):
        # nivel_global = peor de C1, C2 y C3 (calculado en la sección C)
        return {"verde": 1, "naranja": 2, "rojo": 3}.get(nivel_global, None)

    def _sub_d(agg):
        if agg is None: return None
        return 1 if agg < 0.25 else 2 if agg < 0.50 else 3 if agg < 0.75 else 4

    def _sub_b3(sem):
        return {"verde": 1, "rojo": 4}.get(sem, None)

    _SCORES_CALC = {
        "exist":  (0.15, 1),
        "front":  (0.15, _sub_frontera(_sem_a2)),
        "area":   (0.10, _sub_area(_a3_pct)),
        "apt":    (0.15, _sub_aptitud(_apt_score if _apt_res and not _apt_res.get("error") else None)),
        "ndvi":   (0.15, _sub_ndvi(_b2_sum)),
        "b3":     (0.00, _sub_b3(_sem_b3)),   # B3 informativo, no entra en pesos actuales
        "infra":  (0.15, _sub_infra(_color_global if "_color_global" in dir() else "gris")),
        "d":      (0.15, _sub_d(_d_agg_val)),
    }

    _peso_total  = sum(p for p, v in _SCORES_CALC.values() if v is not None)
    _score_sum   = sum(p * v for p, v in _SCORES_CALC.values() if v is not None)
    _score_final = round(_score_sum / _peso_total) if _peso_total > 0 else None

    _DECISION_MAP = {
        1: ("Apto sin restricciones relevantes",   "verde",   "✅"),
        2: ("Apto con validaciones adicionales",    "naranja", "🟡"),
        3: ("Requiere revisión manual",             "rojo",    "⚠️"),
        4: ("No recomendable bajo criterios actuales", "rojo", "⛔"),
    }

    if _score_final is not None and _score_final in _DECISION_MAP:
        _dec_label, _dec_color, _dec_em = _DECISION_MAP[_score_final]
        _dec_bg = {"verde":"#d1fae5","naranja":"#fef3c7","rojo":"#fee2e2"}[_dec_color]
        _dec_bd = {"verde":"#059669","naranja":"#d97706","rojo":"#dc2626"}[_dec_color]
        _dec_tx = {"verde":"#065f46","naranja":"#78350f","rojo":"#7f1d1d"}[_dec_color]
        _n_calc  = sum(1 for p, (_, _pv) in _SCORES_CALC.items() if _pv is not None and p != "b3")
        st.markdown(
            f'<div style="background:{_dec_bg};border-left:8px solid {_dec_bd};'
            f'padding:1.1rem 1.4rem;border-radius:8px;margin:0.5rem 0 1rem 0">'
            f'<div style="font-size:1.5rem;font-weight:800;color:{_dec_tx};margin-bottom:0.3rem">'
            f'{_dec_em} Score final: <span style="font-size:2rem">{_score_final}</span> / 4</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:{_dec_tx}">{_dec_label}</div>'
            f'<div style="font-size:0.82rem;color:{_dec_tx};margin-top:0.4rem;opacity:0.85">'
            f'Score ponderado: {_score_sum / _peso_total:.2f} · basado en {_n_calc} indicadores calculados</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.session_state["score_final"]    = _score_final
        st.session_state["decision_final"] = _dec_label

        # ── Desglose del score (transparencia / diagnóstico) ──────────
        with st.expander("🔎 Desglose del score ponderado", expanded=False):
            _BLK_LABEL = {
                "exist": "Existencia del predio", "front": "A1 · Frontera agrícola",
                "area":  "A2 · Área efectiva",    "apt":   "B1 · Aptitud al cultivo",
                "ndvi":  "B2 · Actividad NDVI",   "b3":    "B3 · Altitud (informativo)",
                "infra": "C · Infraestructura",   "d":     "D · Riesgo agroclimático",
            }
            _rows_bd = ""
            for _k, (_p, _v) in _SCORES_CALC.items():
                _vs   = "—" if _v is None else str(_v)
                _contrib = "—" if (_v is None or _p == 0) else f"{_p*_v:.2f}"
                _peso_s  = f"{_p*100:.0f}%"
                _estado  = "no calculado" if _v is None else ""
                _rows_bd += (
                    f'<tr><td style="padding:4px 8px">{_BLK_LABEL.get(_k,_k)}</td>'
                    f'<td style="padding:4px 8px;text-align:center">{_peso_s}</td>'
                    f'<td style="padding:4px 8px;text-align:center">{_vs}</td>'
                    f'<td style="padding:4px 8px;text-align:center">{_contrib}</td>'
                    f'<td style="padding:4px 8px;color:#94a3b8;font-size:0.78rem">{_estado}</td></tr>'
                )
            st.markdown(
                '<table style="width:100%;border-collapse:collapse;font-size:0.84rem">'
                '<thead><tr style="background:#f1f5f9;font-weight:600">'
                '<td style="padding:4px 8px">Bloque</td>'
                '<td style="padding:4px 8px;text-align:center">Peso</td>'
                '<td style="padding:4px 8px;text-align:center">Sub-score (1–4)</td>'
                '<td style="padding:4px 8px;text-align:center">Aporte (peso×score)</td>'
                '<td style="padding:4px 8px"></td></tr></thead>'
                f'<tbody>{_rows_bd}</tbody></table>',
                unsafe_allow_html=True,
            )
            st.caption(
                f"Score ponderado = Σ(aporte) / Σ(pesos calculados) = "
                f"{_score_sum:.3f} / {_peso_total:.2f} = {_score_sum/_peso_total:.3f} "
                f"→ redondeado a **{_score_final}**. Los bloques 'no calculado' "
                "no entran y su peso se redistribuye entre los disponibles."
            )
    else:
        st.info("⚙️ Calcula los indicadores del análisis para obtener el score final consolidado.")

    # ════════════════════════════════════════════════════════════════════
    #  PDF
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📄 Reporte Ex-Ante PDF")

    c_info, c_btn = st.columns([3,2])
    with c_info:
        cod_rpt = predio.get("codigo","predio")
        st.caption(
            f"📄 Pág. 1: ficha del predio · score final + resolución · "
            f"resumen de validación pre-crédito · documentación requerida · firmas.\n\n"
            f"Pág. 2+: análisis detallado de los indicadores (A geométrica · "
            f"B continuidad · C infraestructura · D riesgo agroclimático).\n\n"
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
                        "existencia_nivel": st.session_state.get("a1_nivel", "verde"),
                        "existencia_texto": st.session_state.get("existencia_texto"),
                        "a1_nivel":     st.session_state.get("a2_nivel", "gris"),  # frontera
                        "a2_nivel":     st.session_state.get("a2_nivel", "gris"),
                        "gdf_frontera": st.session_state.get("gdf_frontera"),
                        "b3_nivel":     st.session_state.get("b3_nivel", "gris"),
                        "b3_elev":      st.session_state.get("b3_elev"),
                        "b3_alt_min":   st.session_state.get("b3_alt_min"),
                        "b3_alt_max":   st.session_state.get("b3_alt_max"),
                        # None si el usuario no lanzó el cálculo (A2 queda en gris)
                        "area_ef":      st.session_state.get("area_ef_result", {}).get("area_ef"),
                        "pct_ef":       st.session_state.get("area_ef_result", {}).get("pct_ef"),
                        "area_no_cultivable": st.session_state.get("area_ef_result", {}).get("area_no_cultivable"),
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
                        "score_final":    st.session_state.get("score_final"),
                        "decision_final": st.session_state.get("decision_final"),
                        "obs_unidad":     st.session_state.get("obs_unidad_productiva_texto", "—"),
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
