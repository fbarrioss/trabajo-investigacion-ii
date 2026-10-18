"""Descarga de la red vial real desde OpenStreetMap.

Estrategia
----------
El corredor Lima–Chulucanas mide ~1 100 km. Descargar un *bounding box* único
sería inviable (Overpass devolvería millones de elementos). En su lugar se usa
una consulta ``around`` sobre la polilínea real de la PE-1N: solo se traen las
vías que están a menos de N km de la ruta, más una descarga radial por ciudad
para capturar la red urbana de acceso a los distribuidores.

Ventajas:
  * Una sola consulta para el corredor (rápida y acotada).
  * Geometría y topología 100 % reales (IDs OSM).
  * Reproducible: el grafo se cachea en ``data/raw/osm``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import networkx as nx
import osmnx as ox
import requests

from ..comun.config import RAIZ, Escenario
from ..red_vial.corredores import (
    ALINEAMIENTO_COSTA,
    CORREDOR_PE1N,
    RAMAL_CHULUCANAS,
)

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "tesis-uni-marl-rutas/1.0 (investigacion academica)"

# Espejos públicos de Overpass. El endpoint principal se satura con frecuencia
# (HTTP 429 / 502 "All query workers are busy"), por lo que se rota entre ellos.
ESPEJOS_OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Vías que forman la red de largo recorrido.
TIPOS_CORREDOR = [
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
]

# Vías urbanas transitables por camión (red "drive").
TIPOS_URBANOS = TIPOS_CORREDOR + [
    "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street",
]

# Se mantiene por compatibilidad con los scripts antiguos.
FILTRO_CORREDOR = '["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link)$"]'

# Radio (m) alrededor de la polilínea para capturar la red de acceso.
RADIO_CORREDOR_M = 4000

# Radio (m) de la red urbana por ciudad (Lima requiere más cobertura).
RADIO_CIUDAD_M = 6000
RADIO_CIUDAD_M_LIMA = 9000

# Altura de cada banda del corredor y margen longitudinal (grados).
BANDA_ALTO_GRADOS = 0.75
BANDA_MARGEN_LON = 0.18

# Velocidades por defecto (km/h) cuando OSM no trae ``maxspeed``.
# Calibradas con los límites legales peruanos (D.S. 033-2001-MTC) y con las
# velocidades de flujo libre observadas en la Panamericana Norte.
VELOCIDADES_POR_TIPO = {
    "motorway": 100, "motorway_link": 60,
    "trunk": 90, "trunk_link": 55,
    "primary": 80, "primary_link": 50,
    "secondary": 70, "secondary_link": 45,
    "tertiary": 55, "tertiary_link": 40,
    "unclassified": 40, "residential": 30, "living_street": 20,
}


def _con_velocidades(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Añade velocidad y tiempo de recorrido tolerando grafos sin ``maxspeed``."""
    G = ox.add_edge_speeds(G, hwy_speeds=VELOCIDADES_POR_TIPO, fallback=40.0)
    G = ox.add_edge_travel_times(G)
    return G


# ---------------------------------------------------------------------------
def _polilinea_corredor() -> list[tuple[float, float]]:
    """Puntos (lat, lon) que definen la cobertura del corredor.

    Incluye la PE-1N, el alineamiento costero alterno Chiclayo–Piura y el ramal
    a Chulucanas. No es una ruta única: es el conjunto de puntos de paso que
    determina qué recuadros se descargan, de modo que la red resultante ofrezca
    las dos alternativas reales de recorrido.
    """
    puntos: list[tuple[float, float]] = []
    for p in CORREDOR_PE1N:
        puntos.append((p.lat, p.lon))
    for p in ALINEAMIENTO_COSTA:
        puntos.append((p.lat, p.lon))
    # Ramal a Chulucanas (evita duplicar Piura).
    for p in RAMAL_CHULUCANAS[1:]:
        puntos.append((p.lat, p.lon))
    return puntos


