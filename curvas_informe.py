#!/usr/bin/env python3
"""Genera las curvas del informe diario como PNG, con la identidad de Balanz.

POR QUÉ PNG Y NO SVG. El informe va por mail y Gmail no renderiza SVG inline: lo descarta sin
avisar y el lector ve un hueco. Los PNG van adjuntos inline y se ven en todos los clientes.

QUÉ CURVAS. Las seis que se miran a diario, cada una con la métrica en la que se negocia:

  1. Globales contra Bonares — LAS DOS PATAS EN MEP. Es la única forma de que el spread signifique
     algo: el monitor valúa los globales al CCL y los bonares al MEP, y restarlos así mezcla dos
     monedas. La solapa Glob vs Bon resuelve lo mismo descartando lo que no esté en MEP.
  2. LECAPs en TEM — la tasa mensual, que es como se cotiza el tramo corto en la mesa.
  3. CER — TIR real, o sea el "CER más x%" que paga cada bono.
  4. LECAPs contra CER — las dos curvas juntas más el breakeven de inflación que las iguala.
  5. TAMAR — TEA.
  6. Dólar linked — TIR.

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

NAVY = COLORS["navy"]
CYAN = COLORS["cyan"]
GRIS = COLORS["label_gray"]
ACERO = COLORS.get("blue_steel", "#145E81")
VERDE = COLORS.get("conservador", "#1B9E5A")
AMBAR = COLORS.get("moderado", "#E08E16")


def _puntos(instr, familia, campo="tea", en_mep=False):
    """[(duration en años, tasa, ticker)] ordenado por duration."""
    out = []
    for r in instr:
        if r["familia"] != familia or r.get("durationMod") is None:
            continue
        if en_mep:
            m = r.get("enMep") or {}
            v = m.get(campo)
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
    ax.set_title(titulo, color=NAVY, fontweight="bold", fontsize=13, pad=12)
    ax.set_xlabel(xlab, color=GRIS, fontsize=10)
    ax.set_ylabel(ylab, color=GRIS, fontsize=10)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    ax.grid(True, color=COLORS.get("border_gray", "#C8D3E0"), alpha=.5, linewidth=.8)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(colors=GRIS, labelsize=9)


def _etiquetar(ax, pts, color, cada=1, dy=6):
    """Rotula los puntos. Con curvas de muchos instrumentos se saltea de a `cada`."""
    for i, (x, y, tk) in enumerate(pts):
        if i % cada:
            continue
        ax.annotate(tk, (x, y), textcoords="offset points", xytext=(0, dy),
                    ha="center", fontsize=7.5, color=color, alpha=.9)


def _serie(ax, pts, color, rotulo, marcador="o", linea="-", etiquetas=True, cada=1):
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
            marker=marcador, markersize=6, label=rotulo, zorder=3)
    if etiquetas:
        _etiquetar(ax, pts, color, cada)


# Los PNG van adjuntos al mail, así que el peso importa: seis gráficos a dpi 170 son 600 KB, y en
# base64 —que es como viajan— casi 800. A 120 dpi con paleta indexada bajan a menos de un quinto sin
# que se note en pantalla, porque son líneas y texto sobre fondo plano: no hay degradados que
# sufran la cuantización.
# 100 dpi da ~950 px de ancho: el doble de lo que un cliente de correo muestra (unos 600), así que
# se ve nítido en pantallas retina sin pesar de más. El base64 viaja inflado un 34%, y con seis
# gráficos eso importa.
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


def _cerrar(fig, ax, ruta, leyenda=True):
    if leyenda:
        ax.legend(frameon=False, fontsize=9.5, labelcolor=NAVY)
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return _comprimir(ruta)


# ── 1 · Globales contra Bonares ──────────────────────────────────────────────
def globales_vs_bonares(instr, salida):
    bon = _puntos(instr, "Bonares")
    glo = _puntos(instr, "Globales", en_mep=True)
    if not (bon and glo):
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, bon, NAVY, "Bonares · ley local")
    _serie(ax, glo, CYAN, "Globales · ley NY", marcador="s")
    _ejes(ax, "Curva soberana en dólares · ley local contra ley NY", "TIR (%)")
    ax.text(.01, -.16, "Ambas curvas en MEP. Los globales se piden en esa punta a propósito: el "
                       "monitor los valúa al CCL y mezclarlos daría un spread que no existe.",
            transform=ax.transAxes, fontsize=8, color=GRIS, va="top")
    return _cerrar(fig, ax, salida)


# ── 2 · LECAPs en TEM ────────────────────────────────────────────────────────
def lecaps_tem(instr, salida):
    pts = [(d, ((1 + t / 100) ** (1 / 12) - 1) * 100, tk)
           for d, t, tk in _puntos(instr, "LECAPs y tasa fija")]
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "LECAPs y tasa fija")
    _ejes(ax, "Curva de pesos a tasa fija · TEM", "TEM (%)")
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.2f%%"))
    ax.text(.01, -.16, "La TEM sale de la TEA que publica 1816: (1 + TEA)^(1/12) − 1.",
            transform=ax.transAxes, fontsize=8, color=GRIS, va="top")
    return _cerrar(fig, ax, salida)


# ── 3 · CER ──────────────────────────────────────────────────────────────────
def curva_cer(instr, salida, duales_cer):
    pts = _puntos(instr, "CER")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "CER · TIR real", cada=2)
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
    ax.text(.01, -.16, nota, transform=ax.transAxes, fontsize=8, color=GRIS, va="top")
    return _cerrar(fig, ax, salida)


# ── 4 · LECAPs contra CER ────────────────────────────────────────────────────
def lecaps_vs_cer(instr, salida, dudosos_cer):
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
    # El panel de abajo sí usa todos los CER: interpolar contra la curva de LECAPs ya descarta solo
    # los que quedan fuera de su rango.
    xmax = max(x for x, _, _ in lec) * 1.08
    cer_vis = [p for p in cer if p[0] <= xmax]
    fuera = len(cer) - len(cer_vis)

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9.5, 7.4), facecolor="white",
                                  gridspec_kw={"height_ratios": [1.25, 1], "hspace": .42})
    _serie(ax, lec, NAVY, "LECAPs · TEA nominal", cada=2)
    _serie(ax, cer_vis, ACERO, "CER · TIR real", marcador="s", cada=2)
    _ejes(ax, "Tasa fija contra CER", "Tasa (%)")
    ax.set_xlim(0, xmax)
    ax.set_xlabel("")

    _serie(ax2, bei, CYAN, "Inflación breakeven", etiquetas=False)
    _ejes(ax2, "Inflación que iguala las dos curvas", "Breakeven anual (%)")
    ax2.legend(frameon=False, fontsize=9.5, labelcolor=NAVY)
    # Al centro-izquierda: las LECAPs corren por arriba y los CER por abajo, así que la franja del
    # medio es la única que queda libre. Con `best`, matplotlib la ponía sobre los CER largos.
    ax.legend(frameon=False, fontsize=9.5, labelcolor=NAVY, loc="center left")
    nota = ("El breakeven es la inflación a la que una LECAP y un CER del mismo plazo rinden lo "
            "mismo: por encima conviene el CER, por debajo la tasa fija.\nCada CER se compara "
            "contra la LECAP interpolada a su misma duration, no contra la más cercana.")
    if fuera:
        nota += (f"\nQuedan {fuera} CER largos fuera del panel de arriba —hasta nueve años de "
                 f"duration—: más allá del último punto de tasa fija no hay contra qué compararlos.")
    ax2.text(.01, -.30, nota, transform=ax2.transAxes, fontsize=8, color=GRIS, va="top")
    fig.savefig(salida, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return _comprimir(salida)


# ── 5 · TAMAR ────────────────────────────────────────────────────────────────
def curva_tamar(instr, salida, duales_tamar, tamar_bcra=None):
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
        ax.text(.01, -.16, nota, transform=ax.transAxes, fontsize=8, color=GRIS, va="top")
    return _cerrar(fig, ax, salida)


# ── 6 · Dólar linked ─────────────────────────────────────────────────────────
def curva_dl(instr, salida):
    pts = _puntos(instr, "Dólar linked")
    if not pts:
        return None
    fig, ax = balanz_figure(figsize=(9.5, 5.2))
    _serie(ax, pts, NAVY, "Dólar linked · TIR")
    _ejes(ax, "Curva dólar linked", "TIR (%)")
    ax.axhline(0, color=GRIS, linewidth=.9, linestyle=":", zorder=1)
    ax.text(.01, -.16, "Rendimiento por encima de la devaluación oficial. Son pocos instrumentos y "
                       "algunos muy ilíquidos, así que la curva es indicativa.",
            transform=ax.transAxes, fontsize=8, color=GRIS, va="top")
    return _cerrar(fig, ax, salida)


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

    out = Path(dir_salida)
    out.mkdir(exist_ok=True)
    hechos = {}
    for nombre, fn in [
        ("globales_bonares", lambda p: globales_vs_bonares(instr, p)),
        ("lecaps_tem", lambda p: lecaps_tem(instr, p)),
        ("cer", lambda p: curva_cer(instr, p, d_cer)),
        ("lecaps_cer", lambda p: lecaps_vs_cer(instr, p, dud_cer)),
        ("tamar", lambda p: curva_tamar(instr, p, d_tam, tamar_spot)),
        ("dl", lambda p: curva_dl(instr, p)),
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
    return hechos


if __name__ == "__main__":
    print(f"Open Sans registrada: {BRAND}")
    generar(sys.argv[1] if len(sys.argv) > 1 else "inf_tmp.json")
