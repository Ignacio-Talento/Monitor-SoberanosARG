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
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import requests

from openpyxl import load_workbook

from actualizar_historicos import (INSTRUMENTOS_FILE, cliente_1816, hoy_art,
                                    leer_tickers)
from macro_informe import _agregar_periodos, datos_macro

DIR_INFORMES = Path("informes")
SERIES_MERCADO = Path(__file__).resolve().parent / "series_mercado.json"
MACRO_SERIES = Path(__file__).resolve().parent / "macro_series.json"

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
    # Las ONs NO se clasifican por hoja: ver mapa_ley_ons(). Se dejan acá para que el instrumento
    # entre al universo, pero la familia se pisa después con la ley que declara el Excel.
    "ON USD": "ONs",
    "ONs": "ONs",
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
    "ONs": "TIR",                 # sólo si alguna quedó sin ley declarada
    "Subsoberanos": "TIR",
}

FERIADOS_API = "https://api.argentinadatos.com/v1/feriados/{anio}"

# El runner de GitHub corre en UTC. Sin esto, datetime.now() daba las 17:08 para una corrida de las
# 14:08 de Buenos Aires, y el informe del 2026-08-28 salió diciendo "tomados a las 17:08 con el
# mercado ya cerrado" cuando el mercado estaba abierto y los precios eran intradía. Con el tzinfo
# puesto el offset viaja en el propio string (-03:00) y no hay forma de leerlo mal.
ART = timezone(timedelta(hours=-3))


# Tipo de pata en Instrumentos.xlsx -> sufijo del ticker en 1816. Verificado contra el catálogo el
# 2026-08-28: para TXMD8 existen "TXMD8 @CER" y "TXMD8 @TAMAR", y cada uno devuelve la tasa de SU
# pata —6,18% real y 40,71% nominal—, mientras el ticker pelado devuelve la de la pata que manda.
SUFIJO_PATA = {"CER": "@CER", "TAMAR": "@TAMAR", "LECAP": "@Tasa Fija", "LINKED": "@USD-L"}


def patas_duales():
    """[(ticker, tipo, ticker1816)] con las patas de cada dual, leídas de la hoja Duales.

    POR QUÉ SE PUEDEN PEDIR. Un dual paga el máximo entre sus dos patas, así que su TIR "entera" es
    la de la pata que domina y la otra no aparece por ningún lado: con un solo número no se puede
    ubicar el instrumento ni en la curva CER ni en la TAMAR. 1816 publica las dos por separado, con
    un ticker por pata, y eso es exactamente lo que hace falta para dibujarlas.

    LA PATA DOMINADA DEVUELVE NULL, y no es un error sino información: 1816 sólo calcula
    indicadores para la pata que va a pagar. "TTS26 @Tasa Fija" viene con todo en null porque hoy
    manda la TAMAR. Así se identifica la pata in the money sin calcular nada.

    El tipo sale del Excel y no del catálogo de 1816: la hoja ya tiene una fila por pata con su
    tipo, es gratis y no gasta una consulta por dual.
    """
    wb = load_workbook(INSTRUMENTOS_FILE, data_only=True)
    if "Duales" not in wb.sheetnames:
        return []
    filas = list(wb["Duales"].iter_rows(values_only=True))
    out, vistos = [], set()
    for f in filas:
        if not f or not f[0] or str(f[0]).strip() in ("Ticker", "None"):
            continue
        tk, tipo = str(f[0]).strip(), str(f[1] or "").strip().upper()
        suf = SUFIJO_PATA.get(tipo)
        if not suf or (tk, tipo) in vistos:
            continue
        vistos.add((tk, tipo))
        out.append((tk, tipo, f"{tk} {suf}"))
    return out


def mapa_ons():
    """ticker -> {"ley": "local"|"ny", "moneda": "mep"|"ccl"}, leído de la hoja ONs.

    HACE FALTA PORQUE LA HOJA NO ES LA LEY. Al armar el informe se supuso que la hoja "ON USD" eran
    las ONs de ley local y la hoja "ONs" las de ley NY, y las dos cosas eran falsas:

      · "ON USD" no es un listado de instrumentos sino una tabla de CRONOGRAMA DE FLUJOS —columnas
        Fecha, Valor Residual, Cupón, Renta, Amortización—. Sus 56 filas son cupones de apenas
        cuatro tickers, que además ya figuran en la otra hoja. El informe del 28/08/2026 reportó
        por eso una familia "ONs ley local" de cuatro instrumentos.
      · "ONs" tiene las dos leyes juntas y las distingue en su columna Ley: 61 local y 36 ny. Todas
        salieron reportadas como ley NY.

    Con la ley leída del Excel, un instrumento va a su familia venga de la hoja que venga.

    La moneda se lee acá SÓLO para poder controlarla contra la que ya resolvió leer_tickers() y para
    informarla en el resumen. La regla es la misma que aplica el monitor en monedaDeON() (ons.html)
    y que actualizar_historicos.py ya implementa en moneda_on(): las de ley NY van todas al CCL, y
    las locales en la moneda en la que PAGAN, que es lo que dice la columna Divisa, scrapeada del
    domicilio de pago. Hoy son 43 al CCL —36 de ley NY y 7 locales que pagan en cable— y 54 al MEP.
    """
    wb = load_workbook(INSTRUMENTOS_FILE, data_only=True)
    if "ONs" not in wb.sheetnames:
        return {}
    ws = wb["ONs"]
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        return {}
    cab = [str(c).strip().lower() if c else "" for c in filas[0]]
    if "ticker" not in cab or "ley" not in cab:
        print("AVISO: la hoja ONs no tiene columnas Ticker y Ley; no se separan por legislación")
        return {}
    i_tk, i_ley = cab.index("ticker"), cab.index("ley")
    i_div = cab.index("divisa") if "divisa" in cab else None
    out = {}
    for f in filas[1:]:
        if not f or not f[i_tk] or not f[i_ley]:
            continue
        ley = str(f[i_ley]).strip().lower()
        div = str(f[i_div] or "").strip().upper() if i_div is not None else ""
        out[str(f[i_tk]).strip()] = {
            "ley": ley,
            "moneda": "ccl" if (ley == "ny" or div == "CCL") else "mep",
        }
    return out


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


def ultima_rueda_de_periodo_anterior(d, fer, periodo):
    """Última rueda hábil de la semana o del mes ANTERIOR a la de d.

    Es contra esta rueda que se mide el cierre semanal y el mensual. Se busca hacia atrás desde el
    primer día del período de d, saltando fines de semana y feriados: para el viernes 28/08/2026 la
    referencia semanal es el viernes 21 y la mensual, el jueves 31/07.
    """
    if periodo == "semanal":
        x = d - timedelta(days=d.weekday() + 1)          # domingo anterior al lunes de esta semana
    else:
        x = d.replace(day=1) - timedelta(days=1)         # último día del mes anterior
    while not es_habil(x, fer):
        x -= timedelta(days=1)
    return x


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


