"""Evaluación comparativa: política MARL vs baselines tradicionales."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..agentes.baselines import BASELINES
from ..agentes.qmix import AgenteQMIX
from ..comun.config import ARTIFACTS_DIR, ConfigEntrenamiento
from ..entorno.entorno_rutas import DIM_OBS, N_ACCIONES
from ..metricas import comparar_con_baseline, resumir_resultados
from .entrenar import cargar_recursos, crear_entorno, evaluar_politica

log = logging.getLogger(__name__)


def cargar_politica_qmix(ruta: Path, env, cfg: ConfigEntrenamiento):
    """Carga un modelo QMIX y devuelve una política determinista."""
    ckpt_cfg = cfg["qmix"]
    agente = AgenteQMIX(
        env.n_agentes, DIM_OBS, N_ACCIONES, env.dim_estado_global,
        ckpt_cfg, semilla=1,
    )
    agente.cargar(ruta)

    def politica(entorno) -> np.ndarray:
        obs = entorno._observaciones()
        return agente.actuar(obs, entorno.mascara_acciones(), explorar=False)

    return politica


def evaluar_completo(
    ruta_modelo: str | Path | None = None,
    episodios: int = 20,
    semilla: int = 1,
    guardar: bool = True,
) -> dict[str, Any]:
    """Compara QMIX entrenado contra los tres baselines y reporta estadística."""
    red, ds, esc, cfg = cargar_recursos()
    env = crear_entorno(red, ds, esc, cfg, semilla=semilla)

    resultados: list[pd.DataFrame] = []

    # 1. Política aprendida.
    if ruta_modelo is not None and Path(ruta_modelo).exists():
        politica_qmix = cargar_politica_qmix(Path(ruta_modelo), env, cfg)
        df = evaluar_politica(env, politica_qmix, episodios, semilla)
        df["politica"] = "qmix"
        resultados.append(df)
        log.info("QMIX evaluado: %d episodios", len(df))
    else:
        log.warning("No se encontró modelo QMIX; se omiten sus resultados.")

    # 2. Baselines.
    for nombre, fn in BASELINES.items():
        df = evaluar_politica(env, fn, episodios, semilla)
        df["politica"] = nombre
        resultados.append(df)
        log.info("%s evaluado: %d episodios", nombre, len(df))

    df_todo = pd.concat(resultados, ignore_index=True)
    resumen = resumir_resultados(df_todo)

    contrastes = []
    for metrica in ["costo_total_pen", "tiempo_total_min", "puntualidad_pct",
                    "desviacion_ruta_km", "recompensa_promedio"]:
        c = comparar_con_baseline(df_todo, metrica, alpha=float(cfg["evaluacion"]["alpha"]))
        if not c.empty:
            contrastes.append(c)
    contrastes_df = pd.concat(contrastes, ignore_index=True) if contrastes else pd.DataFrame()

    if guardar:
        dir_out = ARTIFACTS_DIR / "evaluacion"
        dir_out.mkdir(parents=True, exist_ok=True)
        df_todo.to_csv(dir_out / "episodios.csv", index=False)
        resumen.to_csv(dir_out / "resumen.csv", index=False)
        contrastes_df.to_csv(dir_out / "contrastes.csv", index=False)
        log.info("Resultados guardados en %s", dir_out)

    return {
        "episodios": df_todo,
        "resumen": resumen,
        "contrastes": contrastes_df,
    }
