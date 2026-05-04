"""
utils/risk_scoring.py
Scoring de riesgo agroclimático — MVP hardcoded.

Todos los valores históricos están hardcodeados por caso de estudio.
Los umbrales tienen valores estándar por cultivo (café / plátano).
Una vez validado el formato, se sustituirán indicador a indicador por APIs reales.

Función principal:
    score_riesgo(datos, umbrales_custom=None) -> dict
"""

from __future__ import annotations
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  DEFINICIÓN DE INDICADORES
# ══════════════════════════════════════════════════════════════════════════════

GRUPOS = [
    "Déficit Hídrico",
    "Exceso Hídrico / Inundación",
    "Estrés Térmico",
    "Daño Mecánico / Viento",
    "Salud y Productividad del Predio",
    "Factores Estructurales del Territorio",
]

# direccion:
#   "mayor" → valor > umbral_alto  = riesgo Alto
#   "menor" → valor < umbral_alto  = riesgo Alto
INDICADORES: list[dict] = [
    # ── DÉFICIT HÍDRICO ───────────────────────────────────────────────────
    {
        "id": 1, "grupo": "Déficit Hídrico",
        "nombre": "SWI — Soil Water Index",
        "metrica": "Meses con humedad suelo < umbral / año",
        "fuente": "ERA5-Land · Open-Meteo",
        "api": True, "pendiente": True,
        "unidad": "meses/año",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 1, "medio": 3, "alto": 5},
            "plátano": {"bajo": 0, "medio": 2, "alto": 4},
        },
    },
    {
        "id": 2, "grupo": "Déficit Hídrico",
        "nombre": "SPEI-3 / SPEI-6",
        "metrica": "Meses con SPEI < −1 / año",
        "fuente": "ERA5 · Open-Meteo",
        "api": True, "pendiente": True,
        "unidad": "meses/año",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 1, "medio": 3, "alto": 5},
            "plátano": {"bajo": 0, "medio": 2, "alto": 4},
        },
    },
    {
        "id": 3, "grupo": "Déficit Hídrico",
        "nombre": "WRSI — Water Requirements Satisfaction Index",
        "metrica": "% satisfacción hídrica promedio anual",
        "fuente": "ERA5 + FAO-56",
        "api": True, "pendiente": True,
        "unidad": "%",
        "direccion": "menor",
        "umbrales": {
            "café":    {"bajo": 80, "medio": 60, "alto": 50},
            "plátano": {"bajo": 85, "medio": 70, "alto": 55},
        },
    },
    # ── EXCESO HÍDRICO ────────────────────────────────────────────────────
    {
        "id": 4, "grupo": "Exceso Hídrico / Inundación",
        "nombre": "Precipitación acumulada 7 días",
        "metrica": "Episodios > umbral mm en 7 días / año",
        "fuente": "ERA5 / CHIRPS · Open-Meteo",
        "api": True, "pendiente": True,
        "unidad": "episodios/año",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 1, "medio": 3, "alto": 5},
            "plátano": {"bajo": 2, "medio": 4, "alto": 7},
        },
    },
    {
        "id": 5, "grupo": "Exceso Hídrico / Inundación",
        "nombre": "Índice susceptibilidad deslizamientos",
        "metrica": "Clase de susceptibilidad (1–5)",
        "fuente": "SGC / IRAKA Colombia",
        "api": False, "pendiente": True,
        "unidad": "clase 1–5",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 2, "medio": 3, "alto": 4},
            "plátano": {"bajo": 2, "medio": 3, "alto": 4},
        },
    },
    # ── ESTRÉS TÉRMICO ────────────────────────────────────────────────────
    {
        "id": 6, "grupo": "Estrés Térmico",
        "nombre": "Temperatura máxima media anual",
        "metrica": "T_máx media en meses críticos (°C)",
        "fuente": "ERA5 · Open-Meteo",
        "api": True, "pendiente": False,
        "unidad": "°C",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 26, "medio": 28, "alto": 30},
            "plátano": {"bajo": 32, "medio": 34, "alto": 35},
        },
    },
    {
        "id": 7, "grupo": "Estrés Térmico",
        "nombre": "N° días con T_mín < umbral (heladas)",
        "metrica": "Días/año con T_mín bajo umbral fisiológico",
        "fuente": "ERA5 · Open-Meteo",
        "api": True, "pendiente": False,
        "unidad": "días/año",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 0, "medio": 2, "alto": 5},
            "plátano": {"bajo": 0, "medio": 1, "alto": 3},
        },
    },
    # ── DAÑO MECÁNICO / VIENTO ────────────────────────────────────────────
    {
        "id": 8, "grupo": "Daño Mecánico / Viento",
        "nombre": "N° días con viento > umbral",
        "metrica": "Días/año con viento > 60 km/h",
        "fuente": "ERA5 · Open-Meteo",
        "api": True, "pendiente": True,
        "unidad": "días/año",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 2, "medio": 5, "alto": 10},
            "plátano": {"bajo": 1, "medio": 3, "alto": 7},
        },
    },
    # ── SALUD Y PRODUCTIVIDAD ─────────────────────────────────────────────
    {
        "id": 9, "grupo": "Salud y Productividad del Predio",
        "nombre": "NDVI — anomalía vs. percentiles históricos",
        "metrica": "NDVI promedio anual del predio",
        "fuente": "Sentinel-2 · EOSDA / GEE",
        "api": True, "pendiente": True,
        "unidad": "índice 0–1",
        "direccion": "menor",
        "umbrales": {
            "café":    {"bajo": 0.60, "medio": 0.50, "alto": 0.40},
            "plátano": {"bajo": 0.65, "medio": 0.55, "alto": 0.45},
        },
    },
    {
        "id": 10, "grupo": "Salud y Productividad del Predio",
        "nombre": "NDMI — estrés hídrico foliar",
        "metrica": "NDMI promedio anual del predio",
        "fuente": "Sentinel-2 · EOSDA / GEE",
        "api": True, "pendiente": True,
        "unidad": "índice −1 a 1",
        "direccion": "menor",
        "umbrales": {
            "café":    {"bajo": 0.20, "medio": 0.10, "alto": 0.00},
            "plátano": {"bajo": 0.25, "medio": 0.15, "alto": 0.05},
        },
    },
    {
        "id": 11, "grupo": "Salud y Productividad del Predio",
        "nombre": "NDRE — alerta temprana clorosis",
        "metrica": "NDRE promedio anual del predio",
        "fuente": "Sentinel-2 · EOSDA / GEE",
        "api": True, "pendiente": True,
        "unidad": "índice 0–1",
        "direccion": "menor",
        "umbrales": {
            "café":    {"bajo": 0.40, "medio": 0.30, "alto": 0.20},
            "plátano": {"bajo": 0.45, "medio": 0.35, "alto": 0.25},
        },
    },
    {
        "id": 12, "grupo": "Salud y Productividad del Predio",
        "nombre": "VH backscatter SAR — anomalía post-evento",
        "metrica": "Cambio VH vs. línea base (dB)",
        "fuente": "Sentinel-1 GRD · GEE",
        "api": True, "pendiente": True,
        "unidad": "dB",
        "direccion": "menor",
        "umbrales": {
            "café":    {"bajo": -1.0, "medio": -2.0, "alto": -3.0},
            "plátano": {"bajo": -1.0, "medio": -2.0, "alto": -3.5},
        },
    },
    # ── FACTORES ESTRUCTURALES ────────────────────────────────────────────
    {
        "id": 13, "grupo": "Factores Estructurales del Territorio",
        "nombre": "Valor Potencial del Suelo (VPS)",
        "metrica": "Clase de suelo UPRA (1=mejor, 5=peor)",
        "fuente": "UPRA / SIPRA Colombia",
        "api": False, "pendiente": True,
        "unidad": "clase 1–5",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 2, "medio": 3, "alto": 4},
            "plátano": {"bajo": 2, "medio": 3, "alto": 4},
        },
    },
    {
        "id": 14, "grupo": "Factores Estructurales del Territorio",
        "nombre": "Aptitud agroclimática del cultivo",
        "metrica": "Clase aptitud UPRA (1=óptima, 4=no apta)",
        "fuente": "UPRA / SIPRA Colombia",
        "api": False, "pendiente": True,
        "unidad": "clase 1–4",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 1, "medio": 2, "alto": 3},
            "plátano": {"bajo": 1, "medio": 2, "alto": 3},
        },
    },
    {
        "id": 15, "grupo": "Factores Estructurales del Territorio",
        "nombre": "Distancia al centro urbano más cercano",
        "metrica": "Distancia en km al núcleo urbano más próximo",
        "fuente": "IGAC / OpenStreetMap · OSMnx",
        "api": False, "pendiente": False,
        "unidad": "km",
        "direccion": "mayor",
        "umbrales": {
            "café":    {"bajo": 10, "medio": 25, "alto": 50},
            "plátano": {"bajo": 15, "medio": 30, "alto": 60},
        },
    },
]

