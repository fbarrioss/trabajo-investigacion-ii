"""Topología consolidada de la red vial: nodos de decisión y tramos reales.

A partir del grafo OSM (miles de nodos de geometría) se construye la
**topología operativa** que describe Wilson en la reunión:

    "definición del nodo, del tramo vial... armar un tramo completo, de Lima a
     Piura, identificar todos los tramos que se necesiten"

El algoritmo contrae las cadenas de nodos de grado 2 (puntos de curvatura) y
conserva como nodos únicamente los puntos donde realmente se puede decidir
(intersecciones de grado ≠ 2) más los nodos logísticos (ciudades y
distribuidores). Cada arco resultante es un **tramo vial** con distancia,
tiempo, tipo de vía y peaje agregados.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from ..comun.config import Escenario
from ..comun.geo import haversine_km
from .corredores import RADIO_URBANO_KM

log = logging.getLogger(__name__)

# Jerarquía de vías (para elegir el tipo dominante de un tramo consolidado).
JERARQUIA_VIA = {
    "motorway": 6, "motorway_link": 5,
    "trunk": 5, "trunk_link": 4,
    "primary": 4, "primary_link": 3,
    "secondary": 3, "secondary_link": 2,
    "tertiary": 2, "tertiary_link": 1,
    "residential": 1, "unclassified": 1, "service": 0,
}


@dataclass
class Topologia:
    """Red vial consolidada lista para simulación y análisis.

    ``nodos`` contiene únicamente los **nodos operativos** (extremos de tramo).
    ``coordenadas`` conserva la posición de *todos* los nodos OSM, incluidos los
    de geometría, para poder dibujar y exportar la traza real de cada tramo.
    """

    nodos: pd.DataFrame
    tramos: pd.DataFrame
    grafo: nx.MultiDiGraph
    metadatos: dict[str, Any] = field(default_factory=dict)
    coordenadas: dict[Any, tuple[float, float]] = field(default_factory=dict)
    ciudades: pd.DataFrame | None = None
    distribuidores: pd.DataFrame | None = None

    @property
    def hubs(self) -> pd.DataFrame:
        """Ciudades con rol de hub de origen (punto de partida de los viajes)."""
        if self.ciudades is not None and "rol" in self.ciudades.columns:
            return self.ciudades[self.ciudades["rol"] == "hub_origen"]
        return self.nodos[self.nodos["tipo"] == "hub"]

    def resumen(self) -> dict[str, Any]:
        return {
            "n_nodos": int(len(self.nodos)),
            "n_tramos": int(len(self.tramos)),
            "km_totales": round(float(self.tramos["distancia_km"].sum()), 1),
            "n_decision": int(self.nodos["es_decision"].sum()),
            "n_distribuidores": int((self.nodos["tipo"] == "distribuidor").sum()),
            "conectado": nx.is_weakly_connected(self.grafo),
            **self.metadatos,
        }


# ---------------------------------------------------------------------------
def _atributos_arista(d: dict[str, Any]) -> dict[str, Any]:
    """Normaliza los atributos de una arista OSM."""
    largo = float(d.get("length", 0.0) or 0.0)
    tiempo = d.get("travel_time")
    if tiempo is None or (isinstance(tiempo, float) and math.isnan(tiempo)):
        vel = float(d.get("speed_kph", 0.0) or 0.0) or 40.0
        tiempo = largo / 1000.0 / vel * 3600.0
    via = d.get("highway", "unclassified")
    if isinstance(via, list):
        via = max(via, key=lambda x: JERARQUIA_VIA.get(str(x), 0)) if via else "unclassified"
    return {
        "largo_m": largo,
        "tiempo_s": float(tiempo),
        "via": str(via),
        "nombre": str(d.get("name", "") or ""),
        "puente": bool(d.get("bridge", False)),
        "tunel": bool(d.get("tunnel", False)),
        "peaje": bool(d.get("toll", False)),
    }


def _tipo_dominante(vias: list[str]) -> str:
    if not vias:
        return "unclassified"
    conteo: dict[str, int] = {}
    for v in vias:
        conteo[v] = conteo.get(v, 0) + 1
    return max(
        conteo,
        key=lambda v: (JERARQUIA_VIA.get(v, 0), conteo[v]),
    )


# ---------------------------------------------------------------------------
def grafo_no_dirigido_agregado(G: nx.MultiDiGraph) -> nx.Graph:
    """Colapsa el multigrafo dirigido en un grafo simple no dirigido.

    En OSMnx una vía de doble sentido genera dos aristas (``u→v`` y ``v→u``)
    con la misma geometría. Si se convirtiera directamente a ``MultiGraph``
    esas dos aristas contarían como grado 2 y todos los nodos intermedios se
    contraerían por error. Por eso se agrega por par no ordenado tomando la
    longitud/tiempo **mínimo** (es la misma vía física) y acumulando los
    atributos descriptivos.
    """
    H = nx.Graph()
    H.add_nodes_from(G.nodes(data=True))

    for u, v, d in G.edges(data=True):
        if u == v:
            continue
        a = _atributos_arista(d)
        if H.has_edge(u, v):
            prev = H[u][v]
            prev["largo_m"] = min(prev["largo_m"], a["largo_m"])
            prev["tiempo_s"] = min(prev["tiempo_s"], a["tiempo_s"])
            prev["_vias"].append(a["via"])
            if a["nombre"]:
                prev["_nombres"].add(a["nombre"])
            prev["peaje"] = prev["peaje"] or a["peaje"]
            prev["puente"] = prev["puente"] or a["puente"]
            prev["tunel"] = prev["tunel"] or a["tunel"]
        else:
            H.add_edge(
                u, v,
                largo_m=a["largo_m"], tiempo_s=a["tiempo_s"],
                peaje=a["peaje"], puente=a["puente"], tunel=a["tunel"],
                _vias=[a["via"]],
                _nombres={a["nombre"]} if a["nombre"] else set(),
            )
    return H


def filtrar_red_carga(
    G: nx.MultiDiGraph, nivel_minimo: str = "tertiary"
) -> nx.MultiDiGraph:
    """Conserva solo las vías aptas para transporte de carga pesada.

    La red urbana completa de OSM incluye calles residenciales y de servicio
    que ningún camión de 8–25 t utiliza y que multiplicarían por diez el número
    de nodos de decisión. Este filtro deja la jerarquía vial que sí forma parte
    de la red logística (``motorway`` … ``tertiary``).

    El grafo completo descargado permanece intacto en ``data/raw/osm``; este
    filtro solo afecta a la topología operativa.
    """
    umbral = JERARQUIA_VIA.get(nivel_minimo, 2)
    permitidas = {
        via for via, rango in JERARQUIA_VIA.items()
        if rango >= umbral or via.endswith("_link") and JERARQUIA_VIA.get(via, 0) >= umbral - 1
    }

    def _via(d: dict[str, Any]) -> str:
        v = d.get("highway", "")
        if isinstance(v, list):
            v = max(v, key=lambda x: JERARQUIA_VIA.get(str(x), 0)) if v else ""
        return str(v)

    H = nx.MultiDiGraph()
    H.add_nodes_from(G.nodes(data=True))
    for u, v, k, d in G.edges(keys=True, data=True):
        if _via(d) in permitidas:
            H.add_edge(u, v, key=k, **d)
    # Elimina nodos que quedaron sin ninguna arista tras el filtro.
    H.remove_nodes_from([n for n, g in H.degree() if g == 0])
    log.info(
        "Filtro de red de carga (%s+): %d → %d nodos, %d → %d aristas",
        nivel_minimo, G.number_of_nodes(), H.number_of_nodes(),
        G.number_of_edges(), H.number_of_edges(),
    )
    return H


# ---------------------------------------------------------------------------
def consolidar_tramos(
    G: nx.MultiDiGraph, nodos_logisticos: set[Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Contrae cadenas de grado 2 y devuelve (nodos, tramos)."""
    H = grafo_no_dirigido_agregado(G)
    log.info("Consolidando topología sobre %d nodos...", H.number_of_nodes())

    grado = dict(H.degree())
    decisiones = {n for n in H.nodes if grado.get(n, 0) != 2 or n in nodos_logisticos}

    tramos: list[dict[str, Any]] = []
    visitadas: set[frozenset] = set()
    contador = 0

    for origen in decisiones:
        for vecino in H.neighbors(origen):
            if vecino == origen:
                continue
            clave = frozenset((origen, vecino))
            if clave in visitadas:
                continue

            aristas: list[dict[str, Any]] = [H[origen][vecino]]
            visitadas.add(clave)

            camino_nodos = [origen, vecino]
            anterior, actual = origen, vecino

            while actual not in decisiones:
                siguiente = None
                for cand in H.neighbors(actual):
                    if cand != anterior:
                        siguiente = cand
                        break
                if siguiente is None:
                    break
                aristas.append(H[actual][siguiente])
                visitadas.add(frozenset((actual, siguiente)))
                camino_nodos.append(siguiente)
                anterior, actual = actual, siguiente
                if len(camino_nodos) > 2000:
                    break

            if len(camino_nodos) < 2:
                continue

            largo_m = sum(a["largo_m"] for a in aristas)
            tiempo_s = sum(a["tiempo_s"] for a in aristas)
            if largo_m <= 0:
                continue

            vias: list[str] = []
            nombres: set[str] = set()
            for a in aristas:
                vias.extend(a.get("_vias", []))
                nombres.update(a.get("_nombres", set()))

            # Se redondea primero y se deriva la velocidad de los valores
            # redondeados: así distancia, tiempo y velocidad son coherentes
            # entre sí (con 2 decimales de minuto los tramos de pocos metros
            # quedaban en 0.00 min y la velocidad resultaba absurda).
            dist_km = round(largo_m / 1000.0, 3)
            t_min = round(max(tiempo_s, 0.006) / 60.0, 4)
            vel_kmh = round(dist_km / (t_min / 60.0), 1) if t_min > 0 else 0.0

            contador += 1
            tramos.append(
                {
                    "id_tramo": f"TR-{contador:05d}",
                    "id_nodo_ini": origen,
                    "id_nodo_fin": actual,
                    "n_nodos_geometria": len(camino_nodos),
                    "camino_nodos": ";".join(str(x) for x in camino_nodos),
                    "distancia_km": dist_km,
                    "tiempo_flujo_min": t_min,
                    "velocidad_flujo_kmh": vel_kmh,
                    "tipo_via": _tipo_dominante(vias),
                    "nombre_via": " / ".join(sorted(nombres)[:3]),
                    "peaje": any(a["peaje"] for a in aristas),
                    "puente": any(a["puente"] for a in aristas),
                    "tunel": any(a["tunel"] for a in aristas),
                    "bidireccional": True,
                }
            )

    tramos_df = pd.DataFrame(tramos)
    log.info("Tramos consolidados: %d", len(tramos_df))

    # --- Nodos --------------------------------------------------------------
    filas = []
    for n in H.nodes:
        d = H.nodes[n]
        filas.append(
            {
                "id_nodo": n,
                "lat": float(d.get("y", 0.0)),
                "lon": float(d.get("x", 0.0)),
                "grado": grado.get(n, 0),
                "es_decision": bool(n in decisiones),
                "tipo": "logistico" if n in nodos_logisticos else "interseccion",
                "nombre": "",
                "ciudad_id": "",
            }
        )
    nodos_df = pd.DataFrame(filas)
    return nodos_df, tramos_df