def pct(x):
    """1816 devuelve tea y paridad como FRACCIÓN: 0,2672 es una TEA de 26,72%.

    Verificado el 2026-08-28 contra la pantalla: S30S6 vino con tea 0,26726 y la solapa Sintéticos
    mostraba 26,68% para el mismo instrumento. Sin esta conversión el informe reportaba tasas cien
    veces más chicas, y peor: el redondeo a tres decimales dejaba una TEA del 26,7% sin el decimal
    que importa, y una variación de -1,5 pp aparecía como "-0,015 pp", o sea como si no se hubiera
    movido nada.

    durationMod NO se toca: viene en años y así se usa.
    """
    return None if x is None else x * 100


def variacion(hoy, ayer):
    if hoy is None or ayer in (None, 0):
        return None
    return round((hoy / ayer - 1) * 100, 3)


def delta(hoy, ayer):
    if hoy is None or ayer is None:
        return None
    return round(hoy - ayer, 3)


def redondear(x, n=3):
    return None if x is None else round(x, n)


def resumir(valores):
    """Mediana, mínimo y máximo de una lista que puede venir con huecos."""
    v = [x for x in valores if x is not None]
    if not v:
        return None
    return {"mediana": round(median(v), 3), "min": round(min(v), 3),
            "max": round(max(v), 3), "n": len(v)}


