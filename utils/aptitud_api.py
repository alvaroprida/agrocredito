"""
utils/aptitud_api.py

Calcula la aptitud de un predio a un cultivo usando las APIs públicas de
datos.gov.co (UPRA - Zonificación de aptitud por cultivo).

Metodología:
  1. Consulta la API Socrata del cultivo con intersects(the_geom, 'POLYGON ...')
  2. Calcula el área de cada subpolígono de aptitud que intersecta el predio
  3. Score ponderado por área:
       No apta       → 0.00
       Aptitud baja  → 0.33
       Aptitud media → 0.67
       Aptitud alta  → 1.00
  4. Categorización final:
       score ≥ 0.70 → Alta
       score ≥ 0.40 → Media
       score  < 0.40 → Baja

Cultivos sin API disponible en datos.gov.co:
  Durazno, Guayaba, Limón, Lulo, Mora, Naranja, Plátano, Uchuva
"""

import requests
import geopandas as gpd
from shapely.geometry import shape
from shapely import wkt as swkt

# ── Mapping desplegable → endpoint .json de datos.gov.co ─────────────────────
# Para cultivos con Semestre I y II se usa Semestre II.

CULTIVO_API_MAP: dict[str, str] = {
    "Aguacate (Hass)": "https://www.datos.gov.co/resource/tx7u-frn2.json",
    "Cacao":           "https://www.datos.gov.co/resource/jdjx-qer4.json",
    "Café":            "https://www.datos.gov.co/resource/kwvf-nwea.json",
    "Cebolla":         "https://www.datos.gov.co/resource/nxvg-ufyf.json",  # Sem II
    "Fresa":           "https://www.datos.gov.co/resource/emsg-94di.json",
    "Granadilla":      "https://www.datos.gov.co/resource/aikj-ub3k.json",
    "Gulupa":          "https://www.datos.gov.co/resource/q6xp-whkm.json",
    "Maíz":            "https://www.datos.gov.co/resource/tzga-4zse.json",  # Sem II
    "Mango":           "https://www.datos.gov.co/resource/xt32-m7dh.json",
    "Maracuyá":        "https://www.datos.gov.co/resource/hxs5-w7gt.json",
    "Papa":            "https://www.datos.gov.co/resource/s455-c4e6.json",  # Sem II
    "Piña":            "https://www.datos.gov.co/resource/8fa5-z4v3.json",
}

# ── Pesos por categoría de aptitud ────────────────────────────────────────────
APTITUD_WEIGHTS: dict[str, float] = {
    "No apta":      0.00,
    "Aptitud baja": 0.33,
    "Aptitud media":0.67,
    "Aptitud alta": 1.00,
}

# ── Umbrales de categorización del score bruto ───────────────────────────────
_SCORE_THRESHOLDS = [(0.70, "Alta"), (0.40, "Media")]  # else → Baja


def score_to_category(score: float) -> str:
    for threshold, label in _SCORE_THRESHOLDS:
        if score >= threshold:
            return label
    return "Baja"


# ── Consulta a la API ─────────────────────────────────────────────────────────

def _query_aptitud_polygons(api_url: str, predio_geom, timeout: int = 45) -> list[tuple]:
    """
    Consulta Socrata con intersects() y devuelve lista de
    (shapely_geom, aptitud_label) para todos los polígonos que intersectan
    el predio.
    """
    # Simplificar para no superar límites de URL (tolerancia ~11 m)
    geom_simple = predio_geom.simplify(0.0001, preserve_topology=True)

    params = {
        "$where":  f"intersects(the_geom,'{geom_simple.wkt}')",
        "$select": "aptitud,the_geom",
        "$limit":  500,
    }
    with requests.Session() as session:
        resp = session.get(api_url, params=params, timeout=timeout)
        resp.raise_for_status()
        rows = resp.json()

    results = []
    for row in rows:
        aptitud  = row.get("aptitud")
        geom_raw = row.get("the_geom")
        if not aptitud or not geom_raw:
            continue
        try:
            geom = shape(geom_raw) if isinstance(geom_raw, dict) else swkt.loads(geom_raw)
            results.append((geom, aptitud))
        except Exception:
            continue
    return results


# ── Función principal ─────────────────────────────────────────────────────────

def get_aptitud_api(gdf_predio: gpd.GeoDataFrame, cultivo: str) -> dict | None:
    """
    Calcula el score de aptitud del predio para el cultivo indicado.

    Retorna None si no existe API para el cultivo.
    Retorna dict con:
        score     float          score bruto ponderado (0–1)
        category  str            "Alta" / "Media" / "Baja"
        gdf       GeoDataFrame   polígonos de intersección
                                 (columnas: aptitud, area_ha, pct_predio)
        error     str | None     mensaje de error si ocurrió alguno
    """
    api_url = CULTIVO_API_MAP.get(cultivo)
    if api_url is None:
        return None  # cultivo sin API disponible

    predio_geom = gdf_predio.geometry.iloc[0]
    predio_area = predio_geom.area  # grados² — sólo para ratios

    try:
        polygons = _query_aptitud_polygons(api_url, predio_geom)
    except Exception as exc:
        return {"score": None, "category": None, "gdf": None, "error": str(exc)}

    if not polygons:
        return {
            "score": None, "category": None, "gdf": None,
            "error": "La API no devolvió polígonos de aptitud para este predio.",
        }

    records, geometries = [], []
    weighted_sum     = 0.0
    total_inter_area = 0.0

    for apt_geom, aptitud in polygons:
        try:
            inter = predio_geom.intersection(apt_geom)
            if inter.is_empty:
                continue
            inter_area = inter.area
            weight      = APTITUD_WEIGHTS.get(aptitud, 0.0)
            weighted_sum     += inter_area * weight
            total_inter_area += inter_area
            area_ha  = inter_area * (111_320 ** 2) / 10_000
            pct      = (inter_area / predio_area * 100) if predio_area > 0 else 0.0
            records.append({
                "aptitud":    aptitud,
                "area_ha":    round(area_ha, 3),
                "pct_predio": round(pct, 1),
            })
            geometries.append(inter)
        except Exception:
            continue

    if total_inter_area == 0 or not records:
        return {
            "score": None, "category": None, "gdf": None,
            "error": "No se pudo calcular la intersección de aptitud.",
        }

    score    = weighted_sum / total_inter_area
    category = score_to_category(score)
    gdf      = gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")

    return {
        "score":    round(score, 3),
        "category": category,
        "gdf":      gdf,
        "error":    None,
    }
