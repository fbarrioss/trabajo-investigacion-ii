# MARL-Rutas Carga — Corredor Norte del Perú

Artefacto de software para la tesis **"Sistema multiagente basado en aprendizaje
por refuerzo para la planificación y monitoreo de rutas operativas en el
transporte de carga"** (UNI – FIIS, Grupo 8).

Este repositorio contiene **todo lo necesario para entrenar el modelo**:

| Componente | Qué resuelve | Estado |
|---|---|---|
| `src/red_vial/` | Nodos y tramos del corredor Lima → Chulucanas (tarea de Franklin) | ✅ |
| `src/datos/` | Dataset operativo: vehículos, conductores, viajes, cargas, eventos | ✅ |
| `src/entorno/` | Simulador Dec-POMDP con eventos dinámicos | ✅ |
| `src/agentes/` | QMIX, MAPPO y baselines (Dijkstra, VRP, estático) | ✅ |
| `src/entrenamiento/` | Bucles de entrenamiento y evaluación estadística | ✅ |
| `docs/` | Diccionario de datos, modelo MDP/MARL, selección de algoritmo | ✅ |

---

## 1. Instalación

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
```

> En Windows, PyTorch CPU se instala más rápido con:
> `pip install torch --index-url https://download.pytorch.org/whl/cpu`

## 2. Pipeline completo

```bash
# 1) Red vial real (OpenStreetMap) + topología de nodos
python scripts/01_red_vial.py
python scripts/01_red_vial.py --nivel primary     # solo vías principales
python scripts/01_red_vial.py --recalcular        # ignora la caché del grafo

# 2) Dataset operativo + eventos calibrados
python scripts/02_dataset.py --viajes 180

# 2b) Verificación de que el simulador recorre la red real (no entrena)
python scripts/05_diagnostico_movimiento.py

# 2c) Dashboard HTML de la red (autocontenido)
python dashboard/build_dashboard.py

# 3) Entrenamiento
python scripts/03_entrenar.py --algoritmo qmix  --episodios 2000 --semillas 1 2 3
python scripts/03_entrenar.py --algoritmo mappo --episodios 2000 --semillas 1

# 4) Evaluación contra baselines
python scripts/04_evaluar.py --modelo artifacts/modelos/qmix_s1.pt --episodios 30
```

### Descarga de la red (Overpass)

```bash
# Estado de la caché local
python -m src.red_vial.descargar_osm estado

# Red urbana de una o varias ciudades (útil si Overpass está saturado)
python -m src.red_vial.descargar_osm LIM,PIU,SUL,CHU

# Corredor completo (10 bandas) + red urbana de las 6 ciudades
python -m src.red_vial.descargar_osm todo
```

La descarga usa **rotación de espejos** de Overpass y coincidencias exactas de
`highway` (las expresiones regulares agotan la memoria de los servidores
públicos y devuelven 500/504).

## 3. Estructura

```
config/
  escenario_norte.yaml     # corredor, ciudades, distribuidores, costos, eventos
  entrenamiento.yaml       # hiperparámetros, recompensa, métricas
src/
  comun/                   # configuración y utilidades geográficas
  red_vial/                # construcción y validación de la red
  datos/                   # esquema entidad-relación y generación de datos
  entorno/                 # simulador multiagente
  agentes/                 # QMIX, MAPPO, baselines
  entrenamiento/           # entrenar.py, evaluar.py
  metricas.py              # métricas del plan de tesis
scripts/                   # CLI de las 4 etapas
docs/                      # documentación metodológica
data/                      # raw / interim / processed
artifacts/                 # modelos, logs y resultados
```

## 4. Alcance del escenario

Definido en la reunión del **16/10/2026** a partir de la disponibilidad real de
datos de la empresa:

| Ciudad | Distribuidores |
|---|---|
| Lima (hub de origen) | — |
| Trujillo | 7 |
| Chiclayo | 3 |
| Piura | 3 |
| Sullana | 2 |
| Chulucanas | 2 |
| **Total** | **17** |

Corredor: Panamericana Norte (PE-1N) + alineamiento costero alterno
Chiclayo–Piura + ramal Piura → Chulucanas.

### 4.1 Red vial construida (OpenStreetMap)

`python scripts/01_red_vial.py` produce, a partir de datos reales de OSM:

| Indicador | Valor |
|---|---|
| Nodos operativos (extremos de tramo) | **13 918** |
| Nodos OSM procesados | 248 723 |
| Nodos de geometría contraídos | 234 781 |
| Tramos consolidados | **21 918** |
| Longitud total de la red | 10 547.9 km |
| Distribuidores ubicados | **17 / 17** |
| Grafo débilmente conexo | sí |
| Desvío máximo de snap ciudad → red | 76.9 m |
| Completitud de atributos | 100 % |
| Tramos con desvío alterno (`tiene_alterna`) | **94.9 %** |

**Tramos completos (macro-tramos)** — contraste con un motor de ruteo
independiente (OSRM, perfil `driving`):

| Tramo | Red construida | OSRM | Desvío |
|---|---|---|---|
| Lima → Trujillo | 545.4 km | 557.1 km | −2.1 % |
| Trujillo → Chiclayo | 202.8 km | 199.5 km | +1.7 % |
| Chiclayo → Piura | 212.7 km | 214.7 km | −0.9 % |
| Piura → Sullana | 39.1 km | 38.8 km | +0.8 % |
| Piura → Chulucanas | 61.5 km | 60.8 km | +1.2 % |
| **Total** | **1 061.5 km** | **1 070.9 km** | **−0.9 %** |

