/**
 * Cloudflare Pages Function — Índice Dólar BYMA (MEP) e Índice CCL BYMA, serie histórica.
 *
 *   GET /api/byma-dolar   -> { series: [{ date, mep, ccl }], desde, hasta, diag }
 *
 * BYMA publica dos índices de referencia armados con una canasta de instrumentos. Son mejores que
 * derivar el dólar implícito de un solo bono: no dependen de que ese bono haya operado ni de un
 * print raro en un segmento fino.
 *
 * DE DÓNDE SALEN. BYMA vende el acceso directo a esta serie: su API (apigw.byma.com.ar, OAuth2
 * client_credentials) y el widget del histórico devuelven 401 sin credenciales, y el índice
 * tampoco está entre los 16 que expone el BYMADATA libre —todos de acciones—.
 *
 * Pero la página pública del histórico
 *   https://www.byma.com.ar/productos/productos-de-datos/indice-dolar-byma-historico
 * embebe un widget WordPress que consulta esa API DEL LADO DEL SERVIDOR y expone el resultado por
 * su propio admin-ajax, sin autenticación. Es el mismo dato que BYMA muestra en su sitio, servido
 * por BYMA: eso es lo que se consume acá.
 *
 * Dos cosas que hay que respetar para que responda:
 *  - Los headers Sec-Fetch-*. Sin ellos el host contesta 401 aunque la URL sea correcta (probado
 *    el 2026-08-13: mismo request, con y sin esos headers, 200 contra 401).
 *  - El Referer del propio widget.
 * Por eso se mandan explícitos y no conviene "limpiarlos" en una futura pasada de prolijidad.
 *
 * Una sola llamada trae las DOS series completas desde 2024-01-03 (~633 ruedas), así que no hay
 * paginado ni ventanas que armar.
 */

const AJAX = "https://data-widgets.byma.com.ar/wp-admin/admin-ajax.php?action=get_indice_dolar";
const REFERER = "https://data-widgets.byma.com.ar/indice-dolar-historico-widget/";
// Es dato de cierre: cambia una vez por día. 6 h deja el consumo en unas pocas llamadas diarias
// sin que la serie quede vieja dentro de la jornada, y es un endpoint ajeno: conviene no golpearlo.
const CACHE_TTL = 21600;

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Accept": "*/*",
  "Accept-Language": "es-AR,es;q=0.9",
  "X-Requested-With": "XMLHttpRequest",
  "Sec-Fetch-Dest": "empty",
  "Sec-Fetch-Mode": "cors",
  "Sec-Fetch-Site": "same-origin",
  "Referer": REFERER,
};

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

  const u = new URL(request.url);
  const fresh = u.searchParams.get("fresh") === "1";
  const clave = "https://byma-dolar.cache/eod-v2";
  const cache = caches.default;
  if (!fresh) {
    const hit = await cache.match(new Request(clave));
    if (hit) return hit;
  }

  let d;
  try {
    const r = await fetch(AJAX, { headers: HEADERS });
    if (!r.ok) return json({ error: `BYMA HTTP ${r.status}` }, 502);
    d = await r.json();
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }

  // La respuesta trae apertura, mínimo, máximo y cierre de los dos índices. Acá sólo interesa el
  // cierre. Un día puede venir con uno solo de los dos (19 de las 633 ruedas al 2026-08-13): se
  // conserva la fila con el faltante en null en vez de descartarla, porque el dato existe a medias
  // y esconderlo haría parecer que ese día no hubo rueda.
  const series = (d.result || [])
    .map((x) => ({
      date: String(x.date || "").slice(0, 10),
      mep: typeof x.bymaClosingPrice === "number" ? x.bymaClosingPrice : null,
      ccl: typeof x.cclClosingPrice === "number" ? x.cclClosingPrice : null,
    }))
    .filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x.date))
    .sort((a, b) => a.date.localeCompare(b.date));

  const conAmbos = series.filter((x) => x.mep != null && x.ccl != null).length;
  const res = json({
    series,
    desde: series[0]?.date || null,
    hasta: series[series.length - 1]?.date || null,
    diag: { total: series.length, conAmbos, soloUno: series.length - conAmbos },
  });
  if (series.length) {
    const conTTL = new Response(res.clone().body, res);
    conTTL.headers.set("cache-control", `public, max-age=${CACHE_TTL}`);
    context.waitUntil(cache.put(new Request(clave), conTTL));
  }
  return res;
}