IND_BY_ID = {i["id"]: i for i in INDICADORES}

# ══════════════════════════════════════════════════════════════════════════════
#  VALORES HISTÓRICOS HARDCODED POR CASO DE ESTUDIO
# ══════════════════════════════════════════════════════════════════════════════
# Formato: { cultivo: { id_indicador: valor_historico } }
# pendiente=True → valor representativo para el MVP; se sustituirá por API real.

VALORES_HARDCODED: dict[str, dict[int, float]] = {
    "café": {
        1:  1.0,   # SWI: 1 mes seco al año (Salento, buena humedad)
        2:  1.0,   # SPEI: 1 mes con déficit severo
        3:  82.0,  # WRSI: 82% satisfacción hídrica → bajo riesgo
        4:  1.0,   # Episodios lluvia extrema 7d: 1/año
        5:  2.0,   # Susceptibilidad deslizamiento: clase 2
        6:  24.5,  # T_máx media anual (°C)
        7:  0.0,   # Días con T_mín < umbral helada: 0
        8:  1.0,   # Días viento fuerte: 1
        9:  0.71,  # NDVI promedio
        10: 0.22,  # NDMI promedio
        11: 0.42,  # NDRE promedio
        12: -0.8,  # VH backscatter cambio (dB)
        13: 2.0,   # VPS clase suelo
        14: 1.0,   # Aptitud agroclimática clase
        15: 8.2,   # Distancia urbana (km)
    },
    "plátano": {
        1:  0.0,   # SWI: sin meses secos (Urabá, alta pluviosidad)
        2:  0.0,   # SPEI: sin déficit severo
        3:  88.0,  # WRSI: 88% → bajo riesgo déficit
        4:  6.0,   # Episodios lluvia extrema 7d: 6/año → alto
        5:  3.0,   # Susceptibilidad deslizamiento: clase 3
        6:  32.3,  # T_máx media anual (°C)
        7:  0.0,   # Días helada: 0 (trópico bajo)
        8:  3.0,   # Días viento fuerte: 3 (zona costera)
        9:  0.78,  # NDVI promedio
        10: 0.27,  # NDMI promedio
        11: 0.46,  # NDRE promedio
        12: -0.9,  # VH backscatter
        13: 2.0,   # VPS clase suelo
        14: 1.0,   # Aptitud clase
        15: 14.5,  # Distancia urbana (km)
    },
}

