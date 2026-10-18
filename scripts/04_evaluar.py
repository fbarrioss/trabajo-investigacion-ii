"""Paso 4 — Evaluación comparativa contra baselines.

Uso:
    python scripts/04_evaluar.py --modelo artifacts/modelos/qmix_s1.pt --episodios 30
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.comun.config import ARTIFACTS_DIR  # noqa: E402
from src.entrenamiento.evaluar import evaluar_completo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Evalúa la política MARL vs baselines")
    ap.add_argument("--modelo", default=str(ARTIFACTS_DIR / "modelos" / "qmix_s1.pt"))
    ap.add_argument("--episodios", type=int, default=20)
    ap.add_argument("--semilla", type=int, default=1)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    res = evaluar_completo(args.modelo, args.episodios, args.semilla)

    print("\n=== RESUMEN POR POLÍTICA ===")
    columnas = [c for c in res["resumen"].columns if c.endswith("_media") or c in ("politica", "n")]
    print(res["resumen"][columnas].to_string(index=False))

    if not res["contrastes"].empty:
        print("\n=== CONTRASTES vs POLÍTICA ESTÁTICA (Mann-Whitney) ===")
        print(
            res["contrastes"][
                ["metrica", "politica", "media_politica", "media_baseline",
                 "delta_pct", "p_valor", "significativo"]
            ].to_string(index=False)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
