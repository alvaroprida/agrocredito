"""
utils/gee_ndvi.py

Computes per-pixel NDVI P25 from Sentinel-2 SR via Google Earth Engine.
Authentication uses a service account stored in st.secrets["gee"].
Returns the same dict structure as the former stac_ndvi.get_ndvi_stac().
"""

import json
import warnings
import numpy as np
import geopandas as gpd
import requests
import base64
from io import BytesIO
from datetime import datetime, timedelta

import rasterio
from PIL import Image
import matplotlib
import matplotlib.colors as mcolors
import folium
from folium.plugins import Fullscreen
import ee

warnings.filterwarnings("ignore")

GEE_PROJECT = "agricolombia"
_GEE_INITIALIZED = False


# ── Authentication ────────────────────────────────────────────────────────────

def _init_gee():
    global _GEE_INITIALIZED
    if _GEE_INITIALIZED:
        return

    import streamlit as st

    sa_json = st.secrets.get("gee", {}).get("service_account_json")
    # Proyecto GCP/Earth Engine: configurable por secrets (el cliente usa el suyo
    # sin tocar código). Prioridad: [gee] project → project dentro del JSON → default.
    proj = st.secrets.get("gee", {}).get("project") or GEE_PROJECT
    if sa_json:
        sa_dict = json.loads(sa_json) if isinstance(sa_json, str) else dict(sa_json)
        proj = st.secrets.get("gee", {}).get("project") or sa_dict.get("project_id") or GEE_PROJECT
        credentials = ee.ServiceAccountCredentials(
            email=sa_dict["client_email"],
            key_data=json.dumps(sa_dict),
        )
        ee.Initialize(credentials=credentials, project=proj)
    else:
        # Fallback local: requiere `earthengine authenticate` previo
        try:
            ee.Initialize(project=proj)
        except Exception:
            raise RuntimeError(
                "No se encontraron credenciales GEE. "
                "Añade [gee] service_account_json en los Secrets de Streamlit Cloud, "
                "o ejecuta `earthengine authenticate` localmente."
            )

    _GEE_INITIALIZED = True


# ── Cloud masking (SCL band — Sentinel-2 SR Harmonized) ──────────────────────

def _mask_s2_clouds(image):
    scl = image.select("SCL")
    bad = (scl.eq(3).Or(scl.eq(8)).Or(scl.eq(9))
               .Or(scl.eq(10)).Or(scl.eq(11)).Or(scl.eq(1)))
    return image.updateMask(bad.Not())


# ── PNG helpers for Folium ────────────────────────────────────────────────────

def _ndvi_png(arr: np.ndarray, alpha: float = 0.80) -> str:
    norm  = mcolors.Normalize(vmin=-0.1, vmax=0.8, clip=True)
    rgba  = matplotlib.colormaps["RdYlGn"](norm(arr))
    nan_m = np.isnan(arr)
    rgba[nan_m,  3] = 0.0
    rgba[~nan_m, 3] = alpha
    img = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")
    buf = BytesIO(); img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _binary_png(low_mask: np.ndarray, nan_mask: np.ndarray,
                alpha: float = 0.75) -> str:
    h, w  = low_mask.shape
    rgba  = np.zeros((h, w, 4), np.uint8)
    rgba[~nan_mask & ~low_mask] = [22,  163, 74,  int(alpha * 255)]
    rgba[~nan_mask &  low_mask] = [220, 38,  38,  int(alpha * 255)]
    img = Image.fromarray(rgba, "RGBA")
    buf = BytesIO(); img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── Folium maps ───────────────────────────────────────────────────────────────

def _build_maps(gdf_predio: gpd.GeoDataFrame, ndvi_p25: np.ndarray,
                low_mask: np.ndarray, bounds_wgs84: list) -> dict:
    gdf4 = gdf_predio.to_crs("EPSG:4326")
    b    = bounds_wgs84
    bx   = [[b[1], b[0]], [b[3], b[2]]]
    ctr  = [gdf4.geometry.iloc[0].centroid.y, gdf4.geometry.iloc[0].centroid.x]

    def _base():
        m = folium.Map(location=ctr, zoom_start=15, tiles="Esri.WorldImagery")
        Fullscreen().add_to(m); m.fit_bounds(bx)
        return m

    def _outline(m):
        folium.GeoJson(
            data=gdf4.to_json(),
            style_function=lambda _: {"fillColor": "none", "color": "#ffffff",
                                      "weight": 2.5, "fillOpacity": 0},
        ).add_to(m)

    nan_mask = np.isnan(ndvi_p25)

    ndvi_map = _base()
    folium.raster_layers.ImageOverlay(
        image=_ndvi_png(ndvi_p25), bounds=bx, opacity=0.85
    ).add_to(ndvi_map)
    _outline(ndvi_map)

    prod_map = _base()
    folium.raster_layers.ImageOverlay(
        image=_binary_png(low_mask, nan_mask), bounds=bx, opacity=0.85
    ).add_to(prod_map)
    _outline(prod_map)

    return {"ndvi_map": ndvi_map, "prod_map": prod_map}


