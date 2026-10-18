"""Paso 3 — Entrenamiento de las políticas MARL.

Uso:
    python scripts/03_entrenar.py --algoritmo qmix --episodios 300
    python scripts/03_entrenar.py --algoritmo mappo --episodios 300
    python scripts/03_entrenar.py --algoritmo qmix --semillas 1 2 3
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.comun.config import ARTIFACTS_DIR  # noqa: E402
from src.entrenamiento.entrenar import entrenar  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Entrena QMIX o MAPPO")
    ap.add_argument("--algoritmo", choices=["qmix", "mappo"], default="qmix")
    ap.add_argument("--episodios", type=int, default=None)
    ap.add_argument("--semillas", type=int, nargs="+", default=[1])
    ap.add_argument("--viajes", type=int, default=120)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    for semilla in args.semillas:
        print(f"\n=== {args.algoritmo.upper()} | semilla {semilla} ===")
        res = entrenar(
            algoritmo=args.algoritmo,
            episodios=args.episodios,
            semilla=semilla,
            n_viajes=args.viajes,
        )
        h = res.historial
        print(f"Episodios: {len(h)} | duración: {res.duracion_s/60:.1f} min")
        print(f"Recompensa final (media últ. 20): {h['recompensa'].tail(20).mean():.3f}")
        print(f"Costo medio final: S/ {h['costo_total_pen'].tail(20).mean():,.0f}")
        print(f"Puntualidad final: {h['puntualidad_pct'].tail(20).mean():.1f} %")
        print(f"Modelo: {res.ruta_modelo}")

        dir_out = ARTIFACTS_DIR / "historial"
        dir_out.mkdir(parents=True, exist_ok=True)
        h.to_csv(dir_out / f"{args.algoritmo}_s{semilla}.csv", index=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
