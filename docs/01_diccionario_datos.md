# Diccionario de datos — Red vial y operación

> Producto verificable del **OE1** ("Caracterizar y estructurar la información
> operativa"). Fuente de las entidades: modelo entidad-relación presentado por
> Wilson Ibarra en la reunión del 16/10/2026.

El diccionario ejecutable se genera con `python scripts/02_dataset.py` en
`docs/diccionario_datos.json`.

---

## 1. Red vial

La red se construye **exclusivamente con datos reales de OpenStreetMap**
(Overpass API + OSMnx 2.1.1). No se inventan coordenadas: cada nodo y cada tramo
proviene de un objeto OSM identificado por su `id` global.

### 1.0 Procedimiento

| Paso | Acción | Resultado |
|---|---|---|
| 1 | Descarga del corredor en 10 bandas solapadas (`corredor_bNN.osm`) | Vías `motorway`…`secondary` entre Lima y Chulucanas |
| 2 | Descarga radial de la red urbana de las 6 ciudades (`ciudad_XX.osm`) | Calles de acceso a los distribuidores |
| 3 | Composición (`nx.compose`) y retención de la componente débil mayor | Un único grafo conexo |
| 4 | Filtro de red de carga (jerarquía ≥ `tertiary`) | Se eliminan calles residenciales y de servicio |
| 5 | Consolidación (contracción de cadenas de grado 2) | Nodos de decisión + tramos |
| 6 | Ubicación de ciudades y 17 distribuidores sobre nodos OSM reales | `ciudades.csv`, `distribuidores.csv` |

**Por qué bandas y no una sola consulta:** Overpass no interpreta una lista
larga de coordenadas como polilínea en `around` (usa solo el primer punto). Una
consulta única habría descargado únicamente el área de Lima.

**Por qué coincidencias exactas de `highway`:** las expresiones regulares obligan
a Overpass a recorrer todas las vías del recuadro; los servidores públicos
responden 500/504 por agotamiento de memoria. La unión de igualdades
(`["highway"="primary"]`, etc.) se resuelve por índice en segundos.

### 1.1 `nodos.csv`

| Campo | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `id_nodo` | int64 | ID del nodo OSM | `370432187` |
| `lat` | float | Latitud WGS84 (atributo `y` de OSM) | `-8.1284` |
| `lon` | float | Longitud WGS84 (atributo `x` de OSM) | `-79.0312` |
| `grado` | int | Vías distintas que inciden en el nodo | `4` |
| `es_decision` | bool | `true` si permite elegir entre ≥ 2 tramos (grado ≠ 2) o es nodo logístico | `true` |
| `tipo` | enum | `hub` \| `ciudad` \| `distribuidor` \| `logistico` \| `interseccion` | `distribuidor` |
| `nombre` | string | Nombre del nodo logístico (`"TRU-D03 · Trujillo"`); vacío en intersecciones | `Trujillo` |
| `ciudad_id` | string | Ciudad a la que pertenece (vacío en nodos de paso) | `TRU` |

### 1.2 `tramos.csv`

| Campo | Tipo | Descripción |
|---|---|---|
| `id_tramo` | string | Identificador del tramo consolidado (`TR-00042`) |
| `id_nodo_ini` / `id_nodo_fin` | int64 | Nodos extremos (IDs OSM) |
| `n_nodos_geometria` | int | Cuántos nodos OSM contiene la geometría del tramo |
| `camino_nodos` | string | Secuencia completa de IDs OSM separada por `;` (solo en memoria; se omite del CSV para no inflarlo) |
| `distancia_km` | float | Longitud real sumada de las aristas OSM |
| `tiempo_flujo_min` | float | Tiempo de recorrido a velocidad de flujo libre |
| `velocidad_flujo_kmh` | float | `distancia_km / (tiempo_flujo_min / 60)` |
| `tipo_via` | enum | Jerarquía dominante: `motorway` \| `trunk` \| `primary` \| `secondary` \| `tertiary` |
| `nombre_via` | string | Hasta 3 nombres OSM de la vía (`"Panamericana Norte / PE-1N"`) |
| `peaje` | bool | Si alguna arista del tramo está marcada como `toll` |
| `puente` / `tunel` | bool | Si el tramo contiene un puente o túnel |
| `bidireccional` | bool | Si se modela en ambos sentidos (por defecto `true`) |
| `es_alternativa` | bool | Reservado (vía paralela sintética); hoy siempre `false` |
| `tiene_alterna` | bool | `false` solo si el tramo es **arista de corte**: quitarlo desconecta sus extremos, así que no existe ningún desvío que lo rodee. Habilita la acción «tomar vía alterna» (95.4 % de los tramos) |
| `zona` | string | Ciudad del corredor más cercana al punto medio (`LIM`, `TRU`, …) |

**Regla de consolidación:** se contraen las cadenas de nodos de grado 2 (puntos
de curvatura) y se conservan como extremos los nodos donde realmente se puede
decidir (grado ≠ 2) más los nodos logísticos. Así un tramo puede abarcar varios
kilómetros de vía continua con su geometría real.

### 1.3 `corredor_macro.csv` — tramos completos

Agregación de la topología entre ciudades consecutivas del corredor (lo que
Wilson pidió como *"armar un tramo completo, de Lima a Piura"*):

| Campo | Descripción |
|---|---|
| `id_macro` | `LIM-TRU`, `TRU-CHI`, … |
| `origen` / `destino` | Nombres de ciudad |
| `id_nodo_origen` / `id_nodo_destino` | Nodos OSM de los extremos |
| `distancia_km` | Ruta más corta por distancia sobre la topología |
| `tiempo_min` | Tiempo de flujo libre acumulado |
| `n_tramos` | Cuántos tramos elementales componen el tramo completo |
| `tramos_con_peaje` | Número de tramos con peaje |
| `tipo_via_dominante` | Jerarquía predominante del recorrido |
| `secuencia_tramos` | IDs de los tramos elementales en orden (primeros 200) |

### 1.4 `ciudades.csv` y `distribuidores.csv`

| Archivo | Campos |
|---|---|
| `ciudades.csv` | `id_ciudad`, `nombre`, `id_nodo`, `lat`, `lon`, `rol`, `distancia_snap_m`, `distribuidores` |
| `distribuidores.csv` | `id_distribuidor` (`TRU-D01`), `id_nodo`, `ciudad_id`, `ciudad`, `lat`, `lon`, `orden` |

**Ubicación de los distribuidores:** se toman los nodos OSM dentro del radio
urbano de cada ciudad y se eligen los mejor repartidos con *farthest point
sampling* (semilla fija). No son coordenadas inventadas: cada distribuidor está
sobre una intersección real de la red. Cuando Wilson entregue el maestro de
direcciones, se sustituyen por el nodo OSM más cercano a cada dirección real
(`tolerancia_snap_m: 800`).

### 1.5 Atributos derivados para el modelo

| Atributo | Fórmula | Uso |
|---|---|---|
| `tiempo_estimado` | `tiempo_flujo_min × Π severidad_eventos_activos` | Peso de Dijkstra y observación |
| `costo_tramo` | `km × (combustible + peaje + mantenimiento) + horas × costo_conductor` | Función de recompensa |

---

## 2. Entidades operativas

### 2.1 `conductores`
`id_conductor`, `licencia_categoria` (A-II/A-IIIa/A-IIIb), `anios_experiencia`,
`hora_inicio_jornada`, `horas_max_conduccion`, `descanso_obligatorio_min`,
`zona_base`.

### 2.2 `vehiculos`
`id_vehiculo` (placa anonimizada), `tipo_vehiculo`, `capacidad_t`,
`capacidad_m3`, `velocidad_max_kmh`, `consumo_km_l`, `anio_fabricacion`, `estado`.

### 2.3 `jornadas`
`id_jornada`, `id_conductor`, `id_vehiculo`, `fecha`, `hora_inicio`, `hora_fin`,
`horas_conduccion`, `horas_descanso`, `cumple_regulacion`.

### 2.4 `puntos_descanso`
`id_punto`, `nombre`, `lat`, `lon`, `km_corredor`, `tiene_servicios`.

### 2.5 `clientes`
`id_cliente`, `id_nodo_entrega`, `ciudad`, `ventana_inicio`, `ventana_fin`
(hora decimal), `prioridad` (1 = alta).

### 2.6 `cargas`
`id_carga`, `tipo_carga`, `peso_t`, `volumen_m3`, `requiere_refrigeracion`,
`fragilidad` (baja/media/alta).

### 2.7 `viajes`
`id_viaje`, `id_vehiculo`, `id_conductor`, `id_nodo_origen`, `id_nodo_destino`,
`fecha_planificada`, `hora_salida_plan`, `hora_llegada_plan`,
`distancia_plan_km`, `costo_plan_pen`, `estado`.

### 2.8 `detalle_carga`
`id_detalle`, `id_viaje`, `id_carga`, `id_cliente`, `orden_entrega`.

### 2.9 `gastos_plan`
`id_gasto`, `id_viaje`, `concepto` (combustible/peaje/mantenimiento/conductor),
`monto_pen`, `km_asociados`, `horas_asociadas`.

### 2.10 `eventos_base`
`id_evento`, `id_tramo`, `tipo_evento` (congestion/bloqueo/clima/averia),
`hora_inicio`, `hora_fin`, `severidad`, `fuente` (sutran/mtc/simulador),
`descripcion`, `zona`.

---

## 3. Unidad de análisis (Tabla 4 del plan de tesis)

Cada fila del registro de decisiones representa **una medición estructurada de
la ruta operativa en un instante**:

| Campo | Origen |
|---|---|
| `id_viaje` | `viajes` |
| `id_agente` | entorno (`AG-0..AG-n`) |
| `hora` | reloj del simulador |
| `id_nodo` | posición actual |
| `id_tramo_actual` | tramo en curso |
| `accion` / `accion_nombre` | decisión (0 continuar, 1 alterna, 2 esperar, 3 replanificar) |
| `recompensa` | función de recompensa |
| `estado_resumen` | vector de observación serializado |

---

## 4. Calidad de datos (H1)

Resultado de `src/red_vial/validar_red.py` sobre la red construida:

| Indicador | Umbral H1 | Valor actual |
|---|---|---|
| Completitud de atributos | ≥ 90 % | **100 %** |
| Consistencia referencial (tramos → nodos) | ≥ 95 % | **100 %** |
| Duplicados de `id_nodo` / `id_tramo` | 0 | **0** |
| Valores no positivos (distancia, tiempo, velocidad) | 0 | **0** |
| Coherencia `distancia / tiempo = velocidad` | 0 desvíos | **0** |
| Representatividad de escenarios | ≥ 80 % | **6 ciudades / 17 distribuidores** |
| Snap ciudad → red | ≤ 800 m | **máx. 227.5 m** |
| Grafo débilmente conexo | sí | **sí** |
| Alcanzabilidad hub → distribuidor | 17/17 | **17/17** |

La validación se ejecuta automáticamente al final de `scripts/01_red_vial.py` y
su resultado queda registrado en `resumen.json`.

### 4.1 Validez externa de la red

La red se construyó **sin** usar ningún motor de ruteo: solo con las geometrías
de OSM. Contrastarla después con OSRM es, por tanto, una prueba independiente:

| Tramo | Red construida | OSRM | Desvío |
|---|---|---|---|
| Lima → Trujillo | 545.4 km | 557.1 km | −2.1 % |
| Trujillo → Chiclayo | 202.8 km | 199.5 km | +1.7 % |
| Chiclayo → Piura | 212.7 km | 214.7 km | −0.9 % |
| Piura → Sullana | 39.1 km | 38.8 km | +0.8 % |
| Piura → Chulucanas | 61.5 km | 60.8 km | +1.2 % |
| **Total** | **1 061.5 km** | **1 070.9 km** | **−0.9 %** |

### 4.2 Rutas alternas

El plan de tesis incluye la acción **tomar vía alterna**, por lo que la red debe
admitir desvíos. Un tramo tiene alternativa si no es arista de corte (su
eliminación no desconecta sus extremos):

| Indicador | Valor |
|---|---|
| Tramos con desvío disponible | 27 016 / 28 312 (**95.4 %**) |
| Tramos de corte | 1 296 (4.6 %) |
| Tramo óptimo Lima → Piura | 978.3 km, 514 tramos |
| … de los cuales sin alternativa | 27 tramos, 89.0 km (**9.1 %**) |

Los tramos de corte son cruces de río y pasos obligados del corredor; en el 91 %
restante del recorrido existe un rodeo real.

### 4.3 Limitaciones declaradas

* **2 280 tramos `trunk` y 532 `motorway`** corresponden a la red de mayor
  jerarquía; el resto son vías primarias, secundarias y terciarias urbanas.
* **1 047 nodos con grado < 2** son fondos de saco en el borde de las zonas de
  descarga. No afectan el ruteo entre ciudades.
* Los **tiempos** son de flujo libre (velocidad legal o `maxspeed` de OSM); la
  congestión se incorpora como evento dinámico en el simulador, no en la red.
* El atributo **`peaje`** se toma de la etiqueta `toll=yes` de OSM. OSMnx no la
  conserva por defecto (`useful_tags_way`), por lo que se añade explícitamente
  al importar `src.red_vial.descargar_osm`.