# ---------------------------------------------------------------------------
def _farthest_point_sampling(
    coordenadas: np.ndarray, k: int, semilla: int = 42
) -> list[int]:
    """Selecciona k puntos bien distribuidos (muestreo del punto más lejano)."""
    n = len(coordenadas)
    if n <= k:
        return list(range(n))
    rng = np.random.default_rng(semilla)
    elegidos = [int(rng.integers(0, n))]
    dist_min = np.linalg.norm(coordenadas - coordenadas[elegidos[0]], axis=1)

    for _ in range(k - 1):
        idx = int(np.argmax(dist_min))
        elegidos.append(idx)
        dist_min = np.minimum(
            dist_min, np.linalg.norm(coordenadas - coordenadas[idx], axis=1)
        )
    return elegidos


def seleccionar_distribuidores(
    G: nx.MultiDiGraph, esc: Escenario, nodos_logisticos: set[Any]
) -> tuple[pd.DataFrame, set[Any]]:
    """Ubica los distribuidores sobre nodos reales de la red vial.

    Para cada ciudad se toman los nodos del grafo dentro del radio urbano y se
    eligen los mejor distribuidos con *farthest point sampling*. Los puntos
    resultantes son intersecciones reales de la red, no coordenadas inventadas.
    """
    filas: list[dict[str, Any]] = []
    ids_logisticos: set[Any] = set()

    ids = np.array(list(G.nodes), dtype=object)
    lat = np.array([d.get("y", np.nan) for _, d in G.nodes(data=True)], dtype=float)
    lon = np.array([d.get("x", np.nan) for _, d in G.nodes(data=True)], dtype=float)
    valido = ~(np.isnan(lat) | np.isnan(lon))
    ids, lat, lon = ids[valido], lat[valido], lon[valido]

    for ciudad in esc.ciudades:
        n_dist = int(ciudad.get("distribuidores", 0))
        if n_dist <= 0:
            continue

        radio = RADIO_URBANO_KM.get(ciudad["id"], 4.0) * 1.4
        # Distancia haversine vectorizada desde el centro de la ciudad.
        dlat = np.radians(lat - ciudad["lat"])
        dlon = np.radians(lon - ciudad["lon"])
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(np.radians(ciudad["lat"]))
            * np.cos(np.radians(lat))
            * np.sin(dlon / 2) ** 2
        )
        dist = 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        mask = dist <= radio
        if int(mask.sum()) < n_dist:
            log.warning(
                "%s: solo %d nodos en el radio urbano (%.1f km)",
                ciudad["id"], int(mask.sum()), radio,
            )
            continue

        cand_ids = ids[mask]
        cand_lat = lat[mask]
        cand_lon = lon[mask]

        # Centro del polígono urbano real (media de los nodos candidatos).
        lat0, lon0 = float(cand_lat.mean()), float(cand_lon.mean())

        # Proyección local a km para que la distancia euclídea sea métrica.
        xy = np.column_stack(
            [
                (cand_lat - lat0) * 111.32,
                (cand_lon - lon0) * 111.32 * math.cos(math.radians(lat0)),
            ]
        )
        elegidos = _farthest_point_sampling(xy, n_dist, semilla=esc.semilla + len(filas))

        for i, idx in enumerate(elegidos, start=1):
            n = cand_ids[idx]
            id_dist = f"{ciudad['id']}-D{i:02d}"
            filas.append(
                {
                    "id_distribuidor": id_dist,
                    "id_nodo": n,
                    "ciudad_id": ciudad["id"],
                    "ciudad": ciudad["nombre"],
                    "lat": round(float(cand_lat[idx]), 6),
                    "lon": round(float(cand_lon[idx]), 6),
                    "orden": i,
                }
            )
            ids_logisticos.add(n)
            nodos_logisticos.add(n)

    return pd.DataFrame(filas), ids_logisticos


