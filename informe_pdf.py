#!/usr/bin/env python3
"""Arma el PDF del informe diario: el mismo contenido de mercado, sin nada del monitor.

PARA QUÉ. El mail está escrito para quien mantiene el monitor: dice qué instrumentos vencen y salen
del universo, de qué solapa sale cada cuenta, cómo se piden los duales. Ese PDF se comparte con
colegas a los que nada de eso les sirve. Acá va sólo el mercado: variaciones, curvas, tasas, macro y
los comentarios.

QUÉ SE SACA, en concreto:
  · vencimientos y altas/bajas del universo del monitor
  · referencias a las solapas y a cómo el monitor valúa cada familia
  · el detalle de qué le pide el script a cada fuente
  · las advertencias sobre convenciones de la API
Lo que NO se saca: las aclaraciones metodológicas que cambian cómo se lee un número —mediana en vez
de promedio, la moneda de cada familia, el rezago de las series del BCRA—. Sin eso el lector saca
conclusiones equivocadas, y eso no es una particularidad del monitor sino del dato.

NO LLEVA EL DISCLAIMER INSTITUCIONAL de Balanz a propósito. El texto legal de la casa empieza con
"ha sido preparada por Balanz Capital Valores S.A.U.", y esto es un resumen propio, no una pieza de
research oficial: ponérselo lo haría pasar por lo que no es. Lleva la identidad visual y una nota
neutra. Si alguna vez va a circular como pieza institucional, el disclaimer verbatim está en el
skill balanz-design (assets/disclaimers.md) y hay que agregarlo con el visto bueno de la casa.
"""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, Paragraph, Spacer, Table, TableStyle, PageTemplate)

NAVY = colors.HexColor("#002060")
CYAN = colors.HexColor("#00B0F0")
GRIS = colors.HexColor("#6B7280")
BORDE = colors.HexColor("#C8D3E0")
VERDE = colors.HexColor("#137333")
ROJO = colors.HexColor("#C5221F")
SUAVE = colors.HexColor("#E8EAED")

FUENTES = (r"C:\Users\Usuario\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin"
           r"\b8cdd89c-febc-4430-ac7d-eb55f2fb0c82\032bf20c-fc9a-48c9-80cf-4f4994992f7e"
           r"\skills\balanz-design\assets\fonts")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

ORDEN = ["LECAPs y tasa fija", "CER", "TAMAR", "Duales", "Dólar linked", "Bonares",
         "Globales", "BOPREALes", "ONs ley local", "ONs ley NY", "Subsoberanos"]


def _fuentes():
    """Registra Open Sans. Si no está, reportlab cae a Helvetica y el PDF sale igual."""
    try:
        for nombre, arch in [("OpenSans", "OpenSans-Regular.ttf"),
                             ("OpenSans-Bold", "OpenSans-Bold.ttf"),
                             ("OpenSans-Semi", "OpenSans-SemiBold.ttf")]:
            pdfmetrics.registerFont(TTFont(nombre, str(Path(FUENTES) / arch)))
        pdfmetrics.registerFontFamily("OpenSans", normal="OpenSans", bold="OpenSans-Bold")
        return "OpenSans", "OpenSans-Bold", "OpenSans-Semi"
    except Exception as e:                                        # noqa: BLE001
        print(f"AVISO: sin Open Sans ({e}); se usa Helvetica")
        return "Helvetica", "Helvetica-Bold", "Helvetica-Bold"


REG, BOLD, SEMI = _fuentes()


def num(v, dec=2, signo=False):
    if v is None:
        return "—"
    return (f"{v:+.{dec}f}" if signo else f"{v:.{dec}f}").replace(".", ",")


def miles(v, dec=0):
    return f"{v:,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fecha_larga(iso):
    import datetime as dt
    f = dt.date.fromisoformat(iso)
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]} de {f.year}"


# ── estilos ──────────────────────────────────────────────────────────────────
P = ParagraphStyle("p", fontName=REG, fontSize=9, leading=13.5, textColor=colors.HexColor("#202124"),
                   alignment=TA_JUSTIFY, spaceAfter=7)
