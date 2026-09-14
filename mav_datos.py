#!/usr/bin/env python3
"""Baja los informes del MAV y deja mav_series.json para la solapa MAV del monitor.

    py mav_datos.py                      la última semana publicada (y el último mensual)
    py mav_datos.py --semanas 60         reconstruye hasta 60 semanas hacia atrás
    py mav_datos.py --sin-mensual        sólo semanas

QUÉ ES EL MAV. El Mercado Argentino de Valores negocia el financiamiento PyME: cheques de pago
diferido (ECHEQ y físicos), pagarés bursátiles y facturas de crédito electrónicas (FCE). Es el
tramo corto del crédito privado y NO está en 1816 —no hay curva de cheques ni de pagarés—, así que
es dato nuevo para el monitor y sin segunda fuente con la que cruzarlo.

DE DÓNDE SALE. MAV no tiene API ni dato en vivo: publica un informe SEMANAL (los lunes, con la
semana cerrada el viernes) y uno MENSUAL, como ZIP en su WordPress. Los ZIP se encuentran por la API
REST del sitio (/wp-json/wp/v2/media?search=...), que además da la fecha de publicación. Ojo con el
rezago: el dato de una rueda del lunes recién se ve el lunes siguiente, y en el último año hubo seis
huecos de 9 a 11 días por feriados. La solapa lo dice.

QUÉ SE LEE. El «Anexo I. Base de datos» del ZIP semanal trae OPERACIÓN POR OPERACIÓN —11.502 en la
semana del 07/09/2026— con plazo, tasa, garantía, moneda, montos y el avalista o deudor cedido. De
ahí sale todo: las tasas de referencia y la curva por tramo se calculan acá y no se leen del resumen
del propio informe. Está verificado: el promedio ponderado por monto nominal del cheque avalado en
pesos reproduce EXACTO los índices que publica MAV en el PDF (MAV 30 24,82%, MAV 90 25,78% y
MAV 200 27,47% la semana del 07/09/2026). El promedio simple no: da 25,02% en el tramo corto.

LA TASA ES TNA VENCIDA BASE 365, no descuento directo: el monto liquidado es
    liquidado = nominal / (1 + TNA × días/365)
Despejado contra los montos del propio anexo cierra al peso (33% a 28 días da 2,4690% de descuento).
Por eso la TEA se calcula como (1 + TNA × días/365)^(365/días) − 1, y es la única forma de comparar
esto contra la curva LECAP: a igual plazo, la TNA de MAV corre 3 a 5 puntos debajo de su TEA.

LO QUE NO ENTRA A LA CURVA. Los pagarés con ajuste (TAMAR, SOJA, BADLAR) cotizan un MARGEN sobre el
índice, no una tasa fija: van contados aparte, en `ajustables`. Y las monedas vienen con cuatro
etiquetas que no son la misma cosa —$, U$S al tipo de cambio del BNA, U$D al A3500 y DOL hard
dollar—, así que los montos en dólares se informan por etiqueta y no se suman entre sí.
"""
import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent
SALIDA = REPO / "mav_series.json"
# Los ZIP pesan 1,5 MB el semanal y 6 el mensual: se cachean en una carpeta ignorada por git.
CACHE = REPO / ".cache_mav"
API = "https://mav-sa.com.ar/wp-json/wp/v2/media"
CAB = {"User-Agent": "Mozilla/5.0 (monitor renta fija; uso interno)"}

TRAMOS = [(0, 30), (31, 60), (61, 90), (91, 120), (121, 180), (181, 365), (366, 9999)]
# Etiqueta de moneda del anexo -> (grupo, a qué tipo de cambio está expresado el monto)
MONEDAS = {"$": ("ARS", "pesos"), "U$S": ("USD", "BNA"), "U$D": ("USD", "A3500"),
           "DOL": ("USD", "hard dollar")}
GARANTIAS = {"Avalista": "avalado", "Ag. Vendedor": "garantizado", "No Garantizado": "directo"}
MESES_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
            "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
            "diciembre": 12}


def _get_json(url):
    r = urllib.request.Request(url, headers=CAB)
    with urllib.request.urlopen(r, timeout=60) as f:
        return json.load(f)


