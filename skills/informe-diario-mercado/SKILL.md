---
name: informe-diario-mercado
description: Informe diario de renta fija argentina por mail a las 17:30, media hora después del cierre, con cierre semanal los viernes y mensual la última rueda hábil
---

Armar y ENVIAR POR MAIL el informe diario de renta fija argentina, con los datos del monitor.

El mail sale a las 17:30, media hora después del cierre del mercado.

El usuario (Ignacio) autorizó explícitamente el envío directo a su casilla: ignaciotalento@gmail.com. No hace falta volver a pedirle permiso para mandarlo. Trabaja en research de renta fija, así que el registro es el de un informe interno de mesa: técnico, sin explicar qué es una TIR.

═══════════════════════════════════════════════════════════════════
PASO 1 · CONSEGUIR LOS DATOS
═══════════════════════════════════════════════════════════════════

Repo: C:\Users\Usuario\Downloads\Monitor-SoberanosARG (remoto `fork` = Ignacio-Talento/Monitor-SoberanosARG).
En Windows usar `py`, no `python`, y setear PYTHONIOENCODING=utf-8.

    git -C "C:\Users\Usuario\Downloads\Monitor-SoberanosARG" fetch fork main

VOS DISPARÁS EL WORKFLOW, no lo esperás. Los datos los produce armar_informe.py, que corre en
GitHub Actions porque la API key de 1816 vive sólo como Secret del repo y no está en esta máquina.
Hay un cron a las 18:10 pero es RESPALDO: llega después del mail, y además GitHub demora los
schedules entre 20 y 50 minutos. Arrancalo vos, que por workflow_dispatch sale en segundos:

    gh workflow run informe_diario.yml --repo Ignacio-Talento/Monitor-SoberanosARG

Esperá a que termine —tarda entre 35 y 60 segundos— consultando el estado en un bucle, sin dormir
de más:

    until [ "$(gh run list --workflow=informe_diario.yml --limit 1       --repo Ignacio-Talento/Monitor-SoberanosARG --json status -q '.[0].status')" = "completed" ];       do sleep 10; done

Después traé el resultado (la fecha es la de hoy en calendario argentino):

    git -C "<repo>" fetch fork main
    git -C "<repo>" show fork/main:informes/datos_AAAA-MM-DD.json

SI EL WORKFLOW FALLA O EL ARCHIVO NO EXISTE: no inventes ni uses el de ayer como si fuera de hoy.
Mirá `gh run view <id> --log-failed` y mandá un mail CORTO diciendo que no hay informe y por qué
(1816 caído, feriado, lo que sea). Un mail que avisa del hueco vale; uno que rellena el hueco con
datos viejos, no.

SI EL WORKFLOW ANDA PERO FALTAN MUCHOS INSTRUMENTOS: compará `sinDato` contra lo habitual. Con el
mercado recién cerrado es normal que falten ONs ilíquidas —30 o 40 sobre 186—, pero si faltan
soberanos líquidos como AL30, GD30 o las LECAPs cortas, 1816 todavía no consolidó el cierre.
En ese caso esperá cinco minutos, volvé a disparar el workflow, y si sigue igual decilo en el mail.

QUÉ TRAE EL JSON:
  fecha, ruedaAnterior, tipos (["diario"] y, si corresponde, "semanal" y/o "mensual"),
  universo, sinDato (tickers sin precio de hoy), resumen (por familia) e instrumentos (uno por
  instrumento, con ticker, familia, precio, tea, durationMod, paridad, varPrecio %, varTasa pp,
  varParidad pp).

TRES COSAS QUE HAY QUE SABER PARA NO DECIR MACANAS:
  · `tea` y `paridad` YA vienen en porcentaje (26.73 = 26,73%) y `durationMod` en AÑOS. El script
    hace la conversión; no la repitas.
  · En las familias en pesos, `tea` es TEA nominal; en CER es TIR REAL; en hard dollar es la TIR en
    dólares. NO las compares entre sí sin convertir.
  · Cada familia trae `convencionDudosa`: tickers cuya tasa está en otra escala que sus pares
    (típicamente duales CER/TAMAR que 1816 devuelve unos en TEA nominal y otros en TIR real).
    NO los promedies con el resto ni saques conclusiones de ellos. Si son relevantes, mencionalos
    como dato a verificar, no como hallazgo.

Datos complementarios que SÍ podés conseguir vos:
  · Futuros de dólar: API pública de A3, sin credenciales.
    https://apicem.matbarofex.com.ar/api/v2/closing-prices?product=DLR&type=FUT&from=...&to=...
    (formato de fecha AAAA-MM-DD; el campo `settlement` es el ajuste, `volume` el volumen).
    Ojo: el endpoint tick-prices se cae seguido con 424 "Execution Timeout Expired"; closing-prices
    es el confiable, pero publica el ajuste recién de madrugada, así que a las 20:00 el último
    disponible es el de AYER.
  · Histórico del spread de sintéticos: el archivo spreads_sinteticos.json del repo, que llega
    hasta la rueda anterior (su job corre a las 6 de la mañana).

NO intentes leer las solapas del monitor en el navegador: están detrás de Cloudflare Access y sin
sesión devuelven 302. Todo el análisis sale de los datos de arriba.

═══════════════════════════════════════════════════════════════════
PASO 2 · EL INFORME DIARIO
═══════════════════════════════════════════════════════════════════

