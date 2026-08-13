/**
 * Cloudflare Pages Function — proxy al Índice Dólar BYMA (MEP y CCL).
 *
 *   GET /api/byma-dolar   -> { series: [{ date, mep, ccl }], desde, hasta, diag }
 *
 * BYMA publica dos índices de referencia, "Índice Dólar BYMA" (MEP) e "Índice CCL BYMA", armados
 * a partir de una canasta de instrumentos. Son mejores que derivar el dólar implícito de un solo
 * bono: no dependen de que ese bono haya operado ni de un print raro en un segmento fino.
 *
 * Especificación (Manual API Índice Dólar BYMA v2.0, 20/04/2024):
 *   token   POST https://apigw.byma.com.ar/oauth/token/
 *           grant_type=client_credentials, scope=indiceDolarBYMA.eod.read
 *   datos   GET  https://apigw.byma.com.ar/indice-dolar-byma/v1/eod.json/?date=
 *           sin `date` devuelve TODA la serie desde 2024-01-02 en una sola llamada.
 *           result: [{ bymaClosingPrice, cclClosingPrice, date }]
 *           204 NO CONTENT si la fecha pedida no tiene valores.
 *
 * REQUIERE CREDENCIALES. No hay acceso anónimo: verificado el 2026-08-13, tanto el gateway como
 * el endpoint del portal libre devuelven 401. Se piden a BYMA (apis@byma.com.ar) y se cargan como
 * Secrets del proyecto de Pages:
 *
 *     BYMA_CLIENT_ID       BYMA_CLIENT_SECRET
 *
 * Sin esos Secrets el endpoint responde 503 con `sinCredenciales: true`, que es la señal que usa
 * el frontend para seguir mostrando el spread derivado de los bonos en vez de romperse.
 */

const BYMA_GW  = "https://apigw.byma.com.ar";
const SCOPE    = "indiceDolarBYMA.eod.read";
// Es dato de cierre: cambia una vez por día. 6 h de caché deja el consumo en un puñado de
// llamadas diarias sin que la serie quede vieja dentro de la jornada.
const CACHE_TTL = 21600;

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

let _token = null, _tokenExp = 0;
async function getToken(id, secret) {
  const now = Date.now() / 1000;
  if (_token && _tokenExp - now > 60) return _token;
  const body = new URLSearchParams({
    grant_type: "client_credentials", client_id: id, client_secret: secret, scope: SCOPE,
  });
  const r = await fetch(`${BYMA_GW}/oauth/token/`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) throw new Error(`token BYMA HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const d = await r.json();
  if (!d.access_token) throw new Error("el token de BYMA vino sin access_token");
  _token = d.access_token;
  _tokenExp = now + (Number(d.expires_in) || 3600);
  return _token;
}

export async function onRequest(context) {
  const { request, env } = context;

  const hasAccess = !!request.headers.get("Cf-Access-Jwt-Assertion");
  if (!hasAccess && env.ALLOW_NO_ACCESS !== "1") {
    return json({ error: "no autorizado (Cloudflare Access requerido)" }, 403);
  }

  const id = env.BYMA_CLIENT_ID, secret = env.BYMA_CLIENT_SECRET;
  if (!id || !secret) {
    // No es un error del servidor: es que todavía no se cargaron las credenciales. Se distingue
    // con un flag propio para que el frontend pueda caer al cálculo por bonos sin tratarlo como
    // una falla ni mostrar un cartel rojo.
    return json({ sinCredenciales: true,
                  error: "faltan los Secrets BYMA_CLIENT_ID y BYMA_CLIENT_SECRET en Cloudflare Pages" }, 503);
  }

  const u = new URL(request.url);
  const fresh = u.searchParams.get("fresh") === "1";
  const clave = "https://byma-dolar.cache/eod";
  const cache = caches.default;
  if (!fresh) {
    const hit = await cache.match(new Request(clave));
    if (hit) return hit;
  }

  let d;
  try {
    const token = await getToken(id, secret);
    // Sin `date` viene la serie completa desde 2024-01-02 en una sola llamada.
    const r = await fetch(`${BYMA_GW}/indice-dolar-byma/v1/eod.json/?date=`, {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    });
    if (r.status === 204) return json({ series: [], diag: { aviso: "BYMA devolvió 204 sin contenido" } });
    if (!r.ok) return json({ error: `BYMA HTTP ${r.status}: ${(await r.text()).slice(0, 200)}` }, 502);
    d = await r.json();
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }

  // El manual aclara que un día puede tener uno solo de los dos índices (ej. 2024-01-15 vino con
  // MEP y sin CCL). Se conservan igual y se deja el faltante en null: el gráfico necesita LOS DOS
  // para el spread y los saltea, pero tirar la fila entera escondería que el dato existe a medias.
  const series = (d.result || [])
    .map((x) => ({
      date: String(x.date || "").slice(0, 10),
      mep: typeof x.bymaClosingPrice === "number" ? x.bymaClosingPrice : null,
      ccl: typeof x.cclClosingPrice === "number" ? x.cclClosingPrice : null,
    }))
    .filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x.date))
    .sort((a, b) => a.date.localeCompare(b.date));

  const completos = series.filter((x) => x.mep != null && x.ccl != null).length;
  const res = json({
    series,
    desde: series[0]?.date || null,
    hasta: series[series.length - 1]?.date || null,
    diag: { total: series.length, conAmbos: completos, soloUno: series.length - completos },
  });
  if (series.length) {
    const conTTL = new Response(res.clone().body, res);
    conTTL.headers.set("cache-control", `public, max-age=${CACHE_TTL}`);
    context.waitUntil(cache.put(new Request(clave), conTTL));
  }
  return res;
}
