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
python scripts/01_red_vial.py --reconstruir       # reparsea el .osm (trae hgv/maxweight)

# 2) Dataset operativo + eventos calibrados
python scripts/02_dataset.py --viajes 180

# 2b) Verificación de que el simulador recorre la red real (no entrena)
python scripts/05_diagnostico_movimiento.py

# 2c) Dashboard HTML de la red (autocontenido)
python dashboard/build_dashboard.py

# 2d) Benchmark con los viajes reales de la empresa
#     (lee puntos_entrega.xlsx y el libro de viajes desde ~/Downloads)
python scripts/06_integrar_viajes.py
python scripts/07_cobertura_muestra.py    # marco muestral de ese extracto
python scripts/08_rutas_valhalla.py        # rutas de referencia con perfil camión (requiere Valhalla)
python scripts/09_alternativas.py          # 7 principales + 14 alternativas sobre la red propia

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

> `--recalcular` rehace el grafo desde el `.graphml` cacheado; `--reconstruir`
> vuelve a parsear el `.osm` original. El segundo es obligatorio cuando se
> añaden etiquetas nuevas a `useful_tags_way` (por ejemplo `hgv` y `maxweight`):
> el `.graphml` cacheado no las contiene y el resultado saldría sin
> restricciones de pesados.

### Motor de rutas (Valhalla)

Valhalla calcula la **ruta principal** de cada par OD con perfil de camión real
(`hgv`, `maxweight`, `maxheight`, `maxwidth`, `maxlength`, `axle_load`). Se usa
**fuera** del bucle de aprendizaje: si eligiera la ruta en cada paso, el agente
dejaría de tener sentido — el agente aprende *cuándo* desviarse, no *por dónde*
ir. Detalle, comparativa con OSRM/GraphHopper y resultados medidos en
[`docs/07_motor_rutas.md`](docs/07_motor_rutas.md).

#### 1. Requisitos

- **Docker Desktop** en marcha (en Windows, con backend WSL2).
- **8 GB de RAM** disponibles para el contenedor. Por debajo de 4 GB la
  construcción de tiles muere sin mensaje claro.
- **~2,5 GB de disco** para los tiles de Perú, más 257 MB del PBF.
- Puerto **8002** libre.

#### 2. Instalación

```bash
# 1) Extracto OSM de Perú (una sola vez, 257 MB, desde Geofabrik)
curl -L -o data/raw/pbf/peru-latest.osm.pbf \
  https://download.geofabrik.de/south-america/peru-latest.osm.pbf

# 2) La imagen solo detecta *.pbf dentro de /custom_files: hay que copiarlo ahí
cp data/raw/pbf/peru-latest.osm.pbf data/raw/valhalla/

# 3) Levantar el contenedor
cd infra/valhalla
docker compose up -d

# 4) Seguir la construcción de tiles (la primera vez tarda 20-40 min)
docker compose logs -f

# 5) Comprobar que responde
curl http://localhost:8002/status
```

Cuando termina, `/status` devuelve algo así:

```json
{"version":"3.5.1","tileset_last_modified":1793402470,
 "available_actions":["status","route","optimized_route","sources_to_targets","trace_route", ...]}
```

Los tiles quedan en `data/raw/valhalla/` (ignorado por git). **Las siguientes
veces el arranque es inmediato**: no se reconstruyen salvo que cambie el PBF o
se pida `force_rebuild=True`.

```bash
docker compose down              # detener
docker compose restart           # reiniciar (no reconstruye tiles)
docker stats valhalla-peru       # consumo durante la construcción

# forzar una reconstrucción completa de los tiles
rm -rf data/raw/valhalla/valhalla_tiles data/raw/valhalla/valhalla_tiles.tar
docker compose up -d
```

#### 3. Calcular las rutas principales

```bash
python scripts/08_rutas_valhalla.py
```

Recorre los 7 pares OD del escenario con perfil camión y escribe en
`data/processed/valhalla/`:

