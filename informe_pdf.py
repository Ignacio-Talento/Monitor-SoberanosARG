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
from reportlab.lib.utils import ImageReader
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

MARCA = (r"C:\Users\Usuario\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin"
         r"\b8cdd89c-febc-4430-ac7d-eb55f2fb0c82\032bf20c-fc9a-48c9-80cf-4f4994992f7e"
         r"\skills\balanz-design\assets")
FUENTES = MARCA + r"\fonts"
LOGOS = Path(MARCA) / "logo"
PROPORCION_LOCKUP = 1241 / 142          # el asset oficial, para no deformarlo


def _lockup(canvas, archivo, x_der, y_centro, ancho):
    """Dibuja el lockup alineado a la derecha en x_der y centrado vertical en y_centro."""
    ruta = LOGOS / archivo
    if not ruta.exists():
        return
    alto = ancho / PROPORCION_LOCKUP
    canvas.drawImage(ImageReader(str(ruta)), x_der - ancho, y_centro - alto / 2,
                     width=ancho, height=alto, mask="auto")

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
P = ParagraphStyle("p", fontName=REG, fontSize=11, leading=17,
                   textColor=colors.HexColor("#202124"), alignment=TA_JUSTIFY, spaceAfter=10)
P_CHICO = ParagraphStyle("pc", parent=P, fontSize=9.3, leading=14, textColor=GRIS,
                         spaceAfter=7)
H2 = ParagraphStyle("h2", fontName=SEMI, fontSize=15, leading=19, textColor=NAVY,
                    spaceBefore=19, spaceAfter=9)
PIE_FIG = ParagraphStyle("pf", parent=P_CHICO, alignment=0, spaceBefore=4, spaceAfter=15)
LEDE = ParagraphStyle("lede", parent=P, fontSize=13, leading=20, spaceAfter=16)
CELDA = ParagraphStyle("cel", fontName=REG, fontSize=9.6, leading=12,
                       textColor=colors.HexColor("#202124"))


def _color_num(v):
    return VERDE if (v or 0) > 0 else (ROJO if (v or 0) < 0 else GRIS)


def periodo_de(tipos):
    """Qué período compara este informe: la clave del JSON, el rótulo y el nombre en prosa.

    El mes gana sobre la semana cuando la rueda cierra los dos, que pasa cuando el último hábil del
    mes cae viernes. Devuelve None en un informe puramente diario.
    """
    if "mensual" in (tipos or []):
        return "mensual", "En el mes", "El mes"
    if "semanal" in (tipos or []):
        return "semanal", "En la semana", "La semana"
    return None, "", ""