# ---------------------------------------------------------------------------
def construir_grafo_consolidado(tramos: pd.DataFrame) -> nx.MultiDiGraph:
    """Grafo dirigido con una arista por sentido a partir de los tramos."""
    G = nx.MultiDiGraph()
    for t in tramos.itertuples(index=False):
        G.add_edge(
            t.id_nodo_ini, t.id_nodo_fin, key=t.id_tramo,
            id_tramo=t.id_tramo, distancia_km=float(t.distancia_km),
            tiempo_flujo_min=float(t.tiempo_flujo_min), tipo_via=t.tipo_via,
            peaje=bool(t.peaje), reversa=False,
        )
        if bool(t.bidireccional):
            G.add_edge(
                t.id_nodo_fin, t.id_nodo_ini, key=f"{t.id_tramo}_R",
                id_tramo=t.id_tramo, distancia_km=float(t.distancia_km),
                tiempo_flujo_min=float(t.tiempo_flujo_min), tipo_via=t.tipo_via,
                peaje=bool(t.peaje), reversa=True,
            )
    return G


def enriquecer_nodos(
    nodos: pd.DataFrame,
    ciudades: pd.DataFrame,
    distribuidores: pd.DataFrame,
) -> pd.DataFrame:
    """Añade tipo, nombre y ciudad a los nodos logísticos (vectorizado)."""
    nodos = nodos.copy()
    nodos["nombre"] = nodos["nombre"].astype("object")
    nodos["ciudad_id"] = nodos["ciudad_id"].astype("object")

    tipo_ciudad = {
        r.id_nodo: ("hub" if r.rol == "hub_origen" else "ciudad") for r in ciudades.itertuples(index=False)
    }
    nombre_ciudad = {r.id_nodo: r.nombre for r in ciudades.itertuples(index=False)}
    cid_ciudad = {r.id_nodo: r.id_ciudad for r in ciudades.itertuples(index=False)}

    tipo_dist = {r.id_nodo: "distribuidor" for r in distribuidores.itertuples(index=False)}
    nombre_dist = {
        r.id_nodo: f"{r.id_distribuidor} · {r.ciudad}"
        for r in distribuidores.itertuples(index=False)
    }
    cid_dist = {r.id_nodo: r.ciudad_id for r in distribuidores.itertuples(index=False)}

    es_ciudad = nodos["id_nodo"].isin(tipo_ciudad)
    es_dist = nodos["id_nodo"].isin(tipo_dist)

    nodos.loc[es_ciudad, "tipo"] = nodos.loc[es_ciudad, "id_nodo"].map(tipo_ciudad)
    nodos.loc[es_ciudad, "nombre"] = nodos.loc[es_ciudad, "id_nodo"].map(nombre_ciudad)
    nodos.loc[es_ciudad, "ciudad_id"] = nodos.loc[es_ciudad, "id_nodo"].map(cid_ciudad)

    # Los distribuidores tienen prioridad sobre el nodo de ciudad si coinciden.
    nodos.loc[es_dist, "tipo"] = "distribuidor"
    nodos.loc[es_dist, "nombre"] = nodos.loc[es_dist, "id_nodo"].map(nombre_dist)
    nodos.loc[es_dist, "ciudad_id"] = nodos.loc[es_dist, "id_nodo"].map(cid_dist)
    return nodos


