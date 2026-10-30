"""Paso 8 — Rutas de referencia y alternativas con Valhalla (perfil camión).

Consulta un servidor Valhalla local (``infra/valhalla/docker-compose.yml``) para
cada par origen–destino del escenario y guarda:

* la ruta principal que propone el motor con perfil ``truck``,
* hasta K alternativas reales (parámetro ``alternates``),
* el contraste contra la red propia (``corredor_macro.csv``), que es la que
  usa el simulador.

Para qué sirve
--------------
El motor **no** se usa dentro del bucle de aprendizaje: si eligiera la ruta en
cada paso, el agente dejaría de tener sentido. Se usa fuera, en tres papeles:

1. **Línea de base**: la ruta de referencia por OD contra la que se compara la
   política aprendida (lo que hoy falta en el bloque 2 del dashboard).
2. **Generación de candidatas**: las K alternativas definen el conjunto de
   caminos sobre los que el agente decide *cuándo* desviarse.
3. **Validación independiente**: el desvío porcentual contra la red propia es
   una prueba de que la topología construida desde OSM es razonable.

Uso:
    cd infra/valhalla && docker compose up -d
    python scripts/08_rutas_valhalla.py
    python scripts/08_rutas_valhalla.py --alternativas 3 --peso-t 36
    python scripts/08_rutas_valhalla.py --comparar-auto --sensibilidad-peso
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from src.comun.config import RAIZ, Escenario  # noqa: E402

RED = RAIZ / "data" / "processed" / "red_vial"
SALIDA = RAIZ / "data" / "processed" / "valhalla"
VALHALLA_URL = "http://localhost:8002"


# ---------------------------------------------------------------------------
def decodificar_shape(shape: str, factor: float = 1e6) -> list[tuple[float, float]]:
    """Decodifica el *encoded polyline* de Valhalla a pares (lon, lat).

    Valhalla usa precisión 1e-6 por defecto (``polyline6``); Google usa 1e-5.
    """
    coords: list[tuple[float, float]] = []
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
        coords.append((lon / factor, lat / factor))
    return coords


def _disponible(url: str, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f"{url}/status", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def consultar_ruta(
    url: str,
    origen: tuple[float, float],
    destino: tuple[float, float],
    alternativas: int,
    peso_t: float,
    alto_m: float,
    ancho_m: float,
    largo_m: float,
    perfil: str = "truck",
) -> dict:
    """Pide a Valhalla la ruta principal y sus alternativas.

    ``perfil="truck"`` aplica las restricciones de pesados; ``perfil="auto"``
    sirve de contraste para aislar cuánto del desvío se debe a esas
    restricciones y no a la topología.
    """
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
    r = requests.post(f"{url}/route", json=cuerpo, timeout=180)
    r.raise_for_status()
    return r.json()


def _resumen(trip: dict) -> dict:
    s = trip.get("summary", {})
    leg = (trip.get("legs") or [{}])[0]
    return {
        "km": round(float(s.get("length", 0.0)), 1),
        "min": round(float(s.get("time", 0.0)) / 60.0, 1),
        "maniobras": int(leg.get("maneuvers") and len(leg["maneuvers"]) or 0),
        "shape": leg.get("shape", ""),
    }


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Rutas de referencia con Valhalla")
    ap.add_argument("--url", default=VALHALLA_URL)
    ap.add_argument("--alternativas", type=int, default=2)
    ap.add_argument("--peso-t", type=float, default=36.0,
                    help="peso bruto del camión de referencia (t)")
    ap.add_argument("--alto-m", type=float, default=4.1)
    ap.add_argument("--ancho-m", type=float, default=2.6)
    ap.add_argument("--largo-m", type=float, default=18.0)
    ap.add_argument("--salida", default=str(SALIDA))
    ap.add_argument(
        "--comparar-auto", action="store_true",
        help="consulta también el perfil auto para aislar el efecto de las "
             "restricciones de pesados",
    )
    ap.add_argument(
        "--sensibilidad-peso", action="store_true",
        help="barre el peso bruto para localizar el umbral que rompe el corredor",
    )
    ap.add_argument(
        "--pesos", default="30,36,37,40,42,48",
        help="pesos a probar en --sensibilidad-peso (t, separados por coma)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if not _disponible(args.url):
        logging.error(
            "Valhalla no responde en %s. Levántalo con:\n"
            "  cd infra/valhalla && docker compose up -d",
            args.url,
        )
        return 2

    esc = Escenario.cargar()
    ciudades = pd.read_csv(RED / "ciudades.csv")
    pos = {
        r.id_ciudad: (float(r.lat), float(r.lon))
        for r in ciudades.itertuples(index=False)
    }

    macro = pd.DataFrame()
    ruta_macro = RED / "corredor_macro.csv"
    if ruta_macro.exists():
        macro = pd.read_csv(ruta_macro)
    km_red = (
        dict(zip(macro["id_macro"], macro["distancia_km"])) if not macro.empty else {}
    )

    pares = [(o, d, "corredor") for o, d in esc.conexiones]
    pares += [(o, d, "extension") for o, d in esc.extensiones if (o, d) not in esc.conexiones]

    filas: list[dict] = []
    geo: list[dict] = []

    for origen_id, destino_id, alcance in pares:
        if origen_id not in pos or destino_id not in pos:
            logging.warning("Par %s→%s sin coordenadas", origen_id, destino_id)
            continue
        try:
            resp = consultar_ruta(
                args.url, pos[origen_id], pos[destino_id],
                args.alternativas, args.peso_t, args.alto_m, args.ancho_m, args.largo_m,
            )
        except Exception as exc:  # noqa: BLE001
            logging.error("%s→%s: %s", origen_id, destino_id, exc)
            continue

        principal = _resumen(resp["trip"])
        alts = [_resumen(a["trip"]) for a in resp.get("alternates", [])]
        id_macro = f"{origen_id}-{destino_id}"
        km_propio = km_red.get(id_macro)

        fila = {
            "id_macro": id_macro,
            "alcance": alcance,
            "km_valhalla": principal["km"],
            "min_valhalla": principal["min"],
            "km_red_propia": km_propio,
            "desvio_pct": (
                round(100.0 * (principal["km"] / km_propio - 1.0), 2)
                if km_propio else None
            ),
            "n_alternativas": len(alts),
            "km_alternativas": ";".join(f"{a['km']:.1f}" for a in alts),
            "min_alternativas": ";".join(f"{a['min']:.1f}" for a in alts),
        }

        if args.comparar_auto:
            try:
                auto = _resumen(
                    consultar_ruta(
                        args.url, pos[origen_id], pos[destino_id], 0,
                        args.peso_t, args.alto_m, args.ancho_m, args.largo_m,
                        perfil="auto",
                    )["trip"]
                )
                fila["km_auto"] = auto["km"]
                fila["desvio_auto_pct"] = (
                    round(100.0 * (auto["km"] / km_propio - 1.0), 2)
                    if km_propio else None
                )
                fila["sobrecosto_restriccion_km"] = round(principal["km"] - auto["km"], 1)
            except Exception as exc:  # noqa: BLE001
                logging.warning("%s→%s perfil auto: %s", origen_id, destino_id, exc)

        filas.append(fila)

        for etiqueta, r in [("principal", principal)] + [
            (f"alterna_{i}", a) for i, a in enumerate(alts, start=1)
        ]:
            if not r["shape"]:
                continue
            geo.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": decodificar_shape(r["shape"]),
                    },
                    "properties": {
                        "id_macro": id_macro,
                        "ruta": etiqueta,
                        "km": r["km"],
                        "min": r["min"],
                        "perfil": "truck",
                    },
                }
            )
        logging.info(
            "%s→%s  %.1f km  %.1f min  (%d alternativas)",
            origen_id, destino_id, principal["km"], principal["min"], len(alts),
        )

    # --- Sensibilidad al peso bruto -----------------------------------------
    # El corredor tiene tramos con maxweight=36 t en la Panamericana Norte. Al
    # superarlos el motor no puede usar la ruta directa y rodea cientos de km.
    sens: list[dict] = []
    if args.sensibilidad_peso:
        pesos = [float(x) for x in args.pesos.split(",") if x.strip()]
        logging.info("Barriendo pesos %s t sobre %d pares", pesos, len(pares))
        for origen_id, destino_id, _ in pares:
            if origen_id not in pos or destino_id not in pos:
                continue
            for w in pesos:
                try:
                    r = _resumen(
                        consultar_ruta(
                            args.url, pos[origen_id], pos[destino_id], 0,
                            w, args.alto_m, args.ancho_m, args.largo_m,
                        )["trip"]
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.error("%s→%s %s t: %s", origen_id, destino_id, w, exc)
                    continue
                sens.append(
                    {
                        "id_macro": f"{origen_id}-{destino_id}",
                        "peso_t": w,
                        "km": r["km"],
                        "min": r["min"],
                    }
                )

    destino = Path(args.salida)
    destino.mkdir(parents=True, exist_ok=True)
    tabla = pd.DataFrame(filas)
    tabla.to_csv(destino / "rutas_od.csv", index=False, encoding="utf-8")
    (destino / "rutas_od.json").write_text(
        json.dumps(
            {
                "perfil": "truck",
                "parametros": {
                    "peso_t": args.peso_t, "alto_m": args.alto_m,
                    "ancho_m": args.ancho_m, "largo_m": args.largo_m,
                    "alternativas": args.alternativas,
                },
                "rutas": filas,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (destino / "rutas_od.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": geo}, ensure_ascii=False),
        encoding="utf-8",
    )
    if sens:
        pd.DataFrame(sens).to_csv(
            destino / "sensibilidad_peso.csv", index=False, encoding="utf-8"
        )

    print("\n" + "=" * 68)
    print("  RUTAS DE REFERENCIA — VALHALLA (perfil truck)")
    print("=" * 68)
    if not tabla.empty:
        print(tabla.to_string(index=False))
        validos = tabla["desvio_pct"].dropna()
        if len(validos):
            print(
                f"\n  Desvío medio frente a la red propia: {validos.mean():+.2f} % "
                f"(máx {validos.abs().max():.2f} %)"
            )
        if "km_auto" in tabla.columns:
            auto = tabla["desvio_auto_pct"].dropna()
            if len(auto):
                print(
                    f"  Desvío del perfil auto (sin restricciones): {auto.mean():+.2f} % "
                    f"→ el sobrecosto de pesados es de "
                    f"{tabla['sobrecosto_restriccion_km'].sum():+.1f} km en el corredor"
                )
    if sens:
        piv = (
            pd.DataFrame(sens)
            .pivot(index="id_macro", columns="peso_t", values="km")
            .round(1)
        )
        print("\n  SENSIBILIDAD AL PESO BRUTO (km)")
        print(piv.to_string())
        print(
            "\n  Un salto entre dos columnas consecutivas marca el peso a partir "
            "del cual el corredor directo deja de ser transitable."
        )
    print(f"\n  Archivos en {destino}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
