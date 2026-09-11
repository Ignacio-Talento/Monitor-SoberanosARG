/* ═══ TEMA CLARO / OSCURO ═══════════════════════════════════════════════════════════════════
 *
 * Un solo archivo para las doce solapas. Se carga en el <head> de cada página, ANTES de su CSS,
 * para que el tema elegido esté puesto cuando el navegador pinta la primera vez: si se aplicara
 * después, cada carga en oscuro arrancaría con un fogonazo blanco.
 *
 * CÓMO ESTÁ ARMADO. Las solapas ya tienen su paleta en variables (:root), así que el oscuro es
 * sobre todo redefinir esas variables bajo :root[data-tema="oscuro"]. Hay dos cosas que las
 * variables solas no resuelven:
 *
 *  1. --navy CUMPLE DOS PAPELES: es la tinta de los títulos y el fondo de la barra, las solapas y
 *     los botones activos. En oscuro la tinta tiene que ser clara y el fondo seguir siendo navy.
 *     Por eso el CSS de las páginas usa --navy-fondo para los fondos y bordes de navy, --navy-linea
 *     para las rayas de acento, y --navy queda para la tinta; en claro valen las tres lo mismo.
 *
 *  2. LOS GRÁFICOS SE ARMAN EN SVG CON COLORES FIJOS en los atributos (fill="#0d2055"). Un atributo
 *     de presentación de SVG pierde contra cualquier regla de CSS, así que se remapean con
 *     selectores de atributo —[fill="#0d2055"]— sin tocar el código que dibuja. Lo mismo con los
 *     estilos en línea del HTML generado (style="color:#7a4b00"), que necesitan !important.
 *     Ventaja: el cambio de tema es instantáneo y no hay que redibujar ni volver a pedir datos.
 *
 * LOS COLORES SON LOS DE BALANZ (skill balanz-design): fondo navy_deep #01092d, tarjetas en la
 * escala navy, texto claro (#f2f4f8, border_gray #c8d3e0 y steel_gray #a7b2c8) y el cyan de acento
 * sin cambios. Los semánticos se mantienen salvo el rojo, que sobre navy no se lee y pasa a un tono
 * más claro de la misma familia; el teal, el violeta y el azul acero se aclaran por lo mismo.
 *
 * Los colores fijos del CSS de cada página se reemplazaron por variables --k-<hex> que en claro
 * valen exactamente el mismo hex: el tema claro no cambia en nada.
 */
