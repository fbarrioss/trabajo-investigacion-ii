"""Paso 2 — Generación del dataset operativo y de eventos.

Uso:
    python scripts/02_dataset.py --viajes 180
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.comun.config import RAIZ, Escenario  # noqa: E402
from src.datos.esquema import esquema_completo  # noqa: E402
from src.datos.eventos import GeneradorEventos  # noqa: E402
from src.datos.generar_operacion import generar_dataset, guardar_dataset  # noqa: E402
from src.red_vial.topologia import cargar_topologia  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera el dataset operativo")
    ap.add_argument("--viajes", type=int, default=180)
    ap.add_argument("--salida", default=str(RAIZ / "data" / "processed" / "dataset"))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    esc = Escenario.cargar()
    dir_red = RAIZ / "data" / "processed" / "red_vial"
    if not (dir_red / "nodos.csv").exists():
        logging.error(
            "No existe la red vial en %s. Ejecuta primero: python scripts/01_red_vial.py",
            dir_red,
        )
        return 1
    red = cargar_topologia(dir_red)

    ds = generar_dataset(red, esc, n_viajes=args.viajes)
    rutas = guardar_dataset(ds, args.salida)

    print("Dataset operativo generado:")
    for nombre, n in ds.resumen().items():
        print(f"  {nombre:18s} {n:6d} filas")

    # Eventos base (línea base calibrada SUTRAN/MTC).
    gen = GeneradorEventos(esc, red.tramos, semilla=esc.semilla)
    eventos = gen.generar(horizonte_horas=24.0)
    ruta_ev = Path(args.salida) / "eventos_base.parquet"
    eventos.to_parquet(ruta_ev, index=False)
    print(f"  eventos_base         {len(eventos):6d} filas")
    print(
        f"\nTasa de afectación de tramos: {gen.tasa_afectacion():.2f} %"
        f"  (rutas afectadas: {gen.tasa_afectacion_rutas():.1f} %,"
        f" objetivo SUTRAN {gen.objetivo_rutas * 100:.1f} %)"
    )

    # Diccionario de datos.
    dicc = esquema_completo()
    ruta_dicc = RAIZ / "docs" / "diccionario_datos.json"
    ruta_dicc.parent.mkdir(parents=True, exist_ok=True)
    import json

    ruta_dicc.write_text(
        json.dumps(dicc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDiccionario de datos -> {ruta_dicc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
