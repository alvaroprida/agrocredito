"""
utils/postgis_client.py
Funciones de consulta a la base PostGIS.

En modo simulado (USE_REAL_DB = False) devuelve datos ficticios
con la misma estructura que devolvería la query real, para que
el front funcione sin conexión durante el desarrollo.

Para activar la BD real, pon USE_REAL_DB = True y asegúrate de
que la variable de entorno / Streamlit secret DATABASE_URL esté
configurada.
"""

import os
import json
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, Point, mapping
try:
    import psycopg2
    from sqlalchemy import create_engine, text
    DB_LIBS_OK = True
except ImportError:
    DB_LIBS_OK = False

# ── Configuración ────────────────────────────────────────────────────────────
# Cambia a True cuando tengas la BD accesible (Supabase, ngrok, etc.)
USE_REAL_DB = False

# ── Conexión ─────────────────────────────────────────────────────────────────

def _get_engine():
    """Devuelve un engine SQLAlchemy usando DATABASE_URL del entorno."""
    try:
        import streamlit as st
        db_url = st.secrets["DATABASE_URL"]
    except Exception:
        db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise ValueError("DATABASE_URL no configurada en secrets ni en variables de entorno.")
    # Supabase requiere SSL para conexiones externas
    if "supabase.co" in db_url and "sslmode" not in db_url:
        db_url += "?sslmode=require"
    return create_engine(db_url)


# ── Datos simulados ───────────────────────────────────────────────────────────

# Polígonos aproximados para los dos casos de estudio
_MOCK_PREDIOS = {
    # Café · Salento, Quindío  (lat=4.8087, lon=-75.6906)
    (4.8087, -75.6906): {
        "codigo": "63001000100010001000",
        "municipio": "Salento",
        "departamento": "Quindío",
        "area_catastral_ha": 12.4,
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [-75.6930, 4.8065], [-75.6880, 4.8065],
                [-75.6880, 4.8110], [-75.6930, 4.8110],
                [-75.6930, 4.8065],
            ]],
        },
    },
    # Plátano · Turbo, Antioquia  (lat=7.8833, lon=-76.6500)
    (7.8833, -76.6500): {
        "codigo": "05837000100020002000",
        "municipio": "Turbo",
        "departamento": "Antioquia",
        "area_catastral_ha": 28.0,
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [-76.6540, 7.8800], [-76.6460, 7.8800],
                [-76.6460, 7.8865], [-76.6540, 7.8865],
                [-76.6540, 7.8800],
            ]],
        },
    },
}

def _mock_predio(lat: float, lon: float) -> dict | None:
    """Devuelve el predio simulado más cercano al punto dado."""
    best, best_dist = None, float("inf")
    for (plat, plon), data in _MOCK_PREDIOS.items():
        d = ((lat - plat) ** 2 + (lon - plon) ** 2) ** 0.5
        if d < best_dist:
            best_dist, best = d, data
    return best


# ── Función principal ─────────────────────────────────────────────────────────

def get_predio_por_punto(lat: float, lon: float) -> dict | None:
    """
    Dado un punto (lat, lon), devuelve el predio de la tabla 'predios'
    que lo contiene, con su polígono y código catastral.

    Retorna un dict con:
        codigo          str   · código catastral del predio
        municipio       str
        departamento    str
        area_catastral_ha float
        geojson         dict  · GeoJSON del polígono (EPSG:4326)
        gdf             GeoDataFrame de una fila (para visualización)

    Retorna None si no se encuentra ningún predio.
    """
    if USE_REAL_DB and DB_LIBS_OK:
        return _query_real(lat, lon)
    else:
        return _query_mock(lat, lon)


def _query_mock(lat: float, lon: float) -> dict | None:
    data = _mock_predio(lat, lon)
    if data is None:
        return None
    geom = shape(data["geojson"])
    gdf = gpd.GeoDataFrame(
        [{
            "codigo": data["codigo"],
            "municipio": data["municipio"],
            "departamento": data["departamento"],
            "area_catastral_ha": data["area_catastral_ha"],
        }],
        geometry=[geom],
        crs="EPSG:4326",
    )
    return {**data, "gdf": gdf}


def _query_real(lat: float, lon: float) -> dict | None:
    """
    Query real a PostGIS.
    Asume que la tabla 'predios' tiene:
      - columna de geometría llamada 'geom' (cualquier SRID → se reprojecta a 4326)
      - columna 'codigo'
      - opcionalmente: 'municipio', 'departamento', 'area_catastral_ha'
    """
    sql = text("""
        SELECT
            codigo,
            COALESCE(municipio, '')        AS municipio,
            COALESCE(departamento, '')     AS departamento,
            COALESCE(area_catastral_ha, 0) AS area_catastral_ha,
            ST_AsGeoJSON(geom)::json       AS geojson
        FROM predios
        WHERE ST_Contains(
            geom,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        )
        LIMIT 1
    """)
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(sql, {"lat": lat, "lon": lon}).fetchone()
        if row is None:
            return None
        geojson = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
        geom = shape(geojson)
        gdf = gpd.GeoDataFrame(
            [{
                "codigo": row.codigo,
                "municipio": row.municipio,
                "departamento": row.departamento,
                "area_catastral_ha": row.area_catastral_ha,
            }],
            geometry=[geom],
            crs="EPSG:4326",
        )
        return {
            "codigo": row.codigo,
            "municipio": row.municipio,
            "departamento": row.departamento,
            "area_catastral_ha": row.area_catastral_ha,
            "geojson": geojson,
            "gdf": gdf,
        }
    except Exception as e:
        raise ConnectionError(f"Error consultando PostGIS: {e}")
