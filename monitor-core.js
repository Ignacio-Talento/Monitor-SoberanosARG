/* ── NÚCLEO COMPARTIDO DEL MONITOR ────────────────────────────────────────────────────────────
 *
 * Las diez solapas son archivos HTML sueltos, sin build ni imports: cada una repite lo que
 * necesita. Este archivo empieza a revertir eso, y arranca por lo que más duele — pedir precios.
 *
 * ANTES. Sólo el Monitor y ONs consultaban /api/precios; las otras siete leían `bonos_data` de
 * localStorage, que escribe el Monitor al renderizar. Como localStorage no vence, una solapa
 * abierta días después mostraba precios viejos sin nada que lo delatara. Y las dos que tienen
 * cartel de estado decían "✓ N precios del Monitor" en verde, que informa cuántos hay y no de
 * cuándo son.
 *
 * PLAN. Se migra de a una solapa, no las diez de un saque: `pedirPrecios` es la primera capa y
 * alcanza para las que calculan sus propias tasas a partir del precio (Rotación Bopreal, Sendero
 * DL, Sendero TAMAR, Caución vs Lecap). Duales, Sendero CER y Glob vs Bon consumen métricas ya
 * calculadas —tea, md, frecCupon, itm— que dependen de los cronogramas de Instrumentos.xlsx y del
 * motor de calcMetricas; esas necesitan que se mude también el motor, que es el paso siguiente.
 *
 * Se expone en `window.MonitorCore` y no como módulo ES para que las páginas lo carguen con un
 * <script src> común y sigan siendo archivos abribles sin servidor.
 */
(function (global) {
  'use strict';

  var PROXY = '/api/precios';

  /* Pide precios a 1816 vía el proxy propio.
   *
   * items: [{ ticker, grupo, ind?, par? }]
   *   `ind` pide además tea/durationMod/paridad, para instrumentos sin cronograma cargado.
   *   `par` pide sólo paridad. Los dos encarecen la consulta —1816 cobra tickers x campos—, así
   *   que se mandan sólo donde hacen falta.
   *
   * -> { precios: {TICKER: number}, indicadores, fecha, diag }
   *    `fecha` es la rueda a la que corresponden. Importa: si hoy todavía no operó, 1816 devuelve
   *    el cierre anterior, y compararlo contra sí mismo daría 0% de variación en todo el panel.
   *
   * Tira si el proxy no responde. Cada página decide qué hacer con eso —las que ya venían leyendo
   * `bonos_data` pueden caer ahí y quedar como antes, nunca peor—.
   */
  function pedirPrecios(items, opciones) {
    var opts = opciones || {};
    var url = PROXY + (opts.fresh ? '?fresh=1' : '');
    return fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(items.map(function (i) {
        return { ticker: i.ticker, grupo: i.grupo, ind: !!i.ind, par: !!i.par };
      })),
    }).then(function (r) {
      if (!r.ok) throw new Error('proxy HTTP ' + r.status);
      return r.json();
    }).then(function (data) {
      if (data && data.error) throw new Error(data.error);
      // El proxy devuelve { precios, indicadores, fecha, diag }. Se tolera el formato plano
      // anterior por si quedó una respuesta vieja en caché.
      var mapa = (data && data.precios) || data || {};
      var precios = {};
      items.forEach(function (i) {
        var p = mapa[String(i.ticker).toUpperCase()];
        if (typeof p === 'number' && p > 0) precios[i.ticker] = p;
      });
      return {
        precios: precios,
        indicadores: (data && data.indicadores) || {},
        fecha: (data && data.fecha) || null,
        diag: (data && data.diag) || null,
      };
    });
  }

  /* Fecha de hoy en Argentina, 'YYYY-MM-DD'. El runner, el navegador y el mercado pueden estar en
   * husos distintos; acá siempre manda el calendario argentino. */
  function hoyART() {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Argentina/Buenos_Aires' })
      .format(new Date());
  }

  /* Sello de antigüedad de lo que dejó el Monitor en localStorage. Va en una clave aparte de
   * `bonos_data` para no romper a las páginas que leen ese array. */
  function sello() {
    try { return JSON.parse(localStorage.getItem('bonos_data_meta') || 'null'); }
    catch (e) { return null; }
  }
  function selloEsDeHoy() {
    var m = sello();
    if (!m || !m.ts) return false;
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Argentina/Buenos_Aires' })
      .format(new Date(m.ts)) === hoyART();
  }

  /* Precios que dejó el Monitor, para usar de respaldo cuando el proxy falla. */
  function preciosDelMonitor() {
    var out = {};
    try {
      (JSON.parse(localStorage.getItem('bonos_data') || '[]') || []).forEach(function (d) {
        if (d && d.ticker && d.precio != null) out[d.ticker] = d.precio;
      });
    } catch (e) {}
    return out;
  }

  global.MonitorCore = {
    pedirPrecios: pedirPrecios,
    preciosDelMonitor: preciosDelMonitor,
    sello: sello,
    selloEsDeHoy: selloEsDeHoy,
    hoyART: hoyART,
  };
})(window);