P_CHICO = ParagraphStyle("pc", parent=P, fontSize=7.6, leading=11, textColor=GRIS, spaceAfter=4)
H2 = ParagraphStyle("h2", fontName=SEMI, fontSize=12, leading=15, textColor=NAVY,
                    spaceBefore=13, spaceAfter=6)
PIE_FIG = ParagraphStyle("pf", parent=P_CHICO, alignment=0, spaceBefore=2, spaceAfter=10)
LEDE = ParagraphStyle("lede", parent=P, fontSize=11, leading=16.5, spaceAfter=12)
CELDA = ParagraphStyle("cel", fontName=REG, fontSize=7.8, leading=10,
                       textColor=colors.HexColor("#202124"))


def _color_num(v):
    return VERDE if (v or 0) > 0 else (ROJO if (v or 0) < 0 else GRIS)


def tabla_familias(resumen, ancho):
    """Variaciones por familia. Sin la columna de conteo por moneda: al lector externo le importa
    en qué moneda se lee la TIR, no cuántos instrumentos hay de cada punta."""
    grupo = ["", "", "En el día", "", "En la semana", "", "", ""]
    cab = ["Familia", "N", "Precio", "Tasa (pp)", "Precio", "Tasa (pp)", "Nivel", "Mon."]
    filas = [grupo, cab]
    estilos = [("SPAN", (2, 0), (3, 0)), ("SPAN", (4, 0), (5, 0)),
               ("FONT", (0, 0), (-1, 0), SEMI, 6.5),
               ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
               ("ALIGN", (2, 0), (5, 0), "CENTER"),
               ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
               ("FONT", (0, 1), (-1, 1), SEMI, 7),
               ("TEXTCOLOR", (0, 1), (-1, 1), GRIS),
               ("LINEBELOW", (0, 1), (-1, 1), .8, BORDE)]
    for fam in ORDEN:
        r = resumen.get(fam)
        if not r:
            continue
        sem = r.get("semanal") or {}
        sp = (sem.get("precio") or {}).get("mediana")
        st = (sem.get("tasa") or {}).get("mediana")
        monedas = list(r.get("monedas", {}))
        mon = "/".join(m.upper() for m in monedas) if monedas else "—"
        i = len(filas)
        filas.append([fam, str(r["instrumentos"]),
                      num(r["precio"]["mediana"], 2, True) + "%",
                      num(r["tasa"]["mediana"], 2, True),
                      (num(sp, 2, True) + "%") if sp is not None else "—",
                      num(st, 2, True) if st is not None else "—",
                      f'{num(r["teaMediana"]["mediana"])}%  {r["metrica"]}', mon])
        for col, val in ((2, r["precio"]["mediana"]), (3, r["tasa"]["mediana"]),
                         (4, sp), (5, st)):
            if val is not None:
                estilos.append(("TEXTCOLOR", (col, i), (col, i), _color_num(val)))

    anchos = [w * ancho for w in (.28, .05, .105, .095, .105, .095, .19, .08)]
    t = Table(filas, colWidths=anchos, repeatRows=2)
    t.setStyle(TableStyle([
        ("FONT", (0, 2), (-1, -1), REG, 7.8),
        ("ALIGN", (1, 1), (6, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEAFTER", (3, 1), (3, -1), .5, SUAVE),
        ("LINEAFTER", (5, 1), (5, -1), .5, SUAVE),
        ("TEXTCOLOR", (1, 2), (1, -1), GRIS),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (7, 0), (7, -1), 11),
    ] + estilos))
    return t


def tabla_macro(macro, ancho):
    S = macro["series"]
    rp = macro["riesgoPais"]
    filas = [["Serie", "Valor", "", "Día", "Semana", "Al día"]]
    estilos = []

    def agregar(etiqueta, r, unidad, dec=2, monto=False):
        i = len(filas)
        v = r["valor"]
        val = (miles(v) if abs(v) >= 1000 else num(v, 1)) if monto else num(v, dec)
        var = r.get("variacion")
        cd = "—" if var is None else ((miles(var, 1) if abs(var) >= 1000 else num(var, 1, True))
                                      if monto else num(var, dec, True))
        w = r.get("semanal") or {}
        if not w:
            cs = "—"
        elif w.get("clase") == "flujo":
            a = w["acumulado"]
            cs = ((miles(a, 1) if abs(a) >= 1000 else num(a, 1, True))
                  + f"  ·{w['ruedas']}r")
            estilos.append(("TEXTCOLOR", (4, i), (4, i), _color_num(a)))
        else:
            vw = w["variacion"]
            cs = (miles(vw, 1) if abs(vw) >= 1000 else num(vw, 1 if monto else dec, True))
            estilos.append(("TEXTCOLOR", (4, i), (4, i), _color_num(vw)))
        if var is not None:
            estilos.append(("TEXTCOLOR", (3, i), (3, i), _color_num(var)))
        filas.append([etiqueta, val, unidad, cd, cs, f"{r['fecha'][8:10]}/{r['fecha'][5:7]}"])

    # El riesgo país mejora cuando BAJA: el color se invierte a mano respecto del resto.
    i = len(filas)
    w = rp.get("semanal") or {}
    filas.append(["Riesgo país · EMBI+", f"{rp['valor']:.0f}", "bps", num(rp["variacion"], 0, True),
                  num(w.get("variacion"), 0, True) if w else "—",
                  f"{rp['fecha'][8:10]}/{rp['fecha'][5:7]}"])
    estilos.append(("TEXTCOLOR", (3, i), (3, i), VERDE if rp["variacion"] < 0 else ROJO))
    if w:
        estilos.append(("TEXTCOLOR", (4, i), (4, i),
                        VERDE if w.get("variacion", 0) < 0 else ROJO))

    for et, cl, un, kw in [("TAMAR bancos privados", "tamarTEA", "TEA", {}),
                           ("BADLAR bancos privados", "badlarTEA", "TEA", {}),
                           ("Plazo fijo 30 días", "plazoFijo30", "TNA", {}),
                           ("Caución 1 día · pases entre terceros", "pasesTerceros", "TNA", {}),
                           ("BAIBAR · entre bancos privados", "baibar", "TNA", {}),
                           ("Entre entidades financieras", "interbancario", "TNA", {}),
                           ("Compra de divisas del BCRA", "comprasMLC", "M USD", {"monto": True}),
                           ("Reservas internacionales", "reservas", "M USD", {"monto": True})]:
        if cl in S:
            agregar(et, S[cl], un, **kw)

    anchos = [w * ancho for w in (.35, .13, .09, .13, .17, .13)]
    t = Table(filas, colWidths=anchos, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), SEMI, 7),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("LINEBELOW", (0, 0), (-1, 0), .8, BORDE),
        ("FONT", (0, 1), (-1, -1), REG, 7.8),
        ("FONT", (1, 1), (1, -1), SEMI, 7.8),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("TEXTCOLOR", (2, 1), (2, -1), GRIS),
        ("TEXTCOLOR", (5, 1), (5, -1), GRIS),
        ("LINEAFTER", (3, 0), (3, -1), .5, SUAVE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ] + estilos))
    return t


