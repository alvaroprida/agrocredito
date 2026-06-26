"""
utils/risk_indicators.py

Calcula indicadores de riesgo agroclimático a partir de datos diarios
(Open-Meteo) usando la matriz de vulnerabilidad consolidada.

Approach:
  1. Para cada indicador del cultivo, filtra los datos al período relevante.
     Prioridad: Meses_cálculo (lista) → Fecha_inicio/Fecha_fin → año completo.
     Los períodos que cruzan el año (ej. oct→abr) usan datos de dos años.
  2. Calcula el valor del indicador año a año (10 años).
  3. Interpola el score [0,1] en la curva de vulnerabilidad.
     Los breakpoints pueden ser numéricos o en formato MM/DD (ej. soil_warming).
  4. Usa el percentil anual del "año adverso" como referencia para el riesgo
     crediticio: P80 para indicadores de curva creciente (más valor = más
     riesgo) y P20 para los de curva decreciente (menos valor = más riesgo,
     p.ej. 'Lluvia - Necesidades hídricas' / índice de sequía).
"""

import ast
from datetime import datetime
import numpy as np
import pandas as pd
from pathlib import Path

# ── Ruta de la matriz ─────────────────────────────────────────────────────────
_MATRIX_PATH = Path(__file__).parent.parent / "datos" / "indicadores" / "matriz_vulnerabilidad_consolidada.xlsx"

def _get_matrix() -> pd.DataFrame:
    return pd.read_excel(_MATRIX_PATH)


def get_indicators_for_crop(cultivo_app: str) -> pd.DataFrame:
    """Retorna filas de la matriz correspondientes al cultivo."""
    df = _get_matrix()
    return df[df["Cultivo_app"] == cultivo_app].reset_index(drop=True)


def needed_extra_hourly(cultivo_app: str) -> list[str]:
    """
    Retorna las variables horarias adicionales (más allá de rh_mean) que necesitan
    los indicadores del cultivo. Evita descargar datos innecesarios.
    """
    df = get_indicators_for_crop(cultivo_app)
    extras = set()
    for _, row in df.iterrows():
        for var, _, _ in _parse_conditions(row):
            col = _VAR_MAP.get(var)
            if col == "sm_0_7":
                extras.add("soil_moisture_0_to_7cm")
            elif col == "ws_10m":
                extras.add("wind_speed_10m")
    return list(extras)


def crops_with_matrix() -> list[str]:
    """Lista de cultivos con matriz disponible (nombres del desplegable de la app)."""
    df = _get_matrix()
    return sorted(df["Cultivo_app"].dropna().unique().tolist())


# ── Scoring ───────────────────────────────────────────────────────────────────

