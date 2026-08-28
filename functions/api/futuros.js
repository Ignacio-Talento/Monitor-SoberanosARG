/**
 * Cloudflare Pages Function — futuros de dólar de A3 Mercados (ex Matba Rofex).
 *
 *   GET /api/futuros?tickers=DLR/SEP26,DLR/OCT26
 *   -> { futuros: { "DLR/SEP26": { precio, ultimaOperacion, volumen, operaciones,
 *                                  ajusteAnterior, openInterest, ... } },
 *        fallos: [...], diag: { rueda, fuente, ... } }
 *
 * FUENTE: el Centro de Estadísticas de Mercado de A3, API pública y sin credenciales
 * (apicem.matbarofex.com.ar/api/v2). Es el mercado mismo, no un intermediario.
 *
 * ANTES SE USABA ECO VALORES, por scraping, y era peor en todo:
 *  - publicaba información de BYMA DIFERIDA 20 MINUTOS y lo decía en su propia página;
 *  - devolvía la página sin datos de forma intermitente (~40% de las consultas en DLR/DIC26);
 *  - obligaba a una consulta por contrato;
 *  - y sobre todo DABA DATOS EQUIVOCADOS: el 27/08/2026 mostraba DLR/ABR27 a 1745 sin volumen,
 *    o sea "no operó", cuando A3 registra una operación de 2.000 nominales a 1738 a las 14:00.
 *    Ese precio rancio sostenía el spread más ancho de toda la tabla.
 *
 * CÓMO SE ARMA LA FOTO DEL DÍA. `tick-prices` da operación por operación con su timestamp, así que
 * el último precio, el volumen, la cantidad de operaciones y la hora de la última salen de agregar
 * los ticks de la rueda. `closing-prices` aporta el ajuste y el interés abierto de la rueda
 * anterior — el ajuste del día sale recién después del clearing, por eso no se lo espera.
 *
 * LA RUEDA NO SE ASUME "HOY": se busca la última con ticks. Así un lunes feriado o un sábado
 * devuelve la rueda del viernes en vez de venir vacío, que es lo que hace el resto del monitor.
 */

const CEM = "https://apicem.matbarofex.com.ar/api/v2";
const CACHE_TTL = 120;
const MAX_TICKERS = 30;
// Cuántos días para atrás buscar la última rueda con operaciones. Cubre un fin de semana largo.
const DIAS_ATRAS = 6;

// Headers mínimos: no hacen falta más.
const CABECERAS = { "Accept": "application/json" };

const MES_TXT = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                 "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });

/**
 * DLR092026 -> DLR/SEP26. null para lo que no sea un futuro de dólar simple.
 *
 * Las opciones vienen en el mismo listado como "DLR082026 Call 1500": se descartan por el espacio.
 * Si entraran, el frontend las tomaría por contratos y armaría curvas con strikes adentro.
 */
function aTicker(symbol) {
  if (!symbol || symbol.includes(" ") || !symbol.startsWith("DLR")) return null;
  const resto = symbol.slice(3);
  if (resto.length !== 6 || !/^\d+$/.test(resto)) return null;
  const mes = parseInt(resto.slice(0, 2), 10);
  const anio = parseInt(resto.slice(2), 10);
  if (mes < 1 || mes > 12) return null;
  return `DLR/${MES_TXT[mes - 1]}${String(anio % 100).padStart(2, "0")}`;
}

async function pedir(ruta, params, ms) {
  const url = new URL(CEM + ruta);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  // Timeout propio: cuando tick-prices se degrada, A3 tarda 31 segundos en contestar 424. Sin
  // cortar antes, la página espera medio minuto para terminar sin datos.
  const r = await fetch(url, { headers: CABECERAS, signal: AbortSignal.timeout(ms || 10000) });
  if (!r.ok) {
    // El cuerpo del error viaja en el mensaje: A3 explica qué parámetro no le gustó, y sin eso
    // un 400 o un 424 son indistinguibles de "la API se cayó".
    let detalle = "";
    try { detalle = (await r.text()).slice(0, 200); } catch (e) { /* da igual */ }
    throw new Error(`${ruta} HTTP ${r.status}${detalle ? " · " + detalle : ""}`);
  }
  return r.json();
}

