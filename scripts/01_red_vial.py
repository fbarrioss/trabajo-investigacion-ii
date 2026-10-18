"""Paso 1 — Red vial real y topología de nodos (tarea de Franklin).

Construye la topología operativa del corredor Lima – Chulucanas a partir de
OpenStreetMap:

    1. Descarga la red vial real (Overpass: corredor + red urbana de cada ciudad).
    2. Asocia cada ciudad a su nodo de red más cercano.
    3. Ubica los 17 distribuidores sobre nodos reales de la red.
    4. Consolida la topología: contrae cadenas de grado 2 y genera los tramos
       entre nodos de decisión.
    5. Exporta CSV, GraphML, GeoJSON y un mapa PNG.

Uso:
    python scripts/01_red_vial.py                  # descarga (usa caché si existe)
    python scripts/01_red_vial.py --solo-ciudades  # sin red urbana (más rápido)
    python scripts/01_red_vial.py --recalcular     # ignora la caché del grafo
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx  # noqa: E402
import osmnx as ox  # noqa: E402

from src.comun.config import RAIZ, Escenario  # noqa: E402
from src.red_vial.descargar_osm import descargar_todo  # noqa: E402
from src.red_vial.exportar import (  # noqa: E402
    exportar_geojson,
    guardar_nodos_logisticos,
    guardar_tablas,
    mapa_ciudades,
    mapa_red,
)
from src.red_vial.topologia import (  # noqa: E402
    construir_topologia,
    filtrar_red_carga,
    macro_tramos,
)
from src.red_vial.validar_red import validar_red  # noqa: E402

SALIDA = RAIZ / "data" / "processed" / "red_vial"
CACHE_GRAFO = RAIZ / "data" / "raw" / "osm" / "red_completa.graphml"


def _validar_cobertura(topo, ciudades, distribuidores, hub_id: str) -> list[str]:
    """Comprueba que los 17 distribuidores son alcanzables desde el hub."""
    import networkx as nx

    G = topo.grafo
    problemas: list[str] = []
    hub_nodo = ciudades.loc[ciudades["id_ciudad"] == hub_id, "id_nodo"]
    if hub_nodo.empty:
        return [f"No se encontró el hub {hub_id}"]
    origen = hub_nodo.iloc[0]
    if origen not in G:
        return [f"El nodo del hub {hub_id} ({origen}) no está en el grafo consolidado"]

    largos = nx.single_source_dijkstra_path_length(G, origen, weight="tiempo_flujo_min")
    for d in distribuidores.itertuples(index=False):
        if d.id_nodo not in largos:
            problemas.append(f"{d.id_distribuidor} ({d.ciudad}) NO alcanzable desde {hub_id}")
    return problemas


def _distancias_desde_hub(topo, ciudades, distribuidores, hub_id: str) -> list[tuple]:
    """Distancia por red desde el hub a cada distribuidor (km)."""
    import networkx as nx

    G = topo.grafo
    sel = ciudades.loc[ciudades["id_ciudad"] == hub_id, "id_nodo"]
    if sel.empty:
        return [(d.id_distribuidor, d.ciudad, None) for d in distribuidores.itertuples(index=False)]
    origen = sel.iloc[0]
    if origen not in G:
        return [(d.id_distribuidor, d.ciudad, None) for d in distribuidores.itertuples(index=False)]
    largos = nx.single_source_dijkstra_path_length(G, origen, weight="distancia_km")
    return [
        (d.id_distribuidor, d.ciudad, largos.get(d.id_nodo))
        for d in distribuidores.itertuples(index=False)
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Construye la red vial real del corredor")
    ap.add_argument("--solo-ciudades", action="store_true",
                    help="omite la red urbana de las ciudades")
    ap.add_argument("--recalcular", action="store_true",
                    help="ignora la caché del grafo compuesto")
    ap.add_argument("--nivel", default="tertiary",
                    help="jerarquía vial mínima para la red de carga "
                         "(motorway|trunk|primary|secondary|tertiary|residential)")
    ap.add_argument("--salida", default=str(SALIDA))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    t0 = time.time()

    esc = Escenario.cargar()
    for aviso in esc.validar():
        logging.warning(aviso)

    # --- 1. Grafo OSM --------------------------------------------------------
    if CACHE_GRAFO.exists() and not args.recalcular:
        logging.info("Cargando grafo OSM desde caché: %s", CACHE_GRAFO.name)
        G = ox.load_graphml(CACHE_GRAFO)
    else:
        G = descargar_todo(esc, incluir_urbano=not args.solo_ciudades)

    if not nx.is_weakly_connected(G):
        mayor = max(nx.weakly_connected_components(G), key=len)
        logging.warning(
            "Grafo no conexo: se conserva la componente mayor (%d de %d nodos).",
            len(mayor), G.number_of_nodes(),
        )
        G = G.subgraph(mayor).copy()

    logging.info("Grafo OSM: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())

    # --- 1b. Red de carga (solo vías aptas para camión) ----------------------
    G = filtrar_red_carga(G, nivel_minimo=args.nivel)
    if not nx.is_weakly_connected(G):
        mayor = max(nx.weakly_connected_components(G), key=len)
        logging.warning(
            "Red de carga no conexa: se conserva la componente mayor (%d de %d nodos).",
            len(mayor), G.number_of_nodes(),
        )
        G = G.subgraph(mayor).copy()
    logging.info("Red de carga: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())

    # --- 2-4. Topología ------------------------------------------------------
    topo, ciudades, distribuidores = construir_topologia(G, esc)

    # --- 4b. Validación de cobertura ----------------------------------------
    hub_id = next((c["id"] for c in esc.ciudades if c.get("rol") == "hub_origen"), "LIM")
    problemas = _validar_cobertura(topo, ciudades, distribuidores, hub_id)
    distancias = _distancias_desde_hub(topo, ciudades, distribuidores, hub_id)

    # --- 4c. Macro-tramos del corredor --------------------------------------
    macro = macro_tramos(topo, ciudades, esc)

    # --- 4d. Validación ------------------------------------------------------
    rep_val = validar_red(
        topo, ciudades, distribuidores,
        distribuidores_declarados=esc.total_distribuidores(),
        hub_id=hub_id,
    )

    # --- 5. Exportación ------------------------------------------------------
    destino = Path(args.salida)
    rutas = guardar_tablas(topo, destino)
    rutas.update(guardar_nodos_logisticos(ciudades, distribuidores, destino))
    rutas["macro"] = destino / "corredor_macro.csv"
    macro.to_csv(rutas["macro"], index=False, encoding="utf-8")
    rutas["geojson"] = exportar_geojson(topo, destino / "red_vial.geojson")
    rutas["mapa"] = mapa_red(topo, ciudades, distribuidores, destino / "mapa_red.png")
    rutas["mapa_urbano"] = mapa_ciudades(
        topo, ciudades, distribuidores, destino / "mapa_ciudades.png"
    )

    # --- Reporte -------------------------------------------------------------
    print("\n" + "=" * 62)
    print("  RED VIAL — CORREDOR LIMA – CHULUCANAS")
    print("=" * 62)
    r = topo.resumen()
    print(f"  Fuente           : {topo.metadatos.get('fuente')}")
    print(f"  Nodos            : {r['n_nodos']:,}")
    print(f"  Nodos de decisión: {r['n_decision']:,}")
    print(f"  Nodos OSM totales: {r.get('nodos_osm_totales', 0):,} "
          f"({r.get('nodos_geometria_contraidos', 0):,} de geometría contraídos)")
    print(f"  Tramos           : {r['n_tramos']:,}")
    print(f"  Longitud total   : {r['km_totales']:,.1f} km (suma de tramos)")
    print(f"  Distribuidores   : {r['n_distribuidores']}")
    print(f"  Conexo           : {'sí' if r['conectado'] else 'NO'}")

    print("\n  Distribuidores por ciudad:")
    for ciudad, n in distribuidores.groupby("ciudad").size().items():
        print(f"    {ciudad:<14} {n:>2}")

    print("\n  Tramos completos del corredor (macro-tramos):")
    for m in macro.itertuples(index=False):
        print(
            f"    {m.origen:<12} → {m.destino:<12} "
            f"{m.distancia_km:>7.1f} km  {m.tiempo_min/60:>5.1f} h  "
            f"{m.n_tramos:>4} tramos"
        )
    if not macro.empty:
        print(
            f"    {'TOTAL':<12}   {'':<12} "
            f"{macro.distancia_km.sum():>7.1f} km  "
            f"{macro.tiempo_min.sum()/60:>5.1f} h"
        )

    print(f"\n  Distancia por red desde {hub_id} (km):")
    for id_dist, ciudad, km in distancias:
        txt_km = f"{km:>8.1f}" if km is not None else "  NO ALC."
        print(f"    {id_dist:<10} {ciudad:<14} {txt_km}")

    if problemas:
        print("\n  AVISOS DE COBERTURA:")
        for p in problemas:
            print(f"    ! {p}")

    print("\n  Ciudades (snap a la red):")
    for c in ciudades.itertuples(index=False):
        print(f"    {c.nombre:<12} nodo {str(c.id_nodo):<12} desvío {c.distancia_snap_m:>6.1f} m")

    print("\n  Tipos de vía:")
    for tipo, n in topo.tramos.groupby("tipo_via").size().sort_values(ascending=False).items():
        print(f"    {tipo:<14} {n:>5} tramos")

    print("\n  Archivos generados:")
    for k, v in rutas.items():
        print(f"    {k:<14} -> {v}")

    print("\n  Validación:")
    for linea in rep_val.resumen().splitlines()[1:]:
        print(f"  {linea}")

    print(f"\n  Tiempo total: {time.time() - t0:.1f} s")
    print("=" * 62)

    (destino / "resumen.json").write_text(
        json.dumps(
            {
                **r,
                "nivel_vial_minimo": args.nivel,
                "hub": hub_id,
                "distribuidores_por_ciudad": distribuidores.groupby("ciudad").size().to_dict(),
                "distancias_desde_hub_km": {
                    id_dist: (round(km, 1) if km is not None else None)
                    for id_dist, _ciudad, km in distancias
                },
                "macro_tramos": macro.drop(columns=["secuencia_tramos"], errors="ignore").to_dict("records"),
                "avisos_cobertura": problemas,
                "validacion": {
                    "ok": rep_val.ok,
                    "errores": rep_val.errores,
                    "advertencias": rep_val.advertencias,
                    "metricas": rep_val.metricas,
                },
            },
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
