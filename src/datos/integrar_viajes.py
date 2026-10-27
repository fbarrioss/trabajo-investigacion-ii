"""Integración de los viajes reales de la empresa con la red vial del corredor.

Este módulo resuelve el punto 2 del asesor (encajar los viajes registrados sobre
el universo de tramos) y el punto 3 (discretizar el viaje sin GPS continuo).

Flujo
-----
1. Lee los Excel operativos (`puntos_entrega.xlsx` y el libro de viajes).
2. Geocodifica los 25 puntos de entrega con Nominatim, con caché en disco y
   *fallbacks* documentados (coordenada de viajes → coordenada de catálogo).
3. Hace *snapping* de los 25 puntos + el depósito a los nodos reales de la red.
4. Reconstruye la ruta de cada viaje sobre el grafo (Dijkstra por tiempo),
   parada a parada, y acumula distancia y tiempo por red.
5. Escribe el dataset de *benchmark* (línea base de la operación manual).

Cobertura
---------
La red cubre el corredor de la Panamericana Norte **más** las tres zonas que
faltaban para reconstruir el 100 % de los viajes registrados: la Carretera
Central Lima–Huancayo (GEO-023/024/025), el ramal Piura–Paita (GEO-019) y el
acceso terminal al CEDI de Lima. Ver ``docs/06_cobertura_extendida.md``.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from ..comun.geo import haversine_km

# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------

ORIGEN_LAT = -12.2302778
ORIGEN_LON = -76.9655556
ORIGEN_ID = "CEDI-LIMA"

# Tolerancia de snapping: por encima de este umbral el punto se considera fuera
# de cobertura y su ruta no se usa en el benchmark estricto.
TOLERANCIA_SNAP_M = 2_000.0

# Departamentos reconocibles en la dirección (clave en mayúsculas sin tilde).
DEPARTAMENTOS = {
    "JUNIN": "Junín",
    "LA LIBERTAD": "La Libertad",
    "ANCASH": "Ancash",
    "LAMBAYEQUE": "Lambayeque",
    "PIURA": "Piura",
}

# Ciudades por departamento, de la más específica a la más general. Se buscan
# dentro del departamento ya detectado para evitar falsos positivos como
# "LA VICTORIA" (nombre de calle en Huancayo) tomado como ciudad.
CIUDADES_POR_DEPTO = {
    "Junín": [
        ("NUEVO CHIMBOTE", "Nuevo Chimbote"),  # no aplica; se deja por simetría
        ("HUANCAYO", "Huancayo"),
        ("EL TAMBO", "El Tambo"),
    ],
    "La Libertad": [("TRUJILLO", "Trujillo")],
    "Ancash": [("NUEVO CHIMBOTE", "Nuevo Chimbote"), ("CHIMBOTE", "Chimbote")],
    "Lambayeque": [
        ("CHICLAYO", "Chiclayo"),
        ("LA VICTORIA", "La Victoria"),
        ("MONSEFU", "Monsefú"),
    ],
    "Piura": [
        ("TAMBO GRANDE", "Tambo Grande"),
        ("CHULUCANAS", "Chulucanas"),
        ("SULLANA", "Sullana"),
        ("PAITA", "Paita"),
        ("PIURA", "Piura"),
    ],
}

# Compatibilidad: mapa plano ciudad → departamento para las consultas.
CIUDADES = {
    ciudad.upper(): (ciudad, depto)
    for depto, lista in CIUDADES_POR_DEPTO.items()
    for _, ciudad in lista
    if ciudad != "Nuevo Chimbote" or depto == "Ancash"
}


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------


@dataclass
class Punto:
    id_punto: int
    codigo: str
    nombre: str
    direccion: str
    ciudad: str
    departamento: str
    lat_catalogo: float | None
    lon_catalogo: float | None
    lat_viajes: float | None = None
    lon_viajes: float | None = None
    lat_geo: float | None = None
    lon_geo: float | None = None
    fuente: str = ""
    nodo_snap: int | None = None
    dist_snap_m: float = float("nan")
    fuera_cobertura: bool = False


# ---------------------------------------------------------------------------
# Lectura de los Excel
# ---------------------------------------------------------------------------


def _limpiar_direccion(texto: str) -> str:
    """Quita el prefijo ``Dirección:`` y normaliza espacios."""
    t = str(texto).strip()
    if t.lower().startswith("dirección:"):
        t = t.split(":", 1)[1]
    return " ".join(t.split())


def _parsear_ciudad(direccion: str) -> tuple[str, str]:
    """Infiere (ciudad, departamento) usando el departamento como desambiguador.

    1. Detecta el departamento presente en la dirección (el que aparece más a la
       derecha si hay varios).
    2. Dentro de ese departamento, elige la ciudad conocida que aparece más cerca
       del final (el sufijo administrativo de la dirección).
    """
    mayus = direccion.upper()

    # Departamento: el que aparezca más a la derecha.
    depto = ""
    pos_depto = -1
    for clave, nombre in DEPARTAMENTOS.items():
        pos = mayus.rfind(clave)
        if pos > pos_depto:
            pos_depto = pos
            depto = nombre

    if depto and depto in CIUDADES_POR_DEPTO:
        # Se recorre la lista en orden de prioridad (más específica primero) y
        # se toma la primera que aparezca. Así "SULLANA-SULLANA-PIURA" elige
        # Sullana y no Piura, y "CHULUCANAS-MORROPON-PIURA" elige Chulucanas.
        for clave, ciudad in CIUDADES_POR_DEPTO[depto]:
            if clave in mayus:
                return ciudad, depto

    # Fallback: buscar cualquier ciudad conocida, la más a la derecha.
    mejor_ciudad, mejor_pos, mejor_depto = "", -1, ""
    for clave, (ciudad, d) in CIUDADES.items():
        pos = mayus.rfind(clave)
        if pos > mejor_pos:
            mejor_pos = pos
            mejor_ciudad = ciudad
            mejor_depto = d
    return mejor_ciudad, mejor_depto


def leer_excel(raiz: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Devuelve (catálogo de puntos, paradas por viaje, resumen de viajes)."""
    ruta_cat = raiz / "puntos_entrega.xlsx"
    ruta_viajes = raiz / "EYM_V5_Viajes_Puntos_Entrega_Secuencias.xlsx"
    cat = pd.read_excel(ruta_cat, sheet_name="Hoja1")
    paradas = pd.read_excel(ruta_viajes, sheet_name="PUNTOS_POR_VIAJE")
    resumen = pd.read_excel(ruta_viajes, sheet_name="RESUMEN_VIAJES")
    return cat, paradas, resumen


