"""Generador de eventos dinámicos calibrado con estadísticas SUTRAN/MTC.

Los eventos se generan de forma estocástica pero reproducible, siguiendo la
Tabla 1 del plan de tesis (27.6 % de **rutas** afectadas por bloqueos o clima en
2025) y los parámetros declarados en ``config/escenario_norte.yaml``.

Nota metodológica
-----------------
La unidad del indicador SUTRAN es la **ruta**, no el tramo. Una ruta del
corredor atraviesa cientos de tramos, por lo que la probabilidad por tramo debe
ser muy baja para que la probabilidad de que la ruta resulte afectada sea del
27.6 %. El generador calibra esa probabilidad por tramo a partir del objetivo
declarado (ver ``_calibrar``), en lugar de usar la probabilidad por tramo como
si fuera la de la ruta.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import pandas as pd

from ..comun.config import Escenario

TIPOS = ("congestion", "bloqueo", "clima", "averia")

DESCRIPCIONES = {
    "congestion": "Congestión vehicular por alta demanda",
    "bloqueo": "Bloqueo de vía / restricción de tránsito",
    "clima": "Condiciones climáticas adversas (lluvia, neblina)",
    "averia": "Avería mecánica de la unidad",
}

# Objetivo SUTRAN 2025: proporción de rutas afectadas por bloqueos o clima.
TASA_RUTAS_OBJETIVO = 0.276
# Tramos que recorre una ruta típica del corredor (Lima→Piura ≈ 250 tramos).
TRAMOS_POR_RUTA = 250


@dataclass
class GeneradorEventos:
    escenario: Escenario
    tramos: pd.DataFrame
    semilla: int = 42

    def __post_init__(self) -> None:
        self.rng = random.Random(self.semilla)
        self.cfg = self.escenario.eventos
        self.riesgo_zona = self.cfg.get("riesgo_por_zona", {})
        self.objetivo_rutas = float(
            self.cfg.get("tasa_rutas_objetivo", TASA_RUTAS_OBJETIVO)
        )
        self.tramos_por_ruta = int(
            self.cfg.get("tramos_por_ruta", TRAMOS_POR_RUTA)
        )
        self.escala = self._calibrar()

    # ------------------------------------------------------------------
    def _exposicion(self, distancia_km: float) -> float:
        """Peso por longitud: los tramos largos concentran más incidentes."""
        return min(max(float(distancia_km) / 55.0, 0.25), 1.6)

    def _p_tramo(self, distancia_km: float, zona: str) -> float:
        """Probabilidad de que un tramo sufra al menos un evento en 24 h."""
        factor_zona = float(self.riesgo_zona.get(zona, 1.0))
        expo = self._exposicion(distancia_km)
        p_ninguno = 1.0
        for p_base in self.cfg["probabilidad_base"].values():
            p = min(float(p_base) * factor_zona * expo * self.escala, 0.95)
            p_ninguno *= (1.0 - p)
        return 1.0 - p_ninguno

    def _calibrar(self) -> float:
        """Escala las probabilidades para reproducir la tasa de rutas objetivo.

        Si una ruta recorre ``T`` tramos con probabilidad ``p`` de ser afectado
        cada uno, la probabilidad de que la ruta resulte afectada es
        ``1 - (1-p)^T``. Se despeja ``p`` del objetivo SUTRAN y se obtiene la
        escala dividiendo por la probabilidad media sin escalar.
        """
        if self.tramos.empty or self.objetivo_rutas <= 0:
            return 1.0

        suma_base = 0.0
        for _, t in self.tramos.iterrows():
            zona = str(t.get("zona", ""))
            fz = float(self.riesgo_zona.get(zona, 1.0))
            expo = self._exposicion(t.get("distancia_km", 10.0))
            suma_base += sum(float(p) for p in self.cfg["probabilidad_base"].values()) * fz * expo
        media_base = suma_base / len(self.tramos)
        if media_base <= 0:
            return 1.0

        p_objetivo = 1.0 - (1.0 - self.objetivo_rutas) ** (1.0 / self.tramos_por_ruta)
        return p_objetivo / media_base

    # ------------------------------------------------------------------
    def _elegir_tipo(self) -> str:
        probs = self.cfg["probabilidad_base"]
        tipos = list(probs)
        pesos = [probs[t] for t in tipos]
        return self.rng.choices(tipos, weights=pesos, k=1)[0]

    def _tramo_aleatorio(self) -> pd.Series:
        # Sesgo hacia tramos largos: son los que concentran el riesgo.
        pesos = self.tramos["distancia_km"].clip(lower=1.0) ** 0.6
        idx = self.rng.choices(self.tramos.index, weights=pesos.tolist(), k=1)[0]
        return self.tramos.loc[idx]

    def generar(self, horizonte_horas: float = 24.0, n_max: int = 200_000) -> pd.DataFrame:
        """Genera los eventos del horizonte recorriendo **todos** los tramos.

        El tope ``n_max`` existe solo como salvaguarda; antes se usaba 500 y el
        recorrido se cortaba en los primeros tramos (los de Lima), lo que
        sesgaba todos los eventos a la zona metropolitana.
        """
        filas: list[dict] = []
        contador = 0
        probs = self.cfg["probabilidad_base"]
        escala_tiempo = horizonte_horas / 24.0

        for _, t in self.tramos.iterrows():
            zona = str(t.get("zona", ""))
            factor_zona = float(self.riesgo_zona.get(zona, 1.0))
            expo = self._exposicion(t.get("distancia_km", 10.0))

            for tipo, p_base in probs.items():
                p = min(p_base * factor_zona * expo * self.escala * escala_tiempo, 0.95)
                if self.rng.random() > p:
                    continue

                dur_rng = self.cfg["duracion_horas"][tipo]
                sev_rng = self.cfg["severidad"][tipo]

                duracion = min(self.rng.uniform(*dur_rng), horizonte_horas)
                severidad = self.rng.uniform(*sev_rng) * (1.0 + 0.15 * (factor_zona - 1.0))
                inicio = self.rng.uniform(0, max(horizonte_horas - duracion, 0.1))
                fin = min(inicio + duracion, horizonte_horas)

                contador += 1
                filas.append(
                    {
                        "id_evento": f"EV{contador:05d}",
                        "id_tramo": t["id_tramo"],
                        "tipo_evento": tipo,
                        "hora_inicio": round(inicio, 2),
                        "hora_fin": round(fin, 2),
                        "severidad": round(severidad, 3),
                        "fuente": "simulador",
                        "descripcion": DESCRIPCIONES[tipo],
                        "zona": zona,
                    }
                )
                if len(filas) >= n_max:
                    break
            if len(filas) >= n_max:
                break

        df = pd.DataFrame(filas)
        if not df.empty:
            df = df.sort_values("hora_inicio").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    def tasa_afectacion(self, horizonte_horas: float = 24.0) -> float:
        """% de **tramos** con al menos un evento en el horizonte."""
        if self.tramos.empty:
            return 0.0
        total = 0.0
        for _, t in self.tramos.iterrows():
            total += self._p_tramo(t.get("distancia_km", 10.0), str(t.get("zona", "")))
        return 100.0 * total / len(self.tramos) * (horizonte_horas / 24.0)

    def tasa_afectacion_rutas(self, horizonte_horas: float = 24.0) -> float:
        """% de **rutas** que atraviesan al menos un tramo afectado.

        Es el indicador comparable con la Tabla 1 del plan de tesis
        (27.6 % de rutas afectadas por bloqueos o clima en 2025).
        """
        p_tramo = self.tasa_afectacion(horizonte_horas) / 100.0
        return 100.0 * (1.0 - (1.0 - p_tramo) ** self.tramos_por_ruta)