def listar(busqueda, paginas=3):
    """[{fecha, url, nombre}] de los adjuntos del sitio que matchean, del más nuevo al más viejo."""
    out = []
    for p in range(1, paginas + 1):
        try:
            d = _get_json(f"{API}?search={busqueda}&per_page=100&page={p}&orderby=date&order=desc")
        except Exception:                                         # noqa: BLE001
            break
        if not d:
            break
        out += [{"fecha": m["date"][:10], "url": m["source_url"],
                 "nombre": m["source_url"].rsplit("/", 1)[-1]} for m in d]
    return out


PAUSA = 2.0          # segundos entre descargas; la reconstrucción larga lo sube (ver --pausa)


def bajar(url):
    """Bytes del ZIP, con caché en disco: la reconstrucción de un año son 57 archivos."""
    CACHE.mkdir(exist_ok=True)
    local = CACHE / url.rsplit("/", 1)[-1]
    if local.exists() and local.stat().st_size > 1000:
        return local.read_bytes()
    # El sitio contesta 429 cuando se le piden muchos archivos seguidos, y con una ventana larga:
    # cinco intentos esperando hasta cinco minutos, más la pausa entre descargas. Para la corrida
    # semanal —un archivo— nada de esto se activa; la reconstrucción de un año conviene lanzarla
    # con `--pausa 10`, que la hace lenta pero la termina.
    for intento in range(5):
        try:
            r = urllib.request.Request(url, headers=CAB)
            with urllib.request.urlopen(r, timeout=180) as f:
                b = f.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or intento == 4:
                raise
            time.sleep((30, 60, 120, 300)[intento])
    local.write_bytes(b)
    time.sleep(PAUSA)
    return b


def rango(nombre):
    """('2026-09-07', '2026-09-11') del nombre del archivo, que trae separadores variados."""
    ds = re.findall(r"(\d{2})-(\d{2})-(\d{2})(?!\d)", nombre)
    if len(ds) < 2:
        return None, None

    def iso(t):
        d, m, a = t
        return f"20{a}-{m}-{d}"
    return iso(ds[0]), iso(ds[1])


def _libro(zb, patron):
    """El primer xlsx del ZIP cuyo nombre matchea, ya abierto."""
    z = zipfile.ZipFile(io.BytesIO(zb))
    nombres = [n for n in z.namelist() if re.search(patron, n, re.I) and n.lower().endswith("xlsx")]
    if not nombres:
        return None
    return openpyxl.load_workbook(io.BytesIO(z.read(nombres[0])), read_only=True, data_only=True)


def operaciones(zb):
    """Las operaciones del Anexo I, normalizadas."""
    wb = _libro(zb, r"anexo\s*i[.\s]")
    if wb is None:
        return []
    filas = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
    if not filas:
        return []
    cab = [str(c).strip() if c is not None else "" for c in filas[0]]
    ix = {c: k for k, c in enumerate(cab)}
    falta = [c for c in ("Operatoria", "Mon.", "Plazo", "Tasa", "Monto Nominal") if c not in ix]
    if falta:
        print(f"AVISO: el anexo no trae {falta}; se ignora", file=sys.stderr)
        return []
    col_aval = next((c for c in cab if c.startswith("Razon Act")), None)
    out = []
    for f in filas[1:]:
        if not f or f[0] is None:
            continue
        op = str(f[ix["Operatoria"]] or "")
        plazo, tasa, nominal = f[ix["Plazo"]], f[ix["Tasa"]], f[ix["Monto Nominal"]]
        if not op or not plazo or nominal in (None, 0):
            continue
        mon, tc = MONEDAS.get(str(f[ix["Mon."]] or "").strip(), ("otra", "?"))
        inst = ("CPD" if ("ECHEQ" in op or "CH.DIF" in op) else
                "Pagaré" if "PAGARE" in op else "FCE" if "FCE" in op else "otro")
        out.append({
            "fecha": str(f[0])[:10], "instrumento": inst, "moneda": mon, "tc": tc,
            "garantia": GARANTIAS.get(str(f[ix.get("Gtia.", 0)] or "").strip(), "directo"),
            "plazo": int(plazo), "tasa": float(tasa) if tasa is not None else None,
            "nominal": float(nominal),
            "ajuste": next((a for a in ("TAMAR", "SOJA", "BADLAR") if a in op.upper()), None),
            "avalista": (str(f[ix[col_aval]]) if col_aval and f[ix[col_aval]] else None),
        })
    return out


def tea(tna, dias):
    """TNA vencida 365 -> TEA. Sin esto no se puede comparar contra la curva en pesos."""
    return ((1 + tna / 100 * dias / 365) ** (365 / dias) - 1) * 100


