"""
app.py  ·  AgroCredito · MVP de Evaluación Agroclimática
Streamlit front-end — desplegable en Streamlit Cloud sin instalación local.

Tab 0 · Inicio       → inputs + polígono predio + métricas
Tab 1 · Eligibilidad → validaciones geométrica, productiva, infraestructura
Tab 2 · Riesgo       → indicadores históricos + matriz vulnerabilidad
Tab 3 · Monitoreo    → NDVI actual + forecast
"""

import streamlit as st
import pandas as pd
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
    get_aptitud,
    get_valor_potencial,
    get_construcciones,
)
from utils.eosda_terrain import get_terrain_analysis

# ── Configuración de página ──────────────────────────────────────────────────
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
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
#  DATOS HARDCODEADOS (MVP)
# ════════════════════════════════════════════════════════════════════════════

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
        "precip_mensual": [180,160,210,230,195,140,130,145,220,240,200,175],
        "temp_max_mensual": [24,25,25,24,23,22,22,23,24,25,24,24],
        "temp_min_mensual": [14,14,15,15,14,13,13,13,14,15,15,14],
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
        "precip_mensual": [280,240,310,350,320,260,220,230,310,360,330,290],
        "temp_max_mensual": [32,33,33,32,31,31,31,32,32,33,32,32],
        "temp_min_mensual": [22,22,23,23,22,21,21,21,22,23,23,22],
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

# ════════════════════════════════════════════════════════════════════════════
#  PALETAS Y HELPERS
# ════════════════════════════════════════════════════════════════════════════

COLOR_RIESGO   = {"Nulo":"🟢","Bajo":"🟢","Medio":"🟡","Alto":"🔴","Muy Alto":"🔴"}
COLOR_SEMAFORO = {"verde":"semaforo-verde","naranja":"semaforo-naranja","rojo":"semaforo-rojo"}
COLORES_FRONTERA = {
    "Frontera agrícola":"#16a34a",
    "Frontera agrícola condicionada":"#d97706",
    "Área protegida":"#dc2626",
}
COLORES_APTITUD = {"Alta":"#15803d","Media":"#ca8a04","Baja":"#b45309","No apta":"#dc2626"}

def color_ufh(clase):
    try:
        n = int(clase)
        if n <= 4: return "#15803d"
        if n <= 8: return "#ca8a04"
        return "#dc2626"
    except Exception:
        return "#94a3b8"

def semaforo(texto, nivel):
    st.markdown(f'<div class="{COLOR_SEMAFORO[nivel]}">{texto}</div>', unsafe_allow_html=True)