// Los dos endpoints piden formatos DISTINTOS de fecha, aunque son del mismo API:
// tick-prices sólo acepta ISO completo y closing-prices sólo AAAA-MM-DD. Pasarle a uno el
// formato del otro devuelve 400 con "El atributo [from] no es válido".
const iso = (d) => d.toISOString();
const dia = (d) => d.toISOString().slice(0, 10);

export async function onRequest({ request }) {
  const url = new URL(request.url);
  const pedidos = (url.searchParams.get("tickers") || "")
    .split(",").map((t) => t.trim()).filter(Boolean);
  if (!pedidos.length) return json({ error: "falta el parámetro tickers" }, 400);
  if (pedidos.length > MAX_TICKERS) {
    return json({ error: `máximo ${MAX_TICKERS} tickers por pedido` }, 400);
  }

  const clave = new Request(`https://futuros.cache/${pedidos.slice().sort().join(",")}`,
                            { method: "GET" });
  const cache = caches.default;
  const guardado = await cache.match(clave);
  if (guardado) return guardado;

  const ahora = new Date();
  const desde = new Date(ahora.getTime() - DIAS_ATRAS * 86400000);

  // Los ticks se piden RUEDA POR RUEDA, empezando por hoy y retrocediendo.
  //
  // Pedir un rango de varios días hace que A3 se caiga por timeout de su propia base:
  //     424 · "Execution Timeout Expired. The timeout period elapsed prior to completion..."
  // Ese 424 es del origen, no del Worker. Un día solo —unos 2.200 ticks— responde sin problema.
  //
  // Primero se sondea con pageSize=1 si esa fecha tuvo operaciones, y sólo entonces se trae
  // completa: así un sábado cuesta dos consultas mínimas en vez de una pesada que vuelve vacía.
  // DOS FUENTES, POR ORDEN DE PREFERENCIA.
  //
  //  1. tick-prices — operación por operación, con hora. Es lo que permite mostrar el último
  //     precio operado y a qué hora, o sea la foto real de la rueda en curso.
  //  2. closing-prices — el ajuste. Se usa SÓLO si la primera falla.
  //
  // Hace falta el segundo porque tick-prices se cae con cierta frecuencia del lado de A3: devuelve
  // 424 con "Execution Timeout Expired" después de 31 segundos, y no depende del pedido — el
  // 28/08/2026 fallaba hasta el sondeo de un solo registro mientras closing-prices contestaba en
  // dos segundos. Sin fallback, la solapa se queda sin precios enteros.
  let filas = null, rueda = null, cierres = null, modo = "intradia", avisoTicks = null;
  try {
    for (let atras = 0; atras <= DIAS_ATRAS && !filas; atras++) {
      const ref = new Date(ahora.getTime() - atras * 86400000);
      const argRef = new Date(ref.getTime() - 3 * 3600000);
      const y = argRef.getUTCFullYear(), m = argRef.getUTCMonth(), d = argRef.getUTCDate();
      const ini = new Date(Date.UTC(y, m, d, 3, 0, 0));         // 00:00 ARG
      const fin = new Date(Date.UTC(y, m, d + 1, 2, 59, 59));   // 23:59 ARG
      const sondeo = await pedir("/tick-prices", {
        product: "DLR", from: iso(ini), to: iso(fin), pageSize: 1,
      }, 8000);
      if (!(sondeo.data || []).length) continue;                // esa fecha no tuvo mercado
      const completo = await pedir("/tick-prices", {
        product: "DLR", from: iso(ini), to: iso(fin), pageSize: 5000,
      }, 20000);
      filas = completo.data || [];
      rueda = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    }
  } catch (e) {
    avisoTicks = String((e && e.message) || e);
    filas = null;
  }

  try {
    cierres = await pedir("/closing-prices", {
      product: "DLR", type: "FUT",
      from: dia(new Date(ahora.getTime() - DIAS_ATRAS * 86400000)), to: dia(ahora),
      pageSize: 300, sort: "dateTime", sortDir: "DESC",
    }, 15000);
  } catch (e) {
    // Si además falla esto no queda nada que servir.
    if (!filas) return json({ error: `A3 no responde · ticks: ${avisoTicks} · cierres: ${e.message}` }, 502);
    cierres = { data: [] };
  }

  if (!filas || !filas.length) {
    modo = "ajuste";
    const porFecha = {};
    for (const x of cierres.data || []) {
      const f = String(x.dateTime).slice(0, 10);
      (porFecha[f] ||= []).push(x);
    }
    rueda = Object.keys(porFecha).sort().pop() || null;
    if (!rueda) {
      return json({ error: `A3 no devolvió datos · ticks: ${avisoTicks || "sin operaciones"}` }, 502);
    }
  }

  // ── agregación de los ticks por contrato ──
  const agg = {};
  for (const x of (filas || [])) {
    const tk = aTicker(x.symbol);
    if (!tk) continue;
    const a = (agg[tk] ||= { volumen: 0, operaciones: 0, ultimo: null, precio: null,
                             minimo: null, maximo: null, primero: null, apertura: null });
    a.volumen += Number(x.volume) || 0;
    a.operaciones += 1;
    const p = Number(x.price);
    if (a.minimo === null || p < a.minimo) a.minimo = p;
    if (a.maximo === null || p > a.maximo) a.maximo = p;
    if (!a.ultimo || x.dateTime > a.ultimo) { a.ultimo = x.dateTime; a.precio = p; }
    if (!a.primero || x.dateTime < a.primero) { a.primero = x.dateTime; a.apertura = p; }
  }

  // ── último cierre conocido por contrato (el más reciente de la lista, que viene DESC) ──
  const previo = {};
  for (const x of cierres.data || []) {
    const tk = aTicker(x.symbol);
    if (!tk || previo[tk]) continue;
    previo[tk] = { ajuste: Number(x.settlement) || null,
                   openInterest: Number(x.openInterest) || null,
                   fecha: String(x.dateTime).slice(0, 10) };
  }

  const horaArg = (s) => s
    ? new Date(new Date(s).getTime() - 3 * 3600000).toISOString().slice(11, 19)
    : null;

  // En modo ajuste, el "precio" es el settlement de la rueda y el volumen el de esa rueda: no hay
  // hora de última operación, y se dice explícitamente para que el frontend no lo muestre como si
  // fuera intradía.
  const deRueda = {};
  if (modo === "ajuste") {
    for (const x of cierres.data || []) {
      if (String(x.dateTime).slice(0, 10) !== rueda) continue;
      const tk = aTicker(x.symbol);
      if (tk) deRueda[tk] = x;
    }
  }

  const futuros = {}, fallos = [];
  for (const tk of pedidos) {
    const a = agg[tk], p = previo[tk], c = deRueda[tk];
    if (modo === "ajuste") {
      if (!c) { fallos.push(tk); continue; }
      futuros[tk] = {
        precio: Number(c.settlement) || Number(c.close) || null,
        volumen: Number(c.volume) || 0,
        operaciones: Number(c.tradeCount) || 0,
        ultimaOperacion: null,
        apertura: Number(c.open) || null,
        minimo: Number(c.low) || null,
        maximo: Number(c.high) || null,
        ajusteAnterior: Number(c.previousClose) || null,
        openInterest: Number(c.openInterest) || null,
        fechaAjuste: rueda,
      };
      continue;
    }
    // Sin ticks pero con ajuste previo: el contrato existe y no operó en la rueda. Se devuelve el
    // ajuste con volumen 0 para que el frontend lo marque, en vez de omitirlo como si no existiera.
    if (!a && !p) { fallos.push(tk); continue; }
    futuros[tk] = {
      precio: a ? a.precio : p.ajuste,
      volumen: a ? a.volumen : 0,
      operaciones: a ? a.operaciones : 0,
      ultimaOperacion: a ? horaArg(a.ultimo) : null,
      apertura: a ? a.apertura : null,
      minimo: a ? a.minimo : null,
      maximo: a ? a.maximo : null,
      ajusteAnterior: p ? p.ajuste : null,
      openInterest: p ? p.openInterest : null,
      fechaAjuste: p ? p.fecha : null,
    };
  }

  const salida = json(
    { futuros, fallos,
      diag: { rueda, modo, pedidos: pedidos.length, resueltos: Object.keys(futuros).length,
              ticks: (filas || []).length,
              ...(avisoTicks ? { avisoTicks } : {}),
              fuente: modo === "intradia"
                ? "A3 Mercados · CEM (tick-prices)"
                : "A3 Mercados · CEM (closing-prices · ajuste, tick-prices no respondió)" } },
    200, { "cache-control": `public, max-age=${CACHE_TTL}` });

  if (Object.keys(futuros).length) await cache.put(clave, salida.clone());
  return salida;
}
