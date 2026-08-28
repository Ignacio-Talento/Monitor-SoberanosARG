#!/usr/bin/env python3
"""Métricas macro y de dinero para el informe diario: BCRA, riesgo país y caución.

QUÉ TRAE Y DE DÓNDE.

  · BCRA — API pública de estadísticas monetarias, sin credenciales. Ojo con la versión: la v3 fue
    dada de baja y responde HTTP 410; la vigente al 2026-08-28 es la v4.0. Hay 1.610 series y muchas
    tienen descripciones casi idénticas, así que se referencian por ID y no por nombre.

  · Riesgo país — argentinadatos.com, que republica el EMBI+ Argentina. El BCRA no lo publica.

  · Caución — FUTURO DE TASA DE CAUCIÓN de A3 Mercados, misma API pública que los futuros de dólar.
    Los contratos son CAUC/MMMAA y cotizan la tasa directamente en porcentaje.

    Se llegó acá después de descartar todo lo demás: el BCRA no publica la caución (cero
    coincidencias con "cauc" en sus 1.610 series), 1816 no la tiene en el plan contratado, la API
    de BYMA Data la tiene pero pide OAuth, y Rava y Bolsar dan 404. MAE, que era la otra
    candidata, resultó ser el mismo lugar: su producto se llama "Cauciones A3" y su sitio
    redirige a a3mercados.com.ar, porque A3 es la fusión de Matba Rofex con MAE.

    ES UN FUTURO, NO LA TASA SPOT, y eso importa: el contrato liquida contra el promedio de la
    caución del período, así que a principio de mes cotiza una expectativa a 30 días y recién
    cerca del vencimiento converge a la tasa de hoy. Por eso se informa siempre `diasAlVenc`: con
    dos o tres días es prácticamente spot, con veinticinco no.

    A cambio de esa imprecisión trae algo que ninguna otra fuente daba: volumen y cantidad de
    operaciones, así que se sabe si el número salió de un mercado o de un ajuste teórico. El
    2026-08-27 el contrato de agosto cerró en 23,23% con 29 operaciones.

LAS TRES TASAS DE FONDEO SON DISTINTAS y el informe las trae por separado, cada una con su nombre:

  · caución (A3, CAUC)      — bursátil, garantizada por el mercado. 23,23% al 2026-08-27.
  · pases entre terceros    — recompras entre entidades. Serie 150 del BCRA. 21,54%.
  · BAIBAR                  — préstamos entre bancos privados. Serie 146. 21,29%.

Los ~170 puntos básicos entre la primera y las otras dos no son ruido: son mercados con distinta
garantía y distintos participantes. Mostrar una en lugar de otra —que es lo que hacía la solapa
Caución vs LECAP con la serie 150 bajo el rótulo "caución"— cambia el resultado de cualquier cuenta
de fondeo.

VERIFICAR EL SSL. El BCRA usa una cadena de certificados que Python no siempre valida en Windows
—el mismo problema que tuvo BYMA en el runner de Ubuntu—. Si la verificación falla, se reintenta sin
verificar y se DEJA CONSTANCIA en el resultado: son datos públicos de solo lectura y el informe vale
más que la verificación, pero que quede escrito y no escondido en un except.
"""
import json
from datetime import date, timedelta

import requests

BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
CEM = "https://apicem.matbarofex.com.ar/api/v2"
RIESGO_PAIS = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Series del BCRA que le interesan al informe. Se piden por ID porque los nombres se repiten: hay
# cuatro series distintas llamadas casi igual "Tasa de interés TAMAR de bancos privados", que se
# diferencian sólo en nominal contra efectiva y en si incluyen bancos públicos.
SERIES = {
    "tamarTEA":       (45,  "TAMAR bancos privados", "% TEA"),
    "tamarTNA":       (44,  "TAMAR bancos privados", "% TNA"),
    "badlarTEA":      (35,  "BADLAR bancos privados", "% TEA"),
    "pasesTerceros":  (150, "Pases entre terceros a 1 día", "% TNA"),
    "volPases":       (151, "Volumen de pases entre terceros a 1 día", "millones ARS"),
    # BAIBAR es la tasa a la que se prestan los bancos privados entre sí: el call interbancario
    # propiamente dicho, distinto de los pases entre terceros de arriba.
    "baibar":         (146, "BAIBAR · préstamos entre bancos privados", "% TNA"),
    "interbancario":  (148, "Préstamos entre entidades financieras locales", "% TNA"),
    "plazoFijo30":    (1207, "Plazo fijo a 30 días", "% TNA"),
    # Compras del BCRA en el mercado de cambios, medidas por su impacto en reservas. Es la serie que
    # responde "cuántos dólares compró el Central", en millones de USD.
    "comprasMLC":     (78,  "Compra de divisas · variación de reservas", "millones USD"),
    "efectoMonetario": (47, "Efecto monetario de compras netas al sector privado", "millones ARS"),
    "reservas":       (1,   "Reservas internacionales", "millones USD"),
}


