#!/usr/bin/env python3
"""¿1816 publica las patas de los duales por separado? Diagnóstico de una sola vez."""
import json
from actualizar_historicos import cliente_1816

cli = cliente_1816()
if cli is None:
    raise SystemExit("sin cliente 1816")

print("=" * 70)
print("1. BUSCAR LOS DUALES EN EL CATÁLOGO")
for texto in ["TXM", "TTS26", "TMVE8"]:
    try:
        r = cli.instrumentos(texto=texto)
        print(f"\n  buscar '{texto}': {len(r or [])} resultados")
        for i in (r or [])[:12]:
            print(f"     {json.dumps(i, ensure_ascii=False)[:190]}")
    except Exception as e:
        print(f"  '{texto}': ERROR {e}")

print("\n" + "=" * 70)
print("2. PEDIR INDICADORES DE UN DUAL Y DE SUS PATAS")
pruebas = ["TXMD8", "TXMD8 @CER", "TXMD8 @TAMAR", "TTS26", "TTS26 @Tasa Fija", "TTS26 @TAMAR"]
for tk in pruebas:
    try:
        d = cli.precios([tk], ["precioDirty", "tea", "durationMod"], moneda="ars")
        print(f"  {tk:20s} -> {json.dumps(d, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  {tk:20s} -> ERROR {str(e)[:120]}")
