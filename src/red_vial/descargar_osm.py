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
import math
import time
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import osmnx as ox
import requests

from ..comun.config import RAIZ, Escenario
from ..red_vial.corredores import (
    ACCESO_DEPOSITO,
    ALINEAMIENTO_COSTA,
    CORREDOR_CENTRAL,
    CORREDOR_PE1N,
    RAMAL_CHULUCANAS,
    RAMAL_PAITA,
)

log = logging.getLogger(__name__)

# OSMnx descarta al parsear toda etiqueta que no esté en `useful_tags_way`, y
# `toll` no viene por defecto. Sin esta línea el atributo `peaje` de los tramos
# salía siempre en falso aunque OSM sí marca las vías de peaje (`toll=yes`).
if "toll" not in ox.settings.useful_tags_way:
    ox.settings.useful_tags_way.append("toll")

# Restricciones de vehículos pesados. `oneway` ya viene en `useful_tags_way`
# desde OSMnx 2.x, pero `hgv`, `maxweight`, `maxheight`, `maxwidth` y
# `maxlength` no: sin declararlas aquí el perfil de camión se queda sin datos y
# los tramos donde OSM prohíbe el paso de pesados aparecen como transitables.
ETIQUETAS_PESADOS = [
    "hgv", "hgv:conditional", "maxweight", "maxheight",
    "maxwidth", "maxlength", "access",
]
for _etiqueta in ETIQUETAS_PESADOS:
    if _etiqueta not in ox.settings.useful_tags_way:
        ox.settings.useful_tags_way.append(_etiqueta)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "tesis-uni-marl-rutas/1.0 (investigacion academica)"

# Espejos públicos de Overpass. El endpoint principal se satura con frecuencia
# (HTTP 429 / 502 "All query workers are busy"), por lo que se rota entre ellos.
# El orden importa: los dos primeros responden en menos de un minuto; kumi y
# private.coffee se dejan al final porque con recuadros grandes se quedan
# colgados sin devolver respuesta (el cliente agota el tiempo de lectura).
ESPEJOS_OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",     # misma instancia, otro frontal
    "https://lz4.overpass-api.de/api/interpreter",   # idem (sin compresión si no se pide)
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Tiempo máximo de lectura por intento (s). Una banda de 7 MB tarda ~50 s; por
# encima de 150 s el espejo está colgado y conviene pasar al siguiente.
TIMEOUT_LECTURA_S = 150

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

# Vías del acceso terminal al CEDI: la jerarquía interurbana más la red
# terciaria del cono sur de Lima (el depósito está sobre una vía local y la
# primera vía de rango terciario está a ~1.9 km).
TIPOS_ACCESO = TIPOS_CORREDOR + ["tertiary", "tertiary_link"]

