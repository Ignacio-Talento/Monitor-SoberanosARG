#!/usr/bin/env python3
"""Arma el histórico de insumos del spread de sintéticos, hacia atrás y hacia adelante.

DE DÓNDE SALEN LOS FUTUROS. Del Centro de Estadísticas de Mercado de A3 Mercados (ex Matba Rofex),
que expone una API pública sin credenciales: apicem.matbarofex.com.ar/api/v2/closing-prices. Da
precio de AJUSTE, volumen, interés abierto y tasa implícita por contrato y por rueda, con datos
desde el 2020-01-02. Es mejor fuente que el scraping de Eco que usa la solapa en vivo:

  - el ajuste es el precio oficial de cierre, no el último operado, que es lo que corresponde en
    una serie histórica;
  - no viene diferido 20 minutos ni falla de forma intermitente;
  - trae volumen e interés abierto, que dicen si el contrato realmente operó esa rueda.

A cambio publica con un día de rezago —el ajuste sale después del proceso de clearing—, así que la
rueda de hoy entra recién mañana. Para el intradía la solapa sigue usando Eco.

QUÉ GUARDA. Los INSUMOS de cada rueda, no el spread ya calculado: precio de cada futuro, tasa de
cada bono y tipo de cambio. El spread sale de interpolar curvas, elegir convenciones de
anualización y aplicar comisiones, y todo eso vive en sinteticos.html; recalcularlo acá en Python
sería una segunda implementación de la misma fórmula, y en algún momento divergen sin que nadie se
entere. Con los insumos crudos el frontend rearma el histórico con las mismas funciones que usa
para hoy, y una corrección de fórmula se propaga sola hacia atrás.

SALIDA: spreads_sinteticos.json
    { "2026-08-26": { "tc": 1514.16,
                      "fut":   { "DLR/SEP26": 1538.0, ... },     # precio de ajuste
                      "vol":   { "DLR/SEP26": 130791, ... },     # volumen de esa rueda
                      "lecap": { "S30S6": 28.26, ... },          # TEA en %
                      "dl":    { "D30S6": 9.73, ... } },         # TIR en %
      ... }

USO
    python actualizar_spreads.py                    # completa lo que falte desde DESDE_POR_DEFECTO
    python actualizar_spreads.py --desde 2025-06-01 # backfill más largo
    python actualizar_spreads.py --rehacer          # ignora lo ya guardado y rearma todo
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import requests

try:
    from precios_1816 import Cliente1816, Error1816, MAX_TICKERS_SERIES
except ImportError:
    print("ERROR: no se pudo importar precios_1816", file=sys.stderr)
    sys.exit(1)

from actualizar_historicos import hoy_art, leer_tickers

SALIDA = "spreads_sinteticos.json"
CEM = "https://apicem.matbarofex.com.ar/api/v2/closing-prices"
BCRA_WORKER = "https://indicadoresbcra.granda-fra.workers.dev"

# Desde dónde se arma si no se pide otra cosa. Antes de 2026 las LECAPs y los dólar linked que hoy
# sigue el monitor casi no existían, así que la curva quedaría armada con dos puntos y la
# interpolación diría cualquier cosa. Se puede pisar con --desde para un backfill más largo.
DESDE_POR_DEFECTO = "2026-01-02"

CABECERAS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
             "Referer": "https://cem.matbarofex.com.ar/"}

MES_A_TXT = {1: "ENE", 2: "FEB", 3: "MAR", 4: "ABR", 5: "MAY", 6: "JUN",
             7: "JUL", 8: "AGO", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DIC"}


def a_ticker_solapa(symbol):
    """DLR082026 -> DLR/AGO26. Devuelve None para lo que no sea un futuro de dólar simple.

    Las opciones vienen en el mismo listado como 'DLR082026 Call 1500': se descartan por el
    espacio. Si entraran, el frontend las tomaría como contratos y armaría curvas con strikes.
    """
    if not symbol or " " in symbol or not symbol.startswith("DLR"):
        return None
    resto = symbol[3:]
    if len(resto) != 6 or not resto.isdigit():
        return None
    mes, anio = int(resto[:2]), int(resto[2:])
    if mes not in MES_A_TXT:
        return None
    return f"DLR/{MES_A_TXT[mes]}{anio % 100:02d}"


def futuros_cem(desde, hasta):
    """-> { 'AAAA-MM-DD': { 'DLR/SEP26': {'p': ajuste, 'v': volumen} } }"""
    out = {}
    pagina, porPagina = 1, 1000
    while True:
        try:
            r = requests.get(CEM, headers=CABECERAS, timeout=60, params={
                "product": "DLR", "from": desde, "to": hasta,
                "page": pagina, "pageSize": porPagina, "sort": "dateTime", "sortDir": "ASC"})
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"AVISO: CEM falló en la página {pagina} ({e})", file=sys.stderr)
            break
        filas = d.get("data") or []
        for x in filas:
            tk = a_ticker_solapa(x.get("symbol"))
            precio = x.get("settlement") or x.get("close")
            if not tk or not precio:
                continue
            f = str(x.get("dateTime", ""))[:10]
            out.setdefault(f, {})[tk] = {"p": round(float(precio), 4),
                                         "v": int(x.get("volume") or 0)}
        total = d.get("totalEntries")
        if len(filas) < porPagina or (total and pagina * porPagina >= total):
            break
        pagina += 1
        time.sleep(0.3)
    return out


def tc_bcra(desde, hasta):
    """-> { 'AAAA-MM-DD': A3500 }"""
    try:
        r = requests.get(f"{BCRA_WORKER}/?serie=usd&desde={desde}&hasta={hasta}", timeout=40)
        det = (r.json().get("results") or [{}])[0].get("detalle") or []
        return {d["fecha"]: float(d["valor"]) for d in det if d.get("fecha") and d.get("valor")}
    except Exception as e:
        print(f"AVISO: no se pudo traer el TC mayorista ({e})", file=sys.stderr)
        return {}


def series_tea(cli, items, desde, hasta):
    """-> { 'AAAA-MM-DD': { ticker_del_monitor: tea_% } } para los items dados."""
    porT = {it["t1816"]: it["eco"] for it in items if it["t1816"]}
    out = {}
    tickers = sorted(porT)
    for i in range(0, len(tickers), MAX_TICKERS_SERIES):
        lote = tickers[i:i + MAX_TICKERS_SERIES]
        filas = None
        for espera in (0, 15, 45):
            if espera:
                print(f"    reintentando en {espera}s...", file=sys.stderr)
                time.sleep(espera)
            try:
                filas = cli.series(lote, ["tea"], moneda="ars",
                                   fecha_inicial=desde, fecha_final=hasta)
                break
            except Error1816 as e:
                ultimo = e
        if filas is None:
            print(f"AVISO: 1816 falló en un lote ({ultimo})", file=sys.stderr)
            continue
        for f in filas:
            v = f.get("tea")
            eco = porT.get(f.get("ticker"))
            fecha = str(f.get("fecha", ""))[:10]
            if eco and fecha and isinstance(v, (int, float)):
                out.setdefault(fecha, {})[eco] = round(v * 100, 6)
    return out


def main(argv):
    rehacer = "--rehacer" in argv
    desde = DESDE_POR_DEFECTO
    if "--desde" in argv:
        desde = argv[argv.index("--desde") + 1]
    # El ajuste del día sale recién después del clearing, así que se pide hasta ayer.
    hasta = (hoy_art() - timedelta(days=1)).strftime("%Y-%m-%d")

    datos = {}
    if os.path.exists(SALIDA) and not rehacer:
        with open(SALIDA, encoding="utf-8") as fh:
            datos = json.load(fh)

    print(f"Insumos del spread · {desde} a {hasta}"
          + (f" · {len(datos)} ruedas ya guardadas" if datos else ""))

    print("Futuros (CEM de A3)...")
    fut = futuros_cem(desde, hasta)
    print(f"  {len(fut)} ruedas con futuros")
    if not fut:
        print("Sin futuros, no hay nada que armar.", file=sys.stderr)
        return 1

    # Sólo se piden las tasas de las ruedas que falten: 1816 cobra por ticker y por día.
    faltan = sorted(f for f in fut if rehacer or f not in datos)
    if not faltan:
        print("No hay ruedas nuevas.")
        return 0
    print(f"  {len(faltan)} ruedas nuevas: {faltan[0]} a {faltan[-1]}")

    items = leer_tickers()
    lecaps = [it for it in items if it["hoja"] == "LECAPS"]
    dls = [it for it in items if it["hoja"] == "USD Linked"]
    cli = Cliente1816()

    print(f"Tasas de 1816 ({len(lecaps)} lecaps + {len(dls)} dólar linked)...")
    tLecap = series_tea(cli, lecaps, faltan[0], faltan[-1])
    tDL = series_tea(cli, dls, faltan[0], faltan[-1])
    print(f"  lecaps: {len(tLecap)} ruedas · dólar linked: {len(tDL)} ruedas")

    tcs = tc_bcra(faltan[0], faltan[-1])
    print(f"  TC mayorista: {len(tcs)} ruedas")

    nuevas, sinTasas = 0, 0
    for f in faltan:
        # El A3500 de una rueda puede no estar publicado todavía; se usa el último anterior, que es
        # lo mismo que hace el monitor al valuar los dólar linked.
        tc = tcs.get(f)
        if tc is None:
            previas = [k for k in sorted(tcs) if k <= f]
            tc = tcs[previas[-1]] if previas else None
        if not tc or not tLecap.get(f) or not tDL.get(f):
            sinTasas += 1
            continue
        datos[f] = {"tc": round(tc, 4),
                    "fut": {k: v["p"] for k, v in fut[f].items()},
                    "vol": {k: v["v"] for k, v in fut[f].items()},
                    "lecap": tLecap[f], "dl": tDL[f]}
        nuevas += 1

    if sinTasas:
        print(f"  {sinTasas} ruedas sin tasas o sin TC, se saltean")
    if not nuevas:
        print("No se agregó ninguna rueda.")
        return 0

    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fechas = sorted(datos)
    print(f"\n{SALIDA}: +{nuevas} ruedas · {len(datos)} en total "
          f"({fechas[0]} a {fechas[-1]}) · {os.path.getsize(SALIDA) // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