def datos_mercado(referencias):
    """Margen sobre TAMAR de los Duales y rendimiento de los Bonares AO27/AO28 contra cable.

    NO SE PIDE A 1816 ACÁ. Las dos series ya las bajó series_mercado.py, que corre antes en el
    mismo job y guarda la historia completa en series_mercado.json; pedirlas de nuevo sería pagar
    créditos por lo que está en disco y, peor, dejar dos números distintos circulando —el del
    informe y el del gráfico— cuando el mercado se movió entre una llamada y la otra.

    Se informa `hasta` de cada bloque justamente por eso: si series_mercado.py falló, este bloque
    trae el cierre de ayer, y el PDF tiene que poder decirlo en vez de presentarlo como el del día.
    """
    out = {"disponible": False, "bloques": {}}
    if not SERIES_MERCADO.exists():
        out["motivo"] = "series_mercado.json no existe todavía; lo genera series_mercado.py"
        return out
    try:
        d = json.loads(SERIES_MERCADO.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        out["motivo"] = f"series_mercado.json ilegible: {e}"
        return out

    for clave in ("margenTamar", "bonares"):
        bq = d.get(clave) or {}
        if not bq.get("f"):
            continue
        series = {}
        for tk, vals in (bq.get("series") or {}).items():
            # Un None es una rueda sin operar, no un cero: se saltea, y por eso la "rueda anterior"
            # de un bono ilíquido puede no ser la de ayer. La fecha va siempre al lado del número.
            filas = [(f, v) for f, v in zip(bq["f"], vals) if v is not None]
            if not filas:
                continue
            filas.sort(reverse=True)
            f, v = filas[0]
            reg = {"fecha": f, "valor": v}
            if len(filas) > 1:
                reg["previo"] = {"fecha": filas[1][0], "valor": filas[1][1]}
                reg["variacion"] = round(v - filas[1][1], 4)
            _agregar_periodos(reg, filas, v, referencias)
            series[tk] = reg
        if series:
            out["bloques"][clave] = {
                "nombre": bq.get("nombre"), "unidad": bq.get("unidad"),
                "fuente": bq.get("fuente"), "hasta": bq.get("hasta"), "series": series}

    out["disponible"] = bool(out["bloques"])
    out["generado"] = d.get("generado")
    if d.get("fallos"):
        out["fallos"] = d["fallos"]
    return out


def datos_legislacion(referencias, anio):
    """Diferencial de precio GD30/AL30 con su historia, para contextualizar la tabla de legislación.

    La tabla del informe ya trae el "canje de precio" AL30/GD30 del día, que es este mismo número.
    Lo que agrega este bloque es DÓNDE está: la mediana de 10 ruedas —la serie diaria del GD30 en
    MEP tiene prints sueltos—, sus extremos del año y los meses desde 2024 en que la mediana estuvo
    en un nivel parecido, con el riesgo país que había entonces. Eso es lo que permite contar la
    tesis del semanal de 1816 del 10/09/2026 —"el riesgo país está demasiado bajo o el spread de
    legislación demasiado alto"— y también su límite: el mismo diferencial convivió con riesgos
    país de 500 y de 1.500, así que la relación es floja.

    Sale de series_mercado.json (bloque "legislacion") y de macro_series.json (riesgo país). No
    pide nada a 1816.
    """
    out = {"disponible": False}
    try:
        bq = json.loads(SERIES_MERCADO.read_text(encoding="utf-8"))["legislacion"]
        crudo = [(f, v) for f, v in zip(bq["f"], bq["series"]["GD30/AL30"]) if v is not None]
        S = json.loads(MACRO_SERIES.read_text(encoding="utf-8"))["series"]["riesgoPais"]
        rp = dict(zip(S["f"], S["v"]))
    except Exception as e:                                        # noqa: BLE001
        out["motivo"] = f"sin diferencial de legislación: {e}"
        return out
    if len(crudo) < 10:
        out["motivo"] = "menos de 10 ruedas de diferencial"
        return out

    med = []
    for i in range(9, len(crudo)):
        v = sorted(x[1] for x in crudo[i - 9:i + 1])
        med.append((crudo[i][0], (v[4] + v[5]) / 2))

    filas = sorted(med, reverse=True)
    f, v = filas[0]
    reg = {"fecha": f, "valor": round(v, 3), "crudo": round(crudo[-1][1], 3),
           "previo": {"fecha": filas[1][0], "valor": round(filas[1][1], 3)},
           "variacion": round(v - filas[1][1], 3)}
    _agregar_periodos(reg, med, v, referencias)
    del_anio = [(ff, vv) for ff, vv in med if ff >= f"{anio}-01-01"]
    if del_anio:
        fmin, vmin = min(del_anio, key=lambda x: x[1])
        fmax, vmax = max(del_anio, key=lambda x: x[1])
        reg["minAnio"] = {"fecha": fmin, "valor": round(vmin, 3)}
        reg["maxAnio"] = {"fecha": fmax, "valor": round(vmax, 3)}

    # Meses con la mediana a ±0,5 pp de la de hoy, con el riesgo país promedio de ese mes. El mes
    # en curso queda afuera: compararse contra uno mismo no dice nada.
    por_mes, rp_mes = {}, {}
    for ff, vv in med:
        por_mes.setdefault(ff[:7], []).append(vv)
    for ff, vv in rp.items():
        if ff >= "2024-01-01":
            rp_mes.setdefault(ff[:7], []).append(vv)
    similares = []
    for mes in sorted(por_mes):
        if mes == f[:7] or mes not in rp_mes:
            continue
        m = sum(por_mes[mes]) / len(por_mes[mes])
        if abs(m - v) <= 0.5:
            similares.append({"mes": mes, "diferencial": round(m, 2),
                              "riesgoPais": round(sum(rp_mes[mes]) / len(rp_mes[mes]))})
    reg["mesesSimilares"] = similares
    rph = sorted((ff, vv) for ff, vv in rp.items() if ff <= f)
    reg["riesgoPais"] = {"fecha": rph[-1][0], "valor": rph[-1][1]} if rph else None
    out.update({"disponible": True, "fuente": bq.get("fuente"), "hasta": bq.get("hasta"),
                "mediana10": reg})
    return out


def datos_embig(referencias, anio):
    """EMBIG de Argentina, de Latinoamérica y la distancia entre los dos, para el bloque macro.

    Sale de macro_series.json, que arma el job de Series Macro a partir del BCRP; no se pide acá.
    Es EMBIG y no el EMBI+ de la fila de riesgo país: son índices distintos y difieren en unos
    puntos. Se usa porque es el único que tiene su par regional público.

    LO QUE IMPORTA ES LA BRECHA, no los dos niveles. Con escalas tan distintas —Argentina rinde
    casi el doble que la región— un movimiento de los dos puede ser entero regional, y sólo la
    diferencia dice cuánto es propio. Se calcula en las fechas que tienen las DOS series: restar
    puntas de días distintos daría una brecha que no existió. Trae además la del primer dato del
    año, porque la lectura del año —toda la compresión de 2026 fue regional— sale de ahí.
    """
    out = {"disponible": False}
    try:
        S = json.loads(MACRO_SERIES.read_text(encoding="utf-8"))["series"]
        a = dict(zip(S["embigArg"]["f"], S["embigArg"]["v"]))
        l = dict(zip(S["embigLatam"]["f"], S["embigLatam"]["v"]))
    except Exception as e:                                        # noqa: BLE001
        out["motivo"] = f"sin EMBIG en macro_series.json: {e}"
        return out

    def registro(filas):
        filas = sorted(filas, reverse=True)
        f, v = filas[0]
        reg = {"fecha": f, "valor": v}
        if len(filas) > 1:
            reg["previo"] = {"fecha": filas[1][0], "valor": filas[1][1]}
            reg["variacion"] = round(v - filas[1][1], 2)
        _agregar_periodos(reg, filas, v, referencias)
        return reg

    comunes = sorted(set(a) & set(l))
    if not comunes:
        out["motivo"] = "las dos series no tienen fechas en común"
        return out
    brecha = [(f, a[f] - l[f]) for f in comunes]
    out.update({
        "disponible": True,
        "fuente": "BCRP · EMBIG de J.P. Morgan (series PD04710XD y PD04708XD)",
        "hasta": comunes[-1],
        "argentina": registro([(f, a[f]) for f in comunes]),
        "latam": registro([(f, l[f]) for f in comunes]),
        "brecha": registro(brecha),
    })
    # Punta del año y extremos, de la brecha Y del nivel argentino. Son lo que sostiene la lectura:
    # el 10/09/2026 el nivel había rebotado de 403 a 496 desde el piso de julio mientras la región
    # no se movía, así que ese rebote era entero propio, y en el año la brecha estaba donde empezó.
    for clave, serie in (("brecha", brecha), ("argentina", [(f, a[f]) for f in comunes])):
        del_anio = [(f, v) for f, v in serie if f >= f"{anio}-01-01"]
        if not del_anio:
            continue
        f0, v0 = del_anio[0]
        reg = out[clave]
        reg["inicioAnio"] = {"fecha": f0, "valor": v0}
        reg["variacionAnio"] = round(reg["valor"] - v0, 2)
        fmin, vmin = min(del_anio, key=lambda x: x[1])
        fmax, vmax = max(del_anio, key=lambda x: x[1])
        reg["minAnio"] = {"fecha": fmin, "valor": vmin}
        reg["maxAnio"] = {"fecha": fmax, "valor": vmax}
    # El BCRP REPITE el último dato los feriados de EE.UU., en que el índice no se calcula —el
    # 07/09/2026 fue Labor Day—. Dos ruedas iguales no son necesariamente un mercado quieto, y el
    # informe tiene que poder decir cuál fue el último dato que cambió.
    ult_cambio = comunes[-1]
    for i in range(len(comunes) - 1, 0, -1):
        f, fa = comunes[i], comunes[i - 1]
        if a[f] != a[fa] or l[f] != l[fa]:
            ult_cambio = f
            break
    out["ultimoCambio"] = ult_cambio
    return out


# ── SINTÉTICOS ───────────────────────────────────────────────────────────────────────────────────
# La misma comparación que la solapa Sintéticos, con las mismas fórmulas y los mismos supuestos:
# LECAP contra sintético en pesos (bono DL + venta de futuro) y bono DL contra sintético dólar linked
# (LECAP + compra de futuro). Es una SEGUNDA implementación de filasSinteticos() de sinteticos.html,
# que es justo lo que actualizar_spreads.py evita a propósito; acá no hay otra salida, porque el
# informe se arma en el runner y la solapa vive detrás de Cloudflare Access. Para que no diverjan
# en silencio, los contratos se leen del propio HTML y cada supuesto lleva al lado el nombre de la
# función de la solapa que replica. Si se toca una, se toca la otra.

SINTETICOS_HTML = Path(__file__).resolve().parent / "sinteticos.html"
SPREADS_SINTETICOS = Path(__file__).resolve().parent / "spreads_sinteticos.json"
CEM_API = "https://apicem.matbarofex.com.ar/api/v2"
CABECERAS_CEM = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                 "Referer": "https://cem.matbarofex.com.ar/"}
# `comisiones` de la solapa: aranceles por defecto de la tabla del back-office (grupos "NO REG").
COMISIONES_SINT = {"lecap": 0.5, "dl": 0.5, "fut": 0.2}
# Menos de 10 días no entra, igual que en la solapa: anualizar sobre tan poco plazo convierte
# cualquier diferencia chica en decenas de puntos.
DIAS_MIN_SINT = 10
# PLAZOS_FIJOS de la solapa: el spread interpolado a plazo constante, que es lo único comparable
# en el tiempo —un contrato puntual se acorta todos los días—.
PLAZOS_SINT = (30, 60, 90, 180)
MES_FUT = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}


def contratos_solapa():
    """FUTUROS_TICKERS de sinteticos.html, leídos del archivo para que las tablas sean las mismas."""
    import re
    try:
        s = SINTETICOS_HTML.read_text(encoding="utf-8")
        m = re.search(r"const FUTUROS_TICKERS = \[(.*?)\];", s, re.S)
        tks = re.findall(r"'(DLR/[A-Z]{3}\d{2})'", m.group(1)) if m else []
        if tks:
            return tks
    except OSError:
        pass
    print("AVISO: no se pudieron leer los contratos de sinteticos.html; se usan los de respaldo")
    return ["DLR/SEP26", "DLR/OCT26", "DLR/NOV26", "DLR/DIC26", "DLR/ENE27", "DLR/FEB27",
            "DLR/MAR27", "DLR/ABR27"]


