"""
utils/report_generator.py
Generación del reporte ex-ante en PDF ejecutivo.

Dependencias (añadir a requirements.txt):
    reportlab>=4.0

Función principal:
    generate_exante_report(datos, predio, fmt="pdf") -> bytes
"""

from __future__ import annotations
from io import BytesIO
from datetime import date
from typing import Literal


# ── Paleta corporativa ────────────────────────────────────────────────────────
class C:
    DARK     = "#1e293b"
    MID      = "#334155"
    LIGHT    = "#f8fafc"
    BORDER   = "#e2e8f0"
    GREEN    = "#059669"
    GREEN_BG = "#d1fae5"
    ORANGE   = "#d97706"
    ORANGE_BG= "#fef3c7"
    RED      = "#dc2626"
    RED_BG   = "#fee2e2"
    WHITE    = "#ffffff"
    SUBTEXT  = "#64748b"
    ACCENT   = "#3b82f6"

def _hex(h: str):
    from reportlab.lib import colors
    return colors.HexColor(h)


# ── Helpers ───────────────────────────────────────────────────────────────────

MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

def _nivel_frontera(estado: str) -> tuple[str, str]:
    return {
        "verde":   ("✅ Apto",        "Continuar a Fase 2."),
        "naranja": ("⚠️ Condicional", "Validación manual / verificación en campo requerida."),
        "rojo":    ("🔴 No apto",     "Proceso termina. No elegible."),
    }.get(estado, ("—", "—"))

def _riesgo_label(r: str) -> str:
    return {"Nulo":"🟢 Nulo","Bajo":"🟢 Bajo","Medio":"🟡 Medio",
            "Alto":"🔴 Alto","Muy Alto":"🔴 Muy Alto"}.get(r, r)

def _riesgo_color(r: str) -> tuple[str,str]:
    """(bg_hex, fg_hex)"""
    if r in ("Nulo","Bajo"):   return C.GREEN_BG,  C.GREEN
    if r == "Medio":           return C.ORANGE_BG, C.ORANGE
    return C.RED_BG, C.RED

def _dictamen_global(datos: dict) -> tuple[str, str, str, str]:
    """(label, descripción, bg_hex, fg_hex)"""
    estados = [datos.get("frontera_estado","verde")]
    ndvi_ok = datos.get("ndvi_promedio_3a",1) >= datos.get("ndvi_umbral",0.4)
    if not ndvi_ok: estados.append("naranja")
    if datos.get("riesgo_global","Bajo") == "Alto": estados.append("naranja")

    if "rojo" in estados:
        return ("NO APTO",
                "El predio no cumple los requisitos mínimos de elegibilidad.",
                C.RED_BG, C.RED)
    if "naranja" in estados:
        return ("CONDICIONAL",
                "El predio requiere verificación adicional antes de continuar.",
                C.ORANGE_BG, C.ORANGE)
    return ("APTO",
            "El predio cumple todos los criterios de elegibilidad ex-ante.",
            C.GREEN_BG, C.GREEN)

DECISIONES_RIESGO = {
    "Nulo":     "Sin restricción.",
    "Bajo":     "Sin restricción.",
    "Medio":    "Incluir cláusula de seguimiento trimestral.",
    "Alto":     "Seguro agrícola obligatorio.",
    "Muy Alto": "Evaluar viabilidad del proyecto.",
}


# ══════════════════════════════════════════════════════════════════════════════
#  PDF EJECUTIVO
# ══════════════════════════════════════════════════════════════════════════════

