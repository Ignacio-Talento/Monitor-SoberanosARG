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
// Campos extra para los bonos sin cronograma cargado: se les toman los indicadores ya
// calculados por 1816 en vez de computarlos localmente.
const CAMPOS_IND  = [CAMPO, "tea", "durationMod", "paridad"];
// Sólo la paridad: para los que sí tienen cronograma pero les faltan los cupones ya pagados,
// donde el frontend no puede computar los intereses corridos. Cuesta la mitad que CAMPOS_IND.
const CAMPOS_PAR  = [CAMPO, "paridad"];
// Segundos que dura el caché. Es EL dial de consumo de créditos.
// Costo de 1816 = tickers x campos (medido contra la API el 2026-08-05: 10 tickers x 1 campo =
// 10 créditos, 10 x 4 = 40; el comentario anterior decía "x (campos + 1)" y sobrestimaba ~2x).
// Un refresco del monitor son ~88 tickers x 1 campo + 1 de la resolución de rueda = ~89, y con
// las dos páginas ~180. Con TTL de 120 s, una jornada de 8 h da ~240 refrescos -> ~21k/día,
// consistente con lo observado (15.423 usados de 100.000 al 2026-08-05 19:00).
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
  // ONs de la solapa dedicada (ons.html). Misma moneda/mapeo D->O que onusd; se
  // separan por ley (local/NY) sólo para el render, no para pedir el precio.
  onlocal: "mep", onny: "mep",
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
  else if (grupo === "onusd" || grupo === "onlocal" || grupo === "onny")
    t = ticker.endsWith("D") ? ticker.slice(0, -1) + "O" : null;
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

// Pide a 1816 con reintentos. El límite es de 1 request/segundo y el limitador es GLOBAL por
// API key, no por cliente: alcanza con que haya otra pestaña abierta, otro isolate de Cloudflare
// (throttle() es estado de módulo, no se comparte) o el job diario para chocar y comer un 429.
// Verificado contra la API: dos llamadas a ~800 ms devuelven "Demasiadas solicitudes".
// Por eso se reintenta con espera creciente en vez de una sola vez.
const ESPERAS_REINTENTO = [0, 1500, 3500, 6000];
// Presupuesto de tiempo para TODA la request. Sin esto, cuatro grupos de moneda agotando el
// backoff serían ~80 s de espera y el navegador cortaría antes: peor que devolver lo que hay.
// Pasado el plazo se deja de reintentar y se responde con lo obtenido (Eco cubre el resto).
let _plazo = 0;
async function pedir1816(apiKey, url) {
  let ultima = null;
  for (const espera of ESPERAS_REINTENTO) {
    if (espera && Date.now() + espera > _plazo) break;   // no alcanza el tiempo: cortar acá
    if (espera) await sleep(espera);
    await throttle();
    let r;
    try {
      const token = await getToken(apiKey);
      r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    } catch (e) {
      ultima = { ok: false, status: 0, motivo: String((e && e.message) || e) };
      continue;                                  // error de red: reintentar
    }
    if (r.ok) return r;
    ultima = r;
    if (r.status === 401) { _token = null; continue; }        // token vencido
    if (r.status === 429 || r.status >= 500) continue;         // rate limit o caída: reintentar
    return r;                                                  // 4xx propio: reintentar no ayuda
  }
  return ultima;
}

// Consulta 1816 para una lista de tickers en una moneda.
//   -> { datos: { ticker: {campo: valor, ...} }, fallos: [ "..." ] }
// `fecha` (YYYY-MM-DD) opcional: si va, se pide esa rueda; si es null, la de hoy.
// `campos` permite pedir más que el precio: los instrumentos sin cronograma de flujos cargado
// usan los indicadores que ya calcula 1816 en vez de computarlos localmente.
//
// NUNCA tira: antes un solo 429 en un lote propagaba una excepción, la respuesta salía 502 y el
// frontend mandaba los ~90 tickers a Eco, incluidos los lotes que sí habían venido bien. Ahora se
// devuelve lo que se pudo y el fallback a Eco cubre únicamente los huecos reales.
async function fetch1816(apiKey, tickers, moneda, fecha, campos = [CAMPO]) {
  const out = {};
  const fallos = [];
  for (let i = 0; i < tickers.length; i += MAX_TICKERS) {
    const lote = tickers.slice(i, i + MAX_TICKERS);
    const qs = new URLSearchParams();
    lote.forEach((t) => qs.append("tickers", t));
    campos.forEach((c) => qs.append("campos", c));
    qs.append("moneda", moneda);
    if (fecha) qs.append("fechaOperacion", fecha);

    const r = await pedir1816(apiKey, `${BASE_1816}/v1/mercado/indicadores?` + qs);
    if (!r || !r.ok) {
      fallos.push(`${moneda} x${lote.length}: HTTP ${(r && r.status) || "?"}${r && r.motivo ? " " + r.motivo : ""}`);
      continue;
    }
    let d;
    try { d = await r.json(); } catch (e) { fallos.push(`${moneda} x${lote.length}: JSON inválido`); continue; }
    const inst = d.instrumentos || {};
    for (const t of lote) {
      if (inst[t]) out[t] = inst[t];
    }
  }
  return { datos: out, fallos };
}

