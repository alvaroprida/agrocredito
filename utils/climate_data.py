"""
utils/climate_data.py

Descarga datos climáticos históricos diarios desde Open-Meteo Historical API
(ERA5 reanalysis). Gratuita, sin API key, cobertura global, desde 1940.
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
    "relative_humidity_2m_mean",
    "wind_gusts_10m_max",
    "wind_speed_10m_mean",
    "shortwave_radiation_sum",
    "soil_moisture_0_to_7cm",
]

_RENAME = {
    "temperature_2m_max":        "tmax",
    "temperature_2m_min":        "tmin",
    "temperature_2m_mean":       "tavg",
    "precipitation_sum":         "pr",
    "relative_humidity_2m_mean": "rh_mean",
    "wind_gusts_10m_max":        "gw_10m",
    "wind_speed_10m_mean":       "ws_10m",
    "shortwave_radiation_sum":   "ssrd",    # MJ/m²/day
    "soil_moisture_0_to_7cm":    "sm_0_7",  # m³/m³
}


def get_historical_climate(lat: float, lon: float, n_years: int = 10) -> pd.DataFrame:
    """
    Descarga n_years de datos climáticos diarios para (lat, lon).

    Retorna DataFrame con columnas:
        date, tmax, tmin, tavg, pr, rh_mean, gw_10m, ws_10m, ssrd, sm_0_7
        year, month, doy, thi, soil_sat
    """
    end   = date.today() - timedelta(days=6)   # Open-Meteo tiene ~5 días de lag
    start = date(end.year - n_years, end.month, end.day)

    resp = requests.get(OPEN_METEO_URL, params={
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "daily":      ",".join(_DAILY_VARS),
        "timezone":   "auto",
    }, timeout=60)
    resp.raise_for_status()

    df = pd.DataFrame(resp.json()["daily"])
    df.rename(columns={"time": "date"}, inplace=True)
    df.rename(columns=_RENAME, inplace=True)
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["doy"]   = df["date"].dt.dayofyear

    # THI — índice calor-humedad (riesgo laboral, umbral 41)
    e = df["rh_mean"] / 100 * 6.105 * np.exp(17.27 * df["tavg"] / (237.7 + df["tavg"]))
    df["thi"] = df["tavg"] + 0.33 * e - 4.0

    # Proxy de suelo saturado: sm_0_7 > 0.38 m³/m³
    df["soil_sat"] = (df["sm_0_7"].fillna(0) > 0.38).astype(float)

    return df


def monthly_climatology(df: pd.DataFrame) -> pd.DataFrame:
    """
    Climatología mensual promediada sobre todos los años disponibles.
    Retorna DataFrame indexado por mes (1-12) con:
        tmax_mean, tmin_mean, tavg_mean,
        pr_mean (mm/mes), pr_days (días con lluvia > 1 mm),
        rh_mean, gw_mean
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
    monthly["pr_mean"] = monthly["pr_total"] / n_years
    return monthly