def tabla_familias(resumen, ancho, periodo="semanal", rotulo="En la semana", columnas="ambas"):
    """Variaciones por familia. Sin la columna de conteo por moneda: al lector externo le importa
    en qué moneda se lee la TIR, no cuántos instrumentos hay de cada punta.

    `columnas` elige la ventana: "dia", "periodo" o "ambas". Con una sola ventana desaparece la
    fila de encabezado agrupador, que sin dos bloques que separar no dice nada.
    """
    dos = columnas == "ambas"
    filas, estilos = [], []
    if dos:
        filas.append(["", "", "En el día", "", rotulo, "", "", ""])
        estilos += [("SPAN", (2, 0), (3, 0)), ("SPAN", (4, 0), (5, 0)),
                    ("FONT", (0, 0), (-1, 0), SEMI, 8),
                    ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
                    ("ALIGN", (2, 0), (5, 0), "CENTER"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 1)]
        cab = ["Familia", "N", "Precio", "Tasa (pp)", "Precio", "Tasa (pp)", "Nivel", "Mon."]
    else:
        cab = ["Familia", "N", "Precio", "Tasa (pp)", "Nivel", "Mon."]
    c0 = 1 if dos else 0                      # fila del encabezado de columnas
    filas.append(cab)
    estilos += [("FONT", (0, c0), (-1, c0), SEMI, 8.8),
                ("TEXTCOLOR", (0, c0), (-1, c0), GRIS),
                ("LINEBELOW", (0, c0), (-1, c0), .8, BORDE)]
    for fam in ORDEN:
        r = resumen.get(fam)
        if not r:
            continue
        sem = r.get(periodo) or {}
        sp = (sem.get("precio") or {}).get("mediana")
        st = (sem.get("tasa") or {}).get("mediana")
        monedas = list(r.get("monedas", {}))
        mon = "/".join(m.upper() for m in monedas) if monedas else "—"
        i = len(filas)
        nivel = f'{num(r["teaMediana"]["mediana"])}%  {r["metrica"]}'
        if dos:
            # N es la del día: la tabla muestra las dos ventanas y la del día es la de referencia.
            filas.append([fam, str(r["instrumentos"]),
                          num(r["precio"]["mediana"], 2, True) + "%",
                          num(r["tasa"]["mediana"], 2, True),
                          (num(sp, 2, True) + "%") if sp is not None else "—",
                          num(st, 2, True) if st is not None else "—", nivel, mon])
            pares = ((2, r["precio"]["mediana"]), (3, r["tasa"]["mediana"]), (4, sp), (5, st))
        elif columnas == "periodo":
            # N es la del PERÍODO: cuántos tenían dato en las DOS puntas. La mediana del mes se
            # calculó sobre esos, no sobre los que operaron hoy, y con paneles ilíquidos la
            # diferencia es grande (4 de 11 subsoberanos en agosto de 2026).
            npe = (sem.get("precio") or {}).get("n")
            filas.append([fam, str(npe) if npe is not None else "—",
                          (num(sp, 2, True) + "%") if sp is not None else "—",
                          num(st, 2, True) if st is not None else "—", nivel, mon])
            pares = ((2, sp), (3, st))
        else:
            filas.append([fam, str(r["instrumentos"]),
                          num(r["precio"]["mediana"], 2, True) + "%",
                          num(r["tasa"]["mediana"], 2, True), nivel, mon])
            pares = ((2, r["precio"]["mediana"]), (3, r["tasa"]["mediana"]))
        for col, val in pares:
            if val is not None:
                estilos.append(("TEXTCOLOR", (col, i), (col, i), _color_num(val)))

    rel = ((.28, .05, .105, .095, .105, .095, .19, .08) if dos
           else (.32, .06, .13, .12, .23, .10))
    anchos = [w * ancho for w in rel]
    t = Table(filas, colWidths=anchos, repeatRows=2 if dos else 1)
    t.setStyle(TableStyle([
        ("FONT", (0, c0 + 1), (-1, -1), REG, 9.6),
        ("ALIGN", (1, c0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TEXTCOLOR", (1, c0 + 1), (1, -1), GRIS),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),

    ] + ([("LINEAFTER", (3, c0), (3, -1), .5, SUAVE),
          ("LINEAFTER", (5, c0), (5, -1), .5, SUAVE)] if dos else [])
      + estilos))
    return t


def tabla_mercado(bloque, ancho, periodo=None, rotulo="", con_dia=True, etiquetas=None):
    """Una fila por serie de un bloque de series_mercado.json, con la misma forma que la tabla
    macro: valor, variación del día, variación del período y la fecha del dato.

    LA FECHA POR FILA NO ES DECORACIÓN. Estos son bonos, no series del BCRA: un dual ilíquido puede
    no haber operado hoy, y entonces su "último" es el de ayer y su "variación del día" compara dos
    ruedas que no son consecutivas. La columna «Al día» es lo que permite ver eso en vez de leer un
    cero como si el margen no se hubiera movido.
    """
    per = bool(periodo)
    filas = [["Serie", "Valor", ""] + (["Día"] if con_dia else [])
             + ([rotulo] if per else []) + ["Al día"]]
    estilos = []
    for tk, r in (bloque.get("series") or {}).items():
        i = len(filas)
        var = r.get("variacion")
        w = r.get(periodo) or {}
        if var is not None and con_dia:
            estilos.append(("TEXTCOLOR", (3, i), (3, i), _color_num(var)))
        if w:
            estilos.append(("TEXTCOLOR", (3 + int(con_dia), i), (3 + int(con_dia), i),
                            _color_num(w.get("variacion"))))
        filas.append([(etiquetas or {}).get(tk, tk), num(r["valor"], 2), "%"]
                     + ([num(var, 2, True)] if con_dia else [])
                     + ([num(w.get("variacion"), 2, True) if w else "—"] if per else [])
                     + [f"{r['fecha'][8:10]}/{r['fecha'][5:7]}"])

    ncols = 4 + int(con_dia) + int(per)
    base = {6: (.35, .13, .09, .13, .17, .13), 5: (.42, .15, .11, .17, .15),
            4: (.48, .17, .12, .23)}
    t = Table(filas, colWidths=[w * ancho for w in base[ncols]], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), SEMI, 8.8),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("LINEBELOW", (0, 0), (-1, 0), .8, BORDE),
        ("FONT", (0, 1), (-1, -1), REG, 9.6),
        ("FONT", (1, 1), (1, -1), SEMI, 9.6),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TEXTCOLOR", (2, 1), (2, -1), GRIS),
        ("TEXTCOLOR", (ncols - 1, 1), (ncols - 1, -1), GRIS),
        ("LINEAFTER", (2 + int(con_dia), 0), (2 + int(con_dia), -1), .5, SUAVE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ] + estilos))
    return t


