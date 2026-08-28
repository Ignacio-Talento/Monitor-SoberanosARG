#!/usr/bin/env python3
"""Extrae los datos duros de la rueda para el informe diario.

QUÉ HACE Y QUÉ NO. Este script NO redacta el informe: junta los números y los deja en un JSON
versionado. El análisis —qué se movió, qué vale la pena mirar, qué trade aparece— lo escribe
después Claude leyendo este archivo. La división es a propósito:

  - los números tienen que ser reproducibles y auditables, así que salen de un script determinístico
    que queda commiteado junto a su resultado;
  - el análisis cambia todas las semanas según lo que pase en el mercado, y codificar reglas del
    tipo "si el spread supera X avisá" envejece mal y termina avisando de lo que ya no importa.

Guardar el JSON además deja archivo: se puede releer el informe de cualquier rueda, y comparar el
dataset de hoy contra el de hace un mes sin volver a gastar créditos de 1816.

POR QUÉ CORRE EN GITHUB ACTIONS. La API key de 1816 vive como Secret del repo y no está en ninguna
máquina local. El proxy de producción tampoco sirve como atajo: está detrás de Cloudflare Access y
devuelve 302 al login cuando no hay sesión de navegador.

QUÉ PIDE Y CUÁNTO CUESTA. Una sola llamada a /series por lote de 10 tickers, con dos ruedas —la de
hoy y la anterior— y cuatro campos. 1816 cobra tickers x campos x días, así que son unos 1.500
créditos por corrida: contra los ~21.000 que consume un día de monitor abierto, es marginal.

Se piden DOS ruedas y no una porque el informe es sobre la variación, y la rueda anterior tiene que
venir de la misma fuente que la de hoy. Sacar el precio de ayer de historicos.xlsx y la tasa de
1816 mezclaría convenciones —el Excel guarda precio dirty en la moneda de cada hoja— y las
variaciones saldrían con ruido que no es del mercado.

TIPO DE INFORME. El script decide solo si además del diario corresponde el semanal (viernes o
última rueda de la semana) y el mensual (última rueda hábil del mes), mirando el calendario de
feriados de Argentina. No hace falta un cron por tipo: corre todos los días hábiles y avisa en
`tipos` qué cierres caen hoy.

SALIDA: informes/datos_AAAA-MM-DD.json
"""
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

import requests

from actualizar_historicos import cliente_1816, hoy_art, leer_tickers

DIR_INFORMES = Path("informes")

# Campos que se le piden a 1816. Son los mismos cuatro que pide el monitor para los instrumentos
# sin cronograma cargado (CAMPOS_IND en functions/api/precios.js), así que el informe y la pantalla
# hablan de los mismos números.
CAMPOS = ["precioDirty", "tea", "durationMod", "paridad"]

# Hoja de Instrumentos.xlsx -> familia del informe. Son las que pidió el usuario, con el nombre con
# el que se las nombra en la mesa. LECAPS y TASA FIJA van juntas: es una sola curva en pesos a tasa
# fija, y separarlas parte el tramo corto del largo del mismo instrumento económico.
FAMILIAS = {
    "LECAPS": "LECAPs y tasa fija",
    "TASA FIJA": "LECAPs y tasa fija",
    "CER": "CER",
    "TAMAR": "TAMAR",
    "Duales": "Duales",
    "USD Linked": "Dólar linked",
    "USD Bonares": "Bonares",
    "USD Globales": "Globales",
    "USD Bopreales": "BOPREALes",
    "ON USD": "ONs ley local",
    "ONs": "ONs ley NY",
    "Subsoberanos": "Subsoberanos",
}

# La métrica que se mira en cada familia además del precio. En pesos a tasa fija lo que se negocia
# es la tasa —el precio de una LECAP sube todos los días por el mero paso del tiempo, así que su
# variación de precio no dice casi nada—; en hard dollar se miran las dos, precio y rendimiento.
METRICA = {
    "LECAPs y tasa fija": "TEA",
    "CER": "TIR real",
    "TAMAR": "TEA",
    "Duales": "TEA",
    "Dólar linked": "TIR",
    "Bonares": "TIR",
    "Globales": "TIR",
    "BOPREALes": "TIR",
    "ONs ley local": "TIR",
    "ONs ley NY": "TIR",
    "Subsoberanos": "TIR",
}

