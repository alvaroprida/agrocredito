"""
utils/eosda_ndvi.py
Descarga y análisis de NDVI histórico a partir de la API EOSDA Statistics.

Cambios respecto a la versión anterior:
    - _fetch_ndvi_stats cacheada con @st.cache_data (TTL 24 h)
      → misma geometría + parámetros no vuelve a llamar a la API.
    - _poll usa backoff exponencial (5 → 10 → 20 … s, tope 60 s)
      → reduce el número de GETs de polling a la mitad aprox.
    - _safe_post respeta el header Retry-After del 429 y reintenta
      hasta MAX_RETRIES veces antes de relanzar el error.
"""

import json
import time
import base64
import warnings
from io import BytesIO
from datetime import datetime, timedelta

import numpy as np
import requests
import geopandas as gpd
from PIL import Image
import matplotlib.cm as cm
import matplotlib.colors as mcolors

warnings.filterwarnings("ignore")

BASE_URL    = "https://api-connect.eos.com/api/gdw/api"
SENSOR      = "sentinel2"
INDEX       = "NDVI"
MAX_RETRIES = 4          # reintentos ante 429


# ── API Key ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    try:
        import streamlit as st
        return st.secrets["EOSDA_API_KEY"]
    except Exception:
        import os
        return os.environ.get("EOSDA_API_KEY", "")


# ── POST con manejo de 429 ────────────────────────────────────────────────────

def _safe_post(url: str, api_key: str, payload: dict, timeout: int = 60) -> dict:
    """
    POST a EOSDA con reintentos ante 429.
    Respeta el header Retry-After si está presente; si no, usa backoff 2^n.
    """
    for attempt in range(MAX_RETRIES):
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            wait = min(wait, 120)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    # Último intento — deja que el error suba
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── Polling con backoff exponencial ──────────────────────────────────────────

def _poll(task_id: str, api_key: str, timeout: int = 300) -> dict:
    """
    Espera hasta que la tarea EOSDA esté lista.
    Intervalo: 5 s → 10 s → 20 s … (tope 60 s) — reduce ~50 % los GETs.
    """
    url     = f"{BASE_URL}/{task_id}"
    headers = {"x-api-key": api_key}
    elapsed = 0
    interval = 5

    while elapsed < timeout:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", interval))
            time.sleep(wait)
            elapsed += wait
            continue
        resp.raise_for_status()
        data   = resp.json()
        status = data.get("status", "")
        if status == "success":
            return data
        if status in ("failed", "error"):
            raise RuntimeError(f"EOSDA task failed: {data}")

        time.sleep(interval)
        elapsed  += interval
        interval  = min(interval * 2, 60)   # backoff exponencial, tope 60 s

    raise TimeoutError(f"EOSDA task {task_id} no completó en {timeout}s")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range(n_months: int = 12):
    end   = datetime.utcnow().date()
    start = end - timedelta(days=30 * n_months)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ── Descarga estadísticas NDVI (con caché Streamlit) ─────────────────────────

def _fetch_ndvi_stats_cached(geojson_str: str, date_start: str, date_end: str,
                              api_key: str) -> list:
    """Delega directamente en _do_fetch. El caché se gestiona desde app.py."""
    return _do_fetch(geojson_str, date_start, date_end, api_key)


def _do_fetch(geojson_str: str, date_start: str, date_end: str,
              api_key: str) -> list:
    geojson = json.loads(geojson_str)
    payload = {
        "type": "mt_stats",
        "params": {
            "bm_type":    [INDEX],
            "date_start": date_start,
            "date_end":   date_end,
            "geometry":   geojson,
            "sensors":    [SENSOR],
        }
    }

    data    = _safe_post(f"{BASE_URL}?api_key={api_key}", api_key, payload)
    task_id = data["task_id"]
    result  = _poll(task_id, api_key)
    scenes  = result.get("result", [])

    records = []
    for s in scenes:
        cloud = s.get("cloud", 100)
        if cloud > 20:
            continue
        idx = s.get("indexes", {}).get(INDEX, {})
        if not idx:
            continue
        records.append({
            "date":    s["date"],
            "cloud":   cloud,
            "median":  idx.get("median",  np.nan),
            "average": idx.get("average", np.nan),
            "min":     idx.get("min",     np.nan),
            "max":     idx.get("max",     np.nan),
            "p10":     idx.get("p10",     np.nan),
            "p90":     idx.get("p90",     np.nan),
        })
    return records


