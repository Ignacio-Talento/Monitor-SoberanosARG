#!/usr/bin/env python3
"""Genera las curvas del informe diario como PNG, con la identidad de Balanz.

POR QUÉ PNG Y POR QUÉ VAN POR LINK. El informe va por mail y el envío de Gmail sanitiza el HTML:
borra TODA etiqueta <img>, en cualquier variante —suelta, con style, dentro de <a>, dentro de
<table>— y también las propiedades de fondo. Verificado el 28/08/2026 releyendo el mensaje ya
entregado en el servidor. Así que las curvas no se incrustan: se publican en GitHub Pages junto con
una página índice (pagina_curvas.py) y el mail linkea a esa página. SVG tampoco sirve, ni siquiera
ahí, porque algunos clientes lo descartan.

QUÉ CURVAS. Nueve, cada una con la métrica y la moneda en la que se negocia:

  1. Globales contra Bonares — LAS DOS PATAS EN MEP. Es la única forma de que el spread signifique
     algo: el monitor valúa los globales al CCL y los bonares al MEP, y restarlos así mezcla dos
     monedas. La solapa Glob vs Bon resuelve lo mismo descartando lo que no esté en MEP.
  2. LECAPs en TEM — la tasa mensual, que es como se cotiza el tramo corto en la mesa.
  3. CER — TIR real, o sea el "CER más x%" que paga cada bono.
  4. LECAPs contra CER — las dos curvas, cada una en su escala.
  5. Breakeven de inflación — la que iguala a las dos anteriores.
  5. TAMAR — TEA.
  6. Dólar linked — TIR.
  7. Subsoberanos — TIR al CCL.
  8. Futuros de dólar — precio y devaluación acumulada contra el mayorista, en dos ejes.

NO HAY GRÁFICO DE ONs. Se hicieron y se sacaron: con cincuenta y pico de corporativos el gráfico es
una nube de emisores distintos, no una curva —entre YPF a tres años y Pampa a cuatro no hay nada que
interpolar—, y como nube aportaba menos que las medianas de la tabla. La función curva_ons() queda
por si alguna vez se quiere mirar un subconjunto emparejado por emisor, que sí tendría sentido.

LOS DUALES ENTRAN CON SU PATA, no con la tasa del instrumento entero. 1816 publica un ticker por
pata —"TXMD8 @CER" y "TXMD8 @TAMAR"— y cada uno devuelve la tasa de la suya: 6,04% real y 40,53%
nominal para el mismo bono. El ticker pelado devuelve la de la pata que domina, y no es la misma en
todos: TXMJ8 informa su pata CER y TXMD8 su pata TAMAR.

Van con otro símbolo igual, porque un dual no es un bono puro: se cobra el máximo entre las dos
patas, así que su pata CER rinde menos que un CER equivalente —esa diferencia es el precio de la
opcionalidad— y dibujarlos como un punto más de la curva sugeriría que son sustitutos.
"""
import json
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

SKILL = (r"C:\Users\Usuario\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin"
         r"\b8cdd89c-febc-4430-ac7d-eb55f2fb0c82\032bf20c-fc9a-48c9-80cf-4f4994992f7e"
         r"\skills\balanz-design\assets")
if Path(SKILL).exists():
    sys.path.insert(0, SKILL)

try:
    from balanz_style import COLORS, apply_balanz_mpl, balanz_figure
    apply_balanz_mpl()
    BRAND = True
except Exception as e:                                            # noqa: BLE001
    print(f"AVISO: sin balanz_style ({e}); se usan los colores de marca a mano")
    COLORS = {"navy": "#002060", "cyan": "#00B0F0", "label_gray": "#6B7280",
              "border_gray": "#C8D3E0", "steel_gray": "#A7B2C8", "blue_steel": "#145E81",
              "conservador": "#1B9E5A", "moderado": "#E08E16", "agresivo": "#C0392B"}
    BRAND = False

    def balanz_figure(figsize=(10, 5.6)):
        return plt.subplots(figsize=figsize, facecolor="white")

try:
    from adjustText import adjust_text
except Exception:                                                 # noqa: BLE001
    adjust_text = None
    print("AVISO: sin adjustText; los rótulos se dibujan sin separar")

# CUERPOS. La figura sale a 950 px y se muestra en 674, así que en la página se ven al 71%: lo que
# acá dice 10 se lee como 7 sobre el papel. Están calibrados contra el cuerpo del informe, que es 9.
TAM_TITULO = 18
TAM_EJE = 13
TAM_TICK = 12
TAM_LEYENDA = 13
TAM_ROTULO = 12
TAM_NOTA = 11