def venc_contrato(tk):
    """vencContrato() de monitor-core.js: el último día CALENDARIO del mes del contrato."""
    m, a = MES_FUT.get(tk[4:7]), tk[7:9]
    if not m or not a.isdigit():
        return None
    y = 2000 + int(a)
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _ticker_cem(symbol):
    """DLR092026 -> DLR/SEP26; None para opciones y cualquier otra cosa."""
    if not symbol or " " in symbol or not symbol.startswith("DLR"):
        return None
    r = symbol[3:]
    if len(r) != 6 or not r.isdigit() or not 1 <= int(r[:2]) <= 12:
        return None
    mes = [k for k, v in MES_FUT.items() if v == int(r[:2])][0]
    return f"DLR/{mes}{int(r[2:]) % 100:02d}"


def futuros_a3(hoy):
    """Foto de los futuros de la rueda, como /api/futuros de la solapa.

    Primero tick-prices —operación por operación—, que da el ÚLTIMO OPERADO del día con su hora y
    el volumen de la rueda: a las 17:30 el mercado de A3 cerró a las 15:00, así que es el cierre
    operado de hoy. Si tick-prices falla —se cae seguido con 424 "Execution Timeout Expired"—, el
    ajuste de closing-prices, que a esa hora es el de la rueda ANTERIOR. `modo` dice cuál salió.
    """
    ini = datetime(hoy.year, hoy.month, hoy.day, 3, 0, 0, tzinfo=timezone.utc)   # 00:00 ART
    fin = ini + timedelta(days=1, seconds=-1)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    filas, aviso = [], None
    try:
        pagina = 1
        while pagina <= 10:
            r = requests.get(CEM_API + "/tick-prices", headers=CABECERAS_CEM, timeout=45, params={
                "product": "DLR", "from": iso(ini), "to": iso(fin),
                "pageSize": 5000, "page": pagina})
            r.raise_for_status()
            lote = r.json().get("data") or []
            filas += lote
            if len(lote) < 5000:
                break
            pagina += 1
    except Exception as e:                                        # noqa: BLE001
        aviso, filas = f"tick-prices: {e}", []

    previo = {}
    try:
        r = requests.get(CEM_API + "/closing-prices", headers=CABECERAS_CEM, timeout=45, params={
            "product": "DLR", "type": "FUT", "from": (hoy - timedelta(days=7)).isoformat(),
            "to": hoy.isoformat(), "pageSize": 300, "sort": "dateTime", "sortDir": "DESC"})
        r.raise_for_status()
        for x in r.json().get("data") or []:
            tk = _ticker_cem(x.get("symbol"))
            if tk and tk not in previo and (x.get("settlement") or x.get("close")):
                previo[tk] = {"ajuste": float(x.get("settlement") or x.get("close")),
                              "volumen": int(x.get("volume") or 0),
                              "fecha": str(x.get("dateTime"))[:10]}
    except Exception as e:                                        # noqa: BLE001
        aviso = (aviso + " · " if aviso else "") + f"closing-prices: {e}"

    agg = {}
    for x in filas:
        tk = _ticker_cem(x.get("symbol"))
        if not tk:
            continue
        a = agg.setdefault(tk, {"volumen": 0, "operaciones": 0, "ultimo": None, "precio": None})
        a["volumen"] += float(x.get("volume") or 0)
        a["operaciones"] += 1
        if not a["ultimo"] or x["dateTime"] > a["ultimo"]:
            a["ultimo"], a["precio"] = x["dateTime"], float(x["price"])

    out = {}
    if agg:
        modo, rueda = "intradia", hoy.isoformat()
        for tk in set(agg) | set(previo):
            a, p = agg.get(tk), previo.get(tk)
            if a:
                h = datetime.fromisoformat(a["ultimo"].replace("Z", "+00:00")).astimezone(ART)
                out[tk] = {"precio": a["precio"], "volumen": round(a["volumen"]),
                           "operaciones": a["operaciones"], "ultimaOperacion": h.strftime("%H:%M")}
            else:
                # Existe pero no operó hoy: la solapa lo muestra con el ajuste previo y volumen 0,
                # atenuado y con «sin operar». Acá igual.
                out[tk] = {"precio": p["ajuste"], "volumen": 0, "operaciones": 0,
                           "ultimaOperacion": None, "ajusteDel": p["fecha"]}
    else:
        modo = "ajuste"
        rueda = max((p["fecha"] for p in previo.values()), default=None)
        for tk, p in previo.items():
            if p["fecha"] == rueda:
                out[tk] = {"precio": p["ajuste"], "volumen": p["volumen"], "operaciones": None,
                           "ultimaOperacion": None, "ajusteDel": p["fecha"]}
    return out, modo, rueda, aviso


def tc_mayorista(hoy):
    """-> (fecha, A3500) del último dato en o antes de hoy: bcraData.usdHoy de la solapa."""
    try:
        r = requests.get("https://indicadoresbcra.granda-fra.workers.dev/", timeout=40, params={
            "serie": "usd", "desde": (hoy - timedelta(days=15)).isoformat(),
            "hasta": hoy.isoformat()})
        det = (r.json().get("results") or [{}])[0].get("detalle") or []
        pts = sorted((d["fecha"], float(d["valor"])) for d in det
                     if d.get("fecha") and d.get("valor") and d["fecha"] <= hoy.isoformat())
        return pts[-1] if pts else (None, None)
    except Exception as e:                                        # noqa: BLE001
        print(f"AVISO: sin A3500 ({e})")
        return None, None


def vencimientos_excel(hojas=("LECAPS", "USD Linked")):
    """{ticker: date} de la columna de vencimiento de esas hojas, que es de donde lo lee la solapa.

    La columna se llama distinto en cada hoja —"Fecha Vencimiento" y "Fecha Vencimineto", con la
    errata— así que se busca por las dos, igual que el motor.
    """
    wb = load_workbook(INSTRUMENTOS_FILE, data_only=True)
    out = {}
    for h in hojas:
        if h not in wb.sheetnames:
            continue
        filas = list(wb[h].iter_rows(values_only=True))
        cab = next((i for i, f in enumerate(filas) if f and str(f[0]).strip() == "Ticker"), None)
        if cab is None:
            continue
        nombres = [str(c).strip() if c else "" for c in filas[cab]]
        col = next((nombres.index(n) for n in ("Fecha Vencimiento", "Fecha Vencimineto")
                    if n in nombres), None)
        if col is None:
            continue
        for f in filas[cab + 1:]:
            if f and f[0] and str(f[0]).strip() != "Ticker" and isinstance(f[col], datetime):
                out[str(f[0]).strip()] = f[col].date()
    return out


def interpolar_curva(curva, dias):
    """interpolar() de la solapa: lineal en días y SIN extrapolar —fuera de rango, None—."""
    if not curva or dias < curva[0]["dias"] or dias > curva[-1]["dias"]:
        return None
    for a, b in zip(curva, curva[1:]):
        if a["dias"] <= dias <= b["dias"]:
            if a["dias"] == b["dias"]:
                return {"tasa": a["tasa"], "entre": [a["ticker"], b["ticker"]]}
            w = (dias - a["dias"]) / (b["dias"] - a["dias"])
            return {"tasa": a["tasa"] + w * (b["tasa"] - a["tasa"]),
                    "entre": [a["ticker"], b["ticker"]]}
    return None


