"""
utils/eosda_terrain.py
Descarga y análisis de terreno a partir de la API EOSDA Terrain.
Visualizaciones en Folium como overlays sobre imagen satelital.

Funciones principales:
    get_terrain_analysis(gdf_predio, slope_threshold)
        → dict con arrays DEM/slope/aspect + estadísticas + GeoDataFrames para Folium
"""

import math
import base64
import tempfile
import warnings
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
import rasterio
import rasterio.warp
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.transform import from_bounds
import geopandas as gpd
from PIL import Image
import matplotlib.cm as cm
import matplotlib.colors as mcolors

warnings.filterwarnings("ignore")

ZOOM     = 14
BASE_URL = "https://api-connect.eos.com/api/render/terrain"


# ── API Key ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    try:
        import streamlit as st
        return st.secrets["EOSDA_API_KEY"]
    except Exception:
        import os
        return os.environ.get("EOSDA_API_KEY", "")


# ── Helpers tiles ─────────────────────────────────────────────────────────────

def _deg2tile(lat, lon, zoom):
    lat_r = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y

def _tile2bbox(x, y, zoom):
    n = 2 ** zoom
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_min, lat_min, lon_max, lat_max

def _get_tiles(minx, miny, maxx, maxy, zoom):
    x_min, y_min = _deg2tile(maxy, minx, zoom)
    x_max, y_max = _deg2tile(miny, maxx, zoom)
    return [(x, y, zoom) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]

def _download_tile(x, y, z, tmp_dir, api_key):
    url = f"{BASE_URL}/{z}/{x}/{y}?api_key={api_key}&format=geotiff"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    tile_path = Path(tmp_dir) / f"tile_{z}_{x}_{y}.tif"
    tile_path.write_bytes(resp.content)

    bbox = _tile2bbox(x, y, z)
    with rasterio.open(tile_path) as src:
        data    = src.read()
        profile = src.profile.copy()

    profile.update({
        "crs": "EPSG:3857",
        "transform": from_bounds(
            *rasterio.warp.transform_bounds("EPSG:4326", "EPSG:3857", *bbox),
            width=data.shape[-1], height=data.shape[-2],
        ),
        "driver": "GTiff",
    })
    georef_path = Path(tmp_dir) / f"georef_{z}_{x}_{y}.tif"
    with rasterio.open(georef_path, "w", **profile) as dst:
        dst.write(data)
    return georef_path


# ── Descarga y recorte DEM ────────────────────────────────────────────────────

def _download_dem(gdf_predio: gpd.GeoDataFrame, api_key: str):
    gdf_wgs84 = gdf_predio.to_crs("EPSG:4326")
    bounds    = gdf_wgs84.total_bounds
    tiles     = _get_tiles(*bounds, ZOOM)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tile_paths = []
        for x, y, z in tiles:
            try:
                p = _download_tile(x, y, z, tmp_dir, api_key)
                tile_paths.append(p)
            except Exception:
                continue

        if not tile_paths:
            raise RuntimeError("No se pudo descargar ningún tile del DEM.")

        datasets = [rasterio.open(p) for p in tile_paths]
        mosaic, mosaic_transform = merge(datasets)
        mosaic_profile = datasets[0].profile.copy()
        mosaic_profile.update({
            "height": mosaic.shape[1], "width": mosaic.shape[2],
            "transform": mosaic_transform,
        })
        for ds in datasets:
            ds.close()

        gdf_3857 = gdf_predio.to_crs("EPSG:3857")
        geoms    = [g.__geo_interface__ for g in gdf_3857.geometry]

        mosaic_path = Path(tmp_dir) / "mosaic.tif"
        with rasterio.open(mosaic_path, "w", **mosaic_profile) as dst:
            dst.write(mosaic)

        with rasterio.open(mosaic_path) as src:
            clipped, clipped_transform = mask(src, geoms, crop=True, nodata=np.nan)
            crs = src.crs

        dem = clipped[0].astype("float32")

        # Reproyectar bounds a WGS84 para Folium
        left   = clipped_transform.c
        top    = clipped_transform.f
        right  = left + clipped_transform.a * dem.shape[1]
        bottom = top  + clipped_transform.e * dem.shape[0]
        bounds_3857 = (left, bottom, right, top)
        bounds_wgs84 = rasterio.warp.transform_bounds("EPSG:3857", "EPSG:4326", *bounds_3857)

        return dem, clipped_transform, crs, bounds_wgs84


# ── Cálculo slope / aspect ────────────────────────────────────────────────────

