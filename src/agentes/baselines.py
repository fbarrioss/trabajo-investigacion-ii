"""Políticas de referencia (baselines) para la comparación experimental.

Se implementan las tres líneas base declaradas en el plan de tesis (OE5):
  1. ``politica_estatica``           → ruta manual fija, nunca replanifica.
  2. ``ruta_mas_corta_dijkstra``     → replanifica siempre por el camino mínimo.
  3. ``heuristica_vrp_ortools``      → secuencia de entregas resuelta con OR-Tools.

Además se incluye ``politica_aleatoria`` como control inferior.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

Politica = Callable[[object], np.ndarray]


# ---------------------------------------------------------------------------
def politica_estatica(env) -> np.ndarray:
    """Ruta planificada original: siempre continuar (acción 0)."""
    return np.zeros(env.n_agentes, dtype=int)


def politica_aleatoria(env, semilla: int | None = None) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    m = env.mascara_acciones()
    acc = np.zeros(env.n_agentes, dtype=int)
    for i in range(env.n_agentes):
        validas = np.flatnonzero(m[i])
        acc[i] = int(rng.choice(validas)) if validas.size else 0
    return acc


def ruta_mas_corta_dijkstra(env) -> np.ndarray:
    """Replanifica en cada nodo por el camino de menor tiempo.

    Es el comportamiento de un TMS clásico: recalcula la ruta óptima estática
    cada vez que el vehículo llega a un nodo, sin aprendizaje.
    """
    m = env.mascara_acciones()
    acc = np.zeros(env.n_agentes, dtype=int)
    for i, a in enumerate(env.agentes):
        if a.entregado:
            acc[i] = 0
            continue
        acc[i] = 3 if m[i, 3] else 0
    return acc


def heuristica_vrp_ortools(env) -> np.ndarray:
    """Heurística VRP: sigue la secuencia óptima y re-optimiza ante evento.

    Representa a un TMS con re-optimización por umbral: solo recalcula la ruta
    cuando el tramo planificado empeora su tiempo en 50 % o más. Es el
    comportamiento típico de las herramientas VRP comerciales.
    """
    m = env.mascara_acciones()
    acc = np.zeros(env.n_agentes, dtype=int)
    for i, a in enumerate(env.agentes):
        if a.entregado:
            continue
        clave = a.plan_aristas[0] if a.plan_aristas else None
        base = clave[:-2] if clave and clave.endswith("_R") else clave
        factor = env._factor_evento(base) if base else 1.0
        if factor >= 1.5 and m[i, 3]:
            acc[i] = 3
        else:
            acc[i] = 0
    return acc


BASELINES: dict[str, Politica] = {
    "politica_estatica": politica_estatica,
    "ruta_mas_corta_dijkstra": ruta_mas_corta_dijkstra,
    "heuristica_vrp_ortools": heuristica_vrp_ortools,
}


def obtener_baseline(nombre: str) -> Politica:
    if nombre not in BASELINES:
        raise KeyError(
            f"Baseline desconocido: {nombre}. Opciones: {list(BASELINES)}"
        )
    return BASELINES[nombre]
