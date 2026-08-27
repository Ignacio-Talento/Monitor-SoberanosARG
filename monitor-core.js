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


  /* Deja todo listo para calcular: feriados, el universo de Instrumentos.xlsx y las series del
   * BCRA (CER, TAMAR, dólar). Después de esto se puede llamar a calcMetricas().
   *
   * El orden importa: los feriados van primero porque los días hábiles dependen de ellos, y
   * fetchBCRA necesita `instrumentos` cargado para saber qué series pedir.
   *
   * Es la parte que cada solapa venía evitando heredando `bonos_data` del Monitor. Ahora puede
   * hacerla por su cuenta y calcular con precios frescos.
   */
  async function cargarUniverso() {
    await cargarFeriados();
    const r = await fetch('Instrumentos.xlsx?t=' + Date.now());
    if (!r.ok) throw new Error('Instrumentos.xlsx HTTP ' + r.status);
    parsearExcel(await r.arrayBuffer());
    await fetchBCRA();
    return instrumentos;
  }


  /* El universo con sus métricas calculadas: exactamente la misma forma que el Monitor venía
   * dejando en `bonos_data`. Así las solapas que lo consumían pueden pasar a calcular por su
   * cuenta sin tocar una línea de su código de abajo.
   *
   * Incluye `itm`, que era lo único que no se podía recalcular afuera. La pata in the money de un
   * dual se identifica porque 1816 publica indicadores SÓLO para la que manda: se compara la TEA
   * propia contra la de 1816 y gana la que coincide. La tolerancia de 1 pp es la red de seguridad
   * por si el indicador viene de otra rueda; verificado en los 7 duales, la elegida difiere en
   * menos de 0,08 pp y la otra en decenas de puntos, así que no es ambiguo.
   */
  async function datosCalculados() {
    await cargarUniverso();
    const vivos = instrumentos.filter(i => { const d = diasAlVenc(i.venc); return d === null || d > 0; });
    const r = await pedirPrecios(vivos.map(i => ({
      ticker: i.ticker, grupo: i.grupo,
      ind: usaIndicadores1816(i) || i.grupo === 'dual',
      par: usaParidad1816(i),
    })));
    Object.assign(precios, r.precios);
    indicadores1816 = r.indicadores || {};

    const ITM_TOL_PP = 1.0;
    const itmPorTicker = {};
    for (const i of vivos) {
      if (i.grupo !== 'dual') continue;
      const m = calcMetricas(i);
      if (m.tea == null) continue;
      const tea1816 = (indicadores1816[String(i.ticker).toUpperCase()] || {}).tea;
      if (typeof tea1816 !== 'number' || !isFinite(tea1816)) continue;
      const d = Math.abs(m.tea - tea1816 * 100);
      if (d > ITM_TOL_PP) continue;
      const prev = itmPorTicker[i.ticker];
      if (!prev || d < prev.d) itmPorTicker[i.ticker] = { d, pata: i.pata && i.pata.tipo };
    }

    return instrumentos.map(i => {
      const m = calcMetricas(i);
      return {
        ticker: i.ticker, grupo: i.grupo, nombre: i.nombre, venc: i.venc,
        md: m.md, tea: m.tea, precio: m.precio,
        pataTipo: (i.pata && i.pata.tipo) || null,
        frecCupon: frecuenciaCupon(i) || 1, plazo: m.plazo != null ? m.plazo : null,
        itm: i.grupo === 'dual'
          && (itmPorTicker[i.ticker] || {}).pata === (i.pata && i.pata.tipo),
      };
    }).filter(d => d.md !== null && d.tea !== null);
  }

  /* Contratos de futuro de dólar de Matba Rofex, vía el proxy de Eco.
   *
   * CON REINTENTOS, y no es un lujo: la fuente devuelve `price: null` de forma intermitente para
   * contratos que sí cotizan. Medido el 27/08/2026, DLR/DIC26 falló en 2 de cada 5 llamadas y
   * DLR/MAR27 dos veces seguidas, y los tres que faltaban aparecieron al insistir. Con un solo
   * intento por contrato —como se hacía— la curva queda con huecos distintos cada vez, y quien
   * la mira no tiene forma de distinguir "este mes no cotiza" de "esta vez no contestó".
   *
   * Los contratos vencen SIEMPRE el último día del mes, así que el vencimiento se deriva del
   * propio ticker en vez de hardcodearse.
   *
   * -> { 'DLR/SEP26': { precio, venc: Date, dias }, ... }  sólo los vivos y con precio.
   */
  var MESES_FUT = { ENE:0, FEB:1, MAR:2, ABR:3, MAY:4, JUN:5,
                    JUL:6, AGO:7, SEP:8, OCT:9, NOV:10, DIC:11 };

  function vencContrato(ticker) {
    var m = MESES_FUT[ticker.slice(4, 7)];
    var a = 2000 + parseInt(ticker.slice(7, 9), 10);
    if (m === undefined || isNaN(a)) return null;
    return new Date(a, m + 1, 0);          // día 0 del mes siguiente = último del mes
  }

  function futurosDolar(tickers, intentos) {
    var max = intentos || 4;
    var hoy = new Date(); hoy.setHours(0, 0, 0, 0);
    var out = {}, fallidos = [];

    function unContrato(tk, queda) {
      var venc = vencContrato(tk);
      if (!venc || venc <= hoy) return Promise.resolve();     // vencido o ticker ilegible
      return fetch(ECO_URL + '/?ticker=' + encodeURIComponent(tk))
        .then(function (r) { return r.json(); })
        .catch(function () { return {}; })
        .then(function (d) {
          if (d && d.price > 0) {
            out[tk] = { precio: d.price, venc: venc,
                        dias: Math.round((venc - hoy) / 86400000) };
            return;
          }
          if (queda > 1) {
            return new Promise(function (res) { setTimeout(res, 600); })
              .then(function () { return unContrato(tk, queda - 1); });
          }
          fallidos.push(tk);
        });
    }

    // En serie y no en paralelo: es el mismo worker para todos y dispararlos juntos agrava
    // justamente la intermitencia que se está tratando de cubrir.
    return tickers.reduce(function (p, tk) {
      return p.then(function () { return unContrato(tk, max); })
              .then(function () { return new Promise(function (r) { setTimeout(r, 150); }); });
    }, Promise.resolve()).then(function () {
      return { futuros: out, fallidos: fallidos };
    });
  }

  global.MonitorCore = {
    futurosDolar: futurosDolar,
    vencContrato: vencContrato,
    pedirPrecios: pedirPrecios,
    cargarUniverso: cargarUniverso,
    datosCalculados: datosCalculados,
    preciosDelMonitor: preciosDelMonitor,
    sello: sello,
    selloEsDeHoy: selloEsDeHoy,
    hoyART: hoyART,
  };
})(window);

// URLs de los proxies que consume el motor. Viven acá y no en cada página: fetchBCRA y
// cargarFeriados se mudaron y las necesitan. Al estar en el scope global, las páginas que
// ya las usaban las siguen viendo igual.
const ECO_URL      = 'https://ecovalores-proxy.granda-fra.workers.dev';
const BCRA_WORKER  = 'https://indicadoresbcra.granda-fra.workers.dev';
const ARG_WORKER   = 'https://argentinadatos-proxy.granda-fra.workers.dev';
const FERIADOS_WORKER = 'https://feriados-proxy.granda-fra.workers.dev';

// Constantes y helpers que el motor usa y que habían quedado en la página. Se detectaron
// buscando qué identificadores referencia este archivo sin declararlos: ir de a uno por
// cada error en consola habría llevado varias vueltas de deploy.
// ── GRUPOS (orden de aparición en tabla) ──────────────────────
const GRUPOS = [
  { key: 'lecap',      label: 'LECAPS'       },
  { key: 'tasafija',   label: 'TASA FIJA'    },
  { key: 'cer',        label: 'CER'          },
  { key: 'tamar',      label: 'TAMAR'        },
  { key: 'usdlinked',  label: 'USD LINKED'   },
  { key: 'usdbonares', label: 'USD BONARES'  },
  { key: 'usdglobales', label: 'USD GLOBALES'  },
  { key: 'usdbopreal',  label: 'USD BOPREALES' },
  { key: 'onusd',       label: 'ON USD'         },
  { key: 'subsoberano', label: 'SUBSOBERANOS'   },
  { key: 'dual',        label: 'DUALES'        },
];
// Grupos que están cargados en Instrumentos.xlsx pero NO se muestran en el Monitor.
// Se filtran acá en vez de borrarlos del Excel: reconstruir el cronograma de flujos de un
// bono con cupón es difícil (1816 no expone flujos), así que conviene conservar el dato.
// Para volver a mostrarlos, sacarlos de esta lista.
const GRUPOS_OCULTOS = ['onusd'];
async function fetchCerFecha(fechaStr) {
  if (!fechaStr) return null;
  if (bcraData.cer[fechaStr] !== undefined) return bcraData.cer[fechaStr];
  // Pedir rango de ±5 días para cubrir fines de semana/feriados
  const d = parseLocalDate(fechaStr);
  const desde = dateToStr(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 5));
  const hasta  = dateToStr(new Date(d.getFullYear(), d.getMonth(), d.getDate() + 5));
  try {
    const resp = await fetch(`${BCRA_WORKER}/?serie=cer&desde=${desde}&hasta=${hasta}`);
    const json = await resp.json();
    const detalle = json.results?.[0]?.detalle || [];
    detalle.forEach(r => {
      if (r.fecha && r.valor) bcraData.cer[r.fecha] = parseFloat(r.valor);
    });
    return buscarCerCache(fechaStr);
  } catch(e) {
    console.warn('BCRA CER fetch error:', e);
    return null;
  }
}

