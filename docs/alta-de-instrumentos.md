# Alta de un instrumento y relleno de su historia

Procedimiento completo para sumar un bono al monitor, desde que el detector lo propone hasta que
sus columnas DAY/WTD/MTD/YTD muestran números en vez de guiones.

Extraído de las corridas reales; las advertencias en mayúsculas son errores que ya se cometieron.

---

## 1 · De dónde salen los candidatos

    gh workflow run "Revisar Universo de Instrumentos" --repo Ignacio-Talento/Monitor-SoberanosARG \
      --ref main -f solo_bajas=false

El repaso lista tres cosas: bajas (vencidos que siguen en el archivo), altas soberanas que 1816
lista y el monitor no sigue, y candidatos provinciales/corporativos ordenados por volumen. Respeta
`detector_ignorar.json` e imprime cuántos tickers saltea y por qué.

**El alta la decide una persona.** El repaso propone; no da de alta solo. Si un candidato se
descarta, va a `detector_ignorar.json` **con el motivo escrito**, porque si no vuelve a aparecer
todas las semanas y nadie recuerda por qué se había dicho que no.

Motivos habituales para descartar: es otra especie de un bono que ya se sigue —contaría el mismo
crédito dos veces en la mediana de su familia—, vence en pocos días, o el modelo del monitor no lo
representa.

Si el repaso falla una curva con `429`, **el resultado no cubre esa curva**: puede haber un alta sin
detectar. Repetirlo más tarde, no insistir en el momento (la key de 1816 es compartida y el
limitador es global).

---

## 2 · Qué hay que averiguar antes de cargarlo

De `/v1/mercado/instrumentos` de 1816, buscando por el prefijo del ticker:

| Dato | De dónde sale |
|---|---|
| Ticker de 1816 | Lleva sufijo de especie, casi siempre `O` |
| Vencimiento | `fechaVencimiento` |
| Emisor | `emisorNombre` |
| **Ley** | Del ISIN: `AR*` → `local`, `US*` → `ny` |
| **Divisa** | **NO sale de 1816. Ver abajo.** |

### La Divisa no se deduce

`Ley` y `Divisa` son atributos **independientes**. La Divisa es la moneda en la que el bono
**PAGA**, y hay ONs de ley local que pagan en cable.

Dos atajos que dan mal, los dos probados:

- **Por ISIN.** Determina la ley y nada más. Inferir de ahí la moneda falla en todas las ONs
  `local` + `CCL`.
- **Por dónde opera.** Mide liquidez del segmento, no lugar de pago. Hay bonos que pagan en cable
  y operan casi siempre en MEP: uno tuvo precio MEP en 22 de 22 ruedas y precio CCL en 2, y aun así
  su Divisa correcta es CCL.

**Si la punta no está clara, se pregunta.** No se infiere de los precios.

Consecuencia de tenerlo bien: un bono que paga en cable y no opera ahí **cae en `sinDato` del
informe casi todos los días**. Es esperado, no un error, y el informe tiene que decir «sin precio en
la punta en la que se valúan» y no «no registraron operaciones».

---

## 3 · Cargarlo en Instrumentos.xlsx

Cada familia tiene su hoja. Las ONs en USD van en `ONs`, con cinco columnas:
`Ticker · Nombre · Fecha Vencimiento · Ley · Divisa`.

El **ticker del monitor termina en `D`** donde 1816 usa `O`: `TTC8O` → `TTC8D`.
`actualizar_historicos.py` hace la traducción en `resolver_1816()`.

El **Nombre** sigue el patrón `Emisor AAAA`. Si ese nombre ya existe —dos bonos del mismo emisor
que vencen el mismo año— hay que desambiguar; el mes no siempre alcanza (puede haber dos del mismo
mes), y ahí sirve la serie que usa 1816 en su denominación: `Tecpetrol C8 2027`.

### NUNCA guardar el archivo con openpyxl

El libro tiene **fórmulas compartidas con valores cacheados** en varias hojas. openpyxl descarta el
`<v>` al guardar, lo que vacía columnas calculadas sin avisar. Hay que hacer cirugía sobre el XML:

