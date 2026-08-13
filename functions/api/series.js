/**
 * Cloudflare Pages Function — proxy a la API de 1816 para SERIES HISTÓRICAS.
 *
 *   POST /api/series   body JSON: { tickers: ["AL30","GD30"], moneda: "mep",
 *                                   desde: "2023-01-02", hasta: "2026-08-13" }
 *   -> { series: { "AL30": { "2023-01-02": 30.1, ... }, ... }, diag: {...} }
 *
 * Es hermano de /api/precios pero con otro perfil de uso: en vez de muchas consultas chicas y
 * frescas, es una consulta grande y vieja. Eso cambia dos cosas:
 *
 *  - El CACHÉ es largo (6 h). Una serie histórica no se mueve salvo en su último punto, así que
 *    refrescarla cada 2 minutos como los precios live sería tirar créditos.
 *  - Hay un TOPE DE COSTO explícito. 1816 cobra tickers x campos x días, o sea que un pedido
 *    descuidado —30 tickers por 10 años— son 75.000 créditos de los 100.000 diarios. El endpoint
 *    rechaza lo que se pase del tope en vez de ejecutarlo y avisar después.
 *
 * La key vive en env.API_1816_KEY (Secret del proyecto Pages) y nunca sale al browser. Gateado
 * por Cloudflare Access igual que el resto de /api/*.
 */

const BASE_1816 = "https://api.1816.com.ar";
const CAMPO_DEF = "precioDirty";
// Campos que 1816 acepta en /series (son los que tienen sentido como serie temporal, a diferencia
// de los de /indicadores). Se valida contra esta lista para no mandarle basura y comerse un 400.
const CAMPOS_OK = new Set([
  "currentYield", "duration", "durationMod", "paridad", "precioClean", "precioDirty",
  "spread", "tea", "tem", "tna", "valorTecnico", "volumenMontoDiario", "volumenNominalDiario",
]);
// 6 h. Lo único que cambia dentro del día es el último punto de la serie, y para eso está el
// Monitor: acá interesa la forma histórica, no el tick.
const CACHE_TTL = 21600;
const MAX_TICKERS_REQ = 50;    // límite de 1816 por request
const MAX_DIAS_VENTANA = 360;  // límite de 1816 por request de series
// Tope de costo por pedido, en créditos (= tickers x días con 1 campo). 20.000 es una quinta
// parte del presupuesto diario: alcanza para 10 tickers x 5 años y corta cualquier cosa mayor.
const TOPE_CREDITOS = 20000;
const MONEDAS_OK = new Set(["ars", "mep", "ccl"]);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 1816 limita a 1 request/segundo y el limitador es GLOBAL por API key.
let _ultima = 0;
async function throttle() {
  const falta = 1100 - (Date.now() - _ultima);
  if (falta > 0) await sleep(falta);
  _ultima = Date.now();
}

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

// Mismo criterio que /api/precios: reintentos con espera creciente ante 429/5xx/red, y un
// presupuesto de tiempo para toda la request para no quedar colgados más de lo que aguanta el
// browser. Nunca tira: devuelve lo que se pudo juntar y deja el motivo en `fallos`.
const ESPERAS_REINTENTO = [0, 1500, 3500, 6000];
let _plazo = 0;
async function pedir1816(apiKey, url) {
  let ultima = null;
  for (const espera of ESPERAS_REINTENTO) {
    if (espera && Date.now() + espera > _plazo) break;
    if (espera) await sleep(espera);
    await throttle();
    let r;
    try {
      const token = await getToken(apiKey);
      r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    } catch (e) {
      ultima = { ok: false, status: 0, motivo: String((e && e.message) || e) };
      continue;
    }
    if (r.ok) return r;
    ultima = r;
    if (r.status === 401) { _token = null; continue; }
    if (r.status === 429 || r.status >= 500) continue;
    return r;
  }
  return ultima;
}

const diaMs = 86400000;
const aFecha = (s) => new Date(s + "T00:00:00Z");
const aStr   = (d) => d.toISOString().slice(0, 10);

// Parte [desde, hasta] en ventanas de <= MAX_DIAS_VENTANA días, que es lo que acepta 1816.
function ventanas(desde, hasta) {
  const out = [];
  let ini = aFecha(desde);
  const fin = aFecha(hasta);
  while (ini <= fin) {
    const corte = new Date(Math.min(ini.getTime() + (MAX_DIAS_VENTANA - 1) * diaMs, fin.getTime()));
    out.push([aStr(ini), aStr(corte)]);
    ini = new Date(corte.getTime() + diaMs);
  }
  return out;
}