/* ── MOTOR DE CÁLCULO ─────────────────────────────────────────────────────────────────
 * Mudado desde bonos.html, no copiado: hay una sola definición de cada cosa y el Monitor
 * también las consume desde acá. Todo vive en el scope global —estas páginas no tienen
 * módulos ni build—, así que cargar este archivo antes deja los mismos nombres visibles
 * para quien ya los usaba.
 *
 * El estado va primero porque const/let tienen zona muerta temporal; las funciones se
 * hoistean y su orden entre sí da igual.
 */

// ── HELPERS CER/TAMAR ────────────────────────────────────────
// Restar N días hábiles desde una fecha dada
// Set de feriados (fechas 'YYYY-MM-DD') traído de ArgentinaDatos vía worker.
let feriados = new Set();
let bcraData = { cer: {}, tamar: {}, tamarUltPub: {}, usd: {}, usdHoy: null, tamarReciente: null, caucion1d: null, uva: null, plazo30d: null, riesgopais: null };
let precios = {};   // lo llena cada página; el Monitor, desde sessionStorage
// ── ESTADO ────────────────────────────────────────────────────
let instrumentos = []; // array plano ordenado: { grupo, ...datos }
// Indicadores que calcula 1816 (tea/durationMod/paridad), sólo para los grupos de los que
// NO tenemos cronograma de flujos y por eso no se computan localmente (hoy: subsoberanos).
let indicadores1816 = {};
// Días de TAMAR que se promedian para proyectar el tramo futuro del cupón (TAMAR y duales).
// Son los mismos 5 que usa 1816 (su request manda diasPromedioTasaReferenciaProyectada: 5).
// Pasó de 5 a 10 el 2026-08-12. La nota anterior decía que 10 empeoraba, pero medía el PROMEDIO
// de los 6 TAMAR, y ese promedio estaba dominado por M31G6 (-2,97), que arrastraba un problema
// distinto —los días hábiles sin publicación que no se rellenaban, ver el fetch de TAMAR—. Medido
// por bono contra 1816 en la rueda del 2026-08-12, los 5 con tramo futuro relevante dan:
//
//        N=5   +0,13 a +0,22 pp        N=8   +0,03 a +0,13        N=10  -0,03 a -0,12
//
// El motivo de fondo para agrandar la ventana no es ese ajuste, que es de un día y podría dar
// distinto mañana: es que proyectar un promedio de 1 a 2 años con 5 observaciones diarias de una
// serie ruidosa es pobre. La TAMAR se movió 22,4-23,9 en las últimas 10 ruedas, y el promedio de
// 5 días salta 0,089 pp por día contra 0,057 con 10 (medido sobre las últimas 60 ruedas). O sea
// que con 5 la TEA publicada tiembla por ruido de la serie, no porque cambie el bono.
//
// El residuo que queda (<0,13 pp) no se va con ningún N: entre el 07-08 y el 12-08 el signo del
// desvío se dio vuelta, así que 1816 no está usando un promedio móvil como el nuestro. Perseguirlo
// con esta constante es ajustar ruido.
const TAMAR_DIAS_PROY = 10;
// Grupos que se calculan con parámetros (TEM, CER, TAMAR, TC): no necesitan cronograma.
const GRUPOS_PARAMETRICOS = ['lecap', 'cer', 'tamar', 'usdlinked', 'dual'];

function buscarCerCache(fechaStr) {
  const fechas = Object.keys(bcraData.cer).filter(f => f <= fechaStr).sort();
  if (!fechas.length) return null;
  return bcraData.cer[fechas[fechas.length - 1]];
}

function calcMD(flujos, precio, tir_tea_dec, m = 1) {
  if (!flujos?.length || !precio || tir_tea_dec === null) return null;
  let num = 0, den = 0;
  for (const f of flujos) {
    const d = f.monto / Math.pow(1 + tir_tea_dec, f.t);
    num += f.t * d; den += d;
  }
  if (!den) return null;
  return (num / den) / Math.pow(1 + tir_tea_dec, 1 / (m || 1));
}

