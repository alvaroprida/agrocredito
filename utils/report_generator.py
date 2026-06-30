"""
utils/report_generator.py
Reporte ex-ante PDF ejecutivo para evaluación de crédito agropecuario.

Estructura:
  Página 1 (resumen ejecutivo):
    1. Ficha del predio
    2. Score final consolidado + resolución
    3. Resumen de Validación Pre-Crédito (Existencia, A1, A2, B1, B2, B3, C, D)
    4. Documentación adicional requerida
    5. Aprobación y firmas
  Página 2+ (análisis detallado de los indicadores):
    6. A · Validación geométrica (frontera + desglose de áreas)
    7. B · Continuidad productiva (aptitud + actividad NDVI + altitud B3)
    8. C · Infraestructura
    9. D · Riesgo agroclimático + serie climática
   10. Nota legal
"""

from __future__ import annotations
from io import BytesIO
from datetime import date
from typing import Literal


# ── Paleta ───────────────────────────────────────────────────────────────────

class C:
    DARK     = "#1e293b"
    MID      = "#334155"
    LIGHT    = "#f8fafc"
    BORDER   = "#e2e8f0"
    GREEN    = "#059669"
    GREEN_BG = "#d1fae5"
    AMBER    = "#d97706"
    AMBER_BG = "#fef3c7"
    RED      = "#dc2626"
    RED_BG   = "#fee2e2"
    GREY_BG  = "#f1f5f9"
    WHITE    = "#ffffff"
    SUBTEXT  = "#64748b"

def _hex(h: str):
    from reportlab.lib import colors
    return colors.HexColor(h)

_SEM_BG  = {"verde": C.GREEN_BG, "naranja": C.AMBER_BG, "amarillo": C.AMBER_BG,
             "rojo":  C.RED_BG,   "gris":    C.GREY_BG}
_SEM_FG  = {"verde": C.GREEN,    "naranja": C.AMBER,    "amarillo": C.AMBER,
             "rojo":  C.RED,      "gris":    C.SUBTEXT}
_SEM_EMO = {"verde": "🟢", "naranja": "🟡", "amarillo": "🟡", "rojo": "🔴", "gris": "⚪"}
_SEM_ACT = {
    "verde":    "Sin restricción.",
    "naranja":  "Doc. adicional recomendada.",
    "amarillo": "Doc. adicional recomendada.",
    "rojo":     "Inspección presencial requerida.",
    "gris":     "No calculado.",
}
_DOC_REQ = {
    "A1_naranja": "Solicitar plan de manejo ambiental o certificación étnico-cultural según condición de frontera.",
    "A1_rojo":    "Predio parcialmente fuera de Frontera Agrícola — no procede sin autorización ambiental expresa.",
    "A2_amarillo":"Revisar estructura de costos del proyecto; área efectiva puede limitar volumen de producción.",
    "A2_rojo":    "Viabilidad productiva comprometida — solicitar plan de uso alternativo del suelo.",
    "B1_amarillo":"Solicitar plan de manejo agrícola adaptado al nivel de aptitud del suelo.",
    "B1_rojo":    "Aptitud baja o nula para el cultivo declarado — evaluar reconversión o cambio de cultivo.",
    "B2_amarillo":"Solicitar documentación de soporte de producción: facturas, registros ICA, certificados de cosecha.",
    "B2_rojo":    "Actividad productiva no confirmada por imagen satelital — inspección técnica presencial obligatoria.",
    "C_naranja":  "Verificar costos de transporte e impacto en rentabilidad del proyecto.",
    "C_rojo":     "Riesgo de inaccesibilidad — verificar condición de acceso en temporada de lluvias.",
    "D_medio":    "Incluir cláusula de seguimiento trimestral en contrato de crédito.",
    "D_alto":     "Seguro agrícola obligatorio como condición de desembolso.",
    "D_extremo":  "Evaluar viabilidad técnica y financiera del proyecto ante nivel de riesgo extremo.",
}

# Resolución asociada al score final consolidado (1 mejor · 4 peor)
_RESOLUCION = {
    1: ("Apto sin restricciones relevantes",
        "Se recomienda proceder con la evaluación crediticia ordinaria."),
    2: ("Apto con validaciones adicionales",
        "Procede sujeto a la documentación adicional indicada más abajo."),
    3: ("Requiere revisión manual",
        "Riesgo elevado: se recomienda verificación técnica presencial antes de aprobar."),
    4: ("No recomendable bajo criterios actuales",
        "No procede sin mitigación previa de los factores críticos identificados."),
}

MESES_ES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

_CLIMA_FIG_W, _CLIMA_FIG_H = 7.4, 2.4   # pulgadas (relación de aspecto del gráfico)


