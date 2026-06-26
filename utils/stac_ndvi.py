"""
utils/stac_ndvi.py

Downloads historical per-pixel NDVI from Sentinel-2 L2A COGs via Element84 Earth Search.
No API key required. Cloud filtering uses the SCL band within the predio polygon.

Flow:
  1. STAC search → list of scenes (metadata only, fast)
  2. Pre-filter: eo:cloud_cover < 50 % (tile-level metadata)
  3. Per scene (parallel):
       a. Download SCL window → compute real cloud fraction within predio
       b. If < max_cloud_pct: download B04 + B08, compute per-pixel NDVI
  4. Stack valid NDVI arrays → P25 per pixel across all scenes
  5. Binary mask: pixels with P25 < ndvi_threshold → non-productive

Element84 Earth Search v1 asset names for sentinel-2-l2a:
  red  → B04 10 m   nir  → B08 10 m   scl  → SCL 20 m
"""

import warnings
import numpy as np
import geopandas as gpd
import rasterio
import rasterio.warp
import rasterio.features
import rasterio.enums
from rasterio.transform import from_bounds
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import BytesIO
import base64

from PIL import Image
import matplotlib
import matplotlib.colors as mcolors
import folium
from folium.plugins import Fullscreen

warnings.filterwarnings("ignore")

STAC_URL   = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# SCL pixel classes treated as unusable (cloud shadow, cloud, cirrus, snow/ice)
_BAD_SCL = {0, 1, 3, 8, 9, 10, 11}


# ── STAC search ───────────────────────────────────────────────────────────────

def _search_scenes(bbox_4326: list, n_years: int, pre_cloud: int = 50) -> list:
    from pystac_client import Client
    end   = datetime.utcnow()
    start = end - timedelta(days=365 * n_years)
    client = Client.open(STAC_URL)
    search = client.search(
        collections=[COLLECTION],
        bbox=bbox_4326,
        datetime=f"{start.date().isoformat()}/{end.date().isoformat()}",
        query={"eo:cloud_cover": {"lt": pre_cloud}},
        max_items=2000,
    )
    return list(search.items())


# ── Target grid (UTM) ─────────────────────────────────────────────────────────

def _target_grid(gdf_predio: gpd.GeoDataFrame, res_m: float = 20.0):
    """Returns (epsg, transform, (h, w)) for a UTM grid covering the predio."""
    c    = gdf_predio.to_crs("EPSG:4326").geometry.iloc[0].centroid
    zone = int((c.x + 180) / 6) + 1
    hem  = "6" if c.y >= 0 else "7"
    epsg = int(f"32{hem}{zone:02d}")

    b    = gdf_predio.to_crs(f"EPSG:{epsg}").total_bounds
    buf  = res_m * 3
    minx, miny, maxx, maxy = b[0]-buf, b[1]-buf, b[2]+buf, b[3]+buf
    w    = max(4, int(np.ceil((maxx - minx) / res_m)))
    h    = max(4, int(np.ceil((maxy - miny) / res_m)))
    tfm  = from_bounds(minx, miny, maxx, maxy, w, h)
    return epsg, tfm, (h, w)


def _predio_mask(gdf_predio: gpd.GeoDataFrame, epsg: int, tfm, shape: tuple) -> np.ndarray:
    gdf  = gdf_predio.to_crs(f"EPSG:{epsg}")
    shps = [(g.__geo_interface__, 1) for g in gdf.geometry if not g.is_empty]
    return rasterio.features.rasterize(
        shps, out_shape=shape, transform=tfm, dtype=np.uint8
    ) > 0


# ── COG reading ───────────────────────────────────────────────────────────────

_RASTERIO_ENV = dict(
    GDAL_HTTP_VERSION          = "2",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES = "YES",
    GDAL_DISABLE_READDIR_ON_OPEN = "EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY        = "3",
    GDAL_HTTP_RETRY_DELAY      = "1",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS = ".tif",
    AWS_NO_SIGN_REQUEST        = "YES",
)