function calcMetricas(inst, precioAlt) {
  // precioAlt permite recalcular todo a un precio distinto del de mercado (ver comisión).
  const precio = (precioAlt !== undefined && precioAlt !== null) ? precioAlt : (precios[inst.ticker] || null);
  const dias   = diasAlVenc(inst.venc);
  if (usaIndicadores1816(inst)) return metricasDe1816(inst, precio, dias);
  const hoy    = new Date(); hoy.setHours(0,0,0,0);
  const liq    = sumarDiasHabiles(hoy, 1); // settlement T+1 hábil (precios de Eco Valores son T+1)

  if (inst.grupo === 'lecap') {
    const vpv = inst.vpv;
    if (!precio || !vpv || !dias) return { precio, dias, tna: null, tea: null, md: null, paridad: null };
    // El precio de Eco Valores es T+1: se descuenta desde la fecha de liquidación
    // (hoy + 1 día hábil) hasta el vencimiento, en días calendario reales.
    const diasTir = Math.round((parseLocalDate(inst.venc) - liq) / 86400000);
    if (diasTir <= 0) return { precio, dias, tna: null, tea: null, md: null, paridad: null };
    const tea_dec = Math.pow(vpv / precio, 365 / diasTir) - 1;
    const tea = tea_dec * 100;
    const tna = (Math.pow(1 + tea_dec, 1/365) - 1) * 365 * 100;
    // M.Dur en la convención de 1816, que es la de mercado para LECAPs: la sensibilidad se mide
    // contra la tasa SIMPLE, no la efectiva. Con P = VPV/(1 + s·t) sale -(1/P)·dP/ds = t·P/VPV.
    // Verificado el 2026-08-12 contra el campo durationMod de 1816 en las 12 LECAPs vivas: ajusta
    // con error 0 a cinco decimales. La fórmula anterior, t/(1+TEA), mide contra la tasa efectiva
    // y daba sistemáticamente por debajo (S31G6 0,039 contra 0,049; S16O6 0,138 contra 0,168).
    const plazo = diasTir / 365;
    const md  = plazo * (precio / vpv);
    // `plazo` viaja a bonos_data: los gráficos que ponen LECAPs y CER sobre el mismo eje necesitan
    // rearmar la duration en una convención común, y desde md ya no se puede despejar (ver senderoCER).
    // Paridad: no aplica a LECAPs (capitalizan al vencimiento, sin cupones). Se deja en blanco.
    return { precio, dias, tna, tea, md, paridad: null, plazo };
  }

  if (inst.grupo === 'tasafija') {
    // Paridad sobre valor técnico (residual + intereses corridos a la liquidación T+1).
    const paridad = paridadDe(inst, precio, liq);
    if (!precio || !inst.flujos?.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    // El precio de Eco Valores es T+1: los flujos se descuentan desde la fecha de
    // liquidación (hoy + 1 día hábil), en base 365.
    const flujos = inst.flujos.map(f => {
      const fd = parseLocalDate(f.fecha);
      if (!fd) return null;
      const t = (fechaPagoEfectiva(fd) - liq) / (365 * 86400000);
      return t > 0 ? { t, monto: f.total } : null;
    }).filter(Boolean);
    if (!flujos.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tir = calcTIR(flujos, precio);
    if (tir === null) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea = tir * 100;
    const tna = (Math.pow(1 + tir, 1/365) - 1) * 365 * 100;
    const md  = calcMD(flujos, precio, tir, frecuenciaCupon(inst)); // convención de mercado (semestral)
    return { precio, dias, tna, tea, md, paridad };
  }

  if (inst.grupo === 'cer') {
    // CER hoy = historico[hoy - lag hab]
    // CER emision = historico[fecha_emision - lag hab]
    // Valor teo al venc = 100 * (CER_hoy / CER_emision)
    const cerHoy    = getCerConLag(inst.cer_aplicable);
    const cerEmis   = getCerFecha(inst.emision, inst.cer_aplicable);
    const valorTeo  = (cerHoy && cerEmis) ? 100 * cerHoy / cerEmis : null;
    const paridad   = (precio && valorTeo) ? precio / valorTeo * 100 : null;
    if (!precio || !dias || dias <= 0 || !valorTeo) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const vencDate = parseLocalDate(inst.venc);
    // Precio T+1: se descuenta desde la liquidación (T+1 hábil).
    const t = (vencDate - liq) / (365 * 86400000);
    if (t <= 0) return { precio, dias, tna: null, tea: null, md: null, paridad };
    // Fórmula directa para bono zero coupon: TEA = (VF/P)^(1/t) - 1
    const tea_dec = Math.pow(valorTeo / precio, 1 / t) - 1;
    const tea = tea_dec * 100;
    const tna = (Math.pow(1 + tea_dec, 1/365) - 1) * 365 * 100;
    const md  = t / Math.pow(1 + tea_dec, 1/2); // Macaulay(=t) / (1+TEA)^(1/2): convención semestral (1816)
    return { precio, dias, tna, tea, md, paridad, valorTeo, cerHoy, cerEmis };
  }

  if (inst.grupo === 'tamar') {
    // TAMAR: promedio de la serie + margen → TEM → Valor Final
    const serie = bcraData.tamar[inst.ticker] || [];
    if (!serie.length) return { precio, dias, tna: null, tea: null, md: null, paridad: null };

    // 1) Promedio TAMAR + margen (TNA %).
    //    La serie conocida va de emisión-lag a hoy-lag (TAMAR realizada).
    //    El tramo futuro (hoy → vencimiento) se completa con el PROMEDIO de los últimos
    //    TAMAR_DIAS_PROY datos disponibles, y el promedio se hace sobre la serie ampliada.
    const lag = inst.tamar_aplicable || 10;
    const sumaConocida = serie.reduce((a,v) => a+v, 0);
    const tamarProy    = promedioUltimosN(serie, TAMAR_DIAS_PROY);
    // Fin de lo conocido = fecha del último dato publicado, no hoy-lag: la serie ya trae las
    // tasas de los próximos `lag` días hábiles de devengamiento (ver el fetch de TAMAR).
    const fechaHastaKnown = bcraData.tamarUltPub[inst.ticker] || restarDiasHabiles(dateToStr(hoy), lag);
    const fechaVencLag    = restarDiasHabiles(inst.venc, lag);      // vencimiento con el mismo lag
    const diasFuturos  = contarDiasHabiles(fechaHastaKnown, fechaVencLag);
    const sumaTotal    = sumaConocida + tamarProy * diasFuturos;
    const countTotal   = serie.length + diasFuturos;
    const tamarProm    = sumaTotal / countTotal;
    const tasaTotal = tamarProm + inst.margen; // TNA % total

    // 2) TAMAR TEM = ((1 + tasaTotal/100 / (365/32))^(365/32))^(1/12) - 1
    const tamarTEM = Math.pow(Math.pow(1 + (tasaTotal/100) / (365/32), 365/32), 1/12) - 1;

    // 3) Días base 30/360 desde emision a vencimiento, convertido a meses
    const diasBase = dias360(inst.emision, inst.venc);
    const meses = diasBase / 360 * 12;

    // 4) Valor Final = 100 * (1 + TAMAR_TEM)^meses (emision a vencimiento)
    const valorTeo = 100 * Math.pow(1 + tamarTEM, meses);

    // 5) Valor Técnico = 100 * (1 + TAMAR_TEM)^meses_transcurridos (emision a hoy)
    const diasTranscurridos = dias360(inst.emision, dateToStr(hoy));
    const mesesTranscurridos = diasTranscurridos / 360 * 12;
    const valorTecnico = 100 * Math.pow(1 + tamarTEM, mesesTranscurridos);
    const paridad  = (precio && valorTecnico) ? precio / valorTecnico * 100 : null;

    if (!precio || !valorTeo) return { precio, dias, tna: null, tea: null, md: null, paridad };

    // 5) TEA implícita comparando precio vs valorTeo en t años (base 365)
    const vencDate = parseLocalDate(inst.venc);
    // Precio T+1: se descuenta desde la liquidación (T+1 hábil).
    const t = (vencDate - liq) / (365 * 86400000);
    if (t <= 0) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea_dec = Math.pow(valorTeo / precio, 1 / t) - 1;
    const tea = tea_dec * 100;
    const tna = (Math.pow(1 + tea_dec, 1/365) - 1) * 365 * 100;
    const md  = t / Math.pow(1 + tea_dec, 32/365); // TAMAR capitaliza cada ~32 días (m=365/32): convención 1816
    return { precio, dias, tna, tea, md, paridad, valorTeo, tamarProm, tasaTotal };
  }

  // ── USD LINKED ──────────────────────────────────────────────
  if (inst.grupo === 'usdlinked') {
    // TC del valor teórico: el ÚLTIMO A3500 publicado, no uno rezagado.
    //
    // El rezago de 3 días hábiles fija el monto que se paga AL VENCIMIENTO; no es que el capital
    // de hoy valga el dólar de hace tres ruedas. La valuación corriente va contra el TC corriente.
    // Antes se usaba el de 3 hábiles antes de la liquidación y eso metía un desfase parejo: el
    // 2026-08-12 tomaba el TC del 10-08 (1498,09) contra el de hoy (1492,55), 0,373% de más en el
    // valor teórico. La paridad quedaba 0,37 pp baja en los 6 y la TEA se iba de escala en el
    // tramo corto por la anualización: D31G6 daba 21,13% contra 12,35% de 1816.
    //
    // Verificado el 2026-08-12: con el último publicado, TEA y paridad coinciden con 1816 con
    // diferencia 0,0000 en los 6 que publica. El TC implícito en sus paridades da 1492,548 en los
    // seis, o sea exactamente el A3500 del día.
    const tcHoy = bcraData.usdHoy;
    // Valor teórico = 100 × TC aplicable
    const valorTeo = tcHoy ? 100 * tcHoy : null;
    const paridad  = (precio && valorTeo) ? precio / valorTeo * 100 : null;
    if (!precio || !valorTeo || !dias || dias <= 0) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const vencDate = parseLocalDate(inst.venc);
    // El precio de Eco Valores es T+1: se descuenta desde la liquidación (T+1 hábil).
    const t = (vencDate - liq) / (365 * 86400000);
    if (t <= 0) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea_dec = Math.pow(valorTeo / precio, 1 / t) - 1;
    const tea = tea_dec * 100;
    const tna = (Math.pow(1 + tea_dec, 1/365) - 1) * 365 * 100;
    const md  = t / Math.pow(1 + tea_dec, 1/2); // convención semestral (1816); no altera la base FX de TEA/TNA
    return { precio, dias, tna, tea, md, paridad, valorTeo, tcHoy };
  }

  // ── USD BONARES / USD GLOBALES ────────────────────────────────
  if (inst.grupo === 'usdbonares' || inst.grupo === 'usdglobales') {
    const tcHoy = bcraData.usdHoy;
    // Paridad sobre valor técnico en USD (residual + intereses corridos a la liquidación T+1).
    const paridad = paridadDe(inst, precio, liq);
    if (!precio || !inst.flujos?.length || !tcHoy) return { precio, dias, tna: null, tea: null, md: null, paridad };
    // TIR en USD sobre flujos en USD. Precio T+1: se descuenta desde la liquidación.
    const precioUSD = precio; // precio ya viene en USD desde Eco Valores
    const flujos = inst.flujos.map(f => {
      const fd = parseLocalDate(f.fecha);
      if (!fd) return null;
      const t = (fechaPagoEfectiva(fd) - liq) / (365 * 86400000);
      return t > 0 ? { t, monto: f.total } : null;
    }).filter(Boolean);
    if (!flujos.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tir = calcTIR(flujos, precioUSD);
    if (tir === null) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea = tir * 100;
    const m = frecuenciaCupon(inst); // TNA con la periodicidad del cupón
    const tna = (Math.pow(1 + tir, 1/m) - 1) * m * 100;
    const md  = calcMD(flujos, precioUSD, tir, m); // M.Dur en la misma convención (semestral)
    return { precio, dias, tna, tea, md, paridad };
  }

  // ── DUALES ───────────────────────────────────────────────────
  if (inst.grupo === 'dual') {
    const pata = inst.pata;
    if (!pata) return { precio, dias, tna: null, tea: null, md: null, paridad: null };
    // Duales TAMAR/Dólar Linked: el precio viene en la escala dólar linked (~137.000) y hay que
    // bajarlo a la escala del valor final de la pata (~100). El divisor es el TC de EMISIÓN, no el
    // de hoy: la pata TAMAR devenga sobre el capital EN PESOS del día de emisión, que quedó fijo
    // ahí. Con el TC de hoy el rendimiento de la pata se movería con el dólar —justo lo que esta
    // pata no hace—: a TC 1800 la TEA de TMVE8 daría 55,8% en vez de 29,1%.
    // Sin TC de emisión no se puede escalar → null (mejor "—" que un número inventado).
    const precioP = pata.tcEmision ? (precio ? precio / pata.tcEmision : null) : precio;

    let vf = null;
    if (pata.tipo === 'LECAP') {
      vf = pata.vpv;
    } else if (pata.tipo === 'CER') {
      const cerHoy  = getCerConLag(pata.cer_aplicable);
      const cerEmis = getCerFecha(pata.emision, pata.cer_aplicable);
      vf = (cerHoy && cerEmis) ? 100 * cerHoy / cerEmis : null;
    } else if (pata.tipo === 'TAMAR') {
      const serie = bcraData.tamar[inst.ticker + '_TAMAR'] || bcraData.tamar[inst.ticker] || [];
      if (serie.length) {
        // Misma lógica y misma ventana de proyección que el TAMAR simple (TAMAR_DIAS_PROY).
        const lag = pata.tamar_aplicable || 10;
        const sumaConocida = serie.reduce((a,v) => a+v, 0);
        const tamarProy    = promedioUltimosN(serie, TAMAR_DIAS_PROY);
        const claveSerie = bcraData.tamar[inst.ticker + '_TAMAR'] ? inst.ticker + '_TAMAR' : inst.ticker;
        const fechaHastaKnown = bcraData.tamarUltPub[claveSerie] || restarDiasHabiles(dateToStr(hoy), lag);
        const fechaVencLag    = restarDiasHabiles(pata.venc, lag);
        const diasFuturos  = contarDiasHabiles(fechaHastaKnown, fechaVencLag);
        const tamarProm = (sumaConocida + tamarProy * diasFuturos) / (serie.length + diasFuturos);
        const tasaTotal = tamarProm + (pata.margen || 0);
        const tamarTEM = Math.pow(Math.pow(1 + (tasaTotal/100) / (365/32), 365/32), 1/12) - 1;
        const diasBase = dias360(pata.emision, pata.venc);
        const meses = diasBase / 360 * 12;
        vf = 100 * Math.pow(1 + tamarTEM, meses);
      }
    } else if (pata.tipo === 'LINKED') {
      // Valor final = 100 × último A3500 publicado, igual que el USD Linked simple (ver la nota
      // larga en esa rama: el rezago de 3 hábiles fija el pago al vencimiento, no la valuación de
      // hoy). Acá no se puede contrastar contra 1816 —para TMVE8 publica la pata TAMAR, que es la
      // que está in the money— pero se mantiene alineado con el simple, que sí quedó verificado.
      vf = bcraData.usdHoy ? 100 * bcraData.usdHoy : null;
    }

    const paridad = (precioP && vf) ? precioP / vf * 100 : null;
    if (!precioP || !vf || dias <= 0) return { precio: precioP, dias, tna: null, tea: null, md: null, paridad };

    const vencDate = parseLocalDate(inst.venc);
    // Precio T+1: se descuenta desde la liquidación (T+1 hábil).
    const t = (vencDate - liq) / (365 * 86400000);
    if (t <= 0) return { precio: precioP, dias, tna: null, tea: null, md: null, paridad };

    // Guarda de escala. Una pata cuya paridad se va por encima de PARIDAD_MAX_DUAL no está
    // "fuera del dinero": significa que el precio no está en la misma unidad que el valor final
    // de esa pata, y la TIR que sale de ahí no quiere decir nada.
    //
    // La pata TAMAR de TMVE8 cotiza en la escala dólar linked (~137.000); se normaliza dividiendo
    // por el TC de emisión (tcEmision), lo que la deja en la escala del valor final (~100). La guarda
    // queda como red de seguridad por si algún otro instrumento aparece en una unidad inesperada.
    //
    // OJO con no confundir esto con una pata legítimamente fuera del dinero: la pata LECAP de
    // TTS26 da TEA -58,9% con paridad 109% y ESO SÍ es información válida —dice cuánto perderías
    // si termina pagando la pata fija en vez de la TAMAR—, así que no se toca. En un dual cobrás
    // el máximo de las dos patas; que una dé negativo es lo esperable.
    const PARIDAD_MAX_DUAL = 300;
    if (paridad !== null && paridad > PARIDAD_MAX_DUAL) {
      return { precio: precioP, dias, tna: null, tea: null, md: null, paridad: null, escalaDudosa: true };
    }

    const tea_dec = Math.pow(vf / precioP, 1 / t) - 1;
    const tea = tea_dec * 100;
    const tna = (Math.pow(1 + tea_dec, 1/365) - 1) * 365 * 100;
    // Convención de M.Dur según la pata: CER=semestral, TAMAR=cada 32 días, LINKED=semestral, LECAP=efectiva anual (1816)
    // Convención de M.Dur: la del INSTRUMENTO, no la de cada pata. Todos los duales del monitor
    // tienen una pata TAMAR, que capitaliza cada 32 días, y 1816 aplica esa misma base a las dos
    // patas. Verificado el 2026-08-12 contra su durationMod: las 5 patas CER dan error 0 con
    // 32/365 y quedaban 0,04 a 0,10 por debajo con la semestral que usábamos (TXMJ0: 3,7562
    // contra 3,8551). Las patas TAMAR ya coincidían. De LECAP y LINKED no hay con qué comparar
    // —1816 sólo publica la pata que está in the money— pero siguen la misma base por coherencia:
    // las dos patas de un dual se leen en filas contiguas y medirlas distinto no tiene sentido.
    const md  = t / Math.pow(1 + tea_dec, 32/365);
    return { precio: precioP, dias, tna, tea, md, paridad, vf };
  }

  // ── USD BOPREALES ─────────────────────────────────────────────
  if (inst.grupo === 'usdbopreal') {
    const paridad = paridadDe(inst, precio, liq);
    if (!precio || !inst.flujos?.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const flujos = inst.flujos.map(f => {
      const fd = parseLocalDate(f.fecha);
      if (!fd) return null;
      const t = (fechaPagoEfectiva(fd) - liq) / (365 * 86400000);
      return t > 0 ? { t, monto: f.total } : null;
    }).filter(Boolean);
    if (!flujos.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tir = calcTIR(flujos, precio);
    if (tir === null) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea = tir * 100;
    const m = frecuenciaCupon(inst); // TNA con la periodicidad del cupón
    const tna = (Math.pow(1 + tir, 1/m) - 1) * m * 100;
    const md  = calcMD(flujos, precio, tir, m); // M.Dur en la misma convención (semestral)
    return { precio, dias, tna, tea, md, paridad };
  }

  // ── ON USD / Subsoberanos (Hard Dollar, USD) ──────────────────
  if (inst.grupo === 'onusd' || inst.grupo === 'subsoberano') {
    const paridad = paridadDe(inst, precio, liq);
    if (!precio || !inst.flujos?.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const flujos = inst.flujos.map(f => {
      const fd = parseLocalDate(f.fecha);
      if (!fd) return null;
      const t = (fechaPagoEfectiva(fd) - liq) / (365 * 86400000);
      return t > 0 ? { t, monto: f.total } : null;
    }).filter(Boolean);
    if (!flujos.length) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tir = calcTIR(flujos, precio);
    if (tir === null) return { precio, dias, tna: null, tea: null, md: null, paridad };
    const tea = tir * 100;
    const m = frecuenciaCupon(inst); // TNA con la periodicidad del cupón
    const tna = (Math.pow(1 + tir, 1/m) - 1) * m * 100;
    const md  = calcMD(flujos, precio, tir, m); // M.Dur en la misma convención (semestral)
    return { precio, dias, tna, tea, md, paridad };
  }

  return { precio, dias, tna: null, tea: null, md: null, paridad: null };
}

function calcTIR(flujos, precio) {
  if (!flujos?.length || !precio || precio <= 0) return null;
  let r = 0.10;
  for (let i = 0; i < 200; i++) {
    let vpn = -precio, dvpn = 0;
    for (const f of flujos) {
      const disc = Math.pow(1 + r, f.t);
      vpn  += f.monto / disc;
      dvpn -= f.t * f.monto / (disc * (1 + r));
    }
    if (Math.abs(dvpn) < 1e-12) break;
    const rNew = r - vpn / dvpn;
    if (Math.abs(rNew - r) < 1e-10) { r = rNew; break; }
    r = Math.max(rNew, -0.999);
  }
  return r; // TEA decimal
}

function calcVPV(emision, venc, tem) {
  if (!emision || !venc || !tem) return null;
  const t = dias360(emision, venc) / 30;
  return 100 * Math.pow(1 + tem/100, t);
}

function contarDiasHabiles(fechaInicioStr, fechaFinStr) {
  const ini = parseLocalDate(fechaInicioStr), fin = parseLocalDate(fechaFinStr);
  if (!ini || !fin || fin <= ini) return 0;
  let count = 0;
  const d = new Date(ini);
  while (d < fin) {
    d.setDate(d.getDate() + 1);
    if (esHabil(d)) count++;
  }
  return count;
}

function dias360(emision, venc) {
  const e = parseLocalDate(emision), v = parseLocalDate(venc);
  let d1=e.getDate(), m1=e.getMonth()+1, y1=e.getFullYear();
  let d2=v.getDate(), m2=v.getMonth()+1, y2=v.getFullYear();
  if (d1===31) d1=30;
  if (d2===31 && d1>=30) d2=30;
  return (y2-y1)*360 + (m2-m1)*30 + (d2-d1);
}

function esHabil(d) {
  const dow = d.getDay();
  if (dow === 0 || dow === 6) return false;
  return !feriados.has(dateToStr(d));
}

function fechaPagoEfectiva(fecha) {
  const f = new Date(fecha);
  while (!esHabil(f)) f.setDate(f.getDate() + 1);
  return f;
}

function frecuenciaCupon(inst) {
  const flujos = inst.flujosAll || inst.flujos || [];
  const fechas = flujos.filter(f => (f.renta || 0) > 0)
                       .map(f => parseLocalDate(f.fecha)).filter(Boolean)
                       .sort((a, b) => a - b);
  if (fechas.length < 2) return 2; // fallback: semestral
  const diffs = [];
  for (let i = 1; i < fechas.length; i++) diffs.push((fechas[i] - fechas[i-1]) / 86400000);
  diffs.sort((a, b) => a - b);
  const mediana = diffs[Math.floor(diffs.length / 2)];
  if (!mediana || mediana <= 0) return 2;
  return Math.max(1, Math.round(365 / mediana));
}

function getCerConLag(lagDias) {
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  return buscarCerCache(restarDiasHabiles(dateToStr(sumarDiasHabiles(hoy, 1)), lagDias));
}

function getCerFecha(fechaEmision, lagDias) {
  if (!fechaEmision) return null;
  const fechaRef = restarDiasHabiles(fechaEmision, lagDias);
  return buscarCerCache(fechaRef);
}

function metricasDe1816(inst, precio, dias) {
  // 1816 calcula estos indicadores al precio de mercado y no tenemos el cronograma para
  // recalcularlos a otro precio. Si se pide a un precio distinto (comisión), se aproxima el
  // desplazamiento de la TEA con la duration modificada: Δy ≈ -(ΔP/P)/MD. Queda marcado con
  // `aproxComision` para poder avisarlo en la celda.
  const ind = indicadores1816[String(inst.ticker).toUpperCase()] || {};
  const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
  const teaDec  = num(ind.tea);
  const paridad = num(ind.paridad) === null ? null : num(ind.paridad) * 100;
  const md      = num(ind.durationMod);
  const tea = teaDec === null ? null : teaDec * 100;
  const tna = teaDec === null ? null : (Math.pow(1 + teaDec, 1/2) - 1) * 2 * 100;
  const pMkt = precios[inst.ticker] || null;
  if (precio && pMkt && md && tea !== null && Math.abs(precio - pMkt) > 1e-9) {
    const teaAj = tea - ((precio - pMkt) / pMkt) / md * 100;
    return { precio, dias, tna, tea: teaAj, md, paridad, aproxComision: true };
  }
  return { precio, dias, tna, tea, md, paridad };
}

function normalizarMargen(raw) {
  let m = raw;
  if (typeof m === 'string') m = parseFloat(m.replace('%','').trim()) || 0;
  else m = parseFloat(m) || 0;
  if (m < 1 && m > 0) m = m * 100;
  return m;
}

function paridadDe(inst, precio, liq) {
  if (!precio) return null;
  const vt = valorTecnicoTF(inst, liq);
  if (vt) return precio / vt * 100;
  const ind = indicadores1816[String(inst.ticker).toUpperCase()] || {};
  const p = ind.paridad;
  if (typeof p !== 'number' || !isFinite(p)) return null;
  // 1816 la calcula al precio de mercado y la da como fracción. Si se pidió a otro precio
  // (comisión), se reescala: el valor técnico no depende del precio.
  const pMkt = precios[inst.ticker] || null;
  const factor = (pMkt && precio !== pMkt) ? precio / pMkt : 1;
  return p * 100 * factor;
}

function parsearExcel(buffer) {
  try {
    const wb = XLSX.read(buffer, { type: 'array' });
    let result = [];   // let: más abajo se reasigna al filtrar los grupos ocultos

    // LECAP
    // Las hojas del Excel repiten la fila de cabecera (LECAPS y USD Linked la traen dos veces;
    // Duales, catorce). Sin descartarla entra como un instrumento más: el Monitor mostraba una
    // fila con el ticker "Ticker" y le pedía precio al proxy en cada consulta.
    if (wb.SheetNames.includes('LECAPS')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['LECAPS'], { defval: '', raw: false });
      rows.filter(r => { const t = String(r['Ticker']||'').trim(); return t && t !== 'Ticker'; }).forEach(r => {
        const emision = excelSerialToDate(r['Fecha Emision']);
        const venc    = excelSerialToDate(r['Fecha Vencimiento']);
        const tem     = parseFloat(r['TEM (%)']) || null;
        result.push({
          grupo: 'lecap',
          ticker: String(r['Ticker']).trim(),
          nombre: String(r['Tipo']||'LECAP').trim(),
          emision, venc, tem,
          vpv: calcVPV(emision, venc, tem),
        });
      });
    }

    // TASA FIJA
    if (wb.SheetNames.includes('TASA FIJA')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['TASA FIJA'], { defval: '', raw: false });
      const grupos = {};
      rows.forEach(r => {
        const ticker = String(r['Ticker']||'').trim();
        if (!ticker || ticker === 'Ticker') return;
        if (!grupos[ticker]) grupos[ticker] = [];
        grupos[ticker].push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta'])          || 0,
          amort: parseFloat(r['Amortización'])   || 0,
          total: parseFloat(r['Total'])          || 0,
        });
      });
      Object.entries(grupos).forEach(([ticker, flujos]) => {
        flujos.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
        const venc = flujos[flujos.length-1]?.fecha;
        const vr   = flujos.find(f => f.vr > 0)?.vr || 100;
        result.push({
          grupo: 'tasafija',
          ticker,
          nombre: 'TASA FIJA',
          venc, vr,
          flujos: flujos.filter(f => f.total > 0),
          flujosAll: flujos, // incluye la fila de emisión (total 0), para calcular corridos
        });
      });
    }

    // CER
    if (wb.SheetNames.includes('CER')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['CER'], { defval: '', raw: false });
      rows.filter(r => { const t = String(r['Ticker']||'').trim(); return t && t !== 'Ticker'; }).forEach(r => {
        result.push({
          grupo: 'cer',
          ticker: String(r['Ticker']).trim(),
          nombre: String(r['Tipo']||'CER').trim(),
          emision: excelSerialToDate(r['Fecha Emisión'] || r['Fecha Emision']),
          venc:    excelSerialToDate(r['Fecha Vencimineto'] || r['Fecha Vencimiento']),
          cer_aplicable: parseInt(r['CER aplicable']) || 10,
          margen: parseFloat(r['Margen']) || 0,
        });
      });
    }

    // TAMAR
    if (wb.SheetNames.includes('TAMAR')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['TAMAR'], { defval: '', raw: false });
      rows.filter(r => { const t = String(r['Ticker']||'').trim(); return t && t !== 'Ticker'; }).forEach(r => {
        const margen = normalizarMargen(r['Margen']);
        result.push({
          grupo: 'tamar',
          ticker: String(r['Ticker']).trim(),
          nombre: String(r['Tipo']||'TAMAR').trim(),
          emision: excelSerialToDate(r['Fecha Emisión'] || r['Fecha Emision']),
          venc:    excelSerialToDate(r['Fecha Vencimineto'] || r['Fecha Vencimiento']),
          tamar_aplicable: parseInt(r['TAMAR aplicable']) || 10,
          margen,
        });
      });
    }

    // ── USD LINKED ──────────────────────────────────────────────
    if (wb.SheetNames.includes('USD Linked')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['USD Linked'], { defval: '', raw: false });
      rows.filter(r => String(r['Ticker']||'').trim() && String(r['Ticker']||'').trim() !== 'Ticker').forEach(r => {
        result.push({
          grupo: 'usdlinked',
          ticker: String(r['Ticker']).trim(),
          nombre: String(r['Tipo']||'Linked').trim(),
          emision: excelSerialToDate(r['Fecha Emisión'] || r['Fecha Emision']),
          venc:    excelSerialToDate(r['Fecha Vencimineto'] || r['Fecha Vencimiento']),
          usd_aplicable: parseFloat(r['USD aplicable']) || null,
          margen: parseFloat(r['Margen']) || 0,
        });
      });
    }

    // ── USD BONARES ──────────────────────────────────────────────
    if (wb.SheetNames.includes('USD Bonares')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['USD Bonares'], { defval: '', raw: false });
      const grupos = {};
      rows.forEach(r => {
        const ticker = String(r['Ticker']||'').trim();
        if (!ticker || ticker === 'Ticker') return;
        if (!grupos[ticker]) grupos[ticker] = [];
        grupos[ticker].push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta']) || 0,
          total: parseFloat(r['Total']) || 0,
        });
      });
      Object.entries(grupos).forEach(([ticker, flujos]) => {
        flujos.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
        const venc = flujos[flujos.length-1]?.fecha;
        const vr   = flujos.find(f => f.vr > 0)?.vr || 100;
        result.push({ grupo: 'usdbonares', ticker, nombre: 'USD BONARES', venc, vr, flujos: flujos.filter(f => f.total > 0), flujosAll: flujos });
      });
    }

    // ── USD GLOBALES ─────────────────────────────────────────────
    if (wb.SheetNames.includes('USD Globales')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['USD Globales'], { defval: '', raw: false });
      const grupos = {};
      rows.forEach(r => {
        const ticker = String(r['Ticker']||'').trim();
        if (!ticker || ticker === 'Ticker') return;
        if (!grupos[ticker]) grupos[ticker] = [];
        grupos[ticker].push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta']) || 0,
          total: parseFloat(r['Total']) || 0,
        });
      });
      Object.entries(grupos).forEach(([ticker, flujos]) => {
        flujos.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
        const venc = flujos[flujos.length-1]?.fecha;
        const vr   = flujos.find(f => f.vr > 0)?.vr || 100;
        result.push({ grupo: 'usdglobales', ticker, nombre: 'USD GLOBALES', venc, vr, flujos: flujos.filter(f => f.total > 0), flujosAll: flujos });
      });
    }

    // ── USD BOPREALES ────────────────────────────────────────────
    if (wb.SheetNames.includes('USD Bopreales')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['USD Bopreales'], { defval: '', raw: false });
      const grupos = {};
      rows.forEach(r => {
        const ticker = String(r['Ticker']||'').trim();
        if (!ticker || ticker === 'Ticker') return;
        if (!grupos[ticker]) grupos[ticker] = [];
        grupos[ticker].push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta']) || 0,
          total: parseFloat(r['Total']) || 0,
        });
      });
      Object.entries(grupos).forEach(([ticker, flujos]) => {
        flujos.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
        const venc = flujos[flujos.length-1]?.fecha;
        const vr   = flujos.find(f => f.vr > 0)?.vr || 100;
        result.push({ grupo: 'usdbopreal', ticker, nombre: 'USD BOPREALES', venc, vr, flujos: flujos.filter(f => f.total > 0), flujosAll: flujos });
      });
    }

    // ── ON USD (Hard Dollar) ─────────────────────────────────────
    if (wb.SheetNames.includes('ON USD')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['ON USD'], { defval: '', raw: false });
      const grupos = {};
      rows.forEach(r => {
        const ticker = String(r['Ticker']||'').trim();
        if (!ticker || ticker === 'Ticker') return;
        if (!grupos[ticker]) grupos[ticker] = [];
        grupos[ticker].push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta']) || 0,
          total: parseFloat(r['Total']) || 0,
        });
      });
      Object.entries(grupos).forEach(([ticker, flujos]) => {
        flujos.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
        const venc = flujos[flujos.length-1]?.fecha;
        const vr   = flujos.find(f => f.vr > 0)?.vr || 100;
        result.push({ grupo: 'onusd', ticker, nombre: 'ON USD', venc, vr, flujos: flujos.filter(f => f.total > 0), flujosAll: flujos });
      });
    }

    // Subsoberanos (provinciales USD): hoja PARAMÉTRICA, sin cronograma de flujos.
    // Para este grupo el monitor no calcula nada: muestra los indicadores de 1816
    // (ver la rama 'subsoberano' en calcMetricas). Sólo hacen falta ticker, nombre y venc.
    if (wb.SheetNames.includes('Subsoberanos')) {
      const rows = XLSX.utils.sheet_to_json(wb.Sheets['Subsoberanos'], { defval: '', raw: false });
      rows.filter(r => { const t = String(r['Ticker']||'').trim(); return t && t !== 'Ticker'; }).forEach(r => {
        result.push({
          grupo: 'subsoberano',
          ticker: String(r['Ticker']).trim(),
          nombre: String(r['Nombre']||'').trim(),
          venc:   excelSerialToDate(r['Fecha Vencimiento'] || r['Fecha Vencimineto']),
        });
      });
    }

    // ── DUALES ───────────────────────────────────────────────────
    if (wb.SheetNames.includes('Duales')) {
      const ws = wb.Sheets['Duales'];
      // Leer raw para manejar múltiples bloques con headers distintos
      const rawRows = XLSX.utils.sheet_to_json(ws, { defval: '', raw: false, header: 1 });
      const patas = {};
      let currentHeaders = [];

      rawRows.forEach(row => {
        // Detectar si es fila de headers (primera celda es 'Ticker')
        if (String(row[0]||'').trim() === 'Ticker') {
          currentHeaders = row.map(c => String(c||'').trim());
          return;
        }
        // Saltar filas vacías
        if (!row[0]) return;

        // Construir objeto con headers actuales
        const r = {};
        currentHeaders.forEach((h, i) => { if (h) r[h] = row[i]; });

        const ticker = String(r['Ticker']||'').trim();
        const tipo   = String(r['Tipo']||'').trim().toUpperCase();
        if (!ticker || !tipo) return;
        if (!patas[ticker]) patas[ticker] = [];

        const pata = { tipo, ticker,
          emision: excelSerialToDate(r['Fecha Emisión'] || r['Fecha Emision']),
          venc:    excelSerialToDate(r['Fecha Vencimineto'] || r['Fecha Vencimiento']),
        };
        if (tipo === 'CER')   { pata.cer_aplicable = parseInt(r['CER aplicable']) || 10; pata.margen = normalizarMargen(r['Margen']); }
        if (tipo === 'TAMAR') { pata.tamar_aplicable = parseInt(r['TAMAR aplicable']) || 10; pata.margen = normalizarMargen(r['Margen']); }
        if (tipo === 'LECAP') {
          pata.tem = parseFloat(r['TEM (%)']) || null;
          pata.vpv = calcVPV(pata.emision, pata.venc, pata.tem);
        }
        if (tipo === 'LINKED') { pata.usd_aplicable = parseFloat(r['USD aplicable']) || null; pata.margen = normalizarMargen(r['Margen']); }
        patas[ticker].push(pata);
      });

      Object.entries(patas).forEach(([ticker, ps]) => {
        const linked = ps.find(p => p.tipo === 'LINKED');
        ps.forEach(pata => {
          // Duales TAMAR/Dólar Linked: la pata TAMAR cotiza en escala dólar linked → se normaliza
          // el precio con el TC de EMISIÓN, que es fijo y viene en la columna "USD aplicable" de
          // la pata Linked (para TMVE8, 1499,8387 = A3500 del 28/07, 3 hábiles antes del 31/07).
          if (pata.tipo === 'TAMAR' && linked) pata.tcEmision = linked.usd_aplicable || null;
          result.push({
            grupo: 'dual',
            ticker,
            nombre: pata.tipo,
            venc: pata.venc,
            pata, // single pata for this row
          });
        });
      });
    }

    // ── FLUJOS (hoja long-format): join por ticker ──────────────
    // Los subsoberanos (paramétricos) obtienen así su cronograma y pasan de los indicadores
    // de 1816 a cálculo local (rama subsoberano de calcMetricas), bajando de 5 a 2 créditos.
    if (wb.SheetNames.includes('Flujos')) {
      const frows = XLSX.utils.sheet_to_json(wb.Sheets['Flujos'], { defval: '', raw: false });
      const porTk = {};
      frows.forEach(r => {
        const tk = String(r['Ticker']||'').trim();
        if (!tk || tk === 'Ticker') return;
        (porTk[tk] = porTk[tk] || []).push({
          fecha: excelSerialToDate(r['Fecha']),
          vr:    parseFloat(r['Valor Residual']) || 0,
          renta: parseFloat(r['Renta']) || 0,
          amort: parseFloat(r['Amortización']) || 0,
          total: parseFloat(r['Total']) || 0,
        });
      });
      for (const inst of result) {
        const fl = porTk[inst.ticker];
        if (fl && fl.length && !(inst.flujos && inst.flujos.length)) {
          fl.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
          inst.flujosAll = fl;
          inst.flujos = fl.filter(f => f.total > 0);
          inst.vr = fl.find(f => f.vr > 0)?.vr || 100;
        }
      }
    }

    // Tickers de la solapa ONs (viven en ons.html, no en esta página): se registran como
    // "seguidos" para que el detector NO los cuente como faltantes. Se guarda también la forma
    // 1816 (D->O), que es la que usa el universo de /api/instrumentos.
    window._onsSeguidas = [];
    if (wb.SheetNames.includes('ONs')) {
      XLSX.utils.sheet_to_json(wb.Sheets['ONs'], { defval: '', raw: false }).forEach(r => {
        const tk = String(r['Ticker']||'').trim();
        if (tk && tk !== 'Ticker') {
          window._onsSeguidas.push(tk);
          if (tk.endsWith('D')) window._onsSeguidas.push(tk.slice(0, -1) + 'O');
        }
      });
    }

    // Sacar los grupos ocultos (siguen en el Excel, sólo no se muestran ni se les pide precio)
    result = result.filter(i => !GRUPOS_OCULTOS.includes(i.grupo));

    // Ordenar por grupo (según GRUPOS) y dentro de cada grupo por días al vencimiento
    const grupoOrder = GRUPOS.map(g => g.key);
    result.sort((a,b) => {
      const gi = grupoOrder.indexOf(a.grupo) - grupoOrder.indexOf(b.grupo);
      if (gi !== 0) return gi;
      return (diasAlVenc(a.venc)||9999) - (diasAlVenc(b.venc)||9999);
    });

    instrumentos = result;

  } catch(e) {
    // Se relanza en vez de avisar acá: setStatus y showAlert son de la página, y el motor no
    // puede depender de ellas —cuando lo hacía, cualquier error de parseo se transformaba en un
    // "setStatus is not defined" que tapaba la causa real—. Cada página lo reporta a su manera.
    console.error(e);
    throw e;
  }
}

