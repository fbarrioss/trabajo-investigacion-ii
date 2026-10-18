"""Esquema de entidades operativas (modelo entidad-relación de Wilson Ibarra).

Cada entidad se declara como ``dataclass`` y expone ``columnas()`` para poder
exportarse a CSV/Parquet con un esquema estable y documentado.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, ClassVar


class _Entidad:
    """Utilidades comunes de serialización."""

    @classmethod
    def columnas(cls) -> list[str]:
        return [f.name for f in fields(cls)]  # type: ignore[arg-type]

    def a_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Recursos
# ---------------------------------------------------------------------------
@dataclass
class Conductor(_Entidad):
    id_conductor: str
    licencia_categoria: str          # A-II, A-IIIa, A-IIIb
    anios_experiencia: int
    hora_inicio_jornada: float       # hora decimal (4.5 = 04:30)
    horas_max_conduccion: float = 10.0
    descanso_obligatorio_min: int = 45
    zona_base: str = ""


@dataclass
class Vehiculo(_Entidad):
    id_vehiculo: str                 # placa anonimizada
    tipo_vehiculo: str
    capacidad_t: float
    capacidad_m3: float
    velocidad_max_kmh: float
    consumo_km_l: float
    anio_fabricacion: int
    estado: str = "operativo"        # operativo | mantenimiento


@dataclass
class JornadaConductor(_Entidad):
    id_jornada: str
    id_conductor: str
    id_vehiculo: str
    fecha: str
    hora_inicio: float
    hora_fin: float
    horas_conduccion: float
    horas_descanso: float
    cumple_regulacion: bool


@dataclass
class PuntoDescanso(_Entidad):
    id_punto: str
    nombre: str
    lat: float
    lon: float
    km_corredor: float
    tiene_servicios: bool = True


# ---------------------------------------------------------------------------
# 2. Operación
# ---------------------------------------------------------------------------
@dataclass
class Cliente(_Entidad):
    id_cliente: str
    id_nodo_entrega: str
    ciudad: str
    ventana_inicio: float            # hora decimal
    ventana_fin: float
    prioridad: int = 3               # 1 = alta, 5 = baja


@dataclass
class Carga(_Entidad):
    id_carga: str
    tipo_carga: str
    peso_t: float
    volumen_m3: float
    requiere_refrigeracion: bool = False
    fragilidad: str = "media"


@dataclass
class Viaje(_Entidad):
    id_viaje: str
    id_vehiculo: str
    id_conductor: str
    id_nodo_origen: str
    id_nodo_destino: str
    fecha_planificada: str
    hora_salida_plan: float
    hora_llegada_plan: float
    distancia_plan_km: float
    costo_plan_pen: float
    estado: str = "planificado"


@dataclass
class DetalleCarga(_Entidad):
    id_detalle: str
    id_viaje: str
    id_carga: str
    id_cliente: str
    orden_entrega: int


@dataclass
class DetalleEntrega(_Entidad):
    id_entrega: str
    id_viaje: str
    id_cliente: str
    id_nodo_entrega: str
    hora_llegada_real: float
    hora_salida_real: float
    ventana_inicio: float
    ventana_fin: float
    cumple_ventana: bool
    minutos_retraso: float
    estado: str = "entregado"


@dataclass
class GastoViaje(_Entidad):
    id_gasto: str
    id_viaje: str
    concepto: str                    # combustible | peaje | mantenimiento | detencion
    monto_pen: float
    km_asociados: float = 0.0
    horas_asociadas: float = 0.0


# ---------------------------------------------------------------------------
# 3. Red vial y eventos
# ---------------------------------------------------------------------------
@dataclass
class Nodo(_Entidad):
    id_nodo: str
    nombre: str
    tipo: str
    lat: float
    lon: float
    ciudad_id: str
    es_decision: bool
    grado_salida: int


@dataclass
class TramoVial(_Entidad):
    id_tramo: str
    id_nodo_ini: str
    id_nodo_fin: str
    nombre: str
    tipo_via: str
    distancia_km: float
    velocidad_flujo_kmh: float
    tiempo_flujo_min: float
    peaje: bool
    es_alternativa: bool
    tiene_alterna: bool
    bidireccional: bool
    zona: str
    sinuosidad: float
    riesgo_base: float


@dataclass
class Evento(_Entidad):
    id_evento: str
    id_tramo: str
    tipo_evento: str                 # congestion | bloqueo | clima | averia
    hora_inicio: float
    hora_fin: float
    severidad: float                 # multiplicador de tiempo
    fuente: str = "simulador"        # sutran | mtc | simulador
    descripcion: str = ""


@dataclass
class DecisionOperativa(_Entidad):
    id_decision: str
    id_viaje: str
    id_agente: str
    hora: float
    id_nodo: str
    id_tramo_actual: str
    accion: int
    accion_nombre: str
    recompensa: float
    estado_resumen: str = ""


# ---------------------------------------------------------------------------
# Registro central de entidades (para el diccionario de datos)
# ---------------------------------------------------------------------------
ENTIDADES: dict[str, type] = {
    "conductor": Conductor,
    "vehiculo": Vehiculo,
    "jornada_conductor": JornadaConductor,
    "punto_descanso": PuntoDescanso,
    "cliente": Cliente,
    "carga": Carga,
    "viaje": Viaje,
    "detalle_carga": DetalleCarga,
    "detalle_entrega": DetalleEntrega,
    "gasto_viaje": GastoViaje,
    "nodo": Nodo,
    "tramo_vial": TramoVial,
    "evento": Evento,
    "decision_operativa": DecisionOperativa,
}


def esquema_completo() -> dict[str, list[str]]:
    """Devuelve {entidad: [columnas]} para el diccionario de datos."""
    return {nombre: cls.columnas() for nombre, cls in ENTIDADES.items()}
