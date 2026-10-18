"""Redes neuronales compartidas por QMIX y MAPPO (PyTorch)."""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def mlp(entrada: int, oculto: int, salida: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(entrada, oculto),
        nn.ReLU(),
        nn.Linear(oculto, oculto),
        nn.ReLU(),
        nn.Linear(oculto, salida),
    )


class RedAgente(nn.Module):
    """Q individual por agente (parámetros compartidos entre agentes)."""

    def __init__(self, dim_obs: int, n_acciones: int, oculto: int = 64) -> None:
        super().__init__()
        self.fc1 = nn.Linear(dim_obs, oculto)
        self.fc2 = nn.Linear(oculto, oculto)
        self.cabeza = nn.Linear(oculto, n_acciones)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = F.relu(self.fc1(x))
        h = F.relu(self.fc2(h))
        return self.cabeza(h)


class MezcladorQMIX(nn.Module):
    """Mezclador monotónico: Q_tot = f(Q_1..Q_n, estado global).

    Garantiza ∂Q_tot/∂Q_i ≥ 0 mediante pesos no negativos, lo que preserva la
    consistencia individual-global (IGM) necesaria para la ejecución
    descentralizada.
    """

    def __init__(self, n_agentes: int, dim_estado: int, embed: int = 32) -> None:
        super().__init__()
        self.n_agentes = n_agentes
        self.embed = embed
        self.hiper_w1 = nn.Linear(dim_estado, n_agentes * embed)
        self.hiper_b1 = nn.Linear(dim_estado, embed)
        self.hiper_w2 = nn.Linear(dim_estado, embed)
        self.hiper_b2 = nn.Linear(dim_estado, 1)
        self.w1 = nn.Linear(n_agentes, embed, bias=False)
        self.w2 = nn.Linear(embed, 1, bias=False)
        self.ultima_capa = nn.Linear(embed, 1, bias=False)

    def forward(self, qs: torch.Tensor, estado: torch.Tensor) -> torch.Tensor:
        # qs: (B, n_agentes) | estado: (B, dim_estado)
        b = qs.shape[0]
        w1 = torch.abs(self.hiper_w1(estado)).view(b, self.n_agentes, self.embed)
        b1 = self.hiper_b1(estado).view(b, 1, self.embed)
        h = F.elu(torch.bmm(qs.view(b, 1, self.n_agentes), w1) + b1)

        w2 = torch.abs(self.hiper_w2(estado)).view(b, self.embed, 1)
        b2 = self.hiper_b2(estado).view(b, 1, 1)
        h = F.elu(torch.bmm(h, w2) + b2)
        return h.view(b)


class CriticoCentral(nn.Module):
    """Crítico centralizado para MAPPO (usa el estado global)."""

    def __init__(self, dim_estado: int, oculto: int = 64) -> None:
        super().__init__()
        self.red = mlp(dim_estado, oculto, 1)

    def forward(self, estado: torch.Tensor) -> torch.Tensor:
        return self.red(estado).squeeze(-1)


class ActorMAPPO(nn.Module):
    """Actor con parámetros compartidos y cabeza categórica por agente."""

    def __init__(self, dim_obs: int, n_acciones: int, oculto: int = 64) -> None:
        super().__init__()
        self.cuerpo = nn.Sequential(
            nn.Linear(dim_obs, oculto), nn.Tanh(),
            nn.Linear(oculto, oculto), nn.Tanh(),
        )
        self.logits = nn.Linear(oculto, n_acciones)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.logits(self.cuerpo(obs))

    def distribucion(self, obs: torch.Tensor) -> torch.distributions.Categorical:
        return torch.distributions.Categorical(logits=self.forward(obs))