FERIADOS_API = "https://api.argentinadatos.com/v1/feriados/{anio}"


def feriados(anio):
    """Feriados nacionales del año, como set de date. Si la API no responde, se sigue sin ellos.

    Sólo afecta a la detección de cierre de mes y de semana: sin feriados, el informe mensual podría
    salir un día tarde. Se avisa en el JSON en vez de abortar la corrida entera por eso.
    """
    try:
        r = requests.get(FERIADOS_API.format(anio=anio), timeout=15)
        r.raise_for_status()
        return {date.fromisoformat(f["fecha"]) for f in r.json()}
    except Exception as e:                                    # noqa: BLE001
        print(f"AVISO: no se pudieron leer los feriados de {anio} ({e})")
        return None


def es_habil(d, fer):
    return d.weekday() < 5 and (fer is None or d not in fer)


def proxima_habil(d, fer):
    x = d + timedelta(days=1)
    while not es_habil(x, fer):
        x += timedelta(days=1)
    return x


def tipos_de_cierre(d, fer):
    """Qué cierres caen en esta rueda: siempre 'diario', más 'semanal' y/o 'mensual'.

    Se define por la PRÓXIMA rueda hábil y no por el día de la semana o el número del día: un
    viernes feriado no es cierre semanal, y el 31 puede caer domingo. Mirando hacia adelante, la
    última rueda de la semana es aquella cuya siguiente hábil ya cayó en otra semana, y la última
    del mes, aquella cuya siguiente hábil ya cambió de mes.
    """
    tipos = ["diario"]
    sig = proxima_habil(d, fer)
    if sig.isocalendar()[:2] != d.isocalendar()[:2]:
        tipos.append("semanal")
    if (sig.year, sig.month) != (d.year, d.month):
        tipos.append("mensual")
    return tipos


def rueda_anterior_habil(d, fer):
    x = d - timedelta(days=1)
    while not es_habil(x, fer):
        x -= timedelta(days=1)
    return x


def pedir_series(cli, items, desde, hasta):
    """Indicadores de cada instrumento en el rango, agrupados por moneda.

    Se agrupa por moneda porque 1816 la toma como parámetro de la request, no del ticker: pedir un
    global en 'ars' devuelve el precio en pesos y arruinaría la comparación contra su propia serie.
    """
    por_moneda = defaultdict(list)
    for it in items:
        if it["t1816"] and it["moneda"]:
            por_moneda[it["moneda"]].append(it["t1816"])

    datos = defaultdict(dict)          # ticker1816 -> fecha -> {campo: valor}
    for moneda, tickers in por_moneda.items():
        for i in range(0, len(tickers), 10):        # /series topea en 10 tickers por request
            lote = tickers[i:i + 10]
            try:
                filas = cli.series(lote, CAMPOS, moneda=moneda,
                                   fecha_inicial=desde, fecha_final=hasta)
            except Exception as e:                            # noqa: BLE001
                print(f"AVISO: lote {lote} en {moneda} falló ({e})")
                continue
            for f in filas:
                tk, fe = f.get("ticker"), f.get("fecha")
                if tk and fe:
                    datos[tk][fe] = {c: f.get(c) for c in CAMPOS}
    return datos


def variacion(hoy, ayer):
    if hoy is None or ayer in (None, 0):
        return None
    return round((hoy / ayer - 1) * 100, 3)


def delta(hoy, ayer):
    if hoy is None or ayer is None:
        return None
    return round(hoy - ayer, 3)


def resumir(valores):
    """Mediana, mínimo y máximo de una lista que puede venir con huecos."""
    v = [x for x in valores if x is not None]
    if not v:
        return None
    return {"mediana": round(median(v), 3), "min": round(min(v), 3),
            "max": round(max(v), 3), "n": len(v)}


