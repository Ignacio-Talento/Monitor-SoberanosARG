# Contraste del motor del monitor contra los indicadores de 1816

Cómo verificar que las tasas y durations que calcula el monitor coinciden con las que publica 1816
para los mismos instrumentos. Sirve después de tocar `monitor-core.js`, de sumar una familia, o
cuando un número no cierra y hay que decidir si el problema es del monitor o del dato.

Extraído de las corridas reales; las advertencias en mayúsculas son errores que ya se cometieron.

---

## Cuándo correrlo

**Con la rueda cerrada.** Con el mercado abierto, buena parte del universo todavía no operó y 1816
devuelve vacío: se termina comparando la mitad de los instrumentos y creyendo que se cubrió todo.
Un instrumento sin dato a las 13:00 no es un hallazgo.

Además hay diferencias que **sólo existen durante la rueda** y se cierran solas al día siguiente:
ver la trampa del A3500 más abajo.

---

## 1 · Traer la foto de 1816

La API key vive **sólo como Secret del repo**, así que la foto se pide por Actions:

    gh workflow run "Completar Ruedas Incompletas" --repo Ignacio-Talento/Monitor-SoberanosARG \
      --ref main -f dry_run=true -f probe="<tickers separados por coma>" \
      -f campos="precioDirty,tea,durationMod" -f moneda="<ars|mep|ccl>" -f modo="precios"

Esperar, tomar el id y leer la tabla del log:

    gh run view <id> --repo Ignacio-Talento/Monitor-SoberanosARG --log 2>&1 \
      | sed -n 's/^completar\tSondear [^\t]*\t[0-9T:.Z-]* //p' | grep -E "^\w+ +\||ticker \|"

**Una corrida por moneda**, porque 1816 la toma como parámetro de la request y no del ticker:
pesos para la curva local, MEP para Bonares y BOPREALes, CCL para Globales y subsoberanos. Las
listas salen de `Instrumentos.xlsx`; no conviene fijarlas acá porque el universo cambia.

El costo total ronda los 300 créditos sobre 100.000 diarios. Si aparece un `429` el script
reintenta solo.

---

## 2 · Calcular el lado del monitor

> **NO REIMPLEMENTAR LA FÓRMULA EN PYTHON.** Ya se intentó y llevó a un diagnóstico equivocado que
> costó una tarde: se reportaron cinco Boncer «mal calculados por hasta 32 pp» que en realidad
> estaban bien, porque la réplica tomaba una rama del código que el monitor nunca toma para esos
> bonos. La única fuente de verdad es el motor real.

Correrlo en el browser:

1. `preview_start` con `{name: "monitor"}` — la configuración está en `.claude/launch.json` y sirve
   el repo en el puerto 5300.
2. Navegar a `http://localhost:5300/bonos.html` y esperar unos segundos a que carguen
   `Instrumentos.xlsx`, el CER y la TAMAR.
3. **Los 404 de `/api/*` en consola son normales**: el server estático no tiene esos endpoints, que
   sólo existen como Functions en producción.
4. Cebar las fechas de emisión de los CER viejos antes de calcular, porque no vienen en la carga
   inicial:

       for (const i of instrumentos.filter(x => x.grupo === 'cer')) await fetchCerFecha(i.emision);

5. Para cada ticker: asignar `precios[ticker]` con el `precioDirty` de 1816 y llamar
   `calcMetricas(inst)`. Comparar `m.tea` (viene en %) contra la `tea` de 1816 (viene en
   **fracción**: multiplicar por 100) y `m.md` contra `durationMod`.

---

## 3 · Qué NO se compara, y por qué

Saltear esto no es hacer trampa: son instrumentos donde la comparación **no mide nada**.

**Los que van a los indicadores de 1816 por diseño.** `usaIndicadores1816(inst)` desvía a 1816 los
grupos paramétricos que tienen flujos cargados en la hoja `Flujos` —los Boncer con renta y
amortización, entre otros—, porque la fórmula de zero coupon les daría cualquier cosa. Para esos, el
monitor y 1816 son iguales **por construcción**. Está comentado en `monitor-core.js`.

Ojo con el corolario: en el server local `indicadores1816` queda vacío por los 404, así que esos
instrumentos devuelven `null`. **Son los que hay que saltear, no un error a reportar.**

**Los duales.** 1816 publica sólo la pata que está in the money y el monitor lleva dos filas por
ticker. Emparejarlos es un trabajo aparte; si no se hace, decir que quedaron afuera.

**Los que vencen ese día.** Si vienen sin precio es lo esperado. Mencionarlo, pero **no editar
`Instrumentos.xlsx`** en el medio del contraste: la baja es otro procedimiento.

---

## 4 · Diferencias conocidas

Antes de reportar un hallazgo, descartar estas dos.

### TAMAR: 0,05 a 0,13 pp, permanente

No se va con ninguna ventana de proyección porque 1816 **no usa un promedio móvil** como el del
monitor. Está documentado en el comentario de la constante `TAMAR_DIAS_PROY` en `monitor-core.js`.
Confirmar que sigue en ese orden de magnitud y seguir.

### Dólar linked: el A3500 con el que se valúa

Si aparece una diferencia grande y pareja en toda la familia, **dividirla por la duration de cada
bono antes de sacar conclusiones**. Si ese cociente da constante, no es un problema de convención de
tasa: es una diferencia de **valor**, casi siempre el tipo de cambio.

Pasó exactamente eso: los siete dólar linked daban hasta 1,4 pp de diferencia, el error sobre la
duration daba ≈0,105 en los siete, y despejando el tipo de cambio implícito en la TEA de 1816 salía
el spot mayorista **del día**, mientras el monitor usaba `bcraData.usdHoy`, que es el **último A3500
publicado** —el del día hábil anterior—. Recalculando con el spot correcto las siete diferencias
daban 0,0000.

O sea: durante la rueda esta familia diverge y al día siguiente converge sola. **Es motivo
suficiente para correr el contraste con la rueda cerrada.**

El método general vale para cualquier familia: *error / duration constante ⇒ mirar el valor, no la
fórmula.*

---

## 5 · Cómo se informa

Una tabla por familia con tres columnas: cuántos se compararon, la peor diferencia de TEA en puntos
porcentuales y la peor de M.Dur.

**Ser preciso con el alcance.** Decir cuántos instrumentos se compararon *de verdad* y cuáles
quedaron afuera y por qué. No afirmar que «todo coincide» si sólo se midió una parte — que es
justamente lo fácil de hacer acá, porque los que quedan afuera se caen solos del cuadro.

Como referencia de qué es normal, una corrida sana con la rueda cerrada da diferencias de TEA por
debajo de 0,05 pp en casi todas las familias y durations exactas a varios decimales. Un salto de
orden de magnitud sobre eso es lo que amerita investigar.