def _bloque_mercado(d, clave):
    """El bloque pedido de series_mercado.json, o None si el informe salió sin él."""
    m = d.get("mercado") or {}
    return (m.get("bloques") or {}).get(clave) if m.get("disponible") else None


def _nota_atraso(bloque, d):
    """Aviso cuando el bloque no llega al día del informe. Silencio si está al día."""
    if not bloque or not bloque.get("hasta") or bloque["hasta"] >= d["fecha"]:
        return ""
    return (f" <b>Ojo: el último dato es del {bloque['hasta'][8:10]}/{bloque['hasta'][5:7]}, "
            f"no del día del informe</b>, así que la columna del día compara contra una rueda más "
            f"vieja.")


def tabla_macro(macro, ancho, periodo=None, rotulo="", con_dia=True):
    S = macro["series"]
    rp = macro["riesgoPais"]
    # Sin cierre de periodo la columna no se arma: en un diario salia entera de guiones. Y en un
    # informe de SOLO periodo se cae la del dia, por el mismo motivo por el que se separaron.
    per = bool(periodo)
    filas = [["Serie", "Valor", ""] + (["Día"] if con_dia else [])
             + ([rotulo] if per else []) + ["Al día"]]
    estilos = []

    def agregar(etiqueta, r, unidad, dec=2, monto=False):
        i = len(filas)
        v = r["valor"]
        val = (miles(v) if abs(v) >= 1000 else num(v, 1)) if monto else num(v, dec)
        var = r.get("variacion")
        cd = "—" if var is None else ((miles(var, 1) if abs(var) >= 1000 else num(var, 1, True))
                                      if monto else num(var, dec, True))
        w = r.get(periodo) or {}
        if not w:
            cs = "—"
        elif w.get("clase") == "flujo":
            a = w["acumulado"]
            cs = ((miles(a, 1) if abs(a) >= 1000 else num(a, 1, True))
                  + f"  ·{w['ruedas']}r")
            estilos.append(("TEXTCOLOR", (3 + int(con_dia), i),
                            (3 + int(con_dia), i), _color_num(a)))
        else:
            vw = w["variacion"]
            cs = (miles(vw, 1) if abs(vw) >= 1000 else num(vw, 1 if monto else dec, True))
            estilos.append(("TEXTCOLOR", (3 + int(con_dia), i),
                            (3 + int(con_dia), i), _color_num(vw)))
        if var is not None and con_dia:
            estilos.append(("TEXTCOLOR", (3, i), (3, i), _color_num(var)))
        filas.append([etiqueta, val, unidad] + ([cd] if con_dia else [])
                     + ([cs] if per else [])
                     + [f"{r['fecha'][8:10]}/{r['fecha'][5:7]}"])

    # El riesgo país mejora cuando BAJA: el color se invierte a mano respecto del resto.
    i = len(filas)
    w = rp.get(periodo) or {}
    filas.append(["Riesgo país · EMBI+", f"{rp['valor']:.0f}", "bps"]
                 + ([num(rp["variacion"], 0, True)] if con_dia else [])
                 + ([num(w.get("variacion"), 0, True) if w else "—"] if per else [])
                 + [f"{rp['fecha'][8:10]}/{rp['fecha'][5:7]}"])
    if con_dia:
        estilos.append(("TEXTCOLOR", (3, i), (3, i),
                        VERDE if rp["variacion"] < 0 else ROJO))
    if w:
        estilos.append(("TEXTCOLOR", (3 + int(con_dia), i), (3 + int(con_dia), i),
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

    ncols = 4 + int(con_dia) + int(per)
    base = {6: (.35, .13, .09, .13, .17, .13), 5: (.42, .15, .11, .17, .15),
            4: (.48, .17, .12, .23)}
    rel = base[ncols]
    ultima = ncols - 1
    anchos = [w * ancho for w in rel]
    t = Table(filas, colWidths=anchos, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), SEMI, 8.8),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("LINEBELOW", (0, 0), (-1, 0), .8, BORDE),
        ("FONT", (0, 1), (-1, -1), REG, 9.6),
        ("FONT", (1, 1), (1, -1), SEMI, 9.6),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TEXTCOLOR", (2, 1), (2, -1), GRIS),
        ("TEXTCOLOR", (ultima, 1), (ultima, -1), GRIS),
        ("LINEAFTER", (2 + int(con_dia), 0), (2 + int(con_dia), -1), .5, SUAVE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ] + estilos))
    return t


