#!/usr/bin/env python3
"""Mantiene sincronizado el skill del informe diario entre el repo y la carpeta que lo ejecuta.

EL PROBLEMA. La tarea programada lee el skill de `~/.claude/scheduled-tasks/<id>/SKILL.md`, que no
está bajo control de versiones: si se pierde la máquina, se pierde el historial de por qué cada
regla del informe es como es —y ese historial es la mitad del valor del archivo, porque casi todas
las reglas salieron de un error concreto—. Por eso la copia versionada vive acá, en `skills/`.

Tener dos copias del mismo archivo invita a que se separen sin que nadie se entere. Este script
existe para que esa deriva sea visible en un comando en vez de descubrirse cuando el informe sale
mal. NO sincroniza solo: por defecto sólo compara y dice qué encontró.

    py sincronizar_skills.py              compara y muestra el diff
    py sincronizar_skills.py --traer      copia de ~/.claude AL repo (lo habitual: se editó en vivo)
    py sincronizar_skills.py --llevar     copia del repo A ~/.claude (tras un pull en otra máquina)

POR QUÉ NO UN SYMLINK NI UN HARDLINK. Los dos resolverían la duplicación de raíz, pero el symlink
en Windows pide permisos de administrador o modo desarrollador, y el hardlink se rompe en silencio
en cuanto un editor guarda escribiendo un temporal y renombrando —que es lo que hacen casi todos—.
El resultado sería el mismo problema de deriva, pero sin manera de notarlo. Copiar y comparar es
más tosco y no falla callado.
"""
import argparse
import difflib
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
VIVO = Path.home() / ".claude" / "scheduled-tasks"

# id de la tarea -> ruta relativa dentro del repo. Si mañana se versiona otro skill, va acá.
SKILLS = {"informe-diario-mercado": "skills/informe-diario-mercado/SKILL.md"}


def par(tarea, rel):
    return VIVO / tarea / "SKILL.md", REPO / rel


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--traer", action="store_true", help="de ~/.claude al repo")
    g.add_argument("--llevar", action="store_true", help="del repo a ~/.claude")
    args = ap.parse_args(argv)

    difieren = 0
    for tarea, rel in SKILLS.items():
        vivo, repo = par(tarea, rel)
        if not vivo.exists() and not repo.exists():
            print(f"{tarea}: no existe en ningún lado")
            continue
        if args.traer or args.llevar:
            origen, destino = (vivo, repo) if args.traer else (repo, vivo)
            if not origen.exists():
                print(f"{tarea}: falta el origen {origen}")
                difieren += 1
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
            print(f"{tarea}: copiado {origen} -> {destino}")
            continue

        a = vivo.read_text(encoding="utf-8") if vivo.exists() else ""
        b = repo.read_text(encoding="utf-8") if repo.exists() else ""
        if a == b:
            print(f"{tarea}: iguales ({len(a.splitlines())} líneas)")
            continue
        difieren += 1
        print(f"{tarea}: DIFIEREN — ~/.claude {len(a.splitlines())} líneas, "
              f"repo {len(b.splitlines())}")
        for linea in difflib.unified_diff(b.splitlines(), a.splitlines(),
                                          "repo", "~/.claude", lineterm="", n=2):
            print("   " + linea)

    # Distinto de cero cuando hay deriva, para poder colgarlo de un hook o de un job si algún día
    # conviene que avise solo.
    return 1 if difieren else 0


if __name__ == "__main__":
    sys.exit(main())
