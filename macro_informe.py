#!/usr/bin/env python3
"""Métricas macro y de dinero para el informe diario: BCRA, riesgo país y caución.

QUÉ TRAE Y DE DÓNDE.

  · BCRA — API pública de estadísticas monetarias, sin credenciales. Ojo con la versión: la v3 fue
    dada de baja y responde HTTP 410; la vigente al 2026-08-28 es la v4.0. Hay 1.610 series y muchas
    tienen descripciones casi idénticas, así que se referencian por ID y no por nombre.

  · Riesgo país — argentinadatos.com, que republica el EMBI+ Argentina. El BCRA no lo publica.

  · Caución a 1 día — la SERIE 150 del BCRA, "pases entre terceros a 1 día", que es la misma que
    levanta el monitor. Ver la nota de abajo: no es la caución bursátil.

    NO HAY FUENTE PÚBLICA DE CAUCIÓN BURSÁTIL. Se buscó el 2026-08-28 en todos lados: el BCRA no la
    publica (cero coincidencias con "cauc" en sus 1.610 series), 1816 no la tiene en el plan
    contratado, la API de BYMA Data pide OAuth, y el endpoint de MAE —marketdata.mae.com.ar,
    mercado/titulo/caucionesofertas, con los campos plazo, tasaPP, montoConcertado y volumen, que
    es exactamente lo que haría falta— devuelve 401 sin cuenta.

    También se probó y se DESCARTÓ el futuro de tasa de caución de A3 (contratos CAUC): es público
    y opera de verdad, pero es un futuro MENSUAL que liquida contra el promedio del período, no una
    tasa a 1 día, y sólo el contrato más cercano tiene liquidez —en agosto de 2026 el de agosto
    operó las 18 ruedas y los de septiembre y octubre, tres y dos—. Para una cuenta de fondeo a un
    día, una tasa spot de un mercado cercano dice más que una expectativa mensual del mercado
    exacto.

LAS TRES TASAS DE FONDEO SON DISTINTAS y el informe las trae por separado, cada una con su nombre:

  · pases entre terceros    — recompras entre entidades. Serie 150 del BCRA. 21,54% al 26/08/2026.
  · BAIBAR                  — préstamos entre bancos privados. Serie 146. 21,29%.
  · entre entidades         — préstamos entre entidades financieras locales. Serie 148. 21,86%.

Ninguna de las tres ES la caución bursátil, que como referencia corría unos 170 puntos básicos por
encima: el futuro de A3 marcaba 23,23% el 27/08/2026 contra 21,54% de los pases. Se usa la primera
por ser spot y a un día, pero conviene tener presente ese sesgo en cualquier cuenta de fondeo, y por
eso ni el informe ni la solapa la llaman "caución" a secas.

VERIFICAR EL SSL. El BCRA usa una cadena de certificados que Python no siempre valida en Windows
—el mismo problema que tuvo BYMA en el runner de Ubuntu—. Si la verificación falla, se reintenta sin
verificar y se DEJA CONSTANCIA en el resultado: son datos públicos de solo lectura y el informe vale
más que la verificación, pero que quede escrito y no escondido en un except.
"""
import json
from datetime import date, timedelta

import requests

BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
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

    # La caución de referencia es la SERIE 150 del BCRA —pases entre terceros a 1 día—, la misma que
    # levanta el monitor. Se descartó el futuro de caución de A3: existe y opera, pero es un futuro
    # mensual sobre el promedio del período, no una tasa a 1 día, y sólo el contrato más cercano
    # tiene liquidez. Para una cuenta de fondeo a un día es más útil una tasa spot de otro mercado
    # cercano que una expectativa mensual del mercado correcto.
    ref = out["series"].get("pasesTerceros")
    out["caucion"] = {
        "disponible": bool(ref),
        "tasa": ref["valor"] if ref else None,
        "fecha": ref["fecha"] if ref else None,
        "rezagoDias": ref["rezagoDias"] if ref else None,
        "fuente": "BCRA serie 150 · pases entre terceros a 1 día",
        "esCaucionBursatil": False,
    }

    return out


if __name__ == "__main__":
    print(json.dumps(datos_macro(), ensure_ascii=False, indent=1, default=str))