def _clima_chart_png(meses, precip, tmax, tmin):
    """Genera el gráfico de serie climática mensual (precip. en barras +
    temperatura máx/mín en líneas) como PNG en memoria, igual que en D1."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n  = len(meses)
    pr = [float(precip[i]) if i < len(precip) else 0.0 for i in range(n)]
    tx = [float(tmax[i])   if i < len(tmax)   else None for i in range(n)]
    tn = [float(tmin[i])   if i < len(tmin)   else None for i in range(n)]
    x  = list(range(n))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(_CLIMA_FIG_W, _CLIMA_FIG_H), dpi=150)

    ax1.bar(x, pr, color="#3b82f6", width=0.7)
    ax1.set_title("Precipitación media mensual (mm)", fontsize=9)
    ax1.set_xticks(x); ax1.set_xticklabels(meses, fontsize=7)
    ax1.tick_params(axis="y", labelsize=7)
    ax1.grid(axis="y", alpha=0.3)

    ax2.plot(x, tx, color="#ef4444", marker="o", ms=2.5, lw=1.5, label="T máx")
    ax2.plot(x, tn, color="#3b82f6", marker="o", ms=2.5, lw=1.5, label="T mín")
    ax2.fill_between(x, tn, tx, color="#3b82f6", alpha=0.08)
    ax2.set_title("Temperatura mensual (°C)", fontsize=9)
    ax2.set_xticks(x); ax2.set_xticklabels(meses, fontsize=7)
    ax2.tick_params(axis="y", labelsize=7)
    ax2.legend(fontsize=7, loc="best")
    ax2.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════════════════
#  PDF ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _build_pdf(
    datos:    dict,
    predio:   dict | None,
    analisis: dict | None = None,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether, PageBreak, Image as RLImage,
    )

    an = analisis or {}
    W, H = A4
    buf  = BytesIO()

    # ── Unique-name style factory (fixes ReportLab global registry crash) ─────
    _n = [0]
    def _sty(base="s", **kw):
        _n[0] += 1
        dflt = dict(fontName="Helvetica", fontSize=9,
                    textColor=_hex(C.DARK), leading=13)
        dflt.update(kw)
        return ParagraphStyle(f"_r{_n[0]}_{base}", **dflt)

    # Pre-build style set
    S = {
        "title":   _sty("title",   fontName="Helvetica-Bold", fontSize=18,
                         textColor=_hex(C.DARK), spaceAfter=2),
        "sub":     _sty("sub",     fontSize=9, textColor=_hex(C.SUBTEXT)),
        "h2":      _sty("h2",      fontName="Helvetica-Bold", fontSize=11,
                         textColor=_hex(C.DARK), spaceBefore=5, spaceAfter=3),
        "h3":      _sty("h3",      fontName="Helvetica-Bold", fontSize=9.5,
                         textColor=_hex(C.MID), spaceBefore=5, spaceAfter=2),
        "body":    _sty("body"),
        "small":   _sty("small",   fontSize=7.5, textColor=_hex(C.SUBTEXT),
                         fontName="Helvetica-Oblique"),
        "th":      _sty("th",      fontName="Helvetica-Bold", fontSize=8,
                         textColor=_hex(C.WHITE), alignment=TA_CENTER),
        "td":      _sty("td",      fontSize=8, leading=11),
        "td_c":    _sty("td_c",    fontSize=8, leading=11, alignment=TA_CENTER),
        "td_sm":   _sty("td_sm",   fontSize=7.5, leading=10,
                         textColor=_hex(C.SUBTEXT), fontName="Helvetica-Oblique"),
        "kpival":  _sty("kpival",  fontName="Helvetica-Bold", fontSize=12,
                         alignment=TA_CENTER),
        "kpilbl":  _sty("kpilbl",  fontSize=7.5, alignment=TA_CENTER,
                         textColor=_hex(C.SUBTEXT)),
        "semtx":   _sty("semtx",   fontName="Helvetica-Bold", fontSize=9,
                         alignment=TA_CENTER),
    }

    def P(txt, st="body"): return Paragraph(str(txt), S[st])
    def SP(h=0.25):        return Spacer(1, h * cm)
    def HR():
        return HRFlowable(width="100%", thickness=0.5,
                          color=_hex(C.BORDER), spaceAfter=3, spaceBefore=3)

    # ── Canvas: header + footer ───────────────────────────────────────────────
    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(_hex(C.DARK))
        canvas.rect(0, H - 1.1*cm, W, 1.1*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE))
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(1.8*cm, H - 0.75*cm,
                          "AgroCredito · Reporte de Evaluación Ex-Ante")
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(W - 1.8*cm, H - 0.75*cm,
                               date.today().strftime("%d/%m/%Y"))
        canvas.setFillColor(_hex(C.MID))
        canvas.rect(0, 0, W, 0.8*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(1.8*cm, 0.28*cm,
                          "Confidencial · Uso interno · Generado automáticamente")
        canvas.drawRightString(W - 1.8*cm, 0.28*cm, f"Pág. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.8*cm,  bottomMargin=1.5*cm,
        onFirstPage=_on_page, onLaterPages=_on_page,
    )

    # ── Table helpers ─────────────────────────────────────────────────────────
    TW = W - 3.6*cm  # usable width

    def _tbl(data, cw, header_row=True, row_bgs=None, extra_styles=None):
        t = Table(data, colWidths=cw, repeatRows=1 if header_row else 0)
        base = [
            ("TOPPADDING",    (0,0), (-1,-1), 3.5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3.5),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("RIGHTPADDING",  (0,0), (-1,-1), 6),
            ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]
        if header_row:
            base += [("BACKGROUND", (0,0), (-1,0), _hex(C.MID))]
        if row_bgs:
            base += [("ROWBACKGROUNDS", (0, 1 if header_row else 0),
                      (-1,-1), [_hex(b) for b in row_bgs])]
        if extra_styles:
            base += extra_styles
        t.setStyle(TableStyle(base))
        return t

    # ══════════════════════════════════════════════════════════════════════════
    #  COLLECT DATA
    # ══════════════════════════════════════════════════════════════════════════

    # Predio metadata
    cod       = predio.get("codigo","—")       if predio else "—"
    dep       = predio.get("departamento","—") if predio else "—"
    mun       = ((predio.get("municipio") if predio else None)
                 or an.get("municipio") or datos.get("municipio","—"))
    area_tot  = float((predio.get("area_ha") if predio else None) or datos.get("area_total_ha", 0) or 0)
    cultivo   = an.get("cultivo") or datos.get("cultivo","—")
    c_lat     = an.get("c_lat",  datos.get("lat",  0.0))
    c_lon     = an.get("c_lon",  datos.get("lon",  0.0))

    # Existencia del predio
    exist_nivel = an.get("existencia_nivel", "verde" if predio else "rojo")
    exist_texto = an.get("existencia_texto")

    # A1 · Frontera agrícola
    a1_nivel   = an.get("a1_nivel", "gris")
    gdf_front  = an.get("gdf_frontera")

    # A2 areas — a2_computed=False si el usuario no lanzó el cálculo en la app
    _area_ef_raw = an.get("area_ef", datos.get("area_efectiva_ha"))
    a2_computed  = _area_ef_raw is not None
    area_ef    = float(_area_ef_raw or 0)
    pct_ef     = float(an.get("pct_ef") if an.get("pct_ef") is not None
                       else round(area_ef/max(area_tot,1)*100, 1))
    area_no_cult = float(an.get("area_no_cultivable",
                                max(area_tot - area_ef, 0.0)) or 0)
    a2_nivel   = ("verde" if pct_ef >= 70 else "amarillo" if pct_ef >= 40 else "rojo") \
                 if a2_computed else "gris"

    # B1 aptitud
    apt_res    = an.get("apt_result")
    apt_cat    = (apt_res.get("category") if apt_res and not apt_res.get("error") else None)
    apt_score  = (apt_res.get("score")    if apt_res and not apt_res.get("error") else None)
    b1_nivel   = ("verde" if apt_cat == "Alta" else "amarillo" if apt_cat == "Media"
                  else "rojo" if apt_cat in ("Baja","No apta") else "gris")

    # B2 actividad
    b2         = an.get("b2_result")
    b2_nivel   = (b2["semaforo"] if b2 else "gris")
    b2_pct     = (b2["pct_active"]     if b2 else None)
    b2_peak    = (b2["years_with_peak"] if b2 else None)
    b2_nyears  = (b2["n_years"]         if b2 else None)
    b2_thr_s   = (b2["scene_threshold"] if b2 else None)
    b2_thr_p   = (b2["peak_threshold"]  if b2 else None)

    # B3 altitud
    b3_nivel    = an.get("b3_nivel", "gris")
    b3_elev     = an.get("b3_elev")
    b3_alt_min  = an.get("b3_alt_min")
    b3_alt_max  = an.get("b3_alt_max")
    b3_res      = (f"{b3_elev:.0f} m (rango {b3_alt_min}–{b3_alt_max} m)"
                   if b3_elev is not None else "—")

    # C infraestructura
    c_nivel    = an.get("infra_nivel", "gris")
    infra_cu   = an.get("infra_centro")
    infra_via  = an.get("infra_via")

    # D riesgo
    df_risk    = an.get("df_risk")
    d_score    = an.get("risk_score")
    d_label    = an.get("risk_label", "—")
    d_nivel    = "gris"
    if d_score is not None:
        d_nivel = ("verde"   if d_score < 0.25 else
                   "amarillo" if d_score < 0.50 else
                   "rojo"    if d_score < 0.75 else "rojo")

    # Score final consolidado
    score_final    = an.get("score_final")
    decision_final = an.get("decision_final", "—")
    obs_unidad     = an.get("obs_unidad", "—")

    # Peor semáforo (solo para el color de la tabla de documentación)
    _rank = {"verde":0,"amarillo":1,"naranja":1,"rojo":2,"gris":-1}
    _all_niveles = [a1_nivel, a2_nivel, b1_nivel, b2_nivel, c_nivel, d_nivel]
    _valid = [n for n in _all_niveles if n != "gris"]
    _worst = max(_valid, key=lambda x: _rank.get(x, 0)) if _valid else "gris"

    # ══════════════════════════════════════════════════════════════════════════
    #  STORY
    # ══════════════════════════════════════════════════════════════════════════
    story = []

    # ── PORTADA / FICHA ───────────────────────────────────────────────────────
    story += [
        SP(0.25),
        P("Reporte de Evaluación Ex-Ante", "title"),
        SP(0.45),
        P("Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia", "sub"),
        SP(0.22), HR(), SP(0.1),
    ]

    ficha = _tbl(
        [
            [P("<b>Cultivo</b>","td"),      P(cultivo.capitalize(),"td"),
             P("<b>Municipio</b>","td"),    P(mun,"td")],
            [P("<b>Departamento</b>","td"), P(dep,"td"),
             P("<b>Código catastral</b>","td"), P(cod,"td")],
            [P("<b>Área catastral</b>","td"), P(f"{area_tot:.2f} ha","td"),
             P("<b>Fecha de análisis</b>","td"), P(date.today().strftime("%d/%m/%Y"),"td")],
            [P("<b>Coordenadas</b>","td"),  P(f"Lat {c_lat:.5f} · Lon {c_lon:.5f}","td"),
             P("<b>Analista</b>","td"),     P("___________________________","td")],
        ],
        cw=[TW*0.18, TW*0.32, TW*0.20, TW*0.30],
        header_row=False,
        extra_styles=[
            ("BACKGROUND", (0,0), (0,-1), _hex(C.LIGHT)),
            ("BACKGROUND", (2,0), (2,-1), _hex(C.LIGHT)),
            ("FONTNAME",   (0,0), (0,-1), "Helvetica-Bold"),
            ("FONTNAME",   (2,0), (2,-1), "Helvetica-Bold"),
        ],
    )
    story += [ficha, SP(0.22)]

    # ── SCORE FINAL CONSOLIDADO + RESOLUCIÓN ──────────────────────────────────
    if score_final is not None:
        _sf_colors = {
            1: (C.GREEN_BG, C.GREEN),
            2: (C.AMBER_BG, C.AMBER),
            3: (C.RED_BG,   C.RED),
            4: (C.RED_BG,   C.RED),
        }
        _sf_bg, _sf_fg = _sf_colors.get(score_final, (C.GREY_BG, C.SUBTEXT))
        _res_lbl, _res_desc = _RESOLUCION.get(score_final, (decision_final, ""))
        sf_lbl = _sty("sfl", fontName="Helvetica-Bold", fontSize=14, leading=17,
                      alignment=TA_CENTER, textColor=_hex(_sf_fg))
        sf_dsc = _sty("sfd", fontSize=8.5, leading=11, alignment=TA_CENTER,
                      textColor=_hex(_sf_fg))
        sf_box = Table(
            [[Paragraph(f"SCORE FINAL: {score_final} / 4 · {_res_lbl}", sf_lbl)],
             [Paragraph(_res_desc, sf_dsc)]],
            colWidths=[TW],
        )
        sf_box.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(_sf_bg)),
            ("TOPPADDING",    (0,0), (-1,0),  7),
            ("BOTTOMPADDING", (0,-1),(-1,-1), 7),
            ("TOPPADDING",    (0,1), (-1,-1), 1),
            ("BOTTOMPADDING", (0,0), (-1,0),  1),
            ("LEFTPADDING",   (0,0), (-1,-1), 14),
            ("RIGHTPADDING",  (0,0), (-1,-1), 14),
        ]))
        story += [sf_box, SP(0.12)]
    else:
        story += [P("Score final no disponible — ejecuta los indicadores en la app "
                    "para obtener la resolución consolidada.", "small"), SP(0.2)]

    # Obs. unidad productiva
    if obs_unidad and obs_unidad != "—":
        story.append(P(f"<b>Observación inicial asesor:</b> {obs_unidad}", "body"))
    story.append(SP(0.08))

    # ── RESUMEN DE VALIDACIÓN PRE-CRÉDITO ─────────────────────────────────────
    story.append(P("Resumen de Validación Pre-Crédito", "h2"))

    _front_res = ("Todo en Frontera Agrícola no condicionada"
                  if a1_nivel == "verde"
                  else "Condicionada / parcialmente fuera de frontera"
                  if a1_nivel in ("naranja","amarillo")
                  else "Área fuera de Frontera Agrícola" if a1_nivel == "rojo" else "—")

    _blq_rows = [
        ("",   "Existencia del Predio",       "PostGIS / IGAC",
         exist_nivel,
         exist_texto or ("Polígono catastral identificado" if exist_nivel != "rojo"
                         else "Predio no encontrado")),
        ("A1", "Zona Agrícola · Frontera",    "PostGIS / IGAC",
         a1_nivel, _front_res),
        ("A2", "Área Efectiva Cultivable",    "DEM · NDVI · Catastro",
         a2_nivel, f"{area_ef:.2f} ha ({pct_ef:.0f}% del predio)"),
        ("B1", "Aptitud al Cultivo",          "UPRA · datos.gov.co",
         b1_nivel,
         f"{apt_cat} (score {apt_score:.2f})" if apt_cat else "—"),
        ("B2", "Actividad Productiva NDVI",   "GEE · Sentinel-2",
         b2_nivel,
         f"{b2_pct:.0f}% escenas activas · {b2_peak}/{b2_nyears} años con pico"
         if b2 else "—"),
        ("B3", "Altitud vs. Cultivo",         "DEM Terrarium · Ref. UPRA",
         b3_nivel, b3_res),
        ("C",  "Infraestructura / Acceso",    "OSM · OSRM",
         c_nivel,
         f"{infra_cu['distancia_km']} km a {infra_cu['nombre']}" if infra_cu else "—"),
        ("D",  "Riesgo Agroclimático",        "ERA5 · Open-Meteo · P80/P20",
         d_nivel, d_label if d_label != "—" else "—"),
    ]

    hdr_resumen = [
        P("Bloque","th"), P("Indicador","th"), P("Fuente","th"),
        P("Score","th"), P("Resultado","th"), P("Acción","th"),
    ]
    rows_resumen = [hdr_resumen]
    extra_resumen = []
    for i, (code, name, src, sem, res) in enumerate(_blq_rows):
        bg = _SEM_BG.get(sem, C.GREY_BG)
        fg = _SEM_FG.get(sem, C.SUBTEXT)
        em = _SEM_EMO.get(sem, "⚪")
        ac = _SEM_ACT.get(sem, "—")
        ps_sem = _sty("rs", fontName="Helvetica-Bold", fontSize=11,
                      alignment=TA_CENTER, textColor=_hex(fg))
        rows_resumen.append([
            P(f"<b>{code}</b>", "td"),
            P(name, "td"),
            P(src,  "td_sm"),
            Paragraph(em, ps_sem),
            P(res,  "td"),
            P(ac,   "td_sm"),
        ])
        extra_resumen.append(
            ("BACKGROUND", (0, i+1), (-1, i+1), _hex(bg))
        )
    resumen_t = _tbl(
        rows_resumen,
        cw=[TW*0.07, TW*0.22, TW*0.15, TW*0.07, TW*0.28, TW*0.21],
        extra_styles=extra_resumen,
    )
    story += [resumen_t, SP(0.12), HR()]

    # ── DOCUMENTACIÓN ADICIONAL REQUERIDA ─────────────────────────────────────
    doc_items = []
    if a1_nivel in ("naranja","amarillo"): doc_items.append(_DOC_REQ["A1_naranja"])
    if a1_nivel == "rojo":                 doc_items.append(_DOC_REQ["A1_rojo"])
    if a2_nivel == "amarillo":             doc_items.append(_DOC_REQ["A2_amarillo"])
    if a2_nivel == "rojo":                 doc_items.append(_DOC_REQ["A2_rojo"])
    if b1_nivel in ("amarillo","naranja"): doc_items.append(_DOC_REQ["B1_amarillo"])
    if b1_nivel == "rojo":                 doc_items.append(_DOC_REQ["B1_rojo"])
    if b2_nivel in ("amarillo","naranja"): doc_items.append(_DOC_REQ["B2_amarillo"])
    if b2_nivel == "rojo":                 doc_items.append(_DOC_REQ["B2_rojo"])
    if c_nivel in ("naranja","amarillo"):  doc_items.append(_DOC_REQ["C_naranja"])
    if c_nivel == "rojo":                  doc_items.append(_DOC_REQ["C_rojo"])
    if d_score is not None:
        if 0.25 <= d_score < 0.50:         doc_items.append(_DOC_REQ["D_medio"])
        if 0.50 <= d_score < 0.75:         doc_items.append(_DOC_REQ["D_alto"])
        if d_score >= 0.75:                doc_items.append(_DOC_REQ["D_extremo"])

    story.append(P("Documentación Adicional Requerida", "h2"))
    if doc_items:
        doc_rows = [[P("Ítem","th"), P("Requerimiento","th")]]
        for i, itm in enumerate(doc_items):
            doc_rows.append([P(str(i+1),"td_c"), P(itm,"td")])
        doc_t = _tbl(doc_rows, cw=[TW*0.08, TW*0.92])
        story.append(doc_t)
    else:
        story.append(P("✅ Ninguna documentación adicional requerida — todos los indicadores en verde.", "body"))
    story += [SP(0.22)]

    # ── APROBACIÓN Y FIRMAS ───────────────────────────────────────────────────
    # Bloque de firma sin ninguna línea de tabla: la línea para firmar es el
    # subrayado de cada columna (separados por el padding, sin línea continua).
    _und = "____________________"
    firma_t = Table(
        [
            [P("<b>Analista de Crédito</b>","td_c"),
             P("<b>Responsable de Riesgo</b>","td_c"),
             P("<b>Gerente de Área</b>","td_c")],
            [P(" ","td_c"), P(" ","td_c"), P(" ","td_c")],          # espacio para firmar
            [P(_und,"td_c"), P(_und,"td_c"), P(_und,"td_c")],       # línea de firma
            [P(f"Nombre: {_und}","small"),
             P(f"Nombre: {_und}","small"),
             P(f"Nombre: {_und}","small")],
            [P(f"Fecha:  {_und}","small"),
             P(f"Fecha:  {_und}","small"),
             P(f"Fecha:  {_und}","small")],
        ],
        colWidths=[TW/3]*3,
        rowHeights=[0.5*cm, 0.7*cm, 0.28*cm, 0.4*cm, 0.4*cm],
    )
    firma_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  _hex(C.LIGHT)),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,0),  "MIDDLE"),
        ("VALIGN",        (0,1), (-1,-1), "BOTTOM"),
        ("ALIGN",         (0,0), (-1,0),  "CENTER"),
    ]))
    # KeepTogether evita que el título y la tabla de firmas se partan entre páginas
    story += [KeepTogether([P("Aprobación y Firmas", "h2"), firma_t])]

    # ════════════════════════════════════════════════════════════════════════
    #  PÁGINA 2+ · ANÁLISIS DETALLADO DE LOS INDICADORES
    # ════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(P("Análisis detallado de los indicadores", "h2"))
    story.append(P("Desglose por bloque de validación. El resumen y la resolución "
                   "consolidada figuran en la primera página.", "small"))
    story.append(SP(0.2))

    # ── A · VALIDACIÓN GEOMÉTRICA ────────────────────────────────────────────
    story.append(P("A · Validación Geométrica y Legal", "h2"))

    # A1 tabla de frontera
    story.append(P("A1 · Zona Agrícola — Frontera Agrícola Nacional", "h3"))
    if gdf_front is not None and len(gdf_front) > 0:
        _pct_tot_f = float(gdf_front["pct_predio"].sum())
        _pct_out_f = round(max(0.0, 100.0 - _pct_tot_f), 1)
        front_rows_df = (
            gdf_front.groupby("tipo_condi")
            .agg(area_ha=("area_ha","sum"), pct_predio=("pct_predio","sum"))
            .reset_index()
        )
        fhdr = [P("Tipo de zona","th"), P("Área (ha)","th"), P("% del predio","th")]
        frows = [fhdr]
        for _, fr in front_rows_df.iterrows():
            is_cond = fr["tipo_condi"] != "Frontera Agrícola no condicionada"
            ps_tc = _sty("ftc", fontSize=8, textColor=_hex(C.AMBER if is_cond else C.GREEN))
            frows.append([
                Paragraph(fr["tipo_condi"], ps_tc),
                P(f"{fr['area_ha']:.2f}", "td_c"),
                P(f"{fr['pct_predio']:.1f}%", "td_c"),
            ])
        if _pct_out_f > 2:
            ps_out = _sty("fout", fontSize=8, textColor=_hex(C.RED),
                          fontName="Helvetica-Bold")
            frows.append([
                Paragraph(f"⛔ Fuera de Frontera Agrícola", ps_out),
                P(f"{round(_pct_out_f/100*area_tot,4):.4f}", "td_c"),
                P(f"{_pct_out_f:.1f}%", "td_c"),
            ])
        front_t = _tbl(frows, cw=[TW*0.65, TW*0.17, TW*0.18])
        story.append(front_t)
    else:
        story.append(P("Sin datos de frontera agrícola calculados.", "small"))
    story.append(SP(0.3))

    # A2 desglose de áreas — misma tabla que la tab Validación Pre-Crédito:
    # Área total · − Área no cultivable (unión A2A+A2B+A2C) · = Área efectiva.
    story.append(P("A2 · Área Efectiva Cultivable — Desglose", "h3"))
    if not a2_computed:
        story.append(P("Área efectiva no calculada en la app.", "small"))
    else:
        _pct_no_cult = area_no_cult / max(area_tot, 1) * 100
        a2_rows = [
            [P("Componente","th"), P("Hectáreas","th"), P("% del predio","th")],
            [P("Área total del predio","td"),
             P(f"{area_tot:.4f}","td_c"), P("100.0%","td_c")],
            [P("− Área no cultivable (unión de A2A + A2B + A2C)","td"),
             P(f"−{area_no_cult:.4f}","td_c"),
             P(f"{_pct_no_cult:.1f}%","td_c")],
            [P("✅ Área efectiva cultivable","td"),
             P(f"{area_ef:.4f}","td_c"),
             P(f"{pct_ef:.1f}%","td_c")],
        ]
        a2_bg = _SEM_BG.get(a2_nivel, C.GREY_BG)
        a2_t = _tbl(
            a2_rows,
            cw=[TW*0.65, TW*0.18, TW*0.17],
            extra_styles=[
                ("BACKGROUND",  (0, 3), (-1, 3), _hex(a2_bg)),
                ("FONTNAME",    (0, 3), (-1, 3), "Helvetica-Bold"),
            ],
        )
        story.append(a2_t)
    story += [SP(0.4), HR()]

    # ── B · CONTINUIDAD PRODUCTIVA ───────────────────────────────────────────
    story.append(P("B · Validación de Continuidad Productiva", "h2"))

    # B1 aptitud
    story.append(P("B1 · Aptitud al Cultivo", "h3"))
    b1_bg = _SEM_BG.get(b1_nivel, C.GREY_BG)
    b1_rows = [
        [P("Indicador","th"), P("Resultado","th")],
        [P("Cultivo evaluado","td"),       P(cultivo.capitalize(),"td")],
        [P("Categoría de aptitud","td"),   P(apt_cat or "—","td")],
        [P("Score ponderado (0–1)","td"),  P(f"{apt_score:.2f}" if apt_score else "—","td")],
        [P("Fuente","td"),                 P("UPRA · API datos.gov.co","td")],
    ]
    b1_t = _tbl(b1_rows, cw=[TW*0.5, TW*0.5],
                extra_styles=[("BACKGROUND",(0,3),(-1,3),_hex(b1_bg)),
                               ("FONTNAME",(0,3),(-1,3),"Helvetica-Bold")])
    story += [b1_t, SP(0.3)]

    # B2 actividad productiva
    story.append(P("B2 · Actividad Productiva (NDVI histórico · Sentinel-2)", "h3"))
    if b2:
        b2_bg = _SEM_BG.get(b2_nivel, C.GREY_BG)
        b2_kpis = [
            ("Escenas activas",          f"{b2_pct:.0f}%"),
            ("Umbral escena",            f"≥ {b2_thr_s:.2f}"),
            ("Años con pico anual",      f"{b2_peak}/{b2_nyears}"),
            ("Umbral pico anual",        f"≥ {b2_thr_p:.2f}"),
            ("NDVI mediano global",      f"{b2.get('overall_median',0):.3f}"),
            ("Escenas analizadas",       str(len(b2.get("stats",[])))),
        ]
        b2_kpi_cells = []
        for lbl, val in b2_kpis:
            ps_v = _sty("b2v", fontName="Helvetica-Bold", fontSize=11,
                        alignment=TA_CENTER, textColor=_hex(C.DARK))
            ps_l = _sty("b2l", fontSize=7.5, alignment=TA_CENTER,
                        textColor=_hex(C.SUBTEXT))
            b2_kpi_cells.append(Table(
                [[Paragraph(val, ps_v)], [Paragraph(lbl, ps_l)]],
                colWidths=[TW/6 - 0.1*cm],
            ))
        b2_kpi_t = Table([b2_kpi_cells], colWidths=[TW/6]*6)
        b2_kpi_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(b2_bg)),
            ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("TOPPADDING",    (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(b2_kpi_t)
        story.append(SP(0.2))

        # Picos anuales
        peak_by_year = b2.get("peak_by_year", {})
        if peak_by_year:
            ph = [P("Año","th"), P("Pico NDVI","th"), P("Umbral","th"), P("Resultado","th")]
            prows = [ph]
            for yr, val in sorted(peak_by_year.items()):
                ok = val >= b2_thr_p
                prows.append([
                    P(str(yr), "td_c"),
                    P(f"{val:.3f}", "td_c"),
                    P(f"≥ {b2_thr_p:.2f}", "td_c"),
                    P("✅ Confirmado" if ok else "⚠️ No alcanzado", "td_c"),
                ])
            peak_t = _tbl(prows, cw=[TW*0.2, TW*0.2, TW*0.2, TW*0.4],
                          extra_styles=[
                              ("BACKGROUND", (0, i+1), (-1, i+1),
                               _hex(C.GREEN_BG if list(sorted(peak_by_year.items()))[i][1] >= b2_thr_p
                                    else C.RED_BG))
                              for i in range(len(peak_by_year))
                          ])
            story.append(peak_t)
    else:
        story.append(P("Actividad productiva NDVI no calculada.", "small"))
    story.append(SP(0.3))

    # B3 altitud
    story.append(P("B3 · Altitud del Predio vs. Cultivo (informativo)", "h3"))
    if b3_elev is not None:
        b3_bg = _SEM_BG.get(b3_nivel, C.GREY_BG)
        b3_rows = [
            [P("Indicador","th"), P("Resultado","th")],
            [P("Elevación media del predio","td"), P(f"{b3_elev:.0f} m","td")],
            [P("Rango altitudinal del cultivo","td"),
             P(f"{b3_alt_min}–{b3_alt_max} m" if b3_alt_min is not None else "—","td")],
            [P("Resultado","td"),
             P("Dentro del rango" if b3_nivel == "verde" else
               "Fuera del rango" if b3_nivel == "rojo" else "—","td")],
        ]
        b3_t = _tbl(b3_rows, cw=[TW*0.5, TW*0.5],
                    extra_styles=[("BACKGROUND",(0,3),(-1,3),_hex(b3_bg)),
                                   ("FONTNAME",(0,3),(-1,3),"Helvetica-Bold")])
        story.append(b3_t)
    else:
        story.append(P("Altitud no calculada (requiere análisis de terreno A2-A).", "small"))
    story += [SP(0.4), HR()]

    # ── C · INFRAESTRUCTURA ──────────────────────────────────────────────────
    story.append(P("C · Infraestructura Productiva", "h2"))
    c1_dist = infra_cu.get("distancia_km", "—") if infra_cu else "—"
    c1_nom  = infra_cu.get("nombre", "—")       if infra_cu else "—"
    c1_dur  = infra_cu.get("duracion_min", "—")  if infra_cu else "—"
    c3_dist = infra_cu.get("dist_recta_km", "—") if infra_cu else "—"
    c2_dist = infra_via.get("distancia_m", "—")  if infra_via else "—"
    c2_nom  = infra_via.get("nombre", "—")       if infra_via else "—"
    c2_tipo = infra_via.get("tipo", "—")         if infra_via else "—"
    _c1n = ("verde" if infra_cu and infra_cu.get("distancia_km",99) < 10 else
            "naranja" if infra_cu and infra_cu.get("distancia_km",99) < 25 else
            "rojo" if infra_cu else "gris")
    _c3n = ("verde" if infra_cu and infra_cu.get("dist_recta_km",99) < 5 else
            "naranja" if infra_cu and infra_cu.get("dist_recta_km",99) < 15 else
            "rojo" if infra_cu else "gris")
    _c2n = ("verde" if infra_via and infra_via.get("distancia_m",9999) < 500 else
            "naranja" if infra_via and infra_via.get("distancia_m",9999) < 2000 else
            "rojo" if infra_via else "gris")
    c_rows  = [
        [P("Indicador","th"), P("Resultado","th"), P("Umbral","th"), P("Score","th")],
        [P("C1 · Centro urbano (por carretera)","td"),
         P(f"{c1_dist} km · {c1_nom} ({c1_dur} min)", "td"),
         P("< 10 km verde · 10–25 km amarillo · > 25 km rojo", "td_sm"),
         P(_SEM_EMO.get(_c1n, "⚪"), "td_c")],
        [P("C3 · Centro urbano (línea recta)","td"),
         P(f"{c3_dist} km · {c1_nom}", "td"),
         P("< 5 km verde · 5–15 km amarillo · > 15 km rojo", "td_sm"),
         P(_SEM_EMO.get(_c3n, "⚪"), "td_c")],
        [P("C2 · Vía transitable más cercana","td"),
         P(f"{c2_dist} m · {c2_nom} ({c2_tipo})", "td"),
         P("< 500 m verde · 500 m–2 km amarillo · > 2 km rojo", "td_sm"),
         P(_SEM_EMO.get(_c2n, "⚪"), "td_c")],
    ]
    c_t = _tbl(c_rows, cw=[TW*0.33, TW*0.37, TW*0.22, TW*0.08],
               extra_styles=[
                   ("BACKGROUND", (0,1), (-1,1), _hex(_SEM_BG.get(_c1n, C.GREY_BG))),
                   ("BACKGROUND", (0,2), (-1,2), _hex(_SEM_BG.get(_c3n, C.GREY_BG))),
                   ("BACKGROUND", (0,3), (-1,3), _hex(_SEM_BG.get(_c2n, C.GREY_BG))),
               ])
    story += [c_t, P("Semáforo global C = peor de los tres indicadores.", "small"),
              SP(0.4), HR()]

    # ── D · RIESGO AGROCLIMÁTICO ─────────────────────────────────────────────
    story.append(P("D · Riesgo Agroclimático", "h2"))
    d_bg_g  = _SEM_BG.get(d_nivel, C.GREY_BG)
    d_fg_g  = _SEM_FG.get(d_nivel, C.DARK)
    ps_dscore = _sty("ds", fontName="Helvetica-Bold", fontSize=12,
                     alignment=TA_CENTER, textColor=_hex(d_fg_g))
    ps_dlbl   = _sty("dl2", fontSize=8.5, alignment=TA_CENTER,
                     textColor=_hex(d_fg_g))
    d_head_t  = Table(
        [[Paragraph(f"{d_label}  |  Score: {d_score:.2f}" if d_score else d_label,
                    ps_dscore)],
         [Paragraph("Score global = media del peor indicador por categoría de riesgo "
                    "(año adverso: P80 curvas crecientes · P20 decrecientes)", ps_dlbl)]],
        colWidths=[TW],
    )
    d_head_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), _hex(d_bg_g)),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(d_head_t)
    story.append(SP(0.25))

    if df_risk is not None and not df_risk.empty:
        _COLOR_BG_D  = {
            "verde":"#d1fae5","amarillo":"#dcfce7","naranja":"#fef9c3",
            "rojo":"#fed7aa","granate":"#fee2e2","gris":"#f8fafc",
        }
        dh = [P("Categoría","th"), P("Indicador","th"),
              P("Valor medio","th"), P("Valor adverso","th"), P("Score","th")]
        drows_html = [dh]
        d_extra = []
        # TODOS los indicadores (ordenados por categoría), incluso score 0 / verde
        _df_sorted = df_risk.sort_values(["Categoría_riesgo", "score_p80"],
                                         ascending=[True, False], na_position="last")
        _ri = 0
        for _, r in _df_sorted.iterrows():
            _ri += 1
            score_v = r.get("score_p80")
            color   = r.get("riesgo_color", "gris")
            ps_cat = _sty("dcat", fontSize=7.5, fontName="Helvetica-Bold",
                          textColor=_hex(_SEM_FG.get(
                              "verde" if color in ("verde","amarillo") else
                              "amarillo" if color == "naranja" else
                              "rojo" if color in ("rojo","granate") else "gris", C.DARK)))
            _pct_ref = r.get("percentil_ref", 80)
            _pmark   = " (P20)" if int(_pct_ref or 80) == 20 else ""
            _unidad  = r.get("Unidad", "")
            _vm = r.get("valor_medio"); _vp = r.get("valor_p80")
            drows_html.append([
                Paragraph(str(r.get("Categoría_riesgo","—")), ps_cat),
                P(str(r.get("Nombre_indicador","—")), "td_sm"),
                P(f"{_vm:.1f} {_unidad}" if _vm is not None else "—", "td_c"),
                P(f"{_vp:.1f} {_unidad}{_pmark}" if _vp is not None else "—", "td_c"),
                P(f"{score_v:.2f}" if score_v is not None else "—", "td_c"),
            ])
            d_extra.append(("BACKGROUND", (0,_ri), (-1,_ri),
                            _hex(_COLOR_BG_D.get(color, C.GREY_BG))))
        if len(drows_html) > 1:
            d_t = _tbl(drows_html, cw=[TW*0.20, TW*0.34, TW*0.15, TW*0.16, TW*0.15],
                       extra_styles=d_extra)
            story.append(d_t)
        else:
            story.append(P("Sin indicadores de riesgo calculados.", "small"))
    else:
        story.append(P("Riesgo agroclimático no calculado.", "small"))

    # Serie climática mensual
    precip  = datos.get("precip_mensual", [])
    tmax    = datos.get("temp_max_mensual", [])
    tmin    = datos.get("temp_min_mensual", [])
    if precip and tmax and tmin:
        story += [SP(0.3), P("Serie Climática Mensual (promedio histórico)", "h3")]
        try:
            _buf = _clima_chart_png(MESES_ES, precip, tmax, tmin)
            story.append(RLImage(_buf, width=TW,
                                 height=TW * _CLIMA_FIG_H / _CLIMA_FIG_W))
        except Exception:
            # Fallback: tabla numérica si matplotlib no está disponible
            ch = [P(m, "th") for m in ["Mes"] + MESES_ES]
            pr_row = [P("Precip. (mm)", "td")] + [
                P(str(precip[i]) if i < len(precip) else "—", "td_c") for i in range(12)]
            tx_row = [P("T máx (°C)", "td")] + [
                P(str(tmax[i]) if i < len(tmax) else "—", "td_c") for i in range(12)]
            tn_row = [P("T mín (°C)", "td")] + [
                P(str(tmin[i]) if i < len(tmin) else "—", "td_c") for i in range(12)]
            story.append(_tbl([ch, pr_row, tx_row, tn_row],
                              cw=[TW*0.12] + [TW*0.88/12]*12,
                              row_bgs=[C.LIGHT, C.WHITE]))

    story += [SP(0.4), HR()]

    # ── NOTA LEGAL ────────────────────────────────────────────────────────────
    story.append(P(
        "Este reporte es generado automáticamente por AgroCredito a partir de datos satelitales, "
        "catastrales y climáticos de acceso público. No constituye dictamen definitivo. "
        "El analista de crédito deberá validar cualquier condición condicional o negativa "
        "mediante visita en campo o documentación adicional. "
        f"Fuentes: Sentinel-2 SR (Google Earth Engine), DEM SRTM (AWS Terrarium), ERA5 (Open-Meteo), IGAC, UPRA (datos.gov.co), OSM.",
        "small",
    ))

    doc.build(story)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  FUNCIÓN PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════

def generate_exante_report(
    datos:    dict,
    predio:   dict | None,
    analisis: dict | None = None,
    fmt:      Literal["pdf"] = "pdf",
    # backwards-compat kwargs (ignored)
    scoring:  dict | None = None,
) -> bytes:
    """
    Genera el reporte ex-ante en PDF ejecutivo.

    Args:
        datos:    dict del caso de estudio (variables de display / clima)
        predio:   dict devuelto por get_predio_por_punto
        analisis: dict con resultados calculados (existencia, A1, A2, B1, B2, B3, C, D, áreas)
        fmt:      "pdf" (otros formatos en versión futura)

    Returns:
        bytes listos para st.download_button
    """
    return _build_pdf(datos, predio, analisis)


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTE DE MONITOREO DE PORTAFOLIO
# ══════════════════════════════════════════════════════════════════════════════

_MON_LBL = {"verde": "Normal", "amarillo": "Precaución",
            "rojo": "Alerta", "gris": "—"}


def _build_monitoring_pdf(data: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether, PageBreak,
    )

    W, H = A4
    buf  = BytesIO()
    predios = data.get("predios", [])
    fecha   = data.get("fecha", "")
    n       = len(predios)
    n_alert = sum(1 for p in predios if p.get("global", "verde") != "verde")

    _nn = [0]
    def _sty(base="s", **kw):
        _nn[0] += 1
        d = dict(fontName="Helvetica", fontSize=9, textColor=_hex(C.DARK), leading=13)
        d.update(kw)
        return ParagraphStyle(f"_m{_nn[0]}_{base}", **d)

    S = {
        "title": _sty("title", fontName="Helvetica-Bold", fontSize=18, textColor=_hex(C.DARK)),
        "sub":   _sty("sub", fontSize=9, textColor=_hex(C.SUBTEXT)),
        "h2":    _sty("h2", fontName="Helvetica-Bold", fontSize=11, textColor=_hex(C.DARK),
                      spaceBefore=6, spaceAfter=3),
        "h3":    _sty("h3", fontName="Helvetica-Bold", fontSize=9.5, textColor=_hex(C.MID),
                      spaceBefore=4, spaceAfter=2),
        "body":  _sty("body"),
        "small": _sty("small", fontSize=7.5, textColor=_hex(C.SUBTEXT), fontName="Helvetica-Oblique"),
        "th":    _sty("th", fontName="Helvetica-Bold", fontSize=8, textColor=_hex(C.WHITE),
                      alignment=TA_CENTER),
        "td":    _sty("td", fontSize=8, leading=11),
        "td_c":  _sty("td_c", fontSize=8, leading=11, alignment=TA_CENTER),
        "td_sm": _sty("td_sm", fontSize=7.3, leading=9.5),
    }

    def P(t, st="body"): return Paragraph(str(t), S[st])
    def SP(h=0.25):      return Spacer(1, h * cm)
    def HR():
        return HRFlowable(width="100%", thickness=0.5, color=_hex(C.BORDER),
                          spaceAfter=3, spaceBefore=3)

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(_hex(C.DARK)); canvas.rect(0, H - 1.1*cm, W, 1.1*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE)); canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(1.8*cm, H - 0.75*cm, "AgroCredito · Reporte de Monitoreo de Portafolio")
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(W - 1.8*cm, H - 0.75*cm, date.today().strftime("%d/%m/%Y"))
        canvas.setFillColor(_hex(C.MID)); canvas.rect(0, 0, W, 0.8*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE)); canvas.setFont("Helvetica", 7)
        canvas.drawString(1.8*cm, 0.28*cm, "Confidencial · Uso interno · Generado automáticamente")
        canvas.drawRightString(W - 1.8*cm, 0.28*cm, f"Pág. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.8*cm, bottomMargin=1.5*cm,
                            onFirstPage=_on_page, onLaterPages=_on_page)
    TW = W - 3.6*cm

    def _tbl(rows, cw, header=True, extra=None):
        t = Table(rows, colWidths=cw, repeatRows=1 if header else 0)
        base = [
            ("TOPPADDING", (0,0), (-1,-1), 3.5), ("BOTTOMPADDING", (0,0), (-1,-1), 3.5),
            ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("GRID", (0,0), (-1,-1), 0.4, _hex(C.BORDER)), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]
        if header: base += [("BACKGROUND", (0,0), (-1,0), _hex(C.MID))]
        if extra:  base += extra
        t.setStyle(TableStyle(base))
        return t

    story = []

    # ── PORTADA ───────────────────────────────────────────────────────────────
    story += [
        SP(0.25),
        P("Reporte de Monitoreo de Portafolio", "title"), SP(0.45),
        P("Seguimiento agroclimático y de vegetación durante la vida del crédito · Colombia", "sub"),
        SP(0.22), HR(), SP(0.12),
    ]

    ficha = _tbl([
        [P("<b>Fecha del monitoreo</b>","td"), P(fecha,"td"),
         P("<b>Predios monitoreados</b>","td"), P(str(n),"td")],
        [P("<b>Predios con alerta</b>","td"),
         P(f"{n_alert} de {n}" + (" 🔴" if n_alert else ""),"td"),
         P("<b>Fuentes</b>","td"),
         P("Sentinel-2 (GEE) · ERA5 (Open-Meteo)","td")],
    ], cw=[TW*0.22, TW*0.28, TW*0.22, TW*0.28], header=False,
       extra=[("BACKGROUND",(0,0),(0,-1),_hex(C.LIGHT)),
              ("BACKGROUND",(2,0),(2,-1),_hex(C.LIGHT))])
    story += [ficha, SP(0.2)]

    # ── RESUMEN DE MONITOREO (todos los predios) ───────────────────────────────
    story.append(P("Resumen de Monitoreo · Portafolio", "h2"))
    story.append(P("La Alerta Global es el peor estado actual entre Vegetación (Hoy) y "
                   "Clima (Hoy). El forecast (+7/+14 días) es anticipación.", "small"))

    hdr = [P("Predio","th"), P("Cultivo","th"), P("Alerta Global","th"),
           P("Vegetación (Hoy)","th"), P("Clima (Hoy)","th"),
           P("Clima +7d","th"), P("Clima +14d","th")]
    rows = [hdr]; extra = []
    for i, p in enumerate(predios):
        r = i + 1
        gl = p.get("global", "gris")
        rows.append([
            P(p.get("nombre","—"), "td"),
            P(p.get("cultivo","—"), "td_sm"),
            P(_MON_LBL.get(gl, "—"), "td_c"),
            P(p.get("veg", {}).get("text","—"), "td_sm"),
            P(p.get("hoy", {}).get("text","—"), "td_sm"),
            P(p.get("f7", {}).get("text","—"), "td_sm"),
            P(p.get("f14", {}).get("text","—"), "td_sm"),
        ])
        # Celda Alerta Global (col 2) — destacada (color + fuente mayor en negrita)
        extra += [
            ("BACKGROUND", (2, r), (2, r), _hex(_SEM_BG.get(gl, C.GREY_BG))),
            ("TEXTCOLOR",  (2, r), (2, r), _hex(_SEM_FG.get(gl, C.SUBTEXT))),
            ("FONTNAME",   (2, r), (2, r), "Helvetica-Bold"),
            ("FONTSIZE",   (2, r), (2, r), 9.5),
            ("BACKGROUND", (3, r), (3, r), _hex(_SEM_BG.get(p.get("veg",{}).get("nivel"), C.WHITE))),
            ("BACKGROUND", (4, r), (4, r), _hex(_SEM_BG.get(p.get("hoy",{}).get("nivel"), C.WHITE))),
            ("BACKGROUND", (5, r), (5, r), _hex(_SEM_BG.get(p.get("f7",{}).get("nivel"), C.WHITE))),
            ("BACKGROUND", (6, r), (6, r), _hex(_SEM_BG.get(p.get("f14",{}).get("nivel"), C.WHITE))),
        ]
    # Enmarca la columna Alerta Global (cabecera + filas) para que resalte
    _lastr = len(predios)
    extra += [
        ("LINEBEFORE", (2, 0), (2, _lastr), 1.6, _hex(C.DARK)),
        ("LINEAFTER",  (2, 0), (2, _lastr), 1.6, _hex(C.DARK)),
    ]
    story.append(_tbl(rows, cw=[TW*0.17, TW*0.10, TW*0.15, TW*0.18,
                                TW*0.16, TW*0.12, TW*0.12], extra=extra))
    story += [SP(0.25), HR()]

    # ── ACCIONES REQUERIDAS ─────────────────────────────────────────────────────
    story.append(P("Acciones Requeridas", "h2"))
    _acc_rows = [[P("Predio","th"), P("Acción recomendada","th")]]
    _any = False
    for p in predios:
        for a in p.get("acciones", []):
            _acc_rows.append([P(p.get("nombre","—"), "td"), P(a, "td")])
            _any = True
    if _any:
        story.append(_tbl(_acc_rows, cw=[TW*0.24, TW*0.76]))
    else:
        story.append(P("✅ Ningún predio requiere acción — todo el portafolio en estado normal.", "body"))
    story += [SP(0.22)]

    # ── APROBACIÓN Y FIRMAS ─────────────────────────────────────────────────────
    _und = "____________________"
    firma = Table([
        [P("<b>Responsable de Monitoreo</b>","td_c"), P("<b>Analista de Crédito</b>","td_c"),
         P("<b>Gerente de Área</b>","td_c")],
        [P(" ","td_c"), P(" ","td_c"), P(" ","td_c")],
        [P(_und,"td_c"), P(_und,"td_c"), P(_und,"td_c")],
        [P(f"Nombre: {_und}","small"), P(f"Nombre: {_und}","small"), P(f"Nombre: {_und}","small")],
        [P(f"Fecha:  {_und}","small"), P(f"Fecha:  {_und}","small"), P(f"Fecha:  {_und}","small")],
    ], colWidths=[TW/3]*3, rowHeights=[0.5*cm, 0.7*cm, 0.28*cm, 0.4*cm, 0.4*cm])
    firma.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),_hex(C.LIGHT)),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("VALIGN",(0,0),(-1,0),"MIDDLE"), ("VALIGN",(0,1),(-1,-1),"BOTTOM"),
        ("ALIGN",(0,0),(-1,0),"CENTER"),
    ]))
    story += [KeepTogether([P("Aprobación y Firmas", "h2"), firma])]

    # ── PÁGINA 2+ · DETALLE POR PREDIO ──────────────────────────────────────────
    story.append(PageBreak())
    story.append(P("Análisis detallado por predio", "h2"))
    story.append(P("Vegetación NDVI y clima (Hoy / +7 / +14 días) para cada predio del portafolio.", "small"))

    for p in predios:
        gl = p.get("global", "gris")
        _hdr_bg = _SEM_BG.get(gl, C.GREY_BG); _hdr_fg = _SEM_FG.get(gl, C.DARK)
        _hp = _sty("hp", fontName="Helvetica-Bold", fontSize=10.5, textColor=_hex(_hdr_fg))
        _cab = Table([[Paragraph(
            f"{p.get('nombre','—')} · {p.get('cultivo','—')} — Alerta Global: {_MON_LBL.get(gl,'—')}", _hp)]],
            colWidths=[TW])
        _cab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_hex(_hdr_bg)),
                                  ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
                                  ("LEFTPADDING",(0,0),(-1,-1),10)]))
        story += [SP(0.15), _cab, SP(0.12)]

        # NDVI
        nd = p.get("ndvi_detalle")
        story.append(P("Vegetación · NDVI (Sentinel-2 · GEE)", "h3"))
        if nd:
            _fmt = lambda v, s="": ("—" if v is None else f"{v}{s}")
            story.append(_tbl([
                [P("NDVI último","th"), P("Anomalía vs. mes","th"), P("Tendencia","th"),
                 P("Última escena","th"), P("N° escenas (1a)","th")],
                [P(_fmt(nd.get('ndvi')), "td_c"),
                 P(_fmt(nd.get('anom'), " %"), "td_c"),
                 P(_fmt(nd.get('tend')), "td_c"),
                 P(_fmt(nd.get('fecha')), "td_c"),
                 P(_fmt(nd.get('n')), "td_c")],
            ], cw=[TW*0.2]*5))
        else:
            story.append(P("Sin escenas NDVI válidas en el último año.", "small"))
        story.append(SP(0.12))

        # Clima por indicador y horizonte
        story.append(P("Clima · indicadores por horizonte", "h3"))
        inds = p.get("clima_indicadores", [])
        if inds:
            chdr = [P("Indicador","th"), P("Hoy","th"), P("+7 días","th"), P("+14 días","th")]
            crows = [chdr]; cextra = []
            for j, ind in enumerate(inds):
                rr = j + 1
                crows.append([
                    P(ind.get("label","—"), "td_sm"),
                    P(ind.get("Hoy",{}).get("display","—"), "td_sm"),
                    P(ind.get("+7 días",{}).get("display","—"), "td_sm"),
                    P(ind.get("+14 días",{}).get("display","—"), "td_sm"),
                ])
                for ci, hz in [(1,"Hoy"),(2,"+7 días"),(3,"+14 días")]:
                    _s = ind.get(hz,{}).get("sem")
                    if _s:
                        cextra.append(("BACKGROUND",(ci,rr),(ci,rr),_hex(_SEM_BG.get(_s, C.WHITE))))
            story.append(_tbl(crows, cw=[TW*0.34, TW*0.22, TW*0.22, TW*0.22], extra=cextra))
        else:
            story.append(P("Sin datos climáticos para este predio.", "small"))
        story += [SP(0.15), HR()]

    # ── NOTA LEGAL ──────────────────────────────────────────────────────────────
    story.append(P(
        "Reporte de monitoreo generado automáticamente por AgroCredito a partir de datos "
        "satelitales y climáticos de acceso público. Las alertas son indicativas y deben "
        "validarse con contacto al productor o visita técnica. "
        "Fuentes: Sentinel-2 SR (Google Earth Engine), ERA5 (Open-Meteo).",
        "small",
    ))

    doc.build(story)
    return buf.getvalue()


def generate_monitoring_report(data: dict) -> bytes:
    """
    Genera el reporte de monitoreo de portafolio en PDF (mismo estilo que el ex-ante).

    `data` = {
        "fecha": "DD/MM/YYYY",
        "predios": [
            {"nombre","cultivo","lat","lon","global",
             "veg":{"nivel","text"}, "hoy":{...}, "f7":{...}, "f14":{...},
             "acciones":[str,...],
             "ndvi_detalle":{"ndvi","anom","tend","fecha","n"} | None,
             "clima_indicadores":[{"label","Hoy":{"sem","display"},"+7 días":{...},"+14 días":{...}}]},
            ...
        ],
    }
    """
    return _build_monitoring_pdf(data)