def kpi(label, valor, unidad=""):
    st.markdown(
        f'<div class="kpi-box"><div style="font-size:0.78rem;color:#64748b">{label}</div>'
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

def _colorscale_bar(label: str, colors: list, ticks: list, units: str = "") -> str:
    """Barra de color HTML horizontal con etiquetas."""
    gradient = ", ".join(colors)
    n = len(ticks)
    tick_html = "".join(
        f'<span style="flex:1;text-align:{"left" if i==0 else "right" if i==n-1 else "center"};'
        f'font-size:0.75rem;color:#475569">{t}</span>'
        for i, t in enumerate(ticks)
    )
    return (
        f'<div style="margin:6px 0 14px 0">'
        f'<div style="font-size:0.78rem;color:#64748b;margin-bottom:3px">'
        f'<b>{label}</b> {units}</div>'
        f'<div style="height:16px;border-radius:4px;border:1px solid #e2e8f0;'
        f'background:linear-gradient(to right,{gradient})"></div>'
        f'<div style="display:flex;margin-top:2px">{tick_html}</div>'
        f'</div>'
    )
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

# ── Mapas ─────────────────────────────────────────────────────────────────────

def _calc_zoom(gdf_predio):
    b = gdf_predio.geometry.iloc[0].bounds
    span = max(b[2]-b[0], b[3]-b[1])
    if span < 0.001: return 18
    if span < 0.003: return 17
    if span < 0.007: return 16
    if span < 0.015: return 15
    if span < 0.03:  return 14
    if span < 0.07:  return 13
    return 12

def _base_map(gdf_predio):
    geom = gdf_predio.geometry.iloc[0]
    m = folium.Map(location=[geom.centroid.y, geom.centroid.x],
                   zoom_start=_calc_zoom(gdf_predio), tiles="Esri.WorldImagery")
    Fullscreen().add_to(m)
    return m

def _add_predio(m, gdf_predio):
    folium.GeoJson(
        data=gdf_predio.to_json(), name="Predio",
        style_function=lambda _: {"fillColor":"#22c55e","color":"#16a34a",
                                   "weight":2.5,"fillOpacity":0.15},
        tooltip=folium.GeoJsonTooltip(
            fields=["codigo","departamento","area_ha"],
            aliases=["Código","Departamento","Área (ha)"],
        ),
    ).add_to(m)

def _fit(m, gdf_predio):
    b = gdf_predio.geometry.iloc[0].bounds
    m.fit_bounds([[b[1],b[0]],[b[3],b[2]]])

def mapa_predio_simple(lat, lon, predio):
    m = _base_map(predio["gdf"])
    _add_predio(m, predio["gdf"])
    folium.Marker([lat, lon], tooltip="Punto ingresado",
                  icon=folium.Icon(color="red", icon="map-marker", prefix="fa")).add_to(m)
    _fit(m, predio["gdf"])
    return m

def mapa_capa(gdf_predio, gdf_capa=None, mostrar_predio=True, mostrar_capa=True,
              estilo_capa_fn=None, campos_tooltip=None, aliases_tooltip=None, nombre_capa="Capa"):
    m = _base_map(gdf_predio)
    if mostrar_predio:
        _add_predio(m, gdf_predio)
    if mostrar_capa and gdf_capa is not None and len(gdf_capa) > 0:
        folium.GeoJson(
            data=gdf_capa.to_json(), name=nombre_capa,
            style_function=estilo_capa_fn or (lambda _: {"fillColor":"#3b82f6","color":"#2563eb",
                                                          "weight":1.5,"fillOpacity":0.45}),
            tooltip=folium.GeoJsonTooltip(
                fields=campos_tooltip or [], aliases=aliases_tooltip or [],
            ) if campos_tooltip else folium.GeoJsonTooltip(fields=[]),
        ).add_to(m)
    _fit(m, gdf_predio)
    return m

# ════════════════════════════════════════════════════════════════════════════
#  HEADER
# ════════════════════════════════════════════════════════════════════════════
c1, c2 = st.columns([1, 8])
with c1: st.markdown("## 🌿")
with c2:
    st.markdown("## AgroCredito · Plataforma de Evaluación de Predios")
    st.caption("Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia")
st.divider()

# ════════════════════════════════════════════════════════════════════════════
#  TABS
# ════════════════════════════════════════════════════════════════════════════
tab_inicio, tab_elegibilidad, tab_riesgo, tab_monitoreo = st.tabs([
    "🏠 Inicio · Ingreso del Predio",
    "✅ Eligibilidad",
    "🌧️ Riesgo Agroclimático",
    "📡 Monitoreo & Forecast",
])

# ════════════════════════════════════════════════════════════════════════════
#  TAB 0 · INICIO
# ════════════════════════════════════════════════════════════════════════════
with tab_inicio:
    st.subheader("Datos del predio a evaluar")

    modo = st.radio("Modo de entrada",
                    ["📂 Caso de estudio (demo)", "📍 Coordenadas manuales"],
                    horizontal=True)

    if modo == "📂 Caso de estudio (demo)":
        caso_sel = st.selectbox("Selecciona caso de estudio", list(CASOS_ESTUDIO.keys()))
        d = CASOS_ESTUDIO[caso_sel]
        st.session_state.update({"datos": d, "lat": d["lat"], "lon": d["lon"],
                                  "cultivo": d["cultivo"], "analizado": True})
    else:
        c1, c2, c3 = st.columns(3)
        with c1: lat_input = st.number_input("Latitud",  value=4.21640,  format="%.6f")
        with c2: lon_input = st.number_input("Longitud", value=-73.97898, format="%.6f")
        with c3: cultivo_in = st.selectbox("Tipo de cultivo", ["café", "plátano"])

        if st.button("🔍 Analizar predio", type="primary", use_container_width=True):
            caso_m = "Café · Eje Cafetero" if cultivo_in == "café" else "Plátano · Urabá"
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
    cultivo = st.session_state.get("cultivo", "café")

    with st.spinner("Consultando base catastral..."):
        predio = get_predio_por_punto(lat, lon)

    if predio is None:
        st.warning("No se encontró ningún predio en las coordenadas indicadas.")
        st.stop()

    st.session_state["predio"] = predio

    st.markdown("#### 🗺️ Identificación del predio catastral")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("Código catastral", predio["codigo"])
    with c2: st.metric("Departamento",     predio.get("departamento", "—"))
    with c3: st.metric("Área catastral",   f"{predio.get('area_ha', '—')} ha")
    with c4: st.metric("Cultivo",          cultivo.capitalize())

    st_folium(mapa_predio_simple(lat, lon, predio),
              width=750, height=450, returned_objects=[])
    st.caption("🟢 Polígono del predio catastral  ·  🔴 Punto ingresado")

    # ── Descarga GeoJSON ──────────────────────────────────────────────────
    import json as _json
    geojson_str = _json.dumps(predio["geojson"], ensure_ascii=False, indent=2)
    st.download_button(
        label="⬇️ Descargar GeoJSON del predio",
        data=geojson_str,
        file_name=f"predio_{predio['codigo']}.geojson",
        mime="application/geo+json",
    )

    st.markdown("---")
    st.markdown("👉 Navega a **Eligibilidad** para el análisis detallado del predio.")

# ════════════════════════════════════════════════════════════════════════════
#  TAB 1 · ELIGIBILIDAD
# ════════════════════════════════════════════════════════════════════════════
with tab_elegibilidad:
    predio  = st.session_state.get("predio")
    d       = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    cultivo = st.session_state.get("cultivo", d.get("cultivo", "café"))

    if predio is None:
        st.info("Primero analiza un predio en el tab **Inicio**.")
        st.stop()

    st.subheader(f"Evaluación de Eligibilidad · {cultivo.capitalize()} · {d.get('municipio','')}")

    # ════════════════════════════════════════════════════════════════════
    #  A · VALIDACIÓN GEOMÉTRICA Y LEGAL
    # ════════════════════════════════════════════════════════════════════
    st.markdown("### 📐 A · Validación Geométrica y Legal")

    # ── A1 · Zona Agrícola (Frontera) ─────────────────────────────────────
    with st.expander("🌿 A1 · Zona Agrícola (Frontera)", expanded=True):
        with st.spinner("Cargando frontera agrícola..."):
            gdf_frontera = get_frontera(predio["gdf"])
        st.session_state["gdf_frontera"] = gdf_frontera

        col1, col2 = st.columns(2)
        with col1: ver_predio_a2   = st.checkbox("🟢 Predio",           value=True, key="a2_predio")
        with col2: ver_frontera_a2 = st.checkbox("🟩 Frontera agrícola", value=True, key="a2_front")

        def estilo_frontera(feature):
            color = COLORES_FRONTERA.get(feature["properties"].get("tipo_condi",""), "#d97706")
            return {"fillColor": color, "color": color, "weight": 2, "fillOpacity": 0.40}

        m_a2 = mapa_capa(
            predio["gdf"], gdf_frontera,
            mostrar_predio=ver_predio_a2, mostrar_capa=ver_frontera_a2,
            estilo_capa_fn=estilo_frontera,
            campos_tooltip=["tipo_condi","area_ha","pct_predio"],
            aliases_tooltip=["Tipo","Área (ha)","% predio"],
            nombre_capa="Frontera agrícola",
        )
        st_folium(m_a2, width=700, height=380, returned_objects=[], key="map_a2")

        if gdf_frontera is not None and len(gdf_frontera) > 0:
            df_front = gdf_frontera.groupby("tipo_condi").agg(
                area_ha=("area_ha","sum"), pct_predio=("pct_predio","sum")
            ).reset_index().rename(columns={"tipo_condi":"Tipo de zona",
                                            "area_ha":"Área (ha)","pct_predio":"% del predio"})
            st.dataframe(df_front, use_container_width=True, hide_index=True)
            tipos = gdf_frontera["tipo_condi"].unique().tolist()
            nivel = "verde" if all("condicionada" not in t.lower() and "protegida" not in t.lower()
                                   for t in tipos) else "naranja"
            semaforo(f"Zona agrícola: **{', '.join(tipos)}**", nivel)
        else:
            st.warning("No se encontró información de frontera agrícola para este predio.")

    # ── A2 · Área Efectiva Cultivable ─────────────────────────────────────
    with st.expander("📏 A2 · Área Efectiva Cultivable", expanded=True):
        st.caption("NDVI y Construcciones hardcoded · Se conectará en la próxima versión")

        col1, col2, col3, col4 = st.columns(4)
        with col1: ver_predio_a1 = st.checkbox("🟢 Predio",        value=True, key="a1_predio")
        with col2: ver_pendiente = st.checkbox("🔴 Pendiente >15°", value=True, key="a1_pend")
        with col3: ver_ndvi_bajo = st.checkbox("🟡 NDVI bajo",      value=True, key="a1_ndvi")
        with col4: ver_const_a1  = st.checkbox("🟠 Construcciones", value=True, key="a1_const")

        area_total = predio.get("area_ha", d["area_total_ha"])
        m_a1 = _base_map(predio["gdf"])
        if ver_predio_a1:
            _add_predio(m_a1, predio["gdf"])
        if ver_pendiente:
            geom_pend = predio["gdf"].geometry.iloc[0].buffer(-0.001)
            if not geom_pend.is_empty:
                gdf_pend = gpd.GeoDataFrame([{"tipo":"No cultivable"}],
                                             geometry=[geom_pend], crs="EPSG:4326")
                folium.GeoJson(data=gdf_pend.to_json(),
                               style_function=lambda _: {"fillColor":"#dc2626","color":"#b91c1c",
                                                          "weight":1,"fillOpacity":0.5},
                               tooltip="Pendiente >15°").add_to(m_a1)
        if ver_ndvi_bajo:
            geom_ndvi = predio["gdf"].geometry.iloc[0].buffer(-0.0015)
            if not geom_ndvi.is_empty:
                gdf_ndvi = gpd.GeoDataFrame([{"tipo":"NDVI bajo"}],
                                             geometry=[geom_ndvi], crs="EPSG:4326")
                folium.GeoJson(data=gdf_ndvi.to_json(),
                               style_function=lambda _: {"fillColor":"#eab308","color":"#ca8a04",
                                                          "weight":1,"fillOpacity":0.5},
                               tooltip="NDVI < 0.40").add_to(m_a1)
        _fit(m_a1, predio["gdf"])
        st_folium(m_a1, width=700, height=380, returned_objects=[], key="map_a1")

        # ── Tabla área efectiva — resultado principal ──────────────────
        st.markdown("---")
        st.markdown("#### 📊 Resultado: Área Efectiva Cultivable")
        area_pend  = st.session_state.get("area_pendiente_excluida_ha", d["area_pendiente_excluida_ha"])
        area_ndvi  = d["area_ndvi_bajo_ha"]
        area_const = d["area_construcciones_ha"]
        area_ef    = round(area_total - area_pend - area_ndvi - area_const, 2)
        pct_ef     = round(area_ef / area_total * 100) if area_total > 0 else 0

        c_left, c_right = st.columns([2, 1])
        with c_left:
            df_area = pd.DataFrame({
                "Componente": ["Área total del predio","− Pendiente >umbral",
                               "− NDVI bajo umbral","− Construcciones",
                               "✅ Área efectiva cultivable"],
                "Hectáreas":  [area_total, -area_pend, -area_ndvi, -area_const, area_ef],
            })
            st.dataframe(
                df_area.style.apply(
                    lambda x: ["font-weight:bold;background:#d1fae5" if "✅" in str(v) else "" for v in x],
                    axis=1),
                use_container_width=True, hide_index=True,
            )
        with c_right:
            st.plotly_chart(gauge_riesgo(pct_ef, "% Área efectiva"), use_container_width=True)
            kpi("Área efectiva", area_ef, "ha")

    # ── A2a · Análisis del Terreno ────────────────────────────────────────
    with st.expander("🏔️ A2-A · Análisis del Terreno (EOSDA API)", expanded=False):
        st.caption("Datos de pendiente utilizados en el cálculo del Área Efectiva anterior.")

        slope_threshold_pct = st.slider(
            "Umbral de pendiente no cultivable (%)",
            min_value=5, max_value=50, value=15, step=1,
            key="slope_threshold",
        )
        # Convertir % a grados para el cálculo (tan(θ) = pendiente%)
        slope_threshold = float(np.degrees(np.arctan(slope_threshold_pct / 100)))

        if st.button("🔄 Calcular terreno", type="primary", key="btn_terrain"):
            st.session_state["terrain"] = None
            with st.spinner("Descargando DEM y calculando terreno..."):
                try:
                    terrain = get_terrain_analysis(predio["gdf"], slope_threshold)
                    st.session_state["terrain"] = terrain
                except Exception as e:
                    st.error(f"❌ Error al obtener datos de terreno: {e}")

        terrain = st.session_state.get("terrain")

        if terrain is None:
            st.info("Pulsa **Calcular terreno** para descargar el DEM del predio desde EOSDA API.")
        else:
            s    = terrain["stats"]
            maps = terrain["maps"]

            st.markdown("**Estadísticas del predio**")
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1: kpi("Elevación mínima",  f"{s['elev_min']:.0f}",   "m")
            with c2: kpi("Elevación media",   f"{s['elev_mean']:.0f}",  "m")
            with c3: kpi("Elevación máxima",  f"{s['elev_max']:.0f}",   "m")
            with c4: kpi("Pendiente media",   f"{s['slope_mean']:.1f}", "°")
            with c5: kpi("Aspecto dominante", s["aspect_dominant"])

            st.markdown("---")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**🏔️ Elevación (DEM)**")
                st_folium(maps["dem_map"], width=420, height=340,
                          returned_objects=[], key="map_dem")
                st.plotly_chart(
                    go.Figure(go.Heatmap(
                        z=[[s["elev_min"], s["elev_max"]]],
                        colorscale="Earth", showscale=True,
                        colorbar=dict(title="m s.n.m.", thickness=12, len=0.6),
                        opacity=0,
                    )).update_layout(
                        height=60, margin=dict(t=0,b=0,l=0,r=80),
                        xaxis=dict(visible=False), yaxis=dict(visible=False),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    ), use_container_width=True, key="scale_dem",
                )
            with c2:
                st.markdown("**📐 Pendiente (Slope)**")
                st_folium(maps["slope_map"], width=420, height=340,
                          returned_objects=[], key="map_slope")
                st.plotly_chart(
                    go.Figure(go.Heatmap(
                        z=[[0, 30]], colorscale="RdYlGn_r", showscale=True,
                        colorbar=dict(title="Grados °", thickness=12, len=0.6),
                        opacity=0,
                    )).update_layout(
                        height=60, margin=dict(t=0,b=0,l=0,r=80),
                        xaxis=dict(visible=False), yaxis=dict(visible=False),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    ), use_container_width=True, key="scale_slope",
                )

            c3, c4 = st.columns(2)
            with c3:
                st.markdown("**🧭 Aspecto (Orientación)**")
                st_folium(maps["aspect_map"], width=420, height=340,
                          returned_objects=[], key="map_aspect")
                st.plotly_chart(
                    go.Figure(go.Heatmap(
                        z=[[0, 360]], colorscale="HSV", showscale=True,
                        colorbar=dict(title="Orientación", thickness=12, len=0.6,
                                      tickvals=[0,90,180,270,360],
                                      ticktext=["N","E","S","O","N"]),
                        opacity=0,
                    )).update_layout(
                        height=60, margin=dict(t=0,b=0,l=0,r=80),
                        xaxis=dict(visible=False), yaxis=dict(visible=False),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    ), use_container_width=True, key="scale_aspect",
                )
            with c4:
                st.caption(f"🌱 Zona cultivable (pendiente < {slope_threshold_pct}%)")
                st_folium(maps["cultiv_map"], width=420, height=340,
                          returned_objects=[], key="map_cultiv")
                st.markdown(
                    '<div style="display:flex;gap:1.5rem;margin-top:4px">'
                    '<span style="background:#16a34a;padding:3px 10px;border-radius:4px;color:white;font-size:0.82rem">🟢 Cultivable</span>'
                    '<span style="background:#dc2626;padding:3px 10px;border-radius:4px;color:white;font-size:0.82rem">🔴 No cultivable</span>'
                    '</div>', unsafe_allow_html=True,
                )
                kpi(f"Área cultivable (<{slope_threshold_pct}%)",
                    f"{s['area_cultivable_ha']} ha", f"({s['pct_cultivable']}%)")

            st.markdown("---")
            st.markdown("**Distribución de clases de pendiente**")
            clases  = list(s["slope_classes"].keys())
            valores = list(s["slope_classes"].values())
            fig_cls = go.Figure(go.Bar(
                x=clases, y=valores,
                marker_color=["#2ecc71","#f1c40f","#e67e22","#e74c3c","#8e44ad"],
                text=[f"{v:.1f}%" for v in valores], textposition="outside",
            ))
            fig_cls.update_layout(
                height=260, margin=dict(t=20,b=60,l=10,r=10),
                yaxis=dict(title="% del área", range=[0, max(valores)*1.2]),
                xaxis=dict(tickangle=-20), showlegend=False,
            )
            st.plotly_chart(fig_cls, use_container_width=True)
            st.session_state["area_pendiente_excluida_ha"] = s["area_no_cultivable_ha"]

    # ════════════════════════════════════════════════════════════════════
    #  B · VALIDACIÓN CONTINUIDAD PRODUCTIVA
    # ════════════════════════════════════════════════════════════════════
    st.markdown("### 🌱 B · Validación de Continuidad Productiva")

    # ── B1 · Aptitud del cultivo ──────────────────────────────────────────
    with st.expander(f"🌾 B1 · Aptitud al Cultivo ({cultivo.capitalize()})", expanded=True):
        with st.spinner("Cargando aptitud del cultivo..."):
            gdf_aptitud = get_aptitud(predio["gdf"], cultivo)
        st.session_state["gdf_aptitud"] = gdf_aptitud

        col1, col2 = st.columns(2)
        with col1: ver_predio_b1  = st.checkbox("🟢 Predio",  value=True, key="b1_predio")
        with col2: ver_aptitud_b1 = st.checkbox("🟦 Aptitud", value=True, key="b1_apt")

        def estilo_aptitud(feature):
            color = COLORES_APTITUD.get(feature["properties"].get("aptitud",""), "#3b82f6")
            return {"fillColor": color, "color": color, "weight": 1.5, "fillOpacity": 0.45}

        m_b1 = mapa_capa(
            predio["gdf"], gdf_aptitud,
            mostrar_predio=ver_predio_b1, mostrar_capa=ver_aptitud_b1,
            estilo_capa_fn=estilo_aptitud,
            campos_tooltip=["aptitud","area_ha","pct_predio"],
            aliases_tooltip=["Aptitud","Área (ha)","% predio"],
            nombre_capa="Aptitud cultivo",
        )
        st_folium(m_b1, width=700, height=380, returned_objects=[], key="map_b1")

        if gdf_aptitud is not None and len(gdf_aptitud) > 0:
            df_apt = gdf_aptitud.groupby("aptitud").agg(
                area_ha=("area_ha","sum"), pct_predio=("pct_predio","sum")
            ).reset_index().rename(columns={"aptitud":"Aptitud",
                                            "area_ha":"Área (ha)","pct_predio":"% del predio"})
            st.dataframe(df_apt, use_container_width=True, hide_index=True)
        else:
            st.warning("No se encontró información de aptitud para este predio.")

    # ── B2 · Valor Potencial (UFH) ────────────────────────────────────────
    with st.expander("💎 B2 · Valor Potencial del Suelo", expanded=True):
        with st.spinner("Cargando valor potencial..."):
            gdf_vp = get_valor_potencial(predio["gdf"])
        st.session_state["gdf_valor_potencial"] = gdf_vp

        col1, col2 = st.columns(2)
        with col1: ver_predio_b2 = st.checkbox("🟢 Predio",         value=True, key="b2_predio")
        with col2: ver_vp_b2     = st.checkbox("🟦 Valor potencial", value=True, key="b2_vp")

        def estilo_vp(feature):
            color = color_ufh(feature["properties"].get("clase_ufh",""))
            return {"fillColor": color, "color": color, "weight": 1.5, "fillOpacity": 0.45}

        m_b2 = mapa_capa(
            predio["gdf"], gdf_vp,
            mostrar_predio=ver_predio_b2, mostrar_capa=ver_vp_b2,
            estilo_capa_fn=estilo_vp,
            campos_tooltip=["clase_ufh","area_ha","pct_predio"],
            aliases_tooltip=["Clase UFH","Área (ha)","% predio"],
            nombre_capa="Valor potencial",
        )
        st_folium(m_b2, width=700, height=380, returned_objects=[], key="map_b2")

        if gdf_vp is not None and len(gdf_vp) > 0:
            df_vp = gdf_vp.groupby("clase_ufh").agg(
                area_ha=("area_ha","sum"), pct_predio=("pct_predio","sum")
            ).reset_index().sort_values("clase_ufh").rename(
                columns={"clase_ufh":"Clase UFH","area_ha":"Área (ha)","pct_predio":"% del predio"})
            st.dataframe(df_vp, use_container_width=True, hide_index=True)
            st.caption("Clase 01–04: alto potencial 🟢 · 05–08: medio 🟡 · 09+: bajo 🔴")
        else:
            st.warning("No se encontró información de valor potencial para este predio.")

    # ── B3 · NDVI ─────────────────────────────────────────────────────────
    with st.expander("📊 B3 · Actividad Productiva (NDVI)", expanded=True):
        st.caption("⚠️ Datos hardcoded · Se conectará a EOSDA API en la próxima versión")
        ndvi = MOCK_NDVI
        c1, c2, c3 = st.columns(3)
        with c1: kpi("NDVI promedio 3 años", ndvi["ndvi_promedio"])
        with c2: kpi("Umbral NDVI", ndvi["umbral_ndvi"])
        with c3:
            ndvi_ok = ndvi["ndvi_promedio"] >= ndvi["umbral_ndvi"]
            kpi("Actividad productiva", "✅ Activa" if ndvi_ok else "⚠️ Por verificar")

        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        fig_ndvi = px.line(x=meses, y=d["ndvi_mensual_hist"],
                           labels={"x":"Mes","y":"NDVI"},
                           title="Serie NDVI mensual (últimos 12 meses)",
                           color_discrete_sequence=["#16a34a"])
        fig_ndvi.add_hline(y=ndvi["umbral_ndvi"], line_dash="dash", line_color="#dc2626",
                           annotation_text=f"Umbral {ndvi['umbral_ndvi']}")
        fig_ndvi.update_layout(height=250, margin=dict(t=40,b=20))
        st.plotly_chart(fig_ndvi, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════
    #  C · INFRAESTRUCTURA (hardcoded)
    # ════════════════════════════════════════════════════════════════════
    with st.expander("🏗️ C · Validación de Infraestructura Productiva", expanded=False):
        st.caption("⚠️ Datos hardcoded · Se conectará a PostGIS en la próxima versión")
        c1, c2 = st.columns(2)
        with c1:
            kpi("Construcciones identificadas", d["construcciones_n"], "unidades")
            st.caption(f"**Detalle:** {d['construcciones_desc']}")
        with c2:
            dist = d["distancia_urbana_km"]
            kpi("Distancia a zona urbana", dist, "km")
            semaforo(f"Acceso {'adecuado' if dist < 20 else 'limitado'} ({dist} km).",
                     "verde" if dist < 20 else "naranja")

    # ════════════════════════════════════════════════════════════════════
    #  RESUMEN ELIGIBILIDAD
    # ════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📋 Resumen de Eligibilidad")
    st.caption("⚠️ Resumen parcialmente hardcoded · Se actualizará con datos reales")

    resumen = pd.DataFrame({
        "Validación": [
            "Área efectiva cultivable",
            "Zona agrícola (frontera)",
            "Aptitud al cultivo",
            "Valor potencial del suelo",
            "Actividad productiva (NDVI)",
            "Infraestructura",
        ],
        "Resultado": [
            f"{area_ef} ha ({pct_ef}%)",
            ", ".join(gdf_frontera["tipo_condi"].unique()) if gdf_frontera is not None else "—",
            ", ".join(gdf_aptitud["aptitud"].unique()) if gdf_aptitud is not None else "—",
            ", ".join(gdf_vp["clase_ufh"].unique()) if gdf_vp is not None else "—",
            "✅ Activa" if ndvi_ok else "⚠️ Por verificar",
            d["construcciones_desc"],
        ],
        "Estado": ["✅","✅","✅","✅","✅" if ndvi_ok else "⚠️","✅"],
    })
    st.dataframe(resumen, use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════════════════
#  TAB 2 · RIESGO AGROCLIMÁTICO
# ════════════════════════════════════════════════════════════════════════════
with tab_riesgo:
    d       = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    cultivo = d["cultivo"]

    st.subheader(f"Análisis de Riesgo Agroclimático · {cultivo.capitalize()}")
    st.caption("Indicadores históricos (últimos 3 años) cruzados con la matriz de vulnerabilidad del cultivo.")

    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    c1, c2 = st.columns(2)
    with c1:
        fig_p = px.bar(x=meses, y=d["precip_mensual"], labels={"x":"Mes","y":"mm"},
                       title="Precipitación media mensual (mm)",
                       color_discrete_sequence=["#3b82f6"])
        fig_p.update_layout(height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_p, use_container_width=True)
    with c2:
        fig_t = go.Figure()
        fig_t.add_trace(go.Scatter(x=meses, y=d["temp_max_mensual"], name="Tmax",
                                   line=dict(color="#ef4444")))
        fig_t.add_trace(go.Scatter(x=meses, y=d["temp_min_mensual"], name="Tmin",
                                   line=dict(color="#3b82f6"), fill="tonexty",
                                   fillcolor="rgba(59,130,246,0.1)"))
        fig_t.update_layout(title="Temperatura mensual (°C)", height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_t, use_container_width=True)

    st.markdown("---")
    st.markdown(f"### 🧮 Matriz de Vulnerabilidad · {cultivo.capitalize()}")
    MATRIZ = {
        "café": {
            "Déficit hídrico (sequía)": ("Bajo",  "Precipitación anual >1.200 mm · sin meses secos extremos"),
            "Exceso de lluvia":         ("Medio", "Meses con >300 mm pueden afectar floración"),
            "Heladas":                  ("Bajo",  "T_min > 10°C en todo el año"),
            "Temperatura máxima":       ("Bajo",  "T_max < 30°C · óptimo cafetero"),
            "Viento fuerte":            ("Bajo",  "Sin registro de episodios severos"),
        },
        "plátano": {
            "Déficit hídrico (sequía)": ("Bajo",  "Precipitación abundante en Urabá"),
            "Exceso de lluvia":         ("Alto",  "Precipitación > 350 mm/mes · riesgo sigatoka"),
            "Heladas":                  ("Nulo",  "T_min > 18°C permanente"),
            "Temperatura máxima":       ("Medio", "T_max > 32°C en verano · estrés hídrico"),
            "Viento fuerte":            ("Medio", "Zona costera · riesgo volcamiento"),
        },
    }
    matriz_data = [{"Indicador":i,"Nivel de riesgo":f"{COLOR_RIESGO[r]} {r}","Detalle":det}
                   for i,(r,det) in MATRIZ[cultivo].items()]
    st.dataframe(pd.DataFrame(matriz_data), use_container_width=True, hide_index=True)

    st.markdown("---")
    rg = d["riesgo_global"]
    semaforo(f"**Riesgo agroclimático global: {rg}** · {cultivo.capitalize()} · {d.get('municipio','')}",
             {"Bajo":"verde","Medio":"naranja","Alto":"rojo"}.get(rg,"naranja"))

# ════════════════════════════════════════════════════════════════════════════
#  TAB 3 · MONITOREO & FORECAST
# ════════════════════════════════════════════════════════════════════════════
with tab_monitoreo:
    d = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])

    st.subheader("Monitoreo en Tiempo Real y Forecast")
    st.caption("Módulo activo durante el ciclo de vida del crédito.")

    if d.get("alerta_activa"):
        st.error(d.get("alerta_msg","⚠️ Alerta climática activa"))
    else:
        st.success("✅ Sin alertas climáticas activas en este momento.")

    c1, c2, c3 = st.columns(3)
    with c1:
        kpi("NDVI actual", round(d["ndvi_actual"],2))
        st.caption(f"Tendencia: {d['ndvi_tendencia']}")
    with c2: kpi("Precipitación forecast (hoy)", f"{d['forecast_precip_7d'][0]}", "mm")
    with c3: kpi("Temperatura forecast (hoy)",   f"{d['forecast_temp_7d'][0]}",   "°C")

    st.markdown("---")
    dias = [(date.today()+timedelta(days=i)).strftime("%d %b") for i in range(7)]
    c1, c2 = st.columns(2)
    with c1:
        fig_fp = px.bar(x=dias, y=d["forecast_precip_7d"],
                        title="Precipitación · Forecast 7 días (mm)",
                        labels={"x":"","y":"mm"}, color_discrete_sequence=["#3b82f6"])
        fig_fp.update_layout(height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_fp, use_container_width=True)
    with c2:
        fig_ft = px.line(x=dias, y=d["forecast_temp_7d"],
                         title="Temperatura · Forecast 7 días (°C)",
                         labels={"x":"","y":"°C"}, color_discrete_sequence=["#ef4444"])
        fig_ft.update_layout(height=260, margin=dict(t=40,b=20))
        st.plotly_chart(fig_ft, use_container_width=True)

    st.markdown("---")
    st.markdown("### 📈 Evolución NDVI (últimos 12 meses)")
    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    fig_nm = px.line(x=meses, y=d["ndvi_mensual_hist"],
                     labels={"x":"Mes","y":"NDVI"}, color_discrete_sequence=["#16a34a"])
    fig_nm.add_scatter(x=[meses[-1]], y=[d["ndvi_actual"]], mode="markers",
                       marker=dict(size=10, color="#dc2626"), name="NDVI actual")
    fig_nm.update_layout(height=260, margin=dict(t=20,b=20))
    st.plotly_chart(fig_nm, use_container_width=True)

    st.info("**Próximas funcionalidades:** Alertas automáticas · Umbrales fenológicos · "
            "Recomendaciones de gestión del riesgo.", icon="🔜")