def _consulta_overpass(puntos: list[tuple[float, float]], radio_m: int) -> str:
    """Construye la consulta OverpassQL de vías cercanas a una polilínea."""
    coords = ",".join(f"{lat:.5f},{lon:.5f}" for lat, lon in puntos)
    return f"""
[out:xml][timeout:900][maxsize:1073741824];
(
  way(around:{radio_m},{coords}){FILTRO_CORREDOR};
);
(._;>;);
out body;
"""


def _consulta_bbox(bbox: tuple[float, float, float, float]) -> str:
    """Consulta OverpassQL de la red de carga dentro de un recuadro.

    ``bbox`` = (sur, oeste, norte, este) en grados.

    Se usa una unión de coincidencias **exactas** (``["highway"="primary"]``) en
    lugar de una expresión regular: Overpass resuelve las coincidencias exactas
    por índice y responde en segundos, mientras que la regex obliga a recorrer
    todas las vías del recuadro y termina en 500/504 por agotamiento de memoria.
    """
    s, w, n, e = bbox
    cuerpo = "\n".join(
        f'  way({s:.4f},{w:.4f},{n:.4f},{e:.4f})["highway"="{t}"];'
        for t in TIPOS_CORREDOR
    )
    return f"""[out:xml][timeout:300][maxsize:1073741824];
(
{cuerpo}
);
(._;>;);
out body;
"""


def _bandas_corredor(
    puntos: list[tuple[float, float]],
    alto_grados: float = BANDA_ALTO_GRADOS,
    margen_lon: float = BANDA_MARGEN_LON,
) -> list[tuple[float, float, float, float]]:
    """Divide el corredor en bandas horizontales y devuelve sus recuadros.

    Overpass **no** interpreta una lista larga de coordenadas como polilínea en
    ``around`` (solo usa el primer punto), de ahí que la descarga anterior se
    limitara al área de Lima. La alternativa robusta es cubrir el corredor con
    recuadros solapados: cada banda toma la longitud de los puntos de paso que
    caen dentro de ella (más los vecinos) y añade un margen para no cortar la
    vía cuando se curva.
    """
    lats = [p[0] for p in puntos]
    lo, hi = min(lats), max(lats)
    recuadros: list[tuple[float, float, float, float]] = []
    y = lo
    while y < hi:
        y2 = min(y + alto_grados, hi)
        # Puntos de paso de la banda, ampliada para no perder continuidad.
        sel = [p for p in puntos if y - 0.35 <= p[0] <= y2 + 0.35]
        if sel:
            lons = [p[1] for p in sel]
            recuadros.append(
                (y - 0.05, min(lons) - margen_lon, y2 + 0.05, max(lons) + margen_lon)
            )
        y += alto_grados
    return recuadros