# Corredores andinos: solo el eje principal. En el este de Lima las vías
# secundarias son miles y hacen que Overpass exceda su tiempo máximo de
# respuesta (504); la Carretera Central es `primary`/`trunk` en todo su
# recorrido y la red urbana de Lima se descarga aparte con toda su jerarquía.
TIPOS_CARRETERA = [
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
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


# Memoria que se declara a Overpass. Los espejos públicos rechazan de inmediato
# (HTTP 502) las consultas que piden 1 GB, aunque el resultado sea de menos de
# 1 MB: la reserva se hace por adelantado y supera su cuota. Con 256 MB las
# mismas consultas responden en ~40 s.
MAXSIZE_MB = 268435456

def _consulta_overpass(puntos: list[tuple[float, float]], radio_m: int) -> str:
    """Construye la consulta OverpassQL de vías cercanas a una polilínea."""
    coords = ",".join(f"{lat:.5f},{lon:.5f}" for lat, lon in puntos)
    return f"""
[out:xml][timeout:900][maxsize:{MAXSIZE_MB}];
(
  way(around:{radio_m},{coords}){FILTRO_CORREDOR};
);
(._;>;);
out body;
"""


def _consulta_bbox(
    bbox: tuple[float, float, float, float],
    tipos: tuple[str, ...] | list[str] = tuple(TIPOS_CORREDOR),
) -> str:
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
        for t in tipos
    )
    return f"""[out:xml][timeout:300][maxsize:{MAXSIZE_MB}];
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


def _bandas_eje(
    puntos: list[tuple[float, float]],
    eje: str = "lon",
    alto_grados: float = 0.35,
    margen_cruzado: float = 0.20,
    margen_eje: float = 0.12,
    ventana: float = 0.15,
) -> list[tuple[float, float, float, float]]:
    """Divide una polilínea en bandas perpendiculares a un eje.

    La Carretera Central corre de oeste a este (Lima → La Oroya → Huancayo), de
    modo que bandearla por latitud produciría un único recuadro de 200 km de
    ancho. Aquí el bandeo se hace sobre el eje de mayor extensión (longitud para
    los corredores andinos, latitud para los verticales) y la extensión de cada
    banda se toma de los puntos de paso que caen en ella, ampliada con una
    ``ventana`` para no cortar la vía en las curvas.
    """
    i = 1 if eje == "lon" else 0
    j = 0 if eje == "lon" else 1
    vals = [p[i] for p in puntos]
    lo, hi = min(vals), max(vals)

    recuadros: list[tuple[float, float, float, float]] = []
    x = lo
    while x < hi - 1e-9:
        x2 = min(x + alto_grados, hi)
        sel = [p for p in puntos if x - ventana <= p[i] <= x2 + ventana]
        if sel:
            c_lo = min(p[j] for p in sel) - margen_cruzado
            c_hi = max(p[j] for p in sel) + margen_cruzado
            if eje == "lon":
                recuadros.append((c_lo, x - margen_eje, c_hi, x2 + margen_eje))
            else:
                recuadros.append((x - margen_eje, c_lo, x2 + margen_eje, c_hi))
        x += alto_grados
    return recuadros


def _post_overpass(consulta: str, destino: Path, reintentos: int = 8) -> Path:
    """Ejecuta la consulta y guarda el XML resultante, rotando espejos."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size > 10_000:
        log.info("Usando caché OSM: %s (%.1f MB)",
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
                timeout=(20, TIMEOUT_LECTURA_S),
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
@dataclass(frozen=True)
class ZonaDescarga:
    """Conjunto de recuadros Overpass que cubren una parte del ámbito.

    La descarga se organiza por zonas independientes (una por corredor o
    acceso) en lugar de un único conjunto de bandas: así cada zona se cachea
    con su propio prefijo de archivo y ampliar la cobertura no invalida lo ya
    descargado.
    """

    etiqueta: str
    recuadros: list[tuple[float, float, float, float]]
    tipos: tuple[str, ...] = tuple(TIPOS_CORREDOR)
    descripcion: str = ""
    # Lado máximo (grados) de cada celda de descarga. Los espejos públicos
    # rechazan recuadros grandes en zonas densas (HTTP 502/504) porque la
    # consulta supera su tiempo máximo; subdividirlos en celdas mantiene cada
    # petición por debajo de ~1 min. ``None`` = descargar el recuadro entero.
    max_lado_grados: float | None = None


def _teselar(
    bbox: tuple[float, float, float, float], max_lado: float
) -> list[tuple[float, float, float, float]]:
    """Divide un recuadro en una malla de celdas de lado ≤ ``max_lado``."""
    s, w, n, e = bbox
    filas = max(1, math.ceil((n - s) / max_lado))
    cols = max(1, math.ceil((e - w) / max_lado))
    alto = (n - s) / filas
    ancho = (e - w) / cols
    return [
        (s + i * alto, w + j * ancho, s + (i + 1) * alto, w + (j + 1) * ancho)
        for i in range(filas)
        for j in range(cols)
    ]


def zonas_descarga() -> list[ZonaDescarga]:
    """Zonas que, en conjunto, cubren el 100 % de los puntos de entrega reales.

    * ``corredor``: Panamericana Norte (PE-1N) Lima–Sullana, su alineamiento
      costero y el ramal a Chulucanas (17 distribuidores del plan de tesis).
    * ``central``: Carretera Central Lima–La Oroya–Jauja–Huancayo (GEO-023/024/025).
    * ``paita``: ramal PE-1N Piura–La Unión–Paita (GEO-019).
    * ``acceso``: acceso terminal al CEDI de Lima, origen de los 500 viajes.
    """
    norte = ZonaDescarga(
        etiqueta="corredor",
        recuadros=_bandas_corredor(_polilinea_corredor()),
        tipos=tuple(TIPOS_CORREDOR),
        descripcion="PE-1N Lima–Sullana + alineamiento costero + ramal Chulucanas",
    )
    central = ZonaDescarga(
        etiqueta="central",
        recuadros=_bandas_eje(
            [(p.lat, p.lon) for p in CORREDOR_CENTRAL],
            eje="lon", alto_grados=0.35,
            margen_cruzado=0.25, margen_eje=0.12, ventana=0.15,
        ),
        tipos=tuple(TIPOS_CARRETERA),
        descripcion="Carretera Central Lima–La Oroya–Jauja–Huancayo",
        max_lado_grados=0.40,
    )
    paita = ZonaDescarga(
        etiqueta="paita",
        recuadros=_bandas_eje(
            [(p.lat, p.lon) for p in RAMAL_PAITA],
            eje="lon", alto_grados=0.60,
            margen_cruzado=0.15, margen_eje=0.10, ventana=0.20,
        ),
        tipos=tuple(TIPOS_CORREDOR),
        descripcion="Ramal PE-1N Piura–La Unión–Paita",
        max_lado_grados=0.40,
    )
    acceso = ZonaDescarga(
        etiqueta="acceso",
        recuadros=_bandas_eje(
            [(p.lat, p.lon) for p in ACCESO_DEPOSITO],
            eje="lat", alto_grados=0.50,
            margen_cruzado=0.05, margen_eje=0.06, ventana=0.20,
        ),
        tipos=tuple(TIPOS_ACCESO),
        descripcion="Acceso terminal al CEDI de Lima (cono sur)",
    )
    return [norte, central, paita, acceso]


def _descargar_zonas(
    zonas: list[ZonaDescarga], cache_dir: Path
) -> list[nx.MultiDiGraph]:
    """Descarga (o reutiliza de caché) cada recuadro de cada zona.

    Si una banda completa ya está en caché se usa tal cual. Si no, y la zona
    declara ``max_lado_grados``, se descarga por celdas: los espejos públicos
    devuelven 502/504 cuando la consulta excede su tiempo máximo.
    """
    grafos: list[nx.MultiDiGraph] = []
    for zona in zonas:
        log.info(
            "Zona '%s' — %d recuadros — %s",
            zona.etiqueta, len(zona.recuadros), zona.descripcion,
        )
        for i, bbox in enumerate(zona.recuadros, start=1):
            entero = cache_dir / f"{zona.etiqueta}_b{i:02d}.osm"
            if zona.max_lado_grados and not (
                entero.exists() and entero.stat().st_size > 10_000
            ):
                piezas = _teselar(bbox, zona.max_lado_grados)
            else:
                piezas = [bbox]

            for j, sub in enumerate(piezas, start=1):
                if len(piezas) == 1:
                    xml = entero
                    etiqueta = f"banda {i:02d}/{len(zona.recuadros)}"
                else:
                    xml = cache_dir / f"{zona.etiqueta}_b{i:02d}_t{j:02d}.osm"
                    etiqueta = (
                        f"banda {i:02d}/{len(zona.recuadros)} "
                        f"celda {j:02d}/{len(piezas)}"
                    )
                s, w, n, e = sub
                log.info("  %s  lat %.3f..%.3f  lon %.3f..%.3f", etiqueta, s, n, w, e)
                try:
                    _post_overpass(_consulta_bbox(sub, zona.tipos), xml)
                except Exception as exc:  # noqa: BLE001
                    log.error("  %s no descargada: %s", etiqueta, exc)
                    continue
                # simplify=False: la simplificación contrae los nodos de grado 2
                # y, si se aplica recuadro por recuadro, cada uno elimina nodos
                # distintos en su borde; al componer, los extremos ya no
                # coinciden y el corredor queda partido en pedazos. Se conservan
                # todos los nodos de geometría y la contracción se hace una sola
                # vez en `topologia.consolidar_tramos`.
                G = ox.graph_from_xml(xml, simplify=False, retain_all=True)
                G = _con_velocidades(G)
                grafos.append(G)
                log.info(
                    "  %s: %d nodos, %d aristas",
                    etiqueta, G.number_of_nodes(), G.number_of_edges(),
                )
    return grafos


def descargar_corredor(cache_dir: Path | None = None) -> nx.MultiDiGraph:
    """Descarga y compone la red interurbana de todas las zonas del ámbito.

    Cada recuadro se cachea por separado (``corredor_bNN.osm``,
    ``central_bNN.osm``, …); si uno falla se reintenta con otro espejo sin
    perder lo ya descargado.
    """
    cache_dir = cache_dir or (RAIZ / "data" / "raw" / "osm")
    cache_dir.mkdir(parents=True, exist_ok=True)

    grafos = _descargar_zonas(zonas_descarga(), cache_dir)
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
    return f"""[out:xml][timeout:300][maxsize:{MAXSIZE_MB}];
(
{cuerpo}
);
(._;>;);
out body;
"""


def descargar_ciudad(
    lat: float, lon: float, dist_m: int = RADIO_CIUDAD_M,
    cache_dir: Path | None = None, etiqueta: str = "ciudad",
    forzar_xml: bool = False,
) -> nx.MultiDiGraph:
    """Descarga la red urbana completa alrededor de una ciudad.

    Usa OverpassQL directo (con rotación de espejos) en lugar de
    ``ox.graph_from_point`` para poder reintentar cuando el endpoint público
    responde 429/502 por saturación.

    ``forzar_xml`` ignora el ``.graphml`` cacheado y vuelve a parsear el ``.osm``
    original. Es necesario cuando se amplía ``useful_tags_way``: el GraphML
    guardado antes no contiene las etiquetas nuevas y reutilizarlo dejaría la
    red sin restricciones de pesados.
    """
    cache_dir = cache_dir or (RAIZ / "data" / "raw" / "osm")
    cache_dir.mkdir(parents=True, exist_ok=True)
    graphml = cache_dir / f"ciudad_{etiqueta}.graphml"
    xml = cache_dir / f"ciudad_{etiqueta}.osm"

    if graphml.exists() and not forzar_xml:
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
def descargar_todo(
    esc: Escenario | None = None,
    incluir_urbano: bool = True,
    forzar_xml: bool = False,
) -> nx.MultiDiGraph:
    """Descarga y compone la red vial completa del escenario.

    ``forzar_xml`` obliga a reparsear los ``.osm`` cacheados en lugar de
    reutilizar los ``.graphml``: hace falta cuando se añaden etiquetas nuevas a
    ``useful_tags_way``, porque los GraphML antiguos no las contienen.
    """
    esc = esc or Escenario.cargar()
    cache_dir = RAIZ / "data" / "raw" / "osm"
    cache_dir.mkdir(parents=True, exist_ok=True)

    grafos = _descargar_zonas(zonas_descarga(), cache_dir)

    if incluir_urbano:
        for ciudad in esc.ciudades:
            # Se incluye siempre el hub de origen aunque no tenga distribuidores:
            # es el punto de partida de todos los viajes y necesita su red urbana.
            # Las zonas de extensión (Huancayo, Paita) la piden explícitamente con
            # `red_urbana: true` para que el snapping de sus puntos sea preciso.
            es_hub = ciudad.get("rol") == "hub_origen"
            requiere = (
                int(ciudad.get("distribuidores", 0)) > 0
                or es_hub
                or bool(ciudad.get("red_urbana", False))
            )
            if not requiere:
                continue
            dist = RADIO_CIUDAD_M_LIMA if ciudad["id"] == "LIM" else RADIO_CIUDAD_M
            try:
                grafos.append(
                    descargar_ciudad(
                        ciudad["lat"], ciudad["lon"], dist_m=dist,
                        cache_dir=cache_dir, etiqueta=ciudad["id"],
                        forzar_xml=forzar_xml,
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

