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
import re
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

# ---------------------------------------------------------------------------
# Umbrales de la red de carga pesada
# ---------------------------------------------------------------------------
# Un tramo se considera **restringido para camión** cuando OSM prohíbe
# explícitamente el paso de pesados (`hgv=no`) o cuando el límite de peso o de
# gálibo queda por debajo de la unidad que opera el corredor. La flota de la
# tesis es de 8–25 t, de modo que un `maxweight` de 30 t o más no la afecta.
PESO_CAMION_T = 30.0
ALTO_CAMION_M = 4.0
ANCHO_CAMION_M = 2.6
LARGO_CAMION_M = 14.0

# Tramos por debajo de este umbral son artefactos de digitalización (separación
# de calzadas, cortes en intersecciones) y no unidades operativas reales.
UMBRAL_MICRO_KM = 0.005

_HGV_VALIDOS = {
    "yes", "no", "designated", "destination", "delivery", "permissive",
    "private", "agricultural", "forestry", "unknown",
}
_NUMERO_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _normalizar_hgv(valor: Any) -> str:
    """Normaliza la etiqueta ``hgv`` de OSM.

    Hay valores corruptos en el extracto (``"no||"``, ``"no|no||"``) que un
    ``== "no"`` descartaría en silencio y con ellos la restricción: se toma el
    primer campo antes de la barra y se valida contra el vocabulario de OSM.
    """
    s = str(valor if valor is not None else "").split("|")[0].strip().lower()
    return s if s in _HGV_VALIDOS else ""


def _normalizar_medida(valor: Any, unidad_si_kg: bool = False) -> float | None:
    """Convierte un valor de OSM con unidades libres a número canónico.

    OSM escribe ``maxweight`` como ``"36"``, ``"4 t"`` o ``"60 toneladas"`` y
    ``maxheight`` como ``"5.5"``, ``"5.5 m"`` o ``"5.10"``. Sin normalizar, la
    comparación contra la tara o el gálibo del camión no es posible.
    """
    if valor is None:
        return None
    s = str(valor).strip().lower().replace(",", ".")
    if not s or s in {"none", "nan", "default"}:
        return None
    m = _NUMERO_RE.search(s)
    if not m:
        return None
    num = float(m.group(1))
    if unidad_si_kg and "kg" in s:
        num /= 1000.0
    elif "lb" in s:
        num *= 0.000453592
    elif "ft" in s or "'" in s:
        num *= 0.3048
    elif "cm" in s:
        num /= 100.0
    return round(num, 2)


