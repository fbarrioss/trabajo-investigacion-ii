"""Validación de la red vial real: esquema, conectividad, cobertura y calidad.

Se ejecuta automáticamente al final de ``scripts/01_red_vial.py`` y también
puede invocarse por separado. Cubre los 10 controles que el plan de tesis exige
para el producto verificable del OE1 más dos controles propios de la fuente OSM
(snap de ciudades y coherencia de distancias con el corredor).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import pandas as pd

from .topologia import Topologia

COLUMNAS_NODO = {
    "id_nodo", "lat", "lon", "grado", "es_decision", "tipo", "nombre", "ciudad_id",
}
COLUMNAS_TRAMO = {
    "id_tramo", "id_nodo_ini", "id_nodo_fin", "n_nodos_geometria",
    "distancia_km", "tiempo_flujo_min", "velocidad_flujo_kmh", "tipo_via", "peaje",
    "bidireccional", "tiene_alterna",
}

# Tolerancia de snapping ciudad → red (config: red_vial.tolerancia_snap_m).
TOLERANCIA_SNAP_M = 800.0


@dataclass
class ReporteValidacion:
    ok: bool = True
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    metricas: dict[str, float] = field(default_factory=dict)

    def resumen(self) -> str:
        estado = "OK" if self.ok else "FALLA"
        lineas = [f"Validación de red: {estado}"]
        for k, v in self.metricas.items():
            lineas.append(f"  - {k}: {v}")
        for e in self.errores:
            lineas.append(f"  [ERROR] {e}")
        for a in self.advertencias:
            lineas.append(f"  [AVISO] {a}")
        return "\n".join(lineas)


def validar_red(
    topo: Topologia,
    ciudades: pd.DataFrame | None = None,
    distribuidores: pd.DataFrame | None = None,
    distribuidores_declarados: int | None = None,
    hub_id: str = "LIM",
) -> ReporteValidacion:
    r = ReporteValidacion()
    nodos, tramos, grafo = topo.nodos, topo.tramos, topo.grafo

    # 1. Esquema mínimo ------------------------------------------------------
    faltan_n = COLUMNAS_NODO - set(nodos.columns)
    faltan_t = COLUMNAS_TRAMO - set(tramos.columns)
    if faltan_n:
        r.errores.append(f"Faltan columnas en nodos: {sorted(faltan_n)}")
    if faltan_t:
        r.errores.append(f"Faltan columnas en tramos: {sorted(faltan_t)}")

    # 2. Duplicados ----------------------------------------------------------
    if nodos["id_nodo"].duplicated().any():
        r.errores.append("Existen id_nodo duplicados.")
    if tramos["id_tramo"].duplicated().any():
        r.errores.append("Existen id_tramo duplicados.")

    # 3. Integridad referencial ---------------------------------------------
    ids_nodo = set(nodos["id_nodo"])
    extremos = set(tramos["id_nodo_ini"]) | set(tramos["id_nodo_fin"])
    huerfanos = extremos - ids_nodo
    if huerfanos:
        r.errores.append(
            f"{len(huerfanos)} tramos apuntan a nodos inexistentes en nodos.csv"
        )
    sin_tramo = ids_nodo - extremos
    if sin_tramo:
        r.errores.append(f"{len(sin_tramo)} nodos no participan en ningún tramo.")

    # 4. Nulos en atributos críticos ----------------------------------------
    for col in ["distancia_km", "tiempo_flujo_min", "velocidad_flujo_kmh", "tipo_via"]:
        nulos = int(tramos[col].isna().sum())
        if nulos:
            r.errores.append(f"{col} tiene {nulos} valores nulos.")

    # 5. Valores no positivos ------------------------------------------------
    if (tramos["distancia_km"] <= 0).any():
        r.errores.append("Hay tramos con distancia <= 0 km.")
    if (tramos["tiempo_flujo_min"] <= 0).any():
        r.errores.append("Hay tramos con tiempo <= 0 min.")
    if (tramos["velocidad_flujo_kmh"] <= 0).any():
        r.errores.append("Hay tramos con velocidad <= 0 km/h.")
    absurdos = int((tramos["velocidad_flujo_kmh"] > 140).sum())
    if absurdos:
        r.advertencias.append(f"{absurdos} tramos con velocidad > 140 km/h.")

    # 6. Conectividad --------------------------------------------------------
    conectado = nx.is_weakly_connected(grafo)
    if not conectado:
        comps = list(nx.weakly_connected_components(grafo))
        r.errores.append(f"El grafo no es débilmente conexo ({len(comps)} componentes).")

    # 7. Alcanzabilidad hub → distribuidores --------------------------------
    inalcanzables: list[str] = []
    if distribuidores is not None and ciudades is not None:
        sel = ciudades.loc[ciudades["id_ciudad"] == hub_id, "id_nodo"]
        if sel.empty:
            r.errores.append(f"No existe el hub {hub_id} en ciudades.csv")
        else:
            origen = sel.iloc[0]
            for d in distribuidores.itertuples(index=False):
                if d.id_nodo not in grafo or not nx.has_path(grafo, origen, d.id_nodo):
                    inalcanzables.append(d.id_distribuidor)
    if inalcanzables:
        r.errores.append(f"Distribuidores inalcanzables desde {hub_id}: {inalcanzables}")

    # 8. Cobertura declarada vs construida ----------------------------------
    n_dist = 0 if distribuidores is None else len(distribuidores)
    if distribuidores_declarados is not None and distribuidores_declarados != n_dist:
        r.errores.append(
            f"Distribuidores declarados ({distribuidores_declarados}) "
            f"≠ construidos ({n_dist})."
        )

    # 9. Nodos de decisión ---------------------------------------------------
    n_decision = int(nodos["es_decision"].sum())
    if n_decision == 0:
        r.errores.append("Ningún nodo permite decidir: no hay alternativas de ruta.")
    grados_bajos = int((nodos["grado"] < 2).sum())
    if grados_bajos:
        r.advertencias.append(f"{grados_bajos} nodos con grado < 2 (fondos de saco).")

    # 10. Completitud de atributos (H1 ≥ 90 %) ------------------------------
    celdas = tramos.shape[0] * tramos.shape[1]
    vacias = int(tramos.isna().sum().sum())
    completitud = 100.0 * (1 - vacias / max(celdas, 1))
    if completitud < 90.0:
        r.errores.append(f"Completitud {completitud:.1f} % < 90 % (umbral H1).")

    # 11. Snap de ciudades a la red -----------------------------------------
    if ciudades is not None and "distancia_snap_m" in ciudades.columns:
        fuera = ciudades[ciudades["distancia_snap_m"] > TOLERANCIA_SNAP_M]
        if not fuera.empty:
            r.errores.append(
                "Ciudades fuera de la tolerancia de snap "
                f"({TOLERANCIA_SNAP_M:.0f} m): "
                + ", ".join(f"{x.nombre} ({x.distancia_snap_m:.0f} m)"
                            for x in fuera.itertuples(index=False))
            )

    # 12. Coherencia de atributos derivados ---------------------------------
    ratio = (tramos["distancia_km"] / (tramos["tiempo_flujo_min"] / 60.0)).round(1)
    desvio = int(((ratio - tramos["velocidad_flujo_kmh"]).abs() > 0.5).sum())
    if desvio:
        r.errores.append(f"{desvio} tramos con velocidad incoherente (km / tiempo).")

    # 13. Cobertura de rutas alternas ---------------------------------------
    if "tiene_alterna" in tramos.columns:
        con_alterna = float(tramos["tiene_alterna"].astype(bool).mean() * 100.0)
        if con_alterna < 50.0:
            r.advertencias.append(
                f"Solo el {con_alterna:.1f} % de los tramos admite desvío: "
                "la acción 'tomar vía alterna' quedaría casi inutilizable."
            )
    else:
        con_alterna = 0.0
        r.advertencias.append("Falta la columna 'tiene_alterna'.")

    r.metricas = {
        "n_nodos": len(nodos),
        "n_tramos": len(tramos),
        "n_distribuidores": n_dist,
        "n_nodos_decision": n_decision,
        "km_totales": round(float(tramos["distancia_km"].sum()), 2),
        "completitud_pct": round(completitud, 2),
        "conectado": float(conectado),
        "tramos_con_alterna_pct": round(con_alterna, 2),
    }
    r.ok = not r.errores
    return r
