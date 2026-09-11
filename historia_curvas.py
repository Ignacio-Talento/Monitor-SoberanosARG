"""informes/curvas/historia.json: los puntos y la curva ajustada de cada rueda, para comparar fechas.

Lo lee el modo «Comparar fechas» de la solapa Curvas, que superpone la misma curva en varias ruedas.
Las imágenes de cada rueda no sirven para eso —cada una tiene su propia escala—, así que acá van los
datos crudos: por rueda y por curva, los puntos [duration, tasa, ticker] y la curva ajustada ya
evaluada sobre una malla. El ajuste se hace ACÁ, con la misma función que dibuja las imágenes del
informe (curvas_informe._elegir_forma), para que la curva de la solapa y la del PDF sean la misma y
no dos implementaciones que con el tiempo se separan.

Fuentes, por rueda: informes/datos_AAAA-MM-DD.json (las que tuvieron informe) y
informes/historico/datos_AAAA-MM-DD.json (las reconstruidas por curvas_historicas.py). Si hay de las
dos, manda la del informe.

Uso: py historia_curvas.py   (también lo corre pagina_curvas.escribir_indice en cada publicación)
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent

# id, título, rótulo del eje, decimales, familia, transformación, se ajusta, excluir del ajuste,
# en_mep. El orden es el del selector de la solapa.
CURVAS = [
    ("tasa_fija", "Tasa fija · TEM", "TEM (%)", 2, "LECAPs y tasa fija", "tem", True, None, False),
    ("cer", "CER · TIR real", "CER + x % (TIR real)", 1, "CER", None, True, 2 / 12, False),
    ("tamar", "TAMAR · TEA", "TEA (%)", 1, "TAMAR", None, True, None, False),
    ("bonares", "Bonares · ley local · TIR en MEP", "TIR (%)", 1, "Bonares", None, True, None, False),
    ("globales", "Globales · ley NY · TIR en MEP", "TIR (%)", 1, "Globales", None, True, None, True),
    ("dl", "Dólar linked · TIR", "TIR (%)", 1, "Dólar linked", None, True, 1 / 12, False),
    # Puntos sueltos, sin ajuste: son emisores distintos (pedido del usuario, 11/09/2026).
    ("subsoberanos", "Subsoberanos · TIR en CCL", "TIR (%)", 1, "Subsoberanos", None, False, None, False),
]
MALLA = 40


def _puntos(instr, familia, en_mep):
    """Igual que curvas_informe._puntos, sin importar matplotlib."""
    out = []
    for r in instr:
        if r.get("familia") != familia or r.get("durationMod") is None:
            continue
        if en_mep:
            m = r.get("enMep") or {}
            v = m.get("tea") if m else (r.get("tea") if r.get("moneda") == "mep" else None)
        else:
            v = r.get("tea")
        if v is None:
            continue
        out.append((r["durationMod"], v, r["ticker"]))
    return sorted(out)


def _ajustada(pts, corte):
    """La curva ajustada evaluada en MALLA puntos, con los mismos filtros que el gráfico del informe:
    fuera lo que vence en menos de dos semanas y, en la CER, lo de menos de `corte` años."""
    from curvas_informe import _elegir_forma
    usados = [(x, y) for x, y, _ in pts if x and x >= max(.04, corte or 0)]
    if len(usados) < 3:
        return None
    xs = np.array([u[0] for u in usados], dtype=float)
    ys = np.array([u[1] for u in usados], dtype=float)
    elegida = _elegir_forma(xs, ys)
    if not elegida:
        return None
    _, _, _grado, log_x, coef, _ = elegida
    malla = np.linspace(xs.min(), xs.max(), MALLA)
    ys_m = np.polyval(coef, np.log(malla) if log_x else malla)
    return [[round(float(x), 4), round(float(y), 4)] for x, y in zip(malla, ys_m)]


def series_de(d):
    instr = d.get("instrumentos") or []
    out = {}
    for cid, _t, _y, _dec, fam, transf, ajusta, corte, en_mep in CURVAS:
        pts = _puntos(instr, fam, en_mep)
        if transf == "tem":
            pts = [(x, ((1 + t / 100) ** (1 / 12) - 1) * 100, tk) for x, t, tk in pts]
        if not pts:
            continue
        s = {"p": [[round(x, 4), round(y, 4), tk] for x, y, tk in pts]}
        if ajusta:
            a = _ajustada(pts, corte)
            if a:
                s["a"] = a
        out[cid] = s
    return out


def fuentes(raiz_informes):
    raiz = Path(raiz_informes)
    por_fecha = {}
    for f in sorted((raiz / "historico").glob("datos_*.json")):
        por_fecha[f.stem[6:]] = (f, True)
    for f in sorted(raiz.glob("datos_*.json")):
        por_fecha[f.stem[6:]] = (f, False)
    return {k: v for k, v in por_fecha.items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", k)}


def escribir(raiz_informes=REPO / "informes"):
    raiz = Path(raiz_informes)
    ruedas = []
    for fecha, (ruta, recon) in sorted(fuentes(raiz).items(), reverse=True):
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"historia: {ruta.name} ilegible ({e})", file=sys.stderr)
            continue
        s = series_de(d)
        if s:
            ruedas.append({"fecha": fecha, "reconstruida": recon, "series": s})
    doc = {
        # `corte`: la duration debajo de la cual un punto queda fuera del ajuste.
        "curvas": [{"id": c[0], "titulo": c[1], "eje": c[2], "dec": c[3], "ajuste": c[6],
                    "corte": round(max(.04, c[7] or 0), 4)} for c in CURVAS],
        "ruedas": ruedas,
    }
    destino = raiz / "curvas" / "historia.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return destino, len(ruedas)


if __name__ == "__main__":
    ruta, n = escribir()
    print(f"{ruta}: {n} ruedas, {ruta.stat().st_size // 1024} KB")
