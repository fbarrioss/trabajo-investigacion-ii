"""Integra los viajes reales de la empresa con la red vial y genera el benchmark.

Uso:
    python scripts/06_integrar_viajes.py            # geocodifica y reconstruye
    python scripts/06_integrar_viajes.py --sin-red  # sin Nominatim (solo fallback)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.datos.integrar_viajes import (  # noqa: E402
    ORIGEN_ID,
    TOLERANCIA_SNAP_M,
    cargar_grafo,
    construir_puntos,
    exportar,
    geocodificar,
    hacer_snapping,
    leer_excel,
    reconstruir_rutas,
)

RAIZ = Path(__file__).resolve().parents[1]
DESCARGAS = Path("C:/Users/Franklin/Downloads")
RED = RAIZ / "data" / "processed" / "red_vial"
SALIDA = RAIZ / "data" / "processed" / "benchmark"
CACHE = RAIZ / "data" / "raw" / "geocoding_cache.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-red", action="store_true", help="No usar Nominatim")
    args = ap.parse_args()

    print("=" * 72)
    print("INTEGRACIÓN DE VIAJES REALES → RED VIAL (benchmark MARL)")
    print("=" * 72)

    print("\n[1/6] Leyendo Excel operativos…")
    cat, paradas, resumen = leer_excel(DESCARGAS)
    print(f"      catálogo: {len(cat)} puntos | paradas: {len(paradas)} | viajes: {len(resumen)}")

    print("\n[2/6] Construyendo catálogo de puntos…")
    puntos = construir_puntos(cat, paradas)
    for p in puntos[:3]:
        print(f"      {p.codigo} {p.nombre[:28]:28s} {p.ciudad}, {p.departamento}")

    print("\n[3/6] Geocodificando 25 puntos…")
    geocodificar(puntos, CACHE, usar_red=not args.sin_red)
    fuentes: dict[str, int] = {}
    for p in puntos:
        fuentes[p.fuente] = fuentes.get(p.fuente, 0) + 1
    print(f"      fuentes: {fuentes}")

    print("\n[4/6] Cargando red vial…")
    G, nodos = cargar_grafo(RED)
    print(f"      {G.number_of_nodes():,} nodos | {G.number_of_edges():,} aristas")

    print("\n[5/6] Haciendo snapping de 25 puntos + depósito…")
    mapeo = hacer_snapping(puntos, nodos, G)
    print(f"      depósito {ORIGEN_ID} → nodo {mapeo[ORIGEN_ID]} "
          f"({mapeo['__origen_dist_m']} m)")
    fuera = [p for p in puntos if p.fuera_cobertura]
    print(f"      puntos fuera de cobertura (>{TOLERANCIA_SNAP_M:.0f} m): {len(fuera)}")
    for p in fuera:
        print(f"        - {p.codigo} {p.ciudad:12s} {p.dist_snap_m:8.0f} m")

    print("\n[6/6] Reconstruyendo rutas de los 500 viajes…")
    rutas = reconstruir_rutas(paradas, resumen, puntos, mapeo, G)
    res = exportar(puntos, rutas, mapeo, SALIDA)

    print("\n" + "=" * 72)
    print("RESULTADO")
    print("=" * 72)
    print(f"  Puntos             : {res['n_puntos']} ({res['n_puntos_fuera']} fuera de cobertura)")
    print(f"  Viajes             : {res['n_viajes']}")
    print(f"    válidos          : {res['n_viajes_validos']}")
    print(f"    fuera cobertura  : {res['n_viajes_fuera']}")
    print(f"    con error        : {res['n_viajes_error']}")
    print(f"  Km medio (red)     : {res['km_red_medio']} km")
    print(f"  Km medio (geodés.) : {res['km_geo_medio']} km")
    print(f"  Ratio red/geodés.  : {res['ratio_red_geo']}")
    print(f"  Snap máx           : {res['dist_snap_max_m']} m")
    print(f"  Salida             : {SALIDA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
