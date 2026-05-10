"""
utils/climate_data.py

Descarga datos climáticos históricos diarios desde Open-Meteo Historical API
(ERA5 reanalysis). Gratuita, sin API key, cobertura global, desde 1940.

Variables diarias: Tmax, Tmin, Tavg, precipitación, rachas de viento, radiación.
Variables horarias (agredadas a diario): humedad relativa, velocidad viento, humedad suelo.
"""

import numpy as np
import pandas as pd
import requests
from datetime import date, timedelta

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "wind_gusts_10m_max",
    "shortwave_radiation_sum",
]

# Siempre se incluye humedad relativa (útil en D1 y necesaria en muchos cultivos).
# Las demás se añaden solo si el cultivo las requiere.
_HOURLY_BASE = ["relative_humidity_2m"]

_RENAME_DAILY = {
    "temperature_2m_max":     "tmax",
    "temperature_2m_min":     "tmin",
    "temperature_2m_mean":    "tavg",
    "precipitation_sum":      "pr",
    "wind_gusts_10m_max":     "gw_10m",
    "shortwave_radiation_sum":"ssrd",
}


def get_historical_climate(
    lat: float,
    lon: float,
    n_years: int = 10,
    extra_hourly: list[str] | None = None,
) -> pd.DataFrame:
    """
    Descarga n_years de datos climáticos diarios para (lat, lon).

    Retorna DataFrame con columnas:
        date, tmax, tmin, tavg, pr, gw_10m, ssrd,
        rh_mean [+ ws_10m, sm_0_7 si se requieren por el cultivo],
        year, month, doy, thi, soil_sat
    """
    end   = date.today() - timedelta(days=6)
    start = date(end.year - n_years, end.month, end.day)

    hourly_vars = _HOURLY_BASE + [v for v in (extra_hourly or []) if v not in _HOURLY_BASE]

    resp = requests.get(OPEN_METEO_URL, params={
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "daily":      ",".join(_DAILY_VARS),
        "hourly":     ",".join(hourly_vars),
        "timezone":   "auto",
    }, timeout=60)
    resp.raise_for_status()
    raw = resp.json()

    # ── DataFrame diario ─────────────────────────────────────────────
    df = pd.DataFrame(raw["daily"])
    df.rename(columns={"time": "date"}, inplace=True)
    df.rename(columns=_RENAME_DAILY, inplace=True)
    df["date"] = pd.to_datetime(df["date"])

    # ── Agregar horario → diario ──────────────────────────────────────
    df_h = pd.DataFrame(raw["hourly"])
    df_h["date"] = pd.to_datetime(df_h["time"]).dt.normalize()

    _hourly_agg = {"relative_humidity_2m": ("rh_mean", "mean")}
    if "soil_moisture_0_to_7cm" in hourly_vars:
        _hourly_agg["soil_moisture_0_to_7cm"] = ("sm_0_7", "mean")
    if "wind_speed_10m" in hourly_vars:
        _hourly_agg["wind_speed_10m"] = ("ws_10m", "mean")

    daily_h = df_h.groupby("date").agg(
        **{alias: pd.NamedAgg(column=col, aggfunc=fn)
           for col, (alias, fn) in _hourly_agg.items()
           if col in df_h.columns}
    ).reset_index()

    df = df.merge(daily_h, on="date", how="left")

    # ── Columnas temporales ───────────────────────────────────────────
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["doy"]   = df["date"].dt.dayofyear

    # ── THI — índice calor-humedad (umbral laboral ≈ 41) ─────────────
    e = df["rh_mean"] / 100 * 6.105 * np.exp(17.27 * df["tavg"] / (237.7 + df["tavg"]))
    df["thi"] = df["tavg"] + 0.33 * e - 4.0

    # ── Proxy suelo saturado (solo si se descargó sm_0_7) ───────────
    if "sm_0_7" in df.columns:
        df["soil_sat"] = (df["sm_0_7"].fillna(0) > 0.38).astype(float)
    else:
        df["soil_sat"] = 0.0

    return df


def monthly_climatology(df: pd.DataFrame) -> pd.DataFrame:
    """
    Climatología mensual promediada sobre todos los años disponibles.
    Retorna DataFrame indexado por mes (1-12).
    """
    n_years = df["year"].nunique()
    monthly = (
        df.groupby("month")
        .agg(
            tmax_mean=("tmax",    "mean"),
            tmin_mean=("tmin",    "mean"),
            tavg_mean=("tavg",    "mean"),
            pr_total =("pr",      "sum"),
            pr_days  =("pr",      lambda x: (x > 1).sum()),
            rh_mean  =("rh_mean", "mean"),
            gw_mean  =("gw_10m",  "mean"),
        )
        .reset_index()
    )
    monthly["pr_mean"]  = monthly["pr_total"] / n_years
    monthly["pr_days"]  = monthly["pr_days"]  / n_years
    return monthly
