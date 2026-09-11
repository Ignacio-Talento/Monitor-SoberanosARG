#!/usr/bin/env python3
"""Resultado de la licitación del Tesoro de una fecha, leído de la Secretaría de Finanzas.

    py licitacion_tesoro.py                       # hoy
    py licitacion_tesoro.py --fecha 2026-08-27
    py licitacion_tesoro.py --datos informes/datos_2026-09-11.json   # + comparación con el secundario

DE DÓNDE SALE. La Secretaría publica cada resultado como noticia en argentina.gob.ar, listada en
https://www.argentina.gob.ar/economia/finanzas/noticias. El slug no es predecible —cambia con los
instrumentos y lleva un sufijo numérico—, así que se recorren los links «resultado-de-la-licitacion»
de esa página y se abre cada uno hasta encontrar el que tiene la fecha pedida. Sale a la tarde: la
recepción de ofertas cierra a las 15 y el resultado se publica entre una y tres horas después.

QUÉ TRAE. La primera tabla de la página son los totales (ofertas, VNO y valor efectivo ofertados y
adjudicados, en millones); las siguientes, una por familia, con cada instrumento: VNO ofertado y
adjudicado, valor efectivo adjudicado, precio o TEM de corte, TIREA y VNO en circulación. Todo en
millones. No trae el rollover: los vencimientos que se cubren no figuran en la página.

LOS INSTRUMENTOS NUEVOS NO TRAEN TICKER —«(nueva)» en vez de «(S30N6 - reapertura)»—, así que se
arma con la convención del Tesoro: letra de la familia + día + letra del mes + último dígito del
año (S29E7, X29E7, D30N6). Para los TAMAR nuevos no se arma: la convención no es uniforme y se deja
el nombre.

TIREA CONTRA EL SECUNDARIO. Con --datos se agrega la TIR de cierre de 1816 del mismo ticker: para
LECAP y BONCAP es la TEA, para CER la TIR real y para dólar linked la TIR en dólares, las mismas
magnitudes que la TIREA de corte. Para los TAMAR, 1816 proyecta la TAMAR con su propio supuesto y
el Tesoro informa otra cosa; se deja el dato pero no se resta.

SALIDA: informes/licitacion_AAAA-MM-DD.json y el mismo JSON por pantalla.
"""
import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://www.argentina.gob.ar"
LISTADO = BASE + "/economia/finanzas/noticias"
CAB = {"User-Agent": "Mozilla/5.0"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
LETRA_MES = "EFMAYJLGSOND"          # convención de tickers del Tesoro: E=ene, F=feb, ..., D=dic


def _texto(fragmento):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragmento))).strip()


def _num(celda):
    """'$ 5.000.000 (***)' -> 5000000.0 · 'USD 987,80' -> 987.8 · '29,75%' -> 29.75"""
    m = re.search(r"-?[\d.]+(?:,\d+)?", celda.replace(" ", ""))
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def _get(url):
    """GET con tres intentos: argentina.gob.ar corta conexiones sueltas (ConnectionResetError
    10054 el 11/09/2026) y un solo corte no puede hacer que el informe diga «no salió»."""
    import time
    for i in range(3):
        try:
            r = requests.get(url, headers=CAB, timeout=30)
            if r.status_code < 500:
                # argentina.gob.ar no declara el charset y requests asume ISO-8859-1: sin esto el
                # comunicado entra como «D�LAR» y con él la familia y los nombres de los títulos.
                if not r.encoding or r.encoding.upper() in ("ISO-8859-1", "LATIN-1"):
                    r.encoding = "utf-8"
                return r
        except requests.exceptions.RequestException:
            if i == 2:
                raise
        time.sleep(5 * (i + 1))
    return r


def buscar_resultado(fecha):
    """URL de la noticia de resultado publicada en `fecha`, o None si todavía no salió."""
    r = _get(LISTADO)
    r.raise_for_status()
    links = []
    for href in re.findall(r'href="(/noticias/resultado-de-la-licitacion[^"]*)"', r.text):
        if href not in links:
            links.append(href)
    buscada = f"{fecha.day:02d} de {MESES[fecha.month - 1]} de {fecha.year}"
    buscada2 = f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"
    for href in links[:4]:                  # el listado va del más nuevo al más viejo
        p = _get(BASE + href)
        if p.ok and (buscada in p.text or buscada2 in p.text) and "Secretaría de Finanzas anuncia" in p.text:
            return BASE + href, p.text
    return None, None


def _ticker(nombre):
    m = re.search(r"\(([A-Z0-9]{4,6})\s*-\s*reapertura\)", nombre)
    if m:
        return m.group(1), False
    v = re.search(r"VENCIMIENTO (\d{1,2}) DE ([A-ZÁÉÍÓÚ]+) DE (\d{4})", nombre.upper())
    if not v:
        return None, True
    dia, mes, anio = int(v.group(1)), MESES.index(v.group(2).lower()) + 1, int(v.group(3))
    n = nombre.upper()
    if "TAMAR" in n:
        return None, True
    # «CER» como palabra: las letras dólar linked son «CERO CUPÓN» y con un `in` se las llevaba la
    # rama del CER —la nueva del 11/09/2026 salía X30N6, que además es el ticker de un Boncer vivo—.
    letra = ("X" if re.search(r"CER", n) else "D" if ("DÓLAR" in n or "DOLAR" in n) else
             "T" if n.startswith("BONO") else "S")
    return f"{letra}{dia:02d}{LETRA_MES[mes - 1]}{anio % 10}", True