def tramo(dias):
    for a, b in TRAMOS:
        if a <= dias <= b:
            return f"{a}-{b}"
    return None


def _pond(sel):
    """Promedio ponderado POR MONTO NOMINAL, que es la convención con la que MAV calcula sus
    índices (verificado contra el PDF: el simple da 20 pb más en el tramo corto)."""
    vol = sum(o["nominal"] for o in sel)
    if not vol:
        return None
    return {
        "tna": round(sum(o["tasa"] * o["nominal"] for o in sel) / vol, 3),
        "tea": round(sum(tea(o["tasa"], o["plazo"]) * o["nominal"] for o in sel) / vol, 3),
        "dias": round(sum(o["plazo"] * o["nominal"] for o in sel) / vol, 1),
        "vol": round(vol, 2), "ops": len(sel),
    }


def indices(ops):
    """Las tres tasas de referencia que publica MAV: cheque avalado en pesos por tramo de plazo."""
    base = [o for o in ops if o["instrumento"] == "CPD" and o["moneda"] == "ARS"
            and o["garantia"] == "avalado" and o["tasa"] is not None and not o["ajuste"]]
    out = {}
    for clave, lo, hi in (("mav30", 0, 60), ("mav90", 61, 120), ("mav200", 121, 365)):
        r = _pond([o for o in base if lo <= o["plazo"] <= hi])
        out[clave] = None if not r else {"tna": r["tna"], "tea": r["tea"], "ops": r["ops"],
                                         "vol": r["vol"]}
    return out


def curva_lecap(hasta):
    """[(días, TEA)] de la curva de tasa fija de esa rueda, del archivo del informe diario.

    Va adentro del JSON del MAV a propósito: la solapa dibuja el spread PyME contra la curva
    soberana del MISMO viernes y no contra la de hoy, que es otra rueda.
    """
    for salto in range(0, 5):
        d = date.fromordinal(date.fromisoformat(hasta).toordinal() - salto)
        ruta = REPO / "informes" / f"datos_{d.isoformat()}.json"
        if not ruta.exists():
            continue
        js = json.loads(ruta.read_text(encoding="utf-8"))
        pts = sorted((round(x["durationMod"] * 365, 1), round(x["tea"], 3))
                     for x in js["instrumentos"]
                     if x["familia"] == "LECAPs y tasa fija" and x.get("tea") is not None)
        if pts:
            return {"fecha": js["fecha"], "puntos": pts}
    return None


def resumen_semana(ops, desde, hasta, publicado, archivo=None):
    curva = {}
    for o in ops:
        if o["tasa"] is None or o["ajuste"]:
            continue
        t = tramo(o["plazo"])
        if t:
            curva.setdefault(f"{o['instrumento']} {o['moneda']} {o['garantia']}", {}).setdefault(t, []).append(o)
    curva = {seg: {t: _pond(v) for t, v in sorted(tr.items(), key=lambda kv: int(kv[0].split("-")[0]))}
             for seg, tr in sorted(curva.items())}

    volumen = {}
    for o in ops:
        v = volumen.setdefault(f"{o['instrumento']} {o['moneda']}", {"vol": 0.0, "ops": 0, "tc": o["tc"]})
        v["vol"] += o["nominal"]
        v["ops"] += 1
    for v in volumen.values():
        v["vol"] = round(v["vol"], 2)

    aval, ajust = defaultdict(lambda: {"vol": 0.0, "ops": 0}), defaultdict(lambda: {"vol": 0.0, "ops": 0})
    for o in ops:
        if o["garantia"] == "avalado" and o["avalista"]:
            a = aval[o["avalista"]]
            a["vol"] += o["nominal"]
            a["ops"] += 1
        if o["ajuste"]:
            a = ajust[o["ajuste"]]
            a["vol"] += o["nominal"]
            a["ops"] += 1
    top = sorted(({"nombre": k, "vol": round(v["vol"], 2), "ops": v["ops"]} for k, v in aval.items()),
                 key=lambda x: -x["vol"])[:12]
    return {
        "desde": desde, "hasta": hasta, "publicado": publicado, "archivo": archivo,
        "operaciones": len(ops),
        "indices": indices(ops), "curva": curva, "volumen": volumen, "avalistas": top,
        "ajustables": {k: {"vol": round(v["vol"], 2), "ops": v["ops"]} for k, v in ajust.items()},
        "lecap": curva_lecap(hasta),
    }