def _get(url, params=None, timeout=30):
    """GET con fallback a sin verificación de SSL, dejando dicho cuál de las dos se usó."""
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        r.raise_for_status()
        return r.json(), True
    except requests.exceptions.SSLError:
        r = requests.get(url, params=params, headers=UA, timeout=timeout, verify=False)
        r.raise_for_status()
        return r.json(), False


def serie_bcra(id_var, desde, hasta):
    """Últimos valores de una serie. Devuelve [(fecha, valor), ...] de más nuevo a más viejo."""
    d, seguro = _get(f"{BCRA}/{id_var}",
                     {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "limit": 60})
    filas = []
    for bloque in d.get("results", []):
        for x in bloque.get("detalle", []):
            filas.append((x["fecha"], float(x["valor"])))
    filas.sort(key=lambda x: x[0], reverse=True)
    return filas, seguro


def datos_macro(hoy=None, cliente_1816=None):
    """Devuelve el bloque macro del informe. Nunca lanza: lo que falla queda anotado en 'fallos'."""
    hoy = hoy or date.today()
    # Ventana de 20 días para que un feriado largo o el rezago de publicación no dejen la serie
    # vacía. El BCRA publica las tasas con uno o dos días hábiles de atraso, y las de reservas y
    # compras de divisas suelen ir un día más atrás que las de tasas.
    desde = hoy - timedelta(days=20)

    out = {"fecha": hoy.isoformat(), "series": {}, "fallos": [], "sslSinVerificar": False}

    for clave, (idv, nombre, unidad) in SERIES.items():
        try:
            filas, seguro = serie_bcra(idv, desde, hoy)
            if not seguro:
                out["sslSinVerificar"] = True
            if not filas:
                out["fallos"].append(f"{clave}: sin datos en la ventana")
                continue
            f, v = filas[0]
            reg = {"nombre": nombre, "unidad": unidad, "id": idv, "fecha": f, "valor": v,
                   "rezagoDias": (hoy - date.fromisoformat(f)).days}
            if len(filas) > 1:
                reg["previo"] = {"fecha": filas[1][0], "valor": filas[1][1]}
                reg["variacion"] = round(v - filas[1][1], 4)
            # La serie completa de la ventana sirve para ver la tendencia de la semana sin volver
            # a pedirla; son 20 puntos, no pesa.
            reg["ventana"] = [{"fecha": f2, "valor": v2} for f2, v2 in filas[:15]]
            out["series"][clave] = reg
        except Exception as e:                                    # noqa: BLE001
            out["fallos"].append(f"{clave} (id {idv}): {e}")

    try:
        d, _ = _get(RIESGO_PAIS)
        out["riesgoPais"] = {"valor": d.get("valor"), "fecha": d.get("fecha"),
                             "fuente": "EMBI+ Argentina vía argentinadatos.com"}
    except Exception as e:                                        # noqa: BLE001
        out["fallos"].append(f"riesgoPais: {e}")

    out["caucion"] = caucion_a3(hoy)
    # 1816 queda como sonda: hoy no tiene cauciones, pero el catálogo crece y cuesta un request.
    if cliente_1816:
        out["caucion1816"] = caucion_1816(cliente_1816)

    return out


