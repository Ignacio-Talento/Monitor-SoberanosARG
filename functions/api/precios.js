/**
 * Cloudflare Pages Function — proxy a la API de 1816 para PRECIOS LIVE.
 *
 *   POST /api/precios   body JSON: [{ "ticker": "AL30", "grupo": "usdbonares" }, ...]
 *   GET  /api/precios?ticker=AL30&grupo=usdbonares        (conveniencia, 1 ticker)
 *   -> { "AL30": 56.43, "S31L6": 116.77, ... }   (solo los que se pudieron resolver)
 *
 * Objetivos:
 *  - La API key vive en env.API_1816_KEY (Secret del proyecto Pages). NUNCA se expone al browser.
 *  - CACHÉ con TTL: una consulta a 1816 sirve a todos -> los créditos no dependen de las visitas.
 *  - Gateado por Cloudflare Access (que protege TODO el sitio Pages, incluido /api/*).
 *    Como defensa en profundidad, exigimos el header que Access inyecta (fail-closed).
 *  - Fallback server-side a Eco Valores para tickers que 1816 no tenga.
 *
 * Reglas de mapeo grupo -> (moneda, ticker 1816): mismas que el backend (actualizar_historicos.py,
 * resolver_1816 / MONEDA_1816), verificadas contra la API real. El valor de mercado es precioDirty.
 */

const BASE_1816   = "https://api.1816.com.ar";
const ECO_URL     = "https://ecovalores-proxy.granda-fra.workers.dev"; // fallback (worker del colega)
const CAMPO       = "precioDirty";
// Segundos que dura el caché. Es EL dial de consumo de créditos: el costo de 1816 es
// tickers x (campos + 1), así que una consulta del monitor cuesta ~272 y una jornada de 8 h
// con refresco continuo son 480 consultas -> ~131k, por encima del tope diario de 100.000.
// Con 120 s se baja a ~65k/día y queda margen para seguir sumando instrumentos.
// (Medido el 2026-07-20: 1816 es casi en vivo, ~5-15 s, y los líquidos cambian cada ~40 s,
// así que 120 s sigue mostrando precios frescos; el botón "Actualizar precios" saltea el caché.)
const CACHE_TTL   = 120;
const MAX_TICKERS = 50;    // límite de 1816 por request
const MAX_ECO_FALLBACK = 20; // tope de consultas a Eco (cada una es un subrequest; CF corta ~50)

// grupo del frontend -> moneda a pedir en 1816
const MONEDA = {
  lecap: "ars", tasafija: "ars", cer: "ars", tamar: "ars", usdlinked: "ars", dual: "ars",
  usdbonares: "mep", usdglobales: "mep", usdbopreal: "mep", onusd: "mep",
  subsoberano: "mep",
};
// Bopreales: ticker 1816 irregular (mapa explícito)
// Patrón: BP{XX}D -> BPO{XX}. Se deja explícito por si alguna serie no lo respeta.
const MAPA_BOPREAL = {
  BPA7D: "BPOA7", BPB7D: "BPOB7", BPC7D: "BPOC7", BPD7D: "BPOD7",
  BPA8D: "BPOA8", BPB8D: "BPOB8",
};

// Devuelve { t: <ticker 1816>, moneda } o null si no mapea (=> fallback a Eco)
function map1816(grupo, ticker) {
  const moneda = MONEDA[grupo];
  if (!moneda) return null;
  let t;
  if (grupo === "usdbonares" || grupo === "usdglobales") t = ticker;               // llega sin la D
  else if (grupo === "usdbopreal") t = MAPA_BOPREAL[ticker] || null;
  else if (grupo === "onusd") t = ticker.endsWith("D") ? ticker.slice(0, -1) + "O" : null;
  else t = ticker;                                                                  // pesos: idéntico
  return t ? { t, moneda } : null;
}

// Ticker que espera Eco (fallback): bonares/globales agregan D; el resto va tal cual
function tickerEco(grupo, ticker) {
  return (grupo === "usdbonares" || grupo === "usdglobales") ? ticker + "D" : ticker;
}

// --- rate limit: 1816 admite 1 request/segundo. Espaciamos TODAS las llamadas. ---
let _lastReq = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function throttle() {
  const wait = 1100 - (Date.now() - _lastReq);
  if (wait > 0) await sleep(wait);
  _lastReq = Date.now();
}

