---
name: renta-fija-argentina-1816
description: >-
  Trae datos de mercado de renta fija argentina (bonos y letras soberanos, provinciales,
  corporativos y BCRA) usando la API de 1816 mediante el script local precios_1816.py.
  Cubre precios (clean/dirty), rendimientos (TNA/TEA/TEM/current yield), paridad, spread,
  duration, volumen y valor técnico; foto de un día o series históricas; valuación en ARS,
  CCL o MEP; búsqueda de instrumentos/curvas; y entrega como tabla, Excel/CSV/JSON o gráfico.
  USALO SIEMPRE que el usuario pida información sobre bonos o renta fija argentina —precio,
  tasa, rendimiento, paridad, duration, volumen, evolución/histórico, comparar o graficar—
  o mencione tickers como AL30, GD30, GD35, AE38, Bonares, Globales, Lecap, Lecer, Boncer,
  Botes, duales, CER, Badlar, Tamar, dólar-linked, aunque no nombre "1816" explícitamente.
  NO aplica a acciones, ADRs, opciones, futuros, FX ni a la caja de puntas / order book
  (la API no ofrece libro de órdenes ni intradiario tick a tick).
---

# Datos de renta fija argentina vía API de 1816

Esta skill te permite responder cualquier pedido de datos de **renta fija argentina** usando
la herramienta local `precios_1816.py`, que envuelve la API de 1816 (auth, rate-limit,
batching, series con auto-chunking, y export). El usuario pide en lenguaje natural; vos
traducís a una llamada al script y devolvés tabla, archivo o gráfico.

## Setup (obligatorio antes de correr nada)

- **Carpeta base:** `C:\Users\Usuario\Downloads\API 1816` — ahí viven `precios_1816.py`,
  la API Key (`.1816_key`) y el cache de token. **Corré siempre desde esa carpeta** (o con
  `sys.path` apuntando ahí) para que encuentre la key y el token.
- **Python:** en esta máquina se invoca con **`py`** (no `python`, que es el stub del Store).
- **Encoding:** exportá `PYTHONIOENCODING=utf-8` antes de correr (si no, la salida con
  tildes/unicode puede tirar `UnicodeEncodeError`).
- **Dependencias:** `requests`, `openpyxl` (export xlsx), `matplotlib`+`pandas` (gráficos) —
  ya instaladas.
- **Credenciales:** NUNCA leas, imprimas ni pegues la API Key. El script la toma solo de
  `.1816_key` o de `API_1816_KEY`. Si falta, pedile al usuario que la configure él.

Chequeo de vida barato (no gasta créditos de datos): `py precios_1816.py --balance`.

## Cómo decidir qué correr

Dos modos de uso, elegí según el pedido:

- **CLI** — para pulls simples y exports directos. Rápido.
- **Módulo** (`from precios_1816 import Cliente1816`) — para gráficos, comparaciones,
  transformaciones o cualquier cosa multi-paso. Más flexible.

### CLI — referencia rápida

```powershell
# Precios (foto de un día). --fecha para un día pasado puntual.
py precios_1816.py --tickers AL30,GD30 --campos precioClean,tna,paridad
py precios_1816.py --tickers AL30 --campos precioClean,tna --fecha 2026-07-14 --moneda ccl

# Serie histórica. El rango >1 año se parte solo en tramos.
py precios_1816.py --tickers AL30,GD30 --campos precioClean,tna --serie --desde 2024-01-01 --hasta 2026-07-16 -o serie.xlsx

# Balance de créditos
py precios_1816.py --balance
```

Flags: `--tickers` | `--tickers-file` | `--balance` (excluyentes) · `--campos`
(default `precioClean,precioDirty,tna,paridad`) · `--serie` + `--desde`/`--hasta` ·
`--fuente {byma,mae,homo-1816}` · `--moneda {ars,ccl,mep}` · `--plazo N` · `--fecha` ·
`--convencion` · `-o archivo.{csv,xlsx,json}` (sin `-o` imprime tabla).

### Módulo — patrón base

```python
import sys; sys.path.insert(0, r"C:\Users\Usuario\Downloads\API 1816")
from precios_1816 import Cliente1816
c = Cliente1816()
precios = c.precios(["AL30","GD30"], ["precioClean","tna"], moneda="ccl")   # foto
serie   = c.series(["AL30"], ["precioClean","tna"],
                   fecha_inicial="2024-01-01", fecha_final="2026-07-16")     # histórico (auto-chunk)
insts   = c.instrumentos(texto="boncer")            # buscar instrumentos
print(c.ultima_meta)                                # fecha/fuente/moneda/plazo usados
```