SECCIÓN A — VARIACIONES POR FAMILIA. Es lo que el usuario pidió explícitamente, va primero y en
tabla. Una fila por familia: LECAPs y tasa fija, CER, TAMAR, Duales, Dólar linked, Bonares,
Globales, BOPREALes, ONs ley local, ONs ley NY, Subsoberanos.

Todo se agrega por MEDIANA de los instrumentos de la familia, nunca por promedio ni ponderando por
volumen. Decilo en la nota al pie de la tabla, porque cambia cómo se lee el número: la mediana es el
movimiento del instrumento típico, no el de la familia como cartera.

Incluí SIEMPRE una columna de MONEDA con la punta en la que el monitor valúa cada familia, que
viene en `resumen[familia].monedas`. Es la misma que muestra el chip del monitor: pesos la curva
local, MEP bonares y BOPREALes, CCL globales, subsoberanos y las ONs de ley NY. Las ONs de ley
local traen las dos —cada emisor paga donde paga, al 03/09/2026 son 60 en MEP y 7 en CCL— y ahí
hay que mostrar el reparto, no elegir la dominante: elegirla escondería justo a las que son la
excepción.

LA MONEDA NO SE DEDUCE, SE LEE. Sale de la columna Divisa de la hoja ONs, que es la moneda en la
que el bono PAGA y es independiente de la Ley. Dos atajos que dan mal y que ya se probaron:
el ISIN (`AR*` / `US*`) determina la LEY y nada más, y el volumen por punta mide liquidez del
segmento, no lugar de pago —VSCPD paga en cable y sin embargo opera casi siempre en MEP—. Si
aparece una ON cuya punta no está clara, se pregunta; no se infiere de los precios.

Y tenelo presente al comparar familias entre sí: dos TIR en monedas distintas no forman un spread
de crédito, porque parte de la diferencia es el canje. Si comparás ONs locales contra ley NY,
decilo.

Para cada una: variación de precio (%, mediana) Y el movimiento de su métrica natural en pp
(TEA para pesos, TIR real para CER, TIR para hard dollar), más el nivel de tasa vigente. Las dos
varas juntas, que fue lo que pidió: el % de precio es lo que ve el tenedor y los pp de tasa son lo
que se negocia. Usá SIEMPRE la mediana, nunca el promedio: en cada familia hay algún ilíquido cuyo
precio quedó de hace tres ruedas y salta 4% cuando por fin opera.

Un chequeo de sanidad antes de mandar: dentro de una familia, si bajó la tasa el precio tiene que
haber subido. Si ves los dos signos iguales, algo está mal — decilo en vez de publicarlo.

ESA REGLA VALE PARA LA COLUMNA DEL DÍA Y NO PARA LAS DE PERÍODO. En una rueda el devengamiento es
despreciable —0,07% para una LECAP al 28% TEA— así que cualquier movimiento de precio es de tasa. En
un mes no: esa misma LECAP gana 2,1% de precio sin que su tasa se mueva un punto básico, un CER suma
además la inflación del período y un dólar linked la devaluación. Que en el mes suban precio Y tasa
a la vez es lo NORMAL, no un error, y el informe tiene que decirlo al pie de la tabla en vez de
dejar que el lector tropiece.

Si querés verificarlo, la cuenta es:

    variación de precio  ≈  devengamiento + indexación − duration × cambio de tasa

Medido sobre el cierre de agosto de 2026 cierra con diferencia de 0,2 pp o menos en tasa fija
(+1,78% real contra +1,90% esperado), CER (+0,31% contra +0,49%) y dólar linked (+0,70% contra
+0,71%).

Y OJO CON LOS TAMAR Y LOS DUALES, que no cierran con esa cuenta ni tienen por qué. Son a tasa
flotante: su valor final es 100·(1+TEM proyectada)^meses, así que cuando sube la TAMAR proyectada
sube el pago Y sube la tasa de descuento, y los dos efectos se cancelan en buena parte. El neto es
que el precio SUBE cuando sube la tasa —al revés que en un bono a tasa fija—, en proporción al
tramo ya devengado. Aplicarles −duration × Δtasa da cualquier cosa: en agosto de 2026 predecía
−0,36% para los TAMAR contra +2,22% real. No los uses para diagnosticar un error de dato.

En los duales hay además una trampa propia: 1816 publica sólo la pata que está in the money, y esa
pata puede CAMBIAR entre las dos puntas del período. En agosto de 2026 tres duales mostraron +34,5
pp de variación mensual de tasa por ese motivo y ninguno se había movido. Antes de comentar la
variación de tasa de los duales en un cierre, mirá si la escala de la tasa es la misma en las dos
puntas; si no, decí que el número no es comparable y no lo interpretes.

Mencioná cuántos instrumentos quedaron sin precio de hoy (`sinDato`). Cuando son muchas ONs es lo
normal: no operan todos los días. Si faltan soberanos líquidos, eso sí es una señal de que algo
falló y hay que decirlo.

NO ESCRIBAS QUE LOS DE `sinDato` "NO OPERARON". Sin precio y sin operaciones no son lo mismo, y
la diferencia tiene una causa concreta y recurrente. Cada ON se valúa en la punta en la que PAGA
—la columna Divisa de la hoja ONs, que es independiente de la Ley—, y hay ONs de ley local que
pagan en CABLE aunque casi toda su liquidez esté en MEP. Esas caen en `sinDato` casi todos los
días: se les pide la punta correcta y esa punta no imprime. VSCPD, por ejemplo, quedó sin precio
20 de las 22 ruedas de agosto de 2026 mientras operaba en MEP las 22.

