"""Utilidades geográficas: distancias, interpolación y snapping."""
from __future__ import annotations

import math
from typing import Iterable

R_TIERRA_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia ortodrómica en kilómetros."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_TIERRA_KM * math.asin(math.sqrt(a))


def distancia_polilinea_km(puntos: Iterable[tuple[float, float]]) -> float:
    """Longitud acumulada de una polilínea [(lat, lon), ...] en km."""
    pts = list(puntos)
    return sum(
        haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for i in range(len(pts) - 1)
    )


def interpolar(
    lat1: float, lon1: float, lat2: float, lon2: float, frac: float
) -> tuple[float, float]:
    """Interpolación lineal simple entre dos coordenadas."""
    return (lat1 + (lat2 - lat1) * frac, lon1 + (lon2 - lon1) * frac)


def punto_en_poligono(lat: float, lon: float, anillo: list[tuple[float, float]]) -> bool:
    """Ray casting: True si (lat, lon) cae dentro del anillo [(lat, lon), ...]."""
    dentro = False
    n = len(anillo)
    j = n - 1
    for i in range(n):
        yi, xi = anillo[i]
        yj, xj = anillo[j]
        if ((xi > lon) != (xj > lon)) and (
            lat < (yj - yi) * (lon - xi) / (xj - xi + 1e-12) + yi
        ):
            dentro = not dentro
        j = i
    return dentro


def muestrear_puntos_en_disco(
    lat_c: float, lon_c: float, radio_km: float, n: int, semilla: int
) -> list[tuple[float, float]]:
    """Genera n puntos pseudoaleatorios reproducibles dentro de un disco.

    Se usa para georreferenciar distribuidores placeholder dentro del área
    urbana de cada ciudad hasta contar con las direcciones reales.
    """
    import random

    rng = random.Random(semilla)
    puntos: list[tuple[float, float]] = []
    # Distribución en anillos concéntricos para evitar aglomeración central.
    for i in range(n):
        r = radio_km * math.sqrt((i + 0.5) / max(n, 1))
        theta = rng.uniform(0, 2 * math.pi)
        dlat = (r / 111.32) * math.cos(theta)
        dlon = (r / (111.32 * math.cos(math.radians(lat_c)))) * math.sin(theta)
        puntos.append((round(lat_c + dlat, 6), round(lon_c + dlon, 6)))
    return puntos


def rumbo_grados(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Rumbo inicial (0-360) entre dos puntos."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360
