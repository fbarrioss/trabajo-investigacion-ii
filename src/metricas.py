"""Métricas de evaluación del artefacto (Tabla 3 y sección 4.2.11 del plan)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

METRICAS = [
    "costo_total_pen",
    "tiempo_total_min",
    "puntualidad_pct",
    "cumplimiento_ventana_pct",
    "desviacion_ruta_km",
    "n_replanificaciones",
    "tiempo_detenido_min",
    "recompensa_promedio",
    "resiliencia_pct",
    "entregas_completadas",
    "km_totales",
]


def evaluar_episodio(env, info: dict[str, Any], recompensa_total: float) -> dict[str, float]:
    """Extrae el vector de métricas de un episodio terminado."""
    agentes = env.agentes
    entregas = [h for a in agentes for h in a.historial]
    n_entregas = len(entregas)
    a_tiempo = sum(1 for h in entregas if h["cumple_ventana"])

    costo = sum(a.costo for a in agentes)
    tiempo = max((a.tiempo_min for a in agentes), default=0.0)
    km = sum(a.km_totales for a in agentes)
    replan = sum(a.replanificaciones for a in agentes)
    detenido = sum(a.esperas for a in agentes) * env.dt_min
    retraso = sum(a.retraso_min for a in agentes)

    # Desviación: km reales vs km de la ruta planificada de cada viaje.
    plan = env.ds["viajes"].set_index("id_viaje")
    desvio = 0.0
    for a in agentes:
        if a.id_viaje in plan.index:
            desvio += max(a.km_totales - float(plan.loc[a.id_viaje, "distancia_plan_km"]), 0.0)

    # Resiliencia: proporción de entregas cumplidas pese a eventos severos.
    eventos_severos = 0
    if not env.eventos.empty:
        eventos_severos = int((env.eventos["severidad"] >= 2.0).sum())
    resiliencia = 100.0 * (a_tiempo / n_entregas) if n_entregas else 0.0
    if eventos_severos:
        resiliencia *= min(1.0, 1.0 + 0.05 * eventos_severos)

    return {
        "costo_total_pen": round(costo, 2),
        "tiempo_total_min": round(tiempo, 2),
        "puntualidad_pct": round(100.0 * a_tiempo / n_entregas, 2) if n_entregas else 0.0,
        "cumplimiento_ventana_pct": round(100.0 * a_tiempo / n_entregas, 2) if n_entregas else 0.0,
        "desviacion_ruta_km": round(desvio, 2),
        "n_replanificaciones": float(replan),
        "tiempo_detenido_min": round(detenido, 1),
        "recompensa_promedio": round(recompensa_total, 4),
        "resiliencia_pct": round(min(resiliencia, 100.0), 2),
        "entregas_completadas": float(n_entregas),
        "km_totales": round(km, 2),
        "retraso_total_min": round(retraso, 1),
    }


def resumir_resultados(df: pd.DataFrame) -> pd.DataFrame:
    """Media, desviación estándar e IC 95 % por política."""
    filas = []
    for politica, grupo in df.groupby("politica"):
        fila: dict[str, Any] = {"politica": politica, "n": len(grupo)}
        for m in METRICAS:
            if m not in grupo.columns:
                continue
            valores = grupo[m].to_numpy(dtype=float)
            media = float(np.mean(valores))
            sd = float(np.std(valores, ddof=1)) if len(valores) > 1 else 0.0
            ee = sd / np.sqrt(len(valores)) if len(valores) > 1 else 0.0
            fila[f"{m}_media"] = round(media, 3)
            fila[f"{m}_sd"] = round(sd, 3)
            fila[f"{m}_ic95"] = round(1.96 * ee, 3)
        filas.append(fila)
    return pd.DataFrame(filas)


def comparar_con_baseline(
    df: pd.DataFrame, metrica: str, alpha: float = 0.05
) -> pd.DataFrame:
    """Contraste no paramétrico (Mann-Whitney U) de cada política vs baseline."""
    from scipy import stats

    if "politica_estatica" not in set(df["politica"]):
        return pd.DataFrame()

    base = df.loc[df["politica"] == "politica_estatica", metrica].to_numpy(dtype=float)
    filas = []
    for politica, grupo in df.groupby("politica"):
        if politica == "politica_estatica":
            continue
        valores = grupo[metrica].to_numpy(dtype=float)
        if len(valores) < 3 or len(base) < 3:
            continue
        try:
            u, p = stats.mannwhitneyu(valores, base, alternative="two-sided")
            efecto = 1 - (2 * u) / (len(valores) * len(base))
        except ValueError:
            p, efecto = np.nan, np.nan
        filas.append(
            {
                "politica": politica,
                "metrica": metrica,
                "media_politica": round(float(np.mean(valores)), 3),
                "media_baseline": round(float(np.mean(base)), 3),
                "delta_pct": round(
                    100 * (np.mean(valores) - np.mean(base)) / (abs(np.mean(base)) + 1e-9), 2
                ),
                "p_valor": round(float(p), 4) if p == p else np.nan,
                "significativo": bool(p < alpha) if p == p else False,
                "tamano_efecto": round(float(efecto), 3) if efecto == efecto else np.nan,
            }
        )
    return pd.DataFrame(filas)
