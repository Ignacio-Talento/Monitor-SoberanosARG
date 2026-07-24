/**
 * Cloudflare Pages Function — actividad del día por instrumento, vía 1816.
 *
 *   POST /api/actividad  body: [{ ticker, grupo }, ...]
 *   -> { "AL30": { vol: 1234567, ultimaOp: "2026-07-24T18:43:58.000Z" }, ... }
 *
 * Por instrumento devuelve:
 *  - vol:      volumen operado del día en NOMINALES, sumando los 3 segmentos de liquidación
 *              (MEP + pesos/24hs + cable/CCL) para los hard-dollar; sólo pesos para los ARS.
 *  - ultimaOp: fecha/hora (al segundo) de la última operación del día (la más reciente entre
 *              los 3 segmentos).
 * Se resuelve UNA fecha (la última rueda con datos) igual que /api/precios.
 *
 * - API key en env.API_1816_KEY (Secret). Gate Cloudflare Access (fail-closed).
 * - Caché 5 min: el volumen del día se acumula durante la rueda; no hace falta segundo a segundo.
 *   (Los precios siguen live en /api/precios; esto es una columna de contexto.)
 */

const BASE_1816 = "https://api.1816.com.ar";
const CACHE_TTL = 300;      // 5 min
const MAX_TICKERS = 50;

// Grupos hard-dollar: mismo nominal USD en los 3 segmentos, se suman. El resto (pesos y
// dólar-linked) sólo opera en el segmento de pesos.
const GRUPOS_USD = new Set(["usdbonares", "usdglobales", "usdbopreal", "subsoberano", "onusd", "onlocal", "onny"]);
const SEG_USD = ["mep", "ars", "ccl"];
const SEG_ARS = ["ars"];

const MAPA_BOPREAL = {
  BPA7D: "BPOA7", BPB7D: "BPOB7", BPC7D: "BPOC7", BPD7D: "BPOD7",
  BPA8D: "BPOA8", BPB8D: "BPOB8",
};
// Ticker de 1816 para un (grupo, ticker del monitor). null si no mapea.
function ticker1816(grupo, ticker) {
  if (grupo === "usdbonares" || grupo === "usdglobales") return ticker;
  if (grupo === "usdbopreal") return MAPA_BOPREAL[ticker] || null;
  if (grupo === "onusd" || grupo === "onlocal" || grupo === "onny")
    return ticker.endsWith("D") ? ticker.slice(0, -1) + "O" : null;
  return ticker; // pesos y subsoberanos: idéntico
}

let _lastReq = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function throttle() {
  const wait = 1100 - (Date.now() - _lastReq);
  if (wait > 0) await sleep(wait);
  _lastReq = Date.now();
}
let _token = null, _tokenExp = 0;
async function getToken(apiKey) {
  const now = Date.now() / 1000;
  if (_token && _tokenExp - now > 300) return _token;
  await throttle();
  const r = await fetch(`${BASE_1816}/v1/auth/token`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ apiKey, module: "mercado" }),
  });
  if (!r.ok) throw new Error("auth 1816 HTTP " + r.status);
  const d = await r.json();
  _token = d.token; _tokenExp = now + (d.expiresIn || 86400);
  return _token;
}

