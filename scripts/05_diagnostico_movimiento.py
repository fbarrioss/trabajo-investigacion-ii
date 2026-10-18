"""Paso 5 (diagnóstico) — Verifica que el simulador recorra la red real.

No entrena nada: ejecuta un episodio con la política trivial "continuar" y
comprueba que cada vehículo avanza por la topología OSM, llega a sus
distribuidores y acumula una distancia compatible con el corredor
Lima–Piura (~1 060 km de ida).

Uso:
    python scripts/05_diagnostico_movimiento.py
    python scripts/05_diagnostico_movimiento.py --pasos 240 --semilla 7
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src.comun.config import RAIZ  # noqa: E402
from src.entorno.entorno_rutas import EntornoRutasCarga  # noqa: E402
from src.entrenamiento.entrenar import cargar_recursos  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico de movilidad sobre la red real")
    ap.add_argument("--pasos", type=int, default=None)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--accion", type=int, default=0, help="0=continuar 1=alterna 2=esperar 3=replanificar")
    args = ap.parse_args()

    red, ds, esc, cfg = cargar_recursos()
    print(f"Red: {red.modo} | nodos={len(red.nodos):,} | tramos={len(red.tramos):,}")

    env = EntornoRutasCarga(red, ds, esc, cfg, semilla=args.semilla)
    env.reset(semilla=args.semilla)
    pasos = args.pasos or env.horizonte

    print(f"Agentes={env.n_agentes} | horizonte={env.horizonte} | dt={env.dt_min} min")
    for a in env.agentes:
        print(
            f"  AG-{a.idx} viaje={a.id_viaje} origen={a.nodo_actual} "
            f"destino={a.destino} plan={len(a.plan_nodos)} nodos "
            f"| 1er tramo={a.tramo_actual} ({a.km_tramo:.2f} km)"
        )

    acciones = np.full(env.n_agentes, args.accion, dtype=int)
    for _ in range(pasos):
        _, _, dones, _ = env.step(acciones)
        if dones.all():
            break

    print(f"\n{pasos} pasos ({pasos * env.dt_min / 60:.1f} h) | hora={env.hora_actual:.2f}")
    total_entregas = sum(len(a.historial) for a in env.agentes)
    for a in env.agentes:
        restante = len(a.entregas)
        print(
            f"  AG-{a.idx}: {a.km_totales:8.1f} km | nodo={a.nodo_actual} "
            f"| tramo={a.tramo_actual} {a.km_recorridos_tramo:.2f}/{a.km_tramo:.2f} km "
            f"| entregas={a.entregas_realizadas} (pendientes {restante}) "
            f"| plan={len(a.plan_nodos)} | esperas={a.esperas} replan={a.replanificaciones}"
        )
    print(f"  entregas totales: {total_entregas}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