La fórmula correcta es «sin precio del día EN LA PUNTA EN LA QUE SE VALÚAN». Decir que no operaron
es afirmar algo falso sobre un bono que sí operó. Y no "corrijas" el problema pidiéndole el precio
en la otra punta: la moneda de valuación es un dato del prospecto, no una preferencia.

SECCIÓN B — DINERO Y MACRO. El JSON trae un bloque `macro` con estas series, ya con su variación
contra el dato previo y una ventana de 15 ruedas para ver la tendencia:

  · `riesgoPais` — EMBI+ Argentina.
  · `tamarTEA` y `tamarTNA` — la TAMAR de bancos privados (efectiva y nominal).
  · `badlarTEA`, `plazoFijo30` — el resto del corredor de tasas mayoristas y minoristas.
  · `macro.caucion` — la tasa de fondeo a 1 día, que es la misma serie 150 que levanta el monitor.
    Trae `esCaucionBursatil: false` a propósito: ver la advertencia de abajo.
  · `pasesTerceros` y `volPases` — la misma serie 150, con su volumen.
  · `baibar` — préstamos entre bancos privados: el call interbancario propiamente dicho.
  · `interbancario` — préstamos entre entidades financieras locales.
  · `comprasMLC` — compra de divisas del BCRA medida por su efecto en reservas, en millones de USD.
  · `efectoMonetario` — el mismo hecho visto en pesos emitidos.
  · `reservas` — reservas internacionales.

EN LOS CIERRES, ESTAS SERIES TAMBIÉN TRAEN SU PERÍODO, en `serie.semanal` / `serie.mensual` y en
`macro.riesgoPais.semanal`. Pero ojo con leerlas todas igual, porque hay dos clases y el campo
`clase` lo dice:

  · `stock` —las tasas, las reservas, el riesgo país—: trae `variacion`, la diferencia contra el
    cierre anterior. Es lo que uno espera.
  · `flujo` —compra de divisas, efecto monetario, volumen de pases—: trae `acumulado` y `ruedas`,
    NO una variación. El Central no compra «más que el viernes pasado», compra tanto por día, así
    que del período interesa la suma.

DECÍ SIEMPRE SOBRE CUÁNTAS RUEDAS SE ACUMULÓ cuando informes un flujo. La compra de divisas se
publica con cuatro días de rezago: el 28/08/2026 el acumulado «de la semana» tenía UNA sola rueda,
y presentar esos 11,5 millones como la semana entera habría sido falso.

Y mirá `exacta`: si es false, la fecha de referencia no tenía dato y se usó la anterior más
cercana, que viene en `fecha`. Con el rezago del BCRA pasa seguido que «el viernes pasado» sea en
realidad el miércoles. Si la diferencia es de más de un par de días, aclaralo.

Presentalo en una tabla corta y OBLIGATORIAMENTE con una columna «Al día» que lleve la fecha de
cada serie. No es un detalle: las series NO son todas del mismo día y un encabezado único mentiría.
Las tasas del BCRA salen con dos días hábiles de rezago y las de reservas y compras de divisas con
tres o cuatro, así que en el informe del viernes lo más nuevo del BCRA suele ser del miércoles.
Cada serie trae su `fecha` y su `rezagoDias`.

Y decilo también en el cuerpo, no sólo en la tabla: cuando compares una tasa del BCRA contra el
movimiento de los bonos de hoy, aclará que la del BCRA es de dos días antes. Si alguna serie viene
con un rezago mayor al habitual —`rezagoDias` de 5 o más en las tasas—, marcala: puede ser un
feriado o puede ser que el BCRA dejó de publicarla.

Las variaciones del bloque macro (`variacion`) son contra el dato previo DE ESA SERIE, que no es
necesariamente la rueda anterior. Con un fin de semana o un feriado en el medio, el «previo» puede
ser de tres o cuatro días atrás; el campo `previo.fecha` te dice cuál es.

DOS ADVERTENCIAS PARA NO CONFUNDIR AL LECTOR:

  · LA TASA DE FONDEO A 1 DÍA NO ES LA CAUCIÓN BURSÁTIL. Es la serie 150 del BCRA, pases entre
    terceros, que es el proxy público más cercano: la caución de BYMA y de MAE está detrás de
    credenciales. Como referencia del sesgo, el futuro de caución de A3 marcaba 23,23% el
    27/08/2026 contra 21,54% de los pases, unos 170 puntos básicos. Podés llamarla «tasa de fondeo
    a 1 día», «repo a 1 día entre bancos» —que es como la nombra 1816 en su semanal y como se
    llama la tarjeta de la solapa Macro— o nombrar la serie; no la llames «caución» a secas.
    Es la tasa que primero se mueve cuando el BCRA inyecta o absorbe, así que cuando haya una
    operación de mercado abierto grande, contala mirando esta serie y no la BADLAR.
  · Las otras dos tasas entre entidades —`baibar` (bancos privados) e `interbancario` (entidades
    financieras locales)— son mercados distintos otra vez. No las promedies ni las uses como
    equivalentes.
  · Cruzá el bloque macro con las tasas de mercado de la sección A. Cuando el BCRA muestra la BADLAR
    y el plazo fijo cediendo y las LECAPs cortas comprimen el mismo día, es la misma historia
    contada dos veces y conviene decirlo así, no como dos hallazgos separados. Si en cambio van en
    direcciones opuestas, eso sí es una observación.