# ── GEE download helper ───────────────────────────────────────────────────────

def _download_image(image: ee.Image, band: str, roi: ee.Geometry,
                    scale: float) -> np.ndarray:
    url = image.select(band).getDownloadURL({
        "region": roi,
        "scale":  scale,
        "format": "GEO_TIFF",
        "crs":    "EPSG:4326",
    })
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    with rasterio.open(BytesIO(resp.content)) as src:
        arr = src.read(1).astype(np.float32)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
    # Clamp to valid NDVI range; out-of-range values are nodata artefacts
    arr[(arr < -1) | (arr > 1)] = np.nan
    return arr


# ── B2 · Actividad Productiva (serie NDVI por escena) ─────────────────────────

def get_productivity_analysis_gee(
    gdf_predio:    gpd.GeoDataFrame,
    cultivo:       str,
    n_years:       int   = 3,
    max_cloud_pct: float = 20.0,
    res_m:         float = 10.0,
) -> dict:
    """
    B2 · Actividad Productiva vía Google Earth Engine (Sentinel-2 SR Harmonized).

    Calcula la mediana NDVI por escena sobre el predio durante n_years y aplica
    el mismo scoring por cultivo que la versión anterior. Devuelve el mismo dict
    de resultado que consume la app (clave por clave).
    """
    from utils.eosda_ndvi import score_b2   # scoring agnóstico a la fuente

    _init_gee()

    gdf4 = gdf_predio.to_crs("EPSG:4326")
    roi  = ee.Geometry(gdf4.geometry.iloc[0].__geo_interface__)

    end_dt   = datetime.utcnow()
    start_dt = end_dt - timedelta(days=365 * n_years)

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud_pct))
        .map(_mask_s2_clouds)
        .map(lambda img: img.normalizedDifference(["B8", "B4"])
                           .rename("NDVI")
                           .copyProperties(img, ["system:time_start"]))
    )

    # Mediana NDVI por escena sobre el predio (una sola llamada getInfo)
    def _scene_feature(img):
        img = ee.Image(img)
        val = img.reduceRegion(ee.Reducer.median(), roi, res_m, maxPixels=1e8).get("NDVI")
        return ee.Feature(None, {"date": img.date().format("YYYY-MM-dd"), "median": val})

    raw = ee.FeatureCollection(s2.map(_scene_feature)).getInfo()["features"]

    stats = sorted(
        [{"date": f["properties"]["date"], "median": float(f["properties"]["median"])}
         for f in raw
         if f["properties"].get("median") is not None
         and not np.isnan(f["properties"]["median"])],
        key=lambda x: x["date"],
    )
    if not stats:
        raise RuntimeError(
            "No se obtuvieron escenas Sentinel-2 válidas para el predio en este período. "
            "Intente ampliar el período o aumentar el umbral de nubosidad."
        )

    return score_b2(stats, cultivo)


# ── Main function ─────────────────────────────────────────────────────────────