NAVY = COLORS["navy"]
CYAN = COLORS["cyan"]
GRIS = COLORS["label_gray"]
ACERO = COLORS.get("blue_steel", "#145E81")
VERDE = COLORS.get("conservador", "#1B9E5A")
AMBAR = COLORS.get("moderado", "#E08E16")


def _puntos(instr, familia, campo="tea", en_mep=False):
    """[(duration en años, tasa, ticker)] ordenado por duration.

    `en_mep=True` devuelve TODOS los instrumentos de la familia en esa punta, no sólo los que
    tienen el campo enMep. Hace falta la distinción para las ONs de ley argentina: 47 se valúan en
    MEP de origen y 6 en CCL, porque pagan en cable. Tomando sólo enMep quedarían esas 6 y el
    gráfico mostraría la excepción en vez de la familia; tomando sólo la tasa nativa, esas 6
    entrarían en otra moneda. Se usa enMep donde existe y la nativa —que ya es MEP— donde no.
    """
    out = []
    for r in instr:
        if r["familia"] != familia or r.get("durationMod") is None:
            continue
        if en_mep:
            m = r.get("enMep") or {}
            v = m.get(campo) if m else (r.get(campo) if r.get("moneda") == "mep" else None)
        else:
            v = r.get(campo)
        if v is None:
            continue
        out.append((r["durationMod"], v, r["ticker"]))
    return sorted(out)


def _patas(instr, tipo):
    """[(duration, tasa, ticker)] de la pata pedida de cada dual.

    Usa la duration DE LA PATA, que no es la del instrumento: la pata CER de TXMD8 tiene 2,22 años
    y la TAMAR 1,82, porque cada una descuenta un flujo distinto.
    """
    out = []
    for r in instr:
        if r["familia"] != "Duales":
            continue
        p = (r.get("patas") or {}).get(tipo)
        if not p or p.get("tea") is None or p.get("durationMod") is None:
            continue
        out.append((p["durationMod"], p["tea"], r["ticker"]))
    return sorted(out)


def _ejes(ax, titulo, ylab, xlab="Duration modificada (años)"):
    ax.set_title(titulo, color=NAVY, fontweight="bold", fontsize=TAM_TITULO, pad=14)
    ax.set_xlabel(xlab, color=GRIS, fontsize=TAM_EJE)
    ax.set_ylabel(ylab, color=GRIS, fontsize=TAM_EJE)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    ax.grid(True, color=COLORS.get("border_gray", "#C8D3E0"), alpha=.5, linewidth=.8)
    ax.set_axisbelow(True)
    # Aire en los bordes para que el rotulo del primer y del ultimo punto no quede pegado al eje.
    ax.margins(x=.08, y=.05)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(colors=GRIS, labelsize=TAM_TICK)


def _etiquetar(ax, pts, color, cada=1, tam=TAM_ROTULO):
    """Crea los rótulos y los deja anotados en el eje; la posición la fija _acomodar()."""
    guardados = getattr(ax, "_rotulos", None)
    if guardados is None:
        guardados = ax._rotulos = []
        ax._anclas = []
    ultimo = len(pts) - 1
    for i, (x, y, tk) in enumerate(pts):
        ax._anclas.append((x, y))
        # Los extremos van siempre: definen el rango de la curva y son los dos que uno busca.
        # Sin esto, saltear de a  dejaba sin nombre al bono mas largo cuando la cantidad de
        # puntos hacia que le tocara indice impar -CUAP, a 9,5 de duration, en la curva CER-.
        if i % cada and i not in (0, ultimo):
            continue
        guardados.append(ax.text(x, y, tk, fontsize=tam, color=color, alpha=.95,
                                 ha="center", va="bottom", zorder=5))


def _acomodar(ax):
    """Separa los rótulos que se pisan entre sí y de los puntos de la curva.

    Corre al final a propósito: adjustText trabaja en coordenadas de pantalla y necesita que los
    ejes ya tengan sus límites, que hasta que no se dibujan todas las series no están definidos.
    """
    textos = getattr(ax, "_rotulos", None)
    if not textos or adjust_text is None:
        return
    anclas = getattr(ax, "_anclas", [])
    adjust_text(
        textos, ax=ax,
        x=[a[0] for a in anclas], y=[a[1] for a in anclas],
        expand=(1.25, 1.45),
        force_text=(.7, 1.0), force_static=(.45, .8), force_pull=(.003, .003),
        force_explode=(.35, .8),
        # Margen amplio de desplazamiento: en el tramo corto hay cinco instrumentos en una decima
        # de duration y el rotulo tiene que poder subir hasta la franja vacia para no encimarse.
        max_move=(75, 75),
        arrowprops=dict(arrowstyle="-", color=COLORS.get("border_gray", "#C8D3E0"),
                        lw=.7, shrinkA=1, shrinkB=3),
        min_arrow_len=7)