SECCIÓN C — LO RELEVANTE DEL DÍA. Tres a seis observaciones, no más, y sólo si el dato las
sostiene. Un informe que dice "sin novedades" cuando no las hubo es mejor que uno que infla. Es
todo derivable del JSON:

  · Curva de pesos: ¿se empinó o aplanó? Mirá la TEA contra durationMod y compará el movimiento del
    tramo corto contra el largo. Un día en que las cortas comprimen 2 pp y las largas no se mueven
    es una noticia, y es distinto de "bajaron las tasas".
  · CER contra tasa fija: LA INFLACIÓN BREAKEVEN VA SIEMPRE, no sólo si se movió fuerte. Es una
    de las dos lecturas que más se usan de la curva en pesos. Método: para cada bono CER, interpolá
    la curva de tasa fija A SU MISMA DURATION y calculá (1 + TEA fija) / (1 + TIR real) − 1.
    Interpolar, no emparejar con la LECAP más cercana: emparejando da hasta 60 bps de diferencia
    contra la línea que dibuja el gráfico, y la tabla del informe termina contradiciendo a su propio
    gráfico. Mostralo como tabla de cinco o seis puntos, del corto al largo.
    Sacá de la cuenta los CER de menos de dos meses: con el coeficiente casi todo devengado la TIR
    real es negativa y el breakeven se dispara a 30% y pico sin informar nada. Decí que los sacaste.
  · MARGEN SOBRE TAMAR DE LOS DUALES: VA EN LOS TRES INFORMES. Sale del JSON, en
    `d["mercado"]["bloques"]["margenTamar"]`, con una entrada por dual (TXMJ8, TXMD8, TXMJ9, TXMD9,
    TXMJ0) y su variación del día y del período. NO lo recalcules ni se lo pidas a 1816: lo bajó
    `series_mercado.py` en el mismo job y el PDF ya arma la tabla; tu trabajo es la prosa.

    QUÉ ES. Un dual CER/TAMAR paga al vencimiento lo que haya rendido más entre el CER y la TAMAR
    capitalizada, así que tiene dos valuaciones. Éste es SIEMPRE el margen de la pata TAMAR —el
    ticker `@TAMAR` de 1816—, se esté pagando esa pata o no. Más alto es bono más barato.

    PARA QUÉ SIRVE, que es lo que hay que contar: es el precio al que el Tesoro consigue plazo.
    Mientras los duales largos pidan cerca de 10 puntos sobre TAMAR, es difícil que coloque títulos
    largos en el primario, y eso explica licitaciones que salen cortas. Si el margen comprime en
    toda la curva antes de una licitación, decilo: es la señal más directa de que va a poder estirar.

    Ojo con la CONVENCIÓN: es TNA 32/365, la misma que publica 1816 en su semanal. En 180/360 el
    mismo bono el mismo día da unos dos puntos más. Si citás un número de otra fuente, verificá en
    qué convención está antes de compararlo con éste.
  · BONARES CORTOS Y EL FORWARD DE LA ELECCIÓN: VA EN LOS TRES INFORMES. Sale de
    `d["mercado"]["bloques"]["bonares"]`: AO27, AO28 y el forward 1Y1Y implícito entre los dos, en
    TNA CONTRA CABLE. Contra cable y no contra MEP a propósito —medido contra MEP se mueve con el
    canje CCL/MEP, que cambia por razones ajenas al bono—, así que NO lo compares con las TIR en MEP
    del resto de la sección sin decir que están en puntas distintas.

    EL FORWARD ES EL NÚMERO QUE HAY QUE CONTAR. Los dos vencen el mismo mes con un año de
    diferencia (29-oct-2027 y 31-oct-2028) y el AO27 vence a días de la ELECCIÓN 2027, así que el
    forward es a qué tasa está descontando el mercado que va a rendir un bono argentino en dólares
    durante el año POSTERIOR a la elección. Es la lectura más directa del riesgo electoral que hay
    en la curva, y se mueve mucho más que las TIR de los dos bonos por separado: seguí ese
    movimiento, no el nivel.

    ES UNA APROXIMACIÓN: sale de las TIR de los dos bonos y no de una curva cero bootstrapeada, y
    ambos amortizan y pagan renta. Sirve para el nivel y sobre todo para el movimiento, no para
    discutir décimas. El 03/09/2026 esta cuenta daba 15,79% y el semanal de 1816 publicaba 15,5%.
  · Bonares contra Globales: el spread por legislación, par por par (AL29/GD29, AL30/GD30,
    AL35/GD35, AE38/GD38, AL41/GD41). USÁ SIEMPRE `instrumento.enMep`, NUNCA el campo `tea` o
    `precio` del global.

    Es la regla más importante de esta sección. Los globales se valúan al CCL y los bonares al
    MEP, así que restarles la tasa directamente mezcla dos monedas y el resultado no significa
    nada: da un número plausible, no falla, y está mal. El 28/08/2026 el par AL29/GD29 aparecía
    con el Global rindiendo 24 bps MÁS que el Bonar —una inversión que no existía— cuando en la
    misma punta el Global rinde 255 bps MENOS. El error iba de 79 a 279 puntos básicos según el
    par. La solapa Glob vs Bon resuelve lo mismo descartando todo lo que no esté en MEP.

    Con las dos patas en MEP, lo normal es que el Global rinda MENOS que su Bonar (mejor
    legislación, menos tasa) y que el canje de precio —(precio Global − precio Bonar) / precio
    Bonar, la cuenta de la solapa— sea positivo. Si te da lo contrario, casi seguro estás
    mezclando puntas: verificá antes de reportarlo como hallazgo.
  · CANJE CCL/MEP: VA EN LOS TRES INFORMES —diario, semanal y mensual—. Es cuánto más caro sale el
    dólar cable que el MEP, y es lo que hace que comparar un Global contra un Bonar exija llevarlos
    a la misma punta: sin eso, parte del spread que uno mide es canje y no crédito.

    Sale de `canje_ccl_mep.canje(hasta, referencias)`, que devuelve el nivel, la variación contra
    la rueda anterior y contra cada referencia de período, y el rango de lo que va del año para
    ubicarlo. Pasale `d["referencias"]` del JSON del informe. Un ejemplo del 02/09/2026: 4,06%,
    +0,08 pp en el día, −0,36 en la semana, +0,26 en el mes, contra un rango anual de 2,18% a
    4,45% y una mediana de 3,59%.

    ES UN SOLO NÚMERO POR RUEDA Y NO UNO POR BONO. La fuente primaria es el ÍNDICE DÓLAR BYMA,
    que se arma con una canasta de instrumentos y por eso no depende de que un bono puntual haya
    operado. El respaldo es AL30 en sus dos puntas, del archivo local, y va AL30 solo porque es el
    más operado en los dos segmentos. El módulo elige y avisa cuál usó: decilo en el informe.

    NO USES LA MEDIANA DEL CANJE IMPLÍCITO EN LOS BONOS. Está a mano —cada instrumento en CCL trae
    su `enMep`— y da parecido en el nivel, pero es otra cosa: mezcla la liquidez de cada especie,
    así que su dispersión (de 3,0% a 5,7% el 02/09/2026) se mueve según qué bono operó y se lee
    como si el canje se hubiera movido. Si querés mencionar esa dispersión, presentala como lo que
    es —cuánto cuesta hacer el canje en un bono y no en otro— y no como el nivel del canje.

    Ojo con el NOMBRE. El informe ya usa "canje de precio" para la diferencia entre un Bonar y su
    Global, que es el precio de cambiar de LEGISLACIÓN. Éste es el de MONEDA. Nombralos distinto
    —"canje CCL/MEP" y "canje de legislación"— o el lector los confunde.

    Si el módulo avisa `sslSinVerificar`, el certificado del host de BYMA no validó y el dato se
    trajo igual: merece una línea en el pie, como con el BCRA.

  · BOPREALes: el salto de TIR entre series (la 7 y la 8 rinden muy distinto) y las paridades.
  · ROTACIÓN BOPREAL → BONARES: va todos los días. Emparejá cada BOPREAL con el Bonar de duration
    más parecida y mostrá el diferencial de TIR en una tabla. Las dos familias se valúan en MEP, así
    que se restan directo. Contá también cómo cambió el diferencial en la semana, que sale de
    varTasa_semanal de las dos patas: el 28/08/2026 los BOPREALes comprimieron 0,49 pp y los Bonares
    0,06, y el par 2028 se abrió de 141 a 155 bps.
    Describí el diferencial y de dónde sale —distinto emisor, BCRA contra Tesoro, y distinta
    estructura: la serie 7 cotiza arriba de la par y el Bonar largo con descuento—. NO recomiendes
    rotar ni digas qué conviene comprar: el informe describe el mercado, no aconseja.
  · Dólar linked contra los futuros de A3: va todos los días, con dos cuentas.
    (1) SINTÉTICO. Para cada bono DL, devaluación anualizada del futuro a su misma duration
    —interpolando entre contratos— compuesta con su TIR: (1 + deval)(1 + TIR) − 1. Eso se compara
    contra la curva de tasa fija interpolada a la misma duration. La diferencia dice si la cobertura
    está cara o barata contra tasa fija. Dejá afuera los bonos de menos de un mes: el contrato con
    el que habría que compararlos vence en días y su tasa anualizada no significa nada.
    (2) DEVALUACIÓN CONTRA INFLACIÓN. La implícita de los futuros sube con el plazo y la breakeven
    de CER baja; decí dónde se cruzan y cuánta depreciación real se paga a un año.
    OJO CON LA FECHA DEL AJUSTE: A3 lo publica después del clearing, así que a las 17:30 el del día
    NO está y hay que usar el de la rueda anterior —y decirlo—. Si el informe se rehace más tarde y
    el ajuste ya salió, rehacé también la curva de futuros para que todo sea del mismo día.
  · Sintéticos: el spread de hoy contra su historia (spreads_sinteticos.json). Para juzgar si está
    caro usá SÓLO lo que va del año: el archivo arranca en 2024 con el cepo puesto y esos meses son
    otro régimen cambiario, no otro nivel de este mercado.
  · ONs: ley local contra ley NY, y si alguna se despegó de su curva. Mismo cuidado que con los
    soberanos: las de ley NY están en CCL y las locales casi todas en MEP, así que para compararlas
    hay que usar `enMep` de las NY. Sin eso, la diferencia de TIR que veas es en buena parte el
    canje y no el crédito.