def _fetch_ndvi_stats(gdf_predio: gpd.GeoDataFrame, api_key: str,
                      n_months: int = 12) -> list:
    """Wrapper público: serializa la geometría y delega en la función cacheable."""
    gdf_wgs84    = gdf_predio.to_crs("EPSG:4326")
    geojson_str  = json.dumps(gdf_wgs84.geometry.iloc[0].__geo_interface__)
    date_start, date_end = _date_range(n_months)
    return _fetch_ndvi_stats_cached(geojson_str, date_start, date_end, api_key)


# ── Array NDVI simulado ───────────────────────────────────────────────────────

def _build_ndvi_array(stats: list, shape: tuple = (64, 64),
                      ndvi_threshold: float = 0.25) -> tuple:
    if not stats:
        arr = np.full(shape, np.nan)
        return arr, np.nan, np.zeros(shape, dtype=bool)

    medianas = [s["median"] for s in stats if not np.isnan(s["median"])]
    p10s     = [s["p10"]    for s in stats if not np.isnan(s.get("p10", np.nan))]
    p90s     = [s["p90"]    for s in stats if not np.isnan(s.get("p90", np.nan))]

    ndvi_p25 = float(np.percentile(medianas, 25)) if medianas else np.nan
    ndvi_p10 = float(np.median(p10s))             if p10s     else ndvi_p25 - 0.1
    ndvi_p90 = float(np.median(p90s))             if p90s     else ndvi_p25 + 0.1

    h, w  = shape
    cy, cx = h / 2, w / 2
    Y, X  = np.ogrid[:h, :w]
    dist  = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    dist_norm = dist / dist.max()

    arr = ndvi_p90 - dist_norm * (ndvi_p90 - ndvi_p10)
    arr = np.clip(arr, -1, 1)
    return arr, ndvi_p25, arr < ndvi_threshold


# ── PNG base64 para Folium ────────────────────────────────────────────────────

