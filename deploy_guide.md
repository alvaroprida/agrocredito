# Guía de Despliegue · AgroCredito

**Objetivo:** desplegar la aplicación en tu propia infraestructura (tus cuentas de
GitHub, Google Earth Engine, Supabase y Streamlit Community Cloud), partiendo del
archivo `agrocredito.zip` que te ha entregado el equipo.

**Tiempo estimado:** 45–90 min · **Coste:** 0 € (todos los servicios tienen plan gratuito).

---

## 📋 RESUMEN — todos los pasos de un vistazo

| # | Paso | Dónde | Resultado |
|---|------|-------|-----------|
| 0 | Crear las 4 cuentas gratuitas | GitHub · Google Cloud · Supabase · Streamlit | Acceso a los servicios |
| 1 | Descomprimir `agrocredito.zip` | Tu ordenador | Carpeta con el código |
| 2 | Subir el código a un repositorio propio | GitHub | Repo `agrocredito` |
| 3 | Configurar Google Earth Engine | Google Cloud | JSON de cuenta de servicio |
| 4 | Configurar la base de datos (opcional) | Supabase | Cadena `DATABASE_URL` |
| 5 | Desplegar la app | Streamlit Community Cloud | URL pública de la app |
| 6 | Añadir los *Secrets* | Streamlit Cloud → Settings | Credenciales cargadas |
| 7 | Verificar | Navegador | App funcionando |

> **Prueba rápida (sin base de datos):** puedes desplegar y probar la app en
> **modo demo** (2 predios de ejemplo) saltando el paso 4. Solo necesitas Earth
> Engine (paso 3). Para usar predios reales, completa el paso 4.

### Credenciales que necesitarás (se cargan en el paso 6)
- `DATABASE_URL` → cadena de conexión de Supabase *(solo si usas base de datos real)*
- `[gee] project` + `[gee] service_account_json` → de Google Earth Engine *(obligatorio)*

---

# 🔧 DETALLE PASO A PASO

## Paso 0 · Crear las cuentas (gratuitas)

Crea (si no las tienes) una cuenta en cada servicio, idealmente con el mismo correo:

1. **GitHub** → https://github.com/signup
2. **Google Cloud** (incluye Earth Engine) → https://console.cloud.google.com
   - Regístrate además en Earth Engine: https://code.earthengine.google.com
     (acepta los términos; elige el tipo de uso que corresponda).
3. **Supabase** → https://supabase.com  *(opcional, solo para predios reales)*
4. **Streamlit Community Cloud** → https://share.streamlit.io
   (inicia sesión **con tu cuenta de GitHub**).

---

## Paso 1 · Descomprimir el archivo

1. Descomprime `agrocredito.zip`. Obtendrás una carpeta con `app.py`,
   `requirements.txt`, `utils/`, `datos/`, etc.
2. No necesitas instalar nada en tu ordenador: el despliegue es en la nube.

---

## Paso 2 · Subir el código a GitHub

1. En GitHub, pulsa **New repository**:
   - Nombre: `agrocredito` · Visibilidad: **Private** (recomendado).
   - **No** marques «Add a README» (ya viene en el zip).
2. Sube los archivos. La forma más sencilla (sin terminal):
   - En el repo vacío → enlace **«uploading an existing file»** → arrastra
     **todo el contenido** de la carpeta descomprimida → **Commit changes**.
   - *(Alternativa con terminal, si usas git:)*
     ```bash
     cd carpeta_descomprimida
     git init && git add . && git commit -m "AgroCredito"
     git branch -M main
     git remote add origin https://github.com/TU_USUARIO/agrocredito.git
     git push -u origin main
     ```
3. ⚠️ **Nunca subas** `.streamlit/secrets.toml` (el `.gitignore` incluido ya lo
   evita). Las credenciales se cargan solo en Streamlit (paso 6).

---

## Paso 3 · Configurar Google Earth Engine  (obligatorio)

Sirve para descargar el NDVI de Sentinel-2. Necesitas un **proyecto de Google
Cloud** con la Earth Engine API y una **cuenta de servicio**.