def tabla_simple(filas, anchos_rel, ancho, centrar=()):
    t = Table(filas, colWidths=[w * ancho for w in anchos_rel], repeatRows=1)
    est = [("FONT", (0, 0), (-1, 0), SEMI, 8.8),
           ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
           ("LINEBELOW", (0, 0), (-1, 0), .8, BORDE),
           ("FONT", (0, 1), (-1, -1), REG, 9.6),
           ("TOPPADDING", (0, 0), (-1, -1), 5),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
           ("LEFTPADDING", (0, 0), (-1, -1), 3)]
    for c in centrar:
        est.append(("ALIGN", (c, 0), (c, -1), "CENTER"))
    t.setStyle(TableStyle(est))
    # Entera o en la pagina siguiente: cinco filas partidas al medio no se leen.
    return KeepTogether([t])


def figura(ruta, pie, ancho):
    """Una curva con su pie. KeepTogether evita que el pie quede huérfano en la página siguiente."""
    p = Path(ruta)
    if not p.exists():
        return []
    from PIL import Image as PILImage
    w, h = PILImage.open(p).size
    img = Image(str(p), width=ancho, height=ancho * h / w)
    return [KeepTogether([img, Paragraph(pie, PIE_FIG)])]


def construir(ruta_json, dir_curvas, textos, salida, modo="auto"):
    """`modo` decide QUÉ VENTANA muestra el informe.

      · "diario"  — sólo la variación del día. Es lo que sale todos los días.
      · "periodo" — sólo la del cierre semanal o mensual. Es un entregable APARTE, del mismo día.
      · "auto"    — el comportamiento viejo: las dos ventanas juntas si la rueda cierra período.

    Mezclarlas en un solo informe se lee mal —hay que recordar de qué ventana habla cada frase— y
    encima invita a aplicarle a la columna del período la regla de signos que sólo vale para la del
    día. Por eso el día que cierra período se arman dos.
    """
    d = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
    fecha = d["fecha"]
    dir_curvas = Path(dir_curvas)

    MARGEN = 16 * mm
    ANCHO = A4[0] - 2 * MARGEN

    # El MODO nombra el reporte, no `tipos`: el mismo JSON produce un PDF que dice "Reporte
    # diario" y otro que dice "Reporte mensual".
    tipos = d.get("tipos") or []
    periodo, rotulo, nombre = periodo_de(tipos)
    if modo == "periodo" and not periodo:
        raise ValueError(f"modo='periodo' pero la rueda del {fecha} no cierra período: {tipos}")
    if modo == "diario":
        periodo, rotulo, nombre = None, "", ""
    columnas = "periodo" if modo == "periodo" else ("ambas" if periodo else "dia")
    clase = ("Reporte mensual" if periodo == "mensual" else
             "Reporte semanal" if periodo == "semanal" else "Reporte diario")

    def portada(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 34 * mm, A4[0], 34 * mm, stroke=0, fill=1)
        canvas.setFillColor(CYAN)
        canvas.setFont(SEMI, 9.3)
        canvas.drawString(MARGEN, A4[1] - 13 * mm, "RENTA FIJA ARGENTINA")
        canvas.setFillColor(colors.white)
        canvas.setFont(BOLD, 18)
        canvas.drawString(MARGEN, A4[1] - 21.5 * mm, f"Cierre de mercado · {clase}")
        _lockup(canvas, "balanz_lockup_white.png", A4[0] - MARGEN, A4[1] - 16.5 * mm, 48 * mm)
        canvas.setFont(REG, 11.5)
        canvas.setFillColor(colors.HexColor("#C8D7EE"))
        canvas.drawString(MARGEN, A4[1] - 28.5 * mm, _fecha_larga(fecha).capitalize())
        pie(canvas, doc)
        canvas.restoreState()

    def interior(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDE)
        canvas.setLineWidth(.6)
        canvas.line(MARGEN, A4[1] - 14 * mm, A4[0] - MARGEN, A4[1] - 14 * mm)
        canvas.setFillColor(GRIS)
        canvas.setFont(REG, 9.3)
        canvas.drawString(MARGEN, A4[1] - 12.2 * mm,
                          f"Renta fija Argentina · {clase.lower()} · "
                          f"{fecha[8:10]}/{fecha[5:7]}/{fecha[:4]}")
        _lockup(canvas, "balanz_lockup_navy.png", A4[0] - MARGEN, A4[1] - 11.5 * mm, 32 * mm)
        pie(canvas, doc)
        canvas.restoreState()

    def pie(canvas, doc):
        canvas.setFillColor(GRIS)
        canvas.setFont(REG, 8.6)
        canvas.drawString(MARGEN, 10 * mm,
                          "No constituye asesoramiento de inversión ni recomendación de "
                          "compra o venta.")
        canvas.drawRightString(A4[0] - MARGEN, 10 * mm, str(canvas.getPageNumber()))

    doc = BaseDocTemplate(str(salida), pagesize=A4, leftMargin=MARGEN, rightMargin=MARGEN,
                          topMargin=MARGEN, bottomMargin=MARGEN,
                          title=f"Renta fija Argentina · cierre {fecha}",
                          author="", subject="Cierre de mercado de renta fija argentina")
    marco_p = Frame(MARGEN, MARGEN + 4 * mm, ANCHO, A4[1] - 39 * mm - MARGEN, id="p")
    marco_i = Frame(MARGEN, MARGEN + 4 * mm, ANCHO, A4[1] - 20 * mm - MARGEN, id="i")
    doc.addPageTemplates([PageTemplate(id="portada", frames=[marco_p], onPage=portada),
                          PageTemplate(id="interior", frames=[marco_i], onPage=interior)])

    E = [NextPageTemplate("interior")]
    E.append(Paragraph(textos["resumen"], LEDE))
    E.append(Spacer(1, 4))

    E.append(Paragraph("Variaciones por familia", H2))
    E.append(tabla_familias(d["resumen"], ANCHO, periodo or "semanal",
                            rotulo or "Período", columnas))
    E.append(Spacer(1, 4))
    ref = d.get("referencias", {}).get(periodo) if periodo else None
    nota_ref = (f"{nombre} se mide contra el cierre del {ref[8:10]}/{ref[5:7]}. " if ref else "")
    E.append(Paragraph(
        nota_ref + "Las columnas de precio y tasa son <b>medianas</b>, no promedios: el movimiento "
        "del instrumento típico de cada familia, sin ponderar por volumen ni por circulante. En "
        "toda familia hay algún ilíquido que no opera en varias ruedas y salta cuando por fin "
        "cruza; con promedio ese único dato define el signo de la familia entera. La columna "
        "<b>Mon.</b> es la moneda en la que se valúa cada familia: dos TIR en monedas distintas no "
        "forman un spread, porque parte de la diferencia es canje. Por eso las comparaciones entre "
        "legislaciones que siguen están todas llevadas a MEP.", P_CHICO))

    # Sin esto el lector ve precio y tasa subiendo juntos y lo toma por un error de dato. En la
    # columna del DÍA lo sería; en la del período, no: el devengamiento se come la relación
    # inversa. Va sólo cuando hay cierre de período, porque en un diario la regla sí vale.
    if periodo:
        # KeepTogether: si no entra al pie de la tabla se va entera a la pagina siguiente. Sin eso
        # se partia a mitad de frase en el salto de pagina.
        E.append(KeepTogether([Spacer(1, 3), Paragraph(
            f"<b>En {'el mes' if periodo == 'mensual' else 'la semana'} es normal que suban el "
            "precio Y la tasa a la vez, y no es un error de dato.</b> En una rueda el "
            "devengamiento es despreciable y por eso, si la tasa sube, el precio baja. En un "
            "período largo no: un bono en pesos al 28% de tasa efectiva anual gana cerca de 2% de "
            "precio en un mes sin que su tasa se mueva, los ajustables por CER suman la inflación "
            "del período y los dólar linked la devaluación. Y en las familias a tasa FLOTANTE "
            "—TAMAR y duales— la relación es directa incluso en el día, porque cuando sube la tasa "
            "proyectada sube también el pago final.", P_CHICO)]))

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
                              centrar=(1, 2, 3, 4)))
        E.append(Spacer(1, 6))
    E += figura(dir_curvas / "lecaps_cer.png",
                "Las dos curvas en la misma escala de TEA: los CER llevados a nominal con la "
                "inflación publicada.", ANCHO)
    E += figura(dir_curvas / "breakeven.png",
                "La inflación a la que una LECAP y un CER del mismo plazo rinden lo mismo.", ANCHO)
    E += figura(dir_curvas / "tamar.png",
                "Curva TAMAR, con la pata TAMAR de los duales y la TAMAR spot de bancos privados.",
                ANCHO)

    # El margen sobre TAMAR va acá, pegado a la curva TAMAR, y no en el bloque macro: es un spread
    # de crédito y plazo del Tesoro, no una tasa de política. Es además el número que dice si el
    # Tesoro va a poder colocar plazo en la próxima licitación.
    bq_dua = _bloque_mercado(d, "margenTamar")
    if bq_dua:
        E.append(KeepTogether([
            Paragraph("Margen sobre TAMAR de los duales", H2),
            tabla_mercado(bq_dua, ANCHO, periodo, "Mes" if periodo == "mensual" else "Semana",
                          con_dia=(modo != "periodo"))]))
        E.append(Spacer(1, 4))
        for t in textos.get("duales") or []:
            E.append(Paragraph(t, P))
        E.append(Paragraph(
            "Un dual CER/TAMAR paga al vencimiento lo que haya rendido más entre el CER y la TAMAR "
            "capitalizada, así que tiene dos valuaciones posibles; este margen es siempre el de la "
            "<b>pata TAMAR</b>, se esté pagando esa pata o no, porque es el que se compara contra "
            "la tasa de fondeo. <b>Más alto es bono más barato</b>: el mercado pide más spread para "
            "prestarle al Tesoro a ese plazo. La convención es TNA 32/365 —la misma que publica "
            "1816—; en 180/360 el mismo bono el mismo día da unos dos puntos más y los dos números "
            "no son comparables." + _nota_atraso(bq_dua, d), P_CHICO))

    E.append(Paragraph("Soberanos en dólares", H2))
    for t in textos["dolares"]:
        E.append(Paragraph(t, P))
    if textos.get("tabla_legislacion"):
        E.append(tabla_simple(textos["tabla_legislacion"], (.2, .2, .22, .19, .19), ANCHO,
                              centrar=(1, 2, 3, 4)))
        E.append(Spacer(1, 6))
    E += figura(dir_curvas / "globales_bonares.png",
                "Curva soberana en dólares por legislación, con las dos patas en la misma moneda.",
                ANCHO)

    # El forward de los Bonares cortos va con la curva en dólares y antes del canje: el canje es
    # justamente la razón por la que este rendimiento se mide contra cable y no contra MEP.
    bq_bon = _bloque_mercado(d, "bonares")
    if bq_bon:
        E.append(KeepTogether([
            Paragraph("Bonares cortos y el forward de la elección", H2),
            tabla_mercado(bq_bon, ANCHO, periodo, "Mes" if periodo == "mensual" else "Semana",
                          con_dia=(modo != "periodo"),
                          etiquetas={"AO27": "AO27 · vence oct-2027",
                                     "AO28": "AO28 · vence oct-2028",
                                     "forward": "Forward 1Y1Y implícito"})]))
        E.append(Spacer(1, 4))
        for t in textos.get("bonares") or []:
            E.append(Paragraph(t, P))
        E.append(Paragraph(
            "Rendimientos <b>contra cable</b>, no contra MEP: medido contra MEP el número se mueve "
            "con el canje CCL/MEP, que cambia por razones ajenas al riesgo del bono. Los dos vencen "
            "el mismo mes con un año de diferencia, así que entre ellos queda un forward 1Y1Y "
            "limpio: <b>a qué tasa está descontando el mercado que va a rendir un bono argentino en "
            "dólares durante el año posterior a la elección de octubre de 2027</b>, que es cuando "
            "vence el AO27. Sale de las TIR de los dos bonos y no de una curva cero bootstrapeada, "
            "y ambos amortizan y pagan renta: sirve para el nivel y sobre todo para el movimiento, "
            "no para discutir décimas." + _nota_atraso(bq_bon, d), P_CHICO))

    # El canje va JUNTO al hard dollar y no en el bloque macro: es lo que hace que comparar un
    # Global contra un Bonar exija llevarlos a la misma punta, así que se lee al lado de esa tabla.
    # Opcional para no romper llamadas viejas; el skill lo pide en los tres tipos de informe.
    if textos.get("canje"):
        E.append(Paragraph("Canje CCL/MEP", H2))
        for t in textos["canje"]:
            E.append(Paragraph(t, P))

    E.append(Paragraph("BOPREALes y la rotación a Bonares", H2))
    for t in textos["bopreal"]:
        E.append(Paragraph(t, P))
    if textos.get("tabla_bopreal"):
        E.append(tabla_simple(textos["tabla_bopreal"], (.16, .13, .13, .16, .13, .13, .16), ANCHO,
                              centrar=(1, 2, 3, 4, 5, 6)))
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
                           tabla_macro(d["macro"], ANCHO, periodo,
                                       "Mes" if periodo == "mensual" else "Semana",
                                       con_dia=(modo != "periodo"))]))
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

    # La seccion de cierre existe SOLO si la rueda cierra periodo. En un reporte diario, `periodo`
    # es None y antes se imprimia igual un titulo "Cierre semanal" sin nada debajo: el bloque nunca
    # habia corrido para un diario porque el PDF nacio un viernes.
    cierre = textos.get("cierre") or textos.get("semanal") or []
    if periodo and cierre:
        E.append(PageBreak())
        E.append(Paragraph(f"Cierre {'mensual' if periodo == 'mensual' else 'semanal'}", H2))
        for t in cierre:
            E.append(Paragraph(t, P))

    E.append(Spacer(1, 8))
    E.append(Paragraph(textos["fuentes"], P_CHICO))

    doc.build(E)
    return str(salida)


if __name__ == "__main__":
    print("Se usa desde el informe diario; no tiene modo directo.", file=sys.stderr)