def _get_res_meters(transform, crs, shape):
    if crs and hasattr(crs, "is_geographic") and crs.is_geographic:
        lat_c = transform.f + transform.e * shape[0] / 2
        res_y = abs(transform.e) * 111320
        res_x = abs(transform.a) * 111320 * np.cos(np.radians(lat_c))
    else:
        res_x = abs(transform.a)
        res_y = abs(transform.e)
    return res_x, res_y

def _calc_slope(dem, transform, crs):
    res_x, res_y = _get_res_meters(transform, crs, dem.shape)
    dz_dy, dz_dx = np.gradient(dem, res_y, res_x)
    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))
    slope[np.isnan(dem)] = np.nan
    return slope

def _calc_aspect(dem, transform, crs):
    res_x, res_y = _get_res_meters(transform, crs, dem.shape)
    dz_dy, dz_dx = np.gradient(dem, res_y, res_x)
    aspect = np.degrees(np.arctan2(dz_dy, -dz_dx))
    aspect = (aspect + 360) % 360
    aspect[np.isnan(dem)] = np.nan
    return aspect

def _aspect_label(deg):
    dirs = ["N","NE","E","SE","S","SO","O","NO"]
    return dirs[int((deg + 22.5) / 45) % 8]


# ── Raster → imagen PNG base64 para Folium ImageOverlay ──────────────────────

def _array_to_png_b64(arr: np.ndarray, colormap: str,
                       vmin: float = None, vmax: float = None,
                       alpha: float = 0.65) -> str:
    """Convierte un array 2D a PNG base64 con colormap para usar en Folium."""
    data = arr.copy()
    if vmin is None: vmin = float(np.nanmin(data))
    if vmax is None: vmax = float(np.nanmax(data))

    norm   = mcolors.Normalize(vmin=vmin, vmax=vmax)
    mapper = cm.get_cmap(colormap)

    rgba = mapper(norm(data))  # (H, W, 4)

    # Transparencia en zonas NaN
    nan_mask = np.isnan(data)
    rgba[nan_mask, 3] = 0.0
    rgba[~nan_mask, 3] = alpha

    img_uint8 = (rgba * 255).astype(np.uint8)
    img = Image.fromarray(img_uint8, mode="RGBA")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def _cultivable_to_png_b64(cultivable_mask, no_cultivable_mask,
                             alpha: float = 0.60) -> str:
    """Genera imagen PNG para zona cultivable/no cultivable."""
    h, w = cultivable_mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # Verde = cultivable
    rgba[cultivable_mask]    = [22, 163, 74, int(alpha * 255)]
    # Rojo = no cultivable
    rgba[no_cultivable_mask] = [220, 38, 38, int(alpha * 255)]

    img = Image.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── Construcción mapas Folium ─────────────────────────────────────────────────

def build_terrain_maps(gdf_predio: gpd.GeoDataFrame, terrain: dict) -> dict:
    """
    Construye 4 mapas Folium con overlays raster:
        - dem_map      : elevación
        - slope_map    : pendiente
        - aspect_map   : aspecto
        - cultiv_map   : zona cultivable vs no cultivable
    """
    import folium
    from folium.plugins import Fullscreen

    dem    = terrain["dem"]
    slope  = terrain["slope"]
    aspect = terrain["aspect"]
    bounds_wgs84 = terrain["bounds_wgs84"]  # (minx, miny, maxx, maxy)
    bx = [[bounds_wgs84[1], bounds_wgs84[0]], [bounds_wgs84[3], bounds_wgs84[2]]]

    geom   = gdf_predio.geometry.iloc[0]
    center = [geom.centroid.y, geom.centroid.x]

    def _base(center, zoom=16):
        m = folium.Map(location=center, zoom_start=zoom, tiles="Esri.WorldImagery")
        Fullscreen().add_to(m)
        m.fit_bounds(bx)
        return m

    def _add_predio_outline(m):
        folium.GeoJson(
            data=gdf_predio.to_json(),
            style_function=lambda _: {"fillColor":"none","color":"#ffffff",
                                       "weight":2.5,"fillOpacity":0},
        ).add_to(m)

    # ── DEM ───────────────────────────────────────────────────────────────
    dem_map = _base(center)
    png_dem = _array_to_png_b64(dem, "terrain")
    folium.raster_layers.ImageOverlay(
        image=png_dem, bounds=bx, opacity=0.75, name="Elevación",
    ).add_to(dem_map)
    _add_predio_outline(dem_map)

    # ── Slope ─────────────────────────────────────────────────────────────
    slope_map = _base(center)
    png_slope = _array_to_png_b64(slope, "RdYlGn_r", vmin=0, vmax=30)
    folium.raster_layers.ImageOverlay(
        image=png_slope, bounds=bx, opacity=0.75, name="Pendiente",
    ).add_to(slope_map)
    _add_predio_outline(slope_map)

    # ── Aspect ────────────────────────────────────────────────────────────
    aspect_map = _base(center)
    png_aspect = _array_to_png_b64(aspect, "hsv", vmin=0, vmax=360)
    folium.raster_layers.ImageOverlay(
        image=png_aspect, bounds=bx, opacity=0.75, name="Aspecto",
    ).add_to(aspect_map)
    _add_predio_outline(aspect_map)

    # ── Cultivable ────────────────────────────────────────────────────────
    cultiv_map = _base(center)
    png_cult   = _cultivable_to_png_b64(
        terrain["cultivable_mask"], terrain["no_cultivable_mask"]
    )
    folium.raster_layers.ImageOverlay(
        image=png_cult, bounds=bx, opacity=0.75, name="Zona cultivable",
    ).add_to(cultiv_map)
    _add_predio_outline(cultiv_map)

    return {
        "dem_map":    dem_map,
        "slope_map":  slope_map,
        "aspect_map": aspect_map,
        "cultiv_map": cultiv_map,
    }