# ══════════════════════════════════════════════════════════════════════════════
#  LÓGICA DE SCORING
# ══════════════════════════════════════════════════════════════════════════════

def _score_indicador(valor: float, umbral: dict, direccion: str) -> int:
    """
    Devuelve 0 (Bajo), 1 (Medio), 2 (Alto).
    direccion='mayor': más valor = más riesgo.
    direccion='menor': menos valor = más riesgo.
    """
    b, m, a = umbral["bajo"], umbral["medio"], umbral["alto"]
    if direccion == "mayor":
        if valor >= a: return 2
        if valor >= m: return 1
        return 0
    else:  # menor
        if valor <= a: return 2
        if valor <= m: return 1
        return 0

SCORE_LABEL = {0: "🟢 Bajo", 1: "🟡 Medio", 2: "🔴 Alto"}
SCORE_NUM   = {"Bajo": 0, "Medio": 1, "Alto": 2}
SCORE_COLOR = {0: "#d1fae5", 1: "#fef3c7", 2: "#fee2e2"}
SCORE_TEXT  = {0: "#065f46", 1: "#78350f", 2: "#7f1d1d"}

DECISION_RIESGO = {
    0: "Sin restricción.",
    1: "Incluir cláusula de seguimiento trimestral.",
    2: "Seguro agrícola obligatorio / evaluar viabilidad.",
}

