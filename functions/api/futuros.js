/**
 * Cloudflare Pages Function — futuros de dólar de Matba Rofex, vía Eco Valores.
 *
 *   GET /api/futuros?tickers=DLR/SEP26,DLR/OCT26
 *   -> { futuros: { "DLR/SEP26": { precio, horaDato, ultimaOperacion, volumenVN, ... } },
 *        fallos: [...], diag: {...} }
 *
 * POR QUÉ EXISTE, si ya había un worker que devolvía el precio.
 *
 *  - El worker devolvía SÓLO el último precio. La página de Eco trae además la hora del dato, la
 *    hora de la última operación, el volumen y las dos puntas. Sin la hora no hay forma de
 *    distinguir un precio de hace un minuto de uno de hace tres horas, que es justo el problema
 *    que ya nos mordió con las ONs en CCL; y sin el volumen no se sabe si el contrato opera.
 *  - Una sola llamada trae todos los contratos. El worker obligaba a una por ticker desde el
 *    navegador, y esa ráfaga contra el mismo host —el mismo que sirve BCRA y feriados— hacía que
 *    fallaran en bloque después de que cargarUniverso() lo saturara.
 *  - Con caché en el edge, muchos usuarios y muchas solapas se sirven de una sola consulta a Eco.
 *
 * OJO CON EL DATO: Eco publica información de BYMA **diferida 20 minutos**, y lo dice en su propia
 * página. Verificado el 27/08/2026: a las 15:45 el dato venía sellado 15:25:05. `horaDato` viaja en
 * la respuesta justamente para que el frontend pueda mostrarlo en vez de aparentar tiempo real.
 * Para precios en vivo habría que ir a la API de Primary (Matba Rofex), que pide credenciales.
 */

const ECO = "https://bonos.ecovalores.com.ar/eco/ticker.php";
// 60 s. Los contratos vienen con 20 minutos de atraso, así que cachear un minuto no agrega
// desactualización perceptible y corta de raíz la ráfaga contra Eco.
const CACHE_TTL = 60;
const MAX_TICKERS = 20;
// Eco responde en HTML y a veces devuelve la página sin la tabla cargada. Un reintento alcanza:
// medido, la tasa de fallo por consulta ronda el 40% y dos intentos la dejan por debajo del 20%.
const INTENTOS = 3;

const cabeceras = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
  "Accept": "text/html,application/xhtml+xml",
  "Accept-Language": "es-AR,es;q=0.9",
  "Referer": "https://bonos.ecovalores.com.ar",
};

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });

/** "1.538,00" -> 1538 ; "" o "&nbsp;" -> null */
function num(txt) {
  if (!txt) return null;
  const limpio = String(txt).replace(/&nbsp;/g, "").replace(/\./g, "").replace(",", ".").trim();
  if (!limpio || limpio === "-") return null;
  const v = parseFloat(limpio);
  return isNaN(v) ? null : v;
}

/**
 * Saca los datos de la página de un ticker.
 *
 * Se parsea por RÓTULO y no por posición: la tabla de Eco alterna rótulo y valor
 * ("Volumen V/N" seguido de "130.791"), y si algún día agregan una fila, las posiciones fijas se
 * corren en silencio y devuelven el número equivocado — que es peor que no devolver nada.
 */
function parsear(html) {
  const celdas = [...html.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)]
    .map((m) => m[1].replace(/<[^>]*>/g, "").replace(/&nbsp;/g, "").trim());

  const trasRotulo = (rotulo) => {
    const i = celdas.findIndex((c) => c === rotulo);
    return i >= 0 && i + 1 < celdas.length ? celdas[i + 1] : null;
  };

  // El precio y la hora del dato sí van por clase: son los únicos sin rótulo al lado.
  const precio = num((html.match(/class="precioticker"[^>]*>([^<]*)</) || [])[1]);
  const horaDato = ((html.match(/class="hora"[^>]*>([^<]*)</) || [])[1] || "").trim() || null;
  const variacion = num(((html.match(/class="varticker[^"]*"[^>]*>([^<]*)</) || [])[1] || "")
    .replace("%", ""));

  return {
    precio,
    variacion,
    horaDato,                                   // sello del dato (diferido 20')
    ultimaOperacion: trasRotulo("Últ. Hora"),   // cuándo operó de verdad
    compra: num(trasRotulo("Compra")),
    cantCompra: num(trasRotulo("Cant. compra")),
    venta: num(trasRotulo("Venta")),
    cantVenta: num(trasRotulo("Cant. venta")),
    apertura: num(trasRotulo("Apertura")),
    cierreAnterior: num(trasRotulo("Últ. Cierre")),
    minimo: num(trasRotulo("Mínimo")),
    maximo: num(trasRotulo("Máximo")),
    volumenVN: num(trasRotulo("Volumen V/N")),
    operaciones: num(trasRotulo("Operaciones")),
  };
}

async function unTicker(ticker) {
  let ultimo = null;
  for (let i = 0; i < INTENTOS; i++) {
    try {
      const r = await fetch(`${ECO}?t=${encodeURIComponent(ticker)}`, { headers: cabeceras });
      if (!r.ok) { ultimo = `HTTP ${r.status}`; continue; }
      const d = parsear(await r.text());
      if (d.precio > 0) return d;
      ultimo = "sin precio en la página";
    } catch (e) {
      ultimo = String((e && e.message) || e);
    }
  }
  return { error: ultimo };
}

export async function onRequest({ request }) {
  const url = new URL(request.url);
  const pedidos = (url.searchParams.get("tickers") || "")
    .split(",").map((t) => t.trim()).filter(Boolean);

  if (!pedidos.length) return json({ error: "falta el parámetro tickers" }, 400);
  if (pedidos.length > MAX_TICKERS) {
    return json({ error: `máximo ${MAX_TICKERS} tickers por pedido` }, 400);
  }

  const clave = new Request(
    `https://futuros.cache/${pedidos.slice().sort().join(",")}`, { method: "GET" });
  const cache = caches.default;
  const guardado = await cache.match(clave);
  if (guardado) return guardado;

  // En paralelo: son pocos y contra un sitio normal, no contra el worker compartido que se
  // saturaba. Cada uno reintenta por su cuenta.
  const res = await Promise.all(pedidos.map((t) => unTicker(t).then((d) => [t, d])));

  const futuros = {}, fallos = [];
  for (const [t, d] of res) {
    if (d.error) fallos.push(`${t}: ${d.error}`);
    else futuros[t] = d;
  }

  const salida = json(
    { futuros, fallos,
      diag: { pedidos: pedidos.length, resueltos: Object.keys(futuros).length,
              fuente: "Eco Valores (BYMA diferido 20 minutos)" } },
    200,
    { "cache-control": `public, max-age=${CACHE_TTL}` });

  // Sólo se cachea si salió algo: si fallaron todos, no vale la pena fijar el error un minuto.
  if (Object.keys(futuros).length) await cache.put(clave, salida.clone());
  return salida;
}
