#!/usr/bin/env python3
"""Acumula, día a día, los insumos del spread de sintéticos de la solapa Sintéticos.

POR QUÉ ACUMULAR Y NO BAJAR UNA SERIE. No hay fuente pública de históricos de futuros de dólar:
Eco publica la foto del día y 1816 no lista futuros. A3 Mercados los tiene, pero hay que pedirlos.
Así que el histórico se construye desde el día que se enciende esto, igual que historicos.xlsx.

POR QUÉ GUARDA INSUMOS Y NO EL SPREAD YA CALCULADO. El spread sale de interpolar curvas, elegir
convenciones de anualización y aplicar comisiones — todo eso vive en sinteticos.html. Si el script
lo recalculara en Python habría DOS implementaciones de la misma fórmula, y en algún momento
divergen sin que nadie se entere. Guardando los insumos crudos (precio de cada futuro, tasa de cada
bono, tipo de cambio) el frontend recalcula el histórico con exactamente las mismas funciones que
usa para el día de hoy, y una corrección de fórmula se propaga sola hacia atrás.

SALIDA: spreads_sinteticos.json
    { "2026-08-27": { "tc": 1514.1634,
                      "fut":   { "DLR/SEP26": 1538.0, ... },
                      "lecap": { "S30S6": 28.26, ... },      # TEA en %
                      "dl":    { "D30S6": 9.73, ... } },     # TIR en %
      ... }

USO
    python actualizar_spreads.py             # agrega la rueda de hoy
    python actualizar_spreads.py --forzar    # reescribe la de hoy si ya está
"""

import json
import os
import re
import sys
import time
from datetime import date, timedelta

import requests

try:
    from precios_1816 import Cliente1816, Error1816
except ImportError:
    print("ERROR: no se pudo importar precios_1816", file=sys.stderr)
    sys.exit(1)

from actualizar_historicos import hoy_art, leer_tickers

SALIDA = "spreads_sinteticos.json"
ECO = "https://bonos.ecovalores.com.ar/eco/ticker.php"
BCRA_WORKER = "https://indicadoresbcra.granda-fra.workers.dev"

# Mismos contratos que la solapa. Los vencidos se filtran solos por fecha.
FUTUROS = ["DLR/AGO26", "DLR/SEP26", "DLR/OCT26", "DLR/NOV26", "DLR/DIC26",
           "DLR/ENE27", "DLR/FEB27", "DLR/MAR27", "DLR/ABR27"]

MESES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
         "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}

CABECERAS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html",
    "Referer": "https://bonos.ecovalores.com.ar",
}


def venc_contrato(tk):
    """DLR/SEP26 -> date(2026, 9, 30). El vencimiento es siempre fin de mes."""
    m = MESES.get(tk[4:7])
    a = 2000 + int(tk[7:9])
    if not m:
        return None
    return date(a + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)


def precio_futuro(tk, intentos=3):
    """Precio de cierre del contrato, o None. Eco devuelve la página sin datos de forma
    intermitente —medido, ~40% de las consultas—, así que se reintenta."""
    for i in range(intentos):
        try:
            r = requests.get(f"{ECO}?t={tk}", headers=CABECERAS, timeout=20)
            m = re.search(r'<td class="precioticker">\s*([\d.,]+)\s*</td>', r.text)
            if m:
                return float(m.group(1).replace(".", "").replace(",", "."))
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def tc_mayorista():
    """A3500 más reciente, el mismo que usa el monitor para valuar los dólar linked."""
    hoy = hoy_art()
    desde = (hoy - timedelta(days=20)).strftime("%Y-%m-%d")
    try:
        r = requests.get(f"{BCRA_WORKER}/?serie=usd&desde={desde}"
                         f"&hasta={hoy.strftime('%Y-%m-%d')}", timeout=25)
        det = (r.json().get("results") or [{}])[0].get("detalle") or []
        det = [d for d in det if d.get("fecha") and d.get("valor")]
        if det:
            det.sort(key=lambda d: d["fecha"], reverse=True)
            return float(det[0]["valor"])
    except Exception as e:
        print(f"AVISO: no se pudo traer el TC mayorista ({e})", file=sys.stderr)
    return None


def tasas_1816(cli, items, fecha):
    """-> { ticker_del_monitor: tea_en_% } para los items dados."""
    if not items:
        return {}
    porT = {it["t1816"]: it["eco"] for it in items if it["t1816"]}
    filas = None
    for espera in (0, 15, 45):
        if espera:
            print(f"  reintentando en {espera}s...", file=sys.stderr)
            time.sleep(espera)
        try:
            filas = cli.precios(sorted(porT), ["tea"], moneda="ars", fecha_operacion=fecha)
            break
        except Error1816 as e:
            ultimo = e
    if filas is None:
        print(f"AVISO: 1816 no respondió ({ultimo})", file=sys.stderr)
        return {}
    out = {}
    for f in filas:
        v = f.get("tea")
        eco = porT.get(f.get("ticker"))
        if eco and isinstance(v, (int, float)):
            out[eco] = round(v * 100, 6)     # 1816 devuelve la tea como decimal
    return out


def main(argv):
    forzar = "--forzar" in argv
    hoy = hoy_art()
    clave = hoy.strftime("%Y-%m-%d")

    datos = {}
    if os.path.exists(SALIDA):
        with open(SALIDA, encoding="utf-8") as fh:
            datos = json.load(fh)
    if clave in datos and not forzar:
        print(f"Ya existe la rueda {clave}, saliendo. (--forzar para reescribirla)")
        return 0

    print(f"Insumos del spread para {clave}")

    fut = {}
    for tk in FUTUROS:
        v = venc_contrato(tk)
        if not v or v <= hoy:
            continue
        p = precio_futuro(tk)
        if p:
            fut[tk] = p
        else:
            print(f"  {tk}: sin precio tras 3 intentos", file=sys.stderr)
    print(f"  futuros: {len(fut)}")

    items = leer_tickers()
    lecaps = [it for it in items if it["hoja"] == "LECAPS"]
    dls = [it for it in items if it["hoja"] == "USD Linked"]

    cli = Cliente1816()
    tLecap = tasas_1816(cli, lecaps, clave)
    tDL = tasas_1816(cli, dls, clave)
    print(f"  lecaps: {len(tLecap)} · dólar linked: {len(tDL)}")

    tc = tc_mayorista()
    print(f"  TC mayorista: {tc}")

    # Sin futuros o sin bonos no hay spread posible: no se escribe una fila hueca que después
    # aparezca como un bache en el gráfico.
    if not fut or not tLecap or not tDL:
        print("Faltan insumos, no se guarda la rueda.", file=sys.stderr)
        return 1

    datos[clave] = {"tc": tc, "fut": fut, "lecap": tLecap, "dl": tDL}
    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    print(f"\n{SALIDA} actualizado: {len(datos)} ruedas")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