def score_riesgo(
    datos: dict,
    umbrales_custom: Optional[dict[int, dict]] = None,
) -> dict:
    """
    Calcula el scoring completo de riesgo.

    Args:
        datos:           dict del caso de estudio (CASOS_ESTUDIO)
        umbrales_custom: {id_indicador: {"bajo":x,"medio":x,"alto":x}}
                         Si None, usa umbrales estándar por cultivo.

    Returns:
        {
          "resultados": [ {id, grupo, nombre, metrica, fuente, api, pendiente,
                           unidad, valor, umbral_bajo, umbral_medio, umbral_alto,
                           score_num, score_label, score_color, score_text,
                           decision} ],
          "por_grupo":  { grupo: score_num_medio },
          "score_global": int (0/1/2),
          "score_global_label": str,
          "score_global_color": str,
          "n_alto": int, "n_medio": int, "n_bajo": int,
        }
    """
    cultivo = datos.get("cultivo", "café")
    valores = VALORES_HARDCODED.get(cultivo, VALORES_HARDCODED["café"]).copy()

    # Sobreescribir con valores calculables desde series mensuales si existen
    tmax_series = datos.get("temp_max_mensual", [])
    tmin_series = datos.get("temp_min_mensual", [])
    dist        = datos.get("distancia_urbana_km")
    ndvi_mean   = datos.get("ndvi_promedio_3a")

    if tmax_series:
        valores[6] = round(sum(tmax_series) / len(tmax_series), 1)
    if tmin_series:
        umbral_helada = 10 if cultivo == "café" else 15
        valores[7] = sum(1 for t in tmin_series if t < umbral_helada)
    if dist is not None:
        valores[15] = dist
    if ndvi_mean is not None:
        valores[9] = ndvi_mean

    resultados = []
    scores_por_grupo: dict[str, list[int]] = {g: [] for g in GRUPOS}

    for ind in INDICADORES:
        iid      = ind["id"]
        cultivo_k = cultivo if cultivo in ind["umbrales"] else "café"
        umbral   = (umbrales_custom or {}).get(iid) or ind["umbrales"][cultivo_k]
        valor    = valores.get(iid, 0.0)
        sc       = _score_indicador(valor, umbral, ind["direccion"])

        resultados.append({
            "id":           iid,
            "grupo":        ind["grupo"],
            "nombre":       ind["nombre"],
            "metrica":      ind["metrica"],
            "fuente":       ind["fuente"],
            "api":          ind["api"],
            "pendiente":    ind["pendiente"],
            "unidad":       ind["unidad"],
            "valor":        valor,
            "umbral_bajo":  umbral["bajo"],
            "umbral_medio": umbral["medio"],
            "umbral_alto":  umbral["alto"],
            "score_num":    sc,
            "score_label":  SCORE_LABEL[sc],
            "score_color":  SCORE_COLOR[sc],
            "score_text":   SCORE_TEXT[sc],
            "decision":     DECISION_RIESGO[sc],
        })
        scores_por_grupo[ind["grupo"]].append(sc)

    # Score por grupo → máximo del grupo (criterio conservador)
    por_grupo = {
        g: max(vs) if vs else 0
        for g, vs in scores_por_grupo.items()
    }

    # Score global → máximo entre grupos
    score_global = max(por_grupo.values()) if por_grupo else 0
    n_alto  = sum(1 for r in resultados if r["score_num"] == 2)
    n_medio = sum(1 for r in resultados if r["score_num"] == 1)
    n_bajo  = sum(1 for r in resultados if r["score_num"] == 0)

    return {
        "resultados":          resultados,
        "por_grupo":           por_grupo,
        "score_global":        score_global,
        "score_global_label":  SCORE_LABEL[score_global],
        "score_global_color":  SCORE_COLOR[score_global],
        "score_global_text":   SCORE_TEXT[score_global],
        "n_alto":  n_alto,
        "n_medio": n_medio,
        "n_bajo":  n_bajo,
    }