def construir_puntos(cat: pd.DataFrame, paradas: pd.DataFrame) -> list[Punto]:
    """Combina el catálogo con las coordenadas observadas en los viajes."""
    # Coordenada de viajes por punto (la primera observada, es estable).
    coords_viajes = (
        paradas.groupby("id_punto")[["latitud", "longitud"]].first().to_dict("index")
    )
    puntos: list[Punto] = []
    for _, r in cat.iterrows():
        idp = int(r["id_punto"])
        direccion = _limpiar_direccion(r["restricciones"])
        ciudad, depto = _parsear_ciudad(direccion)
        cv = coords_viajes.get(idp, {})
        puntos.append(
            Punto(
                id_punto=idp,
                codigo=str(r["codigo"]),
                nombre=str(r["nombre"]),
                direccion=direccion,
                ciudad=ciudad,
                departamento=depto,
                lat_catalogo=float(r["latitud"]),
                lon_catalogo=float(r["longitud"]),
                lat_viajes=float(cv["latitud"]) if cv else None,
                lon_viajes=float(cv["longitud"]) if cv else None,
            )
        )
    return puntos


# ---------------------------------------------------------------------------
# Geocodificación
# ---------------------------------------------------------------------------


def _consulta_variantes(p: Punto) -> list[str]:
    """Consultas de Nominatim, de la más específica a la más general.

    No se incluye la variante "solo ciudad": devuelve el centroide urbano y
    colapsaría todos los puntos de una ciudad en un mismo nodo.
    """
    calle = p.direccion
    mayus = calle.upper()
    corte = len(calle)
    for clave in CIUDADES:
        pos = mayus.find(clave)
        if pos > 0:
            corte = min(corte, pos)
    solo_calle = calle[:corte].strip(" -,.")
    variantes = []
    if p.ciudad:
        variantes.append(f"{calle}, {p.ciudad}, {p.departamento}, Perú")
        if solo_calle and solo_calle != calle:
            variantes.append(f"{solo_calle}, {p.ciudad}, {p.departamento}, Perú")
    variantes.append(f"{calle}, Perú")
    return variantes


