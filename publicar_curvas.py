#!/usr/bin/env python3
"""Genera las curvas de una rueda y las deja publicadas en informes/curvas/AAAA-MM-DD/.

    py publicar_curvas.py                      la última rueda con datos
    py publicar_curvas.py 2026-09-04           una rueda puntual

QUÉ HACE Y POR QUÉ EXISTE. `curvas_informe.generar()` deja los nueve PNG donde se le diga, y por
defecto en `curvas/`, que es una carpeta de trabajo que se pisa en cada corrida. Para que el
informe de una rueda se pueda releer meses después con sus gráficos hay que generarlos en una
carpeta con la fecha y escribir su índice. Eso se venía haciendo a mano en cada informe; acá queda
en un comando.

CORRE TAMBIÉN EN GITHUB ACTIONS, y ese es el punto. Las tareas programadas del usuario sólo se
ejecutan con la app abierta: si la máquina está apagada a las 17:30, el informe de esa rueda no
sale y —hasta este script— tampoco quedaban sus curvas. El dato se archivaba igual, pero el link
del archivo histórico daba 404 para siempre. Ahora el job diario las publica sin intervención.

NO PISA EL PDF. La página índice lista los `cierre-*.pdf` que encuentre en la carpeta, así que si
el informe se armó después —el PDF lo escribe la máquina del usuario, porque necesita la prosa del
día— basta con volver a correr esto o `pagina_curvas.escribir` para que aparezca el link. El orden
entre los dos no importa.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from pagina_curvas import escribir                                # noqa: E402

DESTINO = REPO / "informes" / "curvas"


def ultima_rueda():
    """La fecha del datos_*.json más reciente que haya en informes/."""
    archivos = sorted((REPO / "informes").glob("datos_*.json"))
    if not archivos:
        raise SystemExit("no hay ningún informes/datos_*.json; primero corré armar_informe.py")
    return archivos[-1].stem.replace("datos_", "")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    fecha = argv[0] if argv else ultima_rueda()
    ruta_json = REPO / "informes" / f"datos_{fecha}.json"
    if not ruta_json.exists():
        raise SystemExit(f"no existe {ruta_json.name}")

    # Se importa acá y no arriba porque importar curvas_informe registra la fuente y aplica el
    # estilo de matplotlib: no conviene que eso pase por el solo hecho de leer el módulo.
    import curvas_informe
    if not curvas_informe.FUENTE:
        print("AVISO: Open Sans no se registró; las curvas van a salir con otra tipografía")

    destino = DESTINO / fecha
    destino.mkdir(parents=True, exist_ok=True)
    # Se generan DIRECTO en la carpeta de la fecha en vez de generar en `curvas/` y copiar: son los
    # mismos PNG y así no queda una copia intermedia que pueda quedar desincronizada.
    hechos = curvas_informe.generar(str(ruta_json), dir_salida=str(destino))
    if not hechos:
        raise SystemExit("no se generó ninguna curva; no se publica nada")

    escribir(destino, hechos, fecha)
    pdfs = sorted(p.name for p in destino.glob("cierre-*.pdf"))
    print(f"publicadas {len(hechos)} curvas en informes/curvas/{fecha}/"
          + (f" · PDF ya presente: {', '.join(pdfs)}" if pdfs else " · sin PDF todavía"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
