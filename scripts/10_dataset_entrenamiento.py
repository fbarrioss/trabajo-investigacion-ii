"""Paso 10 — Dataset de entrenamiento: 50 % de tramos + alternativas Valhalla.

Genera un subconjunto reproducible del 50 % de los tramos de la red vial y, para
cada tramo seleccionado, consulta Valhalla (perfil ``truck``) por rutas
alternativas entre el nodo inicial y el nodo final del tramo.

Estrategia de seleccion
-----------------------
* **Estratificacion por zona** (LIM, TRU, CHI, PIU, HUY, SUL, PAI, CHU) para
  garantizar que cada zona este representada proporcionalmente.
* **Semilla fija** (20261016, la misma del escenario) para reproducibilidad.
* Se descartan los tramos sin ``tiene_alterna`` (1 328 tramos = 4.6 %), porque
  para ellos no tiene sentido buscar desvios: son aristas puente sin salida
  alternativa.
* Resultado esperado: ~13 657 tramos seleccionados (50 % de 27 314 factibles).

Para que sirve
--------------
El entorno ``EntornoRutasCarga`` necesita, para cada tramo por el que circula
el camion, saber si existe una ruta alternativa y cual es. Este dataset pre-
calcula esa informacion con el motor Valhalla, de modo que el bucle de
entrenamiento (QMIX/MAPPO) puede cargar las alternativas de O(1) en cada paso
en vez de consultar el motor en tiempo real.

Salidas (``data/processed/entrenamiento_tramos/``)
---------------------------------------------------
* ``tramos_seleccionados.csv``   — 50 % de tramos con todos sus atributos.
* ``alternativas_valhalla.csv``  — rutas principales y alternativas por tramo.
* ``alternativas_valhalla.json`` — mismo dato en JSON para carga rapida.
* ``alternativas_valhalla.geojson`` — geometrias decodificadas para inspeccion.
* ``resumen_dataset.json``       — metricas del dataset generado.

Uso:
    # Asegurar Valhalla corriendo:
    cd infra/valhalla && docker compose up -d
    python scripts/10_dataset_entrenamiento.py
    python scripts/10_dataset_entrenamiento.py --porcentaje 0.3 --alternativas 2
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from src.comun.config import RAIZ  # noqa: E402

RED = RAIZ / "data" / "processed" / "red_vial"
SALIDA = RAIZ / "data" / "processed" / "entrenamiento_tramos"
VALHALLA_URL = "http://localhost:8002"
SEED = 20261016

LOG = logging.getLogger("dataset_entrenamiento")


# ---------------------------------------------------------------------------
def _disponible(url: str, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f"{url}/status", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def decodificar_shape(shape: str, factor: float = 1e6) -> list[list[float]]:
    """Decodifica el *encoded polyline* de Valhalla a pares [lon, lat]."""
    coords: list[list[float]] = []
    lat = lon = 0
    i = 0
    n = len(shape)
    while i < n:
        for eje in (0, 1):
            desplazamiento = 0
            resultado = 0
            while True:
                b = ord(shape[i]) - 63
                i += 1
                resultado |= (b & 0x1F) << desplazamiento
                desplazamiento += 5
                if b < 0x20:
                    break
            delta = ~(resultado >> 1) if resultado & 1 else (resultado >> 1)
            if eje == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / factor, lat / factor])
    return coords


def consultar_valhalla(
    url: str,
    origen: tuple[float, float],
    destino: tuple[float, float],
    alternativas: int,
    peso_t: float,
    alto_m: float,
    ancho_m: float,
    largo_m: float,
    perfil: str = "truck",
    timeout: float = 60.0,
) -> dict | None:
    """Consulta Valhalla /route y devuelve el JSON completo, o None si falla."""
    cuerpo = {
        "locations": [
            {"lat": origen[0], "lon": origen[1], "type": "break"},
            {"lat": destino[0], "lon": destino[1], "type": "break"},
        ],
        "costing": perfil,
        "units": "kilometers",
        "directions_options": {"units": "kilometers"},
    }
    if perfil == "truck":
        cuerpo["costing_options"] = {
            "truck": {
                "height": alto_m,
                "width": ancho_m,
                "length": largo_m,
                "weight": peso_t,
                "axle_load": peso_t / 5.0,
                "hazmat": False,
            }
        }
    if alternativas > 0:
        cuerpo["alternates"] = alternativas
    try:
        r = requests.post(f"{url}/route", json=cuerpo, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        LOG.debug("Error Valhalla %s→%s: %s", origen, destino, exc)
        return None


def _resumen_trip(trip: dict) -> dict:
    s = trip.get("summary", {})
    leg = (trip.get("legs") or [{}])[0]
    return {
        "km": round(float(s.get("length", 0.0)), 3),
        "min": round(float(s.get("time", 0.0)) / 60.0, 2),
        "maniobras": int(leg.get("maneuvers") and len(leg["maneuvers"]) or 0),
        "shape": leg.get("shape", ""),
    }


# ---------------------------------------------------------------------------
def seleccionar_tramos(tramos: pd.DataFrame, porcentaje: float, seed: int) -> pd.DataFrame:
    """Seleccion estratificada por zona con semilla fija.

    Descarta los tramos sin ``tiene_alterna`` porque no tienen desvio posible.
    """
    factibles = tramos[tramos["tiene_alterna"] == True].copy()  # noqa: E712
    n_factibles = len(factibles)
    LOG.info(
        "Tramos totales: %d | con alterna: %d | sin alterna (descartados): %d",
        len(tramos), n_factibles, len(tramos) - n_factibles,
    )

    seleccionados_parts = []
    for zona, g in factibles.groupby("zona"):
        n_sel = int(len(g) * porcentaje)
        seleccionados_parts.append(g.sample(n=n_sel, random_state=seed))
    seleccionados = (
        pd.concat(seleccionados_parts)
        .sort_values("id_tramo")
        .reset_index(drop=True)
    )
    LOG.info(
        "Seleccionados %d tramos (%.1f %% de %d factibles)",
        len(seleccionados), porcentaje * 100, n_factibles,
    )
    return seleccionados


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Dataset de entrenamiento: 50 % tramos + alternativas Valhalla"
    )
    ap.add_argument("--url", default=VALHALLA_URL)
    ap.add_argument("--porcentaje", type=float, default=0.50,
                    help="fraccion de tramos a seleccionar (default 0.50)")
    ap.add_argument("--alternativas", type=int, default=2,
                    help="numero de alternativas a pedir a Valhalla (default 2)")
    ap.add_argument("--peso-t", type=float, default=36.0,
                    help="peso bruto del camion de referencia (t)")
    ap.add_argument("--alto-m", type=float, default=4.1)
    ap.add_argument("--ancho-m", type=float, default=2.6)
    ap.add_argument("--largo-m", type=float, default=18.0)
    ap.add_argument("--salida", default=str(SALIDA))
    ap.add_argument("--limite", type=int, default=0,
                    help="procesar solo los primeros N tramos (0 = todos, "
                         "util para pruebas rapidas)")
    ap.add_argument("--reintentos", type=int, default=2,
                    help="reintentos por tramo si Valhalla falla")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Verificar Valhalla ------------------------------------------------
    if not _disponible(args.url):
        LOG.error(
            "Valhalla no responde en %s. Levantar con:\n"
            "  cd infra/valhalla && docker compose up -d",
            args.url,
        )
        return 2

    # --- Cargar datos ------------------------------------------------------
    LOG.info("Cargando topologia desde %s", RED)
    tramos = pd.read_csv(RED / "tramos.csv", dtype={"id_tramo": str})
    nodos = pd.read_csv(RED / "nodos.csv")
    pos = {
        int(r.id_nodo): (float(r.lat), float(r.lon))
        for r in nodos.itertuples(index=False)
    }

    # --- Seleccionar 50 % --------------------------------------------------
    seleccion = seleccionar_tramos(tramos, args.porcentaje, SEED)

    if args.limite > 0:
        seleccion = seleccion.head(args.limite).copy()
        LOG.info("Limitado a %d tramos para prueba", len(seleccion))

    # --- Guardar tramos seleccionados --------------------------------------
    destino = Path(args.salida)
    destino.mkdir(parents=True, exist_ok=True)
    seleccion.to_csv(destino / "tramos_seleccionados.csv", index=False, encoding="utf-8")
    LOG.info("Guardado tramos_seleccionados.csv (%d filas)", len(seleccion))

    # --- Consultar Valhalla por cada tramo ---------------------------------
    filas: list[dict] = []
    features: list[dict] = []
    total = len(seleccion)
    ok = 0
    fallos = 0
    t0 = time.time()

    for idx, row in enumerate(seleccion.itertuples(index=False), start=1):
        id_tramo = row.id_tramo
        nodo_ini = int(row.id_nodo_ini)
        nodo_fin = int(row.id_nodo_fin)

        if nodo_ini not in pos or nodo_fin not in pos:
            LOG.warning("[%d/%d] %s: nodo no encontrado", idx, total, id_tramo)
            fallos += 1
            continue

        origen = pos[nodo_ini]
        destino_coord = pos[nodo_fin]

        # Para tramos muy cortos (<50m), Valhalla puede devolver error
        # porque origen y destino son el mismo punto. Se saltan.
        if row.distancia_km < 0.05:
            filas.append({
                "id_tramo": id_tramo,
                "rango": 0,
                "km_valhalla": round(row.distancia_km, 3),
                "min_valhalla": round(row.tiempo_flujo_min, 2),
                "n_alternativas": 0,
                "km_alternativas": "",
                "min_alternativas": "",
                "shape": "",
                "nota": "tramo muy corto, sin consulta",
            })
            ok += 1
            continue

        resp = None
        for intento in range(args.reintentos + 1):
            resp = consultar_valhalla(
                args.url, origen, destino_coord,
                args.alternativas, args.peso_t,
                args.alto_m, args.ancho_m, args.largo_m,
            )
            if resp is not None:
                break
            time.sleep(0.5 * (intento + 1))

        if resp is None:
            LOG.warning("[%d/%d] %s: Valhalla sin respuesta", idx, total, id_tramo)
            fallos += 1
            filas.append({
                "id_tramo": id_tramo,
                "rango": 0,
                "km_valhalla": None,
                "min_valhalla": None,
                "n_alternativas": 0,
                "km_alternativas": "",
                "min_alternativas": "",
                "shape": "",
                "nota": "sin respuesta de Valhalla",
            })
            continue

        principal = _resumen_trip(resp["trip"])
        alts = [_resumen_trip(a["trip"]) for a in resp.get("alternates", [])]

        # Fila principal (rango 1)
        filas.append({
            "id_tramo": id_tramo,
            "rango": 1,
            "km_valhalla": principal["km"],
            "min_valhalla": principal["min"],
            "n_alternativas": len(alts),
            "km_alternativas": ";".join(f"{a['km']:.3f}" for a in alts),
            "min_alternativas": ";".join(f"{a['min']:.2f}" for a in alts),
            "shape": principal["shape"],
            "nota": "",
        })

        # Geometria principal
        if principal["shape"]:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": decodificar_shape(principal["shape"]),
                },
                "properties": {
                    "id_tramo": id_tramo,
                    "rango": 1,
                    "km": principal["km"],
                    "min": principal["min"],
                    "perfil": "truck",
                },
            })

        # Filas alternativas (rango 2+)
        for i, alt in enumerate(alts, start=2):
            filas.append({
                "id_tramo": id_tramo,
                "rango": i,
                "km_valhalla": alt["km"],
                "min_valhalla": alt["min"],
                "n_alternativas": len(alts),
                "km_alternativas": "",
                "min_alternativas": "",
                "shape": alt["shape"],
                "nota": "",
            })
            if alt["shape"]:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": decodificar_shape(alt["shape"]),
                    },
                    "properties": {
                        "id_tramo": id_tramo,
                        "rango": i,
                        "km": alt["km"],
                        "min": alt["min"],
                        "perfil": "truck",
                    },
                })

        ok += 1
        if idx % 200 == 0 or idx == total:
            elapsed = time.time() - t0
            rate = idx / elapsed if elapsed > 0 else 0
            eta = (total - idx) / rate if rate > 0 else 0
            LOG.info(
                "[%d/%d] %s  ok=%d fallos=%d  (%.1f/s, ETA %.0fs)",
                idx, total, id_tramo, ok, fallos, rate, eta,
            )

    elapsed_total = time.time() - t0

    # --- Guardar resultados ------------------------------------------------
    tabla = pd.DataFrame(filas)
    tabla.to_csv(destino / "alternativas_valhalla.csv", index=False, encoding="utf-8")
    LOG.info("Guardado alternativas_valhalla.csv (%d filas)", len(tabla))

    (destino / "alternativas_valhalla.json").write_text(
        json.dumps(filas, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (destino / "alternativas_valhalla.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    # --- Resumen -----------------------------------------------------------
    n_tramos_seleccionados = len(seleccion)
    n_tramos_con_respuesta = tabla[tabla["rango"] == 1]["km_valhalla"].notna().sum()
    n_tramos_con_alterna = (
        tabla[(tabla["rango"] == 1) & (tabla["n_alternativas"] > 0)]["id_tramo"].nunique()
    )
    n_rutas_total = len(tabla)

    # Distribucion por zona
    dist_zona = (
        seleccion.groupby("zona").size().to_dict()
    )

    resumen = {
        "fecha_generacion": pd.Timestamp.now().isoformat(),
        "parametros": {
            "porcentaje": args.porcentaje,
            "alternativas_pedidas": args.alternativas,
            "peso_t": args.peso_t,
            "alto_m": args.alto_m,
            "ancho_m": args.ancho_m,
            "largo_m": args.largo_m,
            "semilla": SEED,
            "perfil": "truck",
            "url_valhalla": args.url,
        },
        "tramos_totales_red": int(len(tramos)),
        "tramos_factibles": int(len(tramos[tramos["tiene_alterna"] == True])),  # noqa: E712
        "tramos_seleccionados": int(n_tramos_seleccionados),
        "tramos_con_respuesta_valhalla": int(n_tramos_con_respuesta),
        "tramos_con_alternativa_real": int(n_tramos_con_alterna),
        "tramos_sin_respuesta": int(fallos),
        "rutas_total": int(n_rutas_total),
        "tiempo_segundos": round(elapsed_total, 1),
        "distribucion_por_zona": dist_zona,
    }
    (destino / "resumen_dataset.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- Reporte -----------------------------------------------------------
    print("\n" + "=" * 70)
    print("  DATASET DE ENTRENAMIENTO — TRAMOS + ALTERNATIVAS VALHALLA")
    print("=" * 70)
    print(f"  Tramos totales en la red:     {len(tramos):>8,}")
    print(f"  Tramos factibles (con alter.):{len(tramos[tramos['tiene_alterna'] == True]):>8,}")  # noqa: E712
    print(f"  Tramos seleccionados (50 %):  {n_tramos_seleccionados:>8,}")
    print(f"  Tramos con respuesta Valhalla:{n_tramos_con_respuesta:>8,}")
    print(f"  Tramos con alternativa real:  {n_tramos_con_alterna:>8,}")
    print(f"  Tramos sin respuesta:         {fallos:>8,}")
    print(f"  Total de rutas generadas:     {n_rutas_total:>8,}")
    print(f"  Tiempo total:                 {elapsed_total:>8.1f}s")
    print()
    print("  Distribucion por zona:")
    for zona, n in sorted(dist_zona.items(), key=lambda x: -x[1]):
        pct = 100.0 * n / n_tramos_seleccionados
        print(f"    {zona}: {n:>5} ({pct:5.1f} %)")
    print()
    print(f"  Archivos en {destino}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