def _marcar_tiene_alterna(
    tramos: pd.DataFrame, grafo: nx.MultiDiGraph
) -> pd.Series:
    """Marca los tramos que admiten un desvío real.

    Un tramo es **arista de corte** (puente) si al eliminarlo sus extremos
    quedan desconectados; en ese caso no existe ruta alterna y el vehículo
    solo puede esperar. El resto de la red admite un desvío, que es lo que
    habilita la acción "tomar vía alterna" del agente.
    """
    simple = nx.Graph(grafo)
    puentes = {frozenset((int(u), int(v))) for u, v in nx.bridges(simple)}
    ini = tramos["id_nodo_ini"].astype("int64").to_numpy()
    fin = tramos["id_nodo_fin"].astype("int64").to_numpy()
    tiene = [frozenset((int(u), int(v))) not in puentes for u, v in zip(ini, fin)]
    serie = pd.Series(tiene, index=tramos.index, dtype=bool)
    log.info(
        "Alternativas: %d de %d tramos admiten desvío (%.1f %%); %d son de corte",
        int(serie.sum()), len(serie), 100.0 * serie.mean(), len(serie) - int(serie.sum()),
    )
    return serie


def asignar_zona(
    tramos: pd.DataFrame,
    nodos: pd.DataFrame,
    ciudades: pd.DataFrame,
) -> pd.DataFrame:
    """Asigna a cada tramo la zona (ciudad) más cercana a su punto medio.

    ``zona`` alimenta los pesos de riesgo por zona del generador de eventos
    (``riesgo_por_zona`` en el escenario).
    """
    tramos = tramos.copy()
    pos = {
        r.id_nodo: (float(r.lat), float(r.lon))
        for r in nodos.itertuples(index=False)
    }
    ids_ciudad = list(ciudades["id_ciudad"])
    lat_c = ciudades["lat"].to_numpy(dtype=float)
    lon_c = ciudades["lon"].to_numpy(dtype=float)

    zonas: list[str] = []
    for t in tramos.itertuples(index=False):
        a = pos.get(t.id_nodo_ini)
        b = pos.get(t.id_nodo_fin)
        if a is None or b is None:
            zonas.append("")
            continue
        la, lo = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        d = (lat_c - la) ** 2 + ((lon_c - lo) * 0.98) ** 2
        zonas.append(ids_ciudad[int(np.argmin(d))])

    tramos["zona"] = zonas
    return tramos