1. En **Google Cloud Console** → crea un proyecto (anota su **ID**, p. ej.
   `agrocredito-cliente`).
2. **APIs y servicios** → habilita **«Google Earth Engine API»**.
3. **IAM y administración → Cuentas de servicio → Crear cuenta de servicio**:
   - Nombre: `earth-engine` · Rol: *Viewer* (o *Earth Engine Resource Viewer*).
4. En la cuenta creada → pestaña **Claves → Añadir clave → Crear clave → JSON**.
   Se descargará un archivo `.json`: **guárdalo, lo usarás en el paso 6**.
5. Registra/autoriza la cuenta de servicio en Earth Engine (una sola vez): ver
   https://developers.google.com/earth-engine/guides/service_account

> Guarda: el **ID del proyecto** y el **contenido del `.json`**.

---

## Paso 4 · Configurar la base de datos Supabase  (opcional)

Necesario solo para consultar **predios reales** del catastro. Si lo saltas, la
app funciona en **modo demo** (ver más abajo).

1. En **Supabase** → **New project** (elige región y contraseña de BD; anótala).
2. **Database → Extensions** → habilita **`postgis`**.
3. Carga la base de datos espacial que te ha entregado el equipo (predios y
   capas de referencia). El procedimiento completo de **volcado y carga a tu
   Supabase de AGRAPP** está en el **Anexo A** (al final de esta guía).
4. Copia la **cadena de conexión**: **Project Settings → Database →
   Connection string → URI**. Tendrá la forma
   `postgresql://postgres:TU_PASSWORD@db.TU_PROYECTO.supabase.co:5432/postgres`
   → es tu **`DATABASE_URL`** (paso 6).

---

## Paso 5 · Desplegar en Streamlit Community Cloud

1. Entra en https://share.streamlit.io (con tu cuenta de GitHub).
2. **Create app → Deploy a public app from GitHub**.
3. Configura:
   - **Repository:** `TU_USUARIO/agrocredito`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Pulsa **Deploy**. La primera construcción instala las dependencias de
   `requirements.txt` (unos minutos). Puede dar error de credenciales hasta
   completar el paso 6.

---

## Paso 6 · Cargar los *Secrets* (credenciales)

1. En la app desplegada → menú **⋮ → Settings → Secrets**.
2. Pega lo siguiente, **rellenando con tus datos** (plantilla:
   `.streamlit/secrets.toml.example`):

   ```toml
   # Solo si usas base de datos real (paso 4):
   DATABASE_URL = "postgresql://postgres:TU_PASSWORD@db.TU_PROYECTO.supabase.co:5432/postgres"

   [gee]
   project = "tu-proyecto-gcp"
   service_account_json = """
   { ...pega aquí el CONTENIDO COMPLETO del .json de la cuenta de servicio... }
   """
   ```
3. **Save**. La app se reinicia con las credenciales cargadas.

---

## Paso 7 · Verificar

1. Abre la URL pública de tu app.
2. En **🏠 Inicio**: define un predio (por punto, dibujo o GeoJSON) y pulsa
   **Analizar predio**.
3. En **✅ Validación Pre-Crédito**: deberían calcularse terreno, NDVI y
   actividad productiva (la primera vez tarda ~1–2 min por las consultas a GEE).
4. En **📡 Monitoreo & Forecast**: descarga el template, súbelo y pulsa
   **Calcular indicadores**.

✅ Si ves resultados y mapas, el despliegue está completo.

---

## 🧪 Modo demo (sin base de datos)

Para probar la app sin Supabase:
1. Abre `utils/postgis_client.py` y cambia:
   `USE_REAL_DB = True`  →  `USE_REAL_DB = False`
2. Sube el cambio a GitHub. La app usará 2 predios de ejemplo (Salento y Turbo)
   y no necesitarás `DATABASE_URL`; solo Earth Engine (paso 3).

---