// --- Última rueda con datos -------------------------------------------------
// 1816 NO tiene datos los fines de semana ni feriados: pedir "hoy" devuelve todo null.
// Buscamos hacia atrás la última fecha con datos usando UN ticker de referencia (barato).
// Los sábados/domingos se saltean por fecha, sin gastar llamadas.
const MS_ART = 3 * 3600 * 1000; // Argentina = UTC-3
function fechaART(offsetDias) {
  return new Date(Date.now() - MS_ART - offsetDias * 86400000);
}
// Memo de la rueda resuelta. Resolverla cuesta una llamada a 1816 en CADA request del monitor,
// y es justo la llamada que más chance tiene de comerse el 429 porque va primera y sin espacio
// previo. La rueda cambia como mucho una vez por día, así que 5 minutos de memo es de sobra.
let _fechaMemo = null, _fechaMemoExp = 0;
async function resolverFecha(apiKey, tickerRef, moneda) {
  if (_fechaMemo !== null && Date.now() < _fechaMemoExp) return _fechaMemo.v;
  for (let i = 0; i <= 7; i++) {
    const d = fechaART(i);
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) continue;             // fin de semana: ni consultamos
    const fecha = i === 0 ? null : d.toISOString().slice(0, 10);
    const { datos } = await fetch1816(apiKey, [tickerRef], moneda, fecha);
    // Ojo: 1816 devuelve el instrumento aunque no haya operado (con el campo en null),
    // así que hay que mirar el precio, no la presencia de la clave.
    if (datos[tickerRef] && typeof datos[tickerRef][CAMPO] === "number") {
      _fechaMemo = { v: fecha }; _fechaMemoExp = Date.now() + 300000;
      return fecha;
    }
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

  // Se agrupa por moneda y por qué campos necesita cada instrumento, porque 1816 cobra
  // tickers x campos (medido contra la API el 2026-08-05: 10 tickers x 1 campo = 10 créditos,
  // 10 x 4 = 40). Tres niveles:
  //   - normal: sólo el precio.
  //   - `par`:  precio + paridad. Son los que tienen cronograma pero sin los cupones ya pagados,
  //             así que el frontend no puede computar los intereses corridos (26 ONs y 3
  //             subsoberanos al 2026-08-07). Pedirles los 4 campos de `ind` costaría el doble
  //             para usar sólo la paridad.
  //   - `ind`:  precio + TIR + duration + paridad. Los que no tienen cronograma en absoluto.
  // Cada grupo es una llamada más, y cada llamada es otra chance de comerse un 429, así que no
  // conviene multiplicarlos: hoy son 2 en ONs y 3 en el Monitor.
  const porMoneda = {}; // clave -> [{ eco, t, grupo, moneda, ind, par }]
  for (const it of items) {
    const eco = String(it.ticker || "").trim().toUpperCase();
    const grupo = String(it.grupo || "").trim();
    if (!eco || !grupo) continue;
    const m = map1816(grupo, eco);
    if (!m) continue;
    const ind = it.ind === true;
    const par = !ind && it.par === true;
    const clave = (ind ? "ind:" : par ? "par:" : "") + m.moneda;
    (porMoneda[clave] ||= []).push({ eco, t: m.t, grupo, moneda: m.moneda, ind, par });
  }

  const result = {};
  const indicadores = {};   // solo los pedidos con `ind`: TIR/M.Dur/paridad que ya calcula 1816
  const monedas = Object.keys(porMoneda);
  // Rueda a la que corresponden los precios devueltos. El front la necesita: si hoy todavía no
  // operó (o es feriado), acá vuelve el cierre de la rueda anterior, y comparar eso contra el
  // último cierre guardado daría 0% de variación en todo el panel.
  let fechaRueda = null;
  const fallos = [];   // qué se rompió, para poder verlo en el frontend en vez de adivinar
  // Sin key el bloque de abajo no corre y TODO sale por Eco. Antes eso era mudo y se veía igual
  // que un día sin datos; hay que gritarlo, porque es un problema de configuración (falta el
  // Secret API_1816_KEY en el proyecto de Pages), no del mercado.
  if (!apiKey) fallos.push("falta el Secret API_1816_KEY en Cloudflare Pages: no se consultó 1816");
  if (apiKey && monedas.length) {
    // Una sola resolución de fecha para todas las monedas (fin de semana/feriado -> última rueda).
    const ref = porMoneda[monedas[0]][0];
    const fecha = await resolverFecha(apiKey, ref.t, ref.moneda);
    // resolverFecha devuelve null cuando la rueda es la de hoy.
    fechaRueda = fecha || fechaART(0).toISOString().slice(0, 10);
    for (const clave of monedas) {
      const pares = porMoneda[clave];
      const moneda = pares[0].moneda;
      // Deduplicar: los duales mandan 2 filas por ticker y gastarían cupo del lote de 50.
      const tickers = [...new Set(pares.map((p) => p.t))];
      const pideInd = !!(pares[0] && pares[0].ind);
      const pidePar = !!(pares[0] && pares[0].par);
      const campos = pideInd ? CAMPOS_IND : pidePar ? CAMPOS_PAR : [CAMPO];
      const res = await fetch1816(apiKey, tickers, moneda, fecha, campos);
      const datos = res.datos;
      fallos.push(...res.fallos);
      for (const p of pares) {
        const fila = datos[p.t];
        if (!fila) continue;
        if (typeof fila[CAMPO] === "number") result[p.eco] = fila[CAMPO];
        if (pideInd) {
          indicadores[p.eco] = {
            tea: fila.tea, durationMod: fila.durationMod, paridad: fila.paridad,
          };
        } else if (pidePar) {
          indicadores[p.eco] = { paridad: fila.paridad };
        }
      }
    }
  }

  // Fallback a Eco SOLO para lo que 1816 no resolvió. Acotado: cada uno es un subrequest
  // y Cloudflare corta en ~50 por request (si no, un día sin datos deja la respuesta a medias).
  const grupoDe = {};
  for (const it of items) grupoDe[String(it.ticker || "").trim().toUpperCase()] = String(it.grupo || "").trim();
  const SIN_ECO = new Set(["subsoberano", "onlocal", "onny"]);
  const pendientes = [...new Set(
    items.map((it) => String(it.ticker || "").trim().toUpperCase())
         // Subsoberanos y ONs de ley local/NY no están en Eco: pedirlos sólo gastaría subrequests.
         .filter((eco) => eco && !(eco in result) && !SIN_ECO.has(grupoDe[eco]))
  )].slice(0, MAX_ECO_FALLBACK);
  const de1816 = Object.keys(result).length;
  for (const eco of pendientes) {
    const p = await fallbackEco(grupoDe[eco], eco);
    if (p) result[eco] = p;
  }
  // `diag` deja ver por qué se cayó a Eco. Sin esto, un 429 se veía igual que un ticker que
  // simplemente no operó y no había manera de distinguirlos desde el frontend.
  return {
    precios: result, indicadores, fecha: fechaRueda,
    diag: { de1816, deEco: Object.keys(result).length - de1816, fallos },
  };
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

  _plazo = Date.now() + 20000;   // techo para los reintentos (ver pedir1816)

  let datos;   // { precios: {ticker: precio}, indicadores: {ticker: {...}} }
  try {
    datos = await computePrecios(env, items);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }

  // Sólo se cachea una respuesta sana. Si hubo fallos, guardarla dejaría clavada una tanda con
  // medio panel vacío durante todo el TTL y para todos los que entren, convirtiendo un 429 de un
  // segundo en dos minutos de precios faltantes.
  const sana = datos.diag && !datos.diag.fallos.length && datos.diag.de1816 > 0;
  const resp = json(datos, 200, {
    "cache-control": sana ? `public, max-age=${CACHE_TTL}` : "no-store",
  });
  if (sana) context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
