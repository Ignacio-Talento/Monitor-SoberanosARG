#!/usr/bin/env python3
"""Dos series de mercado que necesitan la API de 1816: margen sobre TAMAR de los Duales CER/TAMAR
y rendimiento de los Bonares AO27/AO28 contra Cable, con el forward 1Y1Y implícito.

Deja un único `series_mercado.json` en la raíz. La solapa Macro dibuja la primera y la de
Glob vs Bon la segunda; las dos se bajan juntas porque comparten el mismo requisito —la key de
1816— y así el job diario paga un solo par de requests.

POR QUÉ NO VAN EN macro_series.json. Ese archivo lo escribe un job que NO tiene la key: sus cinco
series son públicas y gratis, y esa es justamente la razón por la que corre aparte y dos veces por
día sin competir con los precios por el rate limit. Meter acá adentro algo que necesita créditos
obligaría a darle la key a ese job y a que una caída de 1816 se llevara puesto el ITCRM.

------------------------------------------------------------------------------
MARGEN SOBRE TAMAR DE LOS DUALES
------------------------------------------------------------------------------
Los Duales CER/TAMAR pagan al vencimiento lo que haya rendido más: el CER más un margen, o la
TAMAR capitalizada. 1816 expone cada bono con TRES tickers y la diferencia no es cosmética:

    TXMJ8            la pata que hoy manda, la que el mercado está pagando
    TXMJ8 @CER       valuado forzando la pata CER
    TXMJ8 @TAMAR     valuado forzando la pata TAMAR

El margen sobre TAMAR es el campo `spread` del ticker **@TAMAR**, y hay que pedirlo por ese ticker
y no por el plano: cuando la pata CER es la que manda —hoy es el caso del TXMJ8— el ticker plano
devuelve `spread: null` y la serie queda con agujeros que no son días sin operar.

LA CONVENCIÓN DE TNA NO ES UN DETALLE. `/mercado/series` devuelve por defecto 180-360 y
`/mercado/indicadores` 32-365, así que el MISMO bono en el MISMO día sale 10,19% por un endpoint y
7,92% por el otro. El gráfico de 1816 aclara "margen formato 32/365"; por eso acá va explícito
`convencion_tna="32-365"`, con lo que la serie coincide al centésimo con el indicador. Sin ese
parámetro la serie no está mal, pero mide otra cosa y no se puede comparar contra el semanal.

------------------------------------------------------------------------------
BONARES AO27 / AO28 Y EL FORWARD
------------------------------------------------------------------------------
Van **contra Cable** (`moneda="ccl"`), no contra MEP, por el mismo motivo que da 1816: el
rendimiento contra MEP está contaminado por el canje CCL/MEP, que se mueve por razones que no
tienen que ver con el riesgo del bono.

El forward es la tasa implícita entre los dos vencimientos: cuánto tiene que rendir un bono en
Dólares que arranque cuando vence el AO27 (29-oct-2027, justo la elección) y termine cuando vence
el AO28 (31-oct-2028), para que dé lo mismo comprar el AO28 hoy que comprar el AO27 y reinvertir.

    F = ( (1+TEA₂)^t₂ / (1+TEA₁)^t₁ ) ^ (1/(t₂-t₁)) - 1

ES UNA APROXIMACIÓN, y conviene saberlo antes de citarla: sale de las TIR y no de una curva cero
bootstrapeada, y estos bonos amortizan y pagan renta, así que el plazo al que rinde cada uno no es
exactamente su vencimiento. Contra el semanal del 03/09/2026, que publica 15,5%, esta cuenta da
15,9%: sirve para ver el nivel y sobre todo el MOVIMIENTO, no para arbitrar cuatro décimas.

------------------------------------------------------------------------------
INCREMENTAL
------------------------------------------------------------------------------
Se relee el JSON y se piden sólo los días que faltan, con tres de solapamiento: 1816 corrige el
cierre de una rueda cuando entra una operación tardía, y sin ese solapamiento el primer valor de
cada corrida quedaría congelado con lo que hubiera a la hora del job. Si el archivo está al día son
un par de requests; el costo escala con los días que faltan, no con la historia.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from precios_1816 import Cliente1816                             # noqa: E402

SALIDA = Path(__file__).resolve().parent / "series_mercado.json"

# Duales CER/TAMAR, con la fecha de emisión que informa 1816 en /mercado/instrumentos. La serie
# arranca ahí: antes el bono no existía y pedir esos días sería pagar créditos por nada.
# Ordenados por VENCIMIENTO y no por emisión: así la leyenda del gráfico va del más corto al más
# largo, que es como se lee la curva. El valor es la fecha de emisión, de donde arranca la serie.
DUALES = {
    "TXMJ8": "2026-05-15",   # vence 30-jun-2028
    "TXMD8": "2026-06-12",   # vence 15-dic-2028
    "TXMJ9": "2026-04-30",   # vence 29-jun-2029
    "TXMD9": "2026-06-12",   # vence 14-dic-2029
    "TXMJ0": "2026-06-12",   # vence 28-jun-2030
}
# Bonares del tramo corto: mismo mes de vencimiento con un año de diferencia, que es lo que hace
# que el forward entre los dos sea limpiamente 1Y1Y.
BONARES = {"AO27": ("2026-02-27", date(2027, 10, 29)),
           "AO28": ("2026-03-31", date(2028, 10, 31))}

SOLAPE = 3          # días que se vuelven a pedir por si 1816 corrigió el cierre
DIAS_ANIO = 365.0


def hoy_art():
    """El runner de GitHub corre en UTC; acá interesa el día argentino."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def leer_viejo():
    if not SALIDA.exists():
        return {}
    try:
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {}                                                # ilegible: se rehace entero