def _asset_href(item, *names: str):
    for name in names:
        a = item.assets.get(name)
        if a:
            return a.href
    return None


def _read_to_grid(href: str, epsg: int, tfm, shape: tuple,
                  resampling=rasterio.enums.Resampling.bilinear):
    """Reads one COG band via HTTPS range request and reprojects to target grid."""
    try:
        h, w = shape
        dst  = np.full((h, w), np.nan, dtype=np.float32)
        with rasterio.Env(**_RASTERIO_ENV):
            with rasterio.open(href) as src:
                rasterio.warp.reproject(
                    source      = rasterio.band(src, 1),
                    destination = dst,
                    src_transform = src.transform,
                    src_crs       = src.crs,
                    dst_transform = tfm,
                    dst_crs       = f"EPSG:{epsg}",
                    resampling    = resampling,
                    dst_nodata    = np.nan,
                )
        return dst
    except Exception:
        return None


# ── Per-scene processing ──────────────────────────────────────────────────────

def _process_scene(item, epsg, tfm, shape, pmask, max_cloud_pct: float):
    """
    Returns (date_str, ndvi_arr) if the scene passes cloud filter, else (date_str, None).
    NDVI pixels under cloud/shadow/snow are set to NaN.
    """
    date_str = item.datetime.strftime("%Y-%m-%d")

    # 1. SCL → cloud fraction within predio
    scl_href = _asset_href(item, "scl", "SCL")
    if not scl_href:
        return date_str, None

    scl = _read_to_grid(scl_href, epsg, tfm, shape, rasterio.enums.Resampling.nearest)
    if scl is None:
        return date_str, None

    predio_scl = scl[pmask].astype(int)
    bad_px     = np.isin(predio_scl, list(_BAD_SCL)).sum()
    tot_px     = predio_scl.size
    if tot_px == 0 or bad_px / tot_px > max_cloud_pct / 100:
        return date_str, None   # too cloudy over predio

    # 2. B04 (Red) + B08 (NIR) → NDVI
    b04_href = _asset_href(item, "red",  "B04", "b04")
    b08_href = _asset_href(item, "nir",  "B08", "b08", "nir08")
    if not b04_href or not b08_href:
        return date_str, None

    b04 = _read_to_grid(b04_href, epsg, tfm, shape)
    b08 = _read_to_grid(b08_href, epsg, tfm, shape)
    if b04 is None or b08 is None:
        return date_str, None

    bad_mask = np.isin(scl.astype(int), list(_BAD_SCL)) | ~pmask
    denom    = b08 + b04
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(
            ~bad_mask & (denom > 0),
            (b08 - b04) / denom,
            np.nan,
        ).astype(np.float32)

    return date_str, ndvi


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
    rgba[~nan_mask & ~low_mask] = [22, 163, 74,  int(alpha * 255)]
    rgba[~nan_mask &  low_mask] = [220, 38,  38, int(alpha * 255)]
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
            style_function=lambda _: {"fillColor":"none","color":"#ffffff",
                                       "weight":2.5,"fillOpacity":0},
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


# ── Main function ─────────────────────────────────────────────────────────────