def _post_overpass(consulta: str, destino: Path, reintentos: int = 6) -> Path:
    """Ejecuta la consulta y guarda el XML resultante, rotando espejos."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size > 10_000:
        log.info("Usando caché Overpass: %s (%.1f MB)",
                 destino.name, destino.stat().st_size / 1e6)
        return destino

    for intento in range(1, reintentos + 1):
        url = ESPEJOS_OVERPASS[(intento - 1) % len(ESPEJOS_OVERPASS)]
        try:
            log.info("Consulta Overpass (intento %d/%d, espejo %s)...",
                     intento, reintentos, url.split("/")[2])
            t0 = time.time()
            resp = requests.post(
                url,
                data={"data": consulta},
                headers={"User-Agent": USER_AGENT},
                timeout=(20, 300),
            )
            resp.raise_for_status()
            if len(resp.content) < 1000:
                raise RuntimeError(f"Respuesta sospechosamente corta: {len(resp.content)} B")
            destino.write_bytes(resp.content)
            log.info(
                "Descargado %.1f MB en %.0f s", len(resp.content) / 1e6, time.time() - t0
            )
            return destino
        except Exception as exc:  # noqa: BLE001
            log.warning("Fallo en intento %d (%s): %s", intento, url.split("/")[2], exc)
            if intento == reintentos:
                raise
            espera = min(20 * intento, 120)
            log.info("Reintentando en %d s...", espera)
            time.sleep(espera)
    return destino


# ---------------------------------------------------------------------------
def descargar_corredor(cache_dir: Path | None = None) -> nx.MultiDiGraph:
    """Descarga la red de carga del corredor en bandas solapadas.

    Cada banda se cachea por separado (``corredor_bNN.osm``); si una falla se
    reintenta con otro espejo sin perder lo ya descargado.
    """
    cache_dir = cache_dir or (RAIZ / "data" / "raw" / "osm")
    cache_dir.mkdir(parents=True, exist_ok=True)

    puntos = _polilinea_corredor()
    recuadros = _bandas_corredor(puntos)
    log.info("Corredor: %d bandas a descargar", len(recuadros))

    grafos: list[nx.MultiDiGraph] = []
    for i, bbox in enumerate(recuadros, start=1):
        xml = cache_dir / f"corredor_b{i:02d}.osm"
        s, w, n, e = bbox
        log.info(
            "Banda %02d/%02d  lat %.2f..%.2f  lon %.2f..%.2f",
            i, len(recuadros), s, n, w, e,
        )
        try:
            _post_overpass(_consulta_bbox(bbox), xml)
        except Exception as exc:  # noqa: BLE001
            log.error("Banda %02d no descargada: %s", i, exc)
            continue
        # simplify=False: la simplificación contrae los nodos de grado 2 y, si
        # se aplica banda por banda, cada banda elimina nodos distintos en su
        # borde; al componer, los extremos ya no coinciden y el corredor queda
        # partido en pedazos. Se conservan todos los nodos de geometría y la
        # contracción se hace una sola vez en `topologia.consolidar_tramos`.
        G = ox.graph_from_xml(xml, simplify=False, retain_all=True)
        G = _con_velocidades(G)
        grafos.append(G)
        log.info(
            "  banda %02d: %d nodos, %d aristas",
            i, G.number_of_nodes(), G.number_of_edges(),
        )

    if not grafos:
        raise RuntimeError("No se pudo descargar ninguna banda del corredor")

    G = componer_grafo(grafos)
    log.info("Corredor compuesto: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())
    return G


# Vías urbanas transitables por camión (red "drive").
FILTRO_URBANO = (
    '["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|'
    'secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|'
    'living_street)$"]'
)


def _consulta_urbana(lat: float, lon: float, radio_m: int) -> str:
    """Consulta OverpassQL de la red urbana alrededor de un punto."""
    cuerpo = "\n".join(
        f'  way(around:{radio_m},{lat:.5f},{lon:.5f})["highway"="{t}"];'
        for t in TIPOS_URBANOS
    )
    return f"""[out:xml][timeout:300][maxsize:1073741824];