function promedioUltimosN(serie, n = 3) {
  if (!serie?.length) return null;
  const ult = serie.slice(-n);
  return ult.reduce((a, v) => a + v, 0) / ult.length;
}

function restarDiasHabiles(fechaStr, n) {
  const d = parseLocalDate(fechaStr) || new Date();
  let restantes = n;
  while (restantes > 0) {
    d.setDate(d.getDate() - 1);
    if (esHabil(d)) restantes--;
  }
  return dateToStr(d);
}

function sumarDiasHabiles(fecha, n) {
  const d = (fecha instanceof Date) ? new Date(fecha) : (parseLocalDate(fecha) || new Date());
  let restantes = n;
  while (restantes > 0) {
    d.setDate(d.getDate() + 1);
    if (esHabil(d)) restantes--;
  }
  return d;
}

function usaIndicadores1816(inst) {
  if (!inst) return false;
  // Los grupos paramétricos se valúan con su propia fórmula y no con flujos... salvo que tengan
  // cronograma cargado. Un Boncer con renta y amortización (DICP, PARP, CUAP, TX28, TX31) NO es
  // un zero coupon, y la rama `cer` —que hace TEA = (valorTeo/precio)^(1/t)-1 sobre un único pago
  // al vencimiento— le daría cualquier cosa. Tener flujos es la marca de que ese instrumento no
  // entra en la fórmula paramétrica; para esos se toman los indicadores de 1816.
  //
  // Al revés que en el resto: ahí los flujos habilitan el cálculo propio, acá lo descartan.
  if (GRUPOS_PARAMETRICOS.includes(inst.grupo)) return !!(inst.flujos && inst.flujos.length);
  return !(inst.flujos && inst.flujos.length);
}