def main():
    cli = cliente_1816()
    if cli is None:
        print("ERROR: sin cliente de 1816. Hace falta el secret API_1816_KEY.")
        return 1

    hoy = hoy_art().date()
    fer = feriados(hoy.year)
    if not es_habil(hoy, fer):
        print(f"{hoy} no es rueda hábil; no se arma informe.")
        return 0

    ayer = rueda_anterior_habil(hoy, fer)
    items = leer_tickers()
    print(f"Universo: {len(items)} instrumentos · ruedas {ayer} y {hoy}")

    datos = pedir_series(cli, items, ayer.isoformat(), hoy.isoformat())
    print(f"1816 devolvió datos de {len(datos)} tickers")

    instrumentos, por_familia = [], defaultdict(list)
    sin_dato = []
    for it in items:
        fam = FAMILIAS.get(it["hoja"])
        if not fam or not it["t1816"]:
            continue
        serie = datos.get(it["t1816"], {})
        h = serie.get(hoy.isoformat())
        a = serie.get(ayer.isoformat())
        if not h:
            sin_dato.append(it["eco"])
            continue
        reg = {
            "ticker": it["eco"],
            "familia": fam,
            "moneda": it["moneda"],
            "precio": h.get("precioDirty"),
            "tea": h.get("tea"),
            "durationMod": h.get("durationMod"),
            "paridad": h.get("paridad"),
            # Las dos varas: el % de precio, que es lo que ve el tenedor, y los puntos de tasa, que
            # es lo que se negocia. Para una LECAP la primera es casi siempre positiva por el mero
            # devengamiento, así que sin la segunda el informe diría que "todo subió" todos los días.
            "varPrecio": variacion(h.get("precioDirty"), (a or {}).get("precioDirty")),
            "varTasa": delta(h.get("tea"), (a or {}).get("tea")),
            "varParidad": delta(h.get("paridad"), (a or {}).get("paridad")),
            "conAyer": bool(a),
        }
        instrumentos.append(reg)
        por_familia[fam].append(reg)

    resumen = {}
    for fam, regs in por_familia.items():
        # La MEDIANA y no el promedio: en cada familia hay siempre algún instrumento ilíquido cuyo
        # precio quedó de hace tres ruedas y salta 4% cuando por fin opera. Con promedio, ese solo
        # dato define el signo de toda la familia.
        rp = resumir([r["varPrecio"] for r in regs])
        rt = resumir([r["varTasa"] for r in regs])
        con_var = [r for r in regs if r["varPrecio"] is not None]
        resumen[fam] = {
            "instrumentos": len(regs),
            "conVariacion": len(con_var),
            "metrica": METRICA.get(fam),
            "precio": rp,
            "tasa": rt,
            "teaMediana": resumir([r["tea"] for r in regs]),
            "mejor": max(con_var, key=lambda r: r["varPrecio"])["ticker"] if con_var else None,
            "peor": min(con_var, key=lambda r: r["varPrecio"])["ticker"] if con_var else None,
        }

    salida = {
        "fecha": hoy.isoformat(),
        "ruedaAnterior": ayer.isoformat(),
        "tipos": tipos_de_cierre(hoy, fer),
        "generado": datetime.now().astimezone().isoformat(timespec="seconds"),
        "feriadosLeidos": fer is not None,
        "universo": len(items),
        "sinDato": sin_dato,
        "campos": CAMPOS,
        "resumen": resumen,
        "instrumentos": instrumentos,
    }

    DIR_INFORMES.mkdir(exist_ok=True)
    ruta = DIR_INFORMES / f"datos_{hoy.isoformat()}.json"
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Escrito {ruta} · {len(instrumentos)} instrumentos · cierres: {', '.join(salida['tipos'])}")
    if sin_dato:
        print(f"Sin dato de hoy ({len(sin_dato)}): {', '.join(sin_dato[:15])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
