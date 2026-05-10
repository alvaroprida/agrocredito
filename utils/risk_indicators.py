"""
utils/risk_indicators.py

Calcula indicadores de riesgo agroclimático a partir de datos diarios
(Open-Meteo) usando la matriz de vulnerabilidad consolidada.

Approach:
  1. Para cada indicador del cultivo, filtra los datos al período relevante.
  2. Calcula el valor del indicador año a año (10 años).
  3. Interpola el score [0,1] en la curva de vulnerabilidad.
  4. Usa el P80 anual como referencia para el análisis de riesgo crediticio.
"""

import ast
import numpy as np
import pandas as pd
from pathlib import Path

# ── Ruta de la matriz ─────────────────────────────────────────────────────────
_MATRIX_PATH = Path(__file__).parent.parent / "datos" / "indicadores" / "matriz_vulnerabilidad_consolidada.xlsx"

_matrix_cache: pd.DataFrame | None = None


def _get_matrix() -> pd.DataFrame:
    global _matrix_cache
    if _matrix_cache is None:
        _matrix_cache = pd.read_excel(_MATRIX_PATH)
    return _matrix_cache


def get_indicators_for_crop(cultivo_app: str) -> pd.DataFrame:
    """Retorna filas de la matriz correspondientes al cultivo."""
    df = _get_matrix()
    return df[df["Cultivo_app"] == cultivo_app].reset_index(drop=True)


def crops_with_matrix() -> list[str]:
    """Lista de cultivos con matriz disponible (nombres del desplegable de la app)."""
    df = _get_matrix()
    return sorted(df["Cultivo_app"].dropna().unique().tolist())


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_from_curve(value: float, row: pd.Series) -> float:
    """
    Interpola el score [0,1] dado el valor del indicador y los breakpoints
    de la curva de vulnerabilidad. Soporta curvas crecientes y decrecientes.
    """
    bp_raw = [
        (row.get("Sin_riesgo_0"),   0.00),
        (row.get("Riesgo_bajo_0.25"), 0.25),
        (row.get("Riesgo_medio_0.5"), 0.50),
        (row.get("Riesgo_alto_0.75"), 0.75),
        (row.get("Riesgo_extremo_1"), 1.00),
    ]
    bp = []
    for v, s in bp_raw:
        try:
            bp.append((float(v), s))
        except (TypeError, ValueError):
            continue
    if len(bp) < 2:
        return np.nan

    vals   = [v for v, _ in bp]
    scores = [s for _, s in bp]
    curve  = str(row.get("Forma_curva", "")).lower()
    decr   = "decreas" in curve

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
    if pd.isna(score): return "rojo"
    if score <= 0.25:  return "verde"
    if score <= 0.50:  return "naranja"
    return "rojo"


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
        # Caso especial: diferencia de temperatura
        if "tmax - tmin" in var or "tmax-tmin" in var:
            col = df["tmax"] - df["tmin"]
        elif "water in soil" in var or "soil" in var:
            col = df["soil_sat"]
        elif var in _VAR_MAP:
            col = df[_VAR_MAP[var]]
        else:
            continue  # variable no disponible → ignorar condición

        if op == "<":   mask &= col < thr
        elif op == ">": mask &= col > thr
        elif op == "<=": mask &= col <= thr
        elif op == ">=": mask &= col >= thr

    return mask


def _parse_months(row: pd.Series) -> list[int] | None:
    """Extrae lista de meses desde Meses_cálculo. None = todos los meses."""
    raw = row.get("Meses_cálculo", "")
    try:
        months = ast.literal_eval(str(raw))
        return months if months else None
    except Exception:
        return None


# ── Cálculo del valor del indicador ──────────────────────────────────────────

def _max_consecutive_run(mask: pd.Series) -> int:
    """Longitud del run más largo de True en la serie booleana."""
    max_run = run = 0
    for v in mask.values:
        run = run + 1 if v else 0
        max_run = max(max_run, run)
    return max_run


def _compute_annual_value(df_year: pd.DataFrame, row: pd.Series) -> float:
    """Calcula el valor del indicador para un año dado."""
    iid        = str(row.get("Indicador_id", "")).lower()
    conditions = _parse_conditions(row)
    months     = _parse_months(row)

    # Filtrar por meses relevantes
    df_filt = df_year[df_year["month"].isin(months)] if months else df_year

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

    # ── Calentamiento del suelo (DOY del primer día ≥ 5 cons. con Tavg > umbral) ──
    if "soil_warming" in iid:
        thr = conditions[0][2] if conditions else 8.0
        cond = (df_filt["tavg"] > thr)
        run = 0
        for i, v in enumerate(cond.values):
            run = run + 1 if v else 0
            if run >= 5:
                return float(df_filt["doy"].iloc[i - 4])
        return 180.0  # no alcanzado → riesgo alto

    # ── Precipitación acumulada (con threshold para filtrar días) ─────
    if "cumulative" in iid:
        if conditions:
            mask = _day_mask(df_filt, conditions)
            return float(df_filt.loc[mask, "pr"].sum())
        return float(df_filt["pr"].sum())

    # ── Construir máscara de días que cumplen la condición ────────────
    if not conditions:
        return np.nan
    mask = _day_mask(df_filt, conditions)

    # ── Días consecutivos ─────────────────────────────────────────────
    if "consecutive" in iid:
        return float(_max_consecutive_run(mask))

    # ── Conteo de días ────────────────────────────────────────────────
    return float(mask.sum())


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
        Forma_curva, Definición,
        Impacto_rendimiento_alto, Impacto_rendimiento_extremo
    """
    indicators = get_indicators_for_crop(cultivo_app)
    if indicators.empty:
        return pd.DataFrame()

    years   = sorted(df_climate["year"].unique())
    records = []

    for _, row in indicators.iterrows():
        annual_vals = []
        for yr in years:
            df_yr = df_climate[df_climate["year"] == yr]
            val   = _compute_annual_value(df_yr, row)
            if not np.isnan(val):
                annual_vals.append(val)

        if not annual_vals:
            continue

        v_mean = float(np.mean(annual_vals))
        v_p80  = float(np.percentile(annual_vals, 80))
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
