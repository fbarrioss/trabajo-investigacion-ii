"""Bucle de entrenamiento y evaluación para QMIX y MAPPO."""
from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..agentes.mappo import AgenteMAPPO
from ..agentes.qmix import AgenteQMIX
from ..comun.config import ARTIFACTS_DIR, ConfigEntrenamiento, Escenario, RAIZ
from ..datos.generar_operacion import DatasetOperativo, generar_dataset
from ..entorno.entorno_rutas import DIM_OBS, N_ACCIONES, EntornoRutasCarga
from ..metricas import evaluar_episodio
from ..red_vial.construir_red import RedVial, cargar_red, construir_red

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def cargar_recursos(
    esc: Escenario | None = None,
    cfg: ConfigEntrenamiento | None = None,
    n_viajes: int = 120,
    usar_cache: bool = True,
) -> tuple[RedVial, dict[str, pd.DataFrame], Escenario, ConfigEntrenamiento]:
    """Carga (o construye) red vial y dataset operativo."""
    esc = esc or Escenario.cargar()
    cfg = cfg or ConfigEntrenamiento.cargar()

    dir_red = RAIZ / "data" / "processed" / "red_vial"
    if usar_cache and (dir_red / "nodos.csv").exists():
        log.info("Cargando red vial desde caché: %s", dir_red)
        red = cargar_red(dir_red)
        red.metadatos = json.loads((dir_red / "metadatos.json").read_text("utf-8")) \
            if (dir_red / "metadatos.json").exists() else {}
    else:
        log.info("Construyendo red vial (modo=%s)...", esc.red_vial.get("modo"))
        red = construir_red(esc)

    dir_ds = RAIZ / "data" / "processed" / "dataset"
    if usar_cache and (dir_ds / "viajes.parquet").exists():
        log.info("Cargando dataset operativo desde caché: %s", dir_ds)
        ds = {
            p.stem: pd.read_parquet(p)
            for p in dir_ds.glob("*.parquet")
        }
    else:
        log.info("Generando dataset operativo (%d viajes)...", n_viajes)
        ds = generar_dataset(red, esc, n_viajes=n_viajes).tablas()

    return red, ds, esc, cfg


def crear_entorno(
    red: RedVial,
    ds: dict[str, pd.DataFrame],
    esc: Escenario,
    cfg: ConfigEntrenamiento,
    semilla: int | None = None,
) -> EntornoRutasCarga:
    return EntornoRutasCarga(red, ds, esc, cfg, semilla=semilla)


# ---------------------------------------------------------------------------
@dataclass
class ResultadoEntrenamiento:
    algoritmo: str
    historial: pd.DataFrame
    ruta_modelo: Path
    duracion_s: float


def _registro_csv(ruta: Path, fila: dict[str, Any]) -> None:
    nuevo = not ruta.exists()
    with open(ruta, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fila))
        if nuevo:
            writer.writeheader()
        writer.writerow(fila)