// --- token 1816 (cacheado en el isolate) ---
let _token = null, _tokenExp = 0;
async function getToken(apiKey) {
  const now = Date.now() / 1000;
  if (_token && _tokenExp - now > 300) return _token;
  await throttle();
  const r = await fetch(`${BASE_1816}/v1/auth/token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ apiKey, module: "mercado" }),
  });
  if (!r.ok) throw new Error("auth 1816 HTTP " + r.status);
  const d = await r.json();
  _token = d.token;
  _tokenExp = now + (d.expiresIn || 86400);
  return _token;
}

// Consulta 1816 para una lista de tickers en una moneda. -> { ticker: {campo: valor, ...} }
// `fecha` (YYYY-MM-DD) opcional: si va, se pide esa rueda; si es null, la de hoy.
// `campos` permite pedir más que el precio (los subsoberanos usan los indicadores ya
// calculados por 1816 en vez de computarlos localmente: no tenemos sus flujos).
async function fetch1816(apiKey, tickers, moneda, fecha, campos = [CAMPO]) {
  const out = {};
  for (let i = 0; i < tickers.length; i += MAX_TICKERS) {
    const lote = tickers.slice(i, i + MAX_TICKERS);
    const qs = new URLSearchParams();
    lote.forEach((t) => qs.append("tickers", t));
    campos.forEach((c) => qs.append("campos", c));
    qs.append("moneda", moneda);
    if (fecha) qs.append("fechaOperacion", fecha);

    const pedir = async () => {
      await throttle();
      const token = await getToken(apiKey);
      return fetch(`${BASE_1816}/v1/mercado/indicadores?` + qs, {
        headers: { Authorization: `Bearer ${token}` },
      });
    };
    let r = await pedir();
    if (r.status === 401) { _token = null; r = await pedir(); }   // token vencido
    if (r.status === 429) { await sleep(1200); r = await pedir(); } // rate limit: esperar y reintentar
    if (!r.ok) throw new Error("indicadores 1816 HTTP " + r.status);
    const d = await r.json();
    const inst = d.instrumentos || {};
    for (const t of lote) {
      if (inst[t]) out[t] = inst[t];
    }
  }
  return out;
}

// --- Última rueda con datos -------------------------------------------------
// 1816 NO tiene datos los fines de semana ni feriados: pedir "hoy" devuelve todo null.
// Buscamos hacia atrás la última fecha con datos usando UN ticker de referencia (barato).
// Los sábados/domingos se saltean por fecha, sin gastar llamadas.
const MS_ART = 3 * 3600 * 1000; // Argentina = UTC-3
function fechaART(offsetDias) {
  return new Date(Date.now() - MS_ART - offsetDias * 86400000);
}
async function resolverFecha(apiKey, tickerRef, moneda) {
  for (let i = 0; i <= 7; i++) {
    const d = fechaART(i);
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) continue;             // fin de semana: ni consultamos
    const fecha = i === 0 ? null : d.toISOString().slice(0, 10);
    const r = await fetch1816(apiKey, [tickerRef], moneda, fecha);
    // Ojo: 1816 devuelve el instrumento aunque no haya operado (con el campo en null),
    // así que hay que mirar el precio, no la presencia de la clave.
    if (r[tickerRef] && typeof r[tickerRef][CAMPO] === "number") return fecha;
  }
  return null;
}

async function fallbackEco(grupo, ticker) {
  try {
    const r = await fetch(`${ECO_URL}/?ticker=${tickerEco(grupo, ticker)}`);
    const d = await r.json();
    if (d && d.price > 0) return d.price;
  } catch (_e) { /* ignorar */ }
  return null;
}

// Núcleo: { "AL30": precio, ... } keyed por el ticker que mandó el frontend (inst.ticker)
async function computePrecios(env, items) {
  const apiKey = env.API_1816_KEY;

  // Se agrupa por moneda, salvo los subsoberanos, que van en su propio lote porque
  // se les piden campos extra (los indicadores que calcula 1816).
  const porMoneda = {}; // clave -> [{ eco, t, grupo, moneda }]
  for (const it of items) {
    const eco = String(it.ticker || "").trim().toUpperCase();
    const grupo = String(it.grupo || "").trim();
    if (!eco || !grupo) continue;
    const m = map1816(grupo, eco);
    if (!m) continue;
    const clave = grupo === "subsoberano" ? "subsoberano" : m.moneda;
    (porMoneda[clave] ||= []).push({ eco, t: m.t, grupo, moneda: m.moneda });
  }

  const result = {};
  const indicadores = {};   // solo subsoberanos: TIR/M.Dur/paridad que ya calcula 1816
  const monedas = Object.keys(porMoneda);
  if (apiKey && monedas.length) {
    // Una sola resolución de fecha para todas las monedas (fin de semana/feriado -> última rueda).
    const ref = porMoneda[monedas[0]][0];
    const fecha = await resolverFecha(apiKey, ref.t, ref.moneda);
    for (const clave of monedas) {
      const pares = porMoneda[clave];
      const moneda = pares[0].moneda;
      // Deduplicar: los duales mandan 2 filas por ticker y gastarían cupo del lote de 50.
      const tickers = [...new Set(pares.map((p) => p.t))];
      // Para subsoberanos pedimos además los indicadores: no tenemos su cronograma de
      // flujos, así que el front muestra los que calcula 1816 en vez de computarlos.
      const esSubsob = pares[0] && pares[0].grupo === "subsoberano";
      const campos = esSubsob ? [CAMPO, "tea", "durationMod", "paridad"] : [CAMPO];
      const datos = await fetch1816(apiKey, tickers, moneda, fecha, campos);
      for (const p of pares) {
        const fila = datos[p.t];
        if (!fila) continue;
        if (typeof fila[CAMPO] === "number") result[p.eco] = fila[CAMPO];
        if (esSubsob) {
          indicadores[p.eco] = {
            tea: fila.tea, durationMod: fila.durationMod, paridad: fila.paridad,
          };
        }
      }
    }
  }

  // Fallback a Eco SOLO para lo que 1816 no resolvió. Acotado: cada uno es un subrequest
  // y Cloudflare corta en ~50 por request (si no, un día sin datos deja la respuesta a medias).
  const grupoDe = {};
  for (const it of items) grupoDe[String(it.ticker || "").trim().toUpperCase()] = String(it.grupo || "").trim();
  const pendientes = [...new Set(
    items.map((it) => String(it.ticker || "").trim().toUpperCase())
         // Los subsoberanos no están en Eco: pedirlos sólo gastaría subrequests.
         .filter((eco) => eco && !(eco in result) && grupoDe[eco] !== "subsoberano")
  )].slice(0, MAX_ECO_FALLBACK);
  for (const eco of pendientes) {
    const p = await fallbackEco(grupoDe[eco], eco);
    if (p) result[eco] = p;
  }
  return { precios: result, indicadores };
}

// --- helpers HTTP ---
function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}
function hashItems(items) {
  const s = items.map((i) => `${i.ticker}:${i.grupo}`).sort().join(",");
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(16);
}

export async function onRequest(context) {
  const { request, env } = context;

  // Gate (defensa en profundidad). Access inyecta este header cuando la request pasó por él.
  // Fail-closed: sin Access no hay precios (así no se queman créditos si el gate no está puesto).
  // Para probar ANTES de configurar Access, poné temporalmente env ALLOW_NO_ACCESS=1.
  const hasAccess = !!request.headers.get("Cf-Access-Jwt-Assertion");
  if (!hasAccess && env.ALLOW_NO_ACCESS !== "1") {
    return json({ error: "no autorizado (Cloudflare Access requerido)" }, 403);
  }

  // Parsear items
  let items = [];
  if (request.method === "POST") {
    try { items = await request.json(); } catch { return json({ error: "body JSON inválido" }, 400); }
    if (!Array.isArray(items)) return json({ error: "se espera un array [{ticker,grupo}]" }, 400);
  } else if (request.method === "GET") {
    const u = new URL(request.url);
    const ticker = u.searchParams.get("ticker");
    const grupo = u.searchParams.get("grupo");
    if (ticker && grupo) items = [{ ticker, grupo }];
    else return json({ error: "faltan ?ticker= y ?grupo=" }, 400);
  } else {
    return json({ error: "método no soportado" }, 405);
  }
  if (!items.length) return json({});

  // Caché (mismo set de tickers -> misma key -> hit entre visitas/usuarios dentro del TTL).
  // ?fresh=1 (botón "Actualizar precios") saltea el caché y pide dato fresco a 1816.
  const fresh = new URL(request.url).searchParams.get("fresh") === "1";
  const cache = caches.default;
  const cacheKey = new Request("https://cache.local/precios?h=" + hashItems(items), { method: "GET" });
  if (!fresh) {
    const hit = await cache.match(cacheKey);
    if (hit) return hit;
  }

  let datos;   // { precios: {ticker: precio}, indicadores: {ticker: {...}} }
  try {
    datos = await computePrecios(env, items);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }

  const resp = json(datos, 200, { "cache-control": `public, max-age=${CACHE_TTL}` });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