def _parse_breakpoint(v):
    """
    Convierte un breakpoint de curva a float.
    Soporta números y formato MM/DD o MM/DD_MM/DD (solo la primera fecha).
    MM/DD se convierte a día del año usando un año no bisiesto.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "—", "-"):
        return None
    # Rango MM/DD_MM/DD → tomar primera fecha
    if "_" in s:
        s = s.split("_")[0].strip()
    # Fecha MM/DD → convertir a día del año
    if "/" in s:
        try:
            dt = datetime.strptime(f"2001/{s}", "%Y/%m/%d")
            return float(dt.timetuple().tm_yday)
        except ValueError:
            return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _curve_is_decreasing(row: pd.Series) -> bool:
    """
    True si la vulnerabilidad CRECE cuando el valor del indicador DISMINUYE
    (curva decreciente). Caso típico: 'Lluvia - Necesidades hídricas', donde a
    menor precipitación acumulada mayor riesgo de sequía.

    Detecta tanto el español ('decreciente') como el inglés ('decreasing').
    """
    curve = str(row.get("Forma_curva", "")).lower()
    return "decre" in curve   # 'decreciente' (ES) · 'decreasing' (EN)


def score_from_curve(value: float, row: pd.Series) -> float:
    """
    Interpola el score [0,1] dado el valor del indicador y los breakpoints
    de la curva de vulnerabilidad. Soporta curvas crecientes y decrecientes,
    y breakpoints en formato numérico o MM/DD.
    """
    bp_raw = [
        (row.get("Sin_riesgo_0"),     0.00),
        (row.get("Riesgo_bajo_0.25"), 0.25),
        (row.get("Riesgo_medio_0.5"), 0.50),
        (row.get("Riesgo_alto_0.75"), 0.75),
        (row.get("Riesgo_extremo_1"), 1.00),
    ]
    bp = []
    for raw_v, s in bp_raw:
        parsed = _parse_breakpoint(raw_v)
        if parsed is not None:
            bp.append((parsed, s))
    if len(bp) < 2:
        return np.nan

    vals   = [v for v, _ in bp]
    scores = [s for _, s in bp]
    decr   = _curve_is_decreasing(row)

    if decr:
        if value >= vals[0]:  return 0.0
        if value <= vals[-1]: return 1.0
        for i in range(len(vals) - 1):
            if vals[i + 1] <= value <= vals[i]:
                t = (vals[i] - value) / (vals[i] - vals[i + 1])
                return scores[i] + t * (scores[i + 1] - scores[i])
    else:
        if value <= vals[0]:  return 0.0
        if value >= vals[-1]: return 1.0
        for i in range(len(vals) - 1):
            if vals[i] <= value <= vals[i + 1]:
                t = (value - vals[i]) / (vals[i + 1] - vals[i])
                return scores[i] + t * (scores[i + 1] - scores[i])
    return np.nan


def score_to_label(score: float) -> str:
    if pd.isna(score):  return "Sin datos"
    if score <= 0.00:   return "Sin riesgo"
    if score <= 0.25:   return "Riesgo bajo"
    if score <= 0.50:   return "Riesgo medio"
    if score <= 0.75:   return "Riesgo alto"
    return "Riesgo extremo"


def score_to_color(score: float) -> str:
    if pd.isna(score):  return "gris"
    if score <= 0.00:   return "verde"
    if score <= 0.25:   return "amarillo"
    if score <= 0.50:   return "naranja"
    if score <= 0.75:   return "rojo"
    return "granate"


# ── Parsing de condiciones ────────────────────────────────────────────────────

# Mapeo nombre de variable (matriz) → columna en el DataFrame climático
_VAR_MAP = {
    "tmin":          "tmin",
    "tmax":          "tmax",
    "tavg":          "tavg",
    "pr":            "pr",
    "rh_mean":       "rh_mean",
    "gw_10m":        "gw_10m",
    "ws_2m":         "ws_10m",   # aproximación logarítmica
    "ws_10m":        "ws_10m",
    "ssrd_mean":     "ssrd",
    "ssrd":          "ssrd",
    "thi":           "thi",
    "soil_sat":      "soil_sat",
}


def _parse_conditions(row: pd.Series) -> list[tuple[str, str, float]]:
    """Descompone Variable_cálculo / Tipo_cálculo / Umbral en lista de (var, op, thr)."""
    var_str = str(row.get("Variable_cálculo", "")).strip()
    op_str  = str(row.get("Tipo_cálculo",     "")).strip()
    thr_str = str(row.get("Umbral",           "")).strip()

    vars_ = [v.strip() for v in var_str.split(" AND ")]
    ops_  = [o.strip() for o in op_str.split(" AND ")]
    thrs_ = [t.strip() for t in thr_str.split(" AND ")]

    result = []
    for var, op, thr in zip(vars_, ops_, thrs_):
        try:
            result.append((var.lower(), op, float(thr)))
        except (ValueError, TypeError):
            continue
    return result


def _day_mask(df: pd.DataFrame, conditions: list[tuple]) -> pd.Series:
    """Retorna máscara booleana: días que cumplen TODAS las condiciones."""
    mask = pd.Series(True, index=df.index)
    for var, op, thr in conditions:
        if "tmax - tmin" in var or "tmax-tmin" in var:
            col = df["tmax"] - df["tmin"]
        elif "water in soil" in var or "soil" in var:
            col = df["soil_sat"]
        elif var in _VAR_MAP:
            col = df[_VAR_MAP[var]]
        else:
            continue

        if op == "<":    mask &= col < thr
        elif op == ">":  mask &= col > thr
        elif op == "<=": mask &= col <= thr
        elif op == ">=": mask &= col >= thr

    return mask


def _filter_period(df_climate, row, yr):
    """
    Filtra el DataFrame climático al período relevante para el indicador en el año yr.

    Prioridad:
      1. Meses_cálculo (lista de enteros) — sin soporte cross-year.
      2. Fecha_inicio / Fecha_fin (formato MM-DD) — soporta períodos cross-year
         (ej. 10-01 → 04-30 usa datos de yr y yr+1).
      3. Año completo por defecto.
    """
    # 1. Meses_cálculo
    mc_raw = row.get("Meses_cálculo", "")
    try:
        months = ast.literal_eval(str(mc_raw))
        if months:
            df_yr = df_climate[df_climate["year"] == yr]
            return df_yr[df_yr["month"].isin(months)]
    except Exception:
        pass

    # 2. Fecha_inicio / Fecha_fin
    fi_raw = str(row.get("Fecha_inicio", "")).strip()
    ff_raw = str(row.get("Fecha_fin",    "")).strip()
    is_full_year = (
        fi_raw in ("nan", "01-01", "") and
        ff_raw in ("nan", "12-31", "")
    )
    if not is_full_year and fi_raw and ff_raw and fi_raw != "nan" and ff_raw != "nan":
        try:
            fi_m, fi_d = map(int, fi_raw.split("-"))
            ff_m, ff_d = map(int, ff_raw.split("-"))
            fi_date = pd.Timestamp(yr, fi_m, fi_d)
            # Cross-year: fin anterior a inicio (ej. oct→abr)
            if (ff_m, ff_d) < (fi_m, fi_d):
                ff_date = pd.Timestamp(yr + 1, ff_m, ff_d)
            else:
                ff_date = pd.Timestamp(yr, ff_m, ff_d)
            return df_climate[
                (df_climate["date"] >= fi_date) &
                (df_climate["date"] <= ff_date)
            ]
        except Exception:
            pass

    # 3. Año completo
    return df_climate[df_climate["year"] == yr]


# ── Cálculo del valor del indicador ──────────────────────────────────────────

def _max_consecutive_run(mask: pd.Series) -> int:
    """Longitud del run más largo de True en la serie booleana."""
    max_run = run = 0
    for v in mask.values:
        run = run + 1 if v else 0
        max_run = max(max_run, run)
    return max_run


def _compute_annual_value(df_climate: pd.DataFrame, row: pd.Series, yr: int) -> float:
    """Calcula el valor del indicador para el año (o temporada) yr."""
    iid        = str(row.get("Indicador_id", "")).lower()
    conditions = _parse_conditions(row)

    df_filt = _filter_period(df_climate, row, yr)

    if df_filt.empty:
        return np.nan

    # ── Precipitación acumulada (sin condición de umbral) ─────────────
    if "cumulative_precipitation" in iid and not conditions:
        return float(df_filt["pr"].sum())

    # ── Meses secos consecutivos ──────────────────────────────────────
    if "consecutive_dry_month" in iid:
        monthly_pr = df_filt.groupby("month")["pr"].sum()
        thr = conditions[0][2] if conditions else 60.0
        dry = (monthly_pr < thr)
        return float(_max_consecutive_run(dry))

    # ── Calentamiento del suelo ───────────────────────────────────────
    if "soil_warming" in iid:
        thr = conditions[0][2] if conditions else 8.0
        cond = (df_filt["tavg"] > thr)
        run = 0
        for i, v in enumerate(cond.values):
            run = run + 1 if v else 0
            if run >= 5:
                return float(df_filt["doy"].iloc[i - 4])
        return 180.0  # no alcanzado → riesgo alto

    # ── Precipitación acumulada (con threshold) ───────────────────────
    if "cumulative" in iid:
        if conditions:
            mask = _day_mask(df_filt, conditions)
            return float(df_filt.loc[mask, "pr"].sum())
        return float(df_filt["pr"].sum())

    # ── Construir máscara ─────────────────────────────────────────────
    if not conditions:
        return np.nan
    mask = _day_mask(df_filt, conditions)

    # ── Días consecutivos ─────────────────────────────────────────────
    if "consecutive" in iid:
        return float(_max_consecutive_run(mask))

    # ── Conteo de días ────────────────────────────────────────────────
    return float(mask.sum())


# ── Agregación global ────────────────────────────────────────────────────────

def aggregate_risk_score(df_risk: pd.DataFrame) -> float:
    """
    Score global: max score dentro de cada categoría de riesgo, luego media entre categorías.
    Evita que categorías con muchos indicadores dominen el resultado.
    Retorna float [0,1] o NaN si no hay datos.
    """
    cat_scores = (
        df_risk.groupby("Categoría_riesgo")["score_p80"]
        .max()
        .dropna()
    )
    if cat_scores.empty:
        return np.nan
    return float(cat_scores.mean())


# ── Función principal ─────────────────────────────────────────────────────────

def compute_risk_for_crop(
    df_climate: pd.DataFrame,
    cultivo_app: str,
) -> pd.DataFrame:
    """
    Calcula el score de riesgo histórico para cada indicador del cultivo.

    Retorna DataFrame con:
        Indicador_id, Nombre_indicador, Categoría_riesgo, Unidad,
        valor_medio, valor_p80, valor_max,
        score_medio, score_p80, score_max,
        riesgo_label, riesgo_color,
        umbrales (Sin_riesgo_0 … Riesgo_extremo_1),
        Forma_curva, Definición
    """
    indicators = get_indicators_for_crop(cultivo_app)
    if indicators.empty:
        return pd.DataFrame()

    years   = sorted(df_climate["year"].unique())
    records = []

    for _, row in indicators.iterrows():
        annual_vals = []
        for yr in years:
            val = _compute_annual_value(df_climate, row, yr)
            if not np.isnan(val):
                annual_vals.append(val)

        if not annual_vals:
            continue

        # Percentil de referencia para el "año adverso":
        #   · curva creciente  → riesgo crece con el valor → cola ALTA  (P80)
        #   · curva decreciente→ riesgo crece al bajar el valor → cola BAJA (P20)
        #     (excepción 'Lluvia - Necesidades hídricas': precipitación acumulada,
        #      índice de sequía; menos lluvia = más riesgo)
        decr = _curve_is_decreasing(row)
        pct  = 20 if decr else 80

        v_mean = float(np.mean(annual_vals))
        v_p80  = float(np.percentile(annual_vals, pct))   # P80 creciente / P20 decreciente
        v_max  = float(np.max(annual_vals))

        s_mean = score_from_curve(v_mean, row)
        s_p80  = score_from_curve(v_p80,  row)
        s_max  = score_from_curve(v_max,  row)

        s_ref = s_p80 if not np.isnan(s_p80) else s_mean

        records.append({
            "Indicador_id":               row["Indicador_id"],
            "Nombre_indicador":           row["Nombre_indicador"],
            "Categoría_riesgo":           row["Categoría_riesgo"],
            "Unidad":                     row["Unidad"],
            "valor_medio":                round(v_mean, 2),
            "valor_p80":                  round(v_p80,  2),
            "valor_max":                  round(v_max,  2),
            "percentil_ref":              pct,
            "score_medio":                round(s_mean, 3) if not np.isnan(s_mean) else None,
            "score_p80":                  round(s_p80,  3) if not np.isnan(s_p80)  else None,
            "score_max":                  round(s_max,  3) if not np.isnan(s_max)  else None,
            "riesgo_label":               score_to_label(s_ref),
            "riesgo_color":               score_to_color(s_ref),
            "Sin_riesgo_0":               row.get("Sin_riesgo_0"),
            "Riesgo_bajo_0.25":           row.get("Riesgo_bajo_0.25"),
            "Riesgo_medio_0.5":           row.get("Riesgo_medio_0.5"),
            "Riesgo_alto_0.75":           row.get("Riesgo_alto_0.75"),
            "Riesgo_extremo_1":           row.get("Riesgo_extremo_1"),
            "Forma_curva":                row.get("Forma_curva"),
            "Definición":                 row.get("Definición"),
            "Impacto_rendimiento_alto":   row.get("Impacto_rendimiento_alto", "—"),
            "Impacto_rendimiento_extremo":row.get("Impacto_rendimiento_extremo", "—"),
        })

    return pd.DataFrame(records)