function usaParidad1816(inst) {
  if (!inst || GRUPOS_PARAMETRICOS.includes(inst.grupo)) return false;
  if (!(inst.flujos && inst.flujos.length)) return false;   // esos ya van por usaIndicadores1816
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  return valorTecnicoTF(inst, sumarDiasHabiles(hoy, 1)) === null;
}

function valorTecnicoTF(inst, fechaLiq) {
  const flujos = inst.flujosAll || inst.flujos;
  if (!flujos?.length) return null;
  const liqMs = fechaLiq.getTime();
  // Buscar el período de cupón que contiene la liquidación: [ini, fin]
  let ini = null, fin = null;
  for (const f of flujos) {
    const fd = parseLocalDate(f.fecha);
    if (!fd) continue;
    if (fd.getTime() <= liqMs) ini = f;            // último flujo <= liq (cupón anterior o emisión)
    if (fd.getTime() > liqMs) { fin = f; break; }  // primer flujo > liq (próximo cupón)
  }
  if (!fin) return null; // ya venció
  // Sin ningún flujo anterior a la liquidación no se sabe desde cuándo devenga el cupón en
  // curso, y antes se asumía residual 100 sin intereses corridos: la paridad quedaba igual al
  // precio. Pasa con los recién emitidos (NDG34) y con los cronogramas scrapeados de 1816, que
  // sólo traen los cupones que faltan (ERM33 daba 106,50 contra 102,27 de 1816). Se devuelve
  // null y el llamador cae a la paridad que publica 1816.
  if (!ini) return null;
  // Residual vigente durante el período en curso = VR del flujo de inicio (o el último VR>0)
  let residual;
  if (ini.vr > 0) residual = ini.vr;
  else {
    const previos = flujos.filter(f => parseLocalDate(f.fecha)?.getTime() <= liqMs && f.vr > 0);
    residual = previos.length ? previos[previos.length - 1].vr : (inst.vr || 100);
  }
  // Intereses corridos = renta del próximo cupón × (días transcurridos / días del período)
  let corridos = 0;
  if (ini) {
    const iniMs = parseLocalDate(ini.fecha).getTime();
    const finMs = parseLocalDate(fin.fecha).getTime();
    const totalDias = (finMs - iniMs) / 86400000;
    const transc    = (liqMs - iniMs) / 86400000;
    if (totalDias > 0 && (fin.renta || 0) > 0) corridos = fin.renta * (transc / totalDias);
  }
  return residual + corridos;
}