`precios()`/`series()` devuelven listas de dicts (formato "tidy", una fila por ticker o por
ticker+fecha). Los campos que no vinieron quedan en `None`; un ticker sin datos trae
`_error: "sin datos"`.

## Convenciones al presentar datos

- **Tasas y paridad vienen en fracción decimal** (0,0896 = 8,96%; paridad 0,85 = 85%).
  Al armar tablas/gráficos para el usuario, **convertí a porcentaje** salvo que pida lo crudo.
- **Series = formato tidy** (una fila por ticker+fecha), ideal para Excel y para graficar.
- **Entregables:** tabla en el chat para consultas rápidas; `-o .xlsx/.csv/.json` cuando
  quiera archivo; **gráfico** cuando pida evolución/comparar (ver abajo).
- Mostrá siempre la metadata relevante (fecha/rango, fuente, moneda) para que el dato sea
  interpretable.

## Gráficos

Para "graficá / mostrá la evolución / compará", traé la serie con el módulo y rasterizá con
matplotlib (`matplotlib.use("Agg")`), guardá un PNG y mostráselo al usuario. Convertí
fracciones a % en el eje. Para comparar instrumentos de escalas distintas, considerá
base-100 o eje secundario. Mantené títulos y ejes claros y en español.

## Lo que la API NO ofrece (aclarar si lo piden)

- **Caja de puntas / order book (bid-offer, profundidad L2):** no disponible. Solo precios
  operados e indicadores.
- **Intradiario tick a tick / streaming en tiempo real:** la data es de cierre/diaria (con
  marca de última operación).
- **Acciones, ADRs, opciones, futuros, FX spot:** fuera de alcance. Solo renta fija ARG.

Si el usuario necesita algo de esto, decilo con franqueza: haría falta otra fuente distinta
a 1816.

## Límites y costos (para dimensionar pedidos grandes)

- **Costo precios** = nº tickers × nº campos. **Costo series** = nº tickers × nº campos ×
  nº días con dato. Listar/buscar instrumentos es barato (~1 por consulta). Balance = gratis.
- **Plan del usuario:** 100.000 créditos/día, 3.100.000/mes (reset 00:00 hora ARG).
- **Rate limit: 1 request/segundo.** El throttle del script solo protege **dentro de una
  misma corrida del proceso**. Si tenés que hacer muchas llamadas, **hacelas todas en un
  único script Python** (no invocaciones separadas de la CLI seguidas) y, si igual aparece
  HTTP 429, subí `precios_1816.MIN_SEGUNDOS_ENTRE_REQUESTS` a ~2.5-3.0 antes de crear el cliente.
- Otros topes: 50 tickers por consulta (el script batchea solo); 1 año por serie (se
  chunkea solo). Historial disponible ≈ desde el inicio de cotización del instrumento
  (~2020 en adelante), ~245 observaciones por año hábil.

## Detalle completo

Para la lista exhaustiva de campos (precios vs series), las 26 curvas del universo, los
endpoints crudos y las formas de respuesta, leé `references/api-1816.md`.

## Ejemplos (pedido → acción)

- "Precio y TNA de AL30 y GD30 hoy" → CLI `--tickers AL30,GD30 --campos precioClean,tna`.
- "PrecioClean de GD35 del 10 de julio" → `--tickers GD35 --campos precioClean --fecha 2026-07-10`.
- "Serie de paridad de AL41, últimos 90 días, en Excel" → `--tickers AL41 --campos paridad --serie --desde <hoy-90> --hasta <hoy> -o paridad_AL41.xlsx` (convertí a % al mostrar).
- "Graficá la TNA de AL30, GD30 y AE38 en 2026" → módulo: `series(...)` 2026-01-01..hoy, matplotlib, PNG.
- "Qué bonos CER hay y cuál tiene mayor duration" → `instrumentos(texto="cer")` o por curva; luego `precios(..., ["duration"])` y ordenás.
- "AL30 en CCL vs MEP" → dos `precios(..., moneda="ccl")` y `moneda="mep")`, comparás.
