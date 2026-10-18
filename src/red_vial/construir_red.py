"""DEPRECADO — no forma parte del pipeline.

Este módulo construía la red a partir de la trayectoria curada de la PE-1N y
generaba tramos alternativos sintéticos. Se conserva únicamente como referencia
histórica: el profesor indicó que para la tesis **no se aceptan datos
simulados**, por lo que la red vigente se construye exclusivamente con
OpenStreetMap en ``descargar_osm.py`` + ``topologia.py``.

Construcción de la red vial: nodos logísticos y tramos.

Genera las dos entidades que Wilson solicitó (diccionario de datos):
  * ``nodos``   → puntos logísticos y de decisión de la red.
  * ``tramos``  → arcos viales con atributos de distancia, tiempo, costo y riesgo.

Modos de operación
------------------
``curada``
    Usa la trayectoria real de la PE-1N (``corredores.py``) y genera un tramo
    alternativo por segmento. No requiere internet. Sirve para desarrollo y
    pruebas de integración.

``osm``
    Descarga la red de OpenStreetMap con OSMnx, ajusta (snap) los nodos
    logísticos y calcula distancias/tiempos reales sobre la red. Genera
    alternativas mediante k-caminos más cortos. Si la descarga falla, cae
    automáticamente al modo curado dejando registro en el log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from ..comun.config import Escenario, RAIZ
from ..comun.geo import haversine_km, muestrear_puntos_en_disco
from .corredores import (
    CORREDOR_PE1N,
    RADIO_URBANO_KM,
    RAMAL_CHULUCANAS,
    VELOCIDAD_URBANA_KMH,
    PuntoCorredor,
    distancia_tramo_km,
)

log = logging.getLogger(__name__)

# Factores de la vía alterna (carretera secundaria / desvío).
FACTOR_DISTANCIA_ALTERNA = 1.18
FACTOR_VELOCIDAD_ALTERNA = 0.85
RECARGO_RIESGO_ALTERNA = 1.25


@dataclass
class RedVial:
    """Contenedor de la red vial lista para simulación."""

    nodos: pd.DataFrame
    tramos: pd.DataFrame
    grafo: nx.MultiDiGraph
    modo: str = "curada"
    metadatos: dict[str, Any] = field(default_factory=dict)

    # --- consultas -----------------------------------------------------------
    def nodo(self, id_nodo: str) -> pd.Series:
        fila = self.nodos.loc[self.nodos["id_nodo"] == id_nodo]
        if fila.empty:
            raise KeyError(f"Nodo inexistente: {id_nodo}")
        return fila.iloc[0]

    def tramos_salida(self, id_nodo: str) -> pd.DataFrame:
        return self.tramos.loc[self.tramos["id_nodo_ini"] == id_nodo]

    def tramo(self, id_tramo: str) -> pd.Series:
        fila = self.tramos.loc[self.tramos["id_tramo"] == id_tramo]
        if fila.empty:
            raise KeyError(f"Tramo inexistente: {id_tramo}")
        return fila.iloc[0]

    @property
    def distribuidores(self) -> pd.DataFrame:
        return self.nodos.loc[self.nodos["tipo"] == "distribuidor"]

    @property
    def hubs(self) -> pd.DataFrame:
        return self.nodos.loc[self.nodos["tipo"] == "hub"]

    def resumen(self) -> dict[str, Any]:
        return {
            "modo": self.modo,
            "n_nodos": int(len(self.nodos)),
            "n_tramos": int(len(self.tramos)),
            "km_totales": round(float(self.tramos["distancia_km"].sum()), 2),
            "n_distribuidores": int(len(self.distribuidores)),
            "n_hubs": int(len(self.hubs)),
            "conectado": nx.is_weakly_connected(self.grafo),
        }


# ---------------------------------------------------------------------------
# Nodos logísticos
# ---------------------------------------------------------------------------
def _nodos_logisticos(esc: Escenario) -> pd.DataFrame:
    """Construye el maestro de nodos: hub, ciudades y distribuidores."""
    filas: list[dict[str, Any]] = []

    # 1. Puntos del corredor que son ciudades del escenario o de paso.
    puntos: dict[str, PuntoCorredor] = {}
    for p in CORREDOR_PE1N + RAMAL_CHULUCANAS:
        puntos[p.id] = p

    ciudades_cfg = {c["id"]: c for c in esc.ciudades}

    for pid, p in puntos.items():
        cfg = ciudades_cfg.get(pid)
        if cfg is None:
            tipo = "intermedio"
        elif cfg["rol"] == "hub_origen":
            tipo = "hub"
        else:
            tipo = "ciudad"
        filas.append(
            {
                "id_nodo": pid,
                "nombre": p.nombre,
                "tipo": tipo,
                "lat": p.lat,
                "lon": p.lon,
                "ciudad_id": pid if cfg else "",
                "es_decision": True,
                "grado_salida": 0,
            }
        )

    # 2. Distribuidores (placeholder georreferenciado por ciudad).
    for cfg in esc.ciudades:
        n = int(cfg.get("distribuidores", 0))
        if n <= 0:
            continue
        radio = RADIO_URBANO_KM.get(cfg["id"], 4.0)
        # Semilla estable (``hash`` de str varía entre procesos).
        semilla_ciudad = esc.semilla + sum(ord(ch) for ch in cfg["id"])
        pts = muestrear_puntos_en_disco(
            cfg["lat"], cfg["lon"], radio, n, semilla_ciudad
        )
        for i, (lat, lon) in enumerate(pts, start=1):
            filas.append(
                {
                    "id_nodo": f"{cfg['id']}-D{i:02d}",
                    "nombre": f"Distribuidor {i} {cfg['nombre']}",
                    "tipo": "distribuidor",
                    "lat": lat,
                    "lon": lon,
                    "ciudad_id": cfg["id"],
                    "es_decision": False,
                    "grado_salida": 0,
                    "origen_coordenada": "placeholder",
                }
            )

    nodos = pd.DataFrame(filas)
    nodos["origen_coordenada"] = nodos.get(
        "origen_coordenada", pd.Series(["corredor_pe1n"] * len(nodos))
    ).fillna("corredor_pe1n")
    return nodos


# ---------------------------------------------------------------------------
# Tramos
# ---------------------------------------------------------------------------
def _tramo(
    id_tramo: str,
    a: pd.Series,
    b: pd.Series,
    distancia_km: float,
    velocidad_kmh: float,
    clase: str,
    peaje: bool,
    alternativa: bool,
    zona: str,
    sinuosidad: float = 1.0,
) -> dict[str, Any]:
    tiempo_h = distancia_km / max(velocidad_kmh, 5.0)
    # ``a`` y ``b`` provienen de un DataFrame indexado por id_nodo, por lo que
    # el identificador vive en ``.name``.
    id_a = a["id_nodo"] if "id_nodo" in a.index else a.name
    id_b = b["id_nodo"] if "id_nodo" in b.index else b.name
    return {
        "id_tramo": id_tramo,
        "id_nodo_ini": id_a,
        "id_nodo_fin": id_b,
        "nombre": f"{a['nombre']} → {b['nombre']}",
        "tipo_via": clase,
        "distancia_km": round(distancia_km, 3),
        "velocidad_flujo_kmh": round(velocidad_kmh, 1),
        "tiempo_flujo_min": round(tiempo_h * 60, 2),
        "peaje": bool(peaje),
        "es_alternativa": bool(alternativa),
        "bidireccional": True,
        "zona": zona,
        "sinuosidad": round(sinuosidad, 3),
        "riesgo_base": round(min(0.05 + 0.35 * (distancia_km / 120.0), 0.45), 3),
    }


def _construir_tramos_curados(nodos: pd.DataFrame, esc: Escenario) -> pd.DataFrame:
    """Tramos principal + alterna para cada segmento del corredor."""
    idx = nodos.set_index("id_nodo")
    filas: list[dict[str, Any]] = []
    contador = 0

    segmentos: list[tuple[PuntoCorredor, PuntoCorredor]] = []
    segmentos += list(zip(CORREDOR_PE1N[:-1], CORREDOR_PE1N[1:]))
    segmentos += [(RAMAL_CHULUCANAS[0], RAMAL_CHULUCANAS[1])]
    segmentos += [(RAMAL_CHULUCANAS[1], RAMAL_CHULUCANAS[2])]

    for a_p, b_p in segmentos:
        if a_p.id not in idx.index or b_p.id not in idx.index:
            continue
        a, b = idx.loc[a_p.id], idx.loc[b_p.id]
        d = distancia_tramo_km(a_p, b_p)
        zona = b_p.id

        contador += 1
        filas.append(
            _tramo(
                f"T{contador:04d}", a, b, d, b_p.velocidad_kmh, b_p.clase_via,
                b_p.peaje, False, zona, b_p.sinuosidad,
            )
        )
        # Vía alterna: solo en tramos largos donde existe desvío razonable.
        if d > 35:
            contador += 1
            filas.append(
                _tramo(
                    f"T{contador:04d}", a, b,
                    d * FACTOR_DISTANCIA_ALTERNA,
                    b_p.velocidad_kmh * FACTOR_VELOCIDAD_ALTERNA,
                    "secondary", False, True, zona,
                    b_p.sinuosidad * FACTOR_DISTANCIA_ALTERNA,
                )
            )

    # Conexiones urbanas ciudad → distribuidor.
    for cfg in esc.ciudades:
        ciudad_id = cfg["id"]
        if ciudad_id not in idx.index:
            continue
        centro = idx.loc[ciudad_id]
        v_urb = VELOCIDAD_URBANA_KMH.get(ciudad_id, 26.0)
        dists = nodos.loc[
            nodos["ciudad_id"] == ciudad_id, ["id_nodo", "lat", "lon"]
        ]
        for _, dist in dists.iterrows():
            if dist["id_nodo"] == ciudad_id:
                continue
            b = idx.loc[dist["id_nodo"]]
            d = haversine_km(centro["lat"], centro["lon"], b["lat"], b["lon"]) * 1.25
            contador += 1
            filas.append(
                _tramo(
                    f"T{contador:04d}", centro, b, max(d, 0.5), v_urb,
                    "urban", False, False, ciudad_id, 1.25,
                )
            )
            # Retorno distribuidor → ciudad (permite replanificar).
            contador += 1
            filas.append(
                _tramo(
                    f"T{contador:04d}", b, centro, max(d, 0.5), v_urb,
                    "urban", False, False, ciudad_id, 1.25,
                )
            )

    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Enriquecimiento con OpenStreetMap
# ---------------------------------------------------------------------------
def _snap_osm(G, lat: float, lon: float):
    import osmnx as ox

    return ox.distance.nearest_nodes(G, X=lon, Y=lat)


def _enriquecer_con_osm(
    nodos: pd.DataFrame, tramos: pd.DataFrame, esc: Escenario
) -> tuple[pd.DataFrame, pd.DataFrame, nx.MultiDiGraph, str]:
    """Recalcula distancias y tiempos sobre la red real de OSM.

    Devuelve (nodos, tramos, grafo, modo_efectivo). Si OSM no está disponible
    o falla, devuelve los insumos sin modificar y ``modo_efectivo='curada'``.
    """
    try:
        import osmnx as ox
    except ImportError:
        log.warning("OSMnx no instalado: se usa el corredor curado.")
        return nodos, tramos, _grafo_desde_tramos(tramos), "curada"

    cache = RAIZ / esc.red_vial["cache_dir"]
    cache.mkdir(parents=True, exist_ok=True)
    ruta_cache = cache / "corredor_norte.graphml"

    try:
        if ruta_cache.exists():
            log.info("Cargando grafo OSM desde caché: %s", ruta_cache)
            G = ox.load_graphml(ruta_cache)
        else:
            lats = [c["lat"] for c in esc.ciudades]
            lons = [c["lon"] for c in esc.ciudades]
            margen = 0.35
            bbox = (
                min(lons) - margen,
                min(lats) - margen,
                max(lons) + margen,
                max(lats) + margen,
            )
            log.info("Descargando red OSM (bbox=%s)...", bbox)
            G = ox.graph_from_bbox(
                bbox=bbox,
                network_type="drive",
                custom_filter=esc.red_vial["filtro_vias"],
                simplify=True,
                retain_all=False,
            )
            G = ox.add_edge_speeds(G)
            G = ox.add_edge_travel_times(G)
            ox.save_graphml(G, ruta_cache)
            log.info("Grafo OSM almacenado en caché (%d nodos).", G.number_of_nodes())
    except Exception as exc:  # noqa: BLE001 - degradación controlada
        log.warning("No se pudo obtener OSM (%s). Se usa el corredor curado.", exc)
        return nodos, tramos, _grafo_desde_tramos(tramos), "curada"

    # Snap de todos los nodos logísticos.
    snap: dict[str, Any] = {}
    for _, n in nodos.iterrows():
        try:
            snap[n["id_nodo"]] = _snap_osm(G, n["lat"], n["lon"])
        except Exception:  # noqa: BLE001
            continue

    actualizados = 0
    for i, t in tramos.iterrows():
        o, d = snap.get(t["id_nodo_ini"]), snap.get(t["id_nodo_fin"])
        if o is None or d is None:
            continue
        try:
            ruta = nx.shortest_path(G, o, d, weight="length")
            largo_m = sum(
                G.edges[ruta[k], ruta[k + 1], 0].get("length", 0.0)
                for k in range(len(ruta) - 1)
            )
            tiempo_s = sum(
                G.edges[ruta[k], ruta[k + 1], 0].get("travel_time", 0.0)
                for k in range(len(ruta) - 1)
            )
            if largo_m <= 0:
                continue
            km = largo_m / 1000.0
            tramos.at[i, "distancia_km"] = round(km, 3)
            tramos.at[i, "tiempo_flujo_min"] = round(tiempo_s / 60.0, 2)
            tramos.at[i, "velocidad_flujo_kmh"] = round(
                km / max(tiempo_s / 3600.0, 1e-6), 1
            )
            actualizados += 1
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    log.info("Tramos recalculados con OSM: %d/%d", actualizados, len(tramos))
    return nodos, tramos, G, "osm"


# ---------------------------------------------------------------------------
# Grafo
# ---------------------------------------------------------------------------
def _grafo_desde_tramos(tramos: pd.DataFrame) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for _, t in tramos.iterrows():
        G.add_edge(
            t["id_nodo_ini"],
            t["id_nodo_fin"],
            key=t["id_tramo"],
            id_tramo=t["id_tramo"],
            distancia_km=float(t["distancia_km"]),
            tiempo_flujo_min=float(t["tiempo_flujo_min"]),
            es_alternativa=bool(t["es_alternativa"]),
        )
        if bool(t["bidireccional"]):
            G.add_edge(
                t["id_nodo_fin"],
                t["id_nodo_ini"],
                key=f"{t['id_tramo']}_R",
                id_tramo=t["id_tramo"],
                distancia_km=float(t["distancia_km"]),
                tiempo_flujo_min=float(t["tiempo_flujo_min"]),
                es_alternativa=bool(t["es_alternativa"]),
            )
    return G


def _actualizar_grados(nodos: pd.DataFrame, tramos: pd.DataFrame) -> pd.DataFrame:
    salida = tramos.groupby("id_nodo_ini").size()
    nodos = nodos.copy()
    nodos["grado_salida"] = nodos["id_nodo"].map(salida).fillna(0).astype(int)
    return nodos


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def construir_red(esc: Escenario | None = None, forzar_modo: str | None = None) -> RedVial:
    """Construye la red vial completa del escenario."""
    esc = esc or Escenario.cargar()
    modo = forzar_modo or esc.red_vial.get("modo", "curada")

    nodos = _nodos_logisticos(esc)
    tramos = _construir_tramos_curados(nodos, esc)
    grafo = _grafo_desde_tramos(tramos)
    modo_efectivo = "curada"

    if modo == "osm":
        nodos, tramos, grafo, modo_efectivo = _enriquecer_con_osm(nodos, tramos, esc)

    nodos = _actualizar_grados(nodos, tramos)
    red = RedVial(nodos=nodos, tramos=tramos, grafo=grafo, modo=modo_efectivo)
    red.metadatos = {
        "escenario": esc.crudo.get("nombre"),
        "semilla": esc.semilla,
        "fuente_geometria": "OpenStreetMap (OSMnx)" if modo_efectivo == "osm" else "Corredor PE-1N curado",
        "n_distribuidores_declarados": esc.total_distribuidores(),
    }
    return red


def guardar_red(red: RedVial, destino: str | Path) -> dict[str, Path]:
    """Exporta nodos, tramos y grafo a disco."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    rutas = {
        "nodos": destino / "nodos.csv",
        "tramos": destino / "tramos.csv",
        "grafo": destino / "red_vial.graphml",
    }
    red.nodos.to_csv(rutas["nodos"], index=False, encoding="utf-8")
    red.tramos.to_csv(rutas["tramos"], index=False, encoding="utf-8")
    nx.write_graphml(red.grafo, rutas["grafo"])
    return rutas


