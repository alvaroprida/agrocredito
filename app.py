"""
app.py  ·  AgroCredito · MVP de Evaluación Agroclimática
Streamlit front-end — desplegable en Streamlit Cloud sin instalación local.

Estructura:
  Tab 0 · Inicio          → inputs del predio + botón analizar
  Tab 1 · Eligibilidad    → 3 bloques de validación ex-ante
  Tab 2 · Riesgo Climático → indicadores históricos + matriz vulnerabilidad
  Tab 3 · Monitoreo       → NDVI actual + forecast (post-desembolso)

Para el MVP, los tabs 1-3 se alimentan con datos de dos casos de estudio
hardcodeados (café · Eje Cafetero / plátano · Urabá) mientras se
conectan las APIs y la base PostGIS.
"""

import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
from datetime import date, timedelta
import plotly.graph_objects as go
import plotly.express as px

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="AgroCredito · Evaluación de Predios",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS mínimo ────────────────────────────────────────────────────────────────
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
#  DATOS DE CASOS DE ESTUDIO (hardcodeados para MVP)
#  → Se reemplazarán por llamadas reales a EOSDA API + PostGIS
# ════════════════════════════════════════════════════════════════════════════

CASOS_ESTUDIO = {
    "Café · Eje Cafetero": {
        "lat": 4.8087, "lon": -75.6906, "cultivo": "café",
        "municipio": "Salento, Quindío",
        # Validación geométrica
        "area_total_ha": 12.4,
        "area_pendiente_excluida_ha": 1.8,
        "area_ndvi_bajo_ha": 0.6,
        "area_construcciones_ha": 0.3,
        "area_efectiva_ha": 9.7,
        "frontera_agricola": "Frontera agrícola",
        "frontera_estado": "verde",
        # Continuidad productiva
        "aptitud_cultivo": "Alta",
        "valor_potencial": "Alto",
        "ndvi_promedio_3a": 0.71,
        "ndvi_umbral": 0.40,
        # Infraestructura
        "construcciones_n": 3,
        "construcciones_desc": "Casa, bodega, beneficiadero",
        "distancia_urbana_km": 8.2,
        # Riesgo climático — series mensuales últimos 3 años
        "precip_mensual": [180,160,210,230,195,140,130,145,220,240,200,175],
        "temp_max_mensual": [24,25,25,24,23,22,22,23,24,25,24,24],
        "temp_min_mensual": [14,14,15,15,14,13,13,13,14,15,15,14],
        "ndvi_mensual_hist": [.65,.67,.70,.72,.71,.68,.66,.67,.70,.73,.72,.69],
        # Riesgo → matriz vulnerabilidad
        "riesgo_sequia": "Bajo",
        "riesgo_exceso_lluvia": "Medio",
        "riesgo_helada": "Bajo",
        "riesgo_temp_alta": "Bajo",
        "riesgo_global": "Bajo",
        # Monitoreo / forecast (simulado)
        "ndvi_actual": 0.69,
        "ndvi_tendencia": "estable",
        "alerta_activa": False,
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
        "aptitud_cultivo": "Alta",
        "valor_potencial": "Muy Alto",
        "ndvi_promedio_3a": 0.78,
        "ndvi_umbral": 0.40,
        "construcciones_n": 5,
        "construcciones_desc": "Casa, dos bodegas, empacadora, generador",
        "distancia_urbana_km": 14.5,
        "precip_mensual": [280,240,310,350,320,260,220,230,310,360,330,290],
        "temp_max_mensual": [32,33,33,32,31,31,31,32,32,33,32,32],
        "temp_min_mensual": [22,22,23,23,22,21,21,21,22,23,23,22],
        "ndvi_mensual_hist": [.74,.76,.79,.81,.80,.77,.75,.76,.79,.82,.81,.78],
        "riesgo_sequia": "Bajo",
        "riesgo_exceso_lluvia": "Alto",
        "riesgo_helada": "Nulo",
        "riesgo_temp_alta": "Medio",
        "riesgo_global": "Medio",
        "ndvi_actual": 0.77,
        "ndvi_tendencia": "ligero descenso",
        "alerta_activa": True,
        "alerta_msg": "⚠️ Exceso de precipitación proyectado próximos 5 días",
        "forecast_precip_7d": [35,42,50,48,38,22,15],
        "forecast_temp_7d":   [30,29,29,30,31,32,32],
    },
}