def get_ndvi_stac(
    gdf_predio:    gpd.GeoDataFrame,
    ndvi_threshold: float = 0.25,
    n_years:        int   = 3,
    max_cloud_pct:  float = 20.0,
    res_m:          float = 20.0,
    max_workers:    int   = 8,
    progress_cb     = None,   # callable(done: int, total: int, msg: str)
) -> dict:
    """
    Downloads Sentinel-2 L2A NDVI per pixel from Element84 COGs and
    computes P25 per pixel across the last n_years of cloud-free scenes.

    Returns dict compatible with the A2C section of app.py:
        ndvi_p25        : 2D float32 array (real per-pixel P25)
        ndvi_p25_mean   : scalar mean of P25 within predio (for KPI display)
        ndvi_median     : 2D float32 array (temporal median per pixel)
        low_ndvi_mask   : 2D bool array — True where P25 < ndvi_threshold
        n_scenes_used   : number of scenes that passed cloud filter
        n_scenes_total  : scenes found before per-predio cloud filter
        ndvi_min/max    : scalar min/max of P25 within predio
        area_low_ha     : area with P25 < threshold
        pct_low         : % of predio area with P25 < threshold
        scene_stats     : list of {date, mean_ndvi} for time-series chart
        bounds_wgs84    : [minx, miny, maxx, maxy] in EPSG:4326
        maps            : {ndvi_map, prod_map} Folium maps
    """
    gdf_4326 = gdf_predio.to_crs("EPSG:4326")
    bbox     = list(gdf_4326.total_bounds)

    if progress_cb:
        progress_cb(0, 1, "Buscando escenas en STAC…")

    items = _search_scenes(bbox, n_years=n_years)
    if not items:
        raise RuntimeError(
            "No se encontraron escenas Sentinel-2 para este predio y período."
        )

    epsg, tfm, shape = _target_grid(gdf_predio, res_m)
    pmask            = _predio_mask(gdf_predio, epsg, tfm, shape)

    n_total    = len(items)
    results    = {}   # date_str → ndvi_arr
    done_count = [0]

    def _cb_wrapper(item):
        res = _process_scene(item, epsg, tfm, shape, pmask, max_cloud_pct)
        done_count[0] += 1
        if progress_cb:
            progress_cb(
                done_count[0], n_total,
                f"Procesando escenas ({done_count[0]}/{n_total}) · "
                f"válidas: {len(results)}…",
            )
        return res

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_cb_wrapper, item): item for item in items}
        for fut in as_completed(futs):
            date_str, ndvi = fut.result()
            if ndvi is not None:
                results[date_str] = ndvi

    if not results:
        raise RuntimeError(
            f"Ninguna escena pasó el filtro de nubosidad (<{max_cloud_pct}%) "
            f"dentro del predio ({n_total} escenas analizadas)."
        )

    sorted_pairs = sorted(results.items())
    ndvi_stack   = np.stack([arr for _, arr in sorted_pairs], axis=0)

    ndvi_p25    = np.nanpercentile(ndvi_stack, 25,  axis=0).astype(np.float32)
    ndvi_median = np.nanmedian(ndvi_stack,           axis=0).astype(np.float32)
    ndvi_p25[~pmask]    = np.nan
    ndvi_median[~pmask] = np.nan

    low_mask = (~np.isnan(ndvi_p25)) & (ndvi_p25 < ndvi_threshold)

    area_predio_ha = float(gdf_predio.to_crs("EPSG:3857").geometry.iloc[0].area / 10_000)
    pct_low        = float(low_mask.sum() / max(pmask.sum(), 1) * 100)
    area_low_ha    = area_predio_ha * pct_low / 100

    p25_in_predio  = ndvi_p25[pmask]
    scene_stats    = [
        {"date": d, "mean_ndvi": float(np.nanmean(arr[pmask]))}
        for d, arr in sorted_pairs
    ]

    return {
        "ndvi_p25":       ndvi_p25,
        "ndvi_p25_mean":  float(np.nanmean(p25_in_predio)) if p25_in_predio.size > 0 else None,
        "ndvi_median":    ndvi_median,
        "low_ndvi_mask":  low_mask,
        "n_scenes_used":  len(results),
        "n_scenes_total": n_total,
        "ndvi_min":       float(np.nanmin(p25_in_predio))  if p25_in_predio.size > 0 else None,
        "ndvi_max":       float(np.nanmax(p25_in_predio))  if p25_in_predio.size > 0 else None,
        "area_low_ha":    round(area_low_ha, 4),
        "pct_low":        round(pct_low, 1),
        "ndvi_threshold": ndvi_threshold,
        "scene_stats":    scene_stats,
        "bounds_wgs84":   bbox,
        "maps":           _build_maps(gdf_predio, ndvi_p25, low_mask, bbox),
    }