Esta comparación es la evidencia de validez de la red: las distancias se
reconstruyen con un error menor al 2 % sin usar OSRM para construirla.

### 4.2 Rutas alternas (soporte de la acción «tomar vía alterna»)

El espacio de acciones del agente incluye **tomar una vía alterna**, así que la
red debe admitir desvíos reales. Se marca cada tramo con `tiene_alterna`: es
`False` solo si el tramo es **arista de corte** (quitarlo desconecta sus
extremos), es decir, si no existe ninguna ruta que lo rodee.

| Indicador | Valor |
|---|---|
| Tramos con desvío disponible | **20 798 / 21 918 (94.9 %)** |
| Tramos de corte (sin alternativa) | 1 120 (5.1 %) |
| Longitud sin alternativa | ≈ 10 % del corredor |

Sobre la ruta óptima Lima → Piura (985.6 km, 516 tramos) solo **26 tramos
(88.6 km, 9 %)** son de corte: son cruces de río y pasos obligados. En el 91 %
restante del recorrido el vehículo puede rodear un tramo bloqueado, de modo que
la acción «tomar alterna» tiene efecto medible (encarece la ruta y alarga el
recorrido) y no es una acción muerta.

### 4.3 Archivos generados

`data/processed/red_vial/`

| Archivo | Contenido |
|---|---|
| `nodos.csv` | 13 918 nodos operativos (ID OSM, lat, lon, grado, tipo) |
| `tramos.csv` | 21 918 tramos (distancia, tiempo, velocidad, tipo de vía, peaje, `tiene_alterna`) |
| `corredor_macro.csv` | Tramos completos entre ciudades |
| `ciudades.csv` | 6 ciudades con su nodo OSM y desvío de snap |
| `distribuidores.csv` | 17 distribuidores sobre intersecciones reales |
| `topologia.graphml` | Grafo dirigido para el simulador |
| `red_vial.geojson` | Trazas reales para QGIS / visualización web |
| `mapa_red.png` | Mapa del corredor completo |
| `mapa_ciudades.png` | Detalle urbano con la ubicación de cada distribuidor |
| `resumen.json` | Métricas, macro-tramos y resultado de la validación |

## 5. Verificación de la simulación sobre la red real

`python scripts/05_diagnostico_movimiento.py` ejecuta un episodio con la política
trivial «continuar» y comprueba que los vehículos **recorren realmente** la
topología OSM (no entrena nada):

| Verificación | Resultado |
|---|---|
| Viaje Lima → Trujillo | 553.6 km en ≈ 7.5 h de conducción |
| Viaje Lima → Piura (corredor completo) | 1 004.0 km dentro del horizonte de 60 h |
| Velocidad media en Panamericana | 84–90 km/h por paso de 15 min |
| Descanso de jornada | 45 min exactos cada 5 h de conducción |
| Entregas registradas | 7 / 7 en el escenario de prueba |
| Distancia contabilizada | exacta (el remanente de cada paso se arrastra como `credito_km`) |

Las cuatro acciones producen resultados distintos y coherentes con el plan de
tesis:

| Acción | Efecto medido |
|---|---|
| 0 · continuar | ruta planificada, 1 004 km, recompensa −4.8 |
| 1 · tomar alterna | desvío real, 1 216 km, recompensa −11.5 |
| 2 · esperar | 9.9 km recorridos, 239 esperas, recompensa −88.2 |
| 3 · replanificar | misma ruta que 0 (sin eventos), recompensa −4.9 |

## 6. Dashboard de la red

`dashboard/index.html` es un visor autocontenido (3.8 MB, sin servidor) con los
21 918 tramos embebidos. Se regenera con:

```bash
python dashboard/build_dashboard.py
```

Incluye mapa con jerarquía vial por color, búsqueda por ID o nombre de vía,
filtros por zona y por disponibilidad de desvío, ficha técnica de cada tramo,
trazado de los 5 tramos completos y dos pestañas con la metodología y la
validación. Acepta enlaces directos: `index.html#TR-03909`, `#metodo`, `#valida`.

## 7. Reproducibilidad

Todas las fuentes de aleatoriedad derivan de `semilla` (config). Los resultados
de la evaluación se guardan en `artifacts/evaluacion/` con media, desviación
estándar, IC 95 % y contraste de Mann-Whitney contra la política estática.

## 8. Estado de los datos

* **Red vial**: geometría y topología **100 % reales de OpenStreetMap**
  (IDs OSM, distancias y tiempos calculados sobre las aristas reales). Validada
  contra OSRM con un error menor al 2 %. Ver `docs/01_diccionario_datos.md`.
* **Distribuidores**: los 17 puntos están sobre **intersecciones reales** de la
  red, elegidos con *farthest point sampling* dentro del área urbana de cada
  ciudad. Al recibir el maestro de direcciones de Wilson se reemplazan por el
  nodo OSM más cercano a cada dirección (`tolerancia_snap_m: 800`).
* **Operación**: datos sintéticos calibrados con las entidades y volúmenes
  declarados por la empresa (3 unidades/día). Los datos reales se incorporan
  sustituyendo los Parquet de `data/processed/dataset/`.

## 9. Reconstruir la red desde cero

```bash
rm -rf data/raw/osm                      # borra la caché de OSM
python -m src.red_vial.descargar_osm todo   # 10 bandas + 6 ciudades (~20 min)
python scripts/01_red_vial.py               # topología + validación (~1.5 min)
```

La descarga es reanudable: cada banda y cada ciudad se cachean por separado, de
modo que si Overpass se satura basta con volver a lanzar el comando.
