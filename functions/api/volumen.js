/**
 * Cloudflare Pages Function — volumen nominal promedio de las últimas ruedas, vía 1816.
 *
 *   POST /api/volumen   body: { tickers: ["DNC7O", ...] }  (tickers en forma 1816, mercado MEP)
 *   -> { "DNC7O": 1234567, ... }   promedio de volumenNominalDiario de las últimas RUEDAS_PROM ruedas
 *
 * Lo usa el detector de bonos.html para el filtro de liquidez de ALTAS nuevas de ONs y
 * subsoberanos: sólo se sugiere sumar al monitor un título nuevo si operó, en promedio de las
 * últimas 3 ruedas, más de UMBRAL nominales. Se cuenta como 0 la rueda sin operar.
 *
 * - API key en env.API_1816_KEY (Secret). Gateado por Cloudflare Access (fail-closed).
 * - Caché corto (1 h): el volumen cambia por rueda, pero el detector se llama en cada carga.
 */

const BASE_1816 = "https://api.1816.com.ar";
const CAMPO = "volumenNominalDiario";
const RUEDAS_PROM = 5;      // promedio de las últimas N ruedas de mercado
const DIAS_VENTANA = 12;    // rango a pedir (calendario) para capturar >=3 ruedas con feriados
const MAX_TICKERS = 10;     // el endpoint de series topea antes que el de precios
const CACHE_TTL = 3600;

// --- rate limit: 1816 admite 1 request/segundo ---
let _lastReq = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function throttle() {
  const wait = 1100 - (Date.now() - _lastReq);
  if (wait > 0) await sleep(wait);
  _lastReq = Date.now();
}

// --- token 1816 cacheado en el isolate ---
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

const iso = (d) => d.toISOString().slice(0, 10);

// Trae la serie de volumenNominalDiario (mep) de un lote de tickers en la ventana.
// -> { ticker: [ [fecha, valor], ... ] }
async function fetchSerie(apiKey, tickers, fi, ff) {
  const pedir = async () => {
    await throttle();
    const token = await getToken(apiKey);
    const qs = new URLSearchParams();
    tickers.forEach((t) => qs.append("tickers", t));
    qs.append("campos", CAMPO);
    qs.append("moneda", "mep");
    qs.append("fechaInicial", fi);
    qs.append("fechaFinal", ff);
    return fetch(`${BASE_1816}/v1/mercado/series?${qs.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  };
  let r = await pedir();
  if (r.status === 401) { _token = null; r = await pedir(); }
  if (r.status === 429) { await sleep(1200); r = await pedir(); }
  if (!r.ok) throw new Error("series 1816 HTTP " + r.status);
  const d = await r.json();
  const out = {};
  const inst = d.instrumentos || {};
  for (const t of tickers) {
    const campos = inst[t];
    out[t] = (campos && campos[CAMPO]) ? campos[CAMPO] : [];
  }
  return out;
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

export async function onRequest(context) {
  const { request, env } = context;

  const hasAccess = !!request.headers.get("Cf-Access-Jwt-Assertion");
  if (!hasAccess && env.ALLOW_NO_ACCESS !== "1") {
    return json({ error: "no autorizado (Cloudflare Access requerido)" }, 403);
  }
  if (request.method !== "POST") return json({ error: "método no soportado" }, 405);

  let tickers;
  try {
    const body = await request.json();
    tickers = [...new Set((body.tickers || [])
      .map((t) => String(t || "").trim().toUpperCase()).filter(Boolean))];
  } catch { return json({ error: "body inválido" }, 400); }
  if (!tickers.length) return json({});

  const apiKey = env.API_1816_KEY;
  if (!apiKey) return json({ error: "sin API key" }, 500);

  const hoy = new Date();
  const ff = iso(hoy);
  const fi = iso(new Date(hoy.getTime() - DIAS_VENTANA * 86400000));

  // Serie por lotes (el endpoint de series topea en ~10 tickers).
  const series = {};
  try {
    for (let i = 0; i < tickers.length; i += MAX_TICKERS) {
      Object.assign(series, await fetchSerie(apiKey, tickers.slice(i, i + MAX_TICKERS), fi, ff));
    }
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }

  // Últimas RUEDAS_PROM ruedas de mercado = las fechas más recientes vistas en cualquier ticker.
  const fechasSet = new Set();
  for (const t of tickers) for (const p of series[t]) if (p && p[0]) fechasSet.add(p[0]);
  const ruedas = [...fechasSet].sort().slice(-RUEDAS_PROM);
  const n = ruedas.length || RUEDAS_PROM;

  const result = {};
  for (const t of tickers) {
    const porFecha = {};
    for (const p of series[t]) if (p && p[0]) porFecha[p[0]] = (typeof p[1] === "number" ? p[1] : 0);
    // suma de las ruedas objetivo (rueda sin operar = 0) / cantidad de ruedas
    let suma = 0;
    for (const f of ruedas) suma += porFecha[f] || 0;
    result[t] = suma / n;
  }

  return json(result, 200, { "cache-control": `public, max-age=${CACHE_TTL}` });
}