def _build_pdf(datos: dict, predio: dict | None, scoring: dict | None = None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether, PageBreak,
    )
    from reportlab.platypus.flowables import HRFlowable

    W, H = A4
    buf = BytesIO()

    # ── Canvas callbacks para header/footer en cada página ────────────────
    def _on_page(canvas, doc):
        canvas.saveState()
        # Barra superior
        canvas.setFillColor(_hex(C.DARK))
        canvas.rect(0, H - 1.2*cm, W, 1.2*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE))
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(1.8*cm, H - 0.85*cm, "🌿 AgroCredito · Plataforma de Evaluación de Predios")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(W - 1.8*cm, H - 0.85*cm,
                               f"Reporte Ex-Ante · {date.today().strftime('%d/%m/%Y')}")
        # Barra inferior
        canvas.setFillColor(_hex(C.MID))
        canvas.rect(0, 0, W, 0.9*cm, fill=1, stroke=0)
        canvas.setFillColor(_hex(C.WHITE))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(1.8*cm, 0.32*cm,
                          "Confidencial · Uso interno · Generado automáticamente")
        canvas.drawRightString(W - 1.8*cm, 0.32*cm, f"Pág. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.8*cm, bottomMargin=1.6*cm,
        onFirstPage=_on_page, onLaterPages=_on_page,
    )

    # ── Estilos de texto ──────────────────────────────────────────────────
    def sty(name, **kw):
        defaults = dict(fontName="Helvetica", fontSize=9,
                        textColor=_hex(C.DARK), leading=13)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    S = {
        "title":   sty("title",   fontName="Helvetica-Bold", fontSize=20,
                        textColor=_hex(C.DARK),   spaceAfter=2),
        "sub":     sty("sub",     fontSize=9, textColor=_hex(C.SUBTEXT)),
        "h2":      sty("h2",      fontName="Helvetica-Bold", fontSize=11,
                        textColor=_hex(C.DARK),   spaceBefore=10, spaceAfter=3),
        "body":    sty("body"),
        "small":   sty("small",   fontSize=8, textColor=_hex(C.SUBTEXT),
                        fontName="Helvetica-Oblique"),
        "center":  sty("center",  alignment=TA_CENTER),
        "th":      sty("th",      fontName="Helvetica-Bold", fontSize=8.5,
                        textColor=_hex(C.WHITE),  alignment=TA_CENTER),
        "td":      sty("td",      fontSize=8.5, leading=12),
        "td_c":    sty("td_c",    fontSize=8.5, alignment=TA_CENTER, leading=12),
        "decision":sty("decision",fontSize=8.5, textColor=_hex(C.MID),
                        fontName="Helvetica-Oblique", leading=12),
    }

    def P(txt, style="body"):  return Paragraph(str(txt), S[style])
    def SP(h=0.2):             return Spacer(1, h*cm)
    def HR():
        return HRFlowable(width="100%", thickness=0.5,
                          color=_hex(C.BORDER), spaceAfter=4, spaceBefore=4)

    # ── Helper: tabla de sección ──────────────────────────────────────────
    def section_table(rows_data, col_widths):
        """
        rows_data: list of dicts con keys: indicador, valor, decision, riesgo (opt)
        Cada fila puede tener colores de semáforo si 'riesgo' está presente.
        """
        TW = sum(col_widths)
        header = [P("Indicador","th"), P("Valor / Resultado","th"), P("Criterio de decisión","th")]
        data   = [header]
        styles_extra = []

        for i, r in enumerate(rows_data):
            row_idx = i + 1  # +1 por header
            ind = P(r.get("indicador",""), "td")
            val = P(r.get("valor",""),     "td")
            dec = P(r.get("decision",""), "decision")
            data.append([ind, val, dec])

            # Color de fondo según riesgo o estado
            bg = C.LIGHT if i % 2 == 0 else C.WHITE
            riesgo = r.get("riesgo")
            if riesgo:
                bg, _ = _riesgo_color(riesgo)
            styles_extra += [
                ("BACKGROUND", (0, row_idx), (-1, row_idx), _hex(bg)),
            ]

        t = Table(data, colWidths=col_widths, repeatRows=1)
        base_style = [
            # Encabezado
            ("BACKGROUND",    (0,0), (-1,0), _hex(C.MID)),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 7),
            ("RIGHTPADDING",  (0,0), (-1,-1), 7),
            ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [_hex(C.LIGHT), _hex(C.WHITE)]),
        ]
        t.setStyle(TableStyle(base_style + styles_extra))
        return t

    # ── Helper: caja dictamen ─────────────────────────────────────────────
    def dictamen_box(label, desc, bg, fg):
        inner = Table(
            [[P(f"DICTAMEN: {label}", sty("_d",
               fontName="Helvetica-Bold", fontSize=14,
               textColor=_hex(fg), alignment=TA_CENTER))],
             [P(desc, sty("_dd",
               fontSize=9, textColor=_hex(fg), alignment=TA_CENTER))]],
            colWidths=[W - 3.6*cm],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(bg)),
            ("TOPPADDING",    (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
            ("LEFTPADDING",   (0,0), (-1,-1), 14),
            ("RIGHTPADDING",  (0,0), (-1,-1), 14),
            ("LINEBELOW",     (0,0), (-1,0), 1, _hex(fg)),
        ]))
        return inner

    # ── Helper: KPI row ───────────────────────────────────────────────────
    def kpi_row(items):
        """items: list of (label, value) → fila de métricas en caja."""
        n = len(items)
        w = (W - 3.6*cm) / n
        cells = []
        for label, val in items:
            cells.append(Table(
                [[P(str(val),  sty("_v", fontName="Helvetica-Bold", fontSize=13,
                               alignment=TA_CENTER, textColor=_hex(C.DARK)))],
                 [P(str(label),sty("_l", fontSize=7.5, alignment=TA_CENTER,
                               textColor=_hex(C.SUBTEXT)))]],
                colWidths=[w - 0.4*cm],
            ))
        t = Table([cells], colWidths=[w]*n)
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), _hex(C.LIGHT)),
            ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("TOPPADDING",    (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]))
        return t

    # ══════════════════════════════════════════════════════════════════════
    #  STORY
    # ══════════════════════════════════════════════════════════════════════
    story = []

    # ── 1 · Portada ───────────────────────────────────────────────────────
    story += [
        SP(0.6),
        P("Reporte de Evaluación Ex-Ante", "title"),
        P(f"Evaluación agroclimática y productiva para decisiones de crédito agrícola · Colombia",
          "sub"),
        SP(0.3), HR(), SP(0.2),
    ]

    # Ficha del predio
    cod   = predio.get("codigo","—")         if predio else "Simulado"
    dep   = predio.get("departamento","—")   if predio else datos.get("municipio","—").split(",")[-1].strip()
    area  = predio.get("area_catastral_ha","—") if predio else datos.get("area_total_ha","—")

    ficha = Table(
        [[P("<b>Cultivo</b>","td"),       P(datos.get("cultivo","—").capitalize(),"td"),
          P("<b>Municipio</b>","td"),     P(datos.get("municipio","—"),"td")],
         [P("<b>Departamento</b>","td"),  P(dep,"td"),
          P("<b>Código catastral</b>","td"), P(cod,"td")],
         [P("<b>Área catastral</b>","td"),P(f"{area} ha","td"),
          P("<b>Fecha de análisis</b>","td"), P(date.today().strftime("%d/%m/%Y"),"td")]],
        colWidths=[(W-3.6*cm)/4]*4,
    )
    ficha.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,-1), _hex(C.LIGHT)),
        ("BACKGROUND",    (2,0), (2,-1), _hex(C.LIGHT)),
        ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story += [ficha, SP(0.4)]

    # ── 2 · Dictamen global ───────────────────────────────────────────────
    d_lbl, d_desc, d_bg, d_fg = _dictamen_global(datos)
    story += [dictamen_box(d_lbl, d_desc, d_bg, d_fg), SP(0.5)]

    # ── 3 · KPIs clave ────────────────────────────────────────────────────
    pct = round(datos.get("area_efectiva_ha",0)/max(datos.get("area_total_ha",1),1)*100)
    ndvi_ok = datos.get("ndvi_promedio_3a",0) >= datos.get("ndvi_umbral",0.4)
    story += [
        P("Indicadores Clave del Predio", "h2"),
        kpi_row([
            ("Área efectiva cultivable",  f"{datos.get('area_efectiva_ha','—')} ha  ({pct}%)"),
            ("NDVI promedio 3 años",      round(datos.get("ndvi_promedio_3a",0),2)),
            ("Riesgo agroclimático global",datos.get("riesgo_global","—")),
            ("Construcciones",            f"{datos.get('construcciones_n','—')} unidades"),
            ("Distancia urbana",          f"{datos.get('distancia_urbana_km','—')} km"),
        ]),
        SP(0.5), HR(),
    ]

    # ── 4 · Sección A ─────────────────────────────────────────────────────
    fe_lbl, fe_dec = _nivel_frontera(datos.get("frontera_estado","verde"))
    story += [
        KeepTogether([
            P("A · Validación Geométrica y Legal", "h2"),
            section_table([
                {"indicador": "Frontera agrícola",
                 "valor":    datos.get("frontera_agricola","—"),
                 "decision": fe_dec},
                {"indicador": "Área total del predio",
                 "valor":    f"{datos.get('area_total_ha','—')} ha",
                 "decision": "Base de cálculo."},
                {"indicador": "− Zonas con pendiente > umbral",
                 "valor":    f"{datos.get('area_pendiente_excluida_ha','—')} ha",
                 "decision": "Excluidas del área productiva."},
                {"indicador": "− Zonas con NDVI bajo",
                 "valor":    f"{datos.get('area_ndvi_bajo_ha','—')} ha",
                 "decision": "Excluidas del área productiva."},
                {"indicador": "− Construcciones e infraestructura",
                 "valor":    f"{datos.get('area_construcciones_ha','—')} ha",
                 "decision": "Excluidas del área productiva."},
                {"indicador": "✅ Área efectiva cultivable",
                 "valor":    f"{datos.get('area_efectiva_ha','—')} ha ({pct}%)",
                 "decision": "Área suficiente para el proyecto." if pct >= 70
                             else "Área efectiva reducida — verificar viabilidad."},
            ], col_widths=[(W-3.6*cm)*0.38, (W-3.6*cm)*0.25, (W-3.6*cm)*0.37]),
        ]),
        SP(0.4),
    ]

    # ── 5 · Sección B ─────────────────────────────────────────────────────
    story += [
        KeepTogether([
            P("B · Validación de Continuidad Productiva", "h2"),
            section_table([
                {"indicador": "Aptitud al cultivo",
                 "valor":    datos.get("aptitud_cultivo","—"),
                 "decision": "Continuar evaluación." if datos.get("aptitud_cultivo")=="Alta"
                             else "Revisar alternativas de cultivo."},
                {"indicador": "Valor potencial del predio",
                 "valor":    datos.get("valor_potencial","—"),
                 "decision": "—"},
                {"indicador": "NDVI promedio 3 años",
                 "valor":    str(round(datos.get("ndvi_promedio_3a",0),2)),
                 "decision": "✅ Actividad productiva confirmada." if ndvi_ok
                             else "⚠️ Por debajo del umbral — visita en campo recomendada."},
                {"indicador": "Umbral NDVI mínimo requerido",
                 "valor":    str(datos.get("ndvi_umbral","—")),
                 "decision": "Referencia de actividad productiva mínima."},
            ], col_widths=[(W-3.6*cm)*0.38, (W-3.6*cm)*0.25, (W-3.6*cm)*0.37]),
        ]),
        SP(0.4),
    ]

    # ── 6 · Sección C ─────────────────────────────────────────────────────
    dist = datos.get("distancia_urbana_km",0)
    story += [
        KeepTogether([
            P("C · Validación de Infraestructura Productiva", "h2"),
            section_table([
                {"indicador": "Construcciones identificadas",
                 "valor":    f"{datos.get('construcciones_n','—')} unidades",
                 "decision": "Infraestructura productiva confirmada." if datos.get("construcciones_n",0)>0
                             else "Sin construcciones — verificar en campo."},
                {"indicador": "Detalle de construcciones",
                 "valor":    datos.get("construcciones_desc","—"),
                 "decision": "—"},
                {"indicador": "Distancia a zona urbana",
                 "valor":    f"{dist} km",
                 "decision": "Acceso adecuado a servicios." if dist < 20
                             else "Acceso limitado — considerar logística de la operación."},
            ], col_widths=[(W-3.6*cm)*0.38, (W-3.6*cm)*0.25, (W-3.6*cm)*0.37]),
        ]),
        SP(0.4),
    ]

    # ── 7 · Sección D ─────────────────────────────────────────────────────
    riesgos = [
        ("Déficit hídrico (sequía)",  datos.get("riesgo_sequia","Bajo")),
        ("Exceso de lluvia",          datos.get("riesgo_exceso_lluvia","Bajo")),
        ("Heladas",                   datos.get("riesgo_helada","Bajo")),
        ("Temperatura alta",          datos.get("riesgo_temp_alta","Bajo")),
        ("⚡ Riesgo global",          datos.get("riesgo_global","Bajo")),
    ]
    rows_d = [
        {"indicador": label,
         "valor":    _riesgo_label(nivel),
         "decision": DECISIONES_RIESGO.get(nivel,"—"),
         "riesgo":   nivel}
        for label, nivel in riesgos
    ]
    story += [
        KeepTogether([
            P("D · Análisis de Riesgo Agroclimático", "h2"),
            section_table(rows_d,
                col_widths=[(W-3.6*cm)*0.38, (W-3.6*cm)*0.22, (W-3.6*cm)*0.40]),
        ]),
        SP(0.5), HR(),
    ]

    # ── 8 · Scoring D (si disponible) ────────────────────────────────────
    if scoring:
        from utils.risk_scoring import GRUPOS, SCORE_COLOR, SCORE_TEXT, SCORE_LABEL
        story += [P("D · Scoring de Riesgo Agroclimático — Resultados", "h2")]

        # Resumen por grupo
        grp_cells = []
        for grupo in GRUPOS:
            sc = scoring["por_grupo"].get(grupo, 0)
            grp_cells.append(Table(
                [[P(grupo,     sty("_gn", fontSize=7.5, alignment=TA_CENTER,
                               textColor=_hex(SCORE_TEXT[sc])))],
                 [P(SCORE_LABEL[sc], sty("_gs", fontName="Helvetica-Bold",
                               fontSize=9, alignment=TA_CENTER,
                               textColor=_hex(SCORE_TEXT[sc])))]],
                colWidths=[(W-3.6*cm)/len(GRUPOS)],
            ))
        grp_t = Table([grp_cells], colWidths=[(W-3.6*cm)/len(GRUPOS)]*len(GRUPOS))
        grp_t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), _hex(C.LIGHT)),
            ("GRID",       (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ]))
        story += [grp_t, SP(0.3)]

        # Tabla detallada de los 15 indicadores
        sc_header = [P(h,"th") for h in
                     ["#","Indicador","Valor","Unidad","Score","Decisión"]]
        sc_rows = [sc_header]
        for r in scoring["resultados"]:
            sc_rows.append([
                P(str(r["id"]),   "td_c"),
                P(r["nombre"],    "td"),
                P(str(r["valor"]),"td_c"),
                P(r["unidad"],    "td_c"),
                P(r["score_label"],"td_c"),
                P(r["decision"],  "decision"),
            ])
        cw_sc = [(W-3.6*cm)*f for f in [0.04,0.30,0.09,0.13,0.12,0.32]]
        sc_t = Table(sc_rows, colWidths=cw_sc, repeatRows=1)
        row_styles = [
            ("BACKGROUND",    (0,0), (-1,0),  _hex(C.MID)),
            ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 5),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]
        for i, r in enumerate(scoring["resultados"]):
            row_styles.append(
                ("BACKGROUND", (4, i+1), (4, i+1), _hex(SCORE_COLOR[r["score_num"]]))
            )
        sc_t.setStyle(TableStyle(row_styles))
        story += [sc_t, SP(0.4), HR()]
    else:
        story += [
            P("D · Scoring de Riesgo Agroclimático", "h2"),
            P("Scoring no ejecutado. Lanza el análisis en la app para incluirlo en el PDF.", "small"),
            SP(0.3), HR(),
        ]

    # ── 9 · Serie climática (tabla compacta) ──────────────────────────────
    story.append(P("Serie Climática Mensual (últimos 12 meses)", "h2"))
    clim_header = [P(h,"th") for h in ["Mes","Precip. (mm)","T_máx (°C)","T_mín (°C)","NDVI"]]
    clim_rows   = [clim_header]
    for i, m in enumerate(MESES):
        clim_rows.append([
            P(m, "td_c"),
            P(str(datos["precip_mensual"][i])    if i<len(datos.get("precip_mensual",[]))    else "—","td_c"),
            P(str(datos["temp_max_mensual"][i])  if i<len(datos.get("temp_max_mensual",[]))  else "—","td_c"),
            P(str(datos["temp_min_mensual"][i])  if i<len(datos.get("temp_min_mensual",[]))  else "—","td_c"),
            P(str(round(datos["ndvi_mensual_hist"][i],2)) if i<len(datos.get("ndvi_mensual_hist",[])) else "—","td_c"),
        ])
    cw = (W-3.6*cm)/5
    clim_t = Table(clim_rows, colWidths=[cw]*5, repeatRows=1)
    clim_t.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (-1,0),  _hex(C.MID)),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [_hex(C.LIGHT), _hex(C.WHITE)]),
        ("GRID",           (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
        ("TOPPADDING",     (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 4),
        ("ALIGN",          (0,0), (-1,-1), "CENTER"),
        ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
        ("FONTSIZE",       (0,0), (-1,-1), 8.5),
    ]))
    story += [clim_t, SP(0.5), HR()]

    # ── 9 · Firmas ────────────────────────────────────────────────────────
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
        colWidths=[(W-3.6*cm)/3]*3,
        rowHeights=[0.5*cm, 0.4*cm, 1.4*cm, 0.45*cm, 0.45*cm],
    )
    firma_t.setStyle(TableStyle([
        ("GRID",          (0,0), (-1,-1), 0.4, _hex(C.BORDER)),
        ("BACKGROUND",    (0,0), (-1,0),  _hex(C.LIGHT)),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("LINEABOVE",     (0,2), (-1,2),  1.2, _hex(C.DARK)),  # línea de firma
    ]))
    story += [firma_t, SP(0.5)]

    # ── 10 · Nota legal ───────────────────────────────────────────────────
    story.append(P(
        "Este reporte es generado automáticamente por AgroCredito a partir de datos satelitales, "
        "catastrales y climáticos. No constituye dictamen definitivo. El analista de crédito deberá "
        "validar cualquier condición condicional o negativa mediante visita en campo o documentación adicional.",
        "small",
    ))

    doc.build(story)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  FUNCIÓN PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════

def generate_exante_report(
    datos: dict,
    predio: dict | None,
    fmt: Literal["pdf"] = "pdf",
    scoring: dict | None = None,
) -> bytes:
    """
    Genera el reporte ex-ante en PDF ejecutivo.

    Args:
        datos:   dict del caso de estudio (CASOS_ESTUDIO[caso_sel])
        predio:  dict devuelto por get_predio_por_punto (puede ser None)
        fmt:     "pdf" (otros formatos en versión futura)
        scoring: dict devuelto por score_riesgo() (puede ser None)

    Returns:
        bytes listos para st.download_button
    """
    return _build_pdf(datos, predio, scoring)