def con_factor(tasa, k, dias):
    """conFactor() de la solapa: costo sobre el capital aplicado a una tasa ya anualizada."""
    if dias <= 0 or k == 1:
        return tasa
    return ((1 + tasa / 100) * k ** (365 / dias) - 1) * 100


def _k_compra(c):
    return 1 / (1 + c / 100)


def _k_venta(c):
    return 1 - c / 100


def _k_compra_fut(c):
    """Comprar un futuro: se paga F(1+c) y la devaluación SUBE. El sentido es el inverso que en un
    bono, cuya tasa va con 1/P; en la solapa estuvo como 1/(1+c) hasta el 10/09/2026."""
    return 1 + c / 100


def fila_sintetico(tk, precio, dias, tc, c_lecap, c_dl):
    """Una fila de filasSinteticos(): bruto y neto de los dos lados. None si no hay devaluación."""
    dev = (pow(precio / tc, 365 / dias) - 1) * 100
    iL, iD = interpolar_curva(c_lecap, dias), interpolar_curva(c_dl, dias)
    fila = {"devTEA": dev, "lecap": iL, "dl": iD, "pesos": None, "dolar": None}
    if iL and iD:
        c = COMISIONES_SINT
        lecap_n = con_factor(iL["tasa"], _k_compra(c["lecap"]), dias)
        dl_n = con_factor(iD["tasa"], _k_compra(c["dl"]), dias)
        # en pesos: comprar DL + VENDER futuro, contra la LECAP. Positivo = gana la LECAP.
        bruto = ((1 + iD["tasa"] / 100) * (1 + dev / 100) - 1) * 100
        dev_v = con_factor(dev, _k_venta(c["fut"]), dias)
        neto_s = ((1 + dl_n / 100) * (1 + dev_v / 100) - 1) * 100
        fila["pesos"] = {"ref": iL["tasa"], "sint": bruto, "spread": iL["tasa"] - bruto,
                         "neto": lecap_n - neto_s,
                         "gana": "LECAP directa" if lecap_n - neto_s >= 0 else "Sintético en pesos"}
        # dólar linked: comprar LECAP + COMPRAR futuro, contra el bono DL. Positivo = gana el sintético.
        bruto = ((1 + iL["tasa"] / 100) / (1 + dev / 100) - 1) * 100
        dev_c = con_factor(dev, _k_compra_fut(c["fut"]), dias)
        neto_s = ((1 + lecap_n / 100) / (1 + dev_c / 100) - 1) * 100
        fila["dolar"] = {"ref": iD["tasa"], "sint": bruto, "spread": bruto - iD["tasa"],
                         "neto": neto_s - dl_n,
                         "gana": "Sintético DL" if neto_s - dl_n >= 0 else "Bono DL directo"}
    return fila


def _curva_de_tasas(tasas, venc, ref):
    """curvaDesdeTasas() de la solapa: {ticker: tasa} -> [{dias, tasa, ticker}] ordenada."""
    pts = []
    for tk, t in tasas.items():
        v = venc.get(tk)
        if v and t is not None:
            dias = (v - ref).days
            if dias > 0:
                pts.append({"dias": dias, "tasa": t, "ticker": tk})
    return sorted(pts, key=lambda p: p["dias"])


def spread_plazo(rueda, fecha, plazo, venc):
    """spreadPlazoConstante() de la solapa: spread BRUTO interpolado entre contratos a `plazo` días.

    Como en spreadHistorico(), la fecha de la rueda hace de liquidación y los contratos de menos
    de 10 días no entran. Sin extrapolar.
    """
    ref = date.fromisoformat(fecha)
    tc = rueda.get("tc")
    if not tc:
        return None
    cL = _curva_de_tasas(rueda.get("lecap") or {}, venc, ref)
    cD = _curva_de_tasas(rueda.get("dl") or {}, venc, ref)
    pts = []
    for tk, p in (rueda.get("fut") or {}).items():
        v = venc_contrato(tk)
        if not v or not p:
            continue
        dias = (v - ref).days
        if dias < DIAS_MIN_SINT:
            continue
        dev = (pow(p / tc, 365 / dias) - 1) * 100
        iL, iD = interpolar_curva(cL, dias), interpolar_curva(cD, dias)
        if not iL or not iD:
            continue
        sp = iL["tasa"] - ((1 + iD["tasa"] / 100) * (1 + dev / 100) - 1) * 100
        sd = ((1 + iL["tasa"] / 100) / (1 + dev / 100) - 1) * 100 - iD["tasa"]
        pts.append((dias, sp, sd, tk))
    pts.sort()
    if len(pts) < 2 or not pts[0][0] <= plazo <= pts[-1][0]:
        return None
    for a, b in zip(pts, pts[1:]):
        if a[0] <= plazo <= b[0]:
            w = 0 if b[0] == a[0] else (plazo - a[0]) / (b[0] - a[0])
            return {"pesos": a[1] + w * (b[1] - a[1]), "dolar": a[2] + w * (b[2] - a[2]),
                    "entre": [a[3], b[3]]}
    return None