| Archivo | Contenido |
|---|---|
| `rutas_od.csv` | km y min del motor, km de la red propia, desvío porcentual, km/min de cada alternativa |
| `rutas_od.json` | Lo mismo con los parámetros usados, para trazabilidad |
| `rutas_od.geojson` | Trazas de la ruta principal y de cada alternativa, listas para QGIS |
| `sensibilidad_peso.csv` | Barrido peso × par OD (solo con `--sensibilidad-peso`) |

Opciones:

```bash
python scripts/08_rutas_valhalla.py --alternativas 3 --peso-t 36   # perfil más pesado
python scripts/08_rutas_valhalla.py --comparar-auto                # añade el perfil auto
python scripts/08_rutas_valhalla.py --sensibilidad-peso            # barre el peso bruto
python scripts/08_rutas_valhalla.py --pesos 30,36,42,48            # pesos del barrido
python scripts/08_rutas_valhalla.py --url http://localhost:8002    # otro servidor
```

- `--comparar-auto` separa el error de topología del efecto de las restricciones
  de pesados: el desvío medio contra `auto` es **+0,73 %**, contra `truck`
  **+2,25 %**, y la diferencia son los tramos con `hgv=no` de la salida de Lima.
- `--sensibilidad-peso` localiza el umbral que rompe el corredor: LIM–TRU son
  609,6 km hasta 36 t y **953,1 km a partir de 37 t** (+56 %).

Parámetros del perfil: peso bruto 36 t, alto 4,1 m, ancho 2,6 m, largo 18 m
(articulado peruano típico); ajustables con `--peso-t`, `--alto-m`, `--ancho-m`,
`--largo-m`.

#### 4. Calcular las alternativas

Valhalla devuelve alternativas en solo 2 de 7 pares con perfil camión, y una
traza suya es geometría, no una secuencia de tramos que el simulador pueda
ejecutar. Para eso está el script 09, que **no necesita Docker**:

```bash
python scripts/09_alternativas.py                     # 7 principales + 14 alternativas
python scripts/09_alternativas.py --k 4               # hasta 4 rutas por par
python scripts/09_alternativas.py --peso-t 30 --alto-m 4.0
python scripts/09_alternativas.py --sin-bloqueo       # ignora límites físicos
```

Salidas en `data/processed/alternativas/`: `alternativas_od.csv` (con
`secuencia_tramos` de cada ruta) y `alternativas_od.geojson`.

> Valhalla solo hace falta para la línea de base externa y la validación
> independiente; las alternativas se calculan sin el contenedor levantado.

#### 5. Consultar la API a mano

```bash
curl -s http://localhost:8002/route -H 'Content-Type: application/json' -d '{
  "locations": [{"lat": -12.0464, "lon": -77.0428}, {"lat": -8.1116, "lon": -79.0287}],
  "costing": "truck",
  "costing_options": {"truck": {"height": 4.1, "width": 2.6, "length": 18.0, "weight": 36.0}},
  "units": "kilometers",
  "alternates": 2
}' | python -m json.tool
```

Documentación interactiva del servidor: <http://localhost:8002/docs>.

#### 6. Problemas comunes

