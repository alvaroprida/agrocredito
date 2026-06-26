"""
utils/monitoring_indicators.py

Calcula indicadores de monitoreo (Bloques B–E) sobre la serie climática combinada.
Cada indicador se evalúa en 3 horizontes temporales: Hoy, +7 días, +14 días.

Bloque A (NDVI) se calcula externamente desde Google Earth Engine y se adjunta aparte.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import date, timedelta

# ── Configuración por cultivo ─────────────────────────────────────────────────

_CROP_CFG: dict[str, dict] = {
    "Café": {
        "tmax_heat": 32,
        "tmin_cold": 10,
        "dry_days":  45,
        "wind_ms":   None,
        "disease":   {"name": "Roya (Hemileia vastatrix)", "type": "rh",
                      "t_min": 22, "t_max": 27, "rh": 80},
    },
    "Cacao": {
        "tmax_heat": 35,
        "tmin_cold": 16,
        "dry_days":  20,
        "wind_ms":   None,
        "disease":   {
            "name": "Moniliophthora / Phytophthora",
            "type": "multi",
            "conditions": [
                {"t_min": 22, "t_max": 26, "rh": 90},            # Moniliophthora roreri
                {"t_min": 24, "t_max": 28, "rh": 85, "pr": 15},  # Phytophthora palmivora
            ],
        },
    },
    "Papa": {
        "tmax_heat": 25,
        "tmin_cold": -2,
        "dry_days":  15,
        "wind_ms":   None,
        "disease":   {"name": "Gota (Phytophthora)", "type": "rain",
                      "t_min": 10, "t_max": 20},
    },
    "Plátano": {
        "tmax_heat": 38,
        "tmin_cold": 12,
        "dry_days":  20,
        "wind_ms":   18.0,
        "disease":   {"name": "Sigatoka negra", "type": "tmin_tavg",
                      "t_min": 24, "rh": 80},
    },
    "Aguacate (Hass)": {
        "tmax_heat": 35,
        "tmin_cold": 5,
        "dry_days":  30,
        "wind_ms":   15.0,
        "disease":   None,
    },
    "Maíz": {
        "tmax_heat": 35,
        "tmin_cold": 5,
        "dry_days":  25,
        "wind_ms":   12.0,
        "disease":   {"name": "Aspergillus flavus", "type": "heat_rh",
                      "tmax_min": 30, "tmax_max": 37, "rh": 80,
                      "months": [4, 5, 6]},
    },
}
_DEFAULT_CFG: dict = {
    "tmax_heat": 35, "tmin_cold": 5, "dry_days": 25,
    "wind_ms": None, "disease": None,
}

def _cfg(cultivo: str) -> dict:
    if cultivo in _CROP_CFG:
        return _CROP_CFG[cultivo]
    for key, val in _CROP_CFG.items():
        if key.lower() in cultivo.lower() or cultivo.lower() in key.lower():
            return val
    return _DEFAULT_CFG


# ── Paleta semáforo ───────────────────────────────────────────────────────────

SEM_ORDER: dict[str, int] = {"verde": 0, "amarillo": 1, "rojo": 2}
SEM_ICON:  dict[str, str] = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}
SEM_BG:    dict[str, str] = {"verde": "#d1fae5", "amarillo": "#fef9c3", "rojo": "#fee2e2"}
SEM_BD:    dict[str, str] = {"verde": "#059669", "amarillo": "#d97706", "rojo": "#dc2626"}
SEM_TEXT:  dict[str, str] = {"verde": "#065f46", "amarillo": "#713f12", "rojo": "#7f1d1d"}


# ── Semáforo helpers ──────────────────────────────────────────────────────────

def _sem(value: float, thr_y: float, thr_r: float,
         higher_is_worse: bool = True) -> str:
    if pd.isna(value):
        return "verde"
    if higher_is_worse:
        if value >= thr_r:    return "rojo"
        if value >= thr_y:    return "amarillo"
        return "verde"
    else:
        if value <= thr_r:    return "rojo"
        if value <= thr_y:    return "amarillo"
        return "verde"

def _sem_b1(pct: float) -> str:
    """B1: déficit (<40%) y exceso (>200%) son rojo; 40-70% y 130-200% amarillo."""
    if pd.isna(pct):      return "verde"
    if pct < 40 or pct > 200: return "rojo"
    if pct < 70 or pct > 130: return "amarillo"
    return "verde"

def worst_sem(*sems: str) -> str:
    return max(sems, key=lambda s: SEM_ORDER.get(s, 0))


# ── Helpers de serie ──────────────────────────────────────────────────────────

def _win(df: pd.DataFrame, end_date: date, days: int) -> pd.DataFrame:
    """Filtra el DataFrame a la ventana [end_date - days + 1, end_date]."""
    end   = pd.Timestamp(end_date)
    start = end - pd.Timedelta(days=days - 1)
    return df[(df["date"] >= start) & (df["date"] <= end)]

def _max_run(mask: pd.Series) -> int:
    """Longitud del run más largo de True en la máscara booleana."""
    max_r = run = 0
    for v in mask.values:
        run = run + 1 if v else 0
        max_r = max(max_r, run)
    return max_r


# ── Textos de acción por indicador y semáforo ─────────────────────────────────

_ACTIONS: dict[str, dict[str, str]] = {
    "B1": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Verificar si el agricultor reporta estrés hídrico; monitorear próxima quincena.",
        "rojo":     "Activar protocolo de alivio si hay pérdida verificable; contactar al agricultor.",
    },
    "B2": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Contacto preventivo; alertar sobre riesgo de sequía.",
        "rojo":     "Verificar disponibilidad de riego; evaluar extensión de plazo.",
    },
    "B3": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Alerta de posible daño por exceso de humedad.",
        "rojo":     "Documentar evento para seguro; solicitar fotos de campo.",
    },
    "C1": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Registro y seguimiento.",
        "rojo":     "Si NDVI también en alerta: escalar alerta global.",
    },
    "C2": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Alertar sobre riesgo de helada o frío.",
        "rojo":     "Activar documentación seguro; proponer plan de pago diferido.",
    },
    "D1": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Informar al agricultor; recomendar revisión del cultivo.",
        "rojo":     "Verificar pérdidas reportadas; activar protocolo de alivio si se documenta impacto.",
    },
    "E1": {
        "verde":    "Sin acción requerida.",
        "amarillo": "Contacto preventivo.",
        "rojo":     "Verificar daños físicos en cultivo; activar documentación seguro.",
    },
}


# ── Cálculo de indicadores ────────────────────────────────────────────────────

def _b1(df: pd.DataFrame, ytd_clim: dict, end_date: date) -> dict:
    """B1: precipitación acumulada año en curso vs. normal YTD histórica."""
    yr_start = pd.Timestamp(end_date.year, 1, 1)
    end_ts   = pd.Timestamp(end_date)
    ytd_df   = df[(df["date"] >= yr_start) & (df["date"] <= end_ts)]
    actual   = float(ytd_df["pr"].fillna(0).sum())
    doy      = end_date.timetuple().tm_yday
    normal   = ytd_clim.get(doy)
    pct      = (actual / normal * 100) if (normal and normal > 0) else np.nan
    sem      = _sem_b1(pct)
    return {
        "label":    "B1 · Precip. acumulada año en curso",
        "value":    round(actual, 0),
        "normal":   round(normal, 0) if normal else None,
        "pct":      round(pct, 1) if not pd.isna(pct) else None,
        "unit":     "mm YTD",
        "display":  (f"{actual:.0f} mm ({pct:.1f}% de la normal)"
                     if not pd.isna(pct) else f"{actual:.0f} mm"),
        "semaforo": sem,
        "action":   _ACTIONS["B1"][sem],
    }


def _b2(df: pd.DataFrame, cultivo: str, end_date: date) -> dict:
    """B2: días consecutivos sin lluvia (< 1 mm) — ventana 30d."""
    cfg = _cfg(cultivo)
    thr = cfg["dry_days"]
    w   = _win(df, end_date, 30)
    run = _max_run(w["pr"].fillna(0) < 1.0) if not w.empty else 0
    sem = _sem(run, thr * 0.5, thr)
    return {
        "label":    "B2 · Días secos consecutivos",
        "value":    run,
        "unit":     "días / ventana 30d",
        "display":  f"{run} días consecutivos secos (umbral {thr}d)",
        "semaforo": sem,
        "action":   _ACTIONS["B2"][sem],
    }


def _b3(df: pd.DataFrame, end_date: date) -> dict:
    """B3: días consecutivos con lluvia intensa (> 30 mm/día) — ventana 30d."""
    w   = _win(df, end_date, 30)
    run = _max_run(w["pr"].fillna(0) > 30.0) if not w.empty else 0
    sem = _sem(run, 2, 4)
    return {
        "label":    "B3 · Lluvia intensa consecutiva (>30 mm/d)",
        "value":    run,
        "unit":     "días / ventana 30d",
        "display":  f"{run} días consecutivos con lluvia intensa",
        "semaforo": sem,
        "action":   _ACTIONS["B3"][sem],
    }


def _c1(df: pd.DataFrame, cultivo: str, end_date: date) -> dict:
    """C1: temperatura máxima media — ventana 14d."""
    cfg = _cfg(cultivo)
    thr = cfg["tmax_heat"]
    w   = _win(df, end_date, 14)
    val = float(w["tmax"].mean()) if not w.empty else np.nan
    sem = _sem(val, thr, thr + 3)
    return {
        "label":     "C1 · Temp. máxima media (14d)",
        "value":     round(val, 1) if not pd.isna(val) else None,
        "threshold": thr,
        "unit":      "°C",
        "display":   (f"{val:.1f} °C (umbral calor {thr}°C)"
                      if not pd.isna(val) else "Sin datos"),
        "semaforo":  sem,
        "action":    _ACTIONS["C1"][sem],
    }


def _c2(df: pd.DataFrame, cultivo: str, end_date: date) -> dict:
    """C2: días con Tmin bajo umbral de frío — ventana 30d."""
    cfg  = _cfg(cultivo)
    thr  = cfg["tmin_cold"]
    w    = _win(df, end_date, 30)
    days = int((w["tmin"].fillna(999) < thr).sum()) if not w.empty else 0
    sem  = _sem(days, 1, 3)
    return {
        "label":     f"C2 · Días con Tmin < {thr}°C",
        "value":     days,
        "threshold": thr,
        "unit":      "días / ventana 30d",
        "display":   f"{days} días con Tmin < {thr}°C",
        "semaforo":  sem,
        "action":    _ACTIONS["C2"][sem],
    }


def _disease_mask(w: pd.DataFrame, disease: dict) -> pd.Series:
    """Devuelve máscara booleana de días con condiciones favorables para la enfermedad."""
    dtype = disease["type"]

    # Filtro de meses si está definido (ej. Aspergillus sólo abr–jun)
    month_ok = (
        w["month"].isin(disease["months"])
        if "months" in disease
        else pd.Series(True, index=w.index)
    )

    if dtype == "rh":
        return month_ok & (
            (w["tavg"].fillna(-999) >= disease["t_min"]) &
            (w["tavg"].fillna(-999) <= disease["t_max"]) &
            (w["rh_mean"].fillna(0)  > disease["rh"])
        )
    elif dtype == "rain":
        return month_ok & (
            (w["tavg"].fillna(-999) >= disease["t_min"]) &
            (w["tavg"].fillna(-999) <= disease["t_max"]) &
            (w["pr"].fillna(0)       > 0)
        )
    elif dtype == "tmin_tavg":
        return month_ok & (
            (w["tavg"].fillna(-999) > disease["t_min"]) &
            (w["rh_mean"].fillna(0) > disease["rh"])
        )
    elif dtype == "heat_rh":
        # Tmax en rango AND rh > umbral (ej. Aspergillus flavus en Maíz)
        return month_ok & (
            (w["tmax"].fillna(-999) >= disease["tmax_min"]) &
            (w["tmax"].fillna(-999) <= disease["tmax_max"]) &
            (w["rh_mean"].fillna(0)  > disease["rh"])
        )
    elif dtype == "multi":
        # OR de múltiples condiciones (ej. Cacao: Moniliophthora OR Phytophthora)
        combined = pd.Series(False, index=w.index)
        for cond in disease["conditions"]:
            sub = (
                (w["tavg"].fillna(-999) >= cond["t_min"]) &
                (w["tavg"].fillna(-999) <= cond["t_max"]) &
                (w["rh_mean"].fillna(0)  > cond["rh"])
            )
            if "pr" in cond:
                sub = sub & (w["pr"].fillna(0) > cond["pr"])
            combined = combined | sub
        return month_ok & combined

    return pd.Series(False, index=w.index)


def _d1_historical_normal(hist_df: pd.DataFrame, disease: dict, end_date: date) -> float | None:
    """
    Calcula la media histórica de días favorables para la enfermedad en la
    misma ventana de 30 días del calendario, sobre todos los años en hist_df.
    """
    month_day = (end_date.month, end_date.day)
    counts: list[int] = []
    for yr, grp in hist_df.groupby("year"):
        try:
            end_hist = date(yr, *month_day)
        except ValueError:
            continue  # 29 feb en año no bisiesto
        w = _win(grp, end_hist, 30)
        if w.empty:
            continue
        counts.append(int(_disease_mask(w, disease).sum()))
    return float(np.mean(counts)) if counts else None


def _d1(df: pd.DataFrame, hist_df: pd.DataFrame, cultivo: str, end_date: date) -> dict | None:
    """D1: anomalía de días favorables para la enfermedad vs. normal histórica — ventana 30d."""
    cfg     = _cfg(cultivo)
    disease = cfg.get("disease")
    if disease is None:
        return None
    w = _win(df, end_date, 30)
    if w.empty:
        return None

    days   = int(_disease_mask(w, disease).sum())
    normal = _d1_historical_normal(hist_df, disease, end_date)

    if normal is None or normal == 0:
        # Sin referencia histórica: semáforo basado en días absolutos como fallback
        sem = _sem(days, 6, 16)
        display = f"{days} días favorables (sin normal histórica)"
    else:
        pct = days / normal * 100
        if pct > 140 or days > normal + 7:
            sem = "rojo"
        elif pct > 115 or days > normal + 3:
            sem = "amarillo"
        else:
            sem = "verde"
        display = f"{days} días favorables (normal: {normal:.0f}d, {pct:.0f}% de la media)"

    return {
        "label":    f"D1 · Condiciones favorables {disease['name']}",
        "value":    days,
        "normal":   round(normal, 1) if normal is not None else None,
        "disease":  disease["name"],
        "unit":     "días / ventana 30d",
        "display":  display,
        "semaforo": sem,
        "action":   _ACTIONS["D1"][sem],
    }


def _e1(df: pd.DataFrame, cultivo: str, end_date: date) -> dict | None:
    """E1: días con ráfagas sobre umbral — ventana 30d. Solo cultivos susceptibles."""
    cfg    = _cfg(cultivo)
    thr_ms = cfg.get("wind_ms")
    if thr_ms is None:
        return None
    thr_kmh = round(thr_ms * 3.6, 0)
    w       = _win(df, end_date, 30)
    days    = int((w["gw_10m"].fillna(0) > thr_kmh).sum()) if not w.empty else 0
    sem     = _sem(days, 1, 5)
    return {
        "label":         f"E1 · Ráfagas > {thr_kmh:.0f} km/h",
        "value":         days,
        "threshold_ms":  thr_ms,
        "threshold_kmh": thr_kmh,
        "unit":          "días / ventana 30d",
        "display":       f"{days} días (umbral {thr_kmh:.0f} km/h)",
        "semaforo":      sem,
        "action":        _ACTIONS["E1"][sem],
    }


# ── Alerta global ─────────────────────────────────────────────────────────────

def _global_alert(inds: dict) -> str:
    sems = [v["semaforo"] for v in inds.values() if v and "semaforo" in v]
    return max(sems, key=lambda s: SEM_ORDER.get(s, 0)) if sems else "verde"


# ── Función principal ─────────────────────────────────────────────────────────

HORIZONS: dict[str, int] = {"Hoy": 0, "+7 días": 7, "+14 días": 14}


def compute_all_indicators(
    combined_df: pd.DataFrame,
    cultivo:     str,
    ytd_clim:    dict,
    today:       date,
    hist_df:     pd.DataFrame | None = None,
) -> dict:
    """
    Calcula indicadores B–E para los 3 horizontes temporales.

    Retorna:
        {
            "Hoy":      {"B1": {...}, "B2": {...}, ..., "global": "verde"|"amarillo"|"rojo"},
            "+7 días":  {...},
            "+14 días": {...},
        }
    """
    results: dict = {}
    for horizon, offset in HORIZONS.items():
        end  = today + timedelta(days=offset)
        _hist = hist_df if hist_df is not None else combined_df
        inds = {
            "B1": _b1(combined_df, ytd_clim, end),
            "B2": _b2(combined_df, cultivo, end),
            "B3": _b3(combined_df, end),
            "C1": _c1(combined_df, cultivo, end),
            "C2": _c2(combined_df, cultivo, end),
            "D1": _d1(combined_df, _hist, cultivo, end),
            "E1": _e1(combined_df, cultivo, end),
        }
        active = {k: v for k, v in inds.items() if v is not None}
        inds["global"] = _global_alert(active)
        results[horizon] = inds
    return results