def _serie(ax, pts, color, rotulo, marcador="o", linea="-", etiquetas=True, cada=1,
           tam=TAM_ROTULO):
    """Una serie de la curva. `linea=""` deja los puntos SUELTOS, sin unir.

    Hace falta distinguirlo: matplotlib interpreta la cadena vacía como «formato por defecto» y
    dibuja la línea igual, así que los duales aparecían encadenados como si formaran una curva
    propia. No la forman —son instrumentos con otra estructura que se muestran sobre la curva del
    puro para poder ubicarlos— y unirlos sugiere una interpolación que no existe.
    """
    if not pts:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ax.plot(xs, ys, color=color, linewidth=2 if linea else 0,
            linestyle=linea if linea else "none",
            marker=marcador, markersize=7, label=rotulo, zorder=3)
    if etiquetas:
        _etiquetar(ax, pts, color, cada, tam=tam)


# EL PESO IMPORTA POR EL PDF que se adjunta al mail: ocho imágenes pesadas lo vuelven inadjuntable.
# A 100 dpi con paleta indexada quedan en unos 15 KB cada uno contra 100 sin comprimir, y no se
# nota: son líneas y texto sobre fondo plano, sin degradados que sufran la cuantización. 100 dpi da
# ~950 px de ancho, más de lo que muestra cualquier lector, así que se ve nítido igual.
DPI = 100


def _comprimir(ruta):
    """Reduce el PNG a paleta indexada. Si Pillow no está, se deja como salió."""
    try:
        from PIL import Image
        im = Image.open(ruta).convert("RGB")
        im.quantize(colors=64, method=Image.MEDIANCUT).save(ruta, optimize=True)
    except Exception:                                             # noqa: BLE001
        pass
    return ruta


# Caracteres por línea del pie. A cuerpo 8 sobre una figura de 9,5 pulgadas, 108 entran holgados;
# más que eso y `bbox_inches="tight"` empieza a ensanchar la figura para que el texto entre, con lo
# que esa curva sale con otra proporción que el resto del juego.
ANCHO_NOTA = 78


def falta(d, *familias):
    """Frase para el pie: cuántos instrumentos del panel no operaron, y cuáles.

    Sin esto la curva se lee como si fuera el panel entero. En subsoberanos eso es la mitad —cinco
    de once el 28/08/2026—, y con paneles chicos saber CUÁL falta importa tanto como cuántos.
    """
    ausentes = []
    for f in familias:
        ausentes += (d.get("sinDatoPorFamilia") or {}).get(f, [])
    if not ausentes:
        return ""
    presentes = sum((d["resumen"].get(f) or {}).get("instrumentos", 0) for f in familias)
    quienes = ", ".join(ausentes[:8]) + (", …" if len(ausentes) > 8 else "")
    return (f"\nEstán los {presentes} de {presentes + len(ausentes)} que operaron; "
            f"no cotizaron {quienes}.")



def _nota(ax, texto, y=-.16):
    """Dibuja el pie del gráfico cortado a ANCHO_NOTA, respetando los saltos que ya traiga."""
    lineas = []
    for parrafo in str(texto).split("\n"):
        lineas.extend(textwrap.wrap(parrafo, ANCHO_NOTA) or [""])
    ax.text(.01, y, "\n".join(lineas), transform=ax.transAxes, fontsize=TAM_NOTA,
            color=GRIS, va="top")


def _cerrar(fig, ax, ruta, leyenda=True):
    _acomodar(ax)
    if leyenda:
        ax.legend(frameon=False, fontsize=TAM_LEYENDA, labelcolor=NAVY)
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return _comprimir(ruta)


