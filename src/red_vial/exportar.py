"""Exportación de la red vial a formatos de intercambio y visualización."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib
import networkx as nx
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .topologia import Topologia  # noqa: E402

log = logging.getLogger(__name__)

COLORES_VIA = {
    "motorway": "#b2182b",
    "trunk": "#d6604d",
    "primary": "#f4a582",
    "secondary": "#92c5de",
    "tertiary": "#4393c3",
    "residential": "#bbbbbb",
    "unclassified": "#cccccc",
}


def guardar_tablas(topo: Topologia, destino: str | Path) -> dict[str, Path]:
    """Exporta nodos, tramos y grafo consolidado."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)

    rutas = {
        "nodos": destino / "nodos.csv",
        "tramos": destino / "tramos.csv",
        "grafo": destino / "topologia.graphml",
        "metadatos": destino / "metadatos.json",
    }
    topo.nodos.to_csv(rutas["nodos"], index=False, encoding="utf-8")
    topo.tramos.drop(columns=["camino_nodos"], errors="ignore").to_csv(
        rutas["tramos"], index=False, encoding="utf-8"
    )
    nx.write_graphml(topo.grafo, rutas["grafo"])
    rutas["metadatos"].write_text(
        json.dumps(topo.resumen(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return rutas


def guardar_nodos_logisticos(
    ciudades: pd.DataFrame, distribuidores: pd.DataFrame, destino: str | Path
) -> dict[str, Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    r_ciudades = destino / "ciudades.csv"
    r_dist = destino / "distribuidores.csv"
    ciudades.to_csv(r_ciudades, index=False, encoding="utf-8")
    distribuidores.to_csv(r_dist, index=False, encoding="utf-8")
    return {"ciudades": r_ciudades, "distribuidores": r_dist}


def _coordenadas_str(topo: Topologia) -> dict[str, tuple[float, float]]:
    """Coordenadas indexadas por texto.

    ``camino_nodos`` guarda los IDs como texto (para poder serializar la
    secuencia), mientras que ``Topologia.coordenadas`` los indexa por el ID
    original. Se normaliza a texto para que ambos coincidan.
    """
    base = topo.coordenadas or {
        r.id_nodo: (float(r.lon), float(r.lat))
        for r in topo.nodos.itertuples(index=False)
    }
    return {str(k): v for k, v in base.items()}


def exportar_geojson(topo: Topologia, destino: str | Path) -> Path:
    """Exporta los tramos consolidados como LineString GeoJSON."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    coords = _coordenadas_str(topo)

    features = []
    for t in topo.tramos.itertuples(index=False):
        camino = str(getattr(t, "camino_nodos", "")).split(";")
        puntos = [coords[n] for n in camino if n in coords]
        if len(puntos) < 2:
            puntos = [
                p for p in (
                    coords.get(str(t.id_nodo_ini)), coords.get(str(t.id_nodo_fin))
                ) if p
            ]
        if len(puntos) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": puntos},
                "properties": {
                    "id_tramo": t.id_tramo,
                    "distancia_km": t.distancia_km,
                    "tiempo_flujo_min": t.tiempo_flujo_min,
                    "tipo_via": t.tipo_via,
                    "peaje": bool(t.peaje),
                },
            }
        )

    coleccion = {"type": "FeatureCollection", "features": features}
    destino.write_text(json.dumps(coleccion, ensure_ascii=False), encoding="utf-8")
    log.info("GeoJSON exportado: %d tramos", len(features))
    return destino


def mapa_red(
    topo: Topologia,
    ciudades: pd.DataFrame,
    distribuidores: pd.DataFrame,
    destino: str | Path,
) -> Path:
    """Genera un mapa PNG de la red con nodos logísticos resaltados."""
    from matplotlib.collections import LineCollection

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    coords = _coordenadas_str(topo)

    fig, ax = plt.subplots(figsize=(11, 13), dpi=150)
    ax.set_facecolor("#f7f7f5")

    # Un LineCollection por jerarquía de vía (mucho más rápido que plot()).
    for tipo, color in COLORES_VIA.items():
        subset = topo.tramos.loc[topo.tramos["tipo_via"] == tipo]
        segmentos = []
        for t in subset.itertuples(index=False):
            camino = str(getattr(t, "camino_nodos", "")).split(";")
            pts = [coords[n] for n in camino if n in coords]
            if len(pts) >= 2:
                segmentos.append(pts)
        if not segmentos:
            continue
        ancho = 1.5 if tipo in {"motorway", "trunk"} else 1.0 if tipo == "primary" else 0.6
        ax.add_collection(
            LineCollection(
                segmentos, colors=color, linewidths=ancho, alpha=0.85, zorder=1
            )
        )

    ax.scatter(
        distribuidores["lon"], distribuidores["lat"],
        s=34, c="#1f78b4", marker="o", edgecolors="white", linewidths=0.7,
        zorder=4, label=f"Distribuidores ({len(distribuidores)})",
    )
    ax.scatter(
        ciudades["lon"], ciudades["lat"],
        s=220, c="#e31a1c", marker="*", edgecolors="white", linewidths=0.9,
        zorder=5, label="Ciudades / hub",
    )
    for c in ciudades.itertuples(index=False):
        ax.annotate(
            c.nombre, (c.lon, c.lat), textcoords="offset points",
            xytext=(8, 6), fontsize=8, fontweight="bold", color="#222",
            zorder=6,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.75),
        )

    ax.autoscale_view()
    ax.set_title(
        "Red vial del corredor Lima – Chulucanas (topología consolidada)",
        fontsize=13, fontweight="bold", pad=14,
    )
    ax.set_xlabel("Longitud")
    ax.set_ylabel("Latitud")
    ax.legend(loc="lower left", frameon=True, fontsize=9)
    ax.grid(True, linestyle=":", linewidth=0.4, alpha=0.5)
    ax.set_aspect(1 / 0.98)  # corrección de latitud (~5°–12° S)

    fig.tight_layout()
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    log.info("Mapa guardado en %s", destino)
    return destino


def mapa_ciudades(
    topo: Topologia,
    ciudades: pd.DataFrame,
    distribuidores: pd.DataFrame,
    destino: str | Path,
    radio_grados: float = 0.12,
) -> Path:
    """Detalle urbano por ciudad con la ubicación de cada distribuidor.

    Permite verificar visualmente que los 17 distribuidores están sobre nodos
    reales de la red y no en coordenadas arbitrarias.
    """
    from matplotlib.collections import LineCollection

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    coords = _coordenadas_str(topo)

    segmentos: list[tuple[float, list]] = []
    for t in topo.tramos.itertuples(index=False):
        camino = str(getattr(t, "camino_nodos", "")).split(";")
        pts = [coords[n] for n in camino if n in coords]
        if len(pts) >= 2:
            segmentos.append((float(t.distancia_km), pts, str(t.tipo_via)))

    n = len(ciudades)
    ncol = 3
    nrow = max(1, (n + ncol - 1) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 4.6 * nrow), dpi=130)
    axes = np.atleast_1d(axes).ravel()

    for ax, c in zip(axes, ciudades.itertuples(index=False)):
        ax.set_facecolor("#f7f7f5")
        segs = []
        cols = []
        for _km, pts, tipo in segmentos:
            if any(
                abs(p[0] - c.lon) <= radio_grados and abs(p[1] - c.lat) <= radio_grados
                for p in pts
            ):
                segs.append(pts)
                cols.append(COLORES_VIA.get(tipo, "#999999"))
        if segs:
            ax.add_collection(
                LineCollection(segs, colors=cols, linewidths=0.9, alpha=0.9, zorder=1)
            )
        sub = distribuidores[distribuidores["ciudad_id"] == c.id_ciudad]
        ax.scatter(
            sub["lon"], sub["lat"], s=70, c="#1f78b4", marker="o",
            edgecolors="white", linewidths=0.9, zorder=4,
            label=f"{len(sub)} distribuidores",
        )
        for d in sub.itertuples(index=False):
            ax.annotate(
                d.id_distribuidor, (d.lon, d.lat), textcoords="offset points",
                xytext=(6, 4), fontsize=7, color="#123", zorder=5,
            )
        ax.scatter([c.lon], [c.lat], s=180, c="#e31a1c", marker="*",
                   edgecolors="white", linewidths=0.9, zorder=5, label="Centro urbano")
        ax.set_xlim(c.lon - radio_grados, c.lon + radio_grados)
        ax.set_ylim(c.lat - radio_grados * 0.75, c.lat + radio_grados * 0.75)
        ax.set_title(f"{c.nombre} ({c.id_ciudad})", fontsize=10, fontweight="bold")
        ax.tick_params(labelsize=6)
        ax.grid(True, linestyle=":", linewidth=0.4, alpha=0.5)
        ax.legend(loc="lower left", fontsize=6, frameon=True)

    for ax in axes[len(ciudades):]:
        ax.axis("off")

    fig.suptitle(
        "Ubicación de los 17 distribuidores sobre la red vial real (OSM)",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    log.info("Mapa urbano guardado en %s", destino)
    return destino