def tabla_simple(filas, anchos_rel, ancho, alinear_der=()):
    t = Table(filas, colWidths=[w * ancho for w in anchos_rel], repeatRows=1)
    est = [("FONT", (0, 0), (-1, 0), SEMI, 7),
           ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
           ("LINEBELOW", (0, 0), (-1, 0), .8, BORDE),
           ("FONT", (0, 1), (-1, -1), REG, 7.8),
           ("TOPPADDING", (0, 0), (-1, -1), 3.5),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
           ("LEFTPADDING", (0, 0), (-1, -1), 3)]
    for c in alinear_der:
        est.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(est))
    return t


def figura(ruta, pie, ancho):
    """Una curva con su pie. KeepTogether evita que el pie quede huérfano en la página siguiente."""
    p = Path(ruta)
    if not p.exists():
        return []
    from PIL import Image as PILImage
    w, h = PILImage.open(p).size
    img = Image(str(p), width=ancho, height=ancho * h / w)
    return [KeepTogether([img, Paragraph(pie, PIE_FIG)])]


def construir(ruta_json, dir_curvas, textos, salida):
    d = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
    fecha = d["fecha"]
    dir_curvas = Path(dir_curvas)

    MARGEN = 16 * mm
    ANCHO = A4[0] - 2 * MARGEN

    def portada(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 30 * mm, A4[0], 30 * mm, stroke=0, fill=1)
        canvas.setFillColor(CYAN)
        canvas.setFont(SEMI, 7.5)
        canvas.drawString(MARGEN, A4[1] - 12 * mm, "RENTA FIJA ARGENTINA")
        canvas.setFillColor(colors.white)
        canvas.setFont(BOLD, 15)
        canvas.drawString(MARGEN, A4[1] - 19.5 * mm, "Cierre de mercado")
        canvas.setFont(REG, 9.5)
        canvas.setFillColor(colors.HexColor("#C8D7EE"))
        canvas.drawString(MARGEN, A4[1] - 25.5 * mm, _fecha_larga(fecha).capitalize())
        pie(canvas, doc)
        canvas.restoreState()

    def interior(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDE)
        canvas.setLineWidth(.6)
        canvas.line(MARGEN, A4[1] - 13 * mm, A4[0] - MARGEN, A4[1] - 13 * mm)
        canvas.setFillColor(GRIS)
        canvas.setFont(REG, 7.5)
        canvas.drawString(MARGEN, A4[1] - 11.5 * mm, "Renta fija Argentina · cierre de mercado")
        canvas.drawRightString(A4[0] - MARGEN, A4[1] - 11.5 * mm,
                               f"{fecha[8:10]}/{fecha[5:7]}/{fecha[:4]}")
        pie(canvas, doc)
        canvas.restoreState()

    def pie(canvas, doc):
        canvas.setFillColor(GRIS)
        canvas.setFont(REG, 7)
        canvas.drawString(MARGEN, 10 * mm,
                          "Elaborado con datos públicos de mercado. No constituye asesoramiento "
                          "de inversión ni recomendación de compra o venta.")
        canvas.drawRightString(A4[0] - MARGEN, 10 * mm, str(canvas.getPageNumber()))

    doc = BaseDocTemplate(str(salida), pagesize=A4, leftMargin=MARGEN, rightMargin=MARGEN,
                          topMargin=MARGEN, bottomMargin=MARGEN,
                          title=f"Renta fija Argentina · cierre {fecha}",
                          author="", subject="Cierre de mercado de renta fija argentina")
    marco_p = Frame(MARGEN, MARGEN + 4 * mm, ANCHO, A4[1] - 34 * mm - MARGEN, id="p")
    marco_i = Frame(MARGEN, MARGEN + 4 * mm, ANCHO, A4[1] - 18 * mm - MARGEN, id="i")
    doc.addPageTemplates([PageTemplate(id="portada", frames=[marco_p], onPage=portada),
                          PageTemplate(id="interior", frames=[marco_i], onPage=interior)])

    E = [NextPageTemplate("interior")]
    E.append(Paragraph(textos["resumen"], LEDE))
    E.append(Spacer(1, 4))

    E.append(Paragraph("Variaciones por familia", H2))
    E.append(tabla_familias(d["resumen"], ANCHO))
    E.append(Spacer(1, 4))
    ref = d.get("referencias", {}).get("semanal")
    nota_ref = (f"La semana se mide contra el cierre del {ref[8:10]}/{ref[5:7]}. " if ref else "")
    E.append(Paragraph(
        nota_ref + "Las columnas de precio y tasa son <b>medianas</b>, no promedios: el movimiento "
        "del instrumento típico de cada familia, sin ponderar por volumen ni por circulante. En "
        "toda familia hay algún ilíquido que no opera en varias ruedas y salta cuando por fin "
        "cruza; con promedio ese único dato define el signo de la familia entera. La columna "
        "<b>Mon.</b> es la moneda en la que se valúa cada familia: dos TIR en monedas distintas no "
        "forman un spread, porque parte de la diferencia es canje. Por eso las comparaciones entre "
        "legislaciones que siguen están todas llevadas a MEP.", P_CHICO))

    E.append(PageBreak())
    E.append(Paragraph("La curva de pesos", H2))
    for t in textos["pesos"]:
        E.append(Paragraph(t, P))
    E += figura(dir_curvas / "lecaps_tem.png",
                "Curva de tasa fija en pesos, en tasa efectiva mensual.", ANCHO)
    E += figura(dir_curvas / "cer.png",
                "Rendimiento real por duration. Los rombos son la pata CER de cada dual.", ANCHO)

    E.append(Paragraph("Tasa fija contra CER: la inflación implícita", H2))
    for t in textos["breakeven"]:
        E.append(Paragraph(t, P))
    if textos.get("tabla_breakeven"):
        E.append(tabla_simple(textos["tabla_breakeven"], (.16, .14, .2, .25, .25), ANCHO,
                              alinear_der=(1, 2, 3, 4)))
        E.append(Spacer(1, 6))
    E += figura(dir_curvas / "lecaps_cer.png",
                "Las dos curvas sobre el tramo que comparten, cada una en su escala, y abajo la "
                "inflación que las iguala.", ANCHO)
    E += figura(dir_curvas / "tamar.png",
                "Curva TAMAR, con la pata TAMAR de los duales y la TAMAR spot de bancos privados.",
                ANCHO)

    E.append(Paragraph("Soberanos en dólares", H2))
    for t in textos["dolares"]:
        E.append(Paragraph(t, P))
    if textos.get("tabla_legislacion"):
        E.append(tabla_simple(textos["tabla_legislacion"], (.2, .2, .22, .19, .19), ANCHO,
                              alinear_der=(1, 2, 3, 4)))
        E.append(Spacer(1, 6))
    E += figura(dir_curvas / "globales_bonares.png",
                "Curva soberana en dólares por legislación, con las dos patas en la misma moneda.",
                ANCHO)

    E.append(Paragraph("BOPREALes y la rotación a Bonares", H2))
    for t in textos["bopreal"]:
        E.append(Paragraph(t, P))
    if textos.get("tabla_bopreal"):
        E.append(tabla_simple(textos["tabla_bopreal"], (.16, .13, .13, .16, .13, .13, .16), ANCHO,
                              alinear_der=(1, 2, 3, 4, 5, 6)))
        E.append(Spacer(1, 6))

    E.append(Paragraph("Dólar linked y futuros", H2))
    for t in textos["dolar_linked"]:
        E.append(Paragraph(t, P))
    E += figura(dir_curvas / "dl.png", "Curva dólar linked: devaluación implícita por vencimiento.",
                ANCHO)
    E += figura(dir_curvas / "futuros.png",
                "Precio de cada contrato y, en el eje derecho, la devaluación acumulada que "
                "implica. Los círculos huecos son contratos de volumen fino.", ANCHO)

    E.append(Paragraph("Subsoberanos", H2))
    for t in textos["subsoberanos"]:
        E.append(Paragraph(t, P))
    E += figura(dir_curvas / "subsoberanos.png",
                "Provinciales y municipales en CCL, ordenados por duration.", ANCHO)

    E.append(KeepTogether([Paragraph("Dinero, tasas y macro", H2),
                           tabla_macro(d["macro"], ANCHO)]))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        "<b>Las series no son todas del mismo día</b>, por eso la columna «Al día»: el BCRA publica "
        "las tasas con dos días hábiles de rezago, y reservas y compra de divisas con tres o "
        "cuatro. No se leen contra el movimiento de los bonos del día como si fueran simultáneas. "
        "<b>Las tasas y las reservas informan cuánto cambiaron; la compra de divisas, cuánto se "
        "acumuló</b> —el sufijo «·Nr» son las ruedas que entraron en el período—. La caución es la "
        "de pases entre terceros a un día que publica el BCRA; no es la caución bursátil, que se "
        "opera en BYMA y en MAE y corre bastante por encima.", P_CHICO))

    for t in textos["macro"]:
        E.append(Paragraph(t, P))

    E.append(PageBreak())
    E.append(Paragraph("Cierre semanal", H2))
    for t in textos["semanal"]:
        E.append(Paragraph(t, P))

    E.append(Spacer(1, 8))
    E.append(Paragraph(textos["fuentes"], P_CHICO))

    doc.build(E)
    return str(salida)


if __name__ == "__main__":
    print("Se usa desde el informe diario; no tiene modo directo.", file=sys.stderr)