# ── 1 · Globales contra Bonares ──────────────────────────────────────────────
def globales_vs_bonares(instr, salida, faltan=""):
    bon = _puntos(instr, "Bonares")
    glo = _puntos(instr, "Globales", en_mep=True)
    if not (bon and glo):
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, bon, NAVY, "Bonares · ley local")
    _serie(ax, glo, CYAN, "Globales · ley NY", marcador="s")
    _ejes(ax, "Curva soberana en dólares · ley local contra ley NY", "TIR (%)")
    _nota(ax, "Ambas curvas en MEP. Los globales se llevan a esa punta a propósito: se " "negocian al CCL y restar dos monedas daría un spread que no existe." + faltan)
    return _cerrar(fig, ax, salida)


# ── 2 · LECAPs en TEM ────────────────────────────────────────────────────────
def lecaps_tem(instr, salida, faltan=""):
    pts = [(d, ((1 + t / 100) ** (1 / 12) - 1) * 100, tk)
           for d, t, tk in _puntos(instr, "LECAPs y tasa fija")]
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "LECAPs y tasa fija")
    _ejes(ax, "Curva de pesos a tasa fija · TEM", "TEM (%)")
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.2f%%"))
    _nota(ax, "La TEM se deriva de la TEA informada: (1 + TEA)^(1/12) − 1." + faltan)
    return _cerrar(fig, ax, salida)


# ── 3 · CER ──────────────────────────────────────────────────────────────────
def curva_cer(instr, salida, duales_cer, faltan=""):
    pts = _puntos(instr, "CER")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "CER · TIR real")
    if duales_cer:
        _serie(ax, duales_cer, AMBAR, "Duales · pata CER", marcador="D", linea="")
    _ejes(ax, "Curva CER · rendimiento real sobre el índice", "CER + x % (TIR real)")
    ax.axhline(0, color=GRIS, linewidth=.9, linestyle=":", zorder=1)
    nota = ("Cada punto es el «CER más x%» que paga el bono. El tramo ultracorto puede dar "
            "negativo, que es normal cuando el mercado paga por cobertura inmediata.")
    if duales_cer:
        nota += ("\nLos rombos son la PATA CER de cada dual, no el instrumento entero. Rinden "
                 "menos que un CER puro de la misma duration: en un dual se cobra el máximo entre "
                 "las dos patas, y esa opcionalidad se paga.")
    _nota(ax, nota + faltan)
    return _cerrar(fig, ax, salida)


# ── 4 · LECAPs contra CER ────────────────────────────────────────────────────
def _cer_contra_fija(instr, dudosos_cer):
    """Datos compartidos por las dos figuras: las curvas, el breakeven y el recorte del eje x."""
    lec = _puntos(instr, "LECAPs y tasa fija")
    cer = [p for p in _puntos(instr, "CER") if p[2] not in dudosos_cer]
    if not (lec and cer):
        return None

    def interp(curva, x):
        if not curva or x < curva[0][0] or x > curva[-1][0]:
            return None
        for i in range(1, len(curva)):
            a, b = curva[i - 1], curva[i]
            if a[0] <= x <= b[0]:
                w = 0 if b[0] == a[0] else (x - a[0]) / (b[0] - a[0])
                return a[1] + w * (b[1] - a[1])
        return None

    bei = []
    for d, tr, tk in cer:
        tn = interp([(x, y) for x, y, _ in lec], d)
        if tn is not None:
            bei.append((d, ((1 + tn / 100) / (1 + tr / 100) - 1) * 100, tk))

    # SÓLO EL TRAMO DONDE HAY LECAPs, como hace la solapa Sendero CER. Los CER llegan a nueve años
    # de duration —PARP, CUAP— y la tasa fija se termina en dos: dibujando todo, las LECAPs quedan
    # apretadas contra el margen izquierdo y no se lee nada. Además, más allá del último punto de
    # tasa fija no hay contra qué comparar, así que ese tramo no pertenece a este gráfico.
    #
    # El gráfico del breakeven sí usa todos los CER: interpolar contra la curva de LECAPs ya
    # descarta solo los que quedan fuera de su rango.
    xmax = max(x for x, _, _ in lec) * 1.08
    cer_vis = [p for p in cer if p[0] <= xmax]
    return lec, cer_vis, bei, len(cer) - len(cer_vis), xmax


