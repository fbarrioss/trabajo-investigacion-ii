"""Corta del extracto OSM de Perú las zonas de descarga del proyecto.

Por qué existe
--------------
La descarga vía Overpass funciona, pero depende de servidores públicos que se
saturan: en la sesión del 27/10/2026 el endpoint principal respondía «All query
workers are busy» (HTTP 502) y los espejos alternos entraban en tiempo de espera
agotado. Para las zonas de extensión se usa en su lugar el **extracto oficial de
Geofabrik** (``peru-latest.osm.pbf``, ~257 MB), que se descarga una sola vez y se
recorta localmente.

El resultado se escribe con el mismo nombre y formato que produce
``src.red_vial.descargar_osm`` (``central_b01.osm``, ``ciudad_HUY.osm``, …), de
modo que el resto del pipeline (OSMnx → topología) no cambia.

Uso:
    python scripts/00_cortar_extracto.py                # zonas de extensión
    python scripts/00_cortar_extracto.py --todas        # todas las zonas
    python scripts/00_cortar_extracto.py --forzar       # reescribe lo existente
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import osmium  # noqa: E402

from src.comun.config import RAIZ, Escenario  # noqa: E402
from src.red_vial.descargar_osm import (  # noqa: E402
    RADIO_CIUDAD_M,
    RADIO_CIUDAD_M_LIMA,
    TIPOS_URBANOS,
    zonas_descarga,
)

PBF = RAIZ / "data" / "raw" / "pbf" / "peru-latest.osm.pbf"
CACHE = RAIZ / "data" / "raw" / "osm"


@dataclass
class Objetivo:
    """Un archivo .osm a producir: recuadro + tipos de vía admitidos."""

    nombre: str
    bbox: tuple[float, float, float, float]
    tipos: frozenset[str]
    n_vias: int = 0
    n_nodos: int = 0


def _bbox_ciudad(lat: float, lon: float, radio_m: int) -> tuple[float, float, float, float]:
    dlat = radio_m / 111_320.0
    dlon = radio_m / (111_320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def _objetivos(esc: Escenario, todas: bool) -> list[Objetivo]:
    """Lista de archivos a generar (zonas de extensión + sus redes urbanas)."""
    objetivos: list[Objetivo] = []

    extensiones = {"central", "paita", "acceso"}
    for zona in zonas_descarga():
        if not todas and zona.etiqueta not in extensiones:
            continue
        tipos = frozenset(zona.tipos)
        for i, bbox in enumerate(zona.recuadros, start=1):
            objetivos.append(
                Objetivo(f"{zona.etiqueta}_b{i:02d}.osm", bbox, tipos)
            )

    # Red urbana de las zonas de extensión: el recuadro es el cuadrado
    # circunscrito al círculo que usa `descargar_ciudad`.
    for ciudad in esc.ciudades:
        if not ciudad.get("red_urbana"):
            continue
        dist = RADIO_CIUDAD_M_LIMA if ciudad["id"] == "LIM" else RADIO_CIUDAD_M
        objetivos.append(
            Objetivo(
                f"ciudad_{ciudad['id']}.osm",
                _bbox_ciudad(ciudad["lat"], ciudad["lon"], dist),
                frozenset(TIPOS_URBANOS),
            )
        )
    return objetivos


class Cortador(osmium.SimpleHandler):
    """Recorre el PBF una sola vez y reparte cada vía entre los objetivos."""

    def __init__(self, objetivos: list[Objetivo]) -> None:
        super().__init__()
        self.objetivos = objetivos
        self.todos_los_tipos = frozenset().union(*(o.tipos for o in objetivos))
        self.nodos: list[dict[int, tuple[float, float]]] = [dict() for _ in objetivos]
        self.vias: list[list[tuple[int, dict[str, str], list[int]]]] = [
            [] for _ in objetivos
        ]

    def way(self, w: osmium.osm.Way) -> None:  # type: ignore[override]
        hw = w.tags.get("highway")
        if hw is None or hw not in self.todos_los_tipos:
            return
        try:
            coords = [(n.ref, n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            return  # vía que toca el borde del extracto: sin geometría completa
        if len(coords) < 2:
            return

        lat_min = min(c[1] for c in coords)
        lat_max = max(c[1] for c in coords)
        lon_min = min(c[2] for c in coords)
        lon_max = max(c[2] for c in coords)
        etiquetas = {t.k: t.v for t in w.tags}

        for k, obj in enumerate(self.objetivos):
            if hw not in obj.tipos:
                continue
            s, o, n, e = obj.bbox
            if lat_max < s or lat_min > n or lon_max < o or lon_min > e:
                continue
            destino_nodos = self.nodos[k]
            for ref, lat, lon in coords:
                if ref not in destino_nodos:
                    destino_nodos[ref] = (lat, lon)
            self.vias[k].append((w.id, etiquetas, [c[0] for c in coords]))
            obj.n_vias += 1


def _escribir(obj: Objetivo, nodos: dict[int, tuple[float, float]],
              vias: list[tuple[int, dict[str, str], list[int]]], ruta: Path) -> None:
    """Escribe el .osm XML equivalente al que devolvería Overpass."""
    obj.n_nodos = len(nodos)
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write('<osm version="0.6" generator="corte-local-geofabrik">\n')
        for ref, (lat, lon) in nodos.items():
            fh.write(f'  <node id="{ref}" lat="{lat:.7f}" lon="{lon:.7f}"/>\n')
        for wid, etiquetas, refs in vias:
            fh.write(f'  <way id="{wid}">\n')
            for ref in refs:
                fh.write(f'    <nd ref="{ref}"/>\n')
            for k, v in etiquetas.items():
                fh.write(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>\n")
            fh.write("  </way>\n")
        fh.write("</osm>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Corta zonas del extracto OSM de Perú")
    ap.add_argument("--todas", action="store_true",
                    help="incluye también el corredor norte (ya cacheado de Overpass)")
    ap.add_argument("--forzar", action="store_true",
                    help="reescribe los .osm aunque ya existan")
    args = ap.parse_args()

    if not PBF.exists():
        print(f"No existe el extracto: {PBF}")
        print("Descárgalo con:")
        print("  curl -L -o data/raw/pbf/peru-latest.osm.pbf "
              "https://download.geofabrik.de/south-america/peru-latest.osm.pbf")
        return 1

    esc = Escenario.cargar()
    objetivos = _objetivos(esc, args.todas)
    pendientes = [
        o for o in objetivos
        if args.forzar or not ((CACHE / o.nombre).exists()
                               and (CACHE / o.nombre).stat().st_size > 10_000)
    ]
    print(f"Extracto : {PBF.name} ({PBF.stat().st_size / 1e6:.0f} MB)")
    print(f"Objetivos: {len(objetivos)} ({len(pendientes)} por generar)")
    if not pendientes:
        print("Nada que hacer: todos los .osm ya existen.")
        return 0

    t0 = time.time()
    print("\nRecorriendo el extracto (una sola pasada)…")
    cortador = Cortador(pendientes)
    cortador.apply_file(str(PBF), locations=True)
    print(f"  lectura completa en {time.time() - t0:.0f} s")

    CACHE.mkdir(parents=True, exist_ok=True)
    print("\nEscribiendo archivos:")
    for k, obj in enumerate(pendientes):
        ruta = CACHE / obj.nombre
        _escribir(obj, cortador.nodos[k], cortador.vias[k], ruta)
        print(
            f"  {obj.nombre:<22} {obj.n_vias:>6} vías  {obj.n_nodos:>7} nodos  "
            f"{ruta.stat().st_size / 1e6:>6.2f} MB"
        )

    print(f"\nTotal: {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