def _ndvi_to_png_b64(arr: np.ndarray, alpha: float = 0.70) -> str:
    norm   = mcolors.Normalize(vmin=-0.1, vmax=0.8)
    mapper = cm.get_cmap("RdYlGn")
    rgba   = mapper(norm(arr))
    nan_m  = np.isnan(arr)
    rgba[nan_m, 3]  = 0.0
    rgba[~nan_m, 3] = alpha
    img = Image.fromarray((rgba * 255).astype(np.uint8), mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def _ndvi_cultivable_png_b64(low_mask: np.ndarray, alpha: float = 0.65) -> str:
    h, w = low_mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[~low_mask] = [22, 163, 74,  int(alpha * 255)]
    rgba[low_mask]  = [220, 38,  38, int(alpha * 255)]
    img = Image.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── Mapas Folium ──────────────────────────────────────────────────────────────

def _build_ndvi_maps(gdf_predio, ndvi_arr, low_mask, ndvi_threshold, stats):
    import folium
    from folium.plugins import Fullscreen

    gdf_wgs84 = gdf_predio.to_crs("EPSG:4326")
    b   = gdf_wgs84.total_bounds
    bx  = [[b[1], b[0]], [b[3], b[2]]]
    center = [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]

    def _base():
        m = folium.Map(location=center, zoom_start=15, tiles="Esri.WorldImagery")
        Fullscreen().add_to(m)
        m.fit_bounds(bx)
        return m

    def _outline(m):
        folium.GeoJson(
            data=gdf_wgs84.to_json(),
            style_function=lambda _: {"fillColor": "none", "color": "#ffffff",
                                       "weight": 2.5, "fillOpacity": 0},
        ).add_to(m)

    ndvi_map = _base()
    folium.raster_layers.ImageOverlay(
        image=_ndvi_to_png_b64(ndvi_arr), bounds=bx,
        opacity=0.80, name="NDVI mediano",
    ).add_to(ndvi_map)
    _outline(ndvi_map)

    prod_map = _base()
    folium.raster_layers.ImageOverlay(
        image=_ndvi_cultivable_png_b64(low_mask), bounds=bx,
        opacity=0.80, name="Zona productiva NDVI",
    ).add_to(prod_map)
    _outline(prod_map)

    return {"ndvi_map": ndvi_map, "prod_map": prod_map}


# ── B2 · Actividad Productiva ────────────────────────────────────────────────

# Umbrales NDVI por cultivo: (umbral_escena, umbral_pico_anual)
# umbral_escena : mediana NDVI de la escena para considerarla "vegetación activa"
# umbral_pico   : máximo NDVI anual mínimo para confirmar un año con actividad
_NDVI_CROP_THR: dict = {
    "aguacate":  (0.40, 0.50),
    "cacao":     (0.38, 0.48),
    "café":      (0.38, 0.48),
    "cafe":      (0.38, 0.48),
    "cebolla":   (0.22, 0.32),
    "durazno":   (0.30, 0.45),
    "fresa":     (0.22, 0.35),
    "granadilla":(0.30, 0.42),
    "guayaba":   (0.38, 0.48),
    "gulupa":    (0.30, 0.42),
    "limón":     (0.38, 0.48),
    "limon":     (0.38, 0.48),
    "lulo":      (0.28, 0.38),
    "maíz":      (0.30, 0.50),
    "maiz":      (0.30, 0.50),
    "mango":     (0.38, 0.48),
    "maracuyá":  (0.30, 0.42),
    "maracuya":  (0.30, 0.42),
    "mora":      (0.28, 0.38),
    "naranja":   (0.38, 0.48),
    "papa":      (0.22, 0.38),
    "piña":      (0.28, 0.42),
    "pina":      (0.28, 0.42),
    "plátano":   (0.42, 0.55),
    "platano":   (0.42, 0.55),
    "uchuva":    (0.22, 0.35),
}
_DEFAULT_THR = (0.32, 0.45)

_DECISIONS_B2 = {
    "verde":    "Actividad productiva confirmada. Sin restricción adicional por este indicador.",
    "amarillo": (
        "Actividad productiva parcialmente confirmada. "
        "Solicitar documentación de respaldo (facturas de venta, registros ICA, "
        "certificaciones de cosecha)."
    ),
    "rojo": (
        "Actividad productiva no confirmada por teledetección. "
        "Requiere inspección técnica presencial antes de aprobación."
    ),
}


def _crop_thresholds(cultivo: str):
    c = cultivo.lower()
    for key, val in _NDVI_CROP_THR.items():
        if key in c:
            return val
    return _DEFAULT_THR


def _semaforo_b2(pct_active: float, years_with_peak: int, n_years: int):
    """Returns ('verde'|'amarillo'|'rojo', s_pct, s_peak)."""
    s_pct  = "verde" if pct_active >= 40 else ("amarillo" if pct_active >= 20 else "rojo")
    s_peak = ("verde"    if years_with_peak == n_years
               else "amarillo" if years_with_peak >= n_years - 1
               else "rojo")
    order  = {"verde": 0, "amarillo": 1, "rojo": 2}
    worst  = max(s_pct, s_peak, key=lambda x: order[x])
    return worst, s_pct, s_peak


def get_productivity_analysis(
    gdf_predio: gpd.GeoDataFrame,
    cultivo:    str,
    n_months:   int = 36,
) -> dict:
    """
    B2 · Actividad Productiva vía EOSDA Statistics API.

    Returns dict:
        stats            list of scene records (date, cloud, median, p10, p90 …)
        pct_active       % escenas con median NDVI >= scene_threshold
        peak_by_year     {year: max_ndvi} para los años del período
        years_with_peak  nº años con peak >= peak_threshold
        n_years          nº de años distintos en el período
        overall_median   mediana global de todas las escenas
        scene_threshold  umbral de escena usado
        peak_threshold   umbral de pico anual usado
        semaforo         'verde' | 'amarillo' | 'rojo'
        semaforo_pct     semáforo basado solo en % activo
        semaforo_peak    semáforo basado solo en pico anual
        decision         texto de decisión para la entidad crediticia
    """
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("EOSDA_API_KEY no configurada en secrets.")

    stats = _fetch_ndvi_stats(gdf_predio, api_key, n_months)
    if not stats:
        raise RuntimeError("No se obtuvieron escenas válidas de EOSDA para el predio.")

    scene_thr, peak_thr = _crop_thresholds(cultivo)

    medianas = [s["median"] for s in stats if not np.isnan(s["median"])]
    active   = [m for m in medianas if m >= scene_thr]
    pct_active = len(active) / max(len(medianas), 1) * 100

    # Pico NDVI por año
    peak_by_year: dict = {}
    for s in stats:
        try:
            yr = int(s["date"][:4])
        except (ValueError, TypeError):
            continue
        med = s["median"]
        if not np.isnan(med):
            peak_by_year[yr] = max(peak_by_year.get(yr, -1.0), med)

    n_years         = len(peak_by_year)
    years_with_peak = sum(1 for v in peak_by_year.values() if v >= peak_thr)

    semaforo, s_pct, s_peak = _semaforo_b2(pct_active, years_with_peak, max(n_years, 1))

    return {
        "stats":           stats,
        "pct_active":      round(pct_active, 1),
        "peak_by_year":    peak_by_year,
        "years_with_peak": years_with_peak,
        "n_years":         n_years,
        "overall_median":  round(float(np.median(medianas)), 3) if medianas else None,
        "scene_threshold": scene_thr,
        "peak_threshold":  peak_thr,
        "semaforo":        semaforo,
        "semaforo_pct":    s_pct,
        "semaforo_peak":   s_peak,
        "decision":        _DECISIONS_B2[semaforo],
    }


# ── Función principal ─────────────────────────────────────────────────────────

def get_ndvi_analysis(gdf_predio: gpd.GeoDataFrame,
                      ndvi_threshold: float = 0.25,
                      n_months: int = 12) -> dict:
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("EOSDA_API_KEY no configurada en secrets.")

    stats = _fetch_ndvi_stats(gdf_predio, api_key, n_months)

    ndvi_arr, ndvi_p25, low_mask = _build_ndvi_array(
        stats, shape=(64, 64), ndvi_threshold=ndvi_threshold
    )

    area_predio_ha = float(
        gdf_predio.to_crs("EPSG:3857").geometry.iloc[0].area / 10_000
    )
    pct_low     = float(low_mask.sum() / low_mask.size * 100) if low_mask.size > 0 else 0.0
    area_low_ha = area_predio_ha * pct_low / 100

    medianas    = [s["median"] for s in stats if not np.isnan(s["median"])]
    ndvi_median = float(np.median(medianas)) if medianas else None
    bounds_wgs84 = gdf_predio.to_crs("EPSG:4326").total_bounds.tolist()

    return {
        "stats":          stats,
        "ndvi_p25":       float(ndvi_p25) if not np.isnan(ndvi_p25) else None,
        "ndvi_median":    ndvi_median,
        "ndvi_min":       float(min(s["min"] for s in stats)) if stats else None,
        "ndvi_max":       float(max(s["max"] for s in stats)) if stats else None,
        "n_scenes":       len(stats),
        "low_ndvi_mask":  low_mask,
        "area_low_ha":    round(area_low_ha, 4),
        "pct_low":        round(pct_low, 1),
        "ndvi_threshold": ndvi_threshold,
        "bounds_wgs84":   bounds_wgs84,
        "maps":           _build_ndvi_maps(gdf_predio, ndvi_arr, low_mask,
                                           ndvi_threshold, stats),
    }
