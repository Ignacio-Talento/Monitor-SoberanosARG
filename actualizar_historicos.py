"""
Script para actualizar historicos.xlsx con precios de cierre del día.
Se ejecuta automáticamente via GitHub Actions al cierre del mercado.
Los tickers se leen dinámicamente desde Instrumentos.xlsx.
"""

import requests
import openpyxl
from openpyxl import load_workbook
from datetime import date
import os
import re
import time

# Cliente de la API de 1816 (fuente primaria de precios). Import defensivo: si
# falta el archivo o la librería, el script sigue funcionando 100% con Eco Valores.
try:
    from precios_1816 import Cliente1816
except Exception as _e:
    Cliente1816 = None
    print(f"AVISO: cliente 1816 no disponible ({_e}); se usará solo Eco Valores.")

# ── CONFIGURACIÓN ─────────────────────────────────────────────
ECO_BASE        = "https://bonos.ecovalores.com.ar/eco/ticker.php"
HISTORICOS_FILE = "historicos.xlsx"
INSTRUMENTOS_FILE = "Instrumentos.xlsx"

# Grupos que usan sufijo D para buscar el precio en USD
GRUPOS_CON_D = {'USD Bonares', 'USD Globales'}

# ── MAPEO A LA API DE 1816 ────────────────────────────────────
# El valor que guarda Eco == 1816 'precioDirty' (dirty ya incorpora el residual
# de los amortizables). Moneda por hoja: 'ars' para instrumentos en pesos, 'mep'
# para los que Eco guarda en dólares (especie D). Verificado contra la API real.
CAMPO_1816 = "precioDirty"
MONEDA_1816 = {
    'LECAPS': 'ars', 'TASA FIJA': 'ars', 'CER': 'ars', 'TAMAR': 'ars',
    'USD Linked': 'ars', 'Duales': 'ars',
    'USD Bonares': 'mep', 'USD Globales': 'mep',
    'USD Bopreales': 'mep', 'ON USD': 'mep',
}
# Bopreales: el ticker de 1816 es irregular (no es un simple swap), mapa explícito.
MAPA_BOPREAL_1816 = {
    'BPC7D': 'BPOC7', 'BPD7D': 'BPOD7', 'BPA8D': 'BPOA8', 'BPB8D': 'BPOB8',
}

def resolver_1816(sheet_name, eco_ticker, master_ticker):
    """Devuelve (ticker_1816, moneda) para consultar 1816, o (None, None) si no aplica.
    - Bonares/Globales: 1816 usa el ticker del master (sin la 'D' que agrega Eco).
    - ON USD: swap de la 'D' final por 'O' (RUCED -> RUCEO).
    - Bopreales: mapa explícito.
    - Resto (pesos): mismo ticker.
    Cualquier caso no resuelto -> (None, None) => fallback a Eco.
    """
    moneda = MONEDA_1816.get(sheet_name)
    if moneda is None:
        return None, None
    if sheet_name in GRUPOS_CON_D:
        return master_ticker, moneda
    if sheet_name == 'USD Bopreales':
        return MAPA_BOPREAL_1816.get(eco_ticker), moneda
    if sheet_name == 'ON USD':
        t = (eco_ticker[:-1] + 'O') if eco_ticker.endswith('D') else None
        return t, moneda
    return eco_ticker, moneda  # pesos: idéntico

# ── LEER TICKERS DESDE INSTRUMENTOS.XLSX ─────────────────────
def leer_tickers():
    """Devuelve una lista de dicts: {'eco', 't1816', 'moneda'} por instrumento.
    'eco' es la columna de historicos (igual que antes); 't1816'/'moneda' se usan
    para pedir el precio a 1816 (None si el instrumento no mapea a 1816)."""
    if not os.path.exists(INSTRUMENTOS_FILE):
        print(f"ERROR: No se encontró {INSTRUMENTOS_FILE}")
        return []

    wb = load_workbook(INSTRUMENTOS_FILE, read_only=True, data_only=True)
    items = []
    vistos = set()

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # Buscar fila de header (contiene 'Ticker')
        header_row = None
        for i, row in enumerate(rows):
            if row and str(row[0]).strip() == 'Ticker':
                header_row = i
                break

        if header_row is None:
            continue

        headers = [str(c).strip() if c else '' for c in rows[header_row]]
        ticker_col = headers.index('Ticker') if 'Ticker' in headers else 0

        for row in rows[header_row + 1:]:
            if not row or not row[ticker_col]:
                continue
            ticker = str(row[ticker_col]).strip()
            if not ticker or ticker == 'Ticker' or ticker == 'None':
                continue

            # Agregar sufijo D para Bonares y Globales
            eco_ticker = ticker + 'D' if sheet_name in GRUPOS_CON_D else ticker

            if eco_ticker in vistos:
                continue
            vistos.add(eco_ticker)

            t1816, moneda = resolver_1816(sheet_name, eco_ticker, ticker)
            items.append({'eco': eco_ticker, 't1816': t1816, 'moneda': moneda})

    print(f"Tickers leídos desde {INSTRUMENTOS_FILE}: {len(items)}")
    return items

