"""Página HTML con las curvas del informe, para servir desde GitHub Pages.

POR QUÉ EXISTE. El informe se manda por Gmail y ahí las imágenes NO llegan: la vía de envío
descarta toda etiqueta <img> del HTML — probado el 28/08/2026 con cinco variantes (img suelto,
con style, dentro de <a>, dentro de <table>, y background-image por CSS): las cinco desaparecen
del mensaje que queda en el servidor. Los links, en cambio, sobreviven. Así que las curvas se
publican acá y el mail linkea a esta página.
"""
from pathlib import Path

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

# Orden de lectura: primero la foto en dólares, después la curva en pesos de corto a largo.
FICHAS = [
    ("globales_bonares", "Globales vs Bonares",
     "Curva en dólares por legislación, con las dos patas llevadas a la misma punta antes de "
     "restarlas, como hace la solapa Glob vs Bon. Mezclar MEP con CCL mueve el spread entre 79 y "
     "279 puntos básicos según el par."),
    ("lecaps_tem", "LECAPs en TEM",
     "Tasa efectiva mensual de la curva de tasa fija en pesos."),
    ("cer", "Curva CER",
     "Rendimiento real, CER más un spread. Cada dual entra con su pata CER, pedida a 1816 por "
     "separado y con su propia duration."),
    ("lecaps_cer", "LECAPs contra CER",
     "Las dos curvas sobre el tramo que comparten, cada una en su escala: la tasa fija a la "
     "izquierda y el rendimiento real de los CER a la derecha. En un solo eje los CER quedaban "
     "aplastados contra el piso."),
    ("breakeven", "Inflación implícita",
     "La inflación a la que una LECAP y un CER del mismo plazo rinden lo mismo. Cada CER se "
     "compara contra la curva de tasa fija interpolada a su misma duration, no contra la LECAP "
     "más cercana."),
    ("tamar", "Curva TAMAR",
     "Incluye la pata TAMAR de los duales y la TAMAR spot de bancos privados que publica el BCRA."),
    ("dl", "Curva dólar linked",
     "Devaluación implícita por vencimiento."),
    ("futuros", "Futuros de dólar",
     "Precio de cada contrato a la izquierda y, a la derecha, la devaluación acumulada que ese "
     "precio implica contra el mayorista. Es una sola curva leída en dos escalas: el porcentaje "
     "acumulado es una función lineal del precio, así que como segunda línea sería el mismo dato "
     "dibujado dos veces. Los círculos huecos son contratos de volumen fino, donde el ajuste lo "
     "pone la cámara y no el mercado."),
    ("subsoberanos", "Subsoberanos en CCL",
     "Provinciales y municipales, valuados en la misma punta en que los muestra el monitor."),
]


def _fecha_larga(iso):
    a, m, d = iso.split("-")
    return f"{int(d)} de {MESES[int(m) - 1]} de {a}"


def escribir(dir_salida, hechos, fecha):
    """Arma index.html en el directorio de las curvas. `hechos` son las que sí se generaron."""
    tarjetas = []
    for nombre, titulo, bajada in FICHAS:
        if nombre not in hechos:
            continue
        tarjetas.append(f"""  <figure>
    <h2>{titulo}</h2>
    <p class="bajada">{bajada}</p>
    <img src="{nombre}.png" alt="{titulo}" loading="lazy">
  </figure>""")

    faltan = [t for n, t, _ in FICHAS if n not in hechos]
    aviso = ""
    if faltan:
        # Que una curva no esté es información: significa que esa familia no tenía instrumentos
        # suficientes en la rueda. Callarlo haría pensar que la página se cortó.
        aviso = ('<p class="aviso">Sin datos suficientes en esta rueda para: '
                 + ", ".join(faltan) + ".</p>")

    # Si el PDF del día ya está al lado, la página lo ofrece: es la versión que se comparte.
    pdf = next((f.name for f in sorted(Path(dir_salida).glob("cierre-*.pdf"))), None)
    bloque_pdf = ""
    if pdf:
        bloque_pdf = (f'<p class="pdf"><a href="{pdf}">Descargar el informe completo en PDF</a>'
                      f'<span> · mismo contenido, en ocho páginas</span></p>')

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Curvas · {fecha}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {{ --navy:#002060; --cyan:#00B0F0; --gris:#6B7280; --borde:#C8D3E0; --fondo:#F2F4F8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--fondo); color:#202124;
         font-family:"Open Sans",-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         font-size:15px; line-height:1.6; }}
  header {{ background:var(--navy); color:#fff; padding:26px 22px 22px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  .eyebrow {{ font-size:11px; letter-spacing:.12em; text-transform:uppercase;
              color:var(--cyan); margin:0 0 6px; font-weight:600; }}
  header h1 {{ margin:0; font-size:24px; font-weight:600; }}
  header p {{ margin:6px 0 0; font-size:13.5px; color:#c8d7ee; }}
  main {{ padding:22px; }}
  figure {{ margin:0 0 20px; background:#fff; border:1px solid var(--borde);
            border-radius:6px; padding:18px 18px 14px; }}
  figure h2 {{ margin:0 0 4px; font-size:16px; font-weight:600; color:var(--navy); }}
  .bajada {{ margin:0 0 14px; font-size:13px; color:var(--gris); }}
  figure img {{ display:block; width:100%; height:auto; }}
  .aviso {{ background:#fff8e1; border-left:3px solid #f9a825; padding:10px 13px;
            font-size:13px; margin:0 0 20px; }}
  .barra {{ padding:16px 22px 0; }}
  .pdf {{ margin:0; font-size:14px; }}
  .pdf a {{ color:var(--navy); font-weight:600; text-decoration:none;
            border-bottom:2px solid var(--cyan); padding-bottom:1px; }}
  .pdf span {{ color:var(--gris); font-weight:400; font-size:12.5px; }}
  footer {{ max-width:960px; margin:0 auto; padding:0 22px 40px;
            font-size:12.5px; color:var(--gris); }}
  footer hr {{ border:none; border-top:1px solid var(--borde); margin:0 0 12px; }}
  @media (max-width:600px) {{ main, header {{ padding-left:14px; padding-right:14px; }}
                              figure {{ padding:14px 12px 10px; }} }}
</style>
</head>
<body>
<header><div class="wrap">
  <p class="eyebrow">Renta fija Argentina</p>
  <h1>Curvas del {_fecha_larga(fecha)}</h1>
  <p>Las mismas curvas del informe diario, en tamaño completo.</p>
</div></header>
<div class="wrap barra">{bloque_pdf}</div>
<main class="wrap">
{aviso}
{chr(10).join(tarjetas)}
</main>
<footer>
  <hr>
  <p>Precios y tasas de 1816, tomados de la rueda del {fecha}. La TEA y la paridad vienen como
  fracción y se escalan acá; la duration modificada está en años. Los duales se piden por pata
  separada, así que cada uno aparece en su curva con la tasa y la duration que le corresponden.</p>
</footer>
</body>
</html>
"""
    ruta = Path(dir_salida) / "index.html"
    ruta.write_text(html, encoding="utf-8")
    return str(ruta)