def lecaps_vs_cer(instr, salida, dudosos_cer, infl_anual=None):
    """Tasa fija contra CER, las dos en TEA nominal sobre un solo eje.

    Los CER se convierten con `infl_anual`, la inflación publicada anualizada. Sin ese dato el
    gráfico no se dibuja: inventar la inflación de conversión sería inventar la comparación.
    """
    datos = _cer_contra_fija(instr, dudosos_cer)
    if not datos:
        return None
    if infl_anual is None:
        print("    (sin dato de inflación: no se puede llevar los CER a nominal)")
        return None
    lec, cer_vis, _bei, _fuera, xmax = datos
    cer_nom = [(d, ((1 + infl_anual / 100) * (1 + r / 100) - 1) * 100, tk) for d, r, tk in cer_vis]

    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, lec, NAVY, "LECAPs · TEA nominal")
    _serie(ax, cer_nom, ACERO, "Bonos CER · CER + TIR", marcador="s")
    _ejes(ax, "Tasa fija contra CER", "TEA (%)")
    ax.set_xlim(-xmax * .09, xmax)
    ax.legend(frameon=False, fontsize=TAM_LEYENDA, labelcolor=NAVY, loc="lower right")
    _nota(ax, "Los CER están llevados a tasa nominal con la inflación efectivamente publicada: los "
              f"tres últimos meses del IPC anualizados dan {infl_anual:.1f}%.\nNo es lo que el "
              "mercado espera —eso es el breakeven del gráfico siguiente, bastante más bajo—, sino "
              "cuánto rendiría cada CER si la inflación se mantuviera en el ritmo actual. Que la "
              "curva CER corra por encima es esa diferencia.")
    # Por _cerrar y no por savefig directo: es el que llama a _acomodar. Guardando a mano, los
    # rotulos quedaban donde cayeron.
    return _cerrar(fig, ax, salida, leyenda=False)


def breakeven_cer(instr, salida, dudosos_cer):
    datos = _cer_contra_fija(instr, dudosos_cer)
    if not datos:
        return None
    _lec, _cer_vis, bei, fuera, _xmax = datos
    if not bei:
        return None

    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, bei, CYAN, "Inflación breakeven", etiquetas=False)
    _ejes(ax, "Inflación que iguala las dos curvas", "Breakeven anual (%)")
    nota = ("El breakeven es la inflación a la que una LECAP y un CER del mismo plazo rinden lo "
            "mismo: por encima conviene el CER, por debajo la tasa fija.\nCada CER se compara "
            "contra la LECAP interpolada a su misma duration, no contra la más cercana.")
    if fuera:
        nota += (f"\nQuedan {fuera} CER largos afuera —hasta nueve años de duration—: más allá del "
                 f"último punto de tasa fija no hay contra qué compararlos.")
    _nota(ax, nota)
    return _cerrar(fig, ax, salida)


# ── 5 · TAMAR ────────────────────────────────────────────────────────────────
def curva_tamar(instr, salida, duales_tamar, tamar_bcra=None, faltan=""):
    pts = _puntos(instr, "TAMAR")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "TAMAR · TEA")
    if duales_tamar:
        _serie(ax, duales_tamar, AMBAR, "Duales · pata TAMAR", marcador="D", linea="")
    if tamar_bcra:
        ax.axhline(tamar_bcra, color=VERDE, linewidth=1.4, linestyle="--", zorder=2,
                   label=f"TAMAR spot BCRA · {tamar_bcra:.2f}%")
    _ejes(ax, "Curva TAMAR", "TEA (%)")
    nota = ""
    if tamar_bcra:
        nota = ("La línea es la TAMAR de bancos privados que publica el BCRA, con dos días hábiles "
                "de rezago: es el nivel spot contra el que se paran los bonos.")
    if duales_tamar:
        extra = ("Los rombos son la PATA TAMAR de cada dual, no el instrumento entero: rinden "
                 "menos que un TAMAR puro porque incluyen el costo de la opcionalidad.")
        nota = (nota + "\n" + extra) if nota else extra
    if nota:
        _nota(ax, nota + faltan)
    return _cerrar(fig, ax, salida)


# ── 6 · Dólar linked ─────────────────────────────────────────────────────────
def curva_dl(instr, salida, faltan=""):
    pts = _puntos(instr, "Dólar linked")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "Dólar linked · TIR")
    _ejes(ax, "Curva dólar linked", "TIR (%)")
    ax.axhline(0, color=GRIS, linewidth=.9, linestyle=":", zorder=1)
    _nota(ax, "Rendimiento por encima de la devaluación oficial. Son pocos instrumentos y " "algunos muy ilíquidos, así que la curva es indicativa." + faltan)
    return _cerrar(fig, ax, salida)


