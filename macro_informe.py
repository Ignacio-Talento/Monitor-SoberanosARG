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
RIESGO_PAIS = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Series del BCRA que le interesan al informe. Se piden por ID porque los nombres se repiten: hay
# cuatro series distintas llamadas casi igual "Tasa de interés TAMAR de bancos privados", que se
# diferencian sólo en nominal contra efectiva y en si incluyen bancos públicos.
# El cuarto campo dice si la serie es un STOCK —un nivel, del que interesa cuánto cambió— o un
# FLUJO —lo que pasó ESE día, del que interesa cuánto se acumuló—. La distinción cambia qué número
# se informa en los cierres: para la TAMAR, la variación semanal es la diferencia contra el viernes
# pasado; para la compra de divisas, esa diferencia no significa nada, porque el Central no compra
# "más que el viernes", compra tanto por día. Lo que se quiere ahí es la suma de la semana.
SERIES = {
    "tamarTEA":       (45,  "TAMAR bancos privados", "% TEA", "stock"),
    "tamarTNA":       (44,  "TAMAR bancos privados", "% TNA", "stock"),
    "badlarTEA":      (35,  "BADLAR bancos privados", "% TEA", "stock"),
    "pasesTerceros":  (150, "Pases entre terceros a 1 día", "% TNA", "stock"),
    "volPases":       (151, "Volumen de pases entre terceros a 1 día", "millones ARS", "flujo"),
    # BAIBAR es la tasa a la que se prestan los bancos privados entre sí: el call interbancario
    # propiamente dicho, distinto de los pases entre terceros de arriba.
    "baibar":         (146, "BAIBAR · préstamos entre bancos privados", "% TNA", "stock"),
    "interbancario":  (148, "Préstamos entre entidades financieras locales", "% TNA", "stock"),
    "plazoFijo30":    (1207, "Plazo fijo a 30 días", "% TNA", "stock"),
    # Compras del BCRA en el mercado de cambios, medidas por su impacto en reservas. Es la serie que
    # responde "cuántos dólares compró el Central", en millones de USD.
    "comprasMLC":     (78,  "Compra de divisas · variación de reservas", "millones USD", "flujo"),
    "efectoMonetario": (47, "Efecto monetario de compras netas al sector privado", "millones ARS",
                        "flujo"),
    "reservas":       (1,   "Reservas internacionales", "millones USD", "stock"),
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


def inflacion(hoy):
    """IPC mensual publicado y su anualización, para llevar los bonos CER a tasa nominal.

    Se anualizan los tres últimos meses: el interanual arrastra un régimen que puede haber quedado
    atrás —33,8% contra 27% en agosto de 2026— y un mes solo se monta sobre un único dato.
    """
    filas, _ = serie_bcra(27, hoy - timedelta(days=220), hoy)
    if len(filas) < 3:
        return None
    ultimos = filas[:3]
    acum = 1.0
    for _f, v in ultimos:
        acum *= 1 + v / 100
    try:
        inter, _ = serie_bcra(28, hoy - timedelta(days=220), hoy)
    except Exception:                                             # noqa: BLE001
        inter = []
    return {"mensuales": [{"fecha": f, "valor": v} for f, v in ultimos],
            "anualizada3m": (acum ** 4 - 1) * 100,
            "interanual": inter[0][1] if inter else None,
            "fecha": ultimos[0][0]}


def _agregar_periodos(reg, filas, valor_hoy, referencias, clase="stock"):
    """Suma al registro la variación contra el cierre de la semana y del mes anteriores.

    LA FECHA DE REFERENCIA NO SIEMPRE EXISTE EN LA SERIE, y por eso se busca el último dato en o
    antes de esa fecha en vez de pedirla exacta: el BCRA publica con dos a cuatro días hábiles de
    rezago, así que un viernes la referencia semanal —el viernes anterior— puede no tener dato y el
    último disponible ser el miércoles. Se informa qué fecha se terminó usando, que no es un
    detalle: comparar contra el miércoles y llamarlo "la semana" sin decirlo es engañoso.
    """
    if not referencias:
        return
    filas_asc = sorted(filas)
    for tipo, fref in referencias.items():
        anterior = [(f, v) for f, v in filas_asc if f <= fref]
        if not anterior:
            continue
        f0, v0 = anterior[-1]
        if clase == "flujo":
            # Acumulado del período: todo lo que pasó DESPUÉS de la rueda de referencia, esa
            # incluida no. Se informa también cuántas ruedas entraron, porque con el rezago de
            # publicación el acumulado de "la semana" puede tener tres días y no cinco.
            tramo = [(f, v) for f, v in filas_asc if f > f0]
            reg[tipo] = {"desde": f0, "acumulado": round(sum(v for _, v in tramo), 4),
                         "ruedas": len(tramo), "pedida": fref, "exacta": f0 == fref,
                         "clase": "flujo"}
        else:
            reg[tipo] = {"fecha": f0, "valor": v0, "variacion": round(valor_hoy - v0, 4),
                         "pedida": fref, "exacta": f0 == fref, "clase": "stock"}


def datos_macro(hoy=None, cliente_1816=None, referencias=None):
    """Devuelve el bloque macro del informe. Nunca lanza: lo que falla queda anotado en 'fallos'."""
    hoy = hoy or date.today()
    # Ventana de 50 días: 20 alcanzaban para la variación diaria, pero el cierre mensual necesita
    # llegar al último día del mes anterior, que un 31 está a 31 días, y hace falta margen para que
    # el rezago de publicación no deje ese extremo afuera.
    desde = hoy - timedelta(days=50)

    referencias = referencias or {}
    out = {"fecha": hoy.isoformat(), "series": {}, "fallos": [], "sslSinVerificar": False,
           "referencias": referencias}

    try:
        out["inflacion"] = inflacion(hoy)
    except Exception as e:                                        # noqa: BLE001
        out["inflacion"] = None
        out["fallos"].append(f"inflacion: {e}")

    for clave, (idv, nombre, unidad, clase) in SERIES.items():
        try:
            filas, seguro = serie_bcra(idv, desde, hoy)
            if not seguro:
                out["sslSinVerificar"] = True
            if not filas:
                out["fallos"].append(f"{clave}: sin datos en la ventana")
                continue
            f, v = filas[0]
            reg = {"nombre": nombre, "unidad": unidad, "id": idv, "fecha": f, "valor": v,
                   "clase": clase, "rezagoDias": (hoy - date.fromisoformat(f)).days}
            if len(filas) > 1:
                reg["previo"] = {"fecha": filas[1][0], "valor": filas[1][1]}
                reg["variacion"] = round(v - filas[1][1], 4)
            # La serie completa de la ventana sirve para ver la tendencia de la semana sin volver
            # a pedirla; son 20 puntos, no pesa.
            reg["ventana"] = [{"fecha": f2, "valor": v2} for f2, v2 in filas[:15]]
            _agregar_periodos(reg, filas, v, referencias, clase)
            out["series"][clave] = reg
        except Exception as e:                                    # noqa: BLE001
            out["fallos"].append(f"{clave} (id {idv}): {e}")

    try:
        # La serie entera y no /ultimo: son 7.689 puntos desde 1999 y pesan 400 KB, pero es el
        # único modo de tener la variación semanal y mensual sin pedir el endpoint tres veces.
        d, _ = _get(RIESGO_PAIS, timeout=45)
        filas = sorted(((x["fecha"], float(x["valor"])) for x in d if x.get("fecha")),
                       reverse=True)
        if filas:
            f, v = filas[0]
            reg = {"valor": v, "fecha": f, "rezagoDias": (hoy - date.fromisoformat(f)).days,
                   "fuente": "EMBI+ Argentina vía argentinadatos.com"}
            if len(filas) > 1:
                reg["previo"] = {"fecha": filas[1][0], "valor": filas[1][1]}
                reg["variacion"] = round(v - filas[1][1], 2)
            _agregar_periodos(reg, filas, v, referencias)
            out["riesgoPais"] = reg
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
