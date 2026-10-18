"""Corredor vial curado (fallback reproducible sin conexión a OSM).

Contiene los puntos reales de paso de la Panamericana Norte (PE-1N) entre
Lima y Chulucanas. Se usa cuando:
  * no hay conectividad con Overpass/OSMnx, o
  * se requiere un grafo mínimo reproducible para pruebas unitarias.

Cada punto tiene: id, nombre, lat, lon, clase de vía de llegada y velocidad
de flujo libre (km/h). Las distancias se calculan con Haversine y se corrigen
por un factor de sinuosidad propio de cada tramo.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..comun.geo import haversine_km


@dataclass(frozen=True)
class PuntoCorredor:
    id: str
    nombre: str
    lat: float
    lon: float
    clase_via: str = "primary"
    velocidad_kmh: float = 80.0
    sinuosidad: float = 1.22
    peaje: bool = False


# --- Trayectoria real de la PE-1N (sur → norte) ------------------------------
CORREDOR_PE1N: list[PuntoCorredor] = [
    PuntoCorredor("LIM", "Lima", -12.0464, -77.0428, "urban", 50, 1.15),
    PuntoCorredor("ANC", "Ancón", -11.7750, -77.1700, "primary", 80, 1.18),
    PuntoCorredor("CHA", "Chancay", -11.5710, -77.2690, "primary", 85, 1.20, True),
    PuntoCorredor("HUA", "Huacho", -11.1070, -77.6050, "primary", 85, 1.20, True),
    PuntoCorredor("BAR", "Barranca", -10.7500, -77.7620, "primary", 85, 1.21, True),
    PuntoCorredor("PAR", "Paramonga", -10.6740, -77.8180, "primary", 85, 1.21),
    PuntoCorredor("CAS", "Casma", -9.4740, -78.3010, "primary", 85, 1.24, True),
    PuntoCorredor("CHB", "Chimbote", -9.0850, -78.5780, "trunk", 90, 1.15, True),
    PuntoCorredor("SRA", "Santa", -8.9880, -78.6110, "trunk", 90, 1.15),
    PuntoCorredor("VIR", "Virú", -8.4150, -78.7520, "trunk", 90, 1.14),
    PuntoCorredor("TRU", "Trujillo", -8.1116, -79.0287, "trunk", 85, 1.16),
    PuntoCorredor("PAI", "Paiján", -7.7333, -79.3000, "trunk", 90, 1.15),
    PuntoCorredor("PAC", "Pacasmayo", -7.4006, -79.5714, "trunk", 90, 1.16, True),
    PuntoCorredor("SPL", "San Pedro de Lloc", -7.4286, -79.5083, "trunk", 85, 1.18),
    PuntoCorredor("GUA", "Guadalupe", -7.2461, -79.4722, "trunk", 85, 1.18),
    PuntoCorredor("MOC", "Mocupe", -6.9833, -79.6167, "trunk", 85, 1.19),
    PuntoCorredor("REQ", "Reque", -6.8667, -79.8167, "trunk", 80, 1.20),
    PuntoCorredor("CHI", "Chiclayo", -6.7714, -79.8409, "urban", 55, 1.15),
    PuntoCorredor("LAM", "Lambayeque", -6.7011, -79.9067, "primary", 80, 1.18),
    PuntoCorredor("MOT", "Motupe", -6.1500, -79.7167, "primary", 85, 1.22),
    PuntoCorredor("OLM", "Olmos", -5.9833, -79.7500, "primary", 85, 1.25),
    PuntoCorredor("PIU", "Piura", -5.1945, -80.6328, "urban", 55, 1.15),
    PuntoCorredor("SUL", "Sullana", -4.9039, -80.6853, "primary", 70, 1.16),
]

# --- Alineamiento costero alterno (Chiclayo → Piura) -------------------------
# La PE-1N tiene dos alineamientos entre Chiclayo y Piura: el interior (vía
# Motupe y Olmos, declarado en CORREDOR_PE1N) y el costero, que es el que usan
# los motores de ruteo por ser más corto (214.7 km frente a ~270 km por Olmos).
# Sin estos puntos las bandas de descarga no cubren la costa y el grafo se ve
# obligado a rodear por el interior, inflando el tramo Chiclayo–Piura.
ALINEAMIENTO_COSTA: list[PuntoCorredor] = [
    PuntoCorredor("MOR", "Mórrope", -6.5500, -80.0100, "primary", 80, 1.15),
    PuntoCorredor("PIM", "Pimentel", -6.4300, -80.1600, "primary", 80, 1.15),
    PuntoCorredor("SRO", "Santa Rosa", -6.1100, -80.4900, "primary", 85, 1.12),
    PuntoCorredor("SEC", "Sechura (eje)", -5.6300, -80.5800, "primary", 85, 1.10),
    PuntoCorredor("CRU", "Cruceta", -5.3500, -80.6200, "primary", 85, 1.10),
]

# --- Ramal Piura → Chulucanas (no pertenece a la PE-1N) ----------------------
# La carretera sale de Piura hacia el este por el valle del Piura; se usa un
# punto intermedio sobre el eje real para que el recuadro de descarga quede
# ceñido a la vía y no se desvíe por Tambogrande (que está al norte).
RAMAL_CHULUCANAS: list[PuntoCorredor] = [
    PuntoCorredor("PIU", "Piura", -5.1945, -80.6328, "urban", 55, 1.15),
    PuntoCorredor("VDP", "Valle del Piura", -5.1500, -80.4000, "secondary", 70, 1.20),
    PuntoCorredor("CHU", "Chulucanas", -5.0925, -80.1625, "secondary", 65, 1.20),
]

# --- Duraciones típicas de entrega urbana ------------------------------------
RADIO_URBANO_KM: dict[str, float] = {
    "LIM": 12.0,
    "TRU": 5.0,
    "CHI": 4.5,
    "PIU": 5.0,
    "SUL": 3.0,
    "CHU": 2.5,
}

VELOCIDAD_URBANA_KMH: dict[str, float] = {
    "LIM": 18.0,   # congestión metropolitana (TomTom: 69.3 %)
    "TRU": 26.0,
    "CHI": 26.0,
    "PIU": 26.0,
    "SUL": 28.0,
    "CHU": 28.0,
}


def distancia_tramo_km(a: PuntoCorredor, b: PuntoCorredor) -> float:
    """Distancia vial estimada entre dos puntos del corredor."""
    return haversine_km(a.lat, a.lon, b.lat, b.lon) * b.sinuosidad


def segmentos_corredor() -> list[tuple[PuntoCorredor, PuntoCorredor]]:
    """Devuelve los pares consecutivos (origen, destino) del corredor completo."""
    pares: list[tuple[PuntoCorredor, PuntoCorredor]] = []
    pares += list(zip(CORREDOR_PE1N[:-1], CORREDOR_PE1N[1:]))
    # Ramal a Chulucanas (evita duplicar Piura).
    pares += [(RAMAL_CHULUCANAS[0], RAMAL_CHULUCANAS[1])]
    pares += [(RAMAL_CHULUCANAS[1], RAMAL_CHULUCANAS[2])]
    return pares


def longitud_corredor_km() -> float:
    return sum(distancia_tramo_km(a, b) for a, b in segmentos_corredor())