# ── 7 · Subsoberanos ─────────────────────────────────────────────────────────
def curva_subsoberanos(instr, salida, faltan=""):
    pts = _puntos(instr, "Subsoberanos")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    # Son cinco puntos en todo el ancho del gráfico: el rótulo de 7,5 que sirve para una curva de
    # veinte instrumentos acá queda diminuto sin ninguna razón.
    _serie(ax, pts, NAVY, "Subsoberanos · TIR")
    _ejes(ax, "Curva subsoberana en dólares", "TIR (%)")
    _nota(ax, "Valuados al CCL. Son provincias " "con riesgos crediticios distintos entre sí, no una curva de un solo emisor: " "la línea ordena por plazo, no dice que sean sustitutos." + faltan)
    return _cerrar(fig, ax, salida)


# ── 8 y 9 · ONs por legislación ──────────────────────────────────────────────
def curva_ons(instr, salida, familia, titulo, moneda, en_mep=False):
    """Dispersión, no curva: cada punto es un emisor distinto.

    Unir estos puntos con una línea sería el error de fondo del gráfico. En la curva soberana la
    línea tiene sentido porque es UN emisor a distintos plazos, y el trazo entre dos puntos es la
    tasa que ese emisor pagaría a un plazo intermedio. Acá cada punto es una empresa con su propio
    riesgo: entre YPF a tres años y Pampa a cuatro no hay nada que interpolar, y el trazo sugeriría
    una estructura temporal que no existe.
    """
    pts = _puntos(instr, familia, en_mep=en_mep)
    if len(pts) < 2:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, f"{familia} · TIR", linea="", etiquetas=False)

    # Con cincuenta y pico de instrumentos no entran todas las etiquetas. Se rotulan los extremos
    # de cada tramo —el que más rinde y el que menos, cada dos años de duration— que son los que
    # uno quiere identificar de un vistazo.
    marcados = []
    for corte in range(0, 12, 2):
        tramo = [p for p in pts if corte <= p[0] < corte + 2]
        if tramo:
            marcados.append(max(tramo, key=lambda p: p[1]))
            if len(tramo) > 1:
                marcados.append(min(tramo, key=lambda p: p[1]))

    _ejes(ax, titulo, "TIR (%)")

    # RECORTE DEL EJE Y. En una nube de cincuenta corporativos siempre hay alguno muy ilíquido o
    # muy castigado que se va al 25% y deja al resto apretado en una banda de dos centímetros.
    # Se acota la escala al grueso y se DICE cuántos quedaron fuera con su nombre: esconderlos
    # sería peor que el gráfico ilegible.
    ys = sorted(y for _, y, _ in pts)
    if len(ys) >= 8:
        q1, q3 = ys[len(ys) // 4], ys[3 * len(ys) // 4]
        rango = max(q3 - q1, 0.5)
        lo, hi = q1 - 2.5 * rango, q3 + 2.5 * rango
        afuera = [(x, y, tk) for x, y, tk in pts if not (lo <= y <= hi)]
        if afuera:
            ax.set_ylim(min(lo, ys[0] if ys[0] >= lo else lo), hi)
    else:
        afuera = []

    nota = [f"Valuadas al {moneda}" + (
        ", pidiéndolas en esa punta aunque el monitor muestre algunas al CCL: sin eso las que"
        " pagan en cable no serían comparables con el resto." if en_mep else
        ", la punta en la que las muestra el monitor.")]
    nota.append(f"Son {len(pts)} emisores distintos, así que van como puntos sueltos: entre dos "
                "empresas a plazos parecidos no hay nada que interpolar.")
    if afuera:
        nota.append("Fuera de escala: "
                    + ", ".join(f"{tk} al {y:.1f}%" for _, y, tk in sorted(afuera, key=lambda p: -p[1]))
                    + ". Suelen ser los más ilíquidos, en una punta o en la otra.")
    # Se rotula después de fijar la escala, y sin los que quedaron afuera: su etiqueta caería en
    # el borde del gráfico apuntando a un punto que no se ve.
    fuera = {tk for _, _, tk in afuera}
    _etiquetar(ax, [p for p in marcados if p[2] not in fuera], NAVY)

    _nota(ax, "\n".join(nota))
    return _cerrar(fig, ax, salida)


# ── 8 · Futuros de dólar ─────────────────────────────────────────────────────
MESES_A3 = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}

# Un contrato con volumen casi nulo tiene ajuste igual, pero ese ajuste lo pone la cámara, no el
# mercado. Dibujarlo con el mismo marcador que el AGO26, que opera 863.000 contratos, sugiere que
# los dos precios tienen la misma información atrás. Se marcan huecos a partir de este umbral.
VOL_MINIMO = 1000