def construir_topologia(
    G: nx.MultiDiGraph,
    esc: Escenario,
) -> tuple[Topologia, pd.DataFrame, pd.DataFrame]:
    """Pipeline completo: ciudades → distribuidores → consolidación → grafo."""
    nodos_logisticos: set[Any] = set()

    ciudades = marcar_ciudades(G, esc, nodos_logisticos)
    log.info("Ciudades asociadas: %d", len(ciudades))

    distribuidores, _ = seleccionar_distribuidores(G, esc, nodos_logisticos)
    log.info("Distribuidores ubicados sobre la red: %d", len(distribuidores))

    nodos_df, tramos_df = consolidar_tramos(G, nodos_logisticos)

    # Coordenadas de todos los nodos (incluidos los de geometría), para dibujar
    # la traza real de cada tramo aunque no formen parte de la topología.
    coordenadas = {
        r.id_nodo: (float(r.lon), float(r.lat))
        for r in nodos_df.itertuples(index=False)
    }

    # La tabla de nodos operativa solo contiene los extremos de tramo.
    extremos = set(tramos_df["id_nodo_ini"]) | set(tramos_df["id_nodo_fin"])
    n_geometria = int(len(nodos_df))
    nodos_df = nodos_df[nodos_df["id_nodo"].isin(extremos)].reset_index(drop=True)

    grafo = construir_grafo_consolidado(tramos_df)

    # --- Poda de fragmentos aislados ---------------------------------------
    # Tras la consolidación quedan pequeños ramales sueltos (bordes de las
    # bandas de descarga) que no forman parte de la red operativa. Se conserva
    # la componente principal, que debe contener todos los nodos logísticos.
    n_descartados = 0
    if not nx.is_weakly_connected(grafo):
        mayor = max(nx.weakly_connected_components(grafo), key=len)
        n_descartados = grafo.number_of_nodes() - len(mayor)
        perdidos = nodos_logisticos - mayor
        if perdidos:
            log.warning(
                "Nodos logísticos fuera de la componente principal: %s", perdidos
            )
        tramos_df = tramos_df[
            tramos_df["id_nodo_ini"].isin(mayor) & tramos_df["id_nodo_fin"].isin(mayor)
        ].reset_index(drop=True)
        nodos_df = nodos_df[nodos_df["id_nodo"].isin(mayor)].reset_index(drop=True)
        grafo = construir_grafo_consolidado(tramos_df)
        log.info(
            "Poda: %d nodos en fragmentos aislados descartados; %d nodos, %d tramos",
            n_descartados, len(nodos_df), len(tramos_df),
        )

    nodos_df = enriquecer_nodos(nodos_df, ciudades, distribuidores)

    # Columnas que consume el simulador. `es_alternativa` marcaba una vía
    # paralela sintética que nunca existió; el dato útil es si el tramo admite
    # un desvío real, es decir, si no es arista de corte de la red de carga.
    tramos_df["es_alternativa"] = False
    tramos_df["tiene_alterna"] = _marcar_tiene_alterna(tramos_df, grafo)
    tramos_df = asignar_zona(tramos_df, nodos_df, ciudades)

    topo = Topologia(
        nodos=nodos_df,
        tramos=tramos_df,
        grafo=grafo,
        metadatos={
            "fuente": "OpenStreetMap (Overpass API + OSMnx)",
            "escenario": esc.crudo.get("nombre"),
            "nodos_logisticos": len(nodos_logisticos),
            "ciudades": len(ciudades),
            "nodos_osm_totales": n_geometria,
            "nodos_geometria_contraidos": n_geometria - len(nodos_df) - n_descartados,
            "nodos_fragmentos_descartados": n_descartados,
        },
        coordenadas=coordenadas,
        ciudades=ciudades,
        distribuidores=distribuidores,
    )
    return topo, ciudades, distribuidores


