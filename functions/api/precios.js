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
const CACHE_TTL   = 600;   // segundos que dura el caché (10 min). Subir = menos créditos, dato más viejo.
const MAX_TICKERS = 50;    // límite de 1816 por request

// grupo del frontend -> moneda a pedir en 1816
const MONEDA = {
  lecap: "ars", tasafija: "ars", cer: "ars", tamar: "ars", usdlinked: "ars", dual: "ars",
  usdbonares: "mep", usdglobales: "mep", usdbopreal: "mep", onusd: "mep",
};
// Bopreales: ticker 1816 irregular (mapa explícito)
const MAPA_BOPREAL = { BPC7D: "BPOC7", BPD7D: "BPOD7", BPA8D: "BPOA8", BPB8D: "BPOB8" };

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

// Consulta precioDirty a 1816 para una lista de tickers 1816 en una moneda. -> { ticker: precio }
async function fetch1816(apiKey, tickers, moneda) {
  const out = {};
  for (let i = 0; i < tickers.length; i += MAX_TICKERS) {
    const lote = tickers.slice(i, i + MAX_TICKERS);
    const qs = new URLSearchParams();
    lote.forEach((t) => qs.append("tickers", t));
    qs.append("campos", CAMPO);
    qs.append("moneda", moneda);

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
      const v = inst[t] && inst[t][CAMPO];
      if (typeof v === "number") out[t] = v;
    }
  }
  return out;
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

  const porMoneda = {}; // moneda -> [{ eco, t }]
  for (const it of items) {
    const eco = String(it.ticker || "").trim().toUpperCase();
    const grupo = String(it.grupo || "").trim();
    if (!eco || !grupo) continue;
    const m = map1816(grupo, eco);
    if (m) (porMoneda[m.moneda] ||= []).push({ eco, t: m.t });
  }

  const result = {};
  if (apiKey) {
    for (const moneda of Object.keys(porMoneda)) {
      const pares = porMoneda[moneda];
      const precios = await fetch1816(apiKey, pares.map((p) => p.t), moneda);
      for (const p of pares) if (p.t in precios) result[p.eco] = precios[p.t];
    }
  }

  // Fallback a Eco para lo que 1816 no resolvió (raro: instrumentos que ya no cotizan).
  for (const it of items) {
    const eco = String(it.ticker || "").trim().toUpperCase();
    if (!eco || eco in result) continue;
    const p = await fallbackEco(String(it.grupo || "").trim(), eco);
    if (p) result[eco] = p;
  }
  return result;
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

  // Caché (mismo set de tickers -> misma key -> hit entre visitas/usuarios dentro del TTL)
  const cache = caches.default;
  const cacheKey = new Request("https://cache.local/precios?h=" + hashItems(items), { method: "GET" });
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let precios;
  try {
    precios = await computePrecios(env, items);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }

  const resp = json(precios, 200, { "cache-control": `public, max-age=${CACHE_TTL}` });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
