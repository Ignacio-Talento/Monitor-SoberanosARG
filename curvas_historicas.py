#!/usr/bin/env python3
"""Reconstruye las curvas de ruedas pasadas para la solapa Curvas, desde el cierre de 1816.

    py curvas_historicas.py                                  # cierres semanales dic-2025 a ago-2026
    py curvas_historicas.py --fechas 2026-03-13,2026-05-29   # ruedas puntuales

PARA QUÉ. La solapa Curvas muestra las curvas de cada informe, y el archivo de informes arranca el
28/08/2026. Para poder comparar curvas contra fechas anteriores —lo pidió el usuario el 11/09/2026:
todos los cierres semanales desde el último hábil de diciembre de 2025— hay que rearmar el dato de
esas ruedas con el mismo formato que deja armar_informe.py y pasarlo por el mismo generador.

EL UNIVERSO ES EL DE ESA RUEDA, NO EL DE HOY. Instrumentos.xlsx tiene sólo lo que está vivo hoy, y
una curva de marzo se arma con las LECAPs y los CER que cotizaban en marzo, que en buena parte ya
vencieron. Por eso las familias exhaustivas —tasa fija, CER, TAMAR, duales, dólar linked, Bonares y
Globales— salen del catálogo de 1816 por curva, filtrado a lo que estaba emitido y sin vencer en la
fecha. Los subsoberanos son la excepción: el monitor sigue una selección curada, no la curva entera
de 1816, así que se usa la de hoy (casi no tienen vencimientos en el período).

LO QUE SE PIDE. /indicadores con fechaOperacion —el cierre de esa rueda—, cuatro campos, en la
punta en que el monitor valúa cada familia: pesos para la curva local, MEP para los Bonares y CCL
para Globales y subsoberanos, más una segunda pasada en MEP de esos dos para poder compararlos con
los Bonares en la misma moneda. Las patas de los duales se piden por su ticker con sufijo
(«TXMD8 @CER»). Cuesta unos 500 créditos por rueda.

LO QUE NO HAY. El rezago del BCRA, las series de mercado y los sintéticos no se reconstruyen: acá
sólo importan las curvas. La inflación para llevar los CER a nominal es la de los tres últimos
meses que el INDEC ya había publicado a esa fecha, y la TAMAR spot, la de esa rueda o la anterior.

SALIDA: informes/historico/datos_AAAA-MM-DD.json y las curvas en informes/curvas/AAAA-MM-DD/.
Una rueda que ya tiene su informe (informes/datos_AAAA-MM-DD.json) no se reconstruye: se usa ése.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from actualizar_historicos import leer_tickers                    # noqa: E402
from precios_1816 import Cliente1816, Error1816                    # noqa: E402

HIST = REPO / "informes" / "historico"

# curvaId de 1816 -> familia del informe (las mismas que arma armar_informe.py)
CURVAS = {9: "LECAPs y tasa fija", 10: "LECAPs y tasa fija", 7: "CER", 28: "TAMAR",
          14: "Duales", 12: "Dólar linked", 17: "Dólar linked", 8: "Bonares", 11: "Globales"}
MONEDA = {"Bonares": "mep", "Globales": "ccl", "Subsoberanos": "ccl"}
CAMPOS = ["precioDirty", "tea", "durationMod", "paridad"]
PATAS = ["@CER", "@TAMAR", "@Tasa Fija", "@USD-L"]
TIPO_PATA = {"@CER": "CER", "@TAMAR": "TAMAR", "@Tasa Fija": "LECAP", "@USD-L": "LINKED"}


def feriados_de_mercado(anio):
    """Los días sin rueda del año. NO es el calendario de armar_informe: ahí los puentes turísticos
    cuentan como no hábiles (sirven para el rezago del CER), pero el mercado OPERA en ellos —1816 tiene
    precios del 23/03/2026 y del 10/07/2026—. Y al revés, el 31/12 no es feriado pero el sistema
    financiero no abre (asueto bancario): el 31/12/2025 no hay un solo precio. El 24/12 sí operó."""
    import requests
    from armar_informe import FERIADOS_API
    r = requests.get(FERIADOS_API.format(anio=anio), timeout=15)
    r.raise_for_status()
    return ({date.fromisoformat(f["fecha"]) for f in r.json() if f.get("tipo") != "puente"}
            | {date(anio, 12, 31)})


def cierres_semanales(desde, hasta):
    """La última rueda de cada semana entre las dos fechas, más la última de diciembre de 2025 aunque
    no cierre semana: es la punta del año, contra la que se mide el YTD."""
    fer = set()
    for a in range(min(desde.year, 2025), hasta.year + 1):
        fer |= feriados_de_mercado(a)

    def rueda(d):
        return d.weekday() < 5 and d not in fer

    out = set()
    d = desde - timedelta(days=desde.weekday())                 # lunes de la primera semana
    while d <= hasta:
        ultima = next((d + timedelta(days=k) for k in range(4, -1, -1) if rueda(d + timedelta(days=k))), None)
        if ultima and desde <= ultima <= hasta:
            out.add(ultima)
        d += timedelta(days=7)
    fin_dic = date(2025, 12, 31)
    while not rueda(fin_dic):
        fin_dic -= timedelta(days=1)
    if desde <= date(2025, 12, 31) and fin_dic <= hasta:
        out.add(fin_dic)
    return sorted(out), fer


def catalogo(cli):
    """{ticker_1816: (familia, emision, vencimiento)} de las curvas exhaustivas, vencidos incluidos."""
    out = {}
    for cid, fam in CURVAS.items():
        for intento in range(3):
            try:
                items = cli.instrumentos(curva_id=cid) or []
                break
            except Error1816:
                time.sleep(10 * (intento + 1))
        else:
            print(f"AVISO: no se pudo leer la curva {cid}", file=sys.stderr)
            continue
        for x in items:
            tk = (x.get("ticker") or "").strip()
            v, e = str(x.get("fechaVencimiento") or "")[:10], str(x.get("fechaEmision") or "")[:10]
            # Las patas de los duales («TTD26 @TAMAR») vienen listadas como instrumentos de la
            # curva 14: se piden aparte, como patas, y no entran como duales.
            if tk and " @" not in tk and len(v) == 10 and tk not in out:
                out[tk] = (fam, date.fromisoformat(e) if len(e) == 10 else date(2000, 1, 1),
                           date.fromisoformat(v))
        time.sleep(1.2)
    return out


def pedir(cli, tickers, moneda, fecha):
    """{ticker: {campo: valor}} con los campos que vinieron; lotes de 50, tres intentos."""
    out = {}
    for i in range(0, len(tickers), 50):
        lote = tickers[i:i + 50]
        for intento in range(3):
            try:
                filas = cli.precios(lote, CAMPOS, moneda=moneda, fecha_operacion=fecha.isoformat())
                break
            except Error1816 as e:
                if intento == 2:
                    print(f"AVISO: {moneda} {fecha} lote {i}: {e}", file=sys.stderr)
                    filas = []
                time.sleep(10 * (intento + 1))
        for f in filas or []:
            if f.get("tea") is not None or f.get("precioDirty") is not None:
                out[f["ticker"]] = f
    return out


def _pct(x):
    return None if x is None else round(x * 100, 3)


def inflacion_a(fecha):
    """Los tres últimos IPC mensuales que el INDEC había publicado a esa fecha, anualizados. El IPC
    de un mes sale a mediados del siguiente; se toma publicado el día 15."""
    S = json.loads((REPO / "macro_series.json").read_text(encoding="utf-8"))["series"]["ipc"]
    pub = []
    for f, v in zip(S["f"], S["v"]):
        m = date.fromisoformat(f)
        salida = date(m.year + (m.month == 12), m.month % 12 + 1, 15)
        if salida <= fecha:
            pub.append((f, v))
    if len(pub) < 3:
        return None
    acum = 1.0
    for _, v in pub[-3:]:
        acum *= 1 + v / 100
    return {"mensuales": [{"fecha": f, "valor": v} for f, v in pub[-3:]],
            "anualizada3m": (acum ** 4 - 1) * 100, "fecha": pub[-1][0]}


def tamar_a(fecha):
    S = json.loads((REPO / "macro_series.json").read_text(encoding="utf-8"))["series"]["tamar"]
    antes = [(f, v) for f, v in zip(S["f"], S["v"]) if f <= fecha.isoformat()]
    return {"fecha": antes[-1][0], "valor": antes[-1][1]} if antes else None


def reconstruir(cli, fecha, cat, subsob, seguidos, hoy):
    # Lo que sigue vivo HOY y el monitor no sigue, no entra: fue una decisión del universo (DIP0 y
    # PAP0 duplican a DICP y PARP, por ejemplo) y la curva vieja tiene que ser comparable con la de
    # hoy. Lo que ya venció no se puede contrastar contra el monitor, así que entra.
    vivos = {tk: fam for tk, (fam, emi, venc) in cat.items()
             if emi <= fecha < venc and (venc <= hoy or tk in seguidos)}
    por_moneda = defaultdict(list)
    for tk, fam in vivos.items():
        por_moneda[MONEDA.get(fam, "ars")].append(tk)
    datos = {}
    for mon, tks in por_moneda.items():
        datos.update({(tk, mon): v for tk, v in pedir(cli, tks, mon, fecha).items()})
    glob_mep = pedir(cli, [tk for tk, f in vivos.items() if f == "Globales"], "mep", fecha)
    # Cada subsoberano en la punta en que lo valúa el monitor (la columna Divisa manda), igual que
    # en el informe diario; más la pasada en MEP para compararlos contra los Bonares.
    sub = {}
    for mon in {m for _, _, m in subsob}:
        sub.update(pedir(cli, [t for _, t, m in subsob if m == mon], mon, fecha))
    sub_mep = pedir(cli, [t for _, t, _ in subsob], "mep", fecha)
    duales = [tk for tk, f in vivos.items() if f == "Duales"]
    patas = pedir(cli, [f"{tk} {s}" for tk in duales for s in PATAS], "ars", fecha)

    instr, sin_dato = [], defaultdict(list)

    def reg(ticker, fam, mon, v, extra=None):
        r = {"ticker": ticker, "familia": fam, "moneda": mon, "precio": v.get("precioDirty"),
             "tea": _pct(v.get("tea")), "durationMod": v.get("durationMod"),
             "paridad": _pct(v.get("paridad"))}
        r.update(extra or {})
        instr.append(r)

    for tk, fam in sorted(vivos.items()):
        mon = MONEDA.get(fam, "ars")
        v = datos.get((tk, mon))
        eco = tk + "D" if fam in ("Bonares", "Globales") else tk
        if not v or v.get("tea") is None:
            sin_dato[fam].append(eco)
            continue
        extra = {}
        if fam == "Globales" and tk in glob_mep and glob_mep[tk].get("tea") is not None:
            m = glob_mep[tk]
            extra["enMep"] = {"precio": m.get("precioDirty"), "tea": _pct(m.get("tea")),
                              "paridad": _pct(m.get("paridad"))}
        if fam == "Duales":
            pp = {}
            for s in PATAS:
                p = patas.get(f"{tk} {s}")
                if p and p.get("tea") is not None:
                    pp[TIPO_PATA[s]] = {"tea": _pct(p.get("tea")), "durationMod": p.get("durationMod"),
                                        "conDatos": True}
            if pp:
                extra["patas"] = pp
        reg(eco, fam, mon, v, extra)
    for eco, t1816, mon_sub in subsob:
        v = sub.get(t1816)
        if not v or v.get("tea") is None:
            sin_dato["Subsoberanos"].append(eco)
            continue
        m = sub_mep.get(t1816) or {}
        extra = {"enMep": {"precio": m.get("precioDirty"), "tea": _pct(m.get("tea")),
                           "paridad": _pct(m.get("paridad"))}} if m.get("tea") is not None else {}
        reg(eco, "Subsoberanos", mon_sub, v, extra)

    resumen = {}
    for fam in {r["familia"] for r in instr}:
        regs = [r for r in instr if r["familia"] == fam]
        teas = [r["tea"] for r in regs if r["tea"] is not None]
        m = median(teas) if teas else None
        raros = sorted(r["ticker"] for r in regs if m and len(teas) >= 4 and m > 0 and r["tea"] is not None
                       and (r["tea"] > m * 3 or r["tea"] < m / 3))
        resumen[fam] = {"instrumentos": len(regs), "convencionDudosa": raros,
                        "monedas": dict(Counter(r["moneda"] for r in regs))}
    tamar = tamar_a(fecha)
    return {
        "fecha": fecha.isoformat(), "tipos": ["semanal"], "reconstruido": True,
        "fuente": "1816 · /indicadores con fechaOperacion; universo del catálogo de 1816 a esa fecha",
        "instrumentos": instr, "resumen": resumen, "sinDatoPorFamilia": dict(sin_dato),
        "sinDato": [t for v in sin_dato.values() for t in v],
        "macro": {"inflacion": inflacion_a(fecha),
                  "series": {"tamarTEA": tamar} if tamar else {}},
    }


def publicar(ruta_json, fecha):
    import curvas_informe
    import pagina_curvas
    destino = REPO / "informes" / "curvas" / fecha
    destino.mkdir(parents=True, exist_ok=True)
    hechos = curvas_informe.generar(str(ruta_json), dir_salida=str(destino))
    if hechos:
        pagina_curvas.escribir(destino, hechos, fecha)
    return hechos


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fechas")
    ap.add_argument("--desde", default="2025-12-31")
    ap.add_argument("--hasta", default="2026-08-31")
    ap.add_argument("--sin-curvas", action="store_true")
    a = ap.parse_args(argv)
    fechas, fer = cierres_semanales(date.fromisoformat(a.desde), date.fromisoformat(a.hasta))
    if a.fechas:
        fechas = [date.fromisoformat(x) for x in a.fechas.split(",")]
    print(f"{len(fechas)} ruedas: {fechas[0]} … {fechas[-1]}")
    cli = Cliente1816()
    cat = catalogo(cli)
    print(f"catálogo: {len(cat)} instrumentos en las curvas exhaustivas")
    items = leer_tickers()
    subsob = [(it["eco"], it["t1816"], it.get("moneda") or "ccl") for it in items
              if it.get("hoja") == "Subsoberanos" and it.get("t1816")]
    seguidos = {it["t1816"] for it in items if it.get("t1816")}
    hoy = date.today()
    HIST.mkdir(parents=True, exist_ok=True)
    for f in fechas:
        propio = REPO / "informes" / f"datos_{f.isoformat()}.json"
        if propio.exists():
            ruta = propio
            print(f"{f}: ya tiene informe, se usa ese")
        else:
            d = reconstruir(cli, f, cat, subsob, seguidos, hoy)
            # Una rueda sin NINGÚN precio no operó, aunque el calendario diga hábil: el 31/12/2025
            # el sistema financiero no abrió (asueto bancario, que no es feriado nacional y por eso no
            # está en la lista). Se toma la rueda hábil anterior, que es el cierre real del período.
            atras = 0
            while not d["instrumentos"] and atras < 3:
                atras += 1
                f -= timedelta(days=1)
                while f.weekday() > 4 or f in fer:
                    f -= timedelta(days=1)
                print(f"   sin ningún precio: se toma la rueda anterior, {f}")
                d = reconstruir(cli, f, cat, subsob, seguidos, hoy)
            if not d["instrumentos"]:
                continue
            ruta = HIST / f"datos_{f.isoformat()}.json"
            ruta.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            fam = Counter(r["familia"] for r in d["instrumentos"])
            print(f"{f}: {len(d['instrumentos'])} instrumentos · {dict(fam)} · "
                  f"sin dato {len(d['sinDato'])}")
        if not a.sin_curvas:
            hechos = publicar(ruta, f.isoformat())
            print(f"   curvas: {len(hechos or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