def geocodificar(
    puntos: list[Punto],
    cache_path: Path,
    usar_red: bool = True,
) -> None:
    """Geocodifica in-place los puntos, con caché y fallbacks documentados.

    Orden de preferencia de la coordenada final:
        1. Nominatim, solo si el resultado cae a <500 m de la referencia
           (coordenada observada en los viajes). Un resultado más lejano suele
           ser el centroide de la ciudad o una calle homónima.
        2. Coordenada observada en los viajes (específica y real).
        3. Coordenada del catálogo.
    """
    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    geocoder = None
    if usar_red:
        try:
            from geopy.extra.rate_limiter import RateLimiter
            from geopy.geocoders import Nominatim

            _geo = Nominatim(user_agent="marl-rutas-carga-tesis/1.0")
            geocoder = RateLimiter(
                _geo.geocode, min_delay_seconds=1.1, error_wait_seconds=2.0
            )
        except Exception:  # pragma: no cover - sin geopy se usa solo el fallback
            geocoder = None

    coords_usadas: dict[tuple[float, float], int] = {}

    for p in puntos:
        clave = f"{p.id_punto}:{p.codigo}"
        if clave in cache and cache[clave].get("lat") is not None:
            c = cache[clave]
            p.lat_geo, p.lon_geo = c["lat"], c["lon"]
            p.fuente = c["fuente"]
            coords_usadas[(round(p.lat_geo, 5), round(p.lon_geo, 5))] = p.id_punto
            continue

        # Referencia local para validar el resultado del geocoder.
        ref_lat = p.lat_viajes if p.lat_viajes is not None else p.lat_catalogo
        ref_lon = p.lon_viajes if p.lon_viajes is not None else p.lon_catalogo

        hallado = False
        if geocoder is not None and p.ciudad:
            for consulta in _consulta_variantes(p):
                try:
                    loc = geocoder(consulta, timeout=15, exactly_one=True)
                except Exception:
                    loc = None
                if loc is None:
                    continue
                d = haversine_km(loc.latitude, loc.longitude, ref_lat, ref_lon)
                # Solo se acepta si coincide con la referencia (≤500 m): evita
                # aceptar el centroide de la ciudad o una calle homónima.
                if d <= 0.5:
                    p.lat_geo, p.lon_geo = float(loc.latitude), float(loc.longitude)
                    p.fuente = "nominatim"
                    hallado = True
                    break

        if not hallado:
            # Fallback 1: coordenada observada en los viajes.
            if p.lat_viajes is not None and p.lon_viajes is not None:
                p.lat_geo, p.lon_geo = p.lat_viajes, p.lon_viajes
                p.fuente = "viajes"
            else:
                # Fallback 2: coordenada del catálogo.
                p.lat_geo, p.lon_geo = p.lat_catalogo, p.lon_catalogo
                p.fuente = "catalogo"

        # Resolución de colapsos: si la coordenada ya está ocupada por otro
        # punto, se intenta la fuente alternativa (catálogo ↔ viajes). Esto
        # descolapsa los pares que comparten coordenada en una sola fuente.
        k = (round(p.lat_geo, 5), round(p.lon_geo, 5))
        if k in coords_usadas and coords_usadas[k] != p.id_punto:
            # Solo se acepta la alternativa si sigue dentro del área urbana
            # (≤8 km de la referencia). Un catálogo con coordenada errónea a
            # 100 km no debe reemplazar una coordenada observada correcta.
            if p.fuente == "viajes" and p.lat_catalogo is not None:
                alt = (p.lat_catalogo, p.lon_catalogo)
                d_alt = haversine_km(alt[0], alt[1], ref_lat, ref_lon)
                k_alt = (round(alt[0], 5), round(alt[1], 5))
                if k_alt != k and d_alt <= 8.0:
                    p.lat_geo, p.lon_geo = alt
                    p.fuente = "catalogo"
                    k = k_alt
            elif p.fuente == "catalogo" and p.lat_viajes is not None:
                alt = (p.lat_viajes, p.lon_viajes)
                d_alt = haversine_km(alt[0], alt[1], ref_lat, ref_lon)
                k_alt = (round(alt[0], 5), round(alt[1], 5))
                if k_alt != k and d_alt <= 8.0:
                    p.lat_geo, p.lon_geo = alt
                    p.fuente = "viajes"
                    k = k_alt
        coords_usadas[k] = p.id_punto

        cache[clave] = {
            "lat": p.lat_geo,
            "lon": p.lon_geo,
            "fuente": p.fuente,
            "ciudad": p.ciudad,
            "departamento": p.departamento,
        }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Grafo y snapping
# ---------------------------------------------------------------------------