| Síntoma | Causa | Solución |
|---|---|---|
| `curl: (3) URL rejected: No host part in the URL` y el contenedor reinicia en bucle | Se puso una ruta local en `tile_urls`, que solo acepta URLs | Copiar el PBF a `data/raw/valhalla/` y no definir `tile_urls` |
| `ERROR ... NOT NULL constraint failed: admin_access.admin_id` para Oman, Slovakia, Philippines… | El builder recorre su lista mundial de países; los que no están en un extracto de país no resuelven | **Ignorar**: el propio mensaje lo dice. El admin db sí se construye (*"Inserted 26 admin areas"*) |
| `short read: expected N bytes but got M: unexpected EOF` al hacer `docker pull` | Corte de red al bajar un layer | Reintentar; los layers ya bajados se reutilizan |
| El contenedor se reinicia cada pocos segundos | Casi siempre es el primer caso de esta tabla | `docker logs valhalla-peru` |
| `Error: bind for 0.0.0.0:8002 failed` | Puerto ocupado | Cambiar el mapeo en `infra/valhalla/docker-compose.yml` y pasar `--url` |
| `/status` responde 503 durante mucho rato | Los tiles siguen construyéndose | `docker compose logs -f` y esperar |

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
scripts/                   # CLI del pipeline (00 extracto → 09 alternativas)
docs/                      # documentación metodológica
infra/valhalla/            # docker-compose del motor de rutas
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
| Nodos operativos (extremos de tramo) | **18 167** |
| Nodos OSM procesados | 339 863 |
| Nodos de geometría contraídos | 321 998 |
| Tramos consolidados | **28 642** |
| Longitud total de la red | 14 017.1 km |
| Ciudades asociadas | **8** (6 del corredor + Huancayo y Paita) |
| Distribuidores ubicados | **17 / 17** |
| Grafo débilmente conexo | sí |
| Desvío máximo de snap ciudad → red | 227.5 m (Paita) |
| Completitud de atributos | 100 % |
| Tramos con desvío alterno (`tiene_alterna`) | **95.4 %** |
| Tramos unidireccionales | **23 567 (82.3 %)** |
| Tramos con restricción de pesados | **206** (102.4 km) |

> Además del corredor norte, la red cubre la **Carretera Central a Huancayo**, el
> **ramal Piura–Paita** y el **acceso al CEDI de Lima**, necesarios para
> reconstruir el 100 % de los viajes registrados. Ver
> [`docs/06_cobertura_extendida.md`](docs/06_cobertura_extendida.md).

**Tramos completos (macro-tramos)** — contraste con un motor de ruteo
independiente (OSRM, perfil `driving`):

| Tramo | Red construida | OSRM | Desvío |
|---|---|---|---|
| Lima → Trujillo | 547.6 km | 557.1 km | −1.7 % |
| Trujillo → Chiclayo | 203.2 km | 199.5 km | +1.9 % |
| Chiclayo → Piura | 212.8 km | 214.7 km | −0.9 % |
| Piura → Sullana | 39.3 km | 38.8 km | +1.3 % |
| Piura → Chulucanas | 61.6 km | 60.8 km | +1.3 % |
| **Total** | **1 064.5 km** | **1 070.9 km** | **−0.6 %** |

Esta comparación es la evidencia de validez de la red: las distancias se
reconstruyen con un error menor al 2 % sin usar OSRM para construirla.

### 4.2 Rutas alternas (soporte de la acción «tomar vía alterna»)

El espacio de acciones del agente incluye **tomar una vía alterna**, así que la
red debe admitir desvíos reales. Se marca cada tramo con `tiene_alterna`: es
`False` solo si el tramo es **arista de corte** (quitarlo desconecta sus
extremos), es decir, si no existe ninguna ruta que lo rodee.

| Indicador | Valor |
|---|---|
| Tramos con desvío disponible | **27 314 / 28 642 (95.4 %)** |
| Tramos de corte (sin alternativa) | 1 328 (4.6 %) |
| Longitud sin alternativa | 4 398.7 km (31.4 %) |

La longitud sin alternativa es alta porque el 100 % de los ejes de un solo
carril del ámbito son aristas de corte: la Carretera Central a Huancayo, el
ramal a Paita y los accesos a Chulucanas y Tambo Grande. Sobre la **ruta óptima
Lima → Piura** (963.6 km, 591 tramos) solo **27 tramos (89.0 km, 9.2 %)** son de
corte: son cruces de río y pasos obligados. En el 91 % restante del recorrido el
vehículo puede rodear un tramo bloqueado, de modo que la acción «tomar alterna»
tiene efecto medible (encarece la ruta y alarga el recorrido) y no es una acción
muerta.

### 4.3 Archivos generados

`data/processed/red_vial/`