def mensual(zb):
    """Del ZIP mensual: el ranking de avalistas mes a mes —una hoja por mes desde enero de 2023— y
    el de agentes del mes. El volumen mensual no sale de acá: se suma de las semanas."""
    out = {}
    wb = _libro(zb, r"anexo\s*ii[.\s]")
    if wb is not None:
        aval = {}
        for hoja in wb.sheetnames:
            # Las hojas van «Enero 23» los primeros años y «Enero 2026» los últimos: el año viene
            # con dos o con cuatro dígitos en el mismo libro, y tomar sólo dos daba 2020 para 2023.
            m = re.match(r"([A-Za-zÁÉÍÓÚáéíóú]+)\s*(\d{2}|\d{4})\s*$", hoja.strip())
            if not m or m.group(1).lower() not in MESES_ES:
                continue
            anio = m.group(2) if len(m.group(2)) == 4 else f"20{m.group(2)}"
            clave = f"{anio}-{MESES_ES[m.group(1).lower()]:02d}"
            filas = [f for f in wb[hoja].iter_rows(values_only=True)
                     if f and f[0] and len(f) > 2 and isinstance(f[1], (int, float))]
            top = sorted(({"nombre": str(f[0]).strip(), "vol": round(float(f[1]), 2),
                           "ops": int(f[2] or 0)} for f in filas
                          if not str(f[0]).strip().lower().startswith("total")),
                         key=lambda x: -x["vol"])[:15]
            if top:
                aval[clave] = top
        out["avalistas"] = dict(sorted(aval.items()))
    wb = _libro(zb, r"anexo\s*iii[.\s]")
    if wb is not None:
        agentes, seccion = {}, None
        for f in wb[wb.sheetnames[0]].iter_rows(values_only=True):
            if not f:
                continue
            c0 = str(f[0]).strip() if f[0] is not None else ""
            c1 = str(f[1]).strip() if len(f) > 1 and f[1] is not None else ""
            # Las cabeceras de sección vienen sin número, con el instrumento en la 2ª columna.
            if not c0 and c1 and len(f) > 2 and isinstance(f[2], (int, float)):
                seccion = c1
                agentes.setdefault(seccion, {"compra": [], "venta": []})
                continue
            if seccion and c0.isdigit() and c1 and len(f) > 2 and isinstance(f[2], (int, float)):
                if len(agentes[seccion]["compra"]) < 10:
                    agentes[seccion]["compra"].append({"agente": c1, "vol": round(float(f[2]), 2)})
                if len(f) > 7 and f[6] and isinstance(f[7], (int, float)) \
                        and len(agentes[seccion]["venta"]) < 10:
                    agentes[seccion]["venta"].append({"agente": str(f[6]).strip(),
                                                      "vol": round(float(f[7]), 2)})
        out["agentes"] = agentes
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--semanas", type=int, default=1, help="cuántas semanas hacia atrás")
    ap.add_argument("--sin-mensual", action="store_true")
    ap.add_argument("--pausa", type=float, default=2.0,
                    help="segundos entre descargas; subilo para reconstruir muchas semanas")
    ap.add_argument("--completar", type=int, default=0,
                    help="además de las últimas, rellena hasta N semanas viejas que falten")
    a = ap.parse_args(argv)
    globals()["PAUSA"] = a.pausa

    previo = json.loads(SALIDA.read_text(encoding="utf-8")) if SALIDA.exists() else {}
    por_semana = {s["hasta"]: s for s in previo.get("semanas", [])}

    archivos = listar("Informe-Semanal")
    # De los 57 adjuntos, cinco son la misma semana subida dos veces —la segunda con sufijo «-1» y
    # con el dato corregido: la del 26/01/2026 pasa de 8.790 a 8.846 operaciones—, así que son 52
    # semanas distintas. La lista viene de la más nueva a la más vieja, y la primera aparición de
    # cada semana es la subida vigente: las siguientes se saltean.
    distintas = {}
    for x in archivos:
        h = rango(x["nombre"])[1]
        if h and h not in distintas:
            distintas[h] = x
    archivos = sorted(distintas.values(), key=lambda x: x["fecha"], reverse=True)
    print(f"{len(archivos)} semanas distintas en el sitio; se revisan {min(a.semanas, len(archivos))}")
    nuevos = 0
    for arch in archivos[:a.semanas]:
        desde, hasta = rango(arch["nombre"])
        if not hasta:
            print(f"  {arch['nombre']}: no se pudo leer el rango de fechas", file=sys.stderr)
            continue
        guardada = por_semana.get(hasta, {})
        # Las semanas viejas del archivo no guardan de qué adjunto salieron: se dan por buenas.
        if guardada.get("operaciones") and guardada.get("archivo", arch["nombre"]) == arch["nombre"]:
            continue
        try:
            ops = operaciones(bajar(arch["url"]))
        except Exception as e:                                    # noqa: BLE001
            print(f"  {hasta}: FALLÓ ({e})", file=sys.stderr)
            continue
        if not ops:
            print(f"  {hasta}: sin operaciones legibles", file=sys.stderr)
            continue
        por_semana[hasta] = resumen_semana(ops, desde, hasta, arch["fecha"], arch["nombre"])
        i = por_semana[hasta]["indices"]
        def t(k):
            return i[k]["tna"] if i.get(k) else "—"
        print(f"  {desde} a {hasta}: {len(ops)} ops · MAV 30 {t('mav30')}% · MAV 90 {t('mav90')}% · "
              f"MAV 200 {t('mav200')}%")
        nuevos += 1

    # RELLENO DE A POCO. El sitio del MAV devuelve 429 cuando se le piden muchos ZIP seguidos y la
    # ventana del límite es larga, así que reconstruir un año de una sentada no siempre entra. El job
    # semanal pide unas pocas semanas viejas en cada corrida —de la más nueva que falte hacia atrás—
    # y el archivo se completa solo en unas semanas, sin castigar al sitio ni depender de una corrida
    # larga que puede fallar a mitad de camino.
    if a.completar:
        faltan = [x for x in archivos if not por_semana.get(rango(x["nombre"])[1] or "")]
        print(f"faltan {len(faltan)} semanas del archivo; se intentan {min(a.completar, len(faltan))}")
        for arch in faltan[:a.completar]:
            desde, hasta = rango(arch["nombre"])
            try:
                ops = operaciones(bajar(arch["url"]))
            except Exception as e:                                # noqa: BLE001
                print(f"  {hasta}: FALLÓ ({e}); queda para la próxima corrida", file=sys.stderr)
                continue
            if not ops:
                continue
            por_semana[hasta] = resumen_semana(ops, desde, hasta, arch["fecha"], arch["nombre"])
            print(f"  + {desde} a {hasta}: {len(ops)} ops")
            nuevos += 1

    doc = {
        "generado": date.today().isoformat(),
        "fuente": "MAV · informes semanales y mensuales de CPD, pagaré y FCE (mav-sa.com.ar)",
        "convencion": ("La tasa del MAV es TNA vencida base 365: liquidado = nominal / (1 + TNA × "
                       "días/365). La TEA se deriva como (1 + TNA × días/365)^(365/días) − 1 y es la "
                       "única forma de compararla contra la curva de pesos."),
        "aviso": ("Dato semanal, no diario: MAV publica los lunes la semana cerrada el viernes, y en "
                  "el último año hubo seis huecos de 9 a 11 días por feriados. Las tasas son el "
                  "promedio ponderado por monto de lo que efectivamente se operó, no puntas de "
                  "pantalla."),
        "tramos": [f"{x}-{y}" for x, y in TRAMOS],
        "semanas": [por_semana[k] for k in sorted(por_semana)],
        "mensual": previo.get("mensual", {}),
    }
    if not a.sin_mensual:
        mens = listar("Informe-mensual")
        if mens:
            m = re.search(r"([A-Za-z]+)-(\d{4})", mens[0]["nombre"])
            try:
                doc["mensual"] = {"archivo": mens[0]["nombre"], "publicado": mens[0]["fecha"],
                                  "mes": (m.group(0) if m else None), **mensual(bajar(mens[0]["url"]))}
                print(f"mensual {doc['mensual'].get('mes')}: "
                      f"{len(doc['mensual'].get('avalistas', {}))} meses de avalistas · "
                      f"{len(doc['mensual'].get('agentes', {}))} secciones de agentes")
            except Exception as e:                                # noqa: BLE001
                print(f"mensual: FALLÓ ({e})", file=sys.stderr)

    SALIDA.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{SALIDA.name}: {len(doc['semanas'])} semanas · {SALIDA.stat().st_size // 1024} KB "
          f"({nuevos} nuevas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
