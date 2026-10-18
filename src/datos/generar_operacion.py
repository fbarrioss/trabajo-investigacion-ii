"""Generación del dataset operativo maestro (entidades de Wilson + demanda).

Produce las tablas que alimentan al entorno de simulación y que constituyen
el "dataset estructurado" del OE1:

    conductores, vehiculos, jornadas, puntos_descanso, clientes, cargas,
    viajes, detalle_carga, gastos_plan, eventos_base

Todas las identificaciones son sintéticas (anonimización declarada en el
plan de tesis, sección 3.6).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import networkx as nx
import pandas as pd

from ..comun.config import Escenario
from ..red_vial.construir_red import RedVial
from .esquema import (
    Carga,
    Cliente,
    Conductor,
    DetalleCarga,
    GastoViaje,
    JornadaConductor,
    PuntoDescanso,
    Vehiculo,
    Viaje,
)

LICENCIAS = ["A-II", "A-IIIa", "A-IIIb"]
TIPOS_CARGA = [
    ("Mercadería farmacéutica", 0.18, False),
    ("Alimentos y productos de consumo", 0.30, True),
    ("Carga comercial mixta", 0.24, False),
    ("Insumos industriales", 0.16, False),
    ("Materiales de construcción", 0.12, False),
]


@dataclass
class DatasetOperativo:
    conductores: pd.DataFrame
    vehiculos: pd.DataFrame
    jornadas: pd.DataFrame
    puntos_descanso: pd.DataFrame
    clientes: pd.DataFrame
    cargas: pd.DataFrame
    viajes: pd.DataFrame
    detalle_carga: pd.DataFrame
    gastos_plan: pd.DataFrame

    def tablas(self) -> dict[str, pd.DataFrame]:
        return {
            "conductores": self.conductores,
            "vehiculos": self.vehiculos,
            "jornadas": self.jornadas,
            "puntos_descanso": self.puntos_descanso,
            "clientes": self.clientes,
            "cargas": self.cargas,
            "viajes": self.viajes,
            "detalle_carga": self.detalle_carga,
            "gastos_plan": self.gastos_plan,
        }

    def resumen(self) -> dict[str, int]:
        return {k: int(len(v)) for k, v in self.tablas().items()}


# ---------------------------------------------------------------------------
def _ruta_entre(red: RedVial, origen: str, destino: str) -> list[str]:
    """Ruta de menor tiempo entre dos nodos (Dijkstra sobre el grafo)."""
    try:
        return nx.shortest_path(
            red.grafo, origen, destino, weight="tiempo_flujo_min"
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def _metricas_ruta(red: RedVial, camino: list[str]) -> tuple[float, float]:
    """Devuelve (distancia_km, tiempo_min) de un camino de nodos."""
    km = 0.0
    minutos = 0.0
    for a, b in zip(camino[:-1], camino[1:]):
        mejor = None
        for _, datos in red.grafo[a][b].items():
            if mejor is None or datos["tiempo_flujo_min"] < mejor["tiempo_flujo_min"]:
                mejor = datos
        if mejor:
            km += float(mejor["distancia_km"])
            minutos += float(mejor["tiempo_flujo_min"])
    return km, minutos


def _ordenar_por_vecino_mas_cercano(
    nodos: pd.DataFrame, ids: list[str], inicio: str
) -> list[str]:
    """Secuencia de entrega por vecino más cercano (heurística de secuenciación)."""
    idx = nodos.set_index("id_nodo")
    pendientes = list(ids)
    secuencia: list[str] = []
    actual = inicio
    while pendientes:
        lat0, lon0 = idx.loc[actual, "lat"], idx.loc[actual, "lon"]
        pendientes.sort(
            key=lambda n: (idx.loc[n, "lat"] - lat0) ** 2 + (idx.loc[n, "lon"] - lon0) ** 2
        )
        actual = pendientes.pop(0)
        secuencia.append(actual)
    return secuencia


# ---------------------------------------------------------------------------
def generar_dataset(
    red: RedVial,
    esc: Escenario | None = None,
    n_viajes: int | None = None,
    fecha_base: str | None = None,
) -> DatasetOperativo:
    """Genera el dataset operativo completo del escenario."""
    esc = esc or Escenario.cargar()
    rng = random.Random(esc.semilla)
    costos = esc.costos
    flota_cfg = esc.flota

    n_viajes = n_viajes or max(int(flota_cfg["n_vehiculos"]) * 30, 30)
    fecha = datetime.fromisoformat(fecha_base) if fecha_base else datetime(2026, 10, 16)

    # --- 1. Conductores ------------------------------------------------------
    n_cond = max(flota_cfg["n_vehiculos"] * 2, 4)
    conductores = [
        Conductor(
            id_conductor=f"CON-{i:03d}",
            licencia_categoria=rng.choices(LICENCIAS, weights=[0.2, 0.5, 0.3])[0],
            anios_experiencia=rng.randint(1, 22),
            hora_inicio_jornada=round(rng.uniform(4, 8), 2),
            horas_max_conduccion=10.0,
            descanso_obligatorio_min=rng.choice([30, 45, 60]),
            zona_base=rng.choice([c["id"] for c in esc.ciudades]),
        )
        for i in range(1, n_cond + 1)
    ]

    # --- 2. Vehículos --------------------------------------------------------
    tipos = flota_cfg["tipos"]
    vehiculos = []
    for i in range(1, flota_cfg["n_vehiculos"] + 1):
        t = rng.choices(tipos, weights=[x["peso"] for x in tipos])[0]
        vehiculos.append(
            Vehiculo(
                id_vehiculo=f"VEH-{i:03d}",
                tipo_vehiculo=t["nombre"],
                capacidad_t=float(t["capacidad_t"]),
                capacidad_m3=round(t["capacidad_t"] * 2.6, 1),
                velocidad_max_kmh=float(t["velocidad_max_kmh"]),
                consumo_km_l=float(t["consumo_km_l"]),
                anio_fabricacion=rng.randint(2014, 2025),
                estado="operativo",
            )
        )

    # --- 3. Jornadas ---------------------------------------------------------
    jornadas = []
    for i, v in enumerate(vehiculos, start=1):
        c = conductores[(i - 1) % len(conductores)]
        inicio = c.hora_inicio_jornada
        jornadas.append(
            JornadaConductor(
                id_jornada=f"JOR-{i:03d}",
                id_conductor=c.id_conductor,
                id_vehiculo=v.id_vehiculo,
                fecha=fecha.date().isoformat(),
                hora_inicio=inicio,
                hora_fin=round(min(inicio + 12.0, 23.99), 2),
                horas_conduccion=round(min(10.0, rng.uniform(6, 10)), 2),
                horas_descanso=round(rng.uniform(0.75, 2.0), 2),
                cumple_regulacion=True,
            )
        )

    # --- 4. Puntos de descanso (grifos/paraderos sobre el corredor) ---------
    # Se usan los puntos de paso reales de la PE-1N (Ancón, Huacho, Casma,
    # Chimbote, Paiján, Pacasmayo, Motupe, Olmos…), cada uno ajustado al nodo
    # más cercano de la red. Antes se buscaban nodos de tipo "intermedio", que
    # ya no existen porque la topología se construye con datos OSM.
    from ..red_vial.corredores import CORREDOR_PE1N

    nodos_red = red.nodos
    lat_r = nodos_red["lat"].to_numpy(dtype=float)
    lon_r = nodos_red["lon"].to_numpy(dtype=float)
    idx_r = nodos_red.index.to_numpy()

    puntos = []
    vistos: set[int] = set()
    for p in CORREDOR_PE1N:
        d = (lat_r - p.lat) ** 2 + ((lon_r - p.lon) * 0.98) ** 2
        j = int(d.argmin())
        i_fila = int(idx_r[j])
        if i_fila in vistos:
            continue
        vistos.add(i_fila)
        puntos.append(
            PuntoDescanso(
                id_punto=f"PD-{len(puntos) + 1:03d}",
                nombre=f"Paradero {p.nombre}",
                lat=float(lat_r[j]),
                lon=float(lon_r[j]),
                km_corredor=0.0,
                tiene_servicios=bool(rng.random() > 0.2),
            )
        )

    # --- 5. Clientes (uno por distribuidor) ---------------------------------
    # Las ventanas representan el horario de recepción del cliente. Se usa un
    # rango amplio (06:00-23:00) porque los viajes del corredor norte toman
    # entre 9 h y 20 h: la llegada temprana es admisible (el vehículo espera).
    clientes = []
    for i, (_, d) in enumerate(red.distribuidores.iterrows(), start=1):
        ini = float(rng.choice([6, 7, 8]))
        clientes.append(
            Cliente(
                id_cliente=f"CLI-{i:03d}",
                id_nodo_entrega=d["id_nodo"],
                ciudad=d["ciudad_id"],
                ventana_inicio=ini,
                ventana_fin=float(rng.choice([20, 21, 22, 23])),
                prioridad=rng.choices([1, 2, 3, 4, 5], weights=[0.1, 0.2, 0.4, 0.2, 0.1])[0],
            )
        )

    # --- 6. Cargas -----------------------------------------------------------
    cargas = []
    for i in range(1, n_viajes * 3 + 1):
        nombre, _, refrig = rng.choices(TIPOS_CARGA, weights=[x[1] for x in TIPOS_CARGA])[0]
        cargas.append(
            Carga(
                id_carga=f"CAR-{i:04d}",
                tipo_carga=nombre,
                peso_t=round(rng.uniform(1.0, 18.0), 2),
                volumen_m3=round(rng.uniform(2.0, 45.0), 2),
                requiere_refrigeracion=refrig and rng.random() < 0.4,
                fragilidad=rng.choices(["baja", "media", "alta"], weights=[0.5, 0.35, 0.15])[0],
            )
        )

    # --- 7. Viajes -----------------------------------------------------------
    hub = red.hubs.iloc[0]["id_nodo"]
    viajes: list[Viaje] = []
    detalles: list[DetalleCarga] = []
    gastos: list[GastoViaje] = []
    idx_clientes = clientes

    for i in range(1, n_viajes + 1):
        v = vehiculos[(i - 1) % len(vehiculos)]
        c = conductores[(i - 1) % len(conductores)]

        # 1-4 entregas por viaje, sesgadas a la misma ciudad.
        k = rng.choices([1, 2, 3, 4], weights=[0.35, 0.35, 0.2, 0.1])[0]
        ciudad_foco = rng.choices(
            [x["id"] for x in esc.ciudades if x.get("distribuidores", 0) > 0],
            weights=[
                x["distribuidores"] for x in esc.ciudades if x.get("distribuidores", 0) > 0
            ],
        )[0]
        candidatos = [x for x in idx_clientes if x.ciudad == ciudad_foco]
        if len(candidatos) < k:
            candidatos = idx_clientes
        elegidos = rng.sample(candidatos, min(k, len(candidatos)))
        destinos = _ordenar_por_vecino_mas_cercano(
            red.nodos, [x.id_nodo_entrega for x in elegidos], hub
        )

        # Ruta planificada: concatenación de caminos mínimos.
        camino: list[str] = [hub]
        for d in destinos:
            tramo = _ruta_entre(red, camino[-1], d)
            if tramo:
                camino += tramo[1:]
        km, minutos = _metricas_ruta(red, camino)

        hora_salida = round(rng.uniform(esc.ventanas["hora_inicio_operacion"],
                                        esc.ventanas["hora_inicio_operacion"] + 5), 2)
        hora_llegada = hora_salida + minutos / 60.0

        costo = (
            km * (costos["combustible_por_km"] + costos["peaje_por_km"]
                  + costos["mantenimiento_por_km"])
            + (minutos / 60.0) * costos["costo_hora_conductor"]
        )

        fecha_viaje = fecha + timedelta(days=(i - 1) // max(len(vehiculos), 1))
        viajes.append(
            Viaje(
                id_viaje=f"VIA-{i:04d}",
                id_vehiculo=v.id_vehiculo,
                id_conductor=c.id_conductor,
                id_nodo_origen=hub,
                id_nodo_destino=destinos[-1],
                fecha_planificada=fecha_viaje.date().isoformat(),
                hora_salida_plan=hora_salida,
                hora_llegada_plan=round(hora_llegada, 2),
                distancia_plan_km=round(km, 2),
                costo_plan_pen=round(costo, 2),
            )
        )

        for orden, (cl, nodo) in enumerate(zip(elegidos, destinos), start=1):
            carga = cargas[(i * 3 + orden) % len(cargas)]
            detalles.append(
                DetalleCarga(
                    id_detalle=f"DET-{i:04d}-{orden}",
                    id_viaje=f"VIA-{i:04d}",
                    id_carga=carga.id_carga,
                    id_cliente=cl.id_cliente,
                    orden_entrega=orden,
                )
            )

        gastos += [
            GastoViaje(f"GAS-{i:04d}-1", f"VIA-{i:04d}", "combustible",
                       round(km * costos["combustible_por_km"], 2), km, 0.0),
            GastoViaje(f"GAS-{i:04d}-2", f"VIA-{i:04d}", "peaje",
                       round(km * costos["peaje_por_km"], 2), km, 0.0),
            GastoViaje(f"GAS-{i:04d}-3", f"VIA-{i:04d}", "mantenimiento",
                       round(km * costos["mantenimiento_por_km"], 2), km, 0.0),
            GastoViaje(f"GAS-{i:04d}-4", f"VIA-{i:04d}", "conductor",
                       round((minutos / 60.0) * costos["costo_hora_conductor"], 2),
                       0.0, round(minutos / 60.0, 2)),
        ]

    return DatasetOperativo(
        conductores=pd.DataFrame([x.a_dict() for x in conductores]),
        vehiculos=pd.DataFrame([x.a_dict() for x in vehiculos]),
        jornadas=pd.DataFrame([x.a_dict() for x in jornadas]),
        puntos_descanso=pd.DataFrame([x.a_dict() for x in puntos]),
        clientes=pd.DataFrame([x.a_dict() for x in clientes]),
        cargas=pd.DataFrame([x.a_dict() for x in cargas]),
        viajes=pd.DataFrame([x.a_dict() for x in viajes]),
        detalle_carga=pd.DataFrame([x.a_dict() for x in detalles]),
        gastos_plan=pd.DataFrame([x.a_dict() for x in gastos]),
    )


def guardar_dataset(ds: DatasetOperativo, destino) -> dict[str, str]:
    from pathlib import Path

    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    rutas: dict[str, str] = {}
    for nombre, df in ds.tablas().items():
        ruta = destino / f"{nombre}.parquet"
        df.to_parquet(ruta, index=False)
        rutas[nombre] = str(ruta)
    return rutas