def marcar_ciudades(
    G: nx.MultiDiGraph, esc: Escenario, nodos_logisticos: set[Any]
) -> pd.DataFrame:
    """Asocia cada ciudad con su nodo de red más cercano."""
    ids = np.array(list(G.nodes), dtype=object)
    lat = np.array([d.get("y", np.nan) for _, d in G.nodes(data=True)], dtype=float)
    lon = np.array([d.get("x", np.nan) for _, d in G.nodes(data=True)], dtype=float)
    valido = ~(np.isnan(lat) | np.isnan(lon))
    ids, lat, lon = ids[valido], lat[valido], lon[valido]

    filas = []
    for ciudad in esc.ciudades:
        dlat = np.radians(lat - ciudad["lat"])
        dlon = np.radians(lon - ciudad["lon"])
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(np.radians(ciudad["lat"]))
            * np.cos(np.radians(lat))
            * np.sin(dlon / 2) ** 2
        )
        dist = 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        i = int(np.argmin(dist))
        mejor, mejor_d = ids[i], float(dist[i])
        filas.append(
            {
                "id_ciudad": ciudad["id"],
                "nombre": ciudad["nombre"],
                "id_nodo": mejor,
                "lat": ciudad["lat"],
                "lon": ciudad["lon"],
                "rol": ciudad.get("rol", "ciudad_destino"),
                "distancia_snap_m": round(mejor_d * 1000, 1),
                "distribuidores": int(ciudad.get("distribuidores", 0)),
            }
        )
        nodos_logisticos.add(mejor)
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
def macro_tramos(
    topo: "Topologia",
    ciudades: pd.DataFrame,
    esc: Escenario,
) -> pd.DataFrame:
    """Tramos completos entre ciudades consecutivas del corredor.

    Es la lectura que pidió Wilson en la reunión ("armar un tramo completo, de
    Lima a Piura"): agrega todos los tramos elementales de la topología en un
    único registro por par origen–destino, con su distancia, tiempo y la
    secuencia de tramos que lo componen.
    """
    import networkx as nx

    G = topo.grafo
    nodo_de = dict(zip(ciudades["id_ciudad"], ciudades["id_nodo"]))
    nombre_de = dict(zip(ciudades["id_ciudad"], ciudades["nombre"]))
    filas: list[dict[str, Any]] = []

    for origen_id, destino_id in esc.conexiones:
        o, d = nodo_de.get(origen_id), nodo_de.get(destino_id)
        if o is None or d is None or o not in G or d not in G:
            log.warning("Macro-tramo %s→%s: nodo ausente en el grafo", origen_id, destino_id)
            continue
        try:
            camino = nx.shortest_path(G, o, d, weight="distancia_km")
        except nx.NetworkXNoPath:
            log.warning("Macro-tramo %s→%s: sin ruta", origen_id, destino_id)
            continue

        ids_tramos: list[str] = []
        km = min_min = 0.0
        vias: list[str] = []
        peajes = 0
        for u, v in zip(camino[:-1], camino[1:]):
            datos = min(
                G[u][v].values(),
                key=lambda x: x.get("distancia_km", math.inf),
            )
            ids_tramos.append(str(datos.get("id_tramo", "")))
            km += float(datos.get("distancia_km", 0.0))
            min_min += float(datos.get("tiempo_flujo_min", 0.0))
            vias.append(str(datos.get("tipo_via", "")))
            peajes += int(bool(datos.get("peaje", False)))

        filas.append(
            {
                "id_macro": f"{origen_id}-{destino_id}",
                "origen": nombre_de.get(origen_id, origen_id),
                "destino": nombre_de.get(destino_id, destino_id),
                "id_nodo_origen": o,
                "id_nodo_destino": d,
                "distancia_km": round(km, 1),
                "tiempo_min": round(min_min, 1),
                "n_tramos": len(ids_tramos),
                "tramos_con_peaje": peajes,
                "tipo_via_dominante": _tipo_dominante(vias),
                "secuencia_tramos": ";".join(ids_tramos[:200]),
            }
        )

    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