def _num(valor: Any, defecto: float) -> float:
    """``float`` tolerante a ``None`` y ``NaN`` (GraphML no admite nulos)."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return defecto
    return defecto if math.isnan(v) else v


def _minimo_no_nulo(valores: list[float | None]) -> float | None:
    validos = [v for v in valores if v is not None]
    return min(validos) if validos else None


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
        t = self.tramos
        unidireccionales = (
            int((~t["bidireccional"]).sum()) if "bidireccional" in t.columns else 0
        )
        restringidos = (
            int(t["restriccion_pesados"].sum()) if "restriccion_pesados" in t.columns else 0
        )
        return {
            "n_nodos": int(len(self.nodos)),
            "n_tramos": int(len(t)),
            "km_totales": round(float(t["distancia_km"].sum()), 1),
            "n_decision": int(self.nodos["es_decision"].sum()),
            "n_distribuidores": int((self.nodos["tipo"] == "distribuidor").sum()),
            "n_unidireccionales": unidireccionales,
            "n_bidireccionales": int(len(t)) - unidireccionales,
            "n_restriccion_pesados": restringidos,
            "km_restriccion_pesados": round(
                float(t.loc[t["restriccion_pesados"], "distancia_km"].sum()), 1
            ) if "restriccion_pesados" in t.columns else 0.0,
            "n_micro_tramos": (
                int(t["es_micro"].sum()) if "es_micro" in t.columns else 0
            ),
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
        # --- restricciones de vehículos pesados -----------------------------
        "hgv": _normalizar_hgv(d.get("hgv")),
        "peso_max_t": _normalizar_medida(d.get("maxweight"), unidad_si_kg=True),
        "alto_max_m": _normalizar_medida(d.get("maxheight")),
        "ancho_max_m": _normalizar_medida(d.get("maxwidth")),
        "largo_max_m": _normalizar_medida(d.get("maxlength")),
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

    El conjunto ``_dirs`` guarda los pares **dirigidos** que realmente existen
    en el grafo OSM. Es la única fuente fiable del sentido de circulación: una
    calzada de un solo sentido aparece con ``(u, v)`` pero sin ``(v, u)``, y
    tras el colapso a grafo no dirigido esa información se perdería.
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
            prev["_dirs"].add((u, v))
            prev["_hgv"].add(a["hgv"])
            prev["_peso_max_t"].append(a["peso_max_t"])
            prev["_alto_max_m"].append(a["alto_max_m"])
            prev["_ancho_max_m"].append(a["ancho_max_m"])
            prev["_largo_max_m"].append(a["largo_max_m"])
        else:
            H.add_edge(
                u, v,
                largo_m=a["largo_m"], tiempo_s=a["tiempo_s"],
                peaje=a["peaje"], puente=a["puente"], tunel=a["tunel"],
                _vias=[a["via"]],
                _nombres={a["nombre"]} if a["nombre"] else set(),
                _dirs={(u, v)},
                _hgv={a["hgv"]} if a["hgv"] else set(),
                _peso_max_t=[a["peso_max_t"]],
                _alto_max_m=[a["alto_max_m"]],
                _ancho_max_m=[a["ancho_max_m"]],
                _largo_max_m=[a["largo_max_m"]],
            )
    return H


def _nodos_cambio_sentido(
    H: nx.Graph, decisiones: set[Any]
) -> set[Any]:
    """Nodos de grado 2 donde el sentido de circulación cambia.

    Si una calle de doble sentido se convierte en un solo sentido sin que haya
    una intersección, el nodo de transición tiene grado 2 y la contracción lo
    borraría: el tramo resultante quedaría mitad recorrible en un sentido y
    mitad en el otro, es decir, intransitable en ambos. Tratarlo como nodo de
    decisión parte la cadena justo donde corresponde.
    """
    cambios: set[Any] = set()
    for n in H.nodes:
        if n in decisiones or H.degree(n) != 2:
            continue
        vecinos = list(H.neighbors(n))
        if len(vecinos) != 2:
            continue
        a, b = vecinos
        dirs_a = H[a][n]["_dirs"]
        dirs_b = H[n][b]["_dirs"]
        patron_a = ((a, n) in dirs_a, (n, a) in dirs_a)
        patron_b = ((n, b) in dirs_b, (b, n) in dirs_b)
        if patron_a != patron_b:
            cambios.add(n)
    return cambios


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
    """Contrae cadenas de grado 2 y devuelve (nodos, tramos).

    Además de la contracción, aquí se resuelven los tres defectos que la red
    heredaba de OSM y que el asesor detectó sobre el GeoJSON:

    * **lazos**: una cadena que vuelve al nodo de partida (glorietas, retornos,
      lazos de doble calzada) producía un tramo con ``ini == fin``, es decir un
      self-loop intransitable que solo inflaba el conteo. Se descarta.
    * **sentido**: cada tramo guarda si es recorrible de ini a fin, de fin a
      ini, o en ambos sentidos, a partir de las aristas dirigidas de OSM.
    * **restricciones de pesados**: se agregan ``hgv``, ``maxweight``,
      ``maxheight``, ``maxwidth`` y ``maxlength`` de toda la cadena.
    """
    H = grafo_no_dirigido_agregado(G)
    log.info("Consolidando topología sobre %d nodos...", H.number_of_nodes())

    grado = dict(H.degree())
    decisiones = {n for n in H.nodes if grado.get(n, 0) != 2 or n in nodos_logisticos}
    cambios = _nodos_cambio_sentido(H, decisiones)
    if cambios:
        log.info("Nodos donde cambia el sentido de circulación: %d", len(cambios))
    decisiones |= cambios

    def _orden(n: Any) -> tuple[int, Any]:
        return (0, n) if isinstance(n, int) else (1, str(n))

    tramos: list[dict[str, Any]] = []
    visitadas: set[frozenset] = set()
    n_lazos = 0
    n_intransitables = 0

    for origen in sorted(decisiones, key=_orden):
        for vecino in H.neighbors(origen):
            if vecino == origen:
                continue
            clave = frozenset((origen, vecino))
            if clave in visitadas:
                continue

            aristas: list[dict[str, Any]] = [H[origen][vecino]]
            pares_traza: list[tuple[Any, Any]] = [(origen, vecino)]
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
                pares_traza.append((actual, siguiente))
                visitadas.add(frozenset((actual, siguiente)))
                camino_nodos.append(siguiente)
                anterior, actual = actual, siguiente
                if len(camino_nodos) > 2000:
                    break

            if len(camino_nodos) < 2:
                continue

            # Lazo cerrado: la cadena regresa al punto de partida.
            if actual == origen:
                n_lazos += 1
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
            # OSM tiene pares de nodos distintos separados por centímetros
            # (duplicados de digitalización en avenidas de dos calzadas). Al
            # redondear a metros quedan tramos de 0 km, que no aportan nada a la
            # topología y rompen la validación de coherencia.
            if dist_km <= 0.0:
                continue
            t_min = round(max(tiempo_s, 0.006) / 60.0, 4)
            vel_kmh = round(dist_km / (t_min / 60.0), 1) if t_min > 0 else 0.0

            # --- Sentido de circulación -------------------------------------
            ini_fin = all(
                par in aristas[i]["_dirs"] for i, par in enumerate(pares_traza)
            )
            fin_ini = all(
                (b, a) in aristas[i]["_dirs"]
                for i, (a, b) in enumerate(pares_traza)
            )
            if not ini_fin and not fin_ini:
                # Patrón mixto sin nodo de corte: no debería ocurrir tras
                # `_nodos_cambio_sentido`, pero si ocurre el tramo no es
                # recorrible en ningún sentido y no debe entrar al grafo.
                n_intransitables += 1
                continue
            if ini_fin and fin_ini:
                sentido = "doble"
            elif ini_fin:
                sentido = "ini_fin"
            else:
                sentido = "fin_ini"

            # --- Restricciones de vehículos pesados -------------------------
            hgv: set[str] = set()
            for a in aristas:
                hgv |= a.get("_hgv", set())
            peso_max = _minimo_no_nulo(
                [v for a in aristas for v in a.get("_peso_max_t", [])]
            )
            alto_max = _minimo_no_nulo(
                [v for a in aristas for v in a.get("_alto_max_m", [])]
            )
            ancho_max = _minimo_no_nulo(
                [v for a in aristas for v in a.get("_ancho_max_m", [])]
            )
            largo_max = _minimo_no_nulo(
                [v for a in aristas for v in a.get("_largo_max_m", [])]
            )
            acceso_hgv = (
                "no" if "no" in hgv
                else "designated" if "designated" in hgv
                else sorted(hgv)[0] if hgv
                else ""
            )
            restringido = bool(
                acceso_hgv == "no"
                or (peso_max is not None and peso_max < PESO_CAMION_T)
                or (alto_max is not None and alto_max < ALTO_CAMION_M)
                or (ancho_max is not None and ancho_max < ANCHO_CAMION_M)
                or (largo_max is not None and largo_max < LARGO_CAMION_M)
            )

            tramos.append(
                {
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
                    "sentido": sentido,
                    "sentido_ini_fin": bool(ini_fin),
                    "sentido_fin_ini": bool(fin_ini),
                    "bidireccional": bool(ini_fin and fin_ini),
                    "acceso_hgv": acceso_hgv,
                    "peso_max_t": peso_max,
                    "alto_max_m": alto_max,
                    "ancho_max_m": ancho_max,
                    "largo_max_m": largo_max,
                    "restriccion_pesados": restringido,
                    "es_micro": bool(dist_km < UMBRAL_MICRO_KM),
                }
            )

    tramos_df = pd.DataFrame(tramos)
    # Los IDs se asignan al final y en orden determinista: antes dependían del
    # orden de iteración de un `set` de nodos, de modo que dos ejecuciones del
    # mismo pipeline podían numerar distinto.
    if not tramos_df.empty:
        tramos_df = tramos_df.sort_values(
            ["id_nodo_ini", "id_nodo_fin", "distancia_km"], kind="stable"
        ).reset_index(drop=True)
        tramos_df.insert(
            0, "id_tramo", [f"TR-{i:05d}" for i in range(1, len(tramos_df) + 1)]
        )
    log.info(
        "Tramos consolidados: %d (lazos descartados: %d, intransitables: %d)",
        len(tramos_df), n_lazos, n_intransitables,
    )
    if not tramos_df.empty:
        log.info(
            "  sentido: doble %d, solo ini→fin %d, solo fin→ini %d; "
            "restricción de pesados en %d tramos",
            int((tramos_df["sentido"] == "doble").sum()),
            int((tramos_df["sentido"] == "ini_fin").sum()),
            int((tramos_df["sentido"] == "fin_ini").sum()),
            int(tramos_df["restriccion_pesados"].sum()),
        )

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
    """Grafo dirigido con una arista por sentido **permitido**.

    A diferencia de la versión anterior, la arista inversa solo se crea si el
    tramo es recorrible en ese sentido: las calzadas de un solo sentido de OSM
    dejan de aparecer como doble vía. Los atributos de restricción viajan con
    la arista para que el planificador pueda evitarlas.
    """
    G = nx.MultiDiGraph()
    for t in tramos.itertuples(index=False):
        comunes = {
            "id_tramo": t.id_tramo,
            "distancia_km": float(t.distancia_km),
            "tiempo_flujo_min": float(t.tiempo_flujo_min),
            "tipo_via": t.tipo_via,
            "peaje": bool(t.peaje),
            "restriccion_pesados": bool(getattr(t, "restriccion_pesados", False)),
            "peso_max_t": _num(getattr(t, "peso_max_t", None), -1.0),
            "alto_max_m": _num(getattr(t, "alto_max_m", None), -1.0),
        }
        if bool(getattr(t, "sentido_ini_fin", True)):
            G.add_edge(t.id_nodo_ini, t.id_nodo_fin, key=t.id_tramo,
                       reversa=False, **comunes)
        if bool(getattr(t, "sentido_fin_ini", True)):
            G.add_edge(t.id_nodo_fin, t.id_nodo_ini, key=f"{t.id_tramo}_R",
                       reversa=True, **comunes)
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
            "fuente": "OpenStreetMap (Overpass API + extracto Geofabrik Perú + OSMnx)",
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

    Los pares del corredor declarado y los de las zonas de extensión se
    reportan con la columna ``alcance`` (``corredor`` | ``extension``) para
    poder sumarlos por separado.
    """
    import networkx as nx

    G = topo.grafo
    nodo_de = dict(zip(ciudades["id_ciudad"], ciudades["id_nodo"]))
    nombre_de = dict(zip(ciudades["id_ciudad"], ciudades["nombre"]))
    filas: list[dict[str, Any]] = []

    pares = [(o, d, "corredor") for o, d in esc.conexiones]
    pares += [(o, d, "extension") for o, d in esc.extensiones if (o, d) not in esc.conexiones]

    for origen_id, destino_id, alcance in pares:
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
                "alcance": alcance,
                "id_nodo_origen": o,
                "id_nodo_destino": d,
                "distancia_km": round(km, 1),
                "tiempo_min": round(min_min, 1),
                "n_tramos": len(ids_tramos),
                "tramos_con_peaje": peajes,
                "tipo_via_dominante": _tipo_dominante(vias),
                "secuencia_tramos": ";".join(ids_tramos),
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
