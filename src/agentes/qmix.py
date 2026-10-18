"""QMIX: aprendizaje por refuerzo multiagente cooperativo con CTDE.

Implementación propia (no depende de PyMARL) con:
  * red de agente compartida (DRQN feed-forward),
  * mezclador monotónico con hiperredes condicionadas por el estado global,
  * target networks + buffer de repetición,
  * exploración ε-greedy con máscara de acciones válidas.
"""
from __future__ import annotations

import random
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from .redes import MezcladorQMIX, RedAgente


class BufferEpisodios:
    """Buffer circular de transiciones multiagente."""

    def __init__(self, capacidad: int = 50_000) -> None:
        self.memoria: deque = deque(maxlen=capacidad)

    def agregar(self, transicion: dict[str, Any]) -> None:
        self.memoria.append(transicion)

    def muestrear(self, batch: int) -> dict[str, torch.Tensor]:
        lote = random.sample(self.memoria, min(batch, len(self.memoria)))
        return {
            "obs": torch.as_tensor(np.stack([t["obs"] for t in lote]), dtype=torch.float32),
            "estado": torch.as_tensor(np.stack([t["estado"] for t in lote]), dtype=torch.float32),
            "acciones": torch.as_tensor(np.stack([t["acciones"] for t in lote]), dtype=torch.long),
            "recompensa": torch.as_tensor([t["recompensa"] for t in lote], dtype=torch.float32),
            "obs_next": torch.as_tensor(np.stack([t["obs_next"] for t in lote]), dtype=torch.float32),
            "estado_next": torch.as_tensor(np.stack([t["estado_next"] for t in lote]), dtype=torch.float32),
            "done": torch.as_tensor([t["done"] for t in lote], dtype=torch.float32),
            "mascara_next": torch.as_tensor(np.stack([t["mascara_next"] for t in lote]), dtype=torch.bool),
        }

    def __len__(self) -> int:
        return len(self.memoria)


