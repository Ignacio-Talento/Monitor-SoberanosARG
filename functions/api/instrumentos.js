/**
 * Cloudflare Pages Function — universo de instrumentos SOBERANOS vivos, vía 1816.
 *
 *   GET /api/instrumentos
 *   -> { generado: "<ISO>", instrumentos: [ { ticker, grupo, emision, venc }, ... ] }
 *
 * Se usa en bonos.html para DETECTAR altas (títulos nuevos que 1816 lista y no están
 * en Instrumentos.xlsx) y bajas (vencidos). NO calcula métricas: 1816 no da cupones/
 * margen/lag, así que el alta final la completa una persona (ver banner en el front).
 *
 * - La API key vive en env.API_1816_KEY (Secret). Nunca se expone al browser.
 * - Gateado por Cloudflare Access (igual que /api/precios); exigimos el header (fail-closed).
 * - Caché largo: la emisión de bonos nuevos es infrecuente (default 6 h).
 * - Sólo curvas SOBERANAS que el monitor sigue de forma exhaustiva. Se excluye adrede
 *   "Corporativos USD" (los ON USD del monitor son un subconjunto curado) y provinciales.
 */

const BASE_1816 = "https://api.1816.com.ar";
// 12 h. Son ~25 curvas espaciadas a 1 req/seg (~28 s por cache miss), y el universo de
// instrumentos cambia muy poco: conviene pagar ese costo un par de veces por día nada más.
const CACHE_TTL = 43200;

// curvaId de 1816 -> grupo del monitor. (12 y 17 = USD Linked + Lelink caen ambos en usdlinked)
const CURVAS = {
  // --- Soberanos ---
  9:  "lecap",       // Soberanos ARS tasa fija (LECAPs/BONCAPs capitalizables)
  10: "tasafija",    // Soberanos ARS Botes
  7:  "cer",         // Soberanos ARS CER
  28: "tamar",       // Soberanos ARS Tamar
  14: "dual",        // Soberanos Duales
  12: "usdlinked",   // Soberanos USD Linked
  17: "usdlinked",   // Soberanos USD Linked Lelink
  8:  "usdbonares",  // Soberanos USD Bonares
  11: "usdglobales", // Soberanos USD Globales
  24: "usdbopreal",  // BCRA USD (Bopreales)
  // --- Provinciales ---
  18: "subsoberano",     // Provinciales USD
  20: "subsoberano",     // Provinciales USD Linked
  21: "subsoberano",     // Provinciales ARS Fijo
  19: "subsoberano",     // Provinciales ARS Inflación
  15: "subsoberano",     // Provinciales ARS Badlar
  27: "subsoberano",     // Provinciales ARS Tamar
  29: "subsoberano",     // Provinciales Duales
  // --- Corporativos (ONs) ---
  16: "onusd",           // Corporativos USD
  3:  "oncorp",          // Corporativos USD Linked
  25: "oncorp",          // Corporativos ARS Fijo
  5:  "oncorp",          // Corporativos ARS Inflación
  4:  "oncorp",          // Corporativos ARS Badlar
  26: "oncorp",          // Corporativos ARS Tamar
  23: "oncorp",          // Corporativos ARS TPM
  30: "oncorp",          // Corporativos ARS Caución
};

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

// Lista los instrumentos de una curva. -> array de items crudos de 1816.
async function fetchCurva(apiKey, curvaId) {
  const pedir = async () => {
    await throttle();
    const token = await getToken(apiKey);
    return fetch(`${BASE_1816}/v1/mercado/instrumentos?curvaId=${curvaId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  };
  let r = await pedir();
  if (r.status === 401) { _token = null; r = await pedir(); }   // token vencido
  if (r.status === 429) { await sleep(1200); r = await pedir(); } // rate limit
  if (!r.ok) throw new Error(`instrumentos 1816 (curva ${curvaId}) HTTP ` + r.status);
  return await r.json();
}

// Arma el universo soberano: { ticker, grupo, emision, venc }. Filtra variantes (@ / espacio).
async function computeUniverso(env) {
  const apiKey = env.API_1816_KEY;
  const vistos = new Map(); // ticker -> { ticker, grupo, emision, venc }
  for (const [curvaId, grupo] of Object.entries(CURVAS)) {
    // Son ~25 curvas espaciadas 1 req/seg: si una falla, no tiramos abajo el resto.
    // El detector es un extra; devolver un universo parcial es mejor que no devolver nada.
    let items = null;
    try {
      items = await fetchCurva(apiKey, curvaId);
    } catch (_e) {
      continue;
    }
    for (const it of items || []) {
      const ticker = String(it.ticker || "").trim();
      if (!ticker || ticker.includes("@") || ticker.includes(" ")) continue; // variantes/opciones
      if (vistos.has(ticker)) continue;
      vistos.set(ticker, {
        ticker,
        grupo,
        emision: it.fechaEmision || null,
        venc: it.fechaVencimiento || null,
        // Extra para el auto-add de ONs/subsoberanos líquidos: ISIN (para la ley local/NY),
        // moneda de denominación (filtro USD) y emisor (para el nombre a mostrar).
        isin: it.isinCode || null,
        monedaDenom: it.monedaDenom || null,
        emisor: it.emisorNombre || null,
      });
    }
  }
  return [...vistos.values()];
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

export async function onRequest(context) {
  const { request, env } = context;

  // Gate (defensa en profundidad), igual que /api/precios.
  const hasAccess = !!request.headers.get("Cf-Access-Jwt-Assertion");
  if (!hasAccess && env.ALLOW_NO_ACCESS !== "1") {
    return json({ error: "no autorizado (Cloudflare Access requerido)" }, 403);
  }
  if (request.method !== "GET") return json({ error: "método no soportado" }, 405);

  // Caché (mismo universo para todos dentro del TTL). ?fresh=1 lo saltea.
  const fresh = new URL(request.url).searchParams.get("fresh") === "1";
  const cache = caches.default;
  // v2: el universo ahora incluye isin/monedaDenom/emisor; el sufijo invalida el caché viejo.
  const cacheKey = new Request("https://cache.local/instrumentos-v2", { method: "GET" });
  if (!fresh) {
    const hit = await cache.match(cacheKey);
    if (hit) return hit;
  }

  let instrumentos;
  try {
    instrumentos = await computeUniverso(env);
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }

  const resp = json(
    { generado: new Date().toISOString(), instrumentos },
    200,
    { "cache-control": `public, max-age=${CACHE_TTL}` }
  );
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
