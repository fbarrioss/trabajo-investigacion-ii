"""Entorno de simulación multiagente para la ruta operativa de carga.

Implementa un Dec-POMDP cooperativo con entrenamiento centralizado y ejecución
descentralizada (CTDE):

    * Agentes      : unidades vehiculares (1 agente = 1 vehículo en tránsito).
    * Observación  : vector local normalizado por agente (18 dimensiones).
    * Estado global: concatenación de observaciones + variables del sistema.
    * Acciones     : 4 (continuar, tomar alterna, esperar, replanificar).
    * Recompensa   : función compartida (cooperativa) según Tabla 3 del plan.

El entorno no depende de Gymnasium: expone la misma interfaz mínima
(``reset`` / ``step``) para evitar una dependencia adicional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from ..comun.config import ConfigEntrenamiento, Escenario
from ..datos.eventos import GeneradorEventos
from ..red_vial.construir_red import RedVial

N_ACCIONES = 4
ACCIONES = {
    0: "continuar",
    1: "tomar_alterna",
    2: "esperar",
    3: "replanificar",
}
DIM_OBS = 20


# ---------------------------------------------------------------------------
@dataclass
class AgenteVehiculo:
    """Estado interno de un agente vehicular."""

    idx: int
    id_viaje: str
    id_vehiculo: str
    nodo_actual: str
    destino: str
    entregas: list[dict[str, Any]]          # [{id_cliente, id_nodo, v_ini, v_fin}]
    entregas_realizadas: int = 0
    plan_nodos: list[str] = field(default_factory=list)
    plan_aristas: list[str] = field(default_factory=list)

    tramo_actual: str | None = None
    tramo_destino: str | None = None
    km_tramo: float = 0.0
    km_recorridos_tramo: float = 0.0
    # Remanente de kilómetros que no se pudieron recorrer al cierre del paso
    # porque el siguiente tramo no cabía completo; se consume en el paso
    # siguiente para no perder ni inventar distancia.
    credito_km: float = 0.0

    km_totales: float = 0.0
    costo: float = 0.0
    tiempo_min: float = 0.0
    retraso_min: float = 0.0
    replanificaciones: int = 0
    esperas: int = 0

    en_nodo: bool = True
    entregado: bool = False
    bloqueado_pasos: int = 0
    horas_conduccion_continua: float = 0.0
    minutos_descanso_restantes: float = 0.0
    recompensa_pendiente: float = 0.0
    costo_paso_ref: float = 100.0
    # Tramos que el conductor evita temporalmente (hora hasta la que evita).
    evitar: dict[str, float] = field(default_factory=dict)
    historial: list[dict[str, Any]] = field(default_factory=list)

    @property
    def progreso_tramo(self) -> float:
        return 0.0 if self.km_tramo <= 0 else min(self.km_recorridos_tramo / self.km_tramo, 1.0)


# ---------------------------------------------------------------------------
class EntornoRutasCarga:
    """Simulador de rutas operativas con eventos dinámicos."""

    def __init__(
        self,
        red: RedVial,
        dataset: dict[str, pd.DataFrame],
        esc: Escenario | None = None,
        cfg: ConfigEntrenamiento | None = None,
        semilla: int | None = None,
    ) -> None:
        self.red = red
        self.ds = dataset
        self.esc = esc or Escenario.cargar()
        self.cfg = cfg or ConfigEntrenamiento.cargar()
        self.semilla = semilla if semilla is not None else self.esc.semilla

        self.n_agentes = int(self.cfg["entorno"]["n_agentes"])
        self.horizonte = int(self.cfg["entorno"]["horizonte_pasos"])
        self.dt_min = float(self.cfg["entorno"]["duracion_paso_min"])
        self.max_replan = int(self.cfg["entorno"]["max_replanificaciones"])
        self.holgura = float(self.cfg["entorno"]["ventana_entrega_min"])
        self.descanso_cada_h = float(self.cfg["entorno"].get("descanso_cada_h", 5.0))
        self.descanso_min = float(self.cfg["entorno"].get("descanso_min", 45))

        self.costos = self.esc.costos
        self.pesos = self.cfg["recompensa"]

        self.rng = np.random.default_rng(self.semilla)
        self._preparar_indices()
        self.reset()

    # ------------------------------------------------------------------
    def _nodo(self, valor: Any) -> Any:
        """Normaliza un ID de nodo al tipo que usa el grafo.

        Las tablas Parquet guardan los IDs como texto, mientras que el grafo
        consolidado los indexa por entero (ID de OSM). Sin esta conversión
        ``nodo_actual`` nunca coincide con una clave del grafo y el vehículo no
        avanza.
        """
        s = str(valor)
        return int(s) if s.lstrip("-").isdigit() else s

    def _preparar_indices(self) -> None:
        self.tramos = self.red.tramos.set_index("id_tramo")
        self.nodos = self.red.nodos.set_index("id_nodo")
        self.salidas: dict[str, list[str]] = {}
        for nodo, grupo in self.red.tramos.groupby("id_nodo_ini"):
            self.salidas[str(nodo)] = grupo["id_tramo"].tolist()

        # Viajes elegibles: los que tienen ruta válida.
        viajes = self.ds["viajes"].copy()
        det = self.ds["detalle_carga"]
        clientes = self.ds["clientes"].set_index("id_cliente")
        self.plantillas: list[dict[str, Any]] = []
        for _, v in viajes.iterrows():
            filas = det.loc[det["id_viaje"] == v["id_viaje"]].sort_values("orden_entrega")
            if filas.empty:
                continue
            entregas = []
            for _, d in filas.iterrows():
                if d["id_cliente"] not in clientes.index:
                    continue
                c = clientes.loc[d["id_cliente"]]
                entregas.append(
                    {
                        "id_cliente": d["id_cliente"],
                        "id_nodo": self._nodo(c["id_nodo_entrega"]),
                        "v_ini": float(c["ventana_inicio"]),
                        "v_fin": float(c["ventana_fin"]),
                    }
                )
            if not entregas:
                continue
            self.plantillas.append(
                {
                    "id_viaje": v["id_viaje"],
                    "id_vehiculo": v["id_vehiculo"],
                    "origen": self._nodo(v["id_nodo_origen"]),
                    "entregas": entregas,
                    "hora_salida": float(v["hora_salida_plan"]),
                    "costo_plan": float(v["costo_plan_pen"]),
                    "horas_plan": max(
                        float(v["hora_llegada_plan"]) - float(v["hora_salida_plan"]), 1.0
                    ),
                    "capacidad_t": float(
                        self.ds["vehiculos"].set_index("id_vehiculo")
                        .loc[v["id_vehiculo"], "capacidad_t"]
                    ),
                }
            )

    # ------------------------------------------------------------------
    def _ruta_minima(
        self,
        origen: str,
        destino: str,
        agente: "AgenteVehiculo | None" = None,
        evitar_tramo: str | None = None,
    ) -> list[str]:
        """Camino de menor tiempo considerando los eventos activos.

        La ponderación incorpora el multiplicador de severidad del evento
        vigente en cada tramo, de modo que replanificar tiene efecto real
        frente a bloqueos y congestiones. Los tramos que el conductor ya
        descartó (``evitar``) reciben una penalización fuerte. Con
        ``evitar_tramo`` se calcula un **desvío** que rodea un tramo concreto.
        """

        def peso(u: str, v: str, datos: dict[str, Any]) -> float:
            # En un MultiDiGraph networkx puede entregar el diccionario de
            # aristas paralelas {clave: atributos}; se toma la más rápida.
            if "tiempo_flujo_min" in datos:
                d = datos
            else:
                d = min(
                    datos.values(),
                    key=lambda x: x.get("tiempo_flujo_min", 1e9),
                )
            base = str(d.get("id_tramo", ""))
            f = self._factor_evento(base)
            if agente is not None and agente.evitar.get(base, 0.0) > self.hora_actual:
                f *= 50.0
            if evitar_tramo is not None and base == evitar_tramo:
                f *= 1000.0
            return float(d["tiempo_flujo_min"]) * f

        clave = (origen, destino, evitar_tramo)
        cache = getattr(self, "_cache_rutas", None)
        if cache is not None and clave in cache:
            return list(cache[clave])

        try:
            camino = nx.shortest_path(self.red.grafo, origen, destino, weight=peso)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            camino = []

        if cache is not None:
            cache[clave] = camino
        return list(camino)

    def _siguiente_tramo(self, nodo: str, destino: str) -> str | None:
        ruta = self._ruta_minima(nodo, destino)
        if len(ruta) < 2:
            return None
        a, b = ruta[0], ruta[1]
        mejor, mejor_t = None, math.inf
        for clave, datos in self.red.grafo[a][b].items():
            t = float(datos["tiempo_flujo_min"])
            if t < mejor_t:
                mejor, mejor_t = clave, t
        return mejor

    # ------------------------------------------------------------------
    def _tramo_entre(
        self, origen: str, destino: str | None, alternativa: bool = False
    ) -> str | None:
        """Arista del grafo entre dos nodos (principal o alterna)."""
        if destino is None or origen not in self.red.grafo:
            return None
        if destino not in self.red.grafo[origen]:
            return None

        candidatos = []
        for clave, datos in self.red.grafo[origen][destino].items():
            base = clave[:-2] if clave.endswith("_R") else clave
            es_alt = bool(self.tramos.loc[base, "es_alternativa"])
            candidatos.append((clave, es_alt, float(datos["tiempo_flujo_min"])))
        if not candidatos:
            return None

        if alternativa:
            alts = [c for c in candidatos if c[1]]
            if not alts:
                return None
            return min(alts, key=lambda c: c[2])[0]

        principales = [c for c in candidatos if not c[1]]
        return min(principales or candidatos, key=lambda c: c[2])[0]

    @staticmethod
    def _proximo_nodo(a: AgenteVehiculo) -> str | None:
        """Siguiente nodo del plan vigente (None si ya está en destino)."""
        return a.plan_nodos[1] if len(a.plan_nodos) >= 2 else None

    def _mejor_arista(
        self, origen: str, destino: str, agente: "AgenteVehiculo | None" = None
    ) -> str | None:
        """Arista de menor tiempo *ajustado por eventos* entre dos nodos."""
        if destino is None or origen not in self.red.grafo:
            return None
        if destino not in self.red.grafo[origen]:
            return None
        mejor, mejor_t = None, math.inf
        for clave, datos in self.red.grafo[origen][destino].items():
            base = clave[:-2] if clave.endswith("_R") else clave
            t = float(datos["tiempo_flujo_min"]) * self._factor_evento(base)
            if agente is not None and agente.evitar.get(base, 0.0) > self.hora_actual:
                t *= 50.0
            if t < mejor_t:
                mejor, mejor_t = clave, t
        return mejor

    def _tiene_alterna(self, clave: str | None) -> bool:
        """¿El tramo admite un desvío real? (no es arista de corte)."""
        if clave is None:
            return False
        base = clave[:-2] if clave.endswith("_R") else clave
        if "tiene_alterna" not in self.tramos.columns:
            return True
        try:
            return bool(self.tramos.at[base, "tiene_alterna"])
        except KeyError:
            return False

    def _planificar(self, a: AgenteVehiculo) -> None:
        """Recalcula el plan (nodos + aristas) con el estado actual de eventos."""
        nodos = self._ruta_minima(a.nodo_actual, a.destino, a)
        a.plan_nodos = nodos
        a.plan_aristas = [
            self._mejor_arista(u, v, a) for u, v in zip(nodos[:-1], nodos[1:])
        ]

    # ------------------------------------------------------------------
    def _indexar_eventos(self) -> None:
        """Índice O(1) de eventos por tramo (evita filtrar el DataFrame)."""
        self.eventos_por_tramo: dict[str, list[tuple[float, float, float, str]]] = {}
        if self.eventos is None or self.eventos.empty:
            return
        for fila in self.eventos.itertuples(index=False):
            self.eventos_por_tramo.setdefault(str(fila.id_tramo), []).append(
                (float(fila.hora_inicio), float(fila.hora_fin),
                 float(fila.severidad), str(fila.tipo_evento))
            )
        self._cache_rutas: dict[tuple[str, str], list[str]] = {}

    # ------------------------------------------------------------------
    def _factor_evento(self, id_tramo: str) -> float:
        """Multiplicador de tiempo por eventos activos en el tramo."""
        lista = self.eventos_por_tramo.get(id_tramo)
        if not lista:
            return 1.0
        hora = self.hora_actual
        f = 1.0
        for ini, fin, sev, _ in lista:
            if ini <= hora < fin:
                f *= sev
        return f

    def _tipo_evento_activo(self, id_tramo: str) -> str:
        lista = self.eventos_por_tramo.get(id_tramo)
        if not lista:
            return ""
        hora = self.hora_actual
        for ini, fin, _sev, tipo in lista:
            if ini <= hora < fin:
                return tipo
        return ""

    # ------------------------------------------------------------------
    def reset(self, semilla: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Reinicia el episodio y devuelve (observaciones, estado_global)."""
        if semilla is not None:
            self.rng = np.random.default_rng(semilla)

        self.paso = 0
        self.hora_actual = float(self.esc.ventanas["hora_inicio_operacion"])

        # Eventos del episodio.
        gen = GeneradorEventos(
            self.esc, self.red.tramos, semilla=int(self.rng.integers(0, 10**6))
        )
        self.eventos = gen.generar(horizonte_horas=24.0)
        self._indexar_eventos()

        # Selección de viajes.
        idx = self.rng.choice(
            len(self.plantillas), size=min(self.n_agentes, len(self.plantillas)),
            replace=False,
        )
        self.agentes: list[AgenteVehiculo] = []
        for k, i in enumerate(idx):
            p = self.plantillas[int(i)]
            a = AgenteVehiculo(
                idx=k,
                id_viaje=p["id_viaje"],
                id_vehiculo=p["id_vehiculo"],
                nodo_actual=p["origen"],
                destino=p["entregas"][0]["id_nodo"],
                entregas=list(p["entregas"]),
                costo_paso_ref=float(
                    p["costo_plan"] / max(p["horas_plan"] * 60.0 / self.dt_min, 1.0)
                ),
            )
            self.agentes.append(a)

        for a in self.agentes:
            self._planificar(a)
            self._iniciar_tramo(a, a.plan_aristas[0] if a.plan_aristas else None)

        self.recompensa_acumulada = 0.0
        self.decisiones: list[dict[str, Any]] = []
        return self._observaciones(), self._estado_global()

    # ------------------------------------------------------------------
    def _resolver_tramo(self, clave: str) -> dict[str, Any]:
        """Resuelve una clave de arista (``T0001`` o ``T0001_R``) a sus datos.

        El grafo es un ``MultiDiGraph`` con una arista por sentido; las aristas
        inversas llevan el sufijo ``_R``. Esta función normaliza ambos casos.
        """
        reversa = clave.endswith("_R")
        base = clave[:-2] if reversa else clave
        fila = self.tramos.loc[base]
        # Los extremos deben quedar con el mismo tipo que las claves del grafo
        # (entero de OSM); de lo contrario `nodo_actual` deja de coincidir tras
        # el primer tramo y el planificador devuelve un camino vacío.
        origen = self._nodo(fila["id_nodo_fin"] if reversa else fila["id_nodo_ini"])
        destino = self._nodo(fila["id_nodo_ini"] if reversa else fila["id_nodo_fin"])
        return {
            "id_tramo": base,
            "clave": clave,
            "origen": origen,
            "destino": destino,
            "km": float(fila["distancia_km"]),
            "velocidad": float(fila["velocidad_flujo_kmh"]),
        }

    def _iniciar_tramo(self, a: AgenteVehiculo, clave: str | None) -> None:
        if clave is None:
            a.tramo_actual = None
            a.en_nodo = True
            return
        info = self._resolver_tramo(clave)
        a.tramo_actual = info["id_tramo"]
        a.tramo_destino = info["destino"]
        a.km_tramo = info["km"]
        a.km_recorridos_tramo = 0.0
        a.en_nodo = False

    # ------------------------------------------------------------------
    def _cerrar_tramo(self, a: AgenteVehiculo) -> None:
        """Marca el tramo actual como recorrido y actualiza el plan."""
        a.nodo_actual = a.tramo_destino or a.nodo_actual
        a.tramo_actual = None
        a.en_nodo = True
        a.km_recorridos_tramo = 0.0
        # Consumir el nodo alcanzado del plan; si el desvío salió del plan,
        # se recalcula para mantener la coherencia de la ruta.
        if len(a.plan_nodos) >= 2 and a.plan_nodos[1] == a.nodo_actual:
            a.plan_nodos.pop(0)
            if a.plan_aristas:
                a.plan_aristas.pop(0)
        elif a.plan_nodos and a.plan_nodos[0] != a.nodo_actual:
            self._planificar(a)
        self._procesar_llegada_nodo(a)

    def _km_por_paso(self, a: AgenteVehiculo, factor_tiempo: float = 1.0) -> float:
        """Distancia que recorre el vehículo en un paso de simulación."""
        if a.tramo_actual is None:
            return 0.0
        fila = self.tramos.loc[a.tramo_actual]
        vel = float(fila["velocidad_flujo_kmh"]) / max(factor_tiempo, 1e-3)
        return max(vel, 3.0) * (self.dt_min / 60.0)

    def _avanzar(self, a: AgenteVehiculo, factor_tiempo: float = 1.0) -> None:
        """Avanza el vehículo a lo largo de su plan durante un paso completo.

        El paso equivale a ``dt_min`` minutos, así que el vehículo debe recorrer
        toda la distancia correspondiente (antes se consumía **un solo tramo**
        por paso: con tramos urbanos de pocas decenas de metros el vehículo
        avanzaba ~0.2 km cada 15 minutos y nunca llegaba a destino).

        La distancia recorrida es exacta: si el presupuesto se agota antes de
        terminar un tramo, el remanente se guarda en ``credito_km`` y se
        consume en el paso siguiente. Así el vehículo termina el paso sobre un
        nodo (y puede decidir) sin inventar ni perder kilómetros.
        """
        if a.tramo_actual is None:
            a.en_nodo = True
            return

        # Regla de jornada: descanso obligatorio tras N horas continuas.
        a.horas_conduccion_continua += self.dt_min / 60.0
        a.costo += (self.dt_min / 60.0) * self.costos["costo_hora_conductor"]
        if a.horas_conduccion_continua >= self.descanso_cada_h:
            a.horas_conduccion_continua = 0.0
            # El propio paso del disparo ya es descanso, por lo que solo resta
            # el remanente (45 min con dt=15 -> 30 min adicionales).
            a.minutos_descanso_restantes = max(self.descanso_min - self.dt_min, 0.0)
            return

        costo_km = (
            self.costos["combustible_por_km"]
            + self.costos["peaje_por_km"]
            + self.costos["mantenimiento_por_km"]
        )

        km_paso = self._km_por_paso(a, factor_tiempo)
        km_restante = km_paso + a.credito_km
        a.credito_km = 0.0
        vueltas = 0
        while km_restante > 1e-9 and vueltas < 5000:
            vueltas += 1
            if a.entregado:
                break

            # Sin tramo en curso: se toma el siguiente del plan sin necesidad
            # de una nueva decisión (el agente ya decidió al salir del nodo).
            if a.tramo_actual is None:
                if not a.plan_aristas:
                    break
                self._iniciar_tramo(a, a.plan_aristas[0])
                if a.tramo_actual is None:
                    break

            restante_tramo = max(a.km_tramo - a.km_recorridos_tramo, 0.0)

            if restante_tramo <= 1e-9:
                self._cerrar_tramo(a)
                continue

            if restante_tramo <= km_restante + 1e-9:
                # El tramo cabe completo en el presupuesto.
                a.km_recorridos_tramo = a.km_tramo
                a.km_totales += restante_tramo
                a.costo += restante_tramo * costo_km
                km_restante -= restante_tramo
                self._cerrar_tramo(a)
                continue

            # El tramo no cabe entero. Si ya está en tránsito o el tramo es más
            # largo que un paso completo, avanza parcialmente; si no, se queda
            # en el nodo y conserva el remanente como crédito.
            if a.km_recorridos_tramo > 1e-9 or restante_tramo > km_paso + 1e-9:
                a.km_recorridos_tramo += km_restante
                a.km_totales += km_restante
                a.costo += km_restante * costo_km
                km_restante = 0.0
                a.en_nodo = False
            else:
                a.credito_km = km_restante
                km_restante = 0.0
                a.tramo_actual = None
                a.tramo_destino = None
                a.km_recorridos_tramo = 0.0
                a.en_nodo = True
            break

    # ------------------------------------------------------------------
    def _procesar_llegada_nodo(self, a: AgenteVehiculo) -> None:
        """Registra entrega si el nodo actual es el destino de la entrega."""
        if not a.entregas:
            return
        objetivo = a.entregas[0]
        if a.nodo_actual != objetivo["id_nodo"]:
            return

        hora = self.hora_actual
        # La llegada temprana es admisible: el vehículo espera al inicio de la
        # ventana. El incumplimiento se produce solo al exceder el cierre.
        limite = objetivo["v_fin"] + self.holgura / 60.0
        cumple = hora <= limite
        if hora > objetivo["v_fin"]:
            a.retraso_min += (hora - objetivo["v_fin"]) * 60.0

        a.entregas.pop(0)
        a.entregas_realizadas += 1
        # Señal de recompensa diferida: se cobra en el paso en que ocurre.
        if cumple:
            a.recompensa_pendiente += float(self.pesos["bonus_entrega"])
        else:
            a.recompensa_pendiente -= float(self.pesos["penalidad_incumplimiento"])
        a.historial.append(
            {
                "id_viaje": a.id_viaje,
                "id_cliente": objetivo["id_cliente"],
                "hora_llegada": round(hora, 2),
                "cumple_ventana": bool(cumple),
            }
        )
        a.entregado = not a.entregas
        if a.entregas:
            a.destino = a.entregas[0]["id_nodo"]
            self._planificar(a)
            self._iniciar_tramo(a, a.plan_aristas[0] if a.plan_aristas else None)

    # ------------------------------------------------------------------
    def _aplicar_accion(self, a: AgenteVehiculo, accion: int) -> float:
        """Ejecuta la acción del agente en un nodo. Devuelve penalización."""
        if not a.en_nodo:
            return 0.0

        penal = 0.0
        prox = self._proximo_nodo(a)
        clave_plan = a.plan_aristas[0] if a.plan_aristas else None

        if accion == 2:  # esperar
            a.esperas += 1
            a.costo += (self.dt_min / 60.0) * self.costos["costo_hora_detencion"]
            penal += 0.05
            # Si sigue bloqueado demasiado tiempo, se fuerza la replanificación.
            if a.bloqueado_pasos >= 3:
                accion = 3
            else:
                return penal

        if accion == 3:  # replanificar desde el nodo actual
            if a.replanificaciones < self.max_replan:
                a.replanificaciones += 1
                penal += self.pesos["w_replanificacion"] * 0.1
            self._planificar(a)
            clave = a.plan_aristas[0] if a.plan_aristas else None
            self._iniciar_tramo(a, clave)
            return penal

        if accion == 1:  # desvío: rodear el tramo planificado
            clave = clave_plan
            if clave is not None:
                base = clave[:-2] if clave.endswith("_R") else clave
                ruta = self._ruta_minima(
                    a.nodo_actual, a.destino, a, evitar_tramo=base
                )
                if len(ruta) >= 2:
                    clave = self._mejor_arista(ruta[0], ruta[1], a)
            self._iniciar_tramo(a, clave)
            return penal

        # accion == 0: continuar por el plan vigente (sin recalcular)
        clave = clave_plan
        if clave is None:
            clave = self._siguiente_tramo(a.nodo_actual, a.destino)
        # No se entra a un tramo severamente bloqueado: se espera en el nodo.
        if clave is not None:
            base = clave[:-2] if clave.endswith("_R") else clave
            if self._factor_evento(base) >= 5.0:
                a.esperas += 1
                a.costo += (self.dt_min / 60.0) * self.costos["costo_hora_detencion"]
                return penal + 0.05
        self._iniciar_tramo(a, clave)
        return penal

    # ------------------------------------------------------------------
    def step(self, acciones: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        """Ejecuta un paso de simulación para todos los agentes."""
        acciones = np.asarray(acciones, dtype=int).reshape(-1)
        # Los eventos cambian con el reloj: se invalida la caché de rutas.
        self._cache_rutas.clear()
        recompensas = np.zeros(self.n_agentes, dtype=np.float32)
        penal_extra = np.zeros(self.n_agentes, dtype=np.float32)
        costo_antes = np.array([a.costo for a in self.agentes], dtype=np.float64)

        # 1. Decisión en nodo.
        for a in self.agentes:
            if a.entregado:
                continue
            if a.en_nodo:
                penal_extra[a.idx] = self._aplicar_accion(a, int(acciones[a.idx]))
                self.decisiones.append(
                    {
                        "id_viaje": a.id_viaje,
                        "id_agente": f"AG-{a.idx}",
                        "hora": round(self.hora_actual, 2),
                        "id_nodo": a.nodo_actual,
                        "accion": int(acciones[a.idx]),
                        "accion_nombre": ACCIONES[int(acciones[a.idx])],
                    }
                )

        # 2. Avance físico.
        for a in self.agentes:
            if a.entregado:
                continue
            if a.minutos_descanso_restantes > 0:
                # Descanso obligatorio del conductor: no hay avance.
                a.minutos_descanso_restantes -= self.dt_min
                a.costo += (self.dt_min / 60.0) * self.costos["costo_hora_conductor"]
                continue
            factor = self._factor_evento(a.tramo_actual) if a.tramo_actual else 1.0
            if factor >= 5.0 and a.tramo_actual is not None:
                # Bloqueo severo: el vehículo no avanza y acumula espera.
                a.bloqueado_pasos += 1
                a.costo += (self.dt_min / 60.0) * self.costos["costo_hora_detencion"]
                penal_extra[a.idx] += self.pesos["penalidad_bloqueo"] * 0.05
                # Tras 4 pasos detenido, la unidad retrocede al último nodo y
                # recupera la capacidad de decisión (evita el bloqueo muerto).
                if a.bloqueado_pasos >= 4:
                    if a.tramo_actual:
                        # El conductor memoriza el tramo bloqueado y lo evita
                        # durante las próximas 3 horas.
                        a.evitar[a.tramo_actual] = self.hora_actual + 3.0
                    a.tramo_actual = None
                    a.tramo_destino = None
                    a.km_recorridos_tramo = 0.0
                    a.en_nodo = True
                    a.bloqueado_pasos = 0
                    self._planificar(a)
            else:
                a.bloqueado_pasos = 0
                self._avanzar(a, factor)

        # 3. Reloj y tiempo por agente.
        self.hora_actual += self.dt_min / 60.0
        for a in self.agentes:
            if not a.entregado:
                a.tiempo_min += self.dt_min

        # 4. Recompensas.
        for a in self.agentes:
            delta_costo = a.costo - float(costo_antes[a.idx])
            recompensas[a.idx] = self._recompensa(a, delta_costo) - penal_extra[a.idx]

        # 5. Fin de episodio.
        self.paso += 1
        terminado = (
            all(a.entregado for a in self.agentes)
            or self.paso >= self.horizonte
        )
        dones = np.full(self.n_agentes, float(terminado), dtype=np.float32)

        obs = self._observaciones()
        estado = self._estado_global()
        info = {
            "hora": round(self.hora_actual, 2),
            "paso": self.paso,
            "decisiones": self.decisiones,
            "agentes": [
                {
                    "id_viaje": a.id_viaje,
                    "entregado": a.entregado,
                    "km": round(a.km_totales, 2),
                    "costo": round(a.costo, 2),
                    "retraso_min": round(a.retraso_min, 1),
                    "replanificaciones": a.replanificaciones,
                    "entregas_realizadas": a.entregas_realizadas,
                    "entregas_totales": a.entregas_realizadas + len(a.entregas),
                }
                for a in self.agentes
            ],
        }
        return obs, recompensas, dones, info

    # ------------------------------------------------------------------
    def _recompensa(self, a: AgenteVehiculo, delta_costo: float = 0.0) -> float:
        """Recompensa del paso, normalizada respecto del costo de referencia.

        El costo se expresa como múltiplo del costo por paso de la ruta
        planificada (~1.0 en operación normal, >1 en desvíos o esperas). Esto
        elimina el sesgo por la longitud del viaje, que variaba entre episodios
        y hacía incomparables los retornos.
        """
        p = self.pesos
        r = 0.0

        referencia = max(a.costo_paso_ref, 1e-6)
        r -= p["w_costo"] * (max(delta_costo, 0.0) / referencia)
        # Término temporal de bajo peso (evita rutas que se estacionan).
        r -= p["w_tiempo"] * 0.05

        # Puntualidad: penalización proporcional al margen negativo restante.
        if a.entregas:
            objetivo = a.entregas[0]
            margen = (objetivo["v_fin"] - self.hora_actual) * 60.0
            if margen < 0:
                r -= p["penalidad_incumplimiento"] * min(abs(margen) / 240.0, 1.0) * 0.05

        # Señal diferida por entregas (bonus o penalidad).
        r += a.recompensa_pendiente
        a.recompensa_pendiente = 0.0

        # Desvío: penaliza el recorrido sobre el tramo actual.
        if a.tramo_actual is not None and a.km_tramo > 0:
            r -= p["w_desvio"] * 0.002

        return float(r)

    # ------------------------------------------------------------------
    def _observaciones(self) -> np.ndarray:
        obs = np.zeros((self.n_agentes, DIM_OBS), dtype=np.float32)
        for a in self.agentes:
            obs[a.idx] = self._obs_agente(a)
        return obs

    def _obs_agente(self, a: AgenteVehiculo) -> np.ndarray:
        o = np.zeros(DIM_OBS, dtype=np.float32)
        if a.entregado:
            o[15] = 1.0
            return o

        ruta = self._ruta_minima(a.nodo_actual, a.destino, a)
        dist_rest = 0.0
        tiempo_rest = 0.0
        for x, y in zip(ruta[:-1], ruta[1:]):
            mejor = min(
                self.red.grafo[x][y].values(), key=lambda d: d["tiempo_flujo_min"]
            )
            dist_rest += float(mejor["distancia_km"])
            tiempo_rest += float(mejor["tiempo_flujo_min"])

        objetivo = a.entregas[0] if a.entregas else None
        horas_ventana = (
            (objetivo["v_fin"] - self.hora_actual) if objetivo else 6.0
        )
        hora_norm = (self.hora_actual % 24.0) / 24.0

        o[0] = min(dist_rest / 1000.0, 1.0)
        o[1] = min(tiempo_rest / 1200.0, 1.0)
        o[2] = np.clip(horas_ventana / 12.0, -1.0, 1.0)
        o[3] = 1.0 - (len(a.entregas) / max(a.entregas_realizadas + len(a.entregas), 1))
        o[4] = min(a.costo / self.pesos["escala_costo"], 1.0)
        o[5] = min(a.retraso_min / 240.0, 1.0)
        o[6] = 1.0 - min(dist_rest / max(a.km_totales + dist_rest, 1e-6), 1.0)
        o[7] = math.sin(2 * math.pi * hora_norm)
        o[8] = math.cos(2 * math.pi * hora_norm)
        factor = self._factor_evento(a.tramo_actual) if a.tramo_actual else 1.0
        tipo = self._tipo_evento_activo(a.tramo_actual) if a.tramo_actual else ""
        o[9] = 1.0 if tipo == "congestion" else 0.0
        o[10] = 1.0 if tipo in ("bloqueo", "averia") else 0.0
        o[11] = len(a.entregas) / 4.0
        o[12] = a.replanificaciones / max(self.max_replan, 1)
        o[13] = min(len(self.salidas.get(a.nodo_actual, [])) / 4.0, 1.0)
        o[14] = 1.0 if a.nodo_actual == a.destino else 0.0
        o[15] = a.progreso_tramo
        o[16] = min(max(factor - 1.0, 0.0) / 5.0, 1.0)
        o[17] = 1.0 if a.en_nodo else 0.0

        # Tiempo estimado del tramo planificado (ajustado por eventos) y
        # disponibilidad de un desvío real. Sin desvío el vehículo solo puede
        # esperar, y el agente debe saberlo antes de decidir.
        prox = self._proximo_nodo(a)
        t_plan = 0.0
        if prox is not None:
            clave_plan = self._tramo_entre(a.nodo_actual, prox)
            if clave_plan:
                base = clave_plan[:-2] if clave_plan.endswith("_R") else clave_plan
                t_plan = float(self.tramos.loc[base, "tiempo_flujo_min"]) * self._factor_evento(base)
        o[18] = min(t_plan / 300.0, 1.0)
        o[19] = 1.0 if self._tiene_alterna(a.plan_aristas[0] if a.plan_aristas else None) else -1.0
        return o

    # ------------------------------------------------------------------
    def _estado_global(self) -> np.ndarray:
        """Estado global para el mezclador (CTDE)."""
        obs = self._observaciones().reshape(-1)
        n_eventos = 0
        if not self.eventos.empty:
            n_eventos = int(
                (
                    (self.eventos["hora_inicio"] <= self.hora_actual)
                    & (self.eventos["hora_fin"] > self.hora_actual)
                ).sum()
            )
        extra = np.array(
            [
                (self.hora_actual % 24.0) / 24.0,
                self.paso / max(self.horizonte, 1),
                min(n_eventos / 20.0, 1.0),
                float(np.mean([a.entregado for a in self.agentes])),
            ],
            dtype=np.float32,
        )
        return np.concatenate([obs, extra])

    # ------------------------------------------------------------------
    @property
    def dim_estado_global(self) -> int:
        return self.n_agentes * DIM_OBS + 4

    def mascara_acciones(self) -> np.ndarray:
        """Máscara booleana (n_agentes, N_ACCIONES) de acciones válidas."""
        m = np.ones((self.n_agentes, N_ACCIONES), dtype=bool)
        for a in self.agentes:
            if a.entregado or not a.en_nodo:
                m[a.idx, :] = False
                m[a.idx, 0] = True
                continue
            prox = self._proximo_nodo(a)
            if not self._tiene_alterna(a.plan_aristas[0] if a.plan_aristas else None):
                m[a.idx, 1] = False
            if a.replanificaciones >= self.max_replan:
                m[a.idx, 3] = False
        return m