async function traerSeries(apiKey, tickers, moneda, desde, hasta, campo) {
  const series = {};   // ticker -> { fecha: valor }
  const fallos = [];
  for (const [ini, fin] of ventanas(desde, hasta)) {
    for (let i = 0; i < tickers.length; i += MAX_TICKERS_REQ) {
      const lote = tickers.slice(i, i + MAX_TICKERS_REQ);
      const qs = new URLSearchParams();
      lote.forEach((t) => qs.append("tickers", t));
      qs.append("campos", campo);
      qs.append("moneda", moneda);
      qs.append("fechaInicial", ini);
      qs.append("fechaFinal", fin);
      const r = await pedir1816(apiKey, `${BASE_1816}/v1/mercado/series?${qs}`);
      if (!r || !r.ok) {
        fallos.push(`${ini}..${fin} x${lote.length}: HTTP ${r ? r.status : "?"}${r && r.motivo ? " " + r.motivo : ""}`);
        continue;
      }
      let d;
      try { d = await r.json(); } catch (e) { fallos.push(`${ini}..${fin}: respuesta ilegible`); continue; }
      const inst = d.instrumentos || {};
      for (const tk of lote) {
        const campos = inst[tk];
        if (!campos) continue;
        // 1816 devuelve { campo: [[fecha, valor], ...] }. Se pivotea a { fecha: valor } y se
        // saltean los null: un día sin operar viene con el valor vacío, no ausente.
        const puntos = campos[campo] || [];
        for (const p of puntos) {
          if (!Array.isArray(p) || p.length < 2) continue;
          const [f, v] = p;
          if (typeof v !== "number" || !isFinite(v)) continue;
          (series[tk] ||= {})[String(f).slice(0, 10)] = v;
        }
      }
    }
  }
  return { series, fallos };
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
  if (request.method !== "POST") return json({ error: "usar POST" }, 405);

  let body;
  try { body = await request.json(); } catch { return json({ error: "body JSON inválido" }, 400); }

  const tickers = [...new Set((body.tickers || []).map((t) => String(t || "").trim().toUpperCase()).filter(Boolean))];
  const moneda  = String(body.moneda || "mep").toLowerCase();
  const desde   = String(body.desde || "").slice(0, 10);
  const campo   = String(body.campo || CAMPO_DEF);
  const hasta   = String(body.hasta || "").slice(0, 10);

  if (!tickers.length) return json({ error: "faltan tickers" }, 400);
  if (!MONEDAS_OK.has(moneda)) return json({ error: "moneda inválida" }, 400);
  if (!CAMPOS_OK.has(campo)) return json({ error: `campo inválido: ${campo}` }, 400);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(desde) || !/^\d{4}-\d{2}-\d{2}$/.test(hasta)) {
    return json({ error: "fechas inválidas (YYYY-MM-DD)" }, 400);
  }
  if (aFecha(desde) > aFecha(hasta)) return json({ error: "desde > hasta" }, 400);

  // Tope de costo ANTES de gastar. 1816 cobra tickers x campos x días y acá el campo es uno solo.
  const dias = Math.round((aFecha(hasta) - aFecha(desde)) / diaMs) + 1;
  const costo = tickers.length * dias;
  if (costo > TOPE_CREDITOS) {
    return json({ error: `el pedido costaría ~${costo} créditos y el tope es ${TOPE_CREDITOS}. ` +
                         `Achicá el rango o la cantidad de tickers.`, costo, tope: TOPE_CREDITOS }, 413);
  }

  const apiKey = env.API_1816_KEY;
  if (!apiKey) return json({ error: "falta el Secret API_1816_KEY en Cloudflare Pages" }, 500);

  const u = new URL(request.url);
  const fresh = u.searchParams.get("fresh") === "1";
  const clave = `https://series.cache/${campo}/${moneda}/${desde}/${hasta}/${tickers.slice().sort().join(",")}`;
  const cache = caches.default;
  if (!fresh) {
    const hit = await cache.match(new Request(clave));
    if (hit) return hit;
  }

  _plazo = Date.now() + 25000;   // presupuesto de tiempo para toda la request
  const { series, fallos } = await traerSeries(apiKey, tickers, moneda, desde, hasta, campo);
  const puntos = Object.values(series).reduce((a, s) => a + Object.keys(s).length, 0);

  const res = json({ series, diag: { campo, tickers: tickers.length, dias, costoEstimado: costo, puntos, fallos } });
  // Sólo se cachea si vino algo. Cachear un fallo lo congelaría 6 h, que acá duele mucho más que
  // en los precios: el usuario vería el gráfico vacío toda la tarde sin forma de forzarlo.
  if (puntos && !fallos.length) {
    const conTTL = new Response(res.clone().body, res);
    conTTL.headers.set("cache-control", `public, max-age=${CACHE_TTL}`);
    context.waitUntil(cache.put(new Request(clave), conTTL));
  }
  return res;
}