def cargar_grafo(red_dir: Path) -> tuple[nx.DiGraph, pd.DataFrame]:
    """Construye el grafo dirigido a partir de los CSV de la topología."""
    nodos = pd.read_csv(red_dir / "nodos.csv")
    tramos = pd.read_csv(red_dir / "tramos.csv")

    G = nx.DiGraph()
    for r in nodos.itertuples(index=False):
        G.add_node(int(r.id_nodo), lat=float(r.lat), lon=float(r.lon))

    for r in tramos.itertuples(index=False):
        u, v = int(r.id_nodo_ini), int(r.id_nodo_fin)
        if u not in G or v not in G:
            continue
        peso = float(r.tiempo_flujo_min)
        dist = float(r.distancia_km)
        G.add_edge(u, v, tiempo=peso, distancia=dist, id_tramo=r.id_tramo)
        if bool(r.bidireccional):
            G.add_edge(v, u, tiempo=peso, distancia=dist, id_tramo=r.id_tramo)
    return G, nodos


def _nodo_mas_cercano(
    nodos: pd.DataFrame, lat: float, lon: float
) -> tuple[int, float]:
    """Nodo de la red más próximo (haversine) y su distancia en metros."""
    dlat = (nodos["lat"].to_numpy() - lat) * 111.0
    dlon = (nodos["lon"].to_numpy() - lon) * 111.0 * math.cos(math.radians(lat))
    d2 = dlat**2 + dlon**2
    i = int(np.argmin(d2))
    return int(nodos["id_nodo"].iloc[i]), float(math.sqrt(d2[i]) * 1000.0)


def hacer_snapping(puntos: list[Punto], nodos: pd.DataFrame) -> dict[str, int]:
    """Asigna a cada punto (y al depósito) su nodo de la red.

    Devuelve el mapeo ``id_punto -> id_nodo`` y actualiza los campos del punto.
    """
    mapeo: dict[str, int] = {}
    for p in puntos:
        nodo, dist_m = _nodo_mas_cercano(nodos, p.lat_geo, p.lon_geo)
        p.nodo_snap = nodo
        p.dist_snap_m = dist_m
        p.fuera_cobertura = dist_m > TOLERANCIA_SNAP_M
        mapeo[str(p.id_punto)] = nodo

    nodo, dist_m = _nodo_mas_cercano(nodos, ORIGEN_LAT, ORIGEN_LON)
    mapeo[ORIGEN_ID] = nodo
    # El origen se guarda como un Punto virtual en el mapeo; su distancia se
    # reporta por separado en el resumen.
    mapeo["__origen_dist_m"] = int(round(dist_m))
    return mapeo


# ---------------------------------------------------------------------------
# Reconstrucción de rutas
# ---------------------------------------------------------------------------


@dataclass
class RutaViaje:
    id_viaje: int
    codigo: str
    id_vehiculo: int
    n_paradas: int
    km_red: float = 0.0
    min_red: float = 0.0
    km_geodesica: float = 0.0
    paradas: list[dict] = field(default_factory=list)
    fuera_cobertura: bool = False
    error: str = ""


def _ruta_entre(
    G: nx.DiGraph, origen: int, destino: int
) -> tuple[float, float, list[str]] | None:
    """Dijkstra por tiempo entre dos nodos. Devuelve (km, min, ids_tramo)."""
    try:
        camino = nx.shortest_path(G, origen, destino, weight="tiempo")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    km = 0.0
    mn = 0.0
    tramos: list[str] = []
    for u, v in zip(camino[:-1], camino[1:]):
        datos = G[u][v]
        km += datos["distancia"]
        mn += datos["tiempo"]
        tramos.append(datos["id_tramo"])
    return km, mn, tramos


