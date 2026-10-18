"""MAPPO: PPO multiagente con crítico centralizado (CTDE).

Actor compartido (ejecución descentralizada) + crítico que observa el estado
global (entrenamiento centralizado). Implementa GAE y recorte de política.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from .redes import ActorMAPPO, CriticoCentral


class AgenteMAPPO:
    """Entrenador MAPPO con buffer on-policy."""

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

        oculto = int(cfg.get("hidden_dim", 64))
        self.actor = ActorMAPPO(dim_obs, n_acciones, oculto).to(self.device)
        self.critico = CriticoCentral(dim_estado, oculto).to(self.device)

        self.opt_actor = torch.optim.Adam(
            self.actor.parameters(), lr=float(cfg.get("lr_actor", 3e-4))
        )
        self.opt_critico = torch.optim.Adam(
            self.critico.parameters(), lr=float(cfg.get("lr_critico", 5e-4))
        )

        self.gamma = float(cfg.get("gamma", 0.99))
        self.lam = float(cfg.get("lambda_gae", 0.95))
        self.clip = float(cfg.get("clip_epsilon", 0.2))
        self.epochs = int(cfg.get("epochs", 4))
        self.batch = int(cfg.get("batch_size", 2048))

        self.limpiar()

    # ------------------------------------------------------------------
    def limpiar(self) -> None:
        self.obs: list[np.ndarray] = []
        self.estados: list[np.ndarray] = []
        self.acciones: list[np.ndarray] = []
        self.logprobs: list[np.ndarray] = []
        self.recompensas: list[float] = []
        self.dones: list[float] = []

    # ------------------------------------------------------------------
    @torch.no_grad()
    def actuar(
        self, obs: np.ndarray, mascara: np.ndarray, explorar: bool = True
    ) -> tuple[np.ndarray, np.ndarray]:
        x = torch.as_tensor(obs, dtype=torch.float32, device=self.device)
        logits = self.actor(x)
        logits = logits.masked_fill(
            ~torch.as_tensor(mascara, device=self.device), -1e9
        )
        dist = torch.distributions.Categorical(logits=logits)

        if explorar:
            acc = dist.sample()
        else:
            acc = torch.argmax(logits, dim=-1)
        return acc.cpu().numpy(), dist.log_prob(acc).cpu().numpy()

    def almacenar(
        self,
        obs: np.ndarray,
        estado: np.ndarray,
        acciones: np.ndarray,
        logprobs: np.ndarray,
        recompensa: float,
        done: bool,
    ) -> None:
        self.obs.append(obs)
        self.estados.append(estado)
        self.acciones.append(acciones)
        self.logprobs.append(logprobs)
        self.recompensas.append(float(recompensa))
        self.dones.append(float(done))

    # ------------------------------------------------------------------
    def _gae(self, valores: np.ndarray, recompensas: np.ndarray, dones: np.ndarray):
        ventajas = np.zeros_like(recompensas, dtype=np.float32)
        ultima = 0.0
        for t in reversed(range(len(recompensas))):
            siguiente = valores[t + 1] if t + 1 < len(valores) else 0.0
            delta = recompensas[t] + self.gamma * siguiente * (1 - dones[t]) - valores[t]
            ultima = delta + self.gamma * self.lam * (1 - dones[t]) * ultima
            ventajas[t] = ultima
        retornos = ventajas + valores[: len(recompensas)]
        return ventajas, retornos

    # ------------------------------------------------------------------
    def entrenar(self) -> dict[str, float] | None:
        if len(self.recompensas) < self.batch:
            return None

        obs = torch.as_tensor(np.stack(self.obs), dtype=torch.float32, device=self.device)
        estados = torch.as_tensor(
            np.stack(self.estados), dtype=torch.float32, device=self.device
        )
        acciones = torch.as_tensor(
            np.stack(self.acciones), dtype=torch.long, device=self.device
        )
        logprobs_viejos = torch.as_tensor(
            np.stack(self.logprobs), dtype=torch.float32, device=self.device
        )
        recompensas = np.asarray(self.recompensas, dtype=np.float32)
        dones = np.asarray(self.dones, dtype=np.float32)

        with torch.no_grad():
            valores = self.critico(estados).cpu().numpy()
            valores_ext = np.append(valores, 0.0)

        # Recompensa cooperativa: se comparte el promedio entre agentes.
        ventajas, retornos = self._gae(valores_ext, recompensas, dones)
        ventajas_t = torch.as_tensor(ventajas, device=self.device)
        ventajas_t = (ventajas_t - ventajas_t.mean()) / (ventajas_t.std() + 1e-8)
        retornos_t = torch.as_tensor(retornos, device=self.device)

        t_steps, n_ag, _ = obs.shape
        obs_f = obs.reshape(t_steps * n_ag, -1)
        acc_f = acciones.reshape(t_steps * n_ag)
        lp_f = logprobs_viejos.reshape(t_steps * n_ag)
        ven_f = ventajas_t.repeat_interleave(n_ag)
        ret_f = retornos_t

        perdida_actor_total = 0.0
        perdida_critico_total = 0.0
        n_lotes = 0

        for _ in range(self.epochs):
            perm = torch.randperm(obs_f.shape[0], device=self.device)
            for i in range(0, obs_f.shape[0], 256):
                idx = perm[i : i + 256]
                dist = torch.distributions.Categorical(logits=self.actor(obs_f[idx]))
                lp = dist.log_prob(acc_f[idx])
                ratio = torch.exp(lp - lp_f[idx])
                obj = torch.minimum(
                    ratio * ven_f[idx],
                    torch.clamp(ratio, 1 - self.clip, 1 + self.clip) * ven_f[idx],
                )
                perdida_actor = -obj.mean()

                self.opt_actor.zero_grad()
                perdida_actor.backward()
                nn.utils.clip_grad_norm_(self.actor.parameters(), 0.5)
                self.opt_actor.step()

                valores_pred = self.critico(estados[idx // n_ag])
                perdida_critico = nn.functional.mse_loss(
                    valores_pred, ret_f[idx // n_ag]
                )
                self.opt_critico.zero_grad()
                perdida_critico.backward()
                nn.utils.clip_grad_norm_(self.critico.parameters(), 0.5)
                self.opt_critico.step()

                perdida_actor_total += float(perdida_actor.item())
                perdida_critico_total += float(perdida_critico.item())
                n_lotes += 1

        self.limpiar()
        return {
            "perdida_actor": perdida_actor_total / max(n_lotes, 1),
            "perdida_critico": perdida_critico_total / max(n_lotes, 1),
            "retorno_medio": float(retornos.mean()),
        }

    # ------------------------------------------------------------------
    def guardar(self, ruta: str | Path) -> Path:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "actor": self.actor.state_dict(),
                "critico": self.critico.state_dict(),
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
        self.actor.load_state_dict(ckpt["actor"])
        self.critico.load_state_dict(ckpt["critico"])
