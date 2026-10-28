"""Mide qué parte del universo de tramos recorre la muestra de viajes reales.

La empresa no entrega el histórico completo: entrega un extracto. Este script
cuantifica ese extracto para poder declararlo como marco muestral en la tesis,
en lugar de asumirlo como si fuera la operación completa.

Salida:
    data/processed/benchmark/cobertura_muestra.json

Uso:
    python scripts/07_cobertura_muestra.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx
import pandas as pd

from src.datos.integrar_viajes import cargar_grafo

RAIZ = Path(__file__).resolve().parents[1]
RED = RAIZ / "data" / "processed" / "red_vial"
BENCH = RAIZ / "data" / "processed" / "benchmark"


def _rutas(G: nx.DiGraph, origen: int, destinos: list[int]) -> list[str]:
    """Secuencia de id_tramo del recorrido origen → destinos en orden."""
    tramos: list[str] = []
    actual = origen
    for destino in destinos:
        if actual == destino:
            continue
        try:
            camino = nx.shortest_path(G, actual, destino, weight="tiempo")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return tramos
        for u, v in zip(camino[:-1], camino[1:]):
            tramos.append(str(G[u][v]["id_tramo"]))
        actual = destino
    return tramos


def main() -> int:
    G, _nodos = cargar_grafo(RED)
    tramos = pd.read_csv(RED / "tramos.csv")
    resumen = json.loads((BENCH / "benchmark_resumen.json").read_text(encoding="utf-8"))
    paradas = pd.read_csv(BENCH / "benchmark_paradas.csv")
    viajes = pd.read_csv(BENCH / "benchmark_viajes.csv")

    origen = int(resumen["origen_nodo"])
    por_tramo = tramos.set_index("id_tramo")

    usados: dict[str, int] = {}
    km_por_viaje: list[float] = []
    for rv in viajes.itertuples(index=False):
        grupo = paradas[paradas["id_viaje"] == rv.id_viaje].sort_values("secuencia")
        destinos = [int(n) for n in grupo["nodo_snap"].tolist()]
        secuencia = _rutas(G, origen, destinos)
        km = sum(float(por_tramo.loc[t, "distancia_km"]) for t in secuencia if t in por_tramo.index)
        km_por_viaje.append(km)
        for t in secuencia:
            usados[t] = usados.get(t, 0) + 1

    n_universo = len(tramos)
    km_universo = float(tramos["distancia_km"].sum())
    km_usados = float(por_tramo.loc[[t for t in usados if t in por_tramo.index], "distancia_km"].sum())

    # Reparto por jerarquía: dentro y fuera de la muestra.
    serie_usados = por_tramo.loc[[t for t in usados if t in por_tramo.index]]
    por_tipo_total = tramos.groupby("tipo_via").size().to_dict()
    por_tipo_usado = serie_usados.groupby("tipo_via").size().to_dict()

    fechas = pd.to_datetime(paradas["llegada_real"])
    vehiculos = int(viajes["id_vehiculo"].nunique())

    salida = {
        "muestra": {
            "n_viajes": int(len(viajes)),
            "n_paradas": int(len(paradas)),
            "n_puntos": int(paradas["id_punto"].nunique()),
            "n_vehiculos": vehiculos,
            "fecha_inicio": str(fechas.min()),
            "fecha_fin": str(fechas.max()),
            "dias_con_viajes": int(fechas.dt.date.nunique()),
            "viajes_por_dia_medio": round(
                len(viajes) / max(fechas.dt.date.nunique(), 1), 2
            ),
            "paradas_por_viaje_medio": round(float(viajes["n_paradas"].mean()), 2),
            "viajes_por_vehiculo_medio": round(len(viajes) / max(vehiculos, 1), 1),
        },
        "universo": {
            "n_tramos": n_universo,
            "km": round(km_universo, 1),
            "tramos_usados": int(len(usados)),
            "pct_tramos_usados": round(100.0 * len(usados) / n_universo, 2),
            "km_usados": round(km_usados, 1),
            "pct_km_usados": round(100.0 * km_usados / km_universo, 2),
            "tramos_repetidos_media": round(
                sum(usados.values()) / max(len(usados), 1), 2
            ),
            "por_tipo": [
                {
                    "tipo": k,
                    "total": int(v),
                    "usados": int(por_tipo_usado.get(k, 0)),
                    "pct": round(100.0 * por_tipo_usado.get(k, 0) / max(v, 1), 1),
                }
                for k, v in sorted(por_tipo_total.items(), key=lambda x: -x[1])
            ],
        },
        "verificacion": {
            "km_medio_recalculado": round(sum(km_por_viaje) / max(len(km_por_viaje), 1), 1),
            "km_medio_benchmark": resumen.get("km_red_medio"),
        },
    }

    destino = BENCH / "cobertura_muestra.json"
    destino.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    m, u = salida["muestra"], salida["universo"]
    print("=" * 62)
    print("  COBERTURA DE LA MUESTRA SOBRE EL UNIVERSO DE TRAMOS")
    print("=" * 62)
    print(f"  Viajes           : {m['n_viajes']:,}  ({m['n_paradas']:,} paradas)")
    print(f"  Ventana          : {m['fecha_inicio'][:10]} → {m['fecha_fin'][:10]}"
          f"  ({m['dias_con_viajes']} días con viajes)")
    print(f"  Vehículos        : {m['n_vehiculos']}")
    print(f"  Tramos usados    : {u['tramos_usados']:,} de {u['n_tramos']:,}"
          f"  ({u['pct_tramos_usados']:.1f} %)")
    print(f"  Km usados        : {u['km_usados']:,.1f} de {u['km']:,.1f}"
          f"  ({u['pct_km_usados']:.1f} %)")
    print(f"  Uso medio        : {u['tramos_repetidos_media']:.1f} viajes por tramo usado")
    print(f"  Control km medio : recalculado {salida['verificacion']['km_medio_recalculado']}"
          f" vs benchmark {salida['verificacion']['km_medio_benchmark']}")
    print("\n  Cobertura por jerarquía:")
    for t in u["por_tipo"]:
        print(f"    {t['tipo']:<14} {t['usados']:>6,} / {t['total']:>6,}   {t['pct']:>5.1f} %")
    print(f"\n  -> {destino}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