REGLA GENERAL: antes de restar dos tasas o dos precios de familias distintas, fijate que estén en
la misma moneda. El campo `moneda` de cada instrumento lo dice, y los que están en CCL traen su
equivalente en `enMep`. Dentro de una misma familia no hace falta —salvo en las ONs de ley local,
que tienen las dos puntas—.

SECCIÓN D — VALOR RELATIVO. Si algo aparece —un par de bonos cuyo spread se salió de su rango, una
curva con un punto claramente fuera de línea, un sintético que paga distinto que su instrumento
directo—, describilo: qué se ve, cuánto vale en pp o bps, y qué lo puede estar explicando además
del error de precio (iliquidez, un dato viejo, distinta legislación, riesgo de reperfilamiento).
Escribilo como observación de valor relativo con su contrapartida, que es lo que es. Si no hay nada
que valga la pena, decilo y listo — no llenes la sección.

Antes de afirmar que algo está desarbitrado, chequeá que el instrumento tenga precio de HOY y no
esté en `sinDato` ni en `convencionDudosa`. La mayoría de los desarbitrajes aparentes son datos
viejos.

═══════════════════════════════════════════════════════════════════
PASO 3 · CIERRES SEMANAL Y MENSUAL
═══════════════════════════════════════════════════════════════════

