"""Paso 9 — Rutas principales y alternativas sobre la red propia.

Valhalla (``scripts/08_rutas_valhalla.py``) resuelve la ruta principal de cada
par OD con perfil camión, pero devuelve alternativas en muy pocos pares: para un
camión pesado el corredor es casi un eje único. Este script cierra ese hueco
generando **K alternativas sobre el grafo consolidado**, es decir rutas que el
simulador puede ejecutar porque son secuencias de ``id_tramo`` propias.

Por qué sobre la red propia y no sobre el motor
-----------------------------------------------
Una traza de Valhalla es geometría, no una secuencia de tramos del simulador.
Para que una alternativa alimente el espacio de acciones tiene que ser
ejecutable paso a paso, y eso solo se garantiza si se calcula sobre el mismo
grafo que usa el entorno. Valhalla queda como referencia externa.

Reglas de factibilidad (perfil camión)
--------------------------------------
* **Bloqueo duro** si ``peso_max_t`` o ``alto_max_m`` están por debajo del
  camión: es imposible pasar, no caro.
* **Penalización** (``penalizacion_pesados``) en los tramos con ``hgv=no``: es
  una restricción legal, no física, y borrarlos fragmentaría la red donde no
  hay desvío legal.

Diversidad
----------
Yen devuelve caminos en orden de costo, y los primeros suelen diferir en un solo
tramo. Se descartan candidatos con solapamiento (Jaccard sobre tramos) mayor a
``--solapamiento`` o con más de ``--holgura`` veces los km de la mejor ruta.

Uso:
    python scripts/09_alternativas.py
    python scripts/09_alternativas.py --k 3 --peso-t 30 --solapamiento 0.7
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402

from src.comun.config import RAIZ, ConfigEntrenamiento, Escenario  # noqa: E402
from src.red_vial.topologia import cargar_topologia  # noqa: E402

RED = RAIZ / "data" / "processed" / "red_vial"
SALIDA = RAIZ / "data" / "processed" / "alternativas"


# ---------------------------------------------------------------------------
def _peso_camion(
    datos: dict, peso_t: float, alto_m: float, bloquear: bool = True
) -> float | None:
    """Costo de la arista, o ``None`` si el camión no puede pasar.

    ``peso_max_t`` y ``alto_max_m`` valen -1 cuando el tramo no declara límite.
    Con ``bloquear=False`` se ignora el límite físico y solo queda la
    penalización por ``hgv``; sirve para reproducir el comportamiento previo.
    """
    pmax = float(datos.get("peso_max_t", -1.0) or -1.0)
    amax = float(datos.get("alto_max_m", -1.0) or -1.0)
    if bloquear:
        if 0.0 < pmax < peso_t:
            return None
        if 0.0 < amax < alto_m:
            return None
    return float(datos.get("tiempo_flujo_min", 0.0))


def grafo_para_rutear(
    G: nx.MultiDiGraph,
    peso_t: float,
    alto_m: float,
    penal_restriccion: float,
    bloquear: bool = True,
) -> nx.DiGraph:
    """Colapsa el MultiDiGraph a un DiGraph con el mejor tramo por par de nodos.

    ``shortest_simple_paths`` no admite multigrafos, así que se conserva la
    arista de menor costo de cada par (u, v).
    """
    H = nx.DiGraph()
    bloqueados = 0
    for u, v, datos in G.edges(data=True):
        w = _peso_camion(datos, peso_t, alto_m, bloquear)
        if w is None:
            bloqueados += 1
            continue
        if bool(datos.get("restriccion_pesados", False)):
            w *= penal_restriccion
        previo = H[u][v]["w"] if H.has_edge(u, v) else None
        if previo is None or w < previo:
            H.add_edge(u, v, w=w, **datos)
    logging.info(
        "Grafo de ruteo: %d nodos, %d aristas (%d bloqueadas por peso/alto)",
        H.number_of_nodes(), H.number_of_edges(), bloqueados,
    )
    return H


def _metricas(camino: list, H: nx.DiGraph) -> dict:
    km = min_min = 0.0
    tramos: list[str] = []
    restringidos = 0
    vias: list[str] = []
    for u, v in zip(camino[:-1], camino[1:]):
        d = H[u][v]
        tramos.append(str(d.get("id_tramo", "")))
        km += float(d.get("distancia_km", 0.0))
        min_min += float(d.get("tiempo_flujo_min", 0.0))
        vias.append(str(d.get("tipo_via", "")))
        restringidos += int(bool(d.get("restriccion_pesados", False)))
    dominante = max(set(vias), key=vias.count) if vias else ""
    return {
        "km": round(km, 1),
        "min": round(min_min, 1),
        "n_tramos": len(tramos),
        "n_restringidos": restringidos,
        "tipo_via_dominante": dominante,
        "tramos": tramos,
    }


def _solapamiento(a: set[str], b: set[str]) -> float:
    """Jaccard sobre los ``id_tramo`` de dos rutas."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def alternativas(
    H: nx.DiGraph,
    origen: int,
    destino: int,
    k: int,
    solapamiento_max: float,
    holgura: float,
    factor: float = 2.5,
    max_intentos: int = 16,
) -> list[dict]:
    """K rutas razonablemente distintas, ordenadas de menor a mayor costo.

    Se usa **diversificación por penalización**, no Yen: se calcula la ruta
    mínima, se multiplica el costo de sus aristas y se vuelve a buscar. Con Yen
    el costo es O(K · n · (m + n log n)) y sobre 18 167 nodos no termina en
    tiempo razonable; con penalización son K Dijkstras. Es además la misma
    familia de heurísticas que usan los motores comerciales para `alternates`.

    Las rutas se reordenan por tiempo al final: el orden de descubrimiento sigue
    al costo *penalizado*, que no es el costo real, así que sin reordenar una
    alternativa de 282 km podía quedar antes que una de 214 km.
    """
    H2 = H.copy()
    rutas: list[dict] = []
    km_mejor: float | None = None

    for _ in range(max_intentos):
        try:
            camino = nx.shortest_path(H2, origen, destino, weight="w")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            break

        m = _metricas(camino, H)
        if km_mejor is None:
            km_mejor = m["km"]

        conjunto = set(m["tramos"])
        distinta = all(
            _solapamiento(conjunto, set(a["tramos"])) <= solapamiento_max
            for a in rutas
        )
        if distinta and m["km"] <= (km_mejor or m["km"]) * holgura:
            m["camino"] = camino
            rutas.append(m)

        # Penalizar lo recorrido para que la próxima búsqueda se separe.
        for u, v in zip(camino[:-1], camino[1:]):
            H2[u][v]["w"] = H2[u][v]["w"] * factor

    rutas.sort(key=lambda r: (r["min"], r["km"]))
    return rutas[:k]


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Rutas principales y alternativas")
    ap.add_argument("--k", type=int, default=3, help="alternativas por par OD")
    ap.add_argument("--peso-t", type=float, default=30.0)
    ap.add_argument("--alto-m", type=float, default=4.0)
    ap.add_argument("--solapamiento", type=float, default=0.7,
                    help="Jaccard máximo con una ruta ya aceptada")
    ap.add_argument("--holgura", type=float, default=1.35,
                    help="km máximos respecto de la mejor ruta")
    ap.add_argument("--sin-bloqueo", action="store_true",
                    help="ignora los límites físicos de peso/alto y deja solo "
                         "la penalización por hgv (comportamiento previo)")
    ap.add_argument("--salida", default=str(SALIDA))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    esc = Escenario.cargar()
    cfg = ConfigEntrenamiento.cargar()
    penal = float(cfg.crudo.get("entorno", {}).get("penalizacion_pesados", 5.0))

    red = cargar_topologia(RED)
    H = grafo_para_rutear(
        red.grafo, args.peso_t, args.alto_m, penal, bloquear=not args.sin_bloqueo
    )

    nodos = {r.id_ciudad: int(r.id_nodo) for r in red.ciudades.itertuples(index=False)}

    pares = [(o, d, "corredor") for o, d in esc.conexiones]
    pares += [(o, d, "extension") for o, d in esc.extensiones
              if (o, d) not in esc.conexiones]

    # Geometría real de cada tramo, para dibujar las rutas.
    geo_tramo: dict[str, list] = {}
    ruta_geo = RED / "red_vial.geojson"
    if ruta_geo.exists():
        with ruta_geo.open(encoding="utf-8") as fh:
            for feat in json.load(fh)["features"]:
                geo_tramo[feat["properties"]["id_tramo"]] = feat["geometry"]["coordinates"]

    # Ruta de referencia de Valhalla, si ya se ejecutó el paso 8.
    valhalla: dict[str, float] = {}
    ruta_val = RAIZ / "data" / "processed" / "valhalla" / "rutas_od.csv"
    if ruta_val.exists():
        v = pd.read_csv(ruta_val)
        valhalla = dict(zip(v["id_macro"], v["km_valhalla"]))

    filas: list[dict] = []
    features: list[dict] = []

    for origen_id, destino_id, alcance in pares:
        o, d = nodos.get(origen_id), nodos.get(destino_id)
        if o is None or d is None:
            logging.warning("Par %s→%s sin nodo", origen_id, destino_id)
            continue

        rutas = alternativas(
            H, o, d, args.k, args.solapamiento, args.holgura
        )
        if not rutas:
            logging.warning("%s→%s sin camino factible", origen_id, destino_id)
            continue

        id_macro = f"{origen_id}-{destino_id}"
        base = set(rutas[0]["tramos"])

        for rango, r in enumerate(rutas, start=1):
            filas.append(
                {
                    "id_macro": id_macro,
                    "alcance": alcance,
                    "rango": rango,
                    "km": r["km"],
                    "min": r["min"],
                    "n_tramos": r["n_tramos"],
                    "n_restringidos": r["n_restringidos"],
                    "tipo_via_dominante": r["tipo_via_dominante"],
                    "solapamiento_principal_pct": round(
                        100.0 * _solapamiento(set(r["tramos"]), base), 1
                    ),
                    "km_valhalla": valhalla.get(id_macro),
                    "secuencia_tramos": ";".join(r["tramos"]),
                }
            )

            coords: list = []
            for t in r["tramos"]:
                tramo = geo_tramo.get(t)
                if tramo:
                    coords.extend(tramo if not coords else tramo[1:])
            if coords:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": coords},
                        "properties": {
                            "id_macro": id_macro,
                            "rango": rango,
                            "km": r["km"],
                            "min": r["min"],
                            "perfil": f"propio {args.peso_t:.0f}t",
                        },
                    }
                )

        logging.info(
            "%s→%s  %d rutas: %s km",
            origen_id, destino_id, len(rutas),
            " / ".join(f"{x['km']:.1f}" for x in rutas),
        )

    destino = Path(args.salida)
    destino.mkdir(parents=True, exist_ok=True)
    tabla = pd.DataFrame(filas)
    tabla.to_csv(destino / "alternativas_od.csv", index=False, encoding="utf-8")
    (destino / "alternativas_od.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 74)
    print("  RUTAS PRINCIPALES Y ALTERNATIVAS — RED PROPIA")
    print("=" * 74)
    if not tabla.empty:
        vista = tabla[[
            "id_macro", "rango", "km", "min", "n_tramos", "n_restringidos",
            "solapamiento_principal_pct", "km_valhalla",
        ]]
        print(vista.to_string(index=False))
        n_pares = tabla["id_macro"].nunique()
        con_alt = int((tabla["rango"] > 1).sum())
        print(f"\n  {n_pares} pares OD · {con_alt} rutas alternativas "
              f"(además de la principal de cada par)")
    print(f"\n  Archivos en {destino}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