1. Abrir el `.xlsx` como zip y localizar la hoja: `xl/workbook.xml` da el `r:id` y
   `xl/_rels/workbook.xml.rels` lo resuelve a `xl/worksheets/sheetN.xml`.
2. Agregar los `<row>` antes de `</sheetData>`, copiando los atributos de estilo de la última fila
   existente (en `ONs`: `s="1"` para texto, `s="3"` para la fecha).
3. Usar `t="inlineStr"` para el texto. Evita tener que tocar `sharedStrings.xml` y sus contadores.
4. Las fechas van como serial: `(fecha - date(1899,12,30)).days`.
5. **Actualizar `<dimension ref="A1:E___"/>`.** Sin esto openpyxl ignora las filas nuevas **en
   silencio**: el archivo abre bien, se ve bien en Excel, y el monitor no las lee.
6. Reescribir el zip copiando todas las demás entradas **byte a byte**.

Verificar después: releer con openpyxl y confirmar que el conteo subió, y que alguna fórmula de
otra hoja conserva su valor cacheado (`data_only=True` no debe devolver `None`).

### Ratings

Si el emisor es nuevo, hay que cargarlo a mano en `ons.html`: 1816 no trae calificaciones. Antes de
darlo por nuevo, chequear el diccionario de ese archivo — un emisor puede ya estar ahí por otra ON.

---

## 4 · Relleno de la historia

Sin historia, las columnas DAY/WTD/MTD/YTD muestran `—` hasta que se acumulen ruedas.

### Primero: la columna tiene que existir

El relleno **no crea columnas**. Las crea la corrida diaria de precios, que agrega al header
cualquier ticker que falte. Hasta que esa corrida no pase, el relleno saltea los nuevos con
`AVISO: <ticker> no está en el histórico o no mapea a 1816` — que no es un error, es esta
situación.

Así que el orden es: **alta → corrida diaria → relleno**. Nunca al revés.

> **NO LANZAR `actualizar_historicos` ANTES DE LAS 17:00 ART.** El script sale si ya existe la fila
> del día, así que un precio intradiario quedaría como el **cierre definitivo** de esa rueda y la
> corrida post-cierre lo saltearía con «Ya existe fila». Si se lanzó por error, cancelar el run
> antes de que llegue al paso de commit y verificar que la última fila del archivo sigue siendo la
> de ayer.

Los crones de este repo **llegan tarde o no disparan**. Antes de asumir que la corrida pasó:

    gh run list --workflow="Actualizar Históricos de Precios" -R Ignacio-Talento/Monitor-SoberanosARG

`createdAt` viene en UTC; ART es UTC−3.

### Después: el relleno, siempre en seco primero

    gh workflow run "Rellenar Historia de Nuevos" --repo Ignacio-Talento/Monitor-SoberanosARG \
      --ref main -f tickers="AAA1D BBB2D" -f dry_run=true

El dry-run dice a quiénes rellenaría, **con qué moneda mapea cada uno** y cuántas ruedas tiene hoy.
Chequear esa columna: es donde se ve si la Divisa quedó bien cargada.

**No hace falta separar por moneda.** `rellenar_historia.py` agrupa los tickers por moneda y le pide
a 1816 un lote por cada una, así que van todos en la misma corrida aunque unos sean MEP y otros CCL.

**El input `reexpresar_ccl` es para otra cosa**: pisa la historia ya cargada de un ticker que
*cambió* de moneda. No se usa para dar de alta.

Si el dry-run se ve bien, repetir con `-f dry_run=false`. Cuesta un crédito por ticker y por día:
unos pocos cientos sobre los 100.000 diarios. El script commitea y pushea solo.

### Verificar

`git pull` y confirmar que cada ticker tiene precios en **ruedas anteriores** y no sólo en la
última. Un ticker con una sola rueda es un relleno que no entró.

---

## 5 · Qué mirar al día siguiente

- Que los nuevos traigan precio en la corrida diaria y no aparezcan en `sinDato` (salvo que paguen
  en una punta ilíquida, ver §2).
- Que la familia a la que entraron no haya cambiado de mediana por un outlier de un ticker que
  todavía tiene poca historia.