def _orden_contrato(sym):
    mes = sym.split("/")[-1]
    return (2000 + int(mes[3:]), MESES_A3[mes[:3]])


def curva_futuros(salida, ruta_spreads="spreads_sinteticos.json", hasta=None):
    """Precio de los futuros de dólar y devaluación acumulada contra el mayorista.

    DOS EJES, UNA SOLA CURVA. La devaluación acumulada es (F/S − 1)·100: una función lineal del
    precio, con S fijo. Dibujarla como segunda línea daría exactamente la misma forma desplazada,
    dos veces el mismo dato. Va como eje derecho de la misma curva, que es lo que el eje secundario
    resuelve bien: el mismo punto se lee en pesos a la izquierda y en porcentaje a la derecha.

    Se toma la última rueda EN O ANTES de `hasta`, que es la fecha del informe. En la corrida de
    las 17:30 eso da la rueda ANTERIOR: A3 publica el ajuste después del clearing, varias horas
    después del cierre, así que el del día todavía no existe. Al reconstruir un informe viejo, en
    cambio, el ajuste de ese día ya está y se usa ese —si no, un informe del 31/08 saldría con los
    futuros del 01/09—.

    El spot que se usa es el mayorista de esa MISMA rueda: mezclar el futuro de un día con el spot
    de otro metería el movimiento cambiario en el numerador y no en el denominador.
    """
    d = json.loads(Path(ruta_spreads).read_text(encoding="utf-8"))
    ruedas = sorted(k for k in d if not k.startswith("_"))
    if hasta:
        ruedas = [r for r in ruedas if r <= hasta]
    if not ruedas:
        return None
    ult = ruedas[-1]
    fut = d[ult].get("fut") or {}
    vol = d[ult].get("vol") or {}
    spot = d[ult].get("tc")
    if not fut or not spot:
        return None

    syms = sorted(fut, key=_orden_contrato)
    xs = list(range(len(syms)))
    ys = [fut[s] for s in syms]
    etiquetas = [f"{s.split('/')[-1][:3]}\n{s.split('/')[-1][3:]}" for s in syms]
    liquidos = [i for i, s in enumerate(syms) if (vol.get(s) or 0) >= VOL_MINIMO]
    finos = [i for i in xs if i not in liquidos]

    fig, ax = balanz_figure(figsize=(10, 5.6))
    ax.plot(xs, ys, color=NAVY, linewidth=2, zorder=3)
    ax.plot([xs[i] for i in liquidos], [ys[i] for i in liquidos], linestyle="none",
            marker="o", markersize=7, color=NAVY, zorder=4, label="Con volumen")
    if finos:
        ax.plot([xs[i] for i in finos], [ys[i] for i in finos], linestyle="none",
                marker="o", markersize=7, markerfacecolor="white", markeredgecolor=NAVY,
                markeredgewidth=1.6, zorder=4, label=f"Menos de {VOL_MINIMO:,} contratos"
                .replace(",", "."))

    ax.axhline(spot, color=CYAN, linestyle="--", linewidth=1.5, zorder=2)
    # Al extremo DERECHO de la linea punteada: sobre la izquierda se pisaba con la devaluacion
    # del contrato mas corto, que por definicion esta pegado al spot.
    ax.annotate(f"Mayorista  {spot:,.2f}".replace(",", "@").replace(".", ",").replace("@", "."),
                (xs[-1], spot), textcoords="offset points", xytext=(0, 7),
                ha="right", fontsize=TAM_EJE, color=CYAN, fontweight="bold")

    # El precio arriba del punto y la devaluación abajo. Antes sólo estaba el porcentaje, y el
    # precio del contrato —que es la serie que dibuja la curva y lo primero que se busca en un
    # gráfico de futuros— había que leerlo del eje.
    for x, y in zip(xs, ys):
        dev = (y / spot - 1) * 100
        ax.annotate(f"{y:,.0f}".replace(",", "."), (x, y), textcoords="offset points",
                    xytext=(0, 11), ha="center", fontsize=TAM_ROTULO, color=NAVY,
                    fontweight="semibold")
        ax.annotate(f"{dev:+.1f}%".replace(".", ","), (x, y), textcoords="offset points",
                    xytext=(0, -19), ha="center", fontsize=TAM_ROTULO - 1.5, color=GRIS)

    ax.set_title(f"Futuros de dólar · ajuste del {ult[8:10]}/{ult[5:7]}",
                 color=NAVY, fontweight="bold", fontsize=TAM_TITULO, pad=14)
    ax.set_xlabel("Vencimiento del contrato", color=GRIS, fontsize=TAM_EJE)
    ax.set_ylabel("Precio del futuro (ARS)", color=GRIS, fontsize=TAM_EJE)
    ax.set_xticks(xs)
    ax.set_xticklabels(etiquetas, fontsize=TAM_TICK)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(
        lambda v, _: f"{v:,.0f}".replace(",", ".")))
    ax.grid(True, color=COLORS.get("border_gray", "#C8D3E0"), alpha=.5, linewidth=.8)
    ax.set_axisbelow(True)
    for lado in ("top",):
        ax.spines[lado].set_visible(False)
    ax.tick_params(colors=GRIS, labelsize=TAM_TICK)

    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * .12
    ax.set_ylim(lo - pad * 1.6, hi + pad)

    # El eje derecho es la misma escala transformada, no otra serie: cada tick es el precio de la
    # izquierda expresado como devaluación acumulada contra el spot.
    der = ax.secondary_yaxis("right", functions=(lambda v: (v / spot - 1) * 100,
                                                 lambda p: spot * (1 + p / 100)))
    der.set_ylabel("Devaluación acumulada contra el mayorista", color=GRIS,
               fontsize=TAM_EJE)
    der.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    der.tick_params(colors=GRIS, labelsize=TAM_TICK)

    ax.legend(frameon=False, fontsize=TAM_LEYENDA, labelcolor=NAVY, loc="upper left")
    fig.savefig(salida, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return _comprimir(salida)


def generar(ruta_json, dir_salida="curvas"):
    d = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
    instr = d["instrumentos"]
    dud_cer = set(d["resumen"].get("CER", {}).get("convencionDudosa") or [])
    dud_dua = set(d["resumen"].get("Duales", {}).get("convencionDudosa") or [])

    # Cada dual va a su curva con SU pata, pedida a 1816 por separado. Antes esto era una
    # heurística sobre la escala de la tasa del instrumento entero, que ubicaba los puntos donde
    # parecían caber; ahora es el dato.
    d_cer = _patas(instr, "CER")
    d_tam = _patas(instr, "TAMAR")

    tamar_spot = (d.get("macro", {}).get("series", {}).get("tamarTEA") or {}).get("valor")
    infl = (d.get("macro", {}).get("inflacion") or {}).get("anualizada3m")

    out = Path(dir_salida)
    out.mkdir(exist_ok=True)
    hechos = {}
    for nombre, fn in [
        ("globales_bonares", lambda p: globales_vs_bonares(instr, p, falta(d, "Bonares", "Globales"))),
        ("lecaps_tem", lambda p: lecaps_tem(instr, p, falta(d, "LECAPs y tasa fija"))),
        ("cer", lambda p: curva_cer(instr, p, d_cer, falta(d, "CER"))),
        ("lecaps_cer", lambda p: lecaps_vs_cer(instr, p, dud_cer, infl)),
        ("breakeven", lambda p: breakeven_cer(instr, p, dud_cer)),
        ("tamar", lambda p: curva_tamar(instr, p, d_tam, tamar_spot, falta(d, "TAMAR"))),
        ("dl", lambda p: curva_dl(instr, p, falta(d, "Dólar linked"))),
        ("subsoberanos", lambda p: curva_subsoberanos(instr, p, falta(d, "Subsoberanos"))),
        ("futuros", lambda p: curva_futuros(p, hasta=d["fecha"])),
    ]:
        ruta = out / f"{nombre}.png"
        try:
            r = fn(str(ruta))
            if r:
                hechos[nombre] = str(ruta)
                print(f"  {nombre}: {ruta.stat().st_size // 1024} KB")
            else:
                print(f"  {nombre}: sin datos suficientes")
        except Exception as e:                                    # noqa: BLE001
            print(f"  {nombre}: FALLÓ ({e})")

    # Las curvas viajan al mail por link, no incrustadas: el envío de Gmail descarta los <img>.
    try:
        from pagina_curvas import escribir
        print(f"  página: {escribir(out, hechos, d['fecha'])}")
    except Exception as e:                                        # noqa: BLE001
        print(f"  página: FALLÓ ({e})")
    return hechos


if __name__ == "__main__":
    print(f"Open Sans registrada: {BRAND}")
    generar(sys.argv[1] if len(sys.argv) > 1 else "inf_tmp.json")