function parseLocalDate(s) {
  if (!s) return null;
  const p = String(s).split('-').map(Number);
  return new Date(p[0], p[1]-1, p[2]);
}

function dateToStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function diasAlVenc(f) {
  if (!f) return null;
  const hoy = new Date(); hoy.setHours(0,0,0,0);
  const v = parseLocalDate(f);
  if (!v) return null;
  return Math.floor((v - hoy) / 86400000);
}

function excelSerialToDate(serial) {
  if (!serial && serial !== 0) return null;
  const s = String(serial).trim();
  // YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  // M/D/YY o MM/DD/YY (formato US corto de Excel con raw:false)
  if (/^\d{1,2}\/\d{1,2}\/\d{2}$/.test(s)) {
    const [m, d, y] = s.split('/');
    const year = parseInt(y) + 2000;
    return `${year}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`;
  }
  // M/D/YYYY o MM/DD/YYYY (formato US largo)
  if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(s)) {
    const [m, d, y] = s.split('/');
    return `${y}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`;
  }
  // Serial numérico (fallback)
  if (/^\d+$/.test(s)) {
    const n = parseInt(s);
    const d = new Date(0);
    d.setUTCFullYear(1899, 11, 31);
    d.setUTCDate(d.getUTCDate() + n);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  }
  return s;
}

