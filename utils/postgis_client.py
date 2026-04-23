"""
utils/postgis_client.py
Funciones de consulta a la base PostGIS / Supabase.

Funciones principales:
    get_predio_por_punto(lat, lon)       → polígono + código + departamento + área
    get_frontera(gdf_predio)             → tipo de frontera agrícola del predio
    get_aptitud(gdf_predio, cultivo)     → aptitud del predio al cultivo
    get_construcciones(gdf_predio)       → construcciones dentro del predio
"""

import os
import json
import geopandas as gpd
from shapely.geometry import shape
try:
    from sqlalchemy import create_engine, text
    import psycopg2
    DB_LIBS_OK = True
except ImportError:
    DB_LIBS_OK = False

# ── Configuración ─────────────────────────────────────────────────────────────
USE_REAL_DB = True

# ── Conexión ──────────────────────────────────────────────────────────────────

def _get_engine():
    try:
        import streamlit as st
        db_url = st.secrets["DATABASE_URL"]
    except Exception:
        db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise ValueError("DATABASE_URL no configurada.")
    if "supabase" in db_url and "sslmode" not in db_url:
        db_url += "?sslmode=require"
    return create_engine(db_url)


# ── Datos simulados ───────────────────────────────────────────────────────────

_MOCK_PREDIOS = {
    (4.8087, -75.6906): {
        "codigo": "63001000100010001000",
        "departamento": "Quindío",
        "area_ha": 12.4,
        "geojson": {"type": "Polygon", "coordinates": [[
            [-75.6930, 4.8065], [-75.6880, 4.8065],
            [-75.6880, 4.8110], [-75.6930, 4.8110],
            [-75.6930, 4.8065],
        ]]},
    },
    (7.8833, -76.6500): {
        "codigo": "05837000100020002000",
        "departamento": "Antioquia",
        "area_ha": 28.0,
        "geojson": {"type": "Polygon", "coordinates": [[
            [-76.6540, 7.8800], [-76.6460, 7.8800],
            [-76.6460, 7.8865], [-76.6540, 7.8865],
            [-76.6540, 7.8800],
        ]]},
    },
}

def _mock_predio(lat, lon):
    best, best_dist = None, float("inf")
    for (plat, plon), data in _MOCK_PREDIOS.items():
        d = ((lat - plat) ** 2 + (lon - plon) ** 2) ** 0.5
        if d < best_dist:
            best_dist, best = d, data
    return best

def _mock_gdf(data):
    geom = shape(data["geojson"])
    return gpd.GeoDataFrame(
        [{"codigo": data["codigo"], "departamento": data["departamento"], "area_ha": data["area_ha"]}],
        geometry=[geom], crs="EPSG:4326",
    )


# ════════════════════════════════════════════════════════════════════════════
#  1 · IDENTIFICACIÓN DEL PREDIO
# ════════════════════════════════════════════════════════════════════════════

def get_predio_por_punto(lat: float, lon: float) -> dict | None:
    """
    Dado un punto (lat, lon), devuelve el predio que lo contiene.
    Retorna dict con: codigo, departamento, area_ha, geojson, gdf
    """
    if USE_REAL_DB and DB_LIBS_OK:
        try:
            return _query_predio_real(lat, lon)
        except Exception as e:
            import streamlit as st
            st.error(f"❌ Error consultando predio: {e}")
            return None
    return _query_predio_mock(lat, lon)


def _query_predio_mock(lat, lon):
    data = _mock_predio(lat, lon)
    if data is None:
        return None
    gdf = _mock_gdf(data)
    return {"codigo": data["codigo"], "departamento": data["departamento"],
            "area_ha": data["area_ha"], "geojson": data["geojson"], "gdf": gdf}


def _query_predio_real(lat, lon):
    sql = text("""
        SELECT
            codigo,
            COALESCE(departamento, '—')             AS departamento,
            COALESCE(ROUND(area_ha::numeric, 2), 0) AS area_ha,
            ST_AsGeoJSON(geom)::json                AS geojson
        FROM predios_mvp
        WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        LIMIT 1
    """)
    with _get_engine().connect() as conn:
        row = conn.execute(sql, {"lat": lat, "lon": lon}).fetchone()
    if row is None:
        return None
    geojson = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
    gdf = gpd.GeoDataFrame(
        [{"codigo": row.codigo, "departamento": row.departamento, "area_ha": float(row.area_ha)}],
        geometry=[shape(geojson)], crs="EPSG:4326",
    )
    return {"codigo": row.codigo, "departamento": row.departamento,
            "area_ha": float(row.area_ha), "geojson": geojson, "gdf": gdf}


# ════════════════════════════════════════════════════════════════════════════
#  2 · FRONTERA AGRÍCOLA
# ════════════════════════════════════════════════════════════════════════════

