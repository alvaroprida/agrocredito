"""
utils/infraestructura.py

Calcula indicadores de infraestructura para un predio:
  1. Distancia por carretera al centro urbano más cercano (Overpass + OSRM)
  2. Distancia en línea recta a la vía transitable más cercana (Overpass + Shapely)

APIs utilizadas (gratuitas, sin API key):
  - Overpass API  → centros urbanos y geometría de vías (OSM)
  - OSRM          → routing por carretera (router.project-osrm.org)
"""

import time
import requests
import polyline as _polyline
import geopandas as gpd
from shapely.geometry import Point, LineString

# ── Parámetros ────────────────────────────────────────────────────────────────
OVERPASS_URL        = "https://overpass-api.de/api/interpreter"
OSRM_URL            = "http://router.project-osrm.org/route/v1/driving"

RADIO_BUSQUEDA_KM   = 80
N_CANDIDATOS        = 5
PAUSA_OSRM_SEG      = 0.5
PAUSA_OVERPASS_SEG  = 5
REINTENTOS_OVERPASS = 3
RADIO_CARRETERA_KM  = 5

HIGHWAY_TYPES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|track"

# ── Helper Overpass con reintentos ────────────────────────────────────────────

def _overpass_get(query: str) -> list:
    headers = {
        "User-Agent": "AgroCredito/1.0",
        "Accept":     "application/json",
    }
    for intento in range(REINTENTOS_OVERPASS):
        resp = requests.get(OVERPASS_URL, params={"data": query},
                            headers=headers, timeout=30)
        if resp.status_code == 429:
            espera = PAUSA_OVERPASS_SEG * (2 ** intento)
            time.sleep(espera)
            continue
        resp.raise_for_status()
        return resp.json().get("elements", [])
    raise RuntimeError("Overpass: límite de reintentos alcanzado (429).")


# ════════════════════════════════════════════════════════════════════════════════
#  1 · DISTANCIA AL CENTRO URBANO MÁS CERCANO
# ════════════════════════════════════════════════════════════════════════════════

def _get_centros_urbanos(lat: float, lon: float) -> list[dict]:
    radio_m = RADIO_BUSQUEDA_KM * 1000
    query = f"""
    [out:json][timeout:25];
    (
      node["place"~"^(city|town|village)$"]
         (around:{radio_m},{lat},{lon});
    );
    out body;
    """
    elementos = _overpass_get(query)
    centros = []
    for e in elementos:
        clat, clon = e["lat"], e["lon"]
        dist = ((lat - clat) ** 2 + (lon - clon) ** 2) ** 0.5 * 111.32
        centros.append({
            "nombre":        e.get("tags", {}).get("name", "Sin nombre"),
            "tipo":          e.get("tags", {}).get("place", ""),
            "lat":           clat,
            "lon":           clon,
            "dist_recta_km": round(dist, 2),
        })
    centros.sort(key=lambda x: x["dist_recta_km"])
    return centros[:N_CANDIDATOS]


def _get_ruta_osrm(lat1, lon1, lat2, lon2) -> dict | None:
    url = f"{OSRM_URL}/{lon1},{lat1};{lon2},{lat2}"
    try:
        resp = requests.get(url, params={"overview": "full", "geometries": "polyline"},
                            timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        ruta = data["routes"][0]
        coords = _polyline.decode(ruta["geometry"])
        return {
            "distancia_km": round(ruta["distance"] / 1000, 2),
            "duracion_min": round(ruta["duration"] / 60, 1),
            "coords":       coords,
        }
    except Exception:
        return None


def get_distancia_centro_urbano(lat: float, lon: float) -> dict | None:
    """
    Devuelve el centro urbano más cercano por carretera y las métricas de ruta.

    Retorna dict con:
        nombre          str     nombre del centro urbano
        tipo            str     city / town / village
        distancia_km    float   km por carretera
        duracion_min    float   minutos estimados en vehículo
        dist_recta_km   float   km en línea recta (referencia)
        coords          list    polyline de la ruta [(lat,lon), ...]
    Retorna None si no se encuentra ruta.
    """
    candidatos = _get_centros_urbanos(lat, lon)
    if not candidatos:
        return None

    # Pausa entre consulta Overpass y llamadas OSRM
    time.sleep(PAUSA_OVERPASS_SEG)

    mejor = None
    for c in candidatos:
        time.sleep(PAUSA_OSRM_SEG)
        ruta = _get_ruta_osrm(lat, lon, c["lat"], c["lon"])
        if ruta is None:
            continue
        c["ruta"] = ruta
        if mejor is None or ruta["distancia_km"] < mejor["ruta"]["distancia_km"]:
            mejor = c

    if mejor is None:
        return None

    r = mejor["ruta"]
    return {
        "nombre":        mejor["nombre"],
        "tipo":          mejor["tipo"],
        "distancia_km":  r["distancia_km"],
        "duracion_min":  r["duracion_min"],
        "dist_recta_km": mejor["dist_recta_km"],
        "coords":        r["coords"],
    }


# ════════════════════════════════════════════════════════════════════════════════
#  2 · DISTANCIA EN LÍNEA RECTA A LA VÍA MÁS CERCANA
# ════════════════════════════════════════════════════════════════════════════════

def get_distancia_via(lat: float, lon: float) -> dict | None:
    """
    Distancia en línea recta (perpendicular) desde el punto al segmento
    vial más cercano dentro de RADIO_CARRETERA_KM.

    Retorna dict con:
        nombre          str     nombre de la vía (o 'Sin nombre')
        tipo            str     highway type (primary, secondary, track, …)
        distancia_m     float   metros
        distancia_km    float   km
    Retorna None si no se encuentra vía.
    """
    radio_m = RADIO_CARRETERA_KM * 1000
    query = f"""
    [out:json][timeout:25];
    way["highway"~"^({HIGHWAY_TYPES})$"]
      (around:{radio_m},{lat},{lon});
    out geom;
    """
    elementos = _overpass_get(query)
    if not elementos:
        return None

    predio_wgs = Point(lon, lat)
    predio_m = (
        gpd.GeoDataFrame(geometry=[predio_wgs], crs="EPSG:4326")
        .to_crs("EPSG:3857")
        .geometry.iloc[0]
    )

    mejor_dist = float("inf")
    mejor_via  = None

    for elem in elementos:
        nodos = elem.get("geometry", [])
        if len(nodos) < 2:
            continue
        coords = [(g["lon"], g["lat"]) for g in nodos]
        linea_m = (
            gpd.GeoDataFrame(geometry=[LineString(coords)], crs="EPSG:4326")
            .to_crs("EPSG:3857")
            .geometry.iloc[0]
        )
        dist = predio_m.distance(linea_m)
        if dist < mejor_dist:
            mejor_dist = dist
            mejor_via  = {
                "nombre":     elem.get("tags", {}).get("name", "Sin nombre"),
                "tipo":       elem.get("tags", {}).get("highway", ""),
                "distancia_m":  round(dist, 1),
                "distancia_km": round(dist / 1000, 3),
            }

    return mejor_via