# ── FETCH PRECIOS DESDE 1816 (fuente primaria) ────────────────
def fetch_precios_1816(items, fecha=None):
    """Devuelve {eco_ticker: precio} solo para los que 1816 respondió con dato.
    `fecha` (AAAA-MM-DD) es opcional: por defecto usa el día de hoy (producción);
    se puede fijar para pruebas o backfills puntuales.
    Ante cualquier problema (sin key, sin cliente, error de red/API) devuelve {}
    y el flujo cae a Eco Valores para todo. Nunca rompe la corrida."""
    if Cliente1816 is None:
        return {}
    if not (os.environ.get("API_1816_KEY") or os.path.exists(".1816_key")):
        print("AVISO: no hay API_1816_KEY; se usará solo Eco Valores.")
        return {}

    # Agrupar por moneda: {moneda: [(eco, t1816), ...]}
    por_moneda = {}
    for it in items:
        if it['t1816'] and it['moneda']:
            por_moneda.setdefault(it['moneda'], []).append((it['eco'], it['t1816']))

    if not por_moneda:
        return {}

    resultado = {}
    try:
        cli = Cliente1816()
        for moneda, pares in por_moneda.items():
            tickers = [t for _, t in pares]
            filas = cli.precios(tickers, [CAMPO_1816], moneda=moneda, fecha_operacion=fecha)
            valor_por_t = {f['ticker']: f.get(CAMPO_1816) for f in filas}
            for eco, t in pares:
                v = valor_por_t.get(t)
                if isinstance(v, (int, float)):
                    resultado[eco] = v
    except Exception as e:
        print(f"AVISO: 1816 no disponible ({e}); se usará Eco para todo.")
        return {}

    print(f"1816: {len(resultado)} precios obtenidos de {sum(len(v) for v in por_moneda.values())} consultables.")
    return resultado

# ── FETCH PRECIO ──────────────────────────────────────────────
def fetch_precio(ticker):
    try:
        url = f"{ECO_BASE}?t={ticker}"
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
            "Referer": "https://bonos.ecovalores.com.ar"
        })
        html = resp.text
        match = re.search(r'<td class="precioticker">\s*([\d.,]+)\s*</td>', html)
        if match:
            price_str = match.group(1).replace(".", "").replace(",", ".")
            return float(price_str)
    except Exception as e:
        print(f"  Error fetching {ticker}: {e}")
    return None

# ── ACTUALIZAR EXCEL ──────────────────────────────────────────
def actualizar_historicos():
    hoy = date.today()
    fecha_str = hoy.strftime("%Y-%m-%d")
    print(f"Actualizando historicos para {fecha_str}...")

    # Leer tickers dinámicamente
    items = leer_tickers()
    if not items:
        print("ERROR: No se pudieron leer los tickers.")
        return
    tickers = [it['eco'] for it in items]

    # Cargar o crear el Excel
    if os.path.exists(HISTORICOS_FILE):
        wb = load_workbook(HISTORICOS_FILE)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Historicos"
        ws.cell(row=1, column=1, value="Fecha")
        print("Archivo historicos.xlsx creado desde cero.")

    # Verificar si ya existe la fila de hoy
    for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
        if row[0] and str(row[0])[:10] == fecha_str:
            print(f"Ya existe fila para {fecha_str}, saliendo.")
            return

    # Próxima fila vacía
    next_row = ws.max_row + 1
    ws.cell(row=next_row, column=1, value=fecha_str)

    # Asegurar que todos los tickers estén en el header
    header = {ws.cell(row=1, column=c).value: c for c in range(2, ws.max_column + 1)}
    for ticker in tickers:
        if ticker not in header:
            new_col = ws.max_column + 1
            ws.cell(row=1, column=new_col, value=ticker)
            header[ticker] = new_col
            print(f"  Nuevo ticker agregado al header: {ticker}")

    # Precios primero desde 1816 (fuente primaria); lo que falte, desde Eco.
    precios_api = fetch_precios_1816(items)

    n1816 = 0
    neco = 0
    err = 0
    for it in items:
        ticker = it['eco']
        precio = precios_api.get(ticker)
        if precio is not None:
            fuente = "1816"
        else:
            # Fallback: scraping de Eco Valores (comportamiento original).
            print(f"  Fetching {ticker} (Eco)...", end=" ")
            precio = fetch_precio(ticker)
            print(f"${precio}" if precio else "sin precio")
            time.sleep(0.4)  # throttle solo cuando efectivamente pegamos a Eco
            fuente = "eco"

        if precio:
            ws.cell(row=next_row, column=header[ticker], value=precio)
            if fuente == "1816":
                n1816 += 1
            else:
                neco += 1
        else:
            err += 1

    wb.save(HISTORICOS_FILE)
    print(f"\nListo: {n1816 + neco} precios guardados "
          f"(1816: {n1816}, Eco: {neco}), {err} sin precio.")

if __name__ == "__main__":
    actualizar_historicos()
