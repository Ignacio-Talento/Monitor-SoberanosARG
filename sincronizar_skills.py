#!/usr/bin/env python3
"""Mantiene sincronizados los skills entre el repo y las carpetas desde las que se ejecutan.

EL PROBLEMA. Los skills viven en `~/.claude/`, que no está bajo control de versiones: si se pierde
la máquina, se pierde el historial de por qué cada regla es como es —y ese historial es la mitad
del valor del archivo, porque casi todas las reglas salieron de un error concreto—. Por eso la
copia versionada vive acá, en `skills/`.

Tener dos copias del mismo archivo invita a que se separen sin que nadie se entere. Este script
existe para que esa deriva sea visible en un comando en vez de descubrirse cuando el informe sale
mal. NO sincroniza solo: por defecto sólo compara y dice qué encontró.

    py sincronizar_skills.py              compara y muestra el diff
    py sincronizar_skills.py --traer      copia de ~/.claude AL repo (lo habitual: se editó en vivo)
    py sincronizar_skills.py --llevar     copia del repo A ~/.claude (tras un pull en otra máquina)

QUÉ SE VERSIONA Y QUÉ NO. Sólo lo que tiene conocimiento durable. Las tareas programadas de
`~/.claude/scheduled-tasks/` son casi todas de un solo uso y con fecha en el nombre —"verificar la
corrida del 18/08"—: ya corrieron y su valor se agotó ahí, así que versionarlas sería ruido. La
excepción es `informe-diario-mercado`, que es la tarea recurrente y donde vive el criterio editorial
del informe.

POR QUÉ NO UN SYMLINK NI UN HARDLINK. Los dos resolverían la duplicación de raíz, pero el symlink
en Windows pide permisos de administrador o modo desarrollador, y el hardlink se rompe en silencio
en cuanto un editor guarda escribiendo un temporal y renombrando —que es lo que hacen casi todos—.
El resultado sería el mismo problema de deriva, pero sin manera de notarlo. Copiar y comparar es
más tosco y no falla callado.
"""
import argparse
import difflib
import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
CLAUDE = Path.home() / ".claude"

# Cada entrada es (ruta en ~/.claude, ruta en el repo). Se sincroniza el ÁRBOL entero, no un solo
# archivo: los skills traen references/ y a veces scripts/.
SKILLS = [
    ("scheduled-tasks/informe-diario-mercado", "skills/informe-diario-mercado"),
    ("skills/renta-fija-argentina-1816",       "skills/renta-fija-argentina-1816"),
]


def archivos(raiz):
    """Rutas relativas de todo lo que cuelga de la carpeta, ordenadas."""
    if not raiz.exists():
        return []
    return sorted(p.relative_to(raiz) for p in raiz.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def comparar(vivo, repo, nombre):
    """-> True si difieren. Imprime qué cambió."""
    a, b = archivos(vivo), archivos(repo)
    solo_vivo, solo_repo = set(a) - set(b), set(b) - set(a)
    distintos = [r for r in set(a) & set(b)
                 if not filecmp.cmp(vivo / r, repo / r, shallow=False)]
    if not (solo_vivo or solo_repo or distintos):
        print(f"{nombre}: iguales ({len(a)} archivos)")
        return False

    print(f"{nombre}: DIFIEREN")
    for r in sorted(solo_vivo):
        print(f"   sólo en ~/.claude: {r}")
    for r in sorted(solo_repo):
        print(f"   sólo en el repo:   {r}")
    for r in sorted(distintos):
        print(f"   distinto: {r}")
        try:
            va = (vivo / r).read_text(encoding="utf-8").splitlines()
            vb = (repo / r).read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            print("      (binario, no se muestra el diff)")
            continue
        for linea in difflib.unified_diff(vb, va, "repo", "~/.claude", lineterm="", n=2):
            print("      " + linea)
    return True


def copiar(origen, destino, nombre):
    if not origen.exists():
        print(f"{nombre}: falta el origen {origen}")
        return True
    # dirs_exist_ok pisa lo que coincide y deja lo demás; después se borra lo que sobra en destino,
    # porque si no un archivo eliminado en el origen sobreviviría para siempre en el otro lado.
    shutil.copytree(origen, destino, dirs_exist_ok=True)
    sobrantes = set(archivos(destino)) - set(archivos(origen))
    for r in sorted(sobrantes):
        (destino / r).unlink()
        print(f"   borrado (ya no está en el origen): {r}")
    print(f"{nombre}: {len(archivos(origen))} archivos {origen} -> {destino}")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--traer", action="store_true", help="de ~/.claude al repo")
    g.add_argument("--llevar", action="store_true", help="del repo a ~/.claude")
    args = ap.parse_args(argv)

    problemas = 0
    for rel_claude, rel_repo in SKILLS:
        vivo, repo = CLAUDE / rel_claude, REPO / rel_repo
        nombre = Path(rel_repo).name
        if args.traer:
            problemas += copiar(vivo, repo, nombre)
        elif args.llevar:
            problemas += copiar(repo, vivo, nombre)
        else:
            problemas += comparar(vivo, repo, nombre)

    # Distinto de cero cuando hay deriva, para poder colgarlo de un hook o de un job si algún día
    # conviene que avise solo.
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
