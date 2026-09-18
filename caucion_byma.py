#!/usr/bin/env python3
"""Índice de Caución BYMA (IDXCAUTIONB): el valor del día y la serie propia en caucion_byma.json.

QUÉ ES. Un índice de BYMA con la tasa de caución a 1 día en pesos, en TNA: el promedio ponderado
por monto (VWAP) de las cauciones a un día de contado inmediato en el segmento de prioridad precio,
calculado en tiempo real durante la rueda. Es la caución BURSÁTIL, que no es la tasa de pases entre
terceros del BCRA (serie 150) que ya está en el informe: ésa es el repo entre bancos y sale con un
día de rezago. Las dos van en la tabla macro, una al lado de la otra.

DE DÓNDE SALE. El servicio público de BYMADATA que alimenta el tablero de open.bymadata.com.ar:
POST .../bymadata/free/getBymaIndexsMep, con el header «Options: dashboard». Devuelve tres índices:
IDXDOLARB (dólar MEP), IDXEXTB (dólar CCL) e IDXCAUTIONB, con último, apertura, máximo, mínimo,
cierre anterior y hora. Su campo `date` NO es confiable: el 17/09/2026 a las 11:00 decía
«2026-09-16» con precios del día. La fecha la pone este script.

POR QUÉ LA SERIE ES PROPIA. BYMA no publica la historia del índice (pedido del usuario el
17/09/2026: empezar a guardarla). Se buscó, sin éxito, el 17/09/2026:
  · BYMADATA chart/index-historical-series y bnown/seriesHistoricas/indices: reconocen el símbolo
    pero devuelven vacío, mientras que para el Merval sí traen datos.
  · El widget de data-widgets.byma.com.ar del índice dólar: trae sólo MEP y CCL.
  · La API del BCRA: no tiene ninguna serie de caución bursátil (sólo la 150, pases entre terceros).
  · Rava («CAUCION 1D»): tiene descarga, pero desde el 18/05/2026, con huecos, y es el ÚLTIMO
    operado, no el VWAP: su cierre del 16/09 fue 19,0 contra 20,27 del índice. No es la misma serie.

CUÁNDO SE GUARDA. Sólo en día de rueda (calendario de ruedas: los puentes cuentan, el 31/12 no) y
desde las 17:05, cuando la sesión de contado inmediato ya cerró y el último valor es el del día. Una
corrida antes de esa hora no escribe nada. Una rueda que falte NO se completa con el «cierre
anterior» de BYMA: después del cierre ese campo ya trae el cierre del día. Correr dos veces
reescribe la misma fecha: no duplica.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

URL = "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/getBymaIndexsMep"
HEADERS = {
    "Content-Type": "application/json",
    "Options": "dashboard",
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://open.bymadata.com.ar",
    "Referer": "https://open.bymadata.com.ar/",
}
SIMBOLO = "IDXCAUTIONB"
SALIDA = Path(__file__).resolve().parent / "caucion_byma.json"
HORA_CIERRE = (17, 5)


def ahora_art():
    return datetime.now(timezone.utc) - timedelta(hours=3)


def vivo(timeout=30):
    """-> {valor, apertura, maximo, minimo, cierreAnterior, hora} del índice ahora. Levanta si falla."""
    try:
        r = requests.post(URL, headers=HEADERS, json={}, timeout=timeout)
    except requests.exceptions.SSLError:
        r = requests.post(URL, headers=HEADERS, json={}, timeout=timeout, verify=False)
    r.raise_for_status()
    x = next((d for d in r.json().get("data", []) if d.get("symbol") == SIMBOLO), None)
    if not x or x.get("price") is None:
        raise ValueError(f"{SIMBOLO} no vino en la respuesta de BYMADATA")
    r2 = lambda v: None if v is None else round(float(v), 2)      # noqa: E731
    # Los precios vienen en float32 (20.579999923706055): se redondean a dos decimales, que es la
    # precisión con la que BYMA publica la tasa.
    return {"valor": r2(x["price"]), "apertura": r2(x.get("openingPrice")),
            "maximo": r2(x.get("highestPrice")), "minimo": r2(x.get("lowestPrice")),
            "cierreAnterior": r2(x.get("previousClosingPrice")), "hora": x.get("time")}


def leer():
    if SALIDA.exists():
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    return {"nombre": "Índice de Caución BYMA · 1 día",
            "fuente": "BYMADATA (IDXCAUTIONB), serie armada por el repo desde el 17/09/2026",
            "unidad": "% TNA", "filas": []}


def filas(serie=None):
    """-> [(fecha ISO, valor)] ordenadas, para _agregar_periodos y las variaciones."""
    serie = serie or leer()
    return sorted((f["fecha"], f["valor"]) for f in serie["filas"])


def registrar():
    import armar_informe as ai                                  # import tardío: evita el ciclo
    t = ahora_art()
    hoy = t.date()
    fer = ai.feriados(hoy.year, puentes=False)
    if not ai.es_habil(hoy, fer):
        print(f"{hoy} no es rueda: no se guarda nada")
        return False
    if (t.hour, t.minute) < HORA_CIERRE:
        print(f"{t:%H:%M} ART: la rueda no cerró, no se guarda nada")
        return False
    v = vivo()
    serie = leer()
    por_fecha = {f["fecha"]: f for f in serie["filas"]}
    por_fecha[hoy.isoformat()] = {"fecha": hoy.isoformat(), "valor": v["valor"],
                                  "apertura": v["apertura"], "maximo": v["maximo"],
                                  "minimo": v["minimo"], "hora": v["hora"]}
    # NO se completa la rueda anterior con el «cierre anterior» de BYMA: después del cierre ese
    # campo ya trae el cierre de HOY (verificado el 17/09/2026), así que rellenaría un hueco con el
    # dato equivocado. Un día que falte queda faltando.
    serie["filas"] = [por_fecha[k] for k in sorted(por_fecha)]
    SALIDA.write_text(json.dumps(serie, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"caución BYMA {hoy}: {v['valor']} (cierre anterior {v['cierreAnterior']}, "
          f"{len(serie['filas'])} ruedas guardadas)")
    return True


if __name__ == "__main__":
    registrar()
