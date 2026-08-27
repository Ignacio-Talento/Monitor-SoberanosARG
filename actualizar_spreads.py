#!/usr/bin/env python3
"""Arma el histórico de insumos del spread de sintéticos, hacia atrás y hacia adelante.

DE DÓNDE SALEN LOS FUTUROS. Del Centro de Estadísticas de Mercado de A3 Mercados (ex Matba Rofex),
que expone una API pública sin credenciales: apicem.matbarofex.com.ar/api/v2/closing-prices. Da
precio de AJUSTE, volumen, interés abierto y tasa implícita por contrato y por rueda, con datos
desde el 2020-01-02:

  - el ajuste es el precio oficial de cierre, no el último operado, que es lo que corresponde en
    una serie histórica;
  - trae volumen e interés abierto, que dicen si el contrato realmente operó esa rueda.

A cambio el AJUSTE se publica con un día de rezago —sale después del proceso de clearing—, así que
la rueda de hoy entra recién mañana. La solapa muestra el intradía con el otro endpoint del mismo
CEM (tick-prices, vía /api/futuros), que sí tiene las operaciones del día.

QUÉ GUARDA. Los INSUMOS de cada rueda, no el spread ya calculado: precio de cada futuro, tasa de
cada bono y tipo de cambio. El spread sale de interpolar curvas, elegir convenciones de
anualización y aplicar comisiones, y todo eso vive en sinteticos.html; recalcularlo acá en Python
sería una segunda implementación de la misma fórmula, y en algún momento divergen sin que nadie se
entere. Con los insumos crudos el frontend rearma el histórico con las mismas funciones que usa
para hoy, y una corrección de fórmula se propaga sola hacia atrás.

EL UNIVERSO SALE DE 1816, NO DEL EXCEL. Instrumentos.xlsx tiene sólo los instrumentos VIVOS, y
para el histórico hacen falta los que ya vencieron: una rueda de marzo se interpola con las LECAPs
que estaban vivas en marzo, no con las de hoy. 1816 los lista todos —70 LECAPs desde 2021 y 26
dólar linked desde 2022— con su emisión y vencimiento.

A cada instrumento se le pide sólo el tramo en que estuvo vivo. Pedirle a todos el rango entero
multiplicaría los créditos por diez para traer huecos: 1816 cobra tickers x campos x días.

Los vencimientos van al JSON en la clave "_venc", porque el frontend los necesita para armar las
curvas y los de los instrumentos vencidos ya no están en el Excel.

SALIDA: spreads_sinteticos.json
    { "_venc": { "S30S6": "2026-09-30", ... },        # vencimiento de cada instrumento usado
      "2026-08-26": { "tc": 1514.16,
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
    # type=FUT deja afuera las opciones, que vienen mezcladas en el mismo listado
    # ("DLR082026 Call 1500") y son casi la mitad de los registros por rueda.
    while pagina <= 60:                     # tope de seguridad, no criterio de corte
        try:
            r = requests.get(CEM, headers=CABECERAS, timeout=60, params={
                "product": "DLR", "type": "FUT", "from": desde, "to": hasta,
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
        # Se corta SÓLO por página incompleta. Antes también se miraba totalEntries contra
        # página x tamaño, y con las opciones adentro ese total no correspondía a lo que se
        # estaba trayendo: el backfill se cortaba en diciembre de 2024 sin avisar.
        if len(filas) < porPagina:
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


# Curvas de 1816: 9 = LECAPs, 12 y 17 = dólar linked. Las mismas que usa revisar_universo.py.
CURVAS_LECAP = [9]
CURVAS_DL = [12, 17]


def universo(cli, curvas, desde, hasta):
    """Instrumentos de esas curvas que estuvieron VIVOS en el rango.

    -> [ { 'ticker', 'emision': date, 'venc': date } ]
    Incluye los ya vencidos, que es justamente el punto: la curva de una rueda de marzo se arma
    con lo que cotizaba en marzo.
    """
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    out, vistos = [], set()
    for cur in curvas:
        items = None
        for espera in (0, 15, 45):
            if espera:
                print(f"    reintentando curva {cur} en {espera}s...", file=sys.stderr)
                time.sleep(espera)
            try:
                items = cli.instrumentos(curva_id=cur)
                break
            except Error1816 as e:
                ultimo = e
        if items is None:
            print(f"AVISO: no se pudo leer la curva {cur} ({ultimo})", file=sys.stderr)
            continue
        for x in items or []:
            tk = (x.get("ticker") or "").strip()
            v, e = str(x.get("fechaVencimiento") or "")[:10], str(x.get("fechaEmision") or "")[:10]
            if not tk or tk in vistos or len(v) != 10:
                continue
            try:
                venc = date.fromisoformat(v)
                emi = date.fromisoformat(e) if len(e) == 10 else d0
            except ValueError:
                continue
            # vivo en algún momento del rango pedido
            if venc < d0 or emi > d1:
                continue
            vistos.add(tk)
            out.append({"ticker": tk, "emision": max(emi, d0), "venc": venc})
        time.sleep(1.2)
    return out


def series_tea_universo(cli, insts, desde, hasta):
    """Series de TEA pidiéndole a cada instrumento SÓLO el tramo en que estuvo vivo.

    Se agrupan los que comparten ventana para no gastar una llamada por instrumento: 1816 admite
    10 tickers por request de series, y los bonos de una misma licitación suelen vivir el mismo
    tramo.
    """
    d1 = date.fromisoformat(hasta)
    porVentana = {}
    for it in insts:
        ini = it["emision"].isoformat()
        fin = min(it["venc"], d1).isoformat()
        if ini >= fin:
            continue
        porVentana.setdefault((ini, fin), []).append(it["ticker"])

    out, pedidos = {}, 0
    for (ini, fin), tickers in sorted(porVentana.items()):
        for i in range(0, len(tickers), MAX_TICKERS_SERIES):
            lote = tickers[i:i + MAX_TICKERS_SERIES]
            filas = None
            for espera in (0, 15, 45):
                if espera:
                    print(f"    reintentando en {espera}s...", file=sys.stderr)
                    time.sleep(espera)
                try:
                    filas = cli.series(lote, ["tea"], moneda="ars",
                                       fecha_inicial=ini, fecha_final=fin)
                    break
                except Error1816 as e:
                    ultimo = e
            pedidos += 1
            if filas is None:
                print(f"AVISO: 1816 falló en {lote[:3]}... ({ultimo})", file=sys.stderr)
                continue
            for f in filas:
                v = f.get("tea")
                tk = f.get("ticker")
                fecha = str(f.get("fecha", ""))[:10]
                if tk and fecha and isinstance(v, (int, float)):
                    out.setdefault(fecha, {})[tk] = round(v * 100, 6)
    print(f"    {pedidos} pedidos a /series")
    return out


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

    ruedasYa = len([k for k in datos if not k.startswith("_")])
    print(f"Insumos del spread · {desde} a {hasta}"
          + (f" · {ruedasYa} ruedas ya guardadas" if ruedasYa else ""))

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

    cli = Cliente1816()
    print("Universo histórico de 1816 (incluye vencidos)...")
    iLecap = universo(cli, CURVAS_LECAP, faltan[0], faltan[-1])
    iDL = universo(cli, CURVAS_DL, faltan[0], faltan[-1])
    print(f"  {len(iLecap)} lecaps + {len(iDL)} dólar linked vivos en el rango")

    print("Tasas de 1816...")
    tLecap = series_tea_universo(cli, iLecap, faltan[0], faltan[-1])
    tDL = series_tea_universo(cli, iDL, faltan[0], faltan[-1])
    print(f"  lecaps: {len(tLecap)} ruedas · dólar linked: {len(tDL)} ruedas")

    # Vencimientos de todo lo usado: el frontend los necesita para armar las curvas y los de los
    # instrumentos vencidos ya no están en Instrumentos.xlsx.
    vencs = dict(datos.get("_venc") or {})
    for it in iLecap + iDL:
        vencs[it["ticker"]] = it["venc"].isoformat()

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

    datos["_venc"] = vencs
    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fechas = sorted(f for f in datos if not f.startswith("_"))
    print(f"\n{SALIDA}: +{nuevas} ruedas · {len(datos)} en total "
          f"({fechas[0]} a {fechas[-1]}) · {os.path.getsize(SALIDA) // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