# ════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════════

COLOR_RIESGO = {"Nulo": "🟢", "Bajo": "🟢", "Medio": "🟡", "Alto": "🔴", "Muy Alto": "🔴"}
COLOR_SEMAFORO = {"verde": "semaforo-verde", "naranja": "semaforo-naranja", "rojo": "semaforo-rojo"}

def semaforo(texto: str, nivel: str):
    st.markdown(f'<div class="{COLOR_SEMAFORO[nivel]}">{texto}</div>', unsafe_allow_html=True)

def kpi(label: str, valor, unidad: str = ""):
    st.markdown(
        f'<div class="kpi-box"><div style="font-size:0.78rem;color:#64748b">{label}</div>'
        f'<div style="font-size:1.4rem;font-weight:700">{valor}<span style="font-size:0.85rem;color:#64748b"> {unidad}</span></div></div>',
        unsafe_allow_html=True,
    )

def mapa_predio(lat, lon, zoom=13):
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="Esri.WorldImagery")
    folium.Marker([lat, lon], tooltip="Predio", icon=folium.Icon(color="green", icon="leaf")).add_to(m)
    folium.Circle([lat, lon], radius=400, color="#22c55e", fill=True, fill_opacity=0.15).add_to(m)
    return m

def gauge_riesgo(valor_pct: int, titulo: str):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=valor_pct,
        title={"text": titulo, "font": {"size": 13}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#3b82f6"},
            "steps": [
                {"range": [0, 33],  "color": "#d1fae5"},
                {"range": [33, 66], "color": "#fef3c7"},
                {"range": [66, 100],"color": "#fee2e2"},
            ],
        },
        number={"suffix": "%"},
    ))
    fig.update_layout(height=200, margin=dict(t=40, b=10, l=10, r=10))
    return fig

# ════════════════════════════════════════════════════════════════════════════
#  HEADER
# ════════════════════════════════════════════════════════════════════════════
col_logo, col_title = st.columns([1, 8])
with col_logo:
    st.markdown("## 🌿")
with col_title:
    st.markdown("## AgroCredito · Plataforma de Evaluación de Predios")
    st.caption("Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia")

st.divider()

# ════════════════════════════════════════════════════════════════════════════
#  TABS PRINCIPALES
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
    st.info(
        "**Demo MVP:** Selecciona uno de los casos de estudio precargados, o ingresa "
        "coordenadas manualmente. La conexión en tiempo real a EOSDA API y PostGIS "
        "estará activa en la versión de producción.",
        icon="ℹ️",
    )

    modo = st.radio(
        "Modo de entrada",
        ["📂 Caso de estudio (demo)", "📍 Coordenadas manuales"],
        horizontal=True,
    )

    if modo == "📂 Caso de estudio (demo)":
        caso_sel = st.selectbox("Selecciona caso de estudio", list(CASOS_ESTUDIO.keys()))
        d = CASOS_ESTUDIO[caso_sel]
        st.success(f"**Municipio:** {d['municipio']}  ·  **Cultivo:** {d['cultivo'].capitalize()}  ·  "
                   f"**Coordenadas:** {d['lat']:.4f}, {d['lon']:.4f}")
        st.session_state["datos"] = d
        st.session_state["caso"] = caso_sel

    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            lat_in = st.number_input("Latitud", value=4.8087, format="%.6f")
        with c2:
            lon_in = st.number_input("Longitud", value=-75.6906, format="%.6f")
        with c3:
            cultivo_in = st.selectbox("Tipo de cultivo", ["café", "plátano"])

        st.caption("⚠️ En modo manual, los resultados mostrarán datos simulados hasta que la API esté conectada.")
        # Para el MVP redirigimos al caso más cercano según cultivo
        caso_manual = "Café · Eje Cafetero" if cultivo_in == "café" else "Plátano · Urabá"
        d = {**CASOS_ESTUDIO[caso_manual], "lat": lat_in, "lon": lon_in, "cultivo": cultivo_in}
        st.session_state["datos"] = d
        st.session_state["caso"] = f"Manual ({cultivo_in})"

    st.markdown("#### Vista del predio")
    datos_tab0 = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    st_folium(mapa_predio(datos_tab0["lat"], datos_tab0["lon"]), width=700, height=350, returned_objects=[])

    st.markdown("---")
    st.markdown(
        "👉 Navega a los tabs superiores para ver los resultados de **Eligibilidad**, "
        "**Riesgo Agroclimático** y **Monitoreo**."
    )