def reconstruir_rutas(
    paradas: pd.DataFrame,
    resumen: pd.DataFrame,
    puntos: list[Punto],
    mapeo: dict[str, int],
    G: nx.DiGraph,
) -> list[RutaViaje]:
    """Reconstruye la ruta de cada viaje sobre el grafo real."""
    por_punto = {p.id_punto: p for p in puntos}
    nodo_origen = mapeo[ORIGEN_ID]

    rutas: list[RutaViaje] = []
    for rv in resumen.itertuples(index=False):
        idv = int(rv.id_viaje)
        grupo = paradas[paradas["id_viaje"] == idv].sort_values("secuencia")

        ruta = RutaViaje(
            id_viaje=idv,
            codigo=str(rv.codigo_viaje),
            id_vehiculo=int(rv.id_vehiculo),
            n_paradas=len(grupo),
        )

        nodo_actual = nodo_origen
        lat_actual, lon_actual = ORIGEN_LAT, ORIGEN_LON
        km_acum = 0.0
        min_acum = 0.0

        for r in grupo.itertuples(index=False):
            idp = int(r.id_punto)
            p = por_punto.get(idp)
            if p is None or p.nodo_snap is None:
                ruta.error = f"punto {idp} sin nodo"
                break
            if p.fuera_cobertura:
                ruta.fuera_cobertura = True

            tramo = _ruta_entre(G, nodo_actual, p.nodo_snap)
            if tramo is None:
                ruta.error = f"sin camino a {p.codigo}"
                break
            km, mn, _ = tramo
            km_acum += km
            min_acum += mn

            # Distancia geodésica del tramo (para comparar con la red).
            km_geo = haversine_km(lat_actual, lon_actual, p.lat_geo, p.lon_geo)

            ruta.paradas.append(
                {
                    "id_viaje": idv,
                    "secuencia": int(r.secuencia),
                    "id_parada": int(r.id_parada),
                    "id_punto": idp,
                    "codigo_punto": p.codigo,
                    "nodo_snap": p.nodo_snap,
                    "dist_snap_m": round(p.dist_snap_m, 1),
                    "fuera_cobertura": p.fuera_cobertura,
                    "km_acum_red": round(km_acum, 3),
                    "min_acum_red": round(min_acum, 3),
                    "km_tramo_red": round(km, 3),
                    "km_tramo_geo": round(km_geo, 3),
                    "llegada_real": str(r.llegada_real),
                    "fin_descarga": str(r.fin_descarga),
                }
            )
            km_acum = km_acum  # noqa: PLW0127 - claridad
            nodo_actual = p.nodo_snap
            lat_actual, lon_actual = p.lat_geo, p.lon_geo

        ruta.km_red = round(km_acum, 3)
        ruta.min_red = round(min_acum, 3)
        ruta.km_geodesica = round(
            sum(par["km_tramo_geo"] for par in ruta.paradas), 3
        )
        rutas.append(ruta)
    return rutas


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


def exportar(
    puntos: list[Punto],
    rutas: list[RutaViaje],
    mapeo: dict[str, int],
    destino: Path,
) -> dict:
    """Escribe los tres CSV del benchmark y devuelve un resumen."""
    destino.mkdir(parents=True, exist_ok=True)

    puntos_df = pd.DataFrame(
        [
            {
                "id_punto": p.id_punto,
                "codigo": p.codigo,
                "nombre": p.nombre,
                "ciudad": p.ciudad,
                "departamento": p.departamento,
                "direccion": p.direccion,
                "lat_geo": p.lat_geo,
                "lon_geo": p.lon_geo,
                "fuente": p.fuente,
                "nodo_snap": p.nodo_snap,
                "dist_snap_m": round(p.dist_snap_m, 1),
                "fuera_cobertura": p.fuera_cobertura,
            }
            for p in puntos
        ]
    )
    puntos_df.to_csv(destino / "puntos_snap.csv", index=False)

    paradas_df = pd.DataFrame(
        [par for r in rutas for par in r.paradas]
    )
    paradas_df.to_csv(destino / "benchmark_paradas.csv", index=False)

    viajes_df = pd.DataFrame(
        [
            {
                "id_viaje": r.id_viaje,
                "codigo": r.codigo,
                "id_vehiculo": r.id_vehiculo,
                "n_paradas": r.n_paradas,
                "km_red": r.km_red,
                "min_red": r.min_red,
                "km_geodesica": r.km_geodesica,
                "fuera_cobertura": r.fuera_cobertura,
                "error": r.error,
            }
            for r in rutas
        ]
    )
    viajes_df.to_csv(destino / "benchmark_viajes.csv", index=False)

    validos = viajes_df[(~viajes_df["fuera_cobertura"]) & (viajes_df["error"] == "")]
    resumen = {
        "n_puntos": len(puntos_df),
        "n_puntos_fuera": int(puntos_df["fuera_cobertura"].sum()),
        "n_viajes": len(viajes_df),
        "n_viajes_validos": len(validos),
        "n_viajes_fuera": int(viajes_df["fuera_cobertura"].sum()),
        "n_viajes_error": int((viajes_df["error"] != "").sum()),
        "km_red_medio": round(float(validos["km_red"].mean()), 1) if len(validos) else 0.0,
        "km_geo_medio": round(float(validos["km_geodesica"].mean()), 1) if len(validos) else 0.0,
        "ratio_red_geo": round(
            float(validos["km_red"].sum() / validos["km_geodesica"].sum()), 3
        ) if len(validos) and validos["km_geodesica"].sum() > 0 else 0.0,
        "dist_snap_max_m": round(float(puntos_df["dist_snap_m"].max()), 1),
        "origen_nodo": mapeo[ORIGEN_ID],
        "origen_dist_m": mapeo["__origen_dist_m"],
        "fuentes": puntos_df["fuente"].value_counts().to_dict(),
    }
    (destino / "benchmark_resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resumen