def cargar_red(origen: str | Path) -> RedVial:
    """Carga la red vial desde disco en el contenedor que usa el simulador.

    La fuente de verdad es la **topología OSM** exportada por
    ``scripts/01_red_vial.py`` (``topologia.graphml`` + ``nodos.csv`` +
    ``tramos.csv``). El antiguo ``red_vial.graphml`` correspondía al corredor
    curado a mano y quedó obsoleto: cargarlo junto a los CSV de la topología
    producía dos redes distintas (IDs de texto frente a IDs enteros de OSM) y
    el planificador no encontraba ningún camino.
    """
    origen = Path(origen)
    if (origen / "topologia.graphml").exists():
        from .topologia import cargar_topologia  # import local: evita ciclos

        topo = cargar_topologia(origen)
        return RedVial(
            nodos=topo.nodos,
            tramos=topo.tramos,
            grafo=topo.grafo,
            modo="topologia",
            metadatos=dict(topo.metadatos),
        )

    # Compatibilidad con exportaciones antiguas.
    nodos = pd.read_csv(origen / "nodos.csv")
    tramos = pd.read_csv(origen / "tramos.csv")
    grafo = nx.read_graphml(origen / "red_vial.graphml")
    return RedVial(nodos=nodos, tramos=tramos, grafo=grafo, modo="cargada")