El campo `tipos` del JSON dice qué cierres caen hoy. Se calcula mirando la próxima rueda hábil
contra el calendario de feriados, así que ya contempla los viernes feriados y los fin de mes que
caen domingo. No lo recalcules por tu cuenta.

LOS DATOS YA VIENEN CALCULADOS, no hace falta que busques nada. Cuando la rueda es cierre de
período, el script pide además la última rueda hábil del período anterior y deja:

  · `sinDatoPorFamilia` — los que no operaron, agrupados. Cada curva ya lo dice en su pie
    («están los 5 de 11 que operaron»), pero si una familia queda con la mitad del panel afuera
    conviene decirlo también en el texto: la mediana de esa familia se calculo sobre pocos.
  · `referencias.semanal` / `referencias.mensual` — la fecha contra la que se midió. NOMBRALA en el
    informe («contra el viernes 21/08») en vez de decir «la semana pasada».
  · `resumen[familia].semanal` y `.mensual` — con `precio` y `tasa`, misma estructura que la diaria.
  · Por instrumento, `varPrecio_semanal`, `varTasa_semanal` y sus equivalentes mensuales.

EL DÍA QUE CIERRA PERÍODO SALEN DOS INFORMES, NO UNO. Uno es el diario de siempre, con la ventana
del día y NADA del período. El otro es el de cierre, con la ventana del período y NADA del día. Son
dos PDF y dos mails.

Antes iban pegados —columnas "En el día" y "En el mes" en la misma tabla, y prosa que saltaba de
una ventana a la otra— y se leía mal: hay que estar recordando de qué ventana habla cada frase.
Peor todavía, invita a leer la columna del período con la regla del día. Pasó: el cierre de agosto
de 2026 mostraba cinco familias con precio y tasa subiendo juntos, que en la columna del día sería
un error de dato y en la del mes es lo normal.

CÓMO SE ARMA CADA UNO:

    informe_pdf.construir(json, dir_curvas, textos, salida, modo="diario")
    informe_pdf.construir(json, dir_curvas, textos_periodo, salida_periodo, modo="periodo")

El modo decide las columnas de la tabla, el título de portada —el mismo JSON produce un PDF que
dice "Reporte diario" y otro "Reporte mensual"— y si aparecen la sección de cierre y la nota sobre
los signos. En modo "periodo" la columna N pasa a ser la del PERÍODO: cuántos instrumentos tenían
dato en las DOS puntas, que es sobre cuántos se calculó esa mediana. En el cierre de agosto eso
deja a los subsoberanos en 4 sobre 11, que es la verdad y antes había que aclararla en prosa.

NOMBRES DE ARCHIVO, que la página de curvas usa para rotular los links:

    informes/curvas/AAAA-MM-DD/cierre-AAAA-MM-DD.pdf            (el diario)
    informes/curvas/AAAA-MM-DD/cierre-mensual-AAAA-MM-DD.pdf    (o cierre-semanal-...)

LOS TEXTOS SON DISTINTOS, no el mismo recortado. El diario cuenta la rueda: qué comprimió, qué se
estiró, qué se despegó de su curva. El de cierre cuenta el período: qué familia lideró, cómo quedó
la curva de punta a punta, qué cambió de dirección respecto de la ventana anterior. Si una
observación sólo tiene sentido con las dos ventanas juntas —"la rueda fue contra la semana"— va en
el de cierre, que es donde la comparación es el tema.

LOS DOS MAILS. Primero el diario, con el asunto de siempre. Después el de cierre, con "· cierre
semanal" o "· cierre de mes" en el asunto y su propio PDF. Cada uno se lee solo: el de cierre no
supone que el lector abrió el otro.

Si `tipos` incluye "semanal": el informe de cierre lleva la variación por familia y, sobre todo,
QUÉ CAMBIÓ DE DIRECCIÓN. Lo más útil de tener las dos ventanas juntas es cuando no coinciden: el
28/08/2026 las tasas en pesos habían subido en la semana (LECAPs +0,18 pp, TAMAR +1,45, Duales
+1,44) y bajaron en el día, o sea que la rueda fue contra la semana. Eso vale más que repetir la
tabla diaria con otros números.

Si incluye "mensual": lo mismo contra la última rueda del mes anterior, más un párrafo de cómo
quedó el mes: qué familia lideró, qué pasó con la curva de pesos de punta a punta, y cómo se
movieron las paridades de los hard dollar.

UN CIERRE DE MES NO ES NECESARIAMENTE UN CIERRE DE SEMANA. El lunes 31/08/2026 es la última rueda
de agosto pero no la de su semana —la próxima hábil, el martes 1, cae en la misma semana ISO—, así
que `tipos` va a ser `['diario', 'mensual']` a secas. Ese día NO hay sección semanal ni datos
semanales: no la escribas ni la busques. Verificado corriendo tipos_de_cierre() contra el
calendario real; la referencia mensual da 2026-07-31.