/* ── MOTOR DE CÁLCULO ─────────────────────────────────────────────────────────────────
 * Mudado desde bonos.html, no copiado: hay una sola definición de cada cosa y el Monitor
 * también las consume desde acá. Todo vive en el scope global —estas páginas no tienen
 * módulos ni build—, así que cargar este archivo antes deja los mismos nombres visibles
 * para quien ya los usaba.
 *
 * El estado va primero porque const/let tienen zona muerta temporal; las funciones se
 * hoistean y su orden entre sí da igual.
 */



async function cargarFeriados() {
  const anioActual = new Date().getFullYear();
  const anios = [];
  for (let y = 2020; y <= anioActual; y++) anios.push(y);
  const set = new Set();
  try {
    const resultados = await Promise.all(anios.map(y =>
      fetch(`${FERIADOS_WORKER}/?anio=${y}`).then(r => r.ok ? r.json() : []).catch(() => [])
    ));
    resultados.flat().forEach(f => { if (f && f.fecha) set.add(String(f.fecha).slice(0, 10)); });
    if (set.size) {
      feriados = set;
      try { localStorage.setItem('bonos_feriados', JSON.stringify([...set])); } catch(e) {}
      console.log(`Feriados cargados: ${set.size} (${anios[0]}–${anioActual})`);
    } else {
      throw new Error('respuesta vacía');
    }
  } catch(e) {
    // Respaldo: última lista guardada
    try {
      const backup = JSON.parse(localStorage.getItem('bonos_feriados') || '[]');
      if (backup.length) { feriados = new Set(backup); console.warn('Feriados desde respaldo localStorage:', backup.length); }
    } catch(_) {}
    console.warn('No se pudieron cargar feriados de la API:', e);
  }
}

