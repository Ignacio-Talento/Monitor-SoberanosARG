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

// Headers mínimos a propósito. Desde un Worker, mandarle a A3 un Referer de otro dominio hacía
// que devolviera 424; con Accept solo, responde igual que a curl.
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

async function pedir(ruta, params) {
  const url = new URL(CEM + ruta);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const r = await fetch(url, { headers: CABECERAS });
  if (!r.ok) {
    // El cuerpo del error viaja en el mensaje: A3 explica qué parámetro no le gustó, y sin eso
    // un 400 o un 424 son indistinguibles de "la API se cayó".
    let detalle = "";
    try { detalle = (await r.text()).slice(0, 200); } catch (e) { /* da igual */ }
    throw new Error(`${ruta} HTTP ${r.status}${detalle ? " · " + detalle : ""}`);
  }
  return r.json();
}

const iso = (d) => d.toISOString();

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

  let ticks, cierres, rueda;
  try {
    // UN solo pedido de ticks, ordenado del más reciente hacia atrás. Antes se hacían dos —uno
    // para averiguar la última rueda y otro para traerla— y con eso más el de cierres el Function
    // pasaba de 45 segundos y el navegador cortaba. Los ticks de una rueda rondan los 2.200, así
    // que en 5.000 entra la última completa aunque el rango abarque tres días.
    [ticks, cierres] = await Promise.all([
      pedir("/tick-prices", {
        product: "DLR", from: iso(new Date(ahora.getTime() - 3 * 86400000)), to: iso(ahora),
        pageSize: 5000, sort: "dateTime", sortDir: "DESC",
      }),
      // Cierres: alcanzan unos pocos, sólo se usa el más reciente de cada contrato.
      pedir("/closing-prices", {
        product: "DLR", type: "FUT",
        from: iso(new Date(ahora.getTime() - DIAS_ATRAS * 86400000)), to: iso(ahora),
        pageSize: 300, sort: "dateTime", sortDir: "DESC",
      }),
    ]);
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }

  const filas = ticks.data || [];
  if (!filas.length) return json({ error: "A3 no devolvió operaciones recientes" }, 502);

  // La rueda es la del tick más reciente, en día ARG (UTC−3) y no UTC: así un sábado o un feriado
  // devuelve la última rueda con mercado en vez de venir vacío.
  const diaArg = (s) => new Date(new Date(s).getTime() - 3 * 3600000).toISOString().slice(0, 10);
  rueda = diaArg(filas[0].dateTime);

  // ── agregación de los ticks por contrato ──
  const agg = {};
  for (const x of filas) {
    if (diaArg(x.dateTime) !== rueda) continue;   // el pedido abarca 3 días; sólo interesa la última
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

  const futuros = {}, fallos = [];
  for (const tk of pedidos) {
    const a = agg[tk], p = previo[tk];
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
      diag: { rueda, pedidos: pedidos.length, resueltos: Object.keys(futuros).length,
              ticks: filas.filter((x) => diaArg(x.dateTime) === rueda).length,
              fuente: "A3 Mercados · CEM (tick-prices + closing-prices)" } },
    200, { "cache-control": `public, max-age=${CACHE_TTL}` });

  if (Object.keys(futuros).length) await cache.put(clave, salida.clone());
  return salida;
}
