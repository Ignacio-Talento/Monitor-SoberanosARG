#!/usr/bin/env python3
"""
Reexpresa en CCL la historia de los instrumentos que pasaron a valuarse en esa moneda.

POR QUÉ. El monitor pasó a mostrar globales, provinciales y las ONs que pagan en CCL en esa punta.
El histórico seguía en MEP, y eso no es un detalle cosmético: las columnas DAY/WTD/MTD/YTD comparan
el precio de hoy contra un cierre guardado, así que mezclar las dos puntas daba variaciones
infladas por la brecha entre MEP y CCL —números plausibles que no significan lo que dicen—.

QUÉ HACE. Pide a 1816 la serie completa de esos tickers en CCL y PISA los valores que ya están en
historicos.xlsx. Es el único script del repo que sobrescribe: los demás sólo llenan huecos. Por eso
el dry-run es el default y hay que pedir explícitamente que escriba.

CONTROL DE CORDURA. Antes de escribir compara cada valor nuevo contra el que había y reporta la
razón CCL/MEP. Tiene que dar una brecha chica y consistente (del orden de 1,00 a 1,10). Si apareciera
un factor de 1.500 sería que algo vino en pesos, y ahí conviene frenar en vez de escribir.

USO
    python reexpresar_ccl.py                 # dry-run: qué cambiaría y con qué brecha
    python reexpresar_ccl.py --escribir      # aplica
    python reexpresar_ccl.py --escribir TLCPD GD30D   # sólo esos
"""

import sys
from statistics import median

try:
    from openpyxl import load_workbook
except ImportError:
    print("ERROR: falta openpyxl", file=sys.stderr)
    sys.exit(1)

from actualizar_historicos import CAMPO_1816, HISTORICOS_FILE, leer_tickers

try:
    from precios_1816 import Cliente1816, Error1816, MAX_TICKERS_SERIES
except ImportError:
    print("ERROR: no se pudo importar precios_1816", file=sys.stderr)
    sys.exit(1)

ESPERAS = [15, 45, 90]
# Fuera de esta banda, el valor nuevo no es "el mismo bono en otra punta" sino otra cosa —otra
# moneda, otra unidad—. Se descarta en vez de pisar la serie con basura.
RAZON_MIN, RAZON_MAX = 0.85, 1.30


def leer(ws):
    cab = [str(c.value) if c.value is not None else "" for c in next(ws.iter_rows(max_row=1))]
    fila_de = {}
    for r in range(2, ws.max_row + 1):
        f = str(ws.cell(row=r, column=1).value or "")[:10]
        if len(f) == 10 and f[4] == "-":
            fila_de[f] = r
    return cab, fila_de


def pedir(cli, tickers, desde, hasta):
    import time
    ultimo = None
    out = []
    for i in range(0, len(tickers), MAX_TICKERS_SERIES):
        lote = tickers[i:i + MAX_TICKERS_SERIES]
        for espera in [0] + ESPERAS:
            if espera:
                print(f"    reintentando en {espera}s...", file=sys.stderr)
                time.sleep(espera)
            try:
                out += cli.series(lote, [CAMPO_1816], moneda="ccl",
                                  fecha_inicial=desde, fecha_final=hasta)
                break
            except Error1816 as e:
                ultimo = e
                if "429" not in str(e) and "Demasiadas" not in str(e):
                    break
            except Exception as e:
                ultimo = e
        else:
            print(f"    lote {i // MAX_TICKERS_SERIES}: sin respuesta ({ultimo})", file=sys.stderr)
    return out


def main(argv):
    escribir = "--escribir" in argv
    pedidos = {a.upper() for a in argv if not a.startswith("--")}

    enCCL = [it for it in leer_tickers()
             if it["moneda"] == "ccl" and it["t1816"]
             and (not pedidos or it["eco"] in pedidos)]
    if not enCCL:
        print("No hay instrumentos en CCL para reexpresar.")
        return 1

    wb = load_workbook(HISTORICOS_FILE)
    ws = wb["Historicos"]
    cab, fila_de = leer(ws)
    col = {t: i + 1 for i, t in enumerate(cab) if t}
    fechas = sorted(fila_de)
    objetivo = [it for it in enCCL if it["eco"] in col]
    print(f"{len(objetivo)} instrumentos en CCL · ruedas {fechas[0]} a {fechas[-1]}")
    if len(objetivo) < len(enCCL):
        print(f"  ({len(enCCL) - len(objetivo)} todavía sin columna en el histórico, se saltean)")

    cli = Cliente1816()
    inv = {it["t1816"]: it["eco"] for it in objetivo}
    filas = pedir(cli, sorted(inv), fechas[0], fechas[-1])

    cambios, razones, raros = [], [], []
    for r in filas:
        v = r.get(CAMPO_1816)
        eco = inv.get(r.get("ticker"))
        f = str(r.get("fecha", ""))[:10]
        if v is None or not eco or f not in fila_de or not isinstance(v, (int, float)) or v <= 0:
            continue
        celda = ws.cell(row=fila_de[f], column=col[eco])
        viejo = celda.value
        if isinstance(viejo, (int, float)) and viejo > 0:
            razon = v / viejo
            if not (RAZON_MIN <= razon <= RAZON_MAX):
                raros.append((eco, f, viejo, v, razon))
                continue
            razones.append(razon)
        cambios.append((celda, v))

    print(f"\n{len(cambios)} celdas a reexpresar")
    if razones:
        razones.sort()
        print(f"  brecha CCL/MEP: mediana {median(razones):.4f} · "
              f"mín {razones[0]:.4f} · máx {razones[-1]:.4f}")
    if raros:
        print(f"  {len(raros)} descartadas por quedar fuera de la banda {RAZON_MIN}-{RAZON_MAX}:")
        for eco, f, viejo, nuevo, razon in raros[:5]:
            print(f"    {eco} {f}: {viejo} -> {nuevo} (x{razon:.2f})")

    if not escribir:
        print("\n(dry-run: no se escribió nada. Agregá --escribir para aplicar)")
        return 0
    for celda, v in cambios:
        celda.value = v
    wb.save(HISTORICOS_FILE)
    print(f"\n{HISTORICOS_FILE} actualizado: {len(cambios)} celdas ahora en CCL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
