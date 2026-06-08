"""
utils/monitoring_climate.py

Descarga y combina series climáticas para el módulo de monitoreo de portafolio:
  - ERA5 histórico (n_hist_years) via Open-Meteo Historical API → climatología YTD
  - ERA5 reciente + forecast 14 días via Open-Meteo Forecast API → indicadores actuales

La serie combinada cubre desde Jan 1 del año en curso hasta today+14,
sin saltos, usando past_days para cubrir el rezago ERA5 de 6 días.
"""

import numpy as np
import pandas as pd
import requests
from datetime import date, timedelta

from utils.climate_data import get_historical_climate

FORECAST_URL  = "https://api.open-meteo.com/v1/forecast"
ERA5_LAG_DAYS = 6   # días de rezago publicación ERA5

_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "wind_gusts_10m_max",
    "shortwave_radiation_sum",
]
_RENAME = {
    "temperature_2m_max":      "tmax",
    "temperature_2m_min":      "tmin",
    "temperature_2m_mean":     "tavg",
    "precipitation_sum":       "pr",
    "wind_gusts_10m_max":      "gw_10m",
    "shortwave_radiation_sum": "ssrd",
}


def _download_forecast(
    lat: float,
    lon: float,
    past_days: int = 30,
    forecast_days: int = 14,
) -> pd.DataFrame:
    """Descarga serie reciente + forecast desde Open-Meteo Forecast API."""
    import time
    _params = {
        "latitude":      lat,
        "longitude":     lon,
        "daily":         ",".join(_DAILY_VARS),
        "hourly":        "relative_humidity_2m",
        "past_days":     past_days,
        "forecast_days": forecast_days,
        "timezone":      "auto",
    }
    for _attempt in range(6):
        resp = requests.get(FORECAST_URL, params=_params, timeout=30)
        if resp.status_code != 429:
            resp.raise_for_status()
            break
        _wait = int(resp.headers.get("Retry-After", 2 ** (_attempt + 1)))
        time.sleep(min(_wait, 60))
    else:
        resp.raise_for_status()
    raw = resp.json()

    df = pd.DataFrame(raw["daily"])
    df.rename(columns={"time": "date"}, inplace=True)
    df.rename(columns=_RENAME, inplace=True)
    df["date"] = pd.to_datetime(df["date"])

    df_h = pd.DataFrame(raw["hourly"])
    df_h["date"] = pd.to_datetime(df_h["time"]).dt.normalize()
    rh = df_h.groupby("date")["relative_humidity_2m"].mean().reset_index()
    rh.columns = ["date", "rh_mean"]
    df = df.merge(rh, on="date", how="left")

    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["doy"]   = df["date"].dt.dayofyear
    return df


def _ytd_climatology(hist_df: pd.DataFrame) -> dict:
    """
    Para cada día del año (doy), calcula la precipitación acumulada promedio
    desde el 1 de enero hasta ese día, sobre todos los años históricos completos.
    Retorna {doy: mm_acumulados_promedio}.
    """
    ytd_by_year: dict = {}
    for yr, grp in hist_df.groupby("year"):
        if grp[grp["month"] == 1].empty:
            continue
        full = grp.sort_values("date").copy()
        full["ytd_pr"] = full["pr"].fillna(0).cumsum()
        ytd_by_year[yr] = full.set_index("doy")["ytd_pr"].to_dict()

    clim: dict = {}
    for doy in range(1, 367):
        vals = [ytd_by_year[yr][doy] for yr in ytd_by_year if doy in ytd_by_year[yr]]
        if vals:
            clim[doy] = float(np.mean(vals))
    return clim


def get_monitoring_series(lat: float, lon: float, n_hist_years: int = 5) -> dict:
    """
    Descarga y combina todas las series necesarias para el módulo de monitoreo.

    Retorna dict con:
        hist_df        : DataFrame ERA5 n_hist_years (para climatología YTD y mensual)
        combined_df    : ERA5 hasta era5_end + forecast hasta today+14 (serie continua)
        ytd_clim       : {doy → mm acumulados normales YTD}
        today          : date
        era5_end       : date (último día ERA5 disponible)
        forecast_start : date (primer día de pronóstico puro)
    """
    today    = date.today()
    era5_end = today - timedelta(days=ERA5_LAG_DAYS)

    # ERA5 histórico completo: climatología YTD y mensual
    hist_df  = get_historical_climate(lat, lon, n_years=n_hist_years)

    # Serie reciente (past_days cubre brecha ERA5) + 14 días de pronóstico
    fcast_df = _download_forecast(lat, lon, past_days=30, forecast_days=14)

    # Combinar: ERA5 hasta era5_end, luego forecast para fechas posteriores
    hist_tail   = hist_df[hist_df["date"].dt.date <= era5_end].copy()
    fcast_extra = fcast_df[fcast_df["date"].dt.date > era5_end].copy()
    combined_df = (
        pd.concat([hist_tail, fcast_extra], ignore_index=True)
        .sort_values("date")
        .reset_index(drop=True)
    )

    return {
        "hist_df":        hist_df,
        "combined_df":    combined_df,
        "ytd_clim":       _ytd_climatology(hist_df),
        "today":          today,
        "era5_end":       era5_end,
        "forecast_start": era5_end + timedelta(days=1),
    }
