"""Carga y validación de la configuración del proyecto."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_DIR = RAIZ / "config"
DATA_DIR = RAIZ / "data"
ARTIFACTS_DIR = RAIZ / "artifacts"


def _ruta(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else RAIZ / p


def cargar_yaml(nombre: str) -> dict[str, Any]:
    """Carga un YAML de la carpeta config/."""
    ruta = CONFIG_DIR / nombre
    if not ruta.exists():
        raise FileNotFoundError(f"No existe la configuración: {ruta}")
    with open(ruta, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class Escenario:
    """Escenario operativo (corredor, ciudades, costos, flota, eventos)."""

    crudo: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def cargar(cls, nombre: str = "escenario_norte.yaml") -> "Escenario":
        return cls(crudo=cargar_yaml(nombre))

    # --- accesos frecuentes --------------------------------------------------
    @property
    def semilla(self) -> int:
        return int(self.crudo.get("semilla", 42))

    @property
    def ciudades(self) -> list[dict[str, Any]]:
        return self.crudo["ciudades"]

    @property
    def red_vial(self) -> dict[str, Any]:
        return self.crudo["red_vial"]

    @property
    def costos(self) -> dict[str, Any]:
        return self.crudo["costos"]

    @property
    def flota(self) -> dict[str, Any]:
        return self.crudo["flota"]

    @property
    def ventanas(self) -> dict[str, Any]:
        return self.crudo["ventanas"]

    @property
    def eventos(self) -> dict[str, Any]:
        return self.crudo["eventos"]

    @property
    def conexiones(self) -> list[tuple[str, str]]:
        """Pares (origen, destino) del corredor, en el orden de la imagen."""
        pares = [
            (c["origen"], c["destino"])
            for c in self.crudo.get("corredor", [])
        ]
        if pares:
            return pares
        # Respaldo: cadena según el orden declarado de ciudades.
        ids = [c["id"] for c in self.ciudades]
        return list(zip(ids[:-1], ids[1:]))

    def ciudad(self, cid: str) -> dict[str, Any]:
        for c in self.ciudades:
            if c["id"] == cid:
                return c
        raise KeyError(f"Ciudad no definida en el escenario: {cid}")

    def total_distribuidores(self) -> int:
        return sum(int(c.get("distribuidores", 0)) for c in self.ciudades)

    def validar(self) -> list[str]:
        """Validaciones mínimas de coherencia del escenario."""
        avisos: list[str] = []
        ids = [c["id"] for c in self.ciudades]
        if len(ids) != len(set(ids)):
            avisos.append("Hay identificadores de ciudad duplicados.")
        if not any(c.get("rol") == "hub_origen" for c in self.ciudades):
            avisos.append("No se definió ningún hub de origen.")
        if self.total_distribuidores() == 0:
            avisos.append("El escenario no tiene distribuidores definidos.")
        return avisos


@dataclass
class ConfigEntrenamiento:
    crudo: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def cargar(cls, nombre: str = "entrenamiento.yaml") -> "ConfigEntrenamiento":
        return cls(crudo=cargar_yaml(nombre))

    def __getitem__(self, clave: str) -> Any:
        return self.crudo[clave]

    def get(self, clave: str, defecto: Any = None) -> Any:
        return self.crudo.get(clave, defecto)
