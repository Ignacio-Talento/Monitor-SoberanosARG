/**
 * Cloudflare Pages Function — tasa de caución, vía el futuro CAUC de A3 Mercados.
 *
 *   GET /api/caucion
 *   -> { rueda, referencia: { symbol, tasa, volumen, operaciones, diasAlVenc, ... },
 *        contratos: [...], diag: {...} }
 *
 * POR QUÉ ESTA FUENTE. La caución bursátil no tiene fuente pública directa: el BCRA no la publica
 * —cero coincidencias con "cauc" en sus 1.610 series—, 1816 no la tiene en el plan contratado, la
 * API de BYMA Data la tiene pero exige OAuth, y Rava y Bolsar responden 404. MAE, que parecía la
 * otra opción, es el mismo lugar: su producto se llama "Cauciones A3" y su sitio remite a
 * a3mercados.com.ar, porque A3 es la fusión de Matba Rofex con MAE.
 *
 * Lo que sí es público es el FUTURO de tasa de caución de A3, en la misma API que ya usa
 * /api/futuros para el dólar y sin credenciales.
 *
 * ES UN FUTURO Y NO LA TASA SPOT. El contrato liquida contra el promedio de la caución del período,
 * así que a principio de mes cotiza una expectativa a 30 días y sólo cerca del vencimiento converge
 * a la tasa de hoy. Por eso viaja `diasAlVenc`: con cuatro días es prácticamente spot, con treinta
 * no, y quien lo consuma tiene que poder decirlo.
 *
 * SE EXIGE QUE HAYA OPERADO. La referencia es el contrato vivo más cercano CON OPERACIONES, no el
 * más cercano a secas: los contratos largos publican ajuste teórico todas las ruedas sin que nadie
 * los toque, y ese número no es un precio de mercado. El 27/08/2026, de los tres contratos vivos
 * sólo el de agosto tenía operaciones (29), y los otros dos figuraban con volumen cero.
 */
const CEM = "https://apicem.matbarofex.com.ar/api/v2";
const CABECERAS = { "User-Agent": "Mozilla/5.0", Accept: "application/json" };
const DIAS_ATRAS = 10;          // margen para fines de semana y feriados largos
const CACHE_TTL = 900;          // 15 min: el ajuste cambia una vez por rueda

const MESES = { ENE: 1, FEB: 2, MAR: 3, ABR: 4, MAY: 5, JUN: 6,
                JUL: 7, AGO: 8, SEP: 9, OCT: 10, NOV: 11, DIC: 12 };

// CAUC/AGO26 -> 2026-08-31. El contrato vence el último día del mes que nombra.
function vencDe(symbol) {
  const m = /^CAUC\/([A-Z]{3})(\d{2})$/.exec(symbol || "");
  if (!m) return null;
  const mes = MESES[m[1]];
  if (!mes) return null;
  return new Date(Date.UTC(2000 + Number(m[2]), mes, 0));   // día 0 del mes siguiente
}

const dia = (d) => d.toISOString().slice(0, 10);

function json(obj, status = 200, ttl = 0) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": ttl ? `public, max-age=${ttl}` : "no-store",
    },
  });
}

export async function onRequestGet({ request }) {
  const cache = caches.default;
  const clave = new Request(new URL(request.url).origin + "/api/caucion", { method: "GET" });
  const guardado = await cache.match(clave);
  if (guardado) return guardado;

  const ahora = new Date();
  const desde = new Date(ahora.getTime() - DIAS_ATRAS * 86400000);

  let datos;
  try {
    const url = new URL(CEM + "/closing-prices");
    url.searchParams.set("product", "Tasa de Caución");
    url.searchParams.set("type", "FUT");
    url.searchParams.set("from", dia(desde));
    url.searchParams.set("to", dia(ahora));
    url.searchParams.set("pageSize", "400");
    url.searchParams.set("sort", "dateTime");
    url.searchParams.set("sortDir", "DESC");
    const r = await fetch(url, { headers: CABECERAS, signal: AbortSignal.timeout(15000) });
    if (!r.ok) {
      let detalle = "";
      try { detalle = (await r.text()).slice(0, 200); } catch (e) { /* sin cuerpo */ }
      return json({ error: `A3 HTTP ${r.status}${detalle ? " · " + detalle : ""}` }, 502);
    }
    datos = await r.json();
  } catch (e) {
    return json({ error: `A3 no respondió: ${String((e && e.message) || e)}` }, 502);
  }

  // El filtro por `product` del endpoint no siempre se aplica, así que se filtra de nuevo por el
  // prefijo del símbolo: sin esto entran los futuros de dólar, soja y todo el resto del mercado.
  const filas = (datos.data || []).filter((x) => String(x.symbol || "").startsWith("CAUC"));
  if (!filas.length) return json({ error: "A3 no devolvió contratos CAUC" }, 502);

  const rueda = filas.map((x) => String(x.dateTime).slice(0, 10)).sort().pop();
  const refFecha = new Date(rueda + "T00:00:00Z");

  const contratos = filas
    .filter((x) => String(x.dateTime).slice(0, 10) === rueda)
    .map((x) => {
      const v = vencDe(x.symbol);
      return {
        symbol: x.symbol,
        tasa: Number(x.settlement) || null,
        volumen: Number(x.volume) || 0,
        operaciones: Number(x.tradeCount) || 0,
        openInterest: Number(x.openInterest) || null,
        vencimiento: v ? dia(v) : null,
        diasAlVenc: v ? Math.round((v - refFecha) / 86400000) : null,
      };
    })
    .sort((a, b) => (a.diasAlVenc ?? 9999) - (b.diasAlVenc ?? 9999));

  const referencia = contratos.find((c) => c.operaciones > 0 && c.tasa) || null;

  const salida = {
    rueda,
    referencia,
    contratos,
    diag: {
      fuente: "A3 Mercados · CEM, futuro de tasa de caución (CAUC)",
      esFuturo: true,
      // Quien consuma esto tiene que poder decir cuánto se parece a la tasa spot, y eso depende
      // enteramente de cuánto le queda al contrato.
      nota: referencia
        ? `Contrato ${referencia.symbol}, a ${referencia.diasAlVenc} días del vencimiento. ` +
          `Liquida contra el promedio de la caución del período, así que converge a la spot ` +
          `a medida que vence.`
        : "Ningún contrato CAUC operó en la última rueda publicada.",
    },
  };

  const resp = json(salida, 200, CACHE_TTL);
  await cache.put(clave, resp.clone());
  return resp;
}
