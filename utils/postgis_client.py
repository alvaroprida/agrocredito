"""
utils/postgis_client.py
Funciones de consulta a la base PostGIS / Supabase.

Funciones:
    get_predio_por_punto(lat, lon)       → polígono + código + departamento + área
    get_frontera(gdf_predio)             → tipo de frontera agrícola
    get_aptitud(gdf_predio, cultivo)     → aptitud del predio al cultivo
    get_valor_potencial(gdf_predio)      → valor potencial (ufh_mvp)
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


# ── Helper: ejecutar query de intersección ────────────────────────────────────

def _query_intersection(sql, geojson_predio, columns, mock_records=None):
    """
    Ejecuta una query de intersección contra Supabase.
    Devuelve GeoDataFrame o None.
    """
    if not (USE_REAL_DB and DB_LIBS_OK):
        if mock_records is None:
            return None
        geom = shape(geojson_predio)
        return gpd.GeoDataFrame(mock_records, geometry=[geom]*len(mock_records), crs="EPSG:4326")

    try:
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
                    records.append({c: getattr(row, c) for c in columns})
                    geometries.append(geom)
            except Exception:
                continue
        if not records:
            return None
        return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")
    except Exception as e:
        import streamlit as st
        st.error(f"❌ Error consultando PostGIS: {e}")
        return None


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
    geom = shape(data["geojson"])
    gdf = gpd.GeoDataFrame(
        [{"codigo": data["codigo"], "departamento": data["departamento"], "area_ha": data["area_ha"]}],
        geometry=[geom], crs="EPSG:4326",
    )
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
    Devuelve zonas de frontera agrícola que intersectan el predio.
    Columnas: tipo_condi, area_ha, pct_predio
    """
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    area_predio_ha = float(gdf_predio.geometry.iloc[0].area * (111320 ** 2) / 10000)

    sql = text("""
        SELECT
            tipo_condi,
            ROUND((ST_Area(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
                ::geography) / 10000)::numeric, 2) AS area_ha,
            ST_AsGeoJSON(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
            )::json AS geojson
        FROM frontera_mvp
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)

    gdf = _query_intersection(
        sql, geojson_predio, ["tipo_condi", "area_ha"],
        mock_records=[{"tipo_condi": "Frontera agrícola", "area_ha": area_predio_ha}]
    )
    if gdf is not None and area_predio_ha > 0:
        gdf["area_ha"]    = gdf["area_ha"].apply(lambda x: float(x) if x is not None else 0.0)
        gdf["pct_predio"] = (gdf["area_ha"] / area_predio_ha * 100).round(1)
    return gdf


# ════════════════════════════════════════════════════════════════════════════
#  3 · APTITUD DEL CULTIVO
# ════════════════════════════════════════════════════════════════════════════

def get_aptitud(gdf_predio: gpd.GeoDataFrame, cultivo: str) -> gpd.GeoDataFrame | None:
    """
    Devuelve zonas de aptitud que intersectan el predio.
    Columnas: aptitud, area_ha, pct_predio
    """
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    area_predio_ha = float(gdf_predio.geometry.iloc[0].area * (111320 ** 2) / 10000)
    tabla = "aptitud_cafe_mvp" if cultivo == "café" else "aptitud_platano_mvp"

    sql = text(f"""
        SELECT
            aptitud,
            ROUND((ST_Area(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
                ::geography) / 10000)::numeric, 2) AS area_ha,
            ST_AsGeoJSON(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
            )::json AS geojson
        FROM {tabla}
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)

    gdf = _query_intersection(
        sql, geojson_predio, ["aptitud", "area_ha"],
        mock_records=[{"aptitud": "Alta", "area_ha": area_predio_ha}]
    )
    if gdf is not None and area_predio_ha > 0:
        gdf["area_ha"]    = gdf["area_ha"].apply(lambda x: float(x) if x is not None else 0.0)
        gdf["pct_predio"] = (gdf["area_ha"] / area_predio_ha * 100).round(1)
    return gdf


# ════════════════════════════════════════════════════════════════════════════
#  4 · VALOR POTENCIAL (UFH)
# ════════════════════════════════════════════════════════════════════════════

def get_valor_potencial(gdf_predio: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """
    Devuelve zonas de valor potencial (ufh_mvp) que intersectan el predio.
    Columnas: clase_ufh, area_ha, pct_predio
    """
    geojson_predio = gdf_predio.geometry.iloc[0].__geo_interface__
    area_predio_ha = float(gdf_predio.geometry.iloc[0].area * (111320 ** 2) / 10000)

    sql = text("""
        SELECT
            clase_ufh,
            ROUND((ST_Area(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
                ::geography) / 10000)::numeric, 2) AS area_ha,
            ST_AsGeoJSON(
                ST_Intersection(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
            )::json AS geojson
        FROM ufh_mvp
        WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)

    gdf = _query_intersection(
        sql, geojson_predio, ["clase_ufh", "area_ha"],
        mock_records=[{"clase_ufh": "03", "area_ha": area_predio_ha}]
    )
    if gdf is not None and area_predio_ha > 0:
        gdf["area_ha"]    = gdf["area_ha"].apply(lambda x: float(x) if x is not None else 0.0)
        gdf["pct_predio"] = (gdf["area_ha"] / area_predio_ha * 100).round(1)
    return gdf


# ════════════════════════════════════════════════════════════════════════════
#  5 · CONSTRUCCIONES
# ════════════════════════════════════════════════════════════════════════════

def get_construcciones(gdf_predio: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """
    Devuelve construcciones del predio via JOIN predios_mvp.codigo = construcciones_mvp.terreno_co.
    Columnas: codigo, identifica, tipo_const, numero_pis, area_ha
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
        [{"codigo": "MOCK001", "identifica": "Casa principal",
          "tipo_const": "Casa", "numero_pis": 1, "area_ha": 0.02}],
        geometry=[geom], crs="EPSG:4326",
    )


def _query_construcciones_real(gdf_predio):
    codigo_predio = gdf_predio["codigo"].iloc[0]
    sql = text("""
        SELECT
            c.codigo,
            c.identifica,
            c.tipo_const,
            c.numero_pis,
            ROUND((ST_Area(c.geom::geography) / 10000)::numeric, 6) AS area_ha,
            ST_AsGeoJSON(c.geom)::json AS geojson
        FROM construcciones_mvp c
        JOIN predios_mvp p ON c.terreno_co = p.codigo
        WHERE p.codigo = :codigo
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql, {"codigo": codigo_predio}).fetchall()
    if not rows:
        return None
    records, geometries = [], []
    for row in rows:
        gj = row.geojson if isinstance(row.geojson, dict) else json.loads(row.geojson)
        try:
            geom = shape(gj)
            if not geom.is_empty:
                records.append({
                    "codigo":     row.codigo or "—",
                    "identifica": row.identifica or "—",
                    "tipo_const": row.tipo_const or "—",
                    "numero_pis": int(row.numero_pis) if row.numero_pis else 0,
                    "area_ha":    float(row.area_ha) if row.area_ha else 0.0,
                })
                geometries.append(geom)
        except Exception:
            continue
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")