Cuando una rueda cierra mes Y semana a la vez —el último hábil del mes caído viernes—, el informe
de cierre es UNO SOLO y muestra el mes en la tabla; la semana se cuenta en prosa adentro de ese
mismo informe. No son tres mails.

EL PDF SE ADAPTA SOLO: informe_pdf.py elige el período con periodo_de(tipos), rotula la columna «En
el mes» o «En la semana», nombra la fecha de referencia y titula la sección «Cierre mensual» o
«Cierre semanal». Vos pasás la prosa en `textos["cierre"]` —la clave vieja `textos["semanal"]`
también se acepta— y no tenés que tocar nada más.

EL MAIL NO se adapta solo: lo escribís vos cada día. En un cierre de mes, los encabezados de la
tabla dicen «En el mes» y la nota al pie nombra la rueda del mes anterior.

Para el mes, mirá especialmente el bloque macro: con dos a cuatro días hábiles de rezago, la
variación mensual de las series del BCRA se mide contra un dato que puede ser de fin del mes
anterior menos unos días. La fecha de cada serie está en el JSON; si la referencia quedó lejos,
decilo en vez de presentarla como «el mes».

Ojo con el `n` de cada período: es cuántos instrumentos tenían dato en las DOS puntas. Si es mucho
menor que el de la familia, la mediana del período se calculó sobre pocos y conviene decirlo.

═══════════════════════════════════════════════════════════════════
PASO 4 · LAS CURVAS
═══════════════════════════════════════════════════════════════════

Generalas con el script del repo, que ya las deja con la identidad de Balanz. Corren en ESTA
maquina, no en el runner: el workflow de GitHub solo saca los datos. Las librerias que hacen falta
—matplotlib, Pillow, adjustText para separar los rotulos, reportlab para el PDF y pypdfium2 para
revisarlo— ya estan instaladas; si alguna faltara, el script avisa y sigue sin ella.

    cd "C:\Users\Usuario\Downloads\Monitor-SoberanosARG"
    py curvas_informe.py <el JSON que bajaste>

Deja NUEVE PNG (~150 KB en total): globales_bonares, lecaps_tem, cer, lecaps_cer, breakeven,
tamar, dl, subsoberanos y futuros. Y además escribe un index.html con las nueve, que es lo que se
linkea.

TODAS AL MISMO TAMAÑO, 9,5 x 5,2 pulgadas. Si alguna vez hace falta una figura de dos paneles, van
como dos PNG separados y no apilados en una sola imagen: apilados, al llevarlos al ancho de columna
cada panel queda a la mitad de alto que el resto y se vuelven ilegibles. Y el pie que va DENTRO del
PNG hay que cortarlo a mano con saltos de línea: en una sola línea larga, bbox_inches="tight"
ensancha la figura y esa curva sale con otra proporción que las demás.

La de futuros sale de spreads_sinteticos.json, no del JSON del informe: precio de cada contrato
DLR en el eje izquierdo y la devaluación acumulada contra el mayorista de esa misma rueda en el
derecho. Toma sola la última rueda del archivo, que a las 17:30 es la de AYER porque A3 publica
el ajuste después del clearing.

LAS CURVAS NO VAN DENTRO DEL MAIL. VAN POR LINK. No es una preferencia de diseño: el envío de
Gmail sanitiza el HTML y BORRA TODA ETIQUETA <img>. Verificado el 28/08/2026 mandando cinco
variantes en un mismo mensaje y releyendo después el fuente que quedó en el servidor —<img> suelto,
con style, dentro de <a>, dentro de <table>, y background-image por CSS—: las cinco desaparecen.
Los <a href> sí sobreviven. Antes de eso perdí varios envíos probando raw.githubusercontent contra
GitHub Pages, que era el diagnóstico equivocado: el problema nunca estuvo del lado del servidor de
las imágenes.

Con adjuntos inline tampoco alcanza: el adjunto llega, pero el <img src="cid:..."> que lo
referencia se cae igual, y encima Gmail le asigna un Content-ID propio (ii_...) que no coincide con
el filename. Y siete PNG en base64 son unos 150.000 caracteres, que exceden el límite del tool call
y hacen fallar el envío entero.

ASÍ QUE: copiá la carpeta a informes/curvas/AAAA-MM-DD/, commiteala junto al JSON, esperá a que
Pages publique —tarda uno o dos minutos, verificalo con un curl— y poné UN SOLO link destacado
arriba del informe, hacia:

    https://ignacio-talento.github.io/Monitor-SoberanosARG/informes/curvas/AAAA-MM-DD/

Publicarlas deja archivo: el informe de cualquier rueda se puede releer después con sus gráficos.

MISMO PROBLEMA CON LOS FONDOS. El sanitizador también se come `background`, `background-color` y
`background-image`. Los recuadros de aviso tienen que marcarse con `border-left` y color de texto,
nunca con un fondo de color, porque el fondo no llega y el bloque queda indistinguible del cuerpo.
`color`, `border`, `padding`, `font-size` y `font-weight` sí pasan.

Las curvas de CER y TAMAR incluyen la PATA correspondiente de cada dual, no la tasa del instrumento
entero: 1816 publica un ticker por pata (`TXMD8 @CER`, `TXMD8 @TAMAR`) y cada uno trae su tasa y su
duration. El script ya los pide así.