# ---------------------------------------------------------------------------
def entrenar(
    algoritmo: str = "qmix",
    episodios: int | None = None,
    semilla: int = 1,
    n_viajes: int = 120,
    guardar: bool = True,
) -> ResultadoEntrenamiento:
    """Entrena el algoritmo indicado y devuelve el historial."""
    t0 = time.time()
    red, ds, esc, cfg = cargar_recursos(n_viajes=n_viajes)
    env = crear_entorno(red, ds, esc, cfg, semilla=semilla)

    cfg_alg = cfg[algoritmo]
    device = cfg["entrenamiento"].get("device", "cpu")
    episodios = episodios or int(cfg["entrenamiento"]["episodios"])

    if algoritmo == "qmix":
        agente: Any = AgenteQMIX(
            env.n_agentes, DIM_OBS, N_ACCIONES, env.dim_estado_global,
            cfg_alg, device=device, semilla=semilla,
        )
    elif algoritmo == "mappo":
        agente = AgenteMAPPO(
            env.n_agentes, DIM_OBS, N_ACCIONES, env.dim_estado_global,
            cfg_alg, device=device, semilla=semilla,
        )
    else:
        raise ValueError(f"Algoritmo no soportado: {algoritmo}")

    dir_logs = ARTIFACTS_DIR / "logs"
    dir_logs.mkdir(parents=True, exist_ok=True)
    ruta_log = dir_logs / f"{algoritmo}_semilla{semilla}.csv"

    historial: list[dict[str, Any]] = []
    eval_cada = int(cfg["entrenamiento"]["eval_cada"])
    mejor = -np.inf
    ruta_modelo = ARTIFACTS_DIR / "modelos" / f"{algoritmo}_s{semilla}.pt"

    for ep in range(1, episodios + 1):
        obs, estado = env.reset(semilla=semilla * 1000 + ep)
        recompensa_total = 0.0
        perdida_ultima = None

        for _ in range(env.horizonte):
            mascara = env.mascara_acciones()

            if algoritmo == "qmix":
                acciones = agente.actuar(obs, mascara, explorar=True)
                obs_next, rew, dones, info = env.step(acciones)
                r_compartida = float(np.mean(rew))
                agente.almacenar(
                    obs, estado, acciones, r_compartida,
                    obs_next, env._estado_global(), bool(dones.all()),
                    env.mascara_acciones(),
                )
                perdida = agente.entrenar()
                if perdida is not None:
                    perdida_ultima = perdida
            else:
                acciones, logprobs = agente.actuar(obs, mascara, explorar=True)
                obs_next, rew, dones, info = env.step(acciones)
                r_compartida = float(np.mean(rew))
                agente.almacenar(
                    obs, estado, acciones, logprobs, r_compartida, bool(dones.all())
                )
                if len(agente.recompensas) >= agente.batch:
                    res = agente.entrenar()
                    if res:
                        perdida_ultima = res["perdida_actor"]

            recompensa_total += r_compartida
            obs, estado = obs_next, env._estado_global()
            if dones.all():
                break

        met = evaluar_episodio(env, info, recompensa_total)
        fila = {
            "episodio": ep,
            "algoritmo": algoritmo,
            "semilla": semilla,
            "recompensa": round(recompensa_total, 4),
            "perdida": round(perdida_ultima, 6) if perdida_ultima is not None else "",
            **met,
        }
        historial.append(fila)
        _registro_csv(ruta_log, fila)

        if ep % 25 == 0:
            log.info(
                "[%s s%d] ep %d | R=%.2f | costo=%.0f | puntualidad=%.1f%%",
                algoritmo, semilla, ep, recompensa_total,
                met["costo_total_pen"], met["puntualidad_pct"],
            )

        if ep % eval_cada == 0:
            punt = np.mean([h["recompensa"] for h in historial[-eval_cada:]])
            if punt > mejor:
                mejor = punt
                if guardar:
                    agente.guardar(ruta_modelo)

    if guardar:
        agente.guardar(ruta_modelo)

    return ResultadoEntrenamiento(
        algoritmo=algoritmo,
        historial=pd.DataFrame(historial),
        ruta_modelo=ruta_modelo,
        duracion_s=time.time() - t0,
    )


# ---------------------------------------------------------------------------
def evaluar_politica(
    env: EntornoRutasCarga,
    politica: Callable[[Any], np.ndarray],
    episodios: int = 20,
    semilla: int = 1,
) -> pd.DataFrame:
    """Evalúa una política (aprendida o baseline) durante N episodios."""
    filas = []
    for ep in range(episodios):
        obs, _ = env.reset(semilla=semilla * 7919 + ep)
        recompensa_total = 0.0
        info: dict[str, Any] = {}
        for _ in range(env.horizonte):
            acciones = politica(env)
            obs, rew, dones, info = env.step(acciones)
            recompensa_total += float(np.mean(rew))
            if dones.all():
                break
        fila = evaluar_episodio(env, info, recompensa_total)
        fila["episodio"] = ep
        filas.append(fila)
    return pd.DataFrame(filas)