class AgenteQMIX:
    """Entrenador QMIX para el entorno de rutas de carga."""

    def __init__(
        self,
        n_agentes: int,
        dim_obs: int,
        n_acciones: int,
        dim_estado: int,
        cfg: dict[str, Any],
        device: str = "cpu",
        semilla: int = 42,
    ) -> None:
        self.n_agentes = n_agentes
        self.dim_obs = dim_obs
        self.n_acciones = n_acciones
        self.dim_estado = dim_estado
        self.cfg = cfg
        self.device = torch.device(device)

        torch.manual_seed(semilla)
        np.random.seed(semilla)
        random.seed(semilla)

        oculto = int(cfg.get("hidden_dim", 64))
        self.red = RedAgente(dim_obs, n_acciones, oculto).to(self.device)
        self.red_target = RedAgente(dim_obs, n_acciones, oculto).to(self.device)
        self.mezclador = MezcladorQMIX(
            n_agentes, dim_estado, int(cfg.get("mixing_embed_dim", 32))
        ).to(self.device)
        self.mezclador_target = MezcladorQMIX(
            n_agentes, dim_estado, int(cfg.get("mixing_embed_dim", 32))
        ).to(self.device)

        self.red_target.load_state_dict(self.red.state_dict())
        self.mezclador_target.load_state_dict(self.mezclador.state_dict())

        params = list(self.red.parameters()) + list(self.mezclador.parameters())
        self.optimizador = torch.optim.Adam(params, lr=float(cfg.get("lr", 5e-4)))
        self.perdida = nn.MSELoss()

        self.gamma = float(cfg.get("gamma", 0.99))
        self.batch_size = int(cfg.get("batch_size", 32))
        self.target_update = int(cfg.get("target_update_interval", 200))
        self.grad_clip = float(cfg.get("grad_norm_clip", 10.0))
        self.buffer = BufferEpisodios(int(cfg.get("buffer_size", 50_000)))

        self.epsilon = float(cfg.get("epsilon_inicio", 1.0))
        self.epsilon_fin = float(cfg.get("epsilon_fin", 0.05))
        self.epsilon_decay = int(cfg.get("epsilon_decay_pasos", 20_000))
        self.pasos = 0

    # ------------------------------------------------------------------
    def _eps_actual(self) -> float:
        if self.epsilon_decay <= 0:
            return self.epsilon_fin
        frac = min(self.pasos / self.epsilon_decay, 1.0)
        return self.epsilon + frac * (self.epsilon_fin - self.epsilon)

    # ------------------------------------------------------------------
    @torch.no_grad()
    def q_valores(self, obs: np.ndarray, red: nn.Module | None = None) -> np.ndarray:
        red = red or self.red
        x = torch.as_tensor(obs, dtype=torch.float32, device=self.device)
        return red(x).cpu().numpy()

    def actuar(
        self, obs: np.ndarray, mascara: np.ndarray, explorar: bool = True
    ) -> np.ndarray:
        """Selecciona acciones con ε-greedy y enmascarado."""
        q = self.q_valores(obs)
        acciones = np.zeros(self.n_agentes, dtype=int)
        eps = self._eps_actual() if explorar else 0.0

        for i in range(self.n_agentes):
            validas = np.flatnonzero(mascara[i])
            if validas.size == 0:
                acciones[i] = 0
                continue
            if explorar and np.random.random() < eps:
                acciones[i] = int(np.random.choice(validas))
            else:
                q_i = q[i].copy()
                q_i[~mascara[i]] = -np.inf
                acciones[i] = int(np.argmax(q_i))
        return acciones

    # ------------------------------------------------------------------
    def almacenar(
        self,
        obs: np.ndarray,
        estado: np.ndarray,
        acciones: np.ndarray,
        recompensa: float,
        obs_next: np.ndarray,
        estado_next: np.ndarray,
        done: bool,
        mascara_next: np.ndarray,
    ) -> None:
        self.buffer.agregar(
            {
                "obs": obs,
                "estado": estado,
                "acciones": acciones,
                "recompensa": float(recompensa),
                "obs_next": obs_next,
                "estado_next": estado_next,
                "done": float(done),
                "mascara_next": mascara_next,
            }
        )
        self.pasos += 1

    # ------------------------------------------------------------------
    def entrenar(self) -> float | None:
        if len(self.buffer) < self.batch_size:
            return None

        lote = self.buffer.muestrear(self.batch_size)
        obs = lote["obs"].to(self.device)
        obs_next = lote["obs_next"].to(self.device)
        estado = lote["estado"].to(self.device)
        estado_next = lote["estado_next"].to(self.device)
        acciones = lote["acciones"].to(self.device)
        r = lote["recompensa"].to(self.device)
        done = lote["done"].to(self.device)
        mascara_next = lote["mascara_next"].to(self.device)

        b, n, d = obs.shape
        q = self.red(obs.reshape(b * n, d)).reshape(b, n, self.n_acciones)
        q_sel = q.gather(2, acciones.unsqueeze(2)).squeeze(2)          # (B, n)
        q_tot = self.mezclador(q_sel, estado)

        with torch.no_grad():
            qn = self.red_target(obs_next.reshape(b * n, d)).reshape(
                b, n, self.n_acciones
            )
            qn = qn.masked_fill(~mascara_next, -1e9)
            qn_max = qn.max(dim=2).values
            q_tot_next = self.mezclador_target(qn_max, estado_next)
            objetivo = r + self.gamma * (1.0 - done) * q_tot_next

        perdida = self.perdida(q_tot, objetivo)

        self.optimizador.zero_grad()
        perdida.backward()
        nn.utils.clip_grad_norm_(
            list(self.red.parameters()) + list(self.mezclador.parameters()),
            self.grad_clip,
        )
        self.optimizador.step()

        if self.pasos % self.target_update == 0:
            self.red_target.load_state_dict(self.red.state_dict())
            self.mezclador_target.load_state_dict(self.mezclador.state_dict())

        return float(perdida.item())

    # ------------------------------------------------------------------
    def guardar(self, ruta: str | Path) -> Path:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "red": self.red.state_dict(),
                "mezclador": self.mezclador.state_dict(),
                "config": self.cfg,
                "dim_obs": self.dim_obs,
                "dim_estado": self.dim_estado,
                "n_agentes": self.n_agentes,
                "n_acciones": self.n_acciones,
            },
            ruta,
        )
        return ruta

    def cargar(self, ruta: str | Path) -> None:
        ckpt = torch.load(ruta, map_location=self.device, weights_only=False)
        self.red.load_state_dict(ckpt["red"])
        self.mezclador.load_state_dict(ckpt["mezclador"])
        self.red_target.load_state_dict(ckpt["red"])
        self.mezclador_target.load_state_dict(ckpt["mezclador"])