def a_mapa(bloque):
    """{'f': [...], 'series': {tk: [...]}} -> {tk: {fecha: valor}}, para poder mezclar por fecha."""
    fechas = (bloque or {}).get("f") or []
    out = {}
    for tk, vals in ((bloque or {}).get("series") or {}).items():
        out[tk] = {f: v for f, v in zip(fechas, vals) if v is not None}
    return out


def a_bloque(mapa, nombre, unidad, fuente, dec, orden=None):
    """{tk: {fecha: valor}} -> un eje de fechas común y una lista por ticker, con None en los
    huecos. Un eje compartido y no uno por serie: el gráfico dibuja varias líneas sobre el mismo
    eje y así no tiene que alinear nada del lado del browser."""
    fechas = sorted({f for v in mapa.values() for f in v})
    tickers = orden or sorted(mapa)
    return {
        "nombre": nombre, "unidad": unidad, "fuente": fuente,
        "desde": fechas[0] if fechas else None,
        "hasta": fechas[-1] if fechas else None,
        "n": len(fechas),
        "f": fechas,
        "series": {tk: [None if mapa.get(tk, {}).get(f) is None
                        else round(mapa[tk][f], dec) for f in fechas]
                   for tk in tickers},
    }


def desde_para(mapa, minimo):
    """Primer día a pedir: el último que ya está, menos el solapamiento. Si no hay nada, la
    emisión."""
    vistas = [f for v in mapa.values() for f in v]
    if not vistas:
        return minimo
    ult = date.fromisoformat(max(vistas)) - timedelta(days=SOLAPE)
    return max(ult.isoformat(), minimo)


def bajar_duales(cli, viejo, hasta):
    mapa = a_mapa(viejo.get("margenTamar"))
    desde = desde_para(mapa, min(DUALES.values()))
    if desde > hasta:
        return a_bloque(mapa, *META_DUALES, 2, orden=list(DUALES))

    # Se pide por el ticker @TAMAR y se guarda con el nombre del bono: el "@TAMAR" es cómo se
    # valúa, no otro instrumento, y en la leyenda del gráfico sólo estorba.
    filas = cli.series([f"{tk} @TAMAR" for tk in DUALES], ["spread"],
                       fecha_inicial=desde, fecha_final=hasta, convencion_tna="32-365")
    nuevos = 0
    for r in filas:
        v = r.get("spread")
        if v is None:
            continue
        tk = r["ticker"].replace(" @TAMAR", "")
        # Sólo desde la emisión: 1816 a veces devuelve el día previo con el precio de licitación.
        if r["fecha"] < DUALES.get(tk, "9999"):
            continue
        mapa.setdefault(tk, {})[r["fecha"]] = v * 100
        nuevos += 1
    print(f"  duales: {nuevos} puntos desde {desde}")
    return a_bloque(mapa, *META_DUALES, 2, orden=list(DUALES))


