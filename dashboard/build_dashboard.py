"""Genera el dashboard HTML autocontenido de la red vial.

Lee la topología exportada por `scripts/01_red_vial.py`, simplifica las
geometrías (~3 m de tolerancia, imperceptible a escala de mapa) y embebe todo
en un único `index.html` para que se pueda abrir sin servidor.

Uso:
    python dashboard/build_dashboard.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from shapely.geometry import LineString

RAIZ = Path(__file__).resolve().parents[1]
RED = RAIZ / "data" / "processed" / "red_vial"
BENCH = RAIZ / "data" / "processed" / "benchmark"
DATASET = RAIZ / "data" / "processed" / "dataset"
AQUI = Path(__file__).resolve().parent

# Simplificación de geometría: 0.00003° ≈ 3.3 m. Invisible a zoom de mapa y
# reduce los puntos al 38 %.
TOLERANCIA = 0.00003


def _constantes_entorno() -> dict:
    """Lee DIM_OBS / N_ACCIONES / ACCIONES del propio entorno.

    Se leen del código en lugar de duplicarlos aquí para que la pestaña
    «Modelo» no quede desactualizada si cambia el espacio de acciones.
    """
    fuente = (RAIZ / "src" / "entorno" / "entorno_rutas.py").read_text(encoding="utf-8")
    dim = int(re.search(r"^DIM_OBS = (\d+)", fuente, re.M).group(1))
    n = int(re.search(r"^N_ACCIONES = (\d+)", fuente, re.M).group(1))
    acciones = [
        {"id": int(i), "clave": c}
        for i, c in re.findall(r"^    (\d+): \"([a-z_]+)\",", fuente, re.M)
    ]
    return {"dim_obs": dim, "n_acciones": n, "acciones": acciones}


def _parametros_entorno() -> dict:
    """Parámetros del entorno desde `config/entrenamiento.yaml`."""
    try:
        from src.comun.config import ConfigEntrenamiento

        ent = ConfigEntrenamiento.cargar()["entorno"]
        return {
            "dt_min": float(ent["duracion_paso_min"]),
            "horizonte_pasos": int(ent["horizonte_pasos"]),
            "n_agentes": int(ent["n_agentes"]),
            "max_replan": int(ent["max_replanificaciones"]),
            "descanso_cada_h": float(ent.get("descanso_cada_h", 5.0)),
            "descanso_min": float(ent.get("descanso_min", 45)),
        }
    except Exception as exc:  # pragma: no cover - el dashboard no debe romperse
        print(f"  [aviso] no se pudo leer la configuración del entorno: {exc}")
        return {}


def _resumen_benchmark() -> dict:
    """Cifras de la reconstrucción de los 500 viajes reales."""
    ruta = BENCH / "benchmark_resumen.json"
    if not ruta.exists():
        return {}
    resumen = json.loads(ruta.read_text(encoding="utf-8"))
    viajes_csv = BENCH / "benchmark_viajes.csv"
    if viajes_csv.exists():
        viajes = pd.read_csv(viajes_csv)
        resumen["duracion_media_min"] = round(float(viajes["min_red"].mean()), 1)
        resumen["n_paradas"] = int(
            len(pd.read_csv(BENCH / "benchmark_paradas.csv"))
            if (BENCH / "benchmark_paradas.csv").exists()
            else 0
        )
    return resumen


def _calibracion_eventos(n_tramos: int) -> dict:
    """Tramos afectados por eventos en el dataset sintético (dato medido)."""
    ruta = DATASET / "eventos_base.parquet"
    if not ruta.exists():
        return {}
    ev = pd.read_parquet(ruta, columns=["id_tramo", "tipo_evento"])
    afectados = int(ev["id_tramo"].nunique())
    por_tipo = ev.groupby("tipo_evento")["id_tramo"].nunique().sort_values(ascending=False)
    return {
        "n_eventos": int(len(ev)),
        "tramos_afectados": afectados,
        "pct_tramos": round(100.0 * afectados / max(n_tramos, 1), 2),
        "por_tipo": [{"tipo": str(k), "n": int(v)} for k, v in por_tipo.items()],
        "objetivo_rutas_pct": 27.6,
    }


def _coords_planas(coords: list[list[float]]) -> list[float]:
    """Convierte [[lon,lat],...] en [lon,lat,lon,lat,...] redondeado a 5 dec."""
    if len(coords) > 2:
        linea = LineString(coords).simplify(TOLERANCIA, preserve_topology=False)
        coords = [list(c) for c in linea.coords]
    plano: list[float] = []
    for lon, lat in coords:
        plano.append(round(lon, 5))
        plano.append(round(lat, 5))
    return plano


def main() -> int:
    geo = json.loads((RED / "red_vial.geojson").read_text(encoding="utf-8"))
    tramos = pd.read_csv(RED / "tramos.csv")
    ciudades = pd.read_csv(RED / "ciudades.csv")
    distribuidores = pd.read_csv(RED / "distribuidores.csv")
    macro = pd.read_csv(RED / "corredor_macro.csv")
    resumen = json.loads((RED / "resumen.json").read_text(encoding="utf-8"))

    # Índices de atributos categóricos para no repetir cadenas 28 312 veces.
    tipos = sorted(tramos["tipo_via"].unique().tolist())
    idx_tipo = {t: i for i, t in enumerate(tipos)}

    nombres = sorted({str(n) for n in tramos["nombre_via"].dropna().unique()})
    idx_nombre = {n: i for i, n in enumerate(nombres)}

    zonas = sorted(tramos["zona"].dropna().unique().tolist())
    idx_zona = {z: i for i, z in enumerate(zonas)}

    # El GeoJSON conserva el orden de tramos.csv.
    por_id = {str(r.id_tramo): i for i, r in enumerate(tramos.itertuples(index=False))}

    lista_tramos = []
    for feature in geo["features"]:
        props = feature["properties"]
        fila = tramos.iloc[por_id[props["id_tramo"]]]
        nombre = fila["nombre_via"]
        lista_tramos.append(
            {
                "i": props["id_tramo"],
                "t": idx_tipo[str(props["tipo_via"])],
                "d": round(float(props["distancia_km"]), 3),
                "m": round(float(props["tiempo_flujo_min"]), 4),
                "p": 1 if props["peaje"] else 0,
                "a": 1 if bool(fila["tiene_alterna"]) else 0,
                "z": idx_zona[str(fila["zona"])],
                "n": idx_nombre[str(nombre)] if pd.notna(nombre) else -1,
                "c": _coords_planas(feature["geometry"]["coordinates"]),
            }
        )

    # Macro-tramos: se traduce la secuencia de IDs a índices de la lista.
    macro_json = []
    for r in macro.itertuples(index=False):
        secuencia = [por_id[t] for t in str(r.secuencia_tramos).split(";") if t in por_id]
        macro_json.append(
            {
                "id": r.id_macro,
                "origen": r.origen,
                "destino": r.destino,
                "alcance": getattr(r, "alcance", "corredor"),
                "km": round(float(r.distancia_km), 1),
                "min": round(float(r.tiempo_min), 1),
                "n": int(r.n_tramos),
                "tipo": r.tipo_via_dominante,
                "sec": secuencia,
            }
        )

    # Bounding box real de la red (mejor encuadre que una caja fija).
    lons = [t["c"][k] for t in lista_tramos for k in range(0, len(t["c"]), 2)]
    lats = [t["c"][k] for t in lista_tramos for k in range(1, len(t["c"]), 2)]
    bbox = [[min(lats), min(lons)], [max(lats), max(lons)]]

    # --- pestaña «Modelo»: los tres puntos del asesor ------------------------
    dist = tramos["distancia_km"].astype(float)
    metricas = resumen.get("validacion", {}).get("metricas", {})
    modelo = {
        "entorno": _parametros_entorno(),
        "acciones": _constantes_entorno(),
        "benchmark": _resumen_benchmark(),
        "eventos": _calibracion_eventos(int(resumen["n_tramos"])),
        "tramo": {
            "km_medio": round(float(dist.mean()), 3),
            "km_mediana": round(float(dist.median()), 3),
            "km_min": round(float(dist.min()), 3),
            "km_max": round(float(dist.max()), 3),
            "pct_menor_100m": round(100.0 * float((dist < 0.1).mean()), 1),
        },
        "red": {
            "n_nodos": int(resumen["n_nodos"]),
            "n_tramos": int(resumen["n_tramos"]),
            "km": round(float(resumen["km_totales"]), 1),
            "alterna_pct": float(metricas.get("tramos_con_alterna_pct", 0.0)),
        },
    }

    datos = {
        "meta": {
            "n_nodos": int(resumen["n_nodos"]),
            "n_tramos": int(resumen["n_tramos"]),
            "km": round(float(resumen["km_totales"]), 1),
            "n_decision": int(resumen["n_decision"]),
            "n_distribuidores": int(resumen["n_distribuidores"]),
            "n_ciudades": int(len(ciudades)),
            "n_zonas_extension": int((ciudades["rol"] == "zona_extension").sum()),
            "nodos_osm": int(resumen["nodos_osm_totales"]),
            "nodos_contraidos": int(resumen["nodos_geometria_contraidos"]),
            "conectado": bool(resumen["conectado"]),
            "fuente": resumen["fuente"],
            "bbox": bbox,
            "validacion": resumen.get("validacion", {}),
            "distancias_hub": resumen.get("distancias_desde_hub_km", {}),
        },
        "tipos": tipos,
        "zonas": zonas,
        "nombres": nombres,
        "tramos": lista_tramos,
        "ciudades": ciudades.to_dict(orient="records"),
        "distribuidores": distribuidores.to_dict(orient="records"),
        "macro": macro_json,
        "modelo": modelo,
    }

    plantilla = (AQUI / "plantilla.html").read_text(encoding="utf-8")
    carga = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    html = plantilla.replace("__DATOS__", carga)
    salida = AQUI / "index.html"
    salida.write_text(html, encoding="utf-8")

    print(f"Dashboard -> {salida}")
    print(f"  tramos embebidos : {len(lista_tramos):,}")
    print(f"  peso del HTML    : {salida.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