(
{cuerpo}
);
(._;>;);
out body;
"""


def descargar_ciudad(
    lat: float, lon: float, dist_m: int = RADIO_CIUDAD_M,
    cache_dir: Path | None = None, etiqueta: str = "ciudad",
) -> nx.MultiDiGraph:
    """Descarga la red urbana completa alrededor de una ciudad.

    Usa OverpassQL directo (con rotación de espejos) en lugar de
    ``ox.graph_from_point`` para poder reintentar cuando el endpoint público
    responde 429/502 por saturación.
    """
    cache_dir = cache_dir or (RAIZ / "data" / "raw" / "osm")
    cache_dir.mkdir(parents=True, exist_ok=True)
    graphml = cache_dir / f"ciudad_{etiqueta}.graphml"
    xml = cache_dir / f"ciudad_{etiqueta}.osm"

    if graphml.exists():
        log.info("Usando caché urbana: %s", graphml.name)
        return ox.load_graphml(graphml)

    log.info("Descargando red urbana de %s (radio %d m)...", etiqueta, dist_m)
    _post_overpass(_consulta_urbana(lat, lon, dist_m), xml)

    G = ox.graph_from_xml(xml, simplify=False, retain_all=True)
    G = _con_velocidades(G)
    ox.save_graphml(G, graphml)
    log.info("%s: %d nodos, %d aristas", etiqueta, G.number_of_nodes(), G.number_of_edges())
    return G


def componer_grafo(grafos: list[nx.MultiDiGraph]) -> nx.MultiDiGraph:
    """Une varios grafos OSM en uno solo (los IDs OSM son globales)."""
    total = nx.MultiDiGraph()
    for g in grafos:
        total = nx.compose(total, g)
    # Elimina nodos aislados que aparecen en los bordes de cada descarga.
    aislados = [n for n, d in total.degree() if d == 0]
    total.remove_nodes_from(aislados)
    return total


# ---------------------------------------------------------------------------
def descargar_todo(esc: Escenario | None = None, incluir_urbano: bool = True) -> nx.MultiDiGraph:
    """Descarga y compone la red vial completa del escenario."""
    esc = esc or Escenario.cargar()
    cache_dir = RAIZ / "data" / "raw" / "osm"

    grafos = [descargar_corredor(cache_dir)]

    if incluir_urbano:
        for ciudad in esc.ciudades:
            # Se incluye siempre el hub de origen aunque no tenga distribuidores:
            # es el punto de partida de todos los viajes y necesita su red urbana.
            es_hub = ciudad.get("rol") == "hub_origen"
            if int(ciudad.get("distribuidores", 0)) <= 0 and not es_hub:
                continue
            dist = RADIO_CIUDAD_M_LIMA if ciudad["id"] == "LIM" else RADIO_CIUDAD_M
            try:
                grafos.append(
                    descargar_ciudad(
                        ciudad["lat"], ciudad["lon"], dist_m=dist,
                        cache_dir=cache_dir, etiqueta=ciudad["id"],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "No se pudo descargar la red urbana de %s: %s. "
                    "Se continúa con el resto (relanzar luego para completarla).",
                    ciudad["id"], exc,
                )

    G = componer_grafo(grafos)
    log.info("Grafo compuesto: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())

    salida = cache_dir / "red_completa.graphml"
    ox.save_graphml(G, salida)
    log.info("Grafo guardado en %s", salida)
    return G


# ---------------------------------------------------------------------------
def descargar_urbano_individual(etiqueta: str, esc: Escenario | None = None) -> Path:
    """Descarga (o reutiliza de caché) la red urbana de una sola ciudad.

    Permite avanzar ciudad por ciudad cuando Overpass está saturado, en lugar
    de abortar toda la descarga completa.
    """
    esc = esc or Escenario.cargar()
    cache_dir = RAIZ / "data" / "raw" / "osm"
    ciudad = next((c for c in esc.ciudades if c["id"] == etiqueta.upper()), None)
    if ciudad is None:
        raise KeyError(f"Ciudad '{etiqueta}' no existe en el escenario")
    dist = RADIO_CIUDAD_M_LIMA if ciudad["id"] == "LIM" else RADIO_CIUDAD_M
    descargar_ciudad(
        ciudad["lat"], ciudad["lon"], dist_m=dist,
        cache_dir=cache_dir, etiqueta=ciudad["id"],
    )
    return cache_dir / f"ciudad_{ciudad['id']}.graphml"


def estado_cache(cache_dir: Path | None = None) -> dict[str, str]:
    """Inventario legible de lo que ya está descargado."""
    cache_dir = cache_dir or (RAIZ / "data" / "raw" / "osm")
    inventario: dict[str, str] = {}
    if not cache_dir.exists():
        return inventario
    for f in sorted(cache_dir.iterdir()):
        if f.suffix in {".osm", ".graphml"}:
            inventario[f.name] = f"{f.stat().st_size / 1e6:.1f} MB"
    return inventario


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s"
    )
    objetivo = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if objetivo.lower() == "todo":
        descargar_todo()
    elif objetivo.lower() == "estado":
        for nombre, tam in estado_cache().items():
            print(f"  {nombre:<28} {tam}")
    else:
        for etq in objetivo.split(","):
            descargar_urbano_individual(etq.strip())