## ❓ Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---------|----------------|----------|
| «No se encontraron credenciales GEE» | Falta o mal formato del `[gee]` en Secrets | Revisa el paso 6; el JSON completo entre `"""` |
| Error al consultar el predio | `DATABASE_URL` ausente o BD sin datos | Completa el paso 4, o usa modo demo |
| La app no arranca tras *Deploy* | Falta una dependencia | Revisa los *logs* en «Manage app» |
| NDVI vacío / sin escenas | Nubosidad alta o zona sin cobertura | Prueba otras coordenadas o amplía el periodo |

---

## 🔒 Seguridad

- Las credenciales **solo** viven en los *Secrets* de Streamlit, **nunca** en el
  código ni en GitHub (el `.gitignore` protege `secrets.toml`).
- Mantén el repositorio en **privado**.
- Si una credencial se expone, revócala y genera una nueva (Google Cloud /
  Supabase) y actualiza los *Secrets*.

---

## Anexo A · Volcado y carga de la base de datos PostGIS (a Supabase AGRAPP)

La aplicación consulta **6 tablas espaciales** en PostGIS:

| Tabla | Contenido |
|-------|-----------|
| `predios` | Polígonos catastrales (geometría `wkb_geometry`) |
| `construcciones_mvp` | Construcciones dentro de los predios |
| `frontera_mvp` | Frontera agrícola nacional |
| `aptitud_cafe_mvp` | Zonificación de aptitud para café |
| `aptitud_platano_mvp` | Zonificación de aptitud para plátano |
| `ufh_mvp` | Unidades de valor potencial del suelo (UFH) |

Estos son los pasos para **trasladar toda la base de datos** desde el origen que
te entrega el equipo hacia **tu cuenta Supabase de AGRAPP**.

### A.1 · Requisitos
- Herramientas cliente de PostgreSQL (`pg_dump`, `psql`, `pg_restore`).
  - **macOS:** `brew install libpq` y añade `libpq` al `PATH`.
  - **Windows/Linux:** instala «PostgreSQL client tools».
- La **cadena de conexión de ORIGEN** (te la entrega el equipo) y la de
  **DESTINO** (tu Supabase de AGRAPP, del Paso 4.4).

### A.2 · Volcar la base de datos de ORIGEN
Vuelca todo el esquema `public` (excluyendo la tabla de sistema de PostGIS):

```bash
pg_dump "postgresql://USUARIO:PASSWORD@HOST_ORIGEN:5432/postgres" \
  --no-owner --no-privileges \
  --schema=public \
  --exclude-table=public.spatial_ref_sys \
  -Fc -f agrapp_postgis.dump
```

Se genera el archivo **`agrapp_postgis.dump`** (contiene esquema + datos de las
6 tablas y cualquier otra del esquema `public`).

### A.3 · Preparar el DESTINO (Supabase AGRAPP)
1. Crea el proyecto Supabase (Paso 4.1) y **habilita la extensión `postgis`**
   (Paso 4.2). Esto debe hacerse **antes** de restaurar.

### A.4 · Restaurar en el DESTINO
```bash
pg_restore --no-owner --no-privileges \
  -d "postgresql://postgres:TU_PASSWORD@db.TU_PROYECTO.supabase.co:5432/postgres" \
  agrapp_postgis.dump
```
Si aparecen avisos sobre `postgis` o `spatial_ref_sys` (ya existen porque
habilitaste la extensión), son **normales y se pueden ignorar**.

### A.5 · Verificar la carga
En Supabase → **SQL Editor**, ejecuta:
```sql
select table_name from information_schema.tables
  where table_schema = 'public' order by 1;
select count(*) from predios;
select postgis_full_version();
```
Debes ver las 6 tablas listadas y un recuento de filas en `predios`.

> **Alternativa (SQL plano):** si prefieres un `.sql` legible en lugar del
> formato binario, usa `-Fp -f agrapp_postgis.sql` en el volcado y restaura con
> `psql "URL_DESTINO" -f agrapp_postgis.sql`.

---

*Soporte: para el volcado de datos de predios o dudas de configuración, contacta
con el equipo que te entregó la aplicación.*