def caucion_a3(hoy, dias_atras=10):
    """Curva de futuros de tasa de caución de A3.

    -> {"contratos": [...], "referencia": {...}, "rueda": "AAAA-MM-DD"} o {"disponible": False}.

    La REFERENCIA es el contrato vivo más cercano que haya operado: es el que mejor aproxima la
    caución de hoy, porque cuanto menos le queda al contrato menos margen hay entre el promedio que
    liquida y la tasa spot. Se exige que haya OPERADO y no sólo que exista, porque los contratos
    largos publican ajuste teórico todos los días sin que nadie los toque, y ese número no es un
    precio de mercado — el 2026-08-27, de los tres contratos vivos sólo el de agosto tenía
    operaciones.
    """
    try:
        u = (f"{CEM}/closing-prices?product=Tasa%20de%20Cauci%C3%B3n&type=FUT"
             f"&from={(hoy - timedelta(days=dias_atras)).isoformat()}&to={hoy.isoformat()}"
             f"&pageSize=400&sort=dateTime&sortDir=DESC")
        d, _ = _get(u, timeout=40)
    except Exception as e:                                        # noqa: BLE001
        return {"disponible": False, "motivo": f"A3 no respondió: {e}"}

    # El filtro por product del endpoint no siempre aplica, así que se filtra también acá por el
    # prefijo del símbolo. Sin esto entran los futuros de dólar, soja y todo lo demás.
    filas = [x for x in d.get("data", []) if str(x.get("symbol", "")).startswith("CAUC")]
    if not filas:
        return {"disponible": False, "motivo": "A3 no devolvió contratos CAUC en la ventana"}

    rueda = max(x["dateTime"][:10] for x in filas)
    delaRueda = [x for x in filas if x["dateTime"][:10] == rueda]

    contratos = []
    for x in delaRueda:
        venc = _venc_cauc(x["symbol"])
        contratos.append({
            "symbol": x["symbol"],
            "tasa": x.get("settlement"),
            "volumen": x.get("volume") or 0,
            "operaciones": x.get("tradeCount") or 0,
            "openInterest": x.get("openInterest"),
            "vencimiento": venc.isoformat() if venc else None,
            "diasAlVenc": (venc - date.fromisoformat(rueda)).days if venc else None,
        })
    contratos.sort(key=lambda c: c["diasAlVenc"] if c["diasAlVenc"] is not None else 9999)

    operados = [c for c in contratos if c["operaciones"] > 0 and c["tasa"]]
    ref = operados[0] if operados else None
    return {"disponible": bool(ref), "rueda": rueda, "contratos": contratos, "referencia": ref,
            "fuente": "A3 Mercados · futuro de tasa de caución (CAUC)",
            **({} if ref else {"motivo": "ningún contrato CAUC operó en esa rueda"})}


_MESES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}


def _venc_cauc(symbol):
    """CAUC/AGO26 -> 2026-08-31. El contrato vence el último día del mes que nombra."""
    try:
        _, per = symbol.split("/")
        mes, anio = _MESES[per[:3].upper()], 2000 + int(per[3:])
        return date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1) - timedelta(days=1)
    except Exception:                                             # noqa: BLE001
        return None


def caucion_1816(cli):
    """Busca la curva de cauciones en 1816. Al 2026-08-28 no la tiene: devuelve lista vacía.

    Se deja la consulta hecha igual, y barata —un solo request—, porque el catálogo de 1816 crece:
    cuando la agreguen, esto la va a encontrar sin que haya que acordarse de volver a probar. El
    informe prefiere decir que no tiene la caución antes que mostrar en su lugar la tasa de pases,
    que es parecida pero es otra cosa.
    """
    res = {"disponible": False}
    try:
        inst = cli.instrumentos(texto="cauc")
        res["busqueda"] = [i.get("ticker") for i in (inst or [])][:20]
        if not inst:
            res["motivo"] = "1816 no devuelve instrumentos que matcheen 'cauc'"
            return res
        res["disponible"] = True
        res["instrumentos"] = inst[:20]
    except Exception as e:                                        # noqa: BLE001
        res["motivo"] = f"error consultando 1816: {e}"
    return res


if __name__ == "__main__":
    print(json.dumps(datos_macro(), ensure_ascii=False, indent=1, default=str))