def bajar_bonares(cli, viejo, hasta):
    mapa = a_mapa(viejo.get("bonares"))
    # El forward se recalcula entero: es una cuenta sobre las otras dos series, no un dato bajado,
    # y recalcularlo cuesta cero. Guardarlo evita que cada cliente que dibuje repita la fórmula.
    mapa.pop("forward", None)
    desde = desde_para(mapa, min(d for d, _ in BONARES.values()))
    tea = {tk: {} for tk in BONARES}

    if desde <= hasta:
        filas = cli.series(list(BONARES), ["tna", "tea"], fecha_inicial=desde,
                           fecha_final=hasta, moneda="ccl")
        nuevos = 0
        for r in filas:
            tk, f = r["ticker"], r["fecha"]
            if r.get("tna") is not None:
                mapa.setdefault(tk, {})[f] = r["tna"] * 100
                nuevos += 1
            if r.get("tea") is not None:
                tea[tk][f] = r["tea"]
        print(f"  bonares: {nuevos} puntos desde {desde}")

    # El forward necesita la TEA de los dos bonos EL MISMO DÍA. Para los días que ya estaban en el
    # archivo no se vuelve a bajar la TEA: se reconstruye desde la TNA guardada, que para estos
    # bonos es semestral 180-360, o sea TEA = (1 + TNA/2)^2 - 1.
    for f in set(mapa.get("AO27", {})) & set(mapa.get("AO28", {})):
        try:
            t = {}
            for tk, (_emis, venc) in BONARES.items():
                y = tea[tk].get(f)
                if y is None:
                    y = (1 + mapa[tk][f] / 200) ** 2 - 1
                plazo = (venc - date.fromisoformat(f)).days / DIAS_ANIO
                t[tk] = (y, plazo)
            (y1, t1), (y2, t2) = t["AO27"], t["AO28"]
            if t2 - t1 <= 0:
                continue
            fwd = (((1 + y2) ** t2 / (1 + y1) ** t1) ** (1 / (t2 - t1)) - 1) * 100
            mapa.setdefault("forward", {})[f] = fwd
        except Exception:                                        # noqa: BLE001
            continue

    return a_bloque(mapa, *META_BONARES, 2, orden=["AO27", "AO28", "forward"])


META_DUALES = ("Margen sobre TAMAR de los Duales CER/TAMAR",
               "% sobre TAMAR · TNA 32/365",
               "1816 · spread del ticker @TAMAR")
META_BONARES = ("Rendimiento de los Bonares AO27 y AO28 contra Cable",
                "% TNA · forward 1Y1Y implícito",
                "1816 · BYMA PPT T+1 en CCL")


def main():
    hasta = hoy_art().isoformat()
    viejo = leer_viejo()
    out = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fallos": []}
    cli = Cliente1816()

    for clave, fn in (("margenTamar", bajar_duales), ("bonares", bajar_bonares)):
        try:
            out[clave] = fn(cli, viejo, hasta)
        except Exception as e:                                   # noqa: BLE001
            out["fallos"].append(f"{clave}: {e}")
            print(f"  {clave} FALLÓ: {e}")
            if viejo.get(clave):
                out[clave] = viejo[clave]                        # vale más el dato de ayer que nada

    if not out.get("margenTamar") and not out.get("bonares"):
        raise SystemExit("no se pudo bajar ninguna de las dos series; no se pisa el JSON anterior")

    # Mismo criterio que macro_series.py: si el dato no cambió no se toca el archivo, así el job
    # diario no deja un commit cuya única línea distinta es la marca de tiempo. `generado` pasa a
    # significar "cuándo cambió el dato".
    sin_fecha = lambda d: {k: v for k, v in d.items() if k != "generado"}   # noqa: E731
    if viejo and sin_fecha(out) == sin_fecha(viejo):
        print(f"sin cambios: {SALIDA.name} queda como estaba (generado {viejo.get('generado')})")
        return

    SALIDA.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for k in ("margenTamar", "bonares"):
        b = out.get(k) or {}
        print(f"{k:12} {b.get('n', 0):5} ruedas  {b.get('desde')} .. {b.get('hasta')}")
    print(f"{SALIDA.name}: {SALIDA.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