(function () {
  var CLAVE = 'monitor_tema';
  var raiz = document.documentElement;

  function leido() {
    try { return localStorage.getItem(CLAVE) === 'oscuro' ? 'oscuro' : 'claro'; }
    catch (e) { return 'claro'; }
  }
  raiz.setAttribute('data-tema', leido());

  var O = ':root[data-tema="oscuro"]';
  var css = [
    /* ── variables nuevas, en claro iguales al color original ── */
    ':root{--navy-fondo:var(--navy);--navy-linea:var(--navy);color-scheme:light;',
    '--k-e8edf5:#e8edf5;--k-eef2f7:#eef2f7;--k-f2f4f8:#f2f4f8;--k-e6f6fd:#e6f6fd;--k-e2f4fc:#e2f4fc;',
    '--k-fef2f2:#fef2f2;--k-fecaca:#fecaca;--k-f0fdf4:#f0fdf4;--k-86efac:#86efac;--k-dcfce7:#dcfce7;',
    '--k-dbeafe:#dbeafe;--k-fff4e5:#fff4e5;--k-fff7e6:#fff7e6;--k-f0c890:#f0c890;--k-7a4b00:#7a4b00;',
    '--k-fef3c7:#fef3c7;--k-fcd34d:#fcd34d;--k-a0b4d8:#a0b4d8;--k-c5cfe0:#c5cfe0;--k-005f80:#005f80;',
    '--k-002060:#002060;--k-17336f-tx:#17336f;--k-1b9e5a:#1b9e5a;--k-198754:#198754;',
    '--k-c0392b:#c0392b;--k-e08e16:#e08e16;--k-fff:#fff;}',

    /* ── paleta oscura ── */
    O + '{color-scheme:dark;',
    '--navy:#f2f4f8;--navy-fondo:#17336f;--navy-light:#22448a;--navy-deep:#01092d;--navy-soft:#06214e;',
    /* Las rayas de acento en navy (debajo de los encabezados de tabla, al costado de las notas):
       en blanco serían un tajo; van en un navy claro de la misma escala. */
    '--navy-linea:#3b5c9a;',
    '--cyan-bg:#06304f;--bg:#01092d;--surface:#071c4a;--card:#0b2556;--row-hover:#102d63;',
    '--border:#1f3d73;--border-l:#2e4f8a;--text:#f2f4f8;--text-2:#c8d3e0;--text-3:#a7b2c8;',
    '--green:#1b9e5a;--green-bg:#0c3a2a;--green-border:#1b9e5a;--amber:#e08e16;--red:#e5604f;',
    '--blue:#5fa8d3;--purple:#a78bfa;--teal:#2cc7b8;',
    '--sh-card:0 1px 3px rgba(0,0,0,.45),0 1px 2px rgba(0,0,0,.3);--sh-raised:0 4px 16px rgba(0,0,0,.5);',
    '--k-e8edf5:#12295a;--k-eef2f7:#0e2553;--k-f2f4f8:#0b2150;--k-e6f6fd:#06304f;--k-e2f4fc:#06304f;',
    '--k-fef2f2:#3a1418;--k-fecaca:#7f2b2b;--k-f0fdf4:#0c3326;--k-86efac:#1f7a4c;--k-dcfce7:#0c3a2a;',
    '--k-dbeafe:#10306a;--k-fff4e5:#3a2a0c;--k-fff7e6:#3a2a0c;--k-f0c890:#8a6420;--k-7a4b00:#f0c890;',
    '--k-fef3c7:#3a300c;--k-fcd34d:#8a7420;--k-a0b4d8:#2e4f8a;--k-c5cfe0:#1f3d73;--k-005f80:#7fd0ef;',
    '--k-002060:#f2f4f8;--k-17336f-tx:#c8d3e0;--k-1b9e5a:#1b9e5a;--k-198754:#1b9e5a;',
    '--k-c0392b:#e5604f;--k-e08e16:#e08e16;--k-fff:#071c4a;}',

    /* La barra de marca queda en el navy de siempre, un escalón arriba del fondo. */
    O + ' .navbar,' + O + ' .nav-tabs{background:#0d2055;}',
    O + ' body{background:var(--bg);color:var(--text);}',
    O + ' input,' + O + ' select,' + O + ' textarea{background:var(--surface);color:var(--text);border-color:var(--border);}',

    /* ── gráficos SVG: colores fijos en los atributos ── */
    O + ' [fill="#0d2055" i],' + O + ' [fill="#002060" i]{fill:#f2f4f8;}',
    O + ' [stroke="#0d2055" i],' + O + ' [stroke="#002060" i]{stroke:#f2f4f8;}',
    O + ' [fill="#17336f" i]{fill:#c8d3e0;}' + O + ' [stroke="#17336f" i]{stroke:#c8d3e0;}',
    O + ' [fill="#6b7280" i],' + O + ' [fill="#3a4a5c" i]{fill:#a7b2c8;}',
    O + ' [stroke="#6b7280" i]{stroke:#a7b2c8;}',
    O + ' [stroke="#e3e8ef" i],' + O + ' [stroke="#e6ecf3" i],' + O + ' [stroke="#e8edf5" i],' +
      O + ' [stroke="#eef2f7" i],' + O + ' [stroke="#f2f4f8" i]{stroke:#1a3466;}',
    O + ' [stroke="#c8d3e0" i],' + O + ' [stroke="#c5cfe0" i]{stroke:#2a4a80;}',
    O + ' [fill="#c8d3e0" i],' + O + ' [fill="#e8edf5" i],' + O + ' [fill="#e6ecf3" i],' +
      O + ' [fill="#f2f4f8" i],' + O + ' [fill="#f8fafc" i]{fill:#12295a;}',
    O + ' [fill="#fff" i],' + O + ' [fill="#ffffff" i],' + O + ' [fill="white" i]{fill:var(--surface);}',
    O + ' [stroke="#fff" i],' + O + ' [stroke="#ffffff" i],' + O + ' [stroke="white" i]{stroke:var(--surface);}',
    O + ' [fill="#145e81" i]{fill:#5fa8d3;}' + O + ' [stroke="#145e81" i]{stroke:#5fa8d3;}',
    O + ' [fill="#0f4c68" i]{fill:#4f9ac0;}' + O + ' [stroke="#0f4c68" i]{stroke:#4f9ac0;}',
    O + ' [fill="#0d9488" i]{fill:#2cc7b8;}' + O + ' [stroke="#0d9488" i]{stroke:#2cc7b8;}',
    O + ' [fill="#7c3aed" i],' + O + ' [fill="#8b5cf6" i]{fill:#a78bfa;}',
    O + ' [stroke="#7c3aed" i],' + O + ' [stroke="#8b5cf6" i]{stroke:#a78bfa;}',
    O + ' [fill="#c0392b" i],' + O + ' [fill="#dc3545" i]{fill:#e5604f;}',
    O + ' [stroke="#c0392b" i],' + O + ' [stroke="#dc3545" i]{stroke:#e5604f;}',

    /* ── estilos en línea del HTML generado ── */
    O + ' [style*="color:#7a4b00" i],' + O + ' [style*="color: #7a4b00" i]{color:#f0c890 !important;}',
    O + ' [style*="background:#fff4e5" i],' + O + ' [style*="background: #fff4e5" i]{background:#3a2a0c !important;}',
    O + ' [style*="#f0c890" i]{border-color:#8a6420 !important;}',
    O + ' [style*="background:#e8edf5" i],' + O + ' [style*="background: #e8edf5" i]{background:#12295a !important;}',
    O + ' [style*="background:#fef3c7" i]{background:#3a300c !important;}',
    O + ' [style*="background:#fee2e2" i]{background:#3a1418 !important;}',
    O + ' [style*="background:#dcfce7" i]{background:#0c3a2a !important;}',
    O + ' [style*="color:#dc3545" i],' + O + ' [style*="color:#c0392b" i]{color:#e5604f !important;}',
    O + ' [style*="color:#6b7280" i],' + O + ' [style*="color: #6b7280" i]{color:#a7b2c8 !important;}',
    O + ' [style*="color:#0d2055" i],' + O + ' [style*="color: #0d2055" i],' +
      O + ' [style*="color:#002060" i]{color:#f2f4f8 !important;}',
    /* muestras de leyenda con el color de la serie: la línea navy pasa a clara, la muestra también */
    O + ' [style*="background:#0d2055" i],' + O + ' [style*="background: #0d2055" i]{background:#f2f4f8 !important;}',
    O + ' [style*="border-color:#0d2055" i],' + O + ' [style*="border-color: #0d2055" i]{border-color:#f2f4f8 !important;}',
    O + ' [style*="background:#6b7280" i],' + O + ' [style*="background: #6b7280" i]{background:#a7b2c8 !important;}',
    O + ' [style*="background:#145e81" i]{background:#5fa8d3 !important;}',
    O + ' [style*="border-color:#145e81" i]{border-color:#5fa8d3 !important;}',
    O + ' [style*="background:#0d9488" i]{background:#2cc7b8 !important;}',
    O + ' [style*="border-color:#0d9488" i]{border-color:#2cc7b8 !important;}',

    /* ── el botón ── */
    '.tema-btn{display:inline-flex;align-items:center;gap:7px;font:600 11px/1 var(--sans,Arial,sans-serif);',
    'letter-spacing:.08em;text-transform:uppercase;color:rgba(255,255,255,.85);background:transparent;',
    'border:1px solid rgba(255,255,255,.28);border-radius:999px;padding:7px 12px;cursor:pointer;',
    'transition:border-color .15s,color .15s;white-space:nowrap;}',
    '.tema-btn:hover{color:#fff;border-color:#00a3e4;}',
    '.tema-btn svg{width:14px;height:14px;display:block;}',
    '.navbar-der{display:flex;align-items:center;gap:14px;}'
  ].join('\n');

  var st = document.createElement('style');
  st.id = 'tema-css';
  st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  var LUNA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
             '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  var SOL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4' +
            'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';

  function pintarBoton(b) {
    var oscuro = raiz.getAttribute('data-tema') === 'oscuro';
    // El botón muestra A DÓNDE va, no dónde está: en oscuro ofrece el claro y al revés.
    b.innerHTML = (oscuro ? SOL : LUNA) + '<span>' + (oscuro ? 'Claro' : 'Oscuro') + '</span>';
    b.setAttribute('aria-label', oscuro ? 'Pasar al tema claro' : 'Pasar al tema oscuro');
    b.setAttribute('aria-pressed', oscuro ? 'true' : 'false');
  }

  function alternar() {
    var nuevo = raiz.getAttribute('data-tema') === 'oscuro' ? 'claro' : 'oscuro';
    raiz.setAttribute('data-tema', nuevo);
    try { localStorage.setItem(CLAVE, nuevo); } catch (e) { /* sin almacenamiento: vale para esta vista */ }
    var b = document.querySelector('.tema-btn');
    if (b) pintarBoton(b);
    try { window.dispatchEvent(new CustomEvent('temacambio', { detail: nuevo })); } catch (e) { /* viejo */ }
  }

  function montar() {
    if (document.querySelector('.tema-btn')) return;
    var barra = document.querySelector('.navbar');
    if (!barra) return;
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'tema-btn';
    b.addEventListener('click', alternar);
    pintarBoton(b);
    // Queda a la derecha de la barra. Donde ya hay un indicador de estado a la derecha
    // (Monitor, ONs), van juntos en un mismo grupo para que space-between no los separe.
    var estado = barra.querySelector('.navbar-status');
    if (estado) {
      var grupo = document.createElement('div');
      grupo.className = 'navbar-der';
      estado.parentNode.insertBefore(grupo, estado);
      grupo.appendChild(estado);
      grupo.appendChild(b);
    } else {
      barra.appendChild(b);
    }
  }

  // Otra pestaña del monitor cambió el tema: ésta lo sigue.
  window.addEventListener('storage', function (e) {
    if (e.key !== CLAVE) return;
    raiz.setAttribute('data-tema', leido());
    var b = document.querySelector('.tema-btn');
    if (b) pintarBoton(b);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', montar);
  else montar();
})();