def _familia(encabezado):
    e = encabezado.lower()
    return ("tasa fija" if "fija" in e else "CER" if "cer" in e else "TAMAR" if "tamar" in e or
            "variable" in e else "dólar linked" if "linked" in e else "dólares" if "dólar" in e else e)


def parsear(url, pagina):
    tablas = re.findall(r"<table.*?</table>", pagina, re.S)
    texto = _texto(re.sub(r"<script.*?</script>|<style.*?</style>", "", pagina, flags=re.S))
    out = {"url": url, "instrumentos": [], "totales": {}}
    pdf = re.findall(r'href="([^"]+\.pdf)"', pagina)
    if pdf:
        out["pdf"] = pdf[0]
    m = re.search(r"ofertas por un total de valor efectivo de \$\s*([\d.,]+) billones.*?adjudicó un total "
                  r"de valor efectivo \$\s*([\d.,]+) billones", texto)
    if m:
        out["totales"]["veOfertadoBillones"] = _num(m.group(1))
        out["totales"]["veAdjudicadoBillones"] = _num(m.group(2))
    rollover = re.search(r"rollover[^.]*?([\d.,]+)\s*%", texto, re.I)
    if rollover:
        out["totales"]["rolloverPct"] = _num(rollover.group(1))
    m = re.search(r"Tipo de Cambio de Referencia del día (.*?) de la Comunicación.*?\(Pesos / USD ([\d.,]+)\)", texto)
    if m:
        out["tcReferencia"] = {"dia": m.group(1), "valor": _num(m.group(2))}
    prorrateos = re.findall(r"factor de prorrateo de ([\d.,]+)%", texto)

    for i, t in enumerate(tablas):
        filas = [[_texto(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", f, re.S)]
                 for f in re.findall(r"<tr.*?</tr>", t, re.S)]
        filas = [f for f in filas if f]
        if not filas:
            continue
        if i == 0 and filas[0] and "Resultado" in " ".join(filas[0]):
            cols = filas[0][1:]
            for f in filas[1:]:
                out["totales"][f[0]] = dict(zip(cols, f[1:]))
            continue
        cab = filas[0]
        fam = _familia(cab[0])
        idx = {k: j for j, k in enumerate(cab)}

        def col(f, clave):
            j = next((j for k, j in idx.items() if k.startswith(clave)), None)
            return f[j] if j is not None and j < len(f) else None

        for f in filas[1:]:
            if len(f) < 4:
                continue
            tk, nueva = _ticker(f[0])
            corte_txt = col(f, "Precio") or ""
            adj_txt = col(f, "VNO Adjudicado") or ""
            reg = {
                "nombre": f[0], "ticker": tk, "nueva": nueva, "familia": fam,
                "moneda": "USD" if "USD" in (col(f, "VNO Ofertado") or "") else "ARS",
                "vnoOfertado": _num(col(f, "VNO Ofertado") or ""),
                "vnoAdjudicado": _num(adj_txt),
                "veAdjudicado": _num(col(f, "Valor Efectivo") or ""),
                "corte": _num(corte_txt),
                # «2,25% (**)» es una TEM de corte (instrumento nuevo capitalizable); si no, es precio
                "corteEsTEM": "%" in corte_txt,
                "tirea": _num(col(f, "TIREA") or "") if col(f, "TIREA") else None,
                "circulacion": _num(col(f, "VNO Total") or ""),
                "prorrateo": "(***)" in adj_txt,
            }
            if reg["vnoOfertado"] and reg["vnoAdjudicado"]:
                reg["ofertadoSobreAdjudicado"] = round(reg["vnoOfertado"] / reg["vnoAdjudicado"], 3)
            out["instrumentos"].append(reg)
    if prorrateos:
        out["factoresProrrateo"] = [_num(p) for p in prorrateos]
    return out


def comparar(res, ruta_datos):
    d = json.loads(Path(ruta_datos).read_text(encoding="utf-8"))
    tea = {x["ticker"]: x for x in d["instrumentos"]}
    for reg in res["instrumentos"]:
        x = tea.get(reg["ticker"] or "")
        if not x or x.get("tea") is None:
            continue
        reg["secundario"] = {"tir": x["tea"], "precio": x.get("precio"), "fecha": d["fecha"]}
        if reg["tirea"] is not None and reg["familia"] != "TAMAR":
            # positivo: el Tesoro convalidó MÁS tasa que el cierre del secundario
            reg["corteMenosSecundarioPb"] = round((reg["tirea"] - x["tea"]) * 100)
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha")
    ap.add_argument("--datos")
    a = ap.parse_args(argv)
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    fecha = date.fromisoformat(a.fecha) if a.fecha else hoy
    url, pagina = buscar_resultado(fecha)
    if not url:
        print(json.dumps({"fecha": fecha.isoformat(), "publicado": False}, ensure_ascii=False))
        return 2
    res = {"fecha": fecha.isoformat(), "publicado": True, **parsear(url, pagina)}
    if a.datos:
        res = comparar(res, a.datos)
    salida = Path(__file__).resolve().parent / "informes" / f"licitacion_{fecha.isoformat()}.json"
    salida.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