def get_frontera(gdf_predio: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """
    Dado el GeoDataFrame del predio, devuelve las zonas de frontera agrícola
    que intersectan con él (tabla frontera_mvp).
    Retorna GeoDataFrame con columna tipo_condi, o None si no hay intersección.
    """
    if USE_REAL_DB and DB_LIBS_OK:
        try:
            return _query_frontera_real(gdf_predio)
        except Exception as e:
            import streamlit as st
            st.error(f"❌ Error consultando frontera: {e}")
            return None
    return _query_frontera_mock(gdf_predio)


def _query_frontera_mock(gdf_predio):
    # Simulado: devuelve frontera agrícola genérica sobre el bbox del predio
    geom = gdf_predio.geometry.iloc[0]
    return gpd.GeoDataFrame(
        [{"tipo_condi": "Frontera agrícola"}],
        geometry=[geom], crs="EPSG:4326",
    )


def _query_frontera_real(gdf_predio):
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    sql = text("""
        SELECT
            tipo_condi,
            ST_AsGeoJSON(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
            )::json AS geojson
        FROM frontera_mvp
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql, {"geom": json.dumps(geojson_predio)}).fetchall()
    if not rows:
        return None
    records, geometries = [], []
    for row in rows:
        gj = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
        try:
            geom = shape(gj)
            if not geom.is_empty:
                records.append({"tipo_condi": row.tipo_condi})
                geometries.append(geom)
        except Exception:
            continue
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


# ════════════════════════════════════════════════════════════════════════════
#  3 · APTITUD DEL CULTIVO
# ════════════════════════════════════════════════════════════════════════════

def get_aptitud(gdf_predio: gpd.GeoDataFrame, cultivo: str) -> gpd.GeoDataFrame | None:
    """
    Dado el GeoDataFrame del predio y el cultivo ('café' o 'plátano'),
    devuelve las zonas de aptitud que intersectan con el predio.
    Retorna GeoDataFrame con columna aptitud, o None si no hay datos.
    """
    if USE_REAL_DB and DB_LIBS_OK:
        try:
            return _query_aptitud_real(gdf_predio, cultivo)
        except Exception as e:
            import streamlit as st
            st.error(f"❌ Error consultando aptitud: {e}")
            return None
    return _query_aptitud_mock(gdf_predio, cultivo)


def _query_aptitud_mock(gdf_predio, cultivo):
    geom = gdf_predio.geometry.iloc[0]
    return gpd.GeoDataFrame(
        [{"aptitud": "Alta"}],
        geometry=[geom], crs="EPSG:4326",
    )


def _query_aptitud_real(gdf_predio, cultivo):
    tabla = "aptitud_cafe_mvp" if cultivo == "café" else "aptitud_platano_mvp"
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    sql = text(f"""
        SELECT
            aptitud,
            ST_AsGeoJSON(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
            )::json AS geojson
        FROM {tabla}
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql, {"geom": json.dumps(geojson_predio)}).fetchall()
    if not rows:
        return None
    records, geometries = [], []
    for row in rows:
        gj = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
        try:
            geom = shape(gj)
            if not geom.is_empty:
                records.append({"aptitud": row.aptitud})
                geometries.append(geom)
        except Exception:
            continue
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


# ════════════════════════════════════════════════════════════════════════════
#  4 · CONSTRUCCIONES
# ════════════════════════════════════════════════════════════════════════════

def get_construcciones(gdf_predio: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """
    Dado el GeoDataFrame del predio, devuelve las construcciones
    que intersectan con él (tabla construcciones_mvp).
    Retorna GeoDataFrame con columnas tipo_const, numero_pis, o None.
    """
    if USE_REAL_DB and DB_LIBS_OK:
        try:
            return _query_construcciones_real(gdf_predio)
        except Exception as e:
            import streamlit as st
            st.error(f"❌ Error consultando construcciones: {e}")
            return None
    return _query_construcciones_mock(gdf_predio)


def _query_construcciones_mock(gdf_predio):
    geom = gdf_predio.geometry.iloc[0].centroid.buffer(0.001)
    return gpd.GeoDataFrame(
        [{"tipo_const": "Casa", "numero_pis": 1, "codigo": "MOCK001"}],
        geometry=[geom], crs="EPSG:4326",
    )


def _query_construcciones_real(gdf_predio):
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    sql = text("""
        SELECT
            codigo,
            tipo_const,
            numero_pis,
            ST_AsGeoJSON(geom)::json AS geojson
        FROM construcciones_mvp
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql, {"geom": json.dumps(geojson_predio)}).fetchall()
    if not rows:
        return None
    records, geometries = [], []
    for row in rows:
        gj = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
        try:
            geom = shape(gj)
            if not geom.is_empty:
                records.append({
                    "codigo":     row.codigo,
                    "tipo_const": row.tipo_const or "—",
                    "numero_pis": row.numero_pis or 0,
                })
                geometries.append(geom)
        except Exception:
            continue
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")
