# Deploy en Streamlit Cloud — Paso a paso

## Requisitos previos
- Cuenta en [GitHub](https://github.com) (gratis)
- Cuenta en [Streamlit Cloud](https://share.streamlit.io) (gratis, login con GitHub)

---

## Paso 1 · Estructura mínima del repositorio

```
agrocredito/
├── app.py                 ← el front
├── requirements.txt       ← dependencias
├── utils/                 ← módulos .py (pueden estar vacíos por ahora)
│   └── __init__.py
├── .streamlit/
│   └── secrets.toml       ← API keys (NO subir a GitHub — ver paso 3)
└── README.md
```

## Paso 2 · Subir a GitHub

```bash
git init
git add app.py requirements.txt utils/ README.md
git commit -m "MVP AgroCredito v0.1"
git remote add origin https://github.com/TU_USUARIO/agrocredito.git
git push -u origin main
```

> ⚠️ Nunca hagas `git add .streamlit/secrets.toml` — contiene tus API keys.

---

## Paso 3 · Secrets (API keys) en Streamlit Cloud

En Streamlit Cloud, ve a tu app → **Settings → Secrets** y pega:

```toml
EOSDA_API_KEY = "tu_clave_aqui"
POSTGIS_URL   = "postgresql://user:pass@host:5432/db"
```

En tu código Python los lees así:
```python
import streamlit as st
api_key = st.secrets["EOSDA_API_KEY"]
```

---

## Paso 4 · Deploy

1. Ve a [share.streamlit.io](https://share.streamlit.io)
2. **New app** → selecciona tu repo `agrocredito`
3. Branch: `main` · Main file: `app.py`
4. Clic en **Deploy** → en ~2 minutos tienes tu URL pública

**URL resultante:** `https://agrocredito-XXXXX.streamlit.app`

---

## Paso 5 · Actualizar la app

Cada `git push` a `main` actualiza la app automáticamente. Cero pasos extra.

```bash
# Flujo de trabajo habitual
git add app.py utils/indicators.py
git commit -m "Agrego indicador NDWI"
git push
# → La app se actualiza sola en ~30 segundos
```

---

## Notas para el MVP

| Qué funciona ahora | Qué se conecta después |
|---|---|
| Casos de estudio café + plátano | Coordenadas reales → EOSDA API |
| Visualizaciones completas | PostGIS local → PostGIS cloud (Supabase) |
| Mapa interactivo | Polígono real del predio desde catastro |
| Matriz de vulnerabilidad | Indicadores adicionales del backtesting |
| Forecast simulado | API meteorológica en tiempo real |