Si el script falla o alguna curva sale vacía, mandá el mail igual y decilo en una línea; la página
lista sola las que faltaron. El informe sin gráficos sirve; el informe que no llega, no.

═══════════════════════════════════════════════════════════════════
PASO 4 BIS · EL PDF PARA COMPARTIR
═══════════════════════════════════════════════════════════════════

El usuario reenvía este PDF a colegas que no tienen nada que ver con el monitor. Se arma con
informe_pdf.construir(json, dir_curvas, textos, salida, modo=...), donde `textos` es un dict con la
prosa del día —incluida la clave `canje`, que es la sección del canje CCL/MEP y va en los tres
tipos de informe— —las mismas secciones que escribís para el mail—. El módulo pone la maqueta, las tablas y las
figuras; vos ponés el texto.

QUÉ SACAR, que es todo el punto:
  · vencimientos y altas o bajas del universo del monitor
  · referencias a las solapas ("como está en Glob vs Bon", "la cuenta de la solapa")
  · el detalle de qué se le pide a cada fuente y las convenciones de la API
  · cualquier cosa que sólo le sirva a quien mantiene el tablero

QUÉ NO SACAR: las aclaraciones que cambian cómo se lee un número —mediana y no promedio, la moneda
de cada familia, el rezago de las series del BCRA, por qué el CER ultracorto queda afuera del
breakeven—. Sin eso el lector concluye mal, y eso no es una particularidad del monitor sino del
dato. Las fuentes también van: es atribución, no plomería.

EL ENCABEZADO YA LO PONE informe_pdf.py: el lockup oficial de Balanz —el asset del skill de marca,
nunca tipografiado a mano— blanco sobre la banda de la portada y navy en las páginas interiores, y
el título «Cierre de mercado · Reporte diario / semanal / mensual» derivado del campo `tipos` del
JSON. No hay que pasarle nada: el viernes dice semanal solo.

Guardalo como informes/curvas/AAAA-MM-DD/cierre-AAAA-MM-DD.pdf. La página índice lo detecta sola y
ofrece el link de descarga arriba de todo.

NO SE PUEDE ADJUNTAR. Pesa unos 380 KB, o sea medio millón de caracteres en base64: no entra en una
llamada al tool de envío. Va por link, igual que las curvas, y el mail explica que se descarga y se
reenvía como archivo.

NO LLEVA EL DISCLAIMER INSTITUCIONAL de Balanz. El texto legal de la casa arranca con "ha sido
preparada por Balanz Capital Valores S.A.U." y esto es un resumen propio, no research oficial:
ponérselo lo haría pasar por lo que no es. Si alguna vez se decide que circule como pieza
institucional, el verbatim está en el skill balanz-design y lo tiene que aprobar la casa.

LOS PIES QUE VAN DENTRO DE LOS PNG se ven también acá, así que no pueden nombrar al monitor ni al
proveedor de datos. Si agregás una curva nueva, revisá su texto con ese criterio.

═══════════════════════════════════════════════════════════════════
PASO 5 · MANDAR EL MAIL
═══════════════════════════════════════════════════════════════════

A ignaciotalento@gmail.com, con la herramienta de Gmail, en htmlBody.

Asunto: "Renta fija AR · cierre DD/MM" y, cuando corresponda, "· cierre semanal" o "· cierre de
mes" al final.

Arriba de todo, un bloque con los DOS LINKS: primero el PDF para compartir —con una línea que
aclare que es la misma información sin nada del monitor, y que se descarga y se reenvía como
archivo— y debajo la página de las curvas. Los links son lo único que sobrevive el saneado del
envío, así que van destacados y no perdidos en el pie.

Formato: HTML sobrio y legible en el cliente de mail —tabla con bordes finos para las variaciones,
verde para las subas y rojo para las bajas, el resto en prosa corta—. Empezá con dos o tres líneas
que resuman la rueda antes de la tabla, para que se entienda leyendo sólo el principio en el
teléfono.

NADA DE IMÁGENES NI DE FONDOS DE COLOR: el envío los borra (ver PASO 4). Los recuadros de aviso se
marcan con `border-left` y color de texto. El resto —color, borde, padding, tamaño, peso, tablas y
`<a href>`— pasa sin problema.

TAMAÑO: el htmlBody entra cómodo hasta unos 37 KB, que es lo que ocupa el informe completo con las
cinco tablas. Si te vas mucho más arriba, acortá la prosa que ya está desarrollada en el PDF antes
que sacar una tabla: las tablas son lo que no está en ningún otro lado del mail.

Al pie, una línea de procedencia: fuente de los datos (1816 para bonos, A3 para futuros), hora de
extracción, y cuántos instrumentos quedaron sin precio. Redactá eso último como «sin precio del día
en la punta en la que se valúan» y NO como «no registraron operaciones»: varias ONs de ley local
pagan en cable y operan en MEP, así que aparecen en `sinDato` habiendo operado. Ver la advertencia
de la SECCIÓN A.

Sé honesto con lo que no pudiste calcular, y no rellenes un hueco con la serie de al lado. Si una
serie del BCRA vino con más rezago del habitual o directamente falló —mirá `macro.fallos`—, decilo
en el pie en vez de omitirla en silencio. Lo mismo si `macro.sslSinVerificar` es true: significa que
el certificado del BCRA no validó y el dato se trajo igual, y eso merece una línea.

REPORTAR en el chat, en dos o tres líneas: que el mail salió, a qué hora, y cualquier cosa rara que
hayas visto en los datos. No pegues el informe entero en el chat.