# ════════════════════════════════════════════════════════════════════════════
#  TAB 1 · ELIGIBILIDAD
# ════════════════════════════════════════════════════════════════════════════
with tab_elegibilidad:
    d = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])

    st.subheader(f"Evaluación de Eligibilidad · {d['cultivo'].capitalize()} · {d.get('municipio','')}")

    # ── Bloque A: Validación Geométrica y Legal ───────────────────────────
    with st.expander("📐 A · Validación Geométrica y Legal", expanded=True):
        c1, c2 = st.columns([2, 1])
        with c1:
            nivel_f = d["frontera_estado"]
            msg_frontera = (
                f"✅ El predio se encuentra en **{d['frontera_agricola']}**."
                if nivel_f == "verde"
                else f"⚠️ El predio se encuentra en **{d['frontera_agricola']}**. "
                     f"Condición: {d.get('condicion_frontera', 'Ver detalle')}."
            )
            semaforo(msg_frontera, nivel_f)
            st.markdown("---")
            st.markdown("**Cálculo del Área Efectiva Cultivable**")

            area_data = {
                "Componente": [
                    "Área total del predio",
                    "− Zonas con pendiente > 10%",
                    "− Zonas con NDVI bajo",
                    "− Construcciones",
                    "✅ Área efectiva cultivable",
                ],
                "Hectáreas": [
                    d["area_total_ha"],
                    -d["area_pendiente_excluida_ha"],
                    -d["area_ndvi_bajo_ha"],
                    -d["area_construcciones_ha"],
                    d["area_efectiva_ha"],
                ],
            }
            df_area = pd.DataFrame(area_data)
            st.dataframe(
                df_area.style.apply(
                    lambda x: ["font-weight:bold; background:#d1fae5" if "✅" in str(v) else "" for v in x],
                    axis=1,
                ),
                use_container_width=True, hide_index=True,
            )

        with c2:
            pct_efectiva = round(d["area_efectiva_ha"] / d["area_total_ha"] * 100)
            st.plotly_chart(gauge_riesgo(pct_efectiva, "% Área efectiva"), use_container_width=True)
            kpi("Área efectiva", d["area_efectiva_ha"], "ha")

    # ── Bloque B: Continuidad Productiva ─────────────────────────────────
    with st.expander("🌱 B · Validación de Continuidad Productiva", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            kpi("Aptitud al cultivo", d["aptitud_cultivo"])
        with c2:
            kpi("Valor potencial", d["valor_potencial"])
        with c3:
            ndvi_ok = d["ndvi_promedio_3a"] >= d["ndvi_umbral"]
            kpi("NDVI promedio 3 años", round(d["ndvi_promedio_3a"], 2),
                "✅ activo" if ndvi_ok else "⚠️ bajo umbral")

        st.markdown("---")
        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        fig_ndvi = px.line(
            x=meses, y=d["ndvi_mensual_hist"],
            labels={"x": "Mes", "y": "NDVI"},
            title="Serie NDVI mensual (últimos 12 meses)",
            color_discrete_sequence=["#16a34a"],
        )
        fig_ndvi.add_hline(y=d["ndvi_umbral"], line_dash="dash", line_color="#dc2626",
                           annotation_text=f"Umbral {d['ndvi_umbral']}")
        fig_ndvi.update_layout(height=250, margin=dict(t=40, b=20))
        st.plotly_chart(fig_ndvi, use_container_width=True)

    # ── Bloque C: Infraestructura ─────────────────────────────────────────
    with st.expander("🏗️ C · Validación de Infraestructura Productiva", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            kpi("Construcciones identificadas", d["construcciones_n"], "unidades")
            st.caption(f"**Detalle:** {d['construcciones_desc']}")
        with c2:
            dist = d["distancia_urbana_km"]
            nivel_dist = "verde" if dist < 20 else "naranja"
            kpi("Distancia a zona urbana", dist, "km")
            semaforo(
                f"Acceso {'adecuado' if dist < 20 else 'limitado'} a servicios urbanos ({dist} km).",
                nivel_dist,
            )

    # ── Resumen elegibilidad ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Resumen de Eligibilidad")
    resumen = pd.DataFrame({
        "Validación": ["Frontera agrícola", "Área efectiva cultivable", "Aptitud al cultivo",
                       "Actividad productiva (NDVI)", "Infraestructura"],
        "Resultado": [
            d["frontera_agricola"],
            f"{d['area_efectiva_ha']} ha ({pct_efectiva}%)",
            d["aptitud_cultivo"],
            "✅ Activo" if d["ndvi_promedio_3a"] >= d["ndvi_umbral"] else "⚠️ Por verificar",
            d["construcciones_desc"],
        ],
        "Estado": ["✅", "✅", "✅", "✅" if d["ndvi_promedio_3a"] >= d["ndvi_umbral"] else "⚠️",
                   "✅"],
    })
    st.dataframe(resumen, use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════════════════
#  TAB 2 · RIESGO AGROCLIMÁTICO
# ════════════════════════════════════════════════════════════════════════════
with tab_riesgo:
    d = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])
    cultivo = d["cultivo"]

    st.subheader(f"Análisis de Riesgo Agroclimático · {cultivo.capitalize()}")
    st.caption("Indicadores históricos (últimos 3 años) cruzados con la matriz de vulnerabilidad del cultivo.")

    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    # Precipitación y temperatura
    c1, c2 = st.columns(2)
    with c1:
        fig_p = px.bar(x=meses, y=d["precip_mensual"],
                       labels={"x": "Mes", "y": "mm"},
                       title="Precipitación media mensual (mm)",
                       color_discrete_sequence=["#3b82f6"])
        fig_p.update_layout(height=260, margin=dict(t=40, b=20))
        st.plotly_chart(fig_p, use_container_width=True)

    with c2:
        fig_t = go.Figure()
        fig_t.add_trace(go.Scatter(x=meses, y=d["temp_max_mensual"], name="Tmax",
                                   line=dict(color="#ef4444")))
        fig_t.add_trace(go.Scatter(x=meses, y=d["temp_min_mensual"], name="Tmin",
                                   line=dict(color="#3b82f6"), fill="tonexty",
                                   fillcolor="rgba(59,130,246,0.1)"))
        fig_t.update_layout(title="Temperatura mensual (°C)", height=260,
                             margin=dict(t=40, b=20))
        st.plotly_chart(fig_t, use_container_width=True)

    # Matriz de vulnerabilidad
    st.markdown("---")
    st.markdown(f"### 🧮 Matriz de Vulnerabilidad · {cultivo.capitalize()}")

    MATRIZ = {
        "café": {
            "Déficit hídrico (sequía)": ("Bajo", "Precipitación anual >1.200 mm · sin meses secos extremos"),
            "Exceso de lluvia": ("Medio", "Meses con >300 mm pueden afectar floración"),
            "Heladas": ("Bajo", "T_min > 10°C en todo el año"),
            "Temperatura máxima": ("Bajo", "T_max < 30°C · óptimo cafetero"),
            "Viento fuerte": ("Bajo", "Sin registro de episodios severos"),
        },
        "plátano": {
            "Déficit hídrico (sequía)": ("Bajo", "Precipitación abundante en Urabá"),
            "Exceso de lluvia": ("Alto", "Precipitación > 350 mm/mes · riesgo sigatoka"),
            "Heladas": ("Nulo", "T_min > 18°C permanente"),
            "Temperatura máxima": ("Medio", "T_max > 32°C en verano · estrés hídrico"),
            "Viento fuerte": ("Medio", "Zona costera · riesgo volcamiento"),
        },
    }

    matriz_data = []
    for indicador, (riesgo, detalle) in MATRIZ[cultivo].items():
        matriz_data.append({
            "Indicador": indicador,
            "Nivel de riesgo": f"{COLOR_RIESGO[riesgo]} {riesgo}",
            "Detalle": detalle,
        })
    df_matriz = pd.DataFrame(matriz_data)
    st.dataframe(df_matriz, use_container_width=True, hide_index=True)

    # Riesgo global
    st.markdown("---")
    riesgo_global = d["riesgo_global"]
    nivel_global = {"Bajo": "verde", "Medio": "naranja", "Alto": "rojo"}.get(riesgo_global, "naranja")
    semaforo(
        f"**Riesgo agroclimático global del predio: {riesgo_global}** · "
        f"Cultivo: {cultivo.capitalize()} · {d.get('municipio','')}",
        nivel_global,
    )

# ════════════════════════════════════════════════════════════════════════════
#  TAB 3 · MONITOREO & FORECAST
# ════════════════════════════════════════════════════════════════════════════
with tab_monitoreo:
    d = st.session_state.get("datos", list(CASOS_ESTUDIO.values())[0])

    st.subheader("Monitoreo en Tiempo Real y Forecast")
    st.caption("Módulo activo durante el ciclo de vida del crédito.")

    # Alerta activa
    if d.get("alerta_activa"):
        st.error(d.get("alerta_msg", "⚠️ Alerta climática activa"))
    else:
        st.success("✅ Sin alertas climáticas activas en este momento.")

    # KPIs de monitoreo
    c1, c2, c3 = st.columns(3)
    with c1:
        kpi("NDVI actual", round(d["ndvi_actual"], 2))
        st.caption(f"Tendencia: {d['ndvi_tendencia']}")
    with c2:
        kpi("Precipitación forecast (hoy)", f"{d['forecast_precip_7d'][0]}", "mm")
    with c3:
        kpi("Temperatura forecast (hoy)", f"{d['forecast_temp_7d'][0]}", "°C")

    st.markdown("---")

    # Forecast 7 días
    dias = [(date.today() + timedelta(days=i)).strftime("%d %b") for i in range(7)]
    c1, c2 = st.columns(2)
    with c1:
        fig_fp = px.bar(x=dias, y=d["forecast_precip_7d"],
                        title="Precipitación · Forecast 7 días (mm)",
                        labels={"x": "", "y": "mm"},
                        color_discrete_sequence=["#3b82f6"])
        fig_fp.update_layout(height=260, margin=dict(t=40, b=20))
        st.plotly_chart(fig_fp, use_container_width=True)

    with c2:
        fig_ft = px.line(x=dias, y=d["forecast_temp_7d"],
                         title="Temperatura · Forecast 7 días (°C)",
                         labels={"x": "", "y": "°C"},
                         color_discrete_sequence=["#ef4444"])
        fig_ft.update_layout(height=260, margin=dict(t=40, b=20))
        st.plotly_chart(fig_ft, use_container_width=True)

    st.markdown("---")
    st.markdown("### 📈 Evolución NDVI (últimos 12 meses)")
    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    fig_ndvi_m = px.line(x=meses, y=d["ndvi_mensual_hist"],
                         labels={"x": "Mes", "y": "NDVI"},
                         color_discrete_sequence=["#16a34a"])
    fig_ndvi_m.add_scatter(x=[meses[-1]], y=[d["ndvi_actual"]],
                            mode="markers", marker=dict(size=10, color="#dc2626"),
                            name="NDVI actual")
    fig_ndvi_m.update_layout(height=260, margin=dict(t=20, b=20))
    st.plotly_chart(fig_ndvi_m, use_container_width=True)

    st.info(
        "**Próximas funcionalidades:** Integración de alertas automáticas por email/SMS · "
        "Comparación con umbrales por fase fenológica · Recomendaciones de gestión del riesgo.",
        icon="🔜",
    )
