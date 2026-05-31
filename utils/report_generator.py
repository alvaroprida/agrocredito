"""
utils/report_generator.py
Reporte ex-ante PDF ejecutivo para evaluación de crédito agropecuario.

Estructura:
  1. Ficha del predio + dictamen ejecutivo
  2. Tabla resumen de indicadores (A1, A2, B1, B2, C, D)
  3. A · Detalle geométrico (frontera + desglose de áreas)
  4. B · Continuidad productiva (aptitud + actividad NDVI)
  5. D · Riesgo agroclimático
  6. C · Infraestructura
  7. Documentación adicional requerida
  8. Firmas + nota legal
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
    "verde":    "Sin restricción adicional.",
    "naranja":  "Documentación adicional recomendada.",
    "amarillo": "Documentación adicional recomendada.",
    "rojo":     "Inspección técnica / verificación presencial requerida.",
    "gris":     "Indicador no calculado.",
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

MESES_ES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]


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
        HRFlowable, KeepTogether, PageBreak,
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
                         textColor=_hex(C.DARK), spaceBefore=10, spaceAfter=4),
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
        "dictlbl": _sty("dictlbl", fontName="Helvetica-Bold", fontSize=15,
                         alignment=TA_CENTER),
        "dictdsc": _sty("dictdsc", fontSize=9, alignment=TA_CENTER,
                         textColor=_hex(C.SUBTEXT)),
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
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
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

    def sem_cell(nivel):
        """Celda coloreada con emoji de semáforo."""
        bg = _SEM_BG.get(nivel, C.GREY_BG)
        fg = _SEM_FG.get(nivel, C.SUBTEXT)
        em = _SEM_EMO.get(nivel, "⚪")
        ps = _sty("sem", fontName="Helvetica-Bold", fontSize=10,
                  alignment=TA_CENTER, textColor=_hex(fg))
        cell_t = Table([[Paragraph(em, ps)]], colWidths=[TW*0.08])
        cell_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(bg)),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        return cell_t

    # ══════════════════════════════════════════════════════════════════════════
    #  COLLECT DATA
    # ══════════════════════════════════════════════════════════════════════════

    # Predio metadata
    cod       = predio.get("codigo","—")       if predio else "—"
    dep       = predio.get("departamento","—") if predio else "—"
    mun       = an.get("municipio") or datos.get("municipio","—")
    area_tot  = float((predio.get("area_ha") if predio else None) or datos.get("area_total_ha", 0) or 0)
    cultivo   = an.get("cultivo") or datos.get("cultivo","—")
    c_lat     = an.get("c_lat",  datos.get("lat",  0.0))
    c_lon     = an.get("c_lon",  datos.get("lon",  0.0))

    # A1
    a1_nivel   = an.get("a1_nivel", "gris")
    gdf_front  = an.get("gdf_frontera")

    # A2 areas
    area_ef    = float(an.get("area_ef",   datos.get("area_efectiva_ha",   0)) or 0)
    pct_ef     = float(an.get("pct_ef",    round(area_ef/max(area_tot,1)*100, 1)))
    area_pend  = float(an.get("area_pend", datos.get("area_pendiente_excluida_ha", 0)) or 0)
    area_ndvi  = float(an.get("area_ndvi", datos.get("area_ndvi_bajo_ha", 0)) or 0)
    area_const = float(an.get("area_const",datos.get("area_construcciones_ha", 0)) or 0)
    slope_thr  = an.get("slope_thr", 25)
    ndvi_thr   = an.get("ndvi_thr",  0.25)
    a2_nivel   = ("verde" if pct_ef >= 70 else "amarillo" if pct_ef >= 40 else "rojo")

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

    # Global dictamen (worst of all 6)
    _rank = {"verde":0,"amarillo":1,"naranja":1,"rojo":2,"gris":-1}
    _all_niveles = [a1_nivel, a2_nivel, b1_nivel, b2_nivel, c_nivel, d_nivel]
    _valid = [n for n in _all_niveles if n != "gris"]
    _worst = max(_valid, key=lambda x: _rank.get(x, 0)) if _valid else "gris"
    _dict_labels = {
        "verde":    ("APTO",       "El predio cumple todos los criterios de elegibilidad ex-ante.",
                     C.GREEN_BG, C.GREEN),
        "amarillo": ("CONDICIONAL","El predio requiere documentación adicional antes de aprobación.",
                     C.AMBER_BG, C.AMBER),
        "naranja":  ("CONDICIONAL","El predio requiere documentación adicional antes de aprobación.",
                     C.AMBER_BG, C.AMBER),
        "rojo":     ("NO APTO",   "El predio no supera los criterios mínimos de elegibilidad.",
                     C.RED_BG,   C.RED),
        "gris":     ("PENDIENTE", "Ejecuta los indicadores en la app para completar el análisis.",
                     C.GREY_BG,  C.SUBTEXT),
    }
    d_lbl, d_desc, d_bg, d_fg = _dict_labels[_worst]

    # ══════════════════════════════════════════════════════════════════════════
    #  STORY
    # ══════════════════════════════════════════════════════════════════════════
    story = []

    # ── 1 · PORTADA / FICHA ───────────────────────────────────────────────────
    story += [
        SP(0.4),
        P("Reporte de Evaluación Ex-Ante", "title"),
        P("Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia", "sub"),
        SP(0.3), HR(), SP(0.15),
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
    story += [ficha, SP(0.4)]

    # ── 2 · DICTAMEN EJECUTIVO ────────────────────────────────────────────────
    dict_ps_lbl = _sty("dl", fontName="Helvetica-Bold", fontSize=14,
                        alignment=TA_CENTER, textColor=_hex(d_fg))
    dict_ps_dsc = _sty("dd", fontSize=9, alignment=TA_CENTER, textColor=_hex(d_fg))
    dict_box = Table(
        [[Paragraph(f"DICTAMEN: {d_lbl}", dict_ps_lbl)],
         [Paragraph(d_desc, dict_ps_dsc)]],
        colWidths=[TW],
    )
    dict_box.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), _hex(d_bg)),
        ("TOPPADDING",    (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING",   (0,0), (-1,-1), 14),
        ("RIGHTPADDING",  (0,0), (-1,-1), 14),
        ("LINEBELOW",     (0,0), (-1,0),  1, _hex(d_fg)),
    ]))
    story += [dict_box, SP(0.3)]

    # ── 2b · SCORE FINAL CONSOLIDADO ─────────────────────────────────────────
    if score_final is not None:
        _sf_colors = {
            1: (C.GREEN_BG, C.GREEN),
            2: (C.AMBER_BG, C.AMBER),
            3: (C.RED_BG,   C.RED),
            4: (C.RED_BG,   C.RED),
        }
        _sf_bg, _sf_fg = _sf_colors.get(score_final, (C.GREY_BG, C.SUBTEXT))
        sf_ps = _sty("sf", fontName="Helvetica-Bold", fontSize=11,
                     alignment=TA_CENTER, textColor=_hex(_sf_fg))
        sf_box = Table(
            [[Paragraph(f"SCORE FINAL: {score_final} / 4  ·  {decision_final}", sf_ps)]],
            colWidths=[TW],
        )
        sf_box.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(_sf_bg)),
            ("TOPPADDING",    (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("LEFTPADDING",   (0,0), (-1,-1), 14),
            ("RIGHTPADDING",  (0,0), (-1,-1), 14),
        ]))
        story += [sf_box, SP(0.15)]

    # Obs. unidad productiva
    if obs_unidad and obs_unidad != "—":
        story.append(P(f"<b>Observación inicial asesor:</b> {obs_unidad}", "body"))
    story.append(SP(0.25))

    # ── 3 · TABLA RESUMEN DE INDICADORES ─────────────────────────────────────
    story.append(P("Resumen de Indicadores", "h2"))

    _blq_rows = [
        ("A1", "Zona Agrícola · Frontera",  "PostGIS / IGAC",
         a1_nivel,
         ("Todo en Frontera Agrícola no condicionada"
          if a1_nivel == "verde"
          else f"Condicionada / parcialmente fuera de frontera"
          if a1_nivel in ("naranja","amarillo")
          else "Área fuera de Frontera Agrícola" if a1_nivel == "rojo" else "—")),
        ("A2", "Área Efectiva Cultivable",   "DEM · NDVI · Catastro",
         a2_nivel, f"{area_ef:.2f} ha ({pct_ef:.0f}% del predio)"),
        ("B1", "Aptitud al Cultivo",         "UPRA · datos.gov.co",
         b1_nivel,
         f"{apt_cat} (score {apt_score:.2f})" if apt_cat else "—"),
        ("B2", "Actividad Productiva NDVI",  "EOSDA · Sentinel-2",
         b2_nivel,
         f"{b2_pct:.0f}% escenas activas · {b2_peak}/{b2_nyears} años con pico"
         if b2 else "—"),
        ("C",  "Infraestructura / Acceso",   "OSM · OSRM",
         c_nivel,
         f"{infra_cu['distancia_km']} km a {infra_cu['nombre']}" if infra_cu else "—"),
        ("D",  "Riesgo Agroclimático",       "ERA5 · Open-Meteo · P80",
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
    story += [resumen_t, SP(0.5), HR()]

    # ── 4 · SECCIÓN A · VALIDACIÓN GEOMÉTRICA ────────────────────────────────
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
            bg = C.AMBER_BG if is_cond else C.GREEN_BG
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

    # A2 desglose de áreas
    story.append(P("A2 · Área Efectiva Cultivable — Desglose", "h3"))
    solapamiento = round(area_pend + area_ndvi + area_const - (area_tot - area_ef), 3)
    solapamiento = max(solapamiento, 0.0)
    a2_rows = [
        [P("Componente","th"), P("Hectáreas","th"), P("% del predio","th")],
        [P("Área total del predio","td"),
         P(f"{area_tot:.4f}","td_c"), P("100.0%","td_c")],
        [P(f"− Pendiente > {slope_thr}% (A2-A)","td"),
         P(f"−{area_pend:.4f}","td_c"),
         P(f"{area_pend/max(area_tot,1)*100:.1f}%","td_c")],
        [P(f"− NDVI P25 < {ndvi_thr:.2f} (A2-C)","td"),
         P(f"−{area_ndvi:.4f}","td_c"),
         P(f"{area_ndvi/max(area_tot,1)*100:.1f}%","td_c")],
        [P("− Construcciones (A2-B)","td"),
         P(f"−{area_const:.4f}","td_c"),
         P(f"{area_const/max(area_tot,1)*100:.1f}%","td_c")],
        [P(f"  ↳ Solapamiento evitado","td_sm"),
         P(f"+{solapamiento:.4f}","td_c"),
         P(f"+{solapamiento/max(area_tot,1)*100:.1f}%","td_c")],
        [P("✅ Área efectiva cultivable","td"),
         P(f"{area_ef:.4f}","td_c"),
         P(f"{pct_ef:.1f}%","td_c")],
    ]
    a2_bg = _SEM_BG.get(a2_nivel, C.GREY_BG)
    a2_t = _tbl(
        a2_rows,
        cw=[TW*0.65, TW*0.18, TW*0.17],
        extra_styles=[
            ("BACKGROUND",  (0, 6), (-1, 6), _hex(a2_bg)),
            ("FONTNAME",    (0, 6), (-1, 6), "Helvetica-Bold"),
            ("FONTNAME",    (0, 5), (-1, 5), "Helvetica-Oblique"),
            ("TEXTCOLOR",   (0, 5), (-1, 5), _hex(C.SUBTEXT)),
        ],
    )
    story += [a2_t, SP(0.4), HR()]

    # ── 5 · SECCIÓN B · CONTINUIDAD PRODUCTIVA ───────────────────────────────
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
        b2_fg = _SEM_FG.get(b2_nivel, C.DARK)
        # KPI tabla
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
                bg = C.GREEN_BG if ok else C.RED_BG
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
    story += [SP(0.4), HR()]

    # ── 6 · SECCIÓN D · RIESGO AGROCLIMÁTICO ─────────────────────────────────
    story.append(P("D · Riesgo Agroclimático", "h2"))
    d_bg_g  = _SEM_BG.get(d_nivel, C.GREY_BG)
    d_fg_g  = _SEM_FG.get(d_nivel, C.DARK)
    ps_dscore = _sty("ds", fontName="Helvetica-Bold", fontSize=12,
                     alignment=TA_CENTER, textColor=_hex(d_fg_g))
    ps_dlbl   = _sty("dl2", fontSize=8.5, alignment=TA_CENTER,
                     textColor=_hex(d_fg_g))
    d_head_t  = Table(
        [[Paragraph(f"{d_label}  |  Score P80: {d_score:.2f}" if d_score else d_label,
                    ps_dscore)],
         [Paragraph("Score global = media del peor indicador por categoría de riesgo", ps_dlbl)]],
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
        # Top riesgo por categoría (solo score > 0)
        _COLOR_BG_D  = {
            "verde":"#d1fae5","amarillo":"#dcfce7","naranja":"#fef9c3",
            "rojo":"#fed7aa","granate":"#fee2e2","gris":"#f8fafc",
        }
        dh = [P("Categoría","th"), P("Indicador (P80)","th"),
              P("Valor medio","th"), P("Valor P80","th"), P("Score","th")]
        drows_html = [dh]
        for cat in df_risk["Categoría_riesgo"].unique():
            df_cat = df_risk[df_risk["Categoría_riesgo"] == cat]
            worst  = df_cat.loc[df_cat["score_p80"].idxmax()]
            score_v = worst.get("score_p80")
            if score_v is None or score_v < 0.05:
                continue
            color = worst.get("riesgo_color", "gris")
            bg = _COLOR_BG_D.get(color, C.GREY_BG)
            ps_cat = _sty("dcat", fontSize=8, fontName="Helvetica-Bold",
                          textColor=_hex(_SEM_FG.get(
                              "verde" if color in ("verde","amarillo") else
                              "amarillo" if color == "naranja" else "rojo", C.DARK)))
            drows_html.append([
                Paragraph(cat, ps_cat),
                P(str(worst.get("Nombre_indicador","—")), "td"),
                P(f"{worst.get('valor_medio',0):.1f} {worst.get('Unidad','')}", "td_c"),
                P(f"{worst.get('valor_p80',0):.1f} {worst.get('Unidad','')}", "td_c"),
                P(f"{score_v:.2f}", "td_c"),
            ])
        if len(drows_html) > 1:
            d_t = _tbl(drows_html, cw=[TW*0.22, TW*0.30, TW*0.16, TW*0.16, TW*0.16])
            story.append(d_t)
        else:
            story.append(P("Sin indicadores de riesgo con score > 0.05.", "small"))
    else:
        story.append(P("Riesgo agroclimático no calculado.", "small"))

    # Serie climática mensual
    precip  = datos.get("precip_mensual", [])
    tmax    = datos.get("temp_max_mensual", [])
    tmin    = datos.get("temp_min_mensual", [])
    if precip and tmax and tmin:
        story += [SP(0.3), P("Serie Climática Mensual (promedio histórico)", "h3")]
        ch = [P(m, "th") for m in ["Mes"] + MESES_ES]
        pr_row = [P("Precip. (mm)", "td")] + [
            P(str(precip[i]) if i < len(precip) else "—", "td_c") for i in range(12)]
        tx_row = [P("T máx (°C)", "td")] + [
            P(str(tmax[i]) if i < len(tmax) else "—", "td_c") for i in range(12)]
        tn_row = [P("T mín (°C)", "td")] + [
            P(str(tmin[i]) if i < len(tmin) else "—", "td_c") for i in range(12)]
        clim_t = _tbl([ch, pr_row, tx_row, tn_row],
                      cw=[TW*0.12] + [TW*0.88/12]*12,
                      row_bgs=[C.LIGHT, C.WHITE])
        story.append(clim_t)

    story += [SP(0.4), HR()]

    # ── 7 · SECCIÓN C · INFRAESTRUCTURA + APTITUD ────────────────────────────
    story.append(P("C · Infraestructura Productiva", "h2"))
    c1_dist = infra_cu.get("distancia_km", "—") if infra_cu else "—"
    c1_nom  = infra_cu.get("nombre", "—")       if infra_cu else "—"
    c1_dur  = infra_cu.get("duracion_min", "—")  if infra_cu else "—"
    c2_dist = infra_via.get("distancia_m", "—")  if infra_via else "—"
    c2_nom  = infra_via.get("nombre", "—")       if infra_via else "—"
    c2_tipo = infra_via.get("tipo", "—")         if infra_via else "—"
    c_rows  = [
        [P("Indicador","th"), P("Resultado","th"), P("Umbral","th"), P("Score","th")],
        [P("Distancia al centro urbano más cercano","td"),
         P(f"{c1_dist} km por carretera · {c1_nom} ({c1_dur} min)", "td"),
         P("< 10 km verde · 10–25 km amarillo · > 25 km rojo", "td_sm"),
         P(_SEM_EMO.get(
             "verde" if infra_cu and infra_cu.get("distancia_km",99) < 10 else
             "naranja" if infra_cu and infra_cu.get("distancia_km",99) < 25 else "rojo",
             "⚪"), "td_c")],
        [P("Distancia a vía transitable más cercana","td"),
         P(f"{c2_dist} m · {c2_nom} ({c2_tipo})", "td"),
         P("< 500 m verde · 500 m–2 km amarillo · > 2 km rojo", "td_sm"),
         P(_SEM_EMO.get(
             "verde" if infra_via and infra_via.get("distancia_m",9999) < 500 else
             "naranja" if infra_via and infra_via.get("distancia_m",9999) < 2000 else "rojo",
             "⚪"), "td_c")],
    ]
    c_t = _tbl(c_rows, cw=[TW*0.35, TW*0.35, TW*0.22, TW*0.08])
    story += [c_t, SP(0.4), HR()]

    # ── 8 · DOCUMENTACIÓN ADICIONAL REQUERIDA ────────────────────────────────
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
            bg = C.AMBER_BG if _worst in ("naranja","amarillo") else C.RED_BG
            doc_rows.append([P(str(i+1),"td_c"), P(itm,"td")])
        doc_t = _tbl(doc_rows, cw=[TW*0.08, TW*0.92])
        story.append(doc_t)
    else:
        story.append(P("✅ Ninguna documentación adicional requerida — todos los indicadores en verde.", "body"))

    story += [SP(0.5), HR()]

    # ── 9 · FIRMAS ────────────────────────────────────────────────────────────
    story.append(P("Aprobación y Firmas", "h2"))
    firma_t = Table(
        [
            [P("<b>Analista de Crédito</b>","td_c"),
             P("<b>Responsable de Riesgo</b>","td_c"),
             P("<b>Gerente de Área</b>","td_c")],
            [P(" ","td_c"), P(" ","td_c"), P(" ","td_c")],
            [P(" ","td_c"), P(" ","td_c"), P(" ","td_c")],
            [P("Nombre: ___________________","small"),
             P("Nombre: ___________________","small"),
             P("Nombre: ___________________","small")],
            [P("Fecha:  ___________________","small"),
             P("Fecha:  ___________________","small"),
             P("Fecha:  ___________________","small")],
        ],
        colWidths=[TW/3]*3,
        rowHeights=[0.5*cm, 0.4*cm, 1.4*cm, 0.45*cm, 0.45*cm],
    )
    firma_t.setStyle(TableStyle([
        ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
        ("BACKGROUND",    (0,0), (-1,0),  _hex(C.LIGHT)),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("LINEABOVE",     (0,2), (-1,2),  1.2, _hex(C.DARK)),
    ]))
    story += [firma_t, SP(0.4)]

    # ── 10 · NOTA LEGAL ───────────────────────────────────────────────────────
    story.append(P(
        "Este reporte es generado automáticamente por AgroCredito a partir de datos satelitales, "
        "catastrales y climáticos de acceso público. No constituye dictamen definitivo. "
        "El analista de crédito deberá validar cualquier condición condicional o negativa "
        "mediante visita en campo o documentación adicional. "
        f"Fuentes: Sentinel-2 L2A (Element84 / EOSDA), ERA5 (Open-Meteo), IGAC, UPRA (datos.gov.co), OSM.",
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
        analisis: dict con resultados calculados (A1, A2, B1, B2, C, D, áreas)
        fmt:      "pdf" (otros formatos en versión futura)

    Returns:
        bytes listos para st.download_button
    """
    return _build_pdf(datos, predio, analisis)