const CAMPOS = ["volumenNominalDiario", "ultimaOperacion"];
// Consulta un lote (<=50) en una moneda/fecha. -> { ticker1816: {volumenNominalDiario, ultimaOperacion} }
async function fetch1816(apiKey, tickers, moneda, fecha) {
  const out = {};
  for (let i = 0; i < tickers.length; i += MAX_TICKERS) {
    const lote = tickers.slice(i, i + MAX_TICKERS);
    const qs = new URLSearchParams();
    lote.forEach((t) => qs.append("tickers", t));
    CAMPOS.forEach((c) => qs.append("campos", c));
    qs.append("campos", "ticker");
    qs.append("moneda", moneda);
    if (fecha) qs.append("fechaOperacion", fecha);
    const pedir = async () => {
      await throttle();
      const token = await getToken(apiKey);
      return fetch(`${BASE_1816}/v1/mercado/indicadores?${qs.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
    };
    let r = await pedir();
    if (r.status === 401) { _token = null; r = await pedir(); }
    if (r.status === 429) { await sleep(1200); r = await pedir(); }
    if (!r.ok) throw new Error("indicadores 1816 HTTP " + r.status);
    const d = await r.json();
    const inst = d.instrumentos || {};       // objeto keyed por ticker, igual que /api/precios
    for (const t of lote) {
      if (inst[t]) out[t] = inst[t];
    }
  }
  return out;
}

// Última rueda con datos (walk-back), usando AL30 en mep como referencia.
async function resolverFecha(apiKey) {
  const hoy = new Date();
  for (let i = 0; i <= 7; i++) {
    const d = new Date(hoy.getTime() - i * 86400000);
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) continue;
    const fecha = i === 0 ? null : d.toISOString().slice(0, 10);
    const r = await fetch1816(apiKey, ["AL30"], "mep", fecha);
    const v = r["AL30"];
    if (v && (typeof v.volumenNominalDiario === "number" || v.ultimaOperacion)) {
      return fecha || d.toISOString().slice(0, 10);
    }
  }
  return null;
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  const hasAccess = !!request.headers.get("Cf-Access-Jwt-Assertion");
  if (!hasAccess && env.ALLOW_NO_ACCESS !== "1") return json({ error: "no autorizado" }, 403);
  if (request.method !== "POST") return json({ error: "método no soportado" }, 405);

  let items;
  try { items = await request.json(); } catch { return json({ error: "body inválido" }, 400); }
  if (!Array.isArray(items) || !items.length) return json({});

  // eco (ticker monitor) -> { t1816, segmentos }
  const info = {};
  const porSeg = { mep: new Set(), ars: new Set(), ccl: new Set() };
  const t2eco = {}; // ticker1816 -> [ecos]
  for (const it of items) {
    const eco = String(it.ticker || "").trim().toUpperCase();
    const grupo = String(it.grupo || "").trim();
    if (!eco || !grupo) continue;
    const t = ticker1816(grupo, eco);
    if (!t) continue;
    const segs = GRUPOS_USD.has(grupo) ? SEG_USD : SEG_ARS;
    info[eco] = { t: t.toUpperCase(), segs };
    for (const s of segs) porSeg[s].add(t.toUpperCase());
    (t2eco[t.toUpperCase()] ||= []).push(eco);
  }

  const apiKey = env.API_1816_KEY;
  if (!apiKey) return json({ error: "sin API key" }, 500);

  // Caché por set de tickers.
  const cache = caches.default;
  const clave = items.map((i) => i.ticker).sort().join(",");
  let h = 0; for (let i = 0; i < clave.length; i++) h = (Math.imul(h, 31) + clave.charCodeAt(i)) | 0;
  const cacheKey = new Request(`https://cache.local/actividad/${h >>> 0}-${items.length}`, { method: "GET" });
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let fecha;
  try { fecha = await resolverFecha(apiKey); }
  catch (e) { return json({ error: String((e && e.message) || e) }, 502); }

  // vol (suma segmentos) y ultimaOp (máx) por ticker1816.
  const acum = {}; // t1816 -> { vol, ultimaOp }
  try {
    for (const seg of SEG_USD) {
      const tks = [...porSeg[seg]];
      if (!tks.length) continue;
      const datos = await fetch1816(apiKey, tks, seg, fecha);
      for (const t of tks) {
        const f = datos[t]; if (!f) continue;
        const a = (acum[t] ||= { vol: 0, ultimaOp: null });
        if (typeof f.volumenNominalDiario === "number") a.vol += f.volumenNominalDiario;
        if (f.ultimaOperacion && (!a.ultimaOp || f.ultimaOperacion > a.ultimaOp)) a.ultimaOp = f.ultimaOperacion;
      }
    }
  } catch (e) { return json({ error: String((e && e.message) || e) }, 502); }

  const result = {};
  for (const [eco, { t }] of Object.entries(info)) {
    const a = acum[t];
    if (a && (a.vol > 0 || a.ultimaOp)) result[eco] = { vol: a.vol, ultimaOp: a.ultimaOp };
  }

  const resp = json(result, 200, { "cache-control": `public, max-age=${CACHE_TTL}` });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