| Archivo | Contenido |
|---|---|
| `nodos.csv` | 18 167 nodos operativos (ID OSM, lat, lon, grado, tipo) |
| `tramos.csv` | 28 642 tramos (distancia, tiempo, velocidad, tipo de vía, peaje, `sentido`, `restriccion_pesados`, `tiene_alterna`) |
| `corredor_macro.csv` | Tramos completos entre ciudades |
| `ciudades.csv` | 8 ciudades con su nodo OSM y desvío de snap |
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
| Red cargada | 18 167 nodos, 28 642 tramos |
| Episodio | 3 agentes, 240 pasos de 15 min (60 h) |
| Kilómetros recorridos | 986.8 / 994.9 / 563.1 km por agente |
| Entregas completadas | 6 de 6, sin pendientes |
| Planes generados | 1 por agente (506, 499 y 278 nodos de ruta) |
| Esperas / replanificaciones | 0 / 0 con la política trivial «continuar» |
| Descanso de jornada | 45 min cada 5 h de conducción (regla del entorno) |
| Distancia contabilizada | exacta (el remanente de cada paso se arrastra como `credito_km`) |

Las cuatro acciones producen resultados distintos y coherentes con el plan de
tesis (`python scripts/05_diagnostico_movimiento.py --accion N`):

| Acción | Km recorridos (AG-0 / AG-1 / AG-2) | Entregas |
|---|---|---|
| 0 · continuar | 986.8 / 994.9 / 563.1 | 6 / 6 |
| 1 · tomar alterna | 1 007.0 / 1 024.1 / 582.3 (**+2.0 %**, desvío real) | 6 / 6 |
| 2 · esperar | 9.9 / 9.9 / 9.9 (239 esperas) | 0 / 6 |
| 3 · replanificar | 986.8 / 994.9 / 563.1 (12 replanificaciones) | 6 / 6 |

La acción «tomar alterna» no es una acción muerta: encarece la ruta un 2 % real
sobre el grafo OSM. La acción «esperar» agota el horizonte sin entregar, que es
el comportamiento esperado de una política que nunca avanza.

## 6. Dashboard de la red

`dashboard/index.html` es un visor autocontenido (5.37 MB, sin servidor) con los
28 642 tramos embebidos. Se regenera con:

```bash
python dashboard/build_dashboard.py
```

Incluye mapa con jerarquía vial por color, búsqueda por ID o nombre de vía,
filtros por zona y por disponibilidad de desvío, ficha técnica de cada tramo,
trazado de los 7 tramos completos (5 del corredor + 2 de las zonas de extensión)
y tres pestañas de lectura: **Modelo** (los tres requerimientos del asesor con su
evidencia numérica), **Metodología** y **Validación**. Acepta enlaces directos:
`index.html#TR-03909`, `#modelo`, `#metodo`, `#valida`.

El botón **GeoJSON** descarga cuatro capas distintas, según lo que se necesite:

| Capa | Archivo | Contenido |
|---|---|---|
| Red vial completa | `red_vial_ambito_completo.geojson` | 28 642 tramos con sentido de circulación y restricciones |
| Solo lo filtrado | `red_vial_filtrada.geojson` | los tramos visibles con los filtros activos (incluye los filtros aplicados en el archivo) |
| Ciudades y distribuidores | `nodos_logisticos.geojson` | 8 ciudades + 17 distribuidores como puntos |
| Tramos completos | `tramos_completos.geojson` | las 7 rutas macro con su secuencia de tramos |

Las coordenadas van en WGS84 (CRS84) simplificadas a 3 m para que el archivo
baje de ~40 MB a ~10 MB; la versión sin simplificar queda en
`data/processed/red_vial/red_vial.geojson`.

## 6.1 Publicar en Vercel

`dashboard/` es un sitio estático: `index.html` más `descargas/`, la carpeta con
los artefactos del benchmark que se bajan desde la pestaña Modelo.

```bash
python dashboard/build_dashboard.py   # genera index.html y copia descargas/
cd dashboard && vercel --prod         # despliega la carpeta como raíz
```

`vercel.json` fuerza `Content-Disposition: attachment` en `/descargas/*` para que
los CSV se descarguen en lugar de abrirse en el navegador.

Ojo: `index.html` está en `.gitignore` por peso (5.2 MB). Si conectas el
repositorio a Vercel en vez de desplegar por CLI, quita esa línea o el build no
encontrará el HTML.

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