def cargar_topologia(directorio: str | Path) -> Topologia:
    """Carga la topología exportada por ``scripts/01_red_vial.py``.

    Lee ``nodos.csv``, ``tramos.csv``, ``ciudades.csv``, ``distribuidores.csv``
    y ``topologia.graphml``. Las coordenadas de los nodos de geometría se
    reconstruyen desde la columna ``camino_nodos`` del GeoJSON cuando está
    disponible; si no, se usan solo las coordenadas de los nodos operativos.
    """
    import json

    directorio = Path(directorio)
    requeridos = ["nodos.csv", "tramos.csv", "topologia.graphml"]
    faltan = [f for f in requeridos if not (directorio / f).exists()]
    if faltan:
        raise FileNotFoundError(
            f"Faltan archivos de red en {directorio}: {faltan}. "
            "Ejecuta `python scripts/01_red_vial.py`."
        )

    nodos = pd.read_csv(directorio / "nodos.csv")
    tramos = pd.read_csv(directorio / "tramos.csv")
    grafo = nx.read_graphml(directorio / "topologia.graphml")

    # GraphML guarda los IDs de nodo como texto; el resto de tablas los lee como
    # enteros. Se normaliza el grafo para que los cruces de identificadores
    # funcionen (p. ej. `red.hubs.iloc[0]["id_nodo"]` dentro del simulador).
    def _id(n: Any) -> Any:
        s = str(n)
        return int(s) if s.lstrip("-").isdigit() else s

    grafo = nx.relabel_nodes(grafo, _id)

    ciudades = (
        pd.read_csv(directorio / "ciudades.csv")
        if (directorio / "ciudades.csv").exists() else None
    )
    distribuidores = (
        pd.read_csv(directorio / "distribuidores.csv")
        if (directorio / "distribuidores.csv").exists() else None
    )

    metadatos: dict[str, Any] = {}
    if (directorio / "metadatos.json").exists():
        metadatos = json.loads((directorio / "metadatos.json").read_text(encoding="utf-8"))

    coordenadas = {
        str(r.id_nodo): (float(r.lon), float(r.lat))
        for r in nodos.itertuples(index=False)
    }
    return Topologia(
        nodos=nodos,
        tramos=tramos,
        grafo=grafo,
        metadatos=metadatos,
        coordenadas=coordenadas,
        ciudades=ciudades,
        distribuidores=distribuidores,
    )