def get_ndvi_gee(
    gdf_predio:     gpd.GeoDataFrame,
    ndvi_threshold: float = 0.25,
    n_years:        int   = 3,
    max_cloud_pct:  float = 20.0,
    res_m:          float = 10.0,
    progress_cb             = None,   # callable(done, total, msg)
) -> dict:
    """
    Downloads Sentinel-2 SR NDVI from Google Earth Engine and computes
    the P25 per pixel across n_years of cloud-free scenes.

    Returns the same dict structure as get_ndvi_stac():
        ndvi_p25, ndvi_p25_mean, ndvi_median, low_ndvi_mask,
        n_scenes_used, n_scenes_total, ndvi_min/max,
        area_low_ha, pct_low, ndvi_threshold,
        scene_stats, bounds_wgs84, maps
    """
    _init_gee()

    if progress_cb:
        progress_cb(0, 5, "Conectando con Google Earth Engine…")

    # Region of interest
    gdf4   = gdf_predio.to_crs("EPSG:4326")
    roi    = ee.Geometry(gdf4.geometry.iloc[0].__geo_interface__)
    bounds = list(gdf4.total_bounds)  # [minx, miny, maxx, maxy]

    end_dt   = datetime.utcnow()
    start_dt = end_dt - timedelta(days=365 * n_years)
    date_end   = end_dt.strftime("%Y-%m-%d")
    date_start = start_dt.strftime("%Y-%m-%d")

    if progress_cb:
        progress_cb(1, 5, "Filtrando colección Sentinel-2…")

    # Build NDVI collection
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud_pct))
        .map(_mask_s2_clouds)
        .map(lambda img: img.normalizedDifference(["B8", "B4"])
                           .rename("NDVI")
                           .copyProperties(img, ["system:time_start"]))
    )

    n_total = s2.size().getInfo()
    if n_total == 0:
        raise RuntimeError(
            "No se encontraron imágenes Sentinel-2 para este predio y período. "
            "Intente ampliar el período o aumentar el umbral de nubosidad."
        )

    if progress_cb:
        progress_cb(2, 5, f"Calculando P25 sobre {n_total} imágenes…")

    # Composites
    ndvi_p25_img = s2.reduce(ee.Reducer.percentile([25])).rename("NDVI_P25")
    ndvi_med_img = s2.reduce(ee.Reducer.median()).rename("NDVI_median")

    # Monthly mean NDVI time series — one getInfo() call, computed server-side
    if progress_cb:
        progress_cb(3, 5, f"Calculando serie temporal mensual ({n_years * 12} meses)…")

    def _monthly_mean(m_offset):
        m_offset  = ee.Number(m_offset)
        start     = ee.Date(date_start).advance(m_offset, "month")
        end       = start.advance(1, "month")
        monthly   = s2.filterDate(start, end)
        mean_val  = ee.Algorithms.If(
            monthly.size().gt(0),
            monthly.mean()
                   .reduceRegion(ee.Reducer.mean(), roi, res_m, maxPixels=1e8)
                   .get("NDVI", None),
            None,
        )
        return ee.Feature(None, {"date": start.format("YYYY-MM-01"), "mean_ndvi": mean_val})

    monthly_fc  = ee.FeatureCollection(
        ee.List.sequence(0, n_years * 12 - 1).map(_monthly_mean)
    )
    monthly_raw = monthly_fc.getInfo()["features"]
    scene_stats = sorted(
        [{"date": f["properties"]["date"], "mean_ndvi": float(f["properties"]["mean_ndvi"])}
         for f in monthly_raw if f["properties"].get("mean_ndvi") is not None],
        key=lambda x: x["date"],
    )

    if progress_cb:
        progress_cb(4, 5, "Descargando rásteres de píxeles…")

    # Download pixel arrays
    ndvi_p25   = _download_image(ndvi_p25_img,  "NDVI_P25",    roi, res_m)
    ndvi_med   = _download_image(ndvi_med_img,  "NDVI_median", roi, res_m)

    # Predio mask (1 inside polygon, 0 outside)
    pmask_img  = ee.Image.constant(1).clip(roi).unmask(0)
    pmask_url  = pmask_img.getDownloadURL({
        "region": roi, "scale": res_m,
        "format": "GEO_TIFF", "crs": "EPSG:4326",
    })
    resp_mask  = requests.get(pmask_url, timeout=60)
    resp_mask.raise_for_status()
    with rasterio.open(BytesIO(resp_mask.content)) as src:
        pmask = src.read(1).astype(bool)

    # Align shapes (GEE pixel grids can differ by 1 px due to rounding)
    min_h = min(ndvi_p25.shape[0], ndvi_med.shape[0], pmask.shape[0])
    min_w = min(ndvi_p25.shape[1], ndvi_med.shape[1], pmask.shape[1])
    ndvi_p25 = ndvi_p25[:min_h, :min_w]
    ndvi_med = ndvi_med[:min_h,  :min_w]
    pmask    = pmask[:min_h,     :min_w]

    ndvi_p25[~pmask] = np.nan
    ndvi_med[~pmask] = np.nan

    low_mask = (~np.isnan(ndvi_p25)) & (ndvi_p25 < ndvi_threshold)

    area_predio_ha = float(gdf_predio.to_crs("EPSG:3857").geometry.iloc[0].area / 10_000)
    pct_low        = float(low_mask.sum() / max(pmask.sum(), 1) * 100)
    area_low_ha    = area_predio_ha * pct_low / 100
    p25_in_predio  = ndvi_p25[pmask]

    if progress_cb:
        progress_cb(5, 5, "Generando mapas…")

    return {
        "ndvi_p25":       ndvi_p25,
        "ndvi_p25_mean":  float(np.nanmean(p25_in_predio)) if p25_in_predio.size > 0 else None,
        "ndvi_median":    ndvi_med,
        "low_ndvi_mask":  low_mask,
        "n_scenes_used":  n_total,
        "n_scenes_total": n_total,
        "ndvi_min":       float(np.nanmin(p25_in_predio)) if p25_in_predio.size > 0 else None,
        "ndvi_max":       float(np.nanmax(p25_in_predio)) if p25_in_predio.size > 0 else None,
        "area_low_ha":    round(area_low_ha, 4),
        "pct_low":        round(pct_low, 1),
        "ndvi_threshold": ndvi_threshold,
        "scene_stats":    scene_stats,
        "bounds_wgs84":   bounds,
        "maps":           _build_maps(gdf_predio, ndvi_p25, low_mask, bounds),
    }