def datos_sinteticos(items, datos, hoy, fer, ayer, referencias):
    """Las dos tablas de la solapa Sintéticos para la rueda de hoy, más el spread a plazo constante
    contra su historia de spreads_sinteticos.json.

    TABLAS: una fila por contrato de la solapa, con la tasa LECAP y la del DL interpoladas al
    vencimiento del futuro sobre las curvas de HOY de 1816 —las mismas tasas del resto del
    informe—, la devaluación implícita del futuro contra el A3500 y los spreads bruto y neto de
    aranceles. Días desde la liquidación T+1, como en la solapa.

    PLAZO CONSTANTE: el spread bruto interpolado a 30/60/90/180 días, que es lo que se puede
    comparar entre ruedas. El de hoy sale de los mismos insumos que las tablas; el histórico, del
    archivo, que usa el AJUSTE de cada rueda y no el último operado —diferencia chica, pero es la
    razón por la que la variación del día trae algo de ruido de método—.
    """
    out = {"disponible": False, "comisiones": COMISIONES_SINT, "diasMinimos": DIAS_MIN_SINT}
    fut, modo, rueda_fut, aviso = futuros_a3(hoy)
    f_tc, tc = tc_mayorista(hoy)
    if not fut or not tc:
        out["motivo"] = f"sin futuros ({aviso})" if not fut else "sin A3500"
        return out

    venc = vencimientos_excel()
    liq = proxima_habil(hoy, fer)
    tasas = {"LECAPS": {}, "USD Linked": {}}
    for it in items:
        if it.get("hoja") in tasas and it.get("t1816"):
            h = datos.get(it["t1816"], {}).get(hoy.isoformat()) or {}
            if h.get("tea") is not None:
                tasas[it["hoja"]][it["eco"]] = pct(h["tea"])
    c_lecap = _curva_de_tasas(tasas["LECAPS"], venc, liq)
    c_dl = _curva_de_tasas(tasas["USD Linked"], venc, liq)

    filas, cortos, sin_dato = [], [], []
    for tk in contratos_solapa():
        v = venc_contrato(tk)
        if not v or v <= hoy:
            continue
        f = fut.get(tk)
        if not f or not f.get("precio"):
            sin_dato.append(tk)
            continue
        dias = (v - liq).days
        if dias < DIAS_MIN_SINT:
            cortos.append(tk)
            continue
        fila = {"contrato": tk, "venc": v.isoformat(), "dias": dias, "precio": f["precio"],
                "volumen": f.get("volumen"), "ultimaOperacion": f.get("ultimaOperacion"),
                "sinOperar": not f.get("volumen")}
        fila.update(fila_sintetico(tk, f["precio"], dias, tc, c_lecap, c_dl))
        filas.append(fila)

    def red(x):
        if isinstance(x, float):
            return round(x, 3)
        if isinstance(x, dict):
            return {k: red(v) for k, v in x.items()}
        if isinstance(x, list):
            return [red(v) for v in x]
        return x

    # ── plazo constante, hoy contra la historia ──
    try:
        hist = json.loads(SPREADS_SINTETICOS.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        hist = {}
        print(f"AVISO: sin spreads_sinteticos.json ({e})")
    venc_h = {k: date.fromisoformat(v) for k, v in (hist.get("_venc") or {}).items()}
    venc_h.update(venc)
    # Para hoy, TODOS los contratos que devolvió A3 y no sólo los de la solapa: el archivo
    # histórico guarda todos, y la interpolación a plazo fijo tiene que ver el mismo abanico.
    rueda_hoy = {"tc": tc, "fut": {k: v["precio"] for k, v in fut.items()},
                 "lecap": tasas["LECAPS"], "dl": tasas["USD Linked"]}
    ruedas = sorted(k for k in hist if not k.startswith("_") and k < hoy.isoformat())
    del_anio = [r for r in ruedas if r >= f"{hoy.year}-01-01"]
    plazos = {}
    for pl in PLAZOS_SINT:
        h = spread_plazo(rueda_hoy, hoy.isoformat(), pl, venc_h)
        if not h:
            continue
        reg = {"hoy": h}
        serie = [(r, spread_plazo(hist[r], r, pl, venc_h)) for r in del_anio]
        serie = [(r, s) for r, s in serie if s]
        previas = [(r, s) for r, s in serie if r <= ayer.isoformat()]
        if previas:
            r, s = previas[-1]
            reg["anterior"] = {"fecha": r, "pesos": s["pesos"], "dolar": s["dolar"],
                               "variacionPesos": h["pesos"] - s["pesos"]}
        for tipo, fref in (referencias or {}).items():
            antes = [(r, s) for r, s in serie if r <= fref]
            if antes:
                r, s = antes[-1]
                reg[tipo] = {"fecha": r, "pesos": s["pesos"], "dolar": s["dolar"],
                             "variacionPesos": h["pesos"] - s["pesos"]}
        if serie:
            vals = [s["pesos"] for _, s in serie]
            rmin = min(serie, key=lambda x: x[1]["pesos"])
            rmax = max(serie, key=lambda x: x[1]["pesos"])
            reg["anio"] = {"ruedas": len(serie), "desde": serie[0][0],
                           "mediana": median(vals),
                           "min": {"fecha": rmin[0], "pesos": rmin[1]["pesos"]},
                           "max": {"fecha": rmax[0], "pesos": rmax[1]["pesos"]},
                           # qué fracción de las ruedas del año tuvo un spread MENOR que el de hoy
                           "percentilHoy": sum(v < h["pesos"] for v in vals) / len(vals)}
        plazos[str(pl)] = reg

    out.update({
        "disponible": bool(filas),
        "fuenteFuturos": ("A3 Mercados · último operado de la rueda (tick-prices)"
                          if modo == "intradia" else
                          "A3 Mercados · precio de AJUSTE de la rueda anterior (tick-prices no respondió)"),
        "modoFuturos": modo,
        "ruedaFuturos": rueda_fut,
        "avisoFuturos": aviso,
        "tc": {"fecha": f_tc, "valor": tc},
        "liquidacion": liq.isoformat(),
        "curvas": {"lecap": [p["ticker"] for p in c_lecap], "dl": [p["ticker"] for p in c_dl]},
        "filas": red(filas),
        "excluidosPorPlazo": cortos,
        "sinPrecio": sin_dato,
        "futuros": {k: {"precio": v["precio"], "volumen": v.get("volumen")}
                    for k, v in sorted(fut.items(), key=lambda x: venc_contrato(x[0]) or date.max)
                    if (venc_contrato(k) or hoy) > hoy},
        "plazoConstante": red(plazos),
    })
    return out


def main():
    cli = cliente_1816()
    if cli is None:
        print("ERROR: sin cliente de 1816. Hace falta el secret API_1816_KEY.")
        return 1

    hoy = hoy_art()          # ya viene como date, en calendario argentino
    fer = feriados(hoy.year)
    if not es_habil(hoy, fer):
        print(f"{hoy} no es rueda hábil; no se arma informe.")
        return 0

    ayer = rueda_anterior_habil(hoy, fer)
    tipos = tipos_de_cierre(hoy, fer)
    items = leer_tickers()

    # CONTROL DE QUE LA MONEDA COINCIDE CON LA DEL MONITOR. No corrige nada: leer_tickers() ya
    # resuelve bien la punta de cada ON —moneda_on() en actualizar_historicos.py aplica la misma
    # regla que monedaDeON() en ons.html: ley NY al CCL, locales según su columna Divisa—. Lo que
    # hace es avisar si alguna vez divergen.
    #
    # Vale la pena tenerlo porque son dos implementaciones de la misma regla en dos lenguajes, y
    # nada obliga a que se muevan juntas: el día que alguien cambie una y no la otra, el informe
    # va a estar mostrando una punta y la pantalla otra, y sin este control no se enteraría nadie.
    ons = mapa_ons()
    discrepan = [it["eco"] for it in items
                 if ons.get(it["eco"]) and it["moneda"] != ons[it["eco"]]["moneda"]]
    print(f"Universo: {len(items)} instrumentos · ruedas {ayer} y {hoy}")
    if discrepan:
        print(f"ATENCIÓN: {len(discrepan)} ONs con moneda distinta a la del monitor: "
              f"{', '.join(discrepan[:12])}")

    datos = pedir_series(cli, items, ayer.isoformat(), hoy.isoformat())
    print(f"1816 devolvió datos de {len(datos)} tickers")

    # SEGUNDA PASADA EN MEP PARA LO QUE SE VALÚA EN CCL.
    #
    # La tabla por familia muestra cada grupo en la punta del monitor, y ahí globales, subsoberanos
    # y ONs de ley NY van al CCL. Pero comparar un global contra su bonar gemelo con esas puntas
    # cruzadas da un número que sale, es plausible, y no significa lo que dice: la propia solapa
    # Glob vs Bon lo tiene documentado —el canje de AL29/GD29 pasa de +4,02% a +0,17% según se
    # mezclen o no—. Por eso esa solapa descarta lo que no esté en MEP en vez de convertirlo.
    #
    # Acá se hace lo mismo pero sin perder el par: se pide una segunda vez en MEP sólo lo que se
    # valúa en CCL, y el instrumento queda con las dos puntas. La tabla sigue usando la del
    # monitor; cualquier comparación entre familias usa la homogénea.
    #
    # Cuesta unos 430 créditos —54 tickers por 4 campos por 2 ruedas— contra los ~1.500 del pedido
    # principal. Convertir con el canje habría salido gratis, pero mete un supuesto propio en cada
    # número; pedirlo es exacto y barato.
    # PATAS DE LOS DUALES. Son pocas —dos por dual— así que el costo es marginal: catorce tickers
    # por cuatro campos por dos ruedas, unos 110 créditos.
    patas = patas_duales()
    datos_patas = {}
    if patas:
        falsos = [{"eco": t1816, "t1816": t1816, "moneda": "ars"} for _, _, t1816 in patas]
        datos_patas = pedir_series(cli, falsos, ayer.isoformat(), hoy.isoformat())
        con = sum(1 for _, _, t in patas if datos_patas.get(t, {}).get(hoy.isoformat(), {}).get("tea"))
        print(f"Patas de duales: {len(patas)} pedidas, {con} con tasa "
              f"(las dominadas vienen en null a propósito)")

    en_ccl = [dict(it, moneda="mep") for it in items if it.get("moneda") == "ccl"]
    homogeneos = {}
    if en_ccl:
        homogeneos = pedir_series(cli, en_ccl, ayer.isoformat(), hoy.isoformat())
        print(f"Segunda pasada en MEP para {len(en_ccl)} instrumentos que se valúan en CCL: "
              f"{len(homogeneos)} con dato")

    # RUEDAS DE REFERENCIA PARA LOS CIERRES. Se piden aparte y sólo el día que hacen falta: el
    # rango hoy-ayer no las cubre, y traer toda la semana o todo el mes multiplicaría los créditos
    # por cinco o por veinte para usar dos fechas.
    #
    # Se le piden a 1816 y no se sacan de historicos.xlsx aunque el Excel tenga la serie desde
    # diciembre: ahí sólo hay PRECIO, y el informe compara además tasas, paridades y durations. Con
    # el Excel el cierre semanal podría decir cuánto se movió el precio pero no cuánto la TIR, que
    # en pesos es justamente lo que se mira.
    refs = {}
    for tipo in ("semanal", "mensual"):
        if tipo not in tipos:
            continue
        f = ultima_rueda_de_periodo_anterior(hoy, fer, tipo)
        print(f"Cierre {tipo}: referencia {f}")
        # Se pide con un día de margen hacia atrás porque /series no devuelve nada cuando la fecha
        # inicial y la final coinciden; después se toma sólo la rueda que interesa.
        d2 = pedir_series(cli, items, (f - timedelta(days=4)).isoformat(), f.isoformat())
        refs[tipo] = {"fecha": f.isoformat(), "datos": d2}
        print(f"  {sum(1 for v in d2.values() if f.isoformat() in v)} tickers con dato en esa rueda")

    print(f"Leyes de ONs: {sum(1 for v in ons.values() if v['ley'] == 'local')} local, "
          f"{sum(1 for v in ons.values() if v['ley'] == 'ny')} NY")

    instrumentos, por_familia = [], defaultdict(list)
    sin_dato, sin_ley = [], []
    # Agrupados por familia además de la lista plana: sin eso no se puede decir en cada curva
    # cuántos de su panel faltan, que en los chicos cambia cómo se lee.
    sin_dato_fam = defaultdict(list)
    for it in items:
        fam = FAMILIAS.get(it["hoja"])
        if not fam or not it["t1816"]:
            continue
        if fam == "ONs":
            info = ons.get(it["eco"]) or {}
            fam = {"local": "ONs ley local", "ny": "ONs ley NY"}.get(info.get("ley"))
            if not fam:
                sin_ley.append(it["eco"])
                continue
        serie = datos.get(it["t1816"], {})
        h = serie.get(hoy.isoformat())
        a = serie.get(ayer.isoformat())
        if not h:
            sin_dato.append(it["eco"])
            sin_dato_fam[fam].append(it["eco"])
            continue
        reg = {
            "ticker": it["eco"],
            "familia": fam,
            "moneda": it["moneda"],
            "precio": h.get("precioDirty"),
            "tea": redondear(pct(h.get("tea"))),
            "durationMod": redondear(h.get("durationMod")),
            "paridad": redondear(pct(h.get("paridad"))),
            # Las dos varas: el % de precio, que es lo que ve el tenedor, y los puntos de tasa, que
            # es lo que se negocia. Para una LECAP la primera es casi siempre positiva por el mero
            # devengamiento, así que sin la segunda el informe diría que "todo subió" todos los días.
            "varPrecio": variacion(h.get("precioDirty"), (a or {}).get("precioDirty")),
            "varTasa": delta(pct(h.get("tea")), pct((a or {}).get("tea"))),
            "varParidad": delta(pct(h.get("paridad")), pct((a or {}).get("paridad"))),
            "conAyer": bool(a),
        }
        # Las patas, para los duales.
        if fam == "Duales":
            pp = {}
            for tk, tipo, t1816 in patas:
                if tk != it["eco"]:
                    continue
                v = datos_patas.get(t1816, {}).get(hoy.isoformat())
                if not v:
                    continue
                # `conDatos` y NO `itm`: 1816 devuelve null para algunas patas dominadas
                # —"TTS26 @Tasa Fija" viene entero en null— pero para otras publica las dos, así
                # que tener tasa no alcanza para afirmar que esa pata es la que va a pagar. Cuál
                # manda lo decide comparar los valores finales al vencimiento, que es lo que hace
                # el monitor; acá sólo se dice si el dato existe.
                pp[tipo] = {"tea": redondear(pct(v.get("tea"))),
                            "durationMod": redondear(v.get("durationMod")),
                            "conDatos": v.get("tea") is not None}
            if pp:
                reg["patas"] = pp

        # La punta homogénea, para poder compararlo con los que se valúan en MEP. Va como campo
        # aparte y no reemplaza al principal: la tabla por familia tiene que seguir mostrando lo
        # mismo que la pantalla.
        if it["moneda"] == "ccl":
            hm = homogeneos.get(it["t1816"], {}).get(hoy.isoformat())
            if hm:
                reg["enMep"] = {"precio": hm.get("precioDirty"),
                                "tea": redondear(pct(hm.get("tea"))),
                                "paridad": redondear(pct(hm.get("paridad")))}

        # Variación contra el cierre de la semana y del mes anteriores, con la misma vara que la
        # diaria: porcentaje de precio y puntos de tasa.
        for tipo, ref in refs.items():
            r0 = (datos_ref := ref["datos"].get(it["t1816"], {})).get(ref["fecha"])
            if r0:
                reg[f"varPrecio_{tipo}"] = variacion(h.get("precioDirty"), r0.get("precioDirty"))
                reg[f"varTasa_{tipo}"] = delta(pct(h.get("tea")), pct(r0.get("tea")))
        instrumentos.append(reg)
        por_familia[fam].append(reg)

    resumen = {}
    for fam, regs in por_familia.items():
        # TASAS QUE NO SON COMPARABLES CON SUS PARES. Dentro de una familia puede haber
        # instrumentos cuya tasa está en otra escala, y hay que marcarlos para que nadie los
        # promedie ni los lea como equivalentes.
        #
        # En los DUALES la causa está entendida: el ticker pelado devuelve la tasa de la pata que
        # DOMINA, y no todos tienen la misma. TXMJ8 viene al 5,82% porque en ese bono manda la pata
        # CER, y TXMD8 al 40,53% porque manda la TAMAR. No es un error de la fuente ni una
        # convención inconsistente —como supuse al principio—: son dos cosas distintas informadas
        # correctamente. Para compararlos está el campo `patas`, que trae cada una por separado.
        #
        # En el resto de las familias puede ser simplemente un valor extremo legítimo, como el CER
        # ultracorto con TIR real negativa.
        teas = [r["tea"] for r in regs if r["tea"] is not None]
        raros = []
        if len(teas) >= 4:
            m = median(teas)
            # Factor 3 y no un múltiplo de desvío: lo que se busca no es un instrumento caro sino
            # una unidad distinta, y eso siempre aparece como un salto de orden de magnitud.
            raros = sorted(r["ticker"] for r in regs
                           if r["tea"] is not None and m > 0
                           and (r["tea"] > m * 3 or r["tea"] < m / 3))
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
            # Tickers cuya tasa está en otra escala que el resto de su familia: hay que mirarlos
            # antes de compararlos con sus pares. Ver el comentario de arriba.
            "convencionDudosa": raros,
            # MONEDA EN LA QUE SE VALÚA LA FAMILIA, la misma que muestra el chip del monitor. No
            # siempre es una sola: las ONs de ley local son 54 en MEP y 7 en CCL, según en qué
            # moneda paga cada emisor. Por eso va el reparto completo y no un rótulo único, que
            # para esa familia sería mentira.
            "monedas": dict(Counter(r["moneda"] for r in regs).most_common()),
        }
        for tipo in refs:
            rp = resumir([r.get(f"varPrecio_{tipo}") for r in regs])
            rt = resumir([r.get(f"varTasa_{tipo}") for r in regs])
            if rp or rt:
                resumen[fam][tipo] = {"precio": rp, "tasa": rt}

    # BCRA, riesgo país y caución. Va después de los bonos y no antes porque si 1816 no responde el
    # informe no sale igual: sin precios no hay nada que contar, y estas series son el contexto.
    print("Pidiendo BCRA, riesgo país y caución...")
    macro = datos_macro(hoy, cliente_1816=cli,
                        referencias={t: r["fecha"] for t, r in refs.items()})
    if macro["fallos"]:
        print("  fallos macro:", "; ".join(macro["fallos"]))
    macro["embig"] = datos_embig({t: r["fecha"] for t, r in refs.items()}, hoy.year)
    if macro["embig"]["disponible"]:
        e = macro["embig"]
        print(f"  EMBIG al {e['hasta']}: Argentina {e['argentina']['valor']:.0f}, "
              f"Latinoamérica {e['latam']['valor']:.0f}, brecha {e['brecha']['valor']:.0f}")
    else:
        print(f"  sin EMBIG: {macro['embig'].get('motivo')}")
    print(f"  {len(macro['series'])} series del BCRA · caución 1816: {macro['caucion']}")

    refs_fechas = {t: r["fecha"] for t, r in refs.items()}
    mercado = datos_mercado(refs_fechas)
    mercado["legislacion"] = datos_legislacion(refs_fechas, hoy.year)
    if mercado["legislacion"]["disponible"]:
        lg = mercado["legislacion"]["mediana10"]
        print(f"  diferencial GD30/AL30 al {lg['fecha']}: {lg['crudo']:.2f}% "
              f"(mediana 10 ruedas {lg['valor']:.2f}%), {len(lg['mesesSimilares'])} meses parecidos")
    else:
        print(f"  sin diferencial de legislación: {mercado['legislacion'].get('motivo')}")
    if mercado["disponible"]:
        bqs = ", ".join(f"{k} al {v['hasta']}" for k, v in mercado["bloques"].items())
        print(f"  series de mercado: {bqs}")
    else:
        print(f"  sin series de mercado: {mercado.get('motivo', 'bloques vacíos')}")

    # Sintéticos contra instrumentos directos: las dos tablas de la solapa. Si A3 o el BCRA no
    # responden, el informe sale igual sin el bloque y lo dice.
    print("Sintéticos: futuros de A3 y A3500...")
    try:
        sinteticos = datos_sinteticos(items, datos, hoy, fer, ayer, refs_fechas)
    except Exception as e:                                        # noqa: BLE001
        sinteticos = {"disponible": False, "motivo": f"error armando el bloque: {e}"}
    if sinteticos["disponible"]:
        p90 = (sinteticos["plazoConstante"].get("90") or {}).get("hoy") or {}
        print(f"  {len(sinteticos['filas'])} contratos · futuros {sinteticos['modoFuturos']} "
              f"del {sinteticos['ruedaFuturos']} · A3500 {sinteticos['tc']['valor']} del "
              f"{sinteticos['tc']['fecha']} · spread a 90 días {p90.get('pesos')}")
    else:
        print(f"  sin sintéticos: {sinteticos.get('motivo')}")

    salida = {
        "fecha": hoy.isoformat(),
        "ruedaAnterior": ayer.isoformat(),
        "tipos": tipos,
        # EN HORA ARGENTINA. El runner de GitHub corre en UTC, así que datetime.now() daba las
        # 17:08 para una corrida de las 14:08 de Buenos Aires. El informe del 28/08/2026 salió
        # diciendo "tomados a las 17:08 con el mercado ya cerrado" cuando el mercado estaba abierto
        # y los precios eran intradía.
        "generado": datetime.now(ART).isoformat(timespec="seconds"),
        "feriadosLeidos": fer is not None,
        "universo": len(items),
        "sinDato": sin_dato,
        "sinDatoPorFamilia": dict(sin_dato_fam),
        # ONs que no declaran ley en el Excel: quedan fuera del informe en vez de caer en una
        # familia arbitraria, y se listan para que se pueda completar la columna.
        "onsSinLey": sin_ley,
        "campos": CAMPOS,
        "resumen": resumen,
        "instrumentos": instrumentos,
        "macro": macro,
        # Margen sobre TAMAR de los Duales y Bonares AO27/AO28 con su forward. Salen del archivo
        # que dejó series_mercado.py en este mismo job, no de una llamada nueva a 1816.
        "mercado": mercado,
        # Las dos tablas de la solapa Sintéticos —LECAP contra sintético en pesos, bono DL contra
        # sintético dólar linked— y el spread a plazo constante contra su historia del año.
        "sinteticos": sinteticos,
        # Fechas contra las que se midió cada cierre, para que el informe pueda nombrarlas en vez
        # de decir "la semana pasada".
        "referencias": refs_fechas,
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