# ── Función principal ─────────────────────────────────────────────────────────

def get_terrain_analysis(gdf_predio: gpd.GeoDataFrame,
                         slope_threshold: float = 15.0) -> dict:
    """
    Descarga DEM y calcula terreno completo.
    Retorna dict con arrays, estadísticas y mapas Folium listos para st_folium.
    """
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("EOSDA_API_KEY no configurada en secrets.")

    dem, transform, crs, bounds_wgs84 = _download_dem(gdf_predio, api_key)

    slope  = _calc_slope(dem, transform, crs)
    aspect = _calc_aspect(dem, transform, crs)

    res_x, res_y  = _get_res_meters(transform, crs, dem.shape)
    pixel_area_ha = (res_x * res_y) / 10_000

    valid_mask         = ~np.isnan(dem)
    cultivable_mask    = valid_mask & (slope < slope_threshold)
    no_cultivable_mask = valid_mask & (slope >= slope_threshold)

    area_total_ha      = valid_mask.sum() * pixel_area_ha
    area_cultivable_ha = cultivable_mask.sum() * pixel_area_ha
    pct_cultivable     = area_cultivable_ha / area_total_ha * 100 if area_total_ha > 0 else 0

    breaks = [0, 5, 15, 30, 45, 90]
    labels = ["Plana (0–5°)","Suave (5–15°)","Moderada (15–30°)",
              "Fuerte (30–45°)","Muy fuerte (>45°)"]
    slope_v = slope[valid_mask]
    slope_classes = {
        labels[i]: float(np.sum((slope_v >= breaks[i]) & (slope_v < breaks[i+1])) / len(slope_v) * 100)
        for i in range(len(breaks) - 1)
    }

    asp_med = float(np.nanmedian(aspect[valid_mask]))

    stats = {
        "elev_min":   float(np.nanmin(dem)),
        "elev_max":   float(np.nanmax(dem)),
        "elev_mean":  float(np.nanmean(dem)),
        "elev_range": float(np.nanmax(dem) - np.nanmin(dem)),
        "slope_min":  float(np.nanmin(slope)),
        "slope_max":  float(np.nanmax(slope)),
        "slope_mean": float(np.nanmean(slope)),
        "slope_median": float(np.nanmedian(slope)),
        "slope_classes": slope_classes,
        "aspect_dominant":     _aspect_label(asp_med),
        "aspect_dominant_deg": asp_med,
        "area_total_ha":       round(area_total_ha, 2),
        "area_cultivable_ha":  round(area_cultivable_ha, 2),
        "area_no_cultivable_ha": round(area_total_ha - area_cultivable_ha, 2),
        "pct_cultivable":      round(pct_cultivable, 1),
        "slope_threshold":     slope_threshold,
        "res_x_m": round(res_x, 1),
        "res_y_m": round(res_y, 1),
    }

    terrain = {
        "dem":               dem,
        "slope":             slope,
        "aspect":            aspect,
        "cultivable_mask":   cultivable_mask,
        "no_cultivable_mask":no_cultivable_mask,
        "pixel_area_ha":     pixel_area_ha,
        "slope_threshold":   slope_threshold,
        "bounds_wgs84":      bounds_wgs84,
        "stats":             stats,
    }

    # Construir mapas Folium
    terrain["maps"] = build_terrain_maps(gdf_predio, terrain)

    return terrain