async function fetchBCRA() {
  const hoy = dateToStr(new Date());

  // CER: para cada bono CER fetchear fecha_emision-lag y hoy-lag
  // Incluir patas CER de Duales (igual que se hace con TAMAR)
  const bonosCer = [
    ...instrumentos.filter(i => i.grupo === 'cer'),
    ...instrumentos.filter(i => i.grupo === 'dual' && i.pata?.tipo === 'CER').map(i => ({
      emision: i.pata.emision,
      cer_aplicable: i.pata.cer_aplicable,
    }))
  ];
  for (const b of bonosCer) {
    const fechaHoy   = restarDiasHabiles(hoy, b.cer_aplicable);
    const fechaEmis  = restarDiasHabiles(b.emision, b.cer_aplicable);
    await fetchCerFecha(fechaHoy);
    await fetchCerFecha(fechaEmis);
  }

  // TAMAR: serie desde emision-lag hasta HOY (no hasta hoy-lag).
  // El cupón devenga con rezago: la tasa que se aplica el día D es la TAMAR publicada D-lag
  // hábiles antes. Por eso los próximos `lag` días hábiles de devengamiento YA tienen su tasa
  // publicada, y traer la serie sólo hasta hoy-lag obligaba a proyectarlos. Con la TAMAR en
  // suba eso subestimaba el promedio: medido contra las TEA de 1816 del 2026-08-07, la
  // diferencia en valor pasa de 0,18% promedio a 0,10% al usar los datos reales.
  // Incluir patas TAMAR de Duales (ahora cada pata es un instrumento separado)
  const bonosTamar = [
    ...instrumentos.filter(i => i.grupo === 'tamar'),
    ...instrumentos.filter(i => i.grupo === 'dual' && i.pata?.tipo === 'TAMAR').map(i => ({
      ticker: i.ticker + '_TAMAR',
      emision: i.pata.emision,
      tamar_aplicable: i.pata.tamar_aplicable,
    }))
  ];
  for (const b of bonosTamar) {
    const fechaDesde = restarDiasHabiles(b.emision, b.tamar_aplicable);
    const fechaHasta = hoy;   // en fetchBCRA `hoy` ya es string 'YYYY-MM-DD'
    try {
      const resp = await fetch(`${BCRA_WORKER}/?serie=tamar&desde=${fechaDesde}&hasta=${fechaHasta}`);
      const json = await resp.json();
      const detalle = json.results?.[0]?.detalle || [];
      // Ordenar por fecha ascendente (viejo→nuevo): la API puede devolverlos al
      // revés, y el cálculo necesita que serie[length-1] sea el dato MÁS RECIENTE.
      detalle.sort((a,b) => (a.fecha||'').localeCompare(b.fecha||''));
      // Guardar serie por ticker
      // Rellenar los días hábiles SIN publicación arrastrando la última tasa. El cupón devenga
      // todos los días hábiles: el que no tiene dato nuevo devenga a la última publicada. Saltearlos
      // no era neutro, porque el promedio se hace sobre serie.length y los saca del peso. En la
      // ventana de M31G6 faltaban 3 —06-11-2025 (Día del Bancario), 24-12 y 31-12—, los tres con la
      // TAMAR bastante más alta que la de hoy (36%, 29%, 29% contra 23%), así que el promedio
      // quedaba tirado para abajo y con él el valor final.
      const porFecha = new Map(detalle.map(r => [String(r.fecha).slice(0, 10), parseFloat(r.valor)]));
      const ultimaFecha = detalle.length ? String(detalle[detalle.length - 1].fecha).slice(0, 10) : null;
      const serieRell = [];
      if (ultimaFecha) {
        let arrastre = null;
        for (const d = parseLocalDate(fechaDesde); dateToStr(d) <= ultimaFecha; d.setDate(d.getDate() + 1)) {
          if (!esHabil(d)) continue;
          const v = porFecha.get(dateToStr(d));
          if (v != null) arrastre = v;
          if (arrastre != null) serieRell.push(arrastre);
        }
      }
      bcraData.tamar[b.ticker] = serieRell;
      // Fecha del último dato: marca hasta dónde llega lo CONOCIDO (ver calcMetricas).
      if (detalle.length) bcraData.tamarUltPub[b.ticker] = String(detalle[detalle.length - 1].fecha).slice(0, 10);
      console.log(`TAMAR ${b.ticker}: ${detalle.length} datos`);
      console.log(`TAMAR ${b.ticker}: ${detalle.length} datos, promedio: ${(bcraData.tamar[b.ticker].reduce((a,v)=>a+v,0)/bcraData.tamar[b.ticker].length).toFixed(4)}%`);
    } catch(e) {
      console.warn(`BCRA TAMAR ${b.ticker} error:`, e);
    }
  }

  // USD: TC mayorista — buscar últimos 10 días hábiles para cubrir feriados
  try {
    const hoyStr = dateToStr(new Date());
    const hace = restarDiasHabiles(hoyStr, 10);
    const resp = await fetch(`${BCRA_WORKER}/?serie=usd&desde=${hace}&hasta=${hoyStr}`);
    const json = await resp.json();
    const detalle = json.results?.[0]?.detalle || [];
    if (detalle.length) {
      // Guardar toda la serie por fecha (para el TC aplicable de Dollar Linked)
      detalle.forEach(r => { if (r.fecha && r.valor) bcraData.usd[r.fecha] = parseFloat(r.valor); });
      detalle.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.usdHoy = parseFloat(detalle[0].valor);
      console.log(`TC Mayorista: ${bcraData.usdHoy} (${detalle[0].fecha})`);
    }
  } catch(e) {
    console.warn('BCRA USD error:', e);
  }

  // PF 30d
  try {
    const hoyStr = dateToStr(new Date());
    const hace7 = restarDiasHabiles(hoyStr, 7);
    const respP = await fetch(`${BCRA_WORKER}/?serie=plazo30d&desde=${hace7}&hasta=${hoyStr}`);
    const jsonP = await respP.json();
    const detP = jsonP.results?.[0]?.detalle || [];
    if (detP.length) {
      detP.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.plazo30d = parseFloat(detP[0].valor);
    }
  } catch(e) { console.warn('BCRA PF30d error:', e); }

  // Caución 1d
  try {
    const hoyStr = dateToStr(new Date());
    const hace7 = restarDiasHabiles(hoyStr, 7);
    const respC = await fetch(`${BCRA_WORKER}/?serie=caucion1d&desde=${hace7}&hasta=${hoyStr}`);
    const jsonC = await respC.json();
    const detC = jsonC.results?.[0]?.detalle || [];
    if (detC.length) {
      detC.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.caucion1d = parseFloat(detC[0].valor);
    }
  } catch(e) { console.warn('BCRA caucion1d error:', e); }

  // UVA
  try {
    const hoyStr = dateToStr(new Date());
    const hace7 = restarDiasHabiles(hoyStr, 7);
    const respU = await fetch(`${BCRA_WORKER}/?serie=uva&desde=${hace7}&hasta=${hoyStr}`);
    const jsonU = await respU.json();
    const detU = jsonU.results?.[0]?.detalle || [];
    if (detU.length) {
      detU.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.uva = parseFloat(detU[0].valor);
    }
  } catch(e) { console.warn('BCRA UVA error:', e); }

  // CER de hoy (solo para el banner) — último dato <= hoy de la serie.
  // Independiente del lag de 10 hábiles que usan los cálculos de los bonos.
  try {
    const hoyStr = dateToStr(new Date());
    const hace7 = restarDiasHabiles(hoyStr, 7);
    const respCer = await fetch(`${BCRA_WORKER}/?serie=cer&desde=${hace7}&hasta=${hoyStr}`);
    const jsonCer = await respCer.json();
    const detCer = jsonCer.results?.[0]?.detalle || [];
    if (detCer.length) {
      detCer.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.cerHoy = parseFloat(detCer[0].valor);
      console.log(`CER hoy (banner): ${bcraData.cerHoy} (${detCer[0].fecha})`);
    }
  } catch(e) { console.warn('BCRA CER hoy error:', e); }

  // TAMAR reciente (para panel)
  try {
    const hoyStr = dateToStr(new Date());
    const hace7 = restarDiasHabiles(hoyStr, 7);
    const respT = await fetch(`${BCRA_WORKER}/?serie=tamar&desde=${hace7}&hasta=${hoyStr}`);
    const jsonT = await respT.json();
    const detT = jsonT.results?.[0]?.detalle || [];
    if (detT.length) {
      detT.sort((a,b) => b.fecha.localeCompare(a.fecha));
      bcraData.tamarReciente = parseFloat(detT[0].valor);
    }
  } catch(e) { console.warn('BCRA TAMAR reciente error:', e); }

  // MEP y CCL desde Eco Valores
  try {
    const [rAL30, rAL30D, rAL30C] = await Promise.all([
      fetch(`${ECO_URL}/?ticker=AL30`).then(r=>r.json()),
      fetch(`${ECO_URL}/?ticker=AL30D`).then(r=>r.json()),
      fetch(`${ECO_URL}/?ticker=AL30C`).then(r=>r.json()),
    ]);
    if (rAL30.price && rAL30D.price) bcraData.mep = rAL30.price / rAL30D.price;
    if (rAL30.price && rAL30C.price) bcraData.ccl = rAL30.price / rAL30C.price;
  } catch(e) { console.warn('MEP/CCL error:', e); }

  // Riesgo País
  try {
    const respRP = await fetch(`${ARG_WORKER}/?serie=riesgopais`);
    const jsonRP = await respRP.json();
    if (jsonRP.valor) bcraData.riesgopais = jsonRP.valor;
  } catch(e) { console.warn('Riesgo País error:', e); }

}
