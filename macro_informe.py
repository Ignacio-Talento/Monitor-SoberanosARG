#!/usr/bin/env python3
"""Métricas macro y de dinero para el informe diario: BCRA, riesgo país y caución.

QUÉ TRAE Y DE DÓNDE.

  · BCRA — API pública de estadísticas monetarias, sin credenciales. Ojo con la versión: la v3 fue
    dada de baja y responde HTTP 410; la vigente al 2026-08-28 es la v4.0. Hay 1.610 series y muchas
    tienen descripciones casi idénticas, así que se referencian por ID y no por nombre.

  · Riesgo país — argentinadatos.com, que republica el EMBI+ Argentina. El BCRA no lo publica.

  · Caución bursátil — NO HAY FUENTE PÚBLICA. Se buscó el 2026-08-28 y no está en ningún lado
    accesible: el BCRA no la publica (cero coincidencias con "cauc" en sus 1.610 series), 1816
    tampoco la tiene en el plan contratado, y la API de BYMA Data —que sí la tiene, su bundle
    referencia una ruta /cauciones— está detrás de OAuth con usuario y contraseña. Rava y Bolsar
    devuelven 404 en los endpoints que se probaron.

    Lo más cercano que queda es la tasa de PASES ENTRE TERCEROS a 1 día (serie 150), que es el
    proxy que ya usa la solapa Caución vs LECAP. Se sirve con ese nombre y no como "caución": ver
    la nota de abajo.

LA CONFUSIÓN QUE HAY QUE EVITAR. La solapa Caución vs LECAP muestra como "caución 1 día" la serie
150 del BCRA, que en realidad es la tasa de pases entre terceros. Son mercados parecidos y las tasas
suelen andar cerca, pero no son lo mismo: la caución es bursátil, se pacta en BYMA y la garantiza el
mercado; los pases entre terceros son operaciones de recompra entre entidades. Acá se las trata como
dos series distintas y cada una se nombra por lo que es.

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

    out["caucion"] = caucion_1816(cliente_1816) if cliente_1816 else {
        "disponible": False, "motivo": "sin cliente de 1816"}

    return out


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
