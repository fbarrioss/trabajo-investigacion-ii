# Cobertura extendida de la red vial

Documento de soporte del **universo de tramos** ampliado. Cierra los tres huecos
que dejaban viajes sin reconstruir sobre la red del corredor norte: la Carretera
Central (Huancayo), el ramal Piura–Paita y el acceso al CEDI de Lima.

## 1. Por qué se amplió

La red del corredor norte (Lima → Trujillo → Chiclayo → Piura → Sullana →
Chulucanas) cubría los 17 distribuidores del plan de tesis, pero **no** el total
de los viajes registrados por la empresa. Con esa red quedaban 4 de los 25
puntos fuera de cobertura y **187 viajes (37.4 %) sin reconstruir**:

| Punto | Ciudad | Distancia al nodo más cercano (red anterior) |
|---|---|---|
| GEO-019 | Paita | 21 257 m |
| GEO-023 / 024 / 025 | Huancayo | 175 488 / 176 642 / 175 893 m |
| CEDI (origen) | Sur de Lima | 12 239 m |

Como el objetivo del capítulo de resultados es comparar la política aprendida
contra **la operación manual completa**, la red debía cubrir el 100 % de los
puntos con datos reales.

## 2. Zonas añadidas

| Zona | Corredor | Recuadros | Vías | Nodos OSM |
|---|---|---|---|---|
| `central` | Carretera Central Lima–Chosica–La Oroya–Jauja–Huancayo | 6 | 9 325 | 128 349 |
| `paita` | PE-1N Piura–La Unión–La Legua–Paita | 1 | 829 | 12 213 |
| `acceso` | Acceso terminal al CEDI (cono sur de Lima) | 1 | 13 822 | 78 474 |
| `ciudad_HUY` | Red urbana de Huancayo / El Tambo | 1 | 5 745 | 30 226 |
| `ciudad_PAI` | Red urbana de Paita | 1 | 1 887 | 8 779 |

El corredor norte (10 bandas, 28 MB) y las 6 redes urbanas originales se
conservan sin cambios, de modo que los macro-tramos ya validados siguen dando
los mismos valores.

### 2.1 Alineamientos usados

* **Carretera Central (PE-22 / PE-3S)**: Lima −12.046,−77.043 → Ate → Chosica
  −11.936,−76.697 → San Bartolomé → Matucana → San Mateo → Casapalca →
  La Oroya −11.517,−75.900 → Jauja −11.783,−75.500 → Concepción → Huancayo
  −12.068,−75.210.
* **Ramal a Paita**: Piura −5.195,−80.633 → La Unión → La Legua → Paita
  −5.089,−81.114.
* **Acceso al CEDI**: Lima → San Juan de Miraflores → CEDI −12.2303,−76.9656.

## 3. Método de descarga

Se mantiene Overpass para el corredor norte (ya cacheado y validado) y se usa el
**extracto oficial de Geofabrik** para las zonas nuevas.

### 3.1 Overpass: límites encontrados

Tres hallazgos que quedaron documentados en el código:

1. **`maxsize` de 1 GB provoca rechazo inmediato.** Los espejos públicos
   responden `HTTP 502` en ~1 s a cualquier consulta que declare
   `[maxsize:1073741824]`, aunque el resultado sea de menos de 1 MB: la reserva
   se hace por adelantado y excede su cuota. Con `[maxsize:268435456]` (256 MB)
   la misma consulta responde en ~40 s.
2. **Los recuadros grandes en zonas densas agotan el tiempo del servidor.** El
   este de Lima tiene miles de vías secundarias; una banda de 68 × 65 km devuelve
   `504`. Se resuelve teselando el recuadro en celdas de ≤0,40°.
3. **Los espejos se saturan por horas.** Durante la sesión del 27/10/2026 el
   endpoint principal respondió «All query workers are busy» de forma
   intermitente.

### 3.2 Extracto de Geofabrik (vía principal para las zonas nuevas)

| Dato | Valor |
|---|---|
| Archivo | `peru-latest.osm.pbf` |
| Tamaño | 257 MB |
| Snapshot OSM | 2026-10-26 21:24 UTC |
| URL | `https://download.geofabrik.de/south-america/peru-latest.osm.pbf` |

`scripts/00_cortar_extracto.py` recorre el `.pbf` **una sola vez** con
`pyosmium` (160 s), reparte cada vía entre los recuadros de todas las zonas y
escribe archivos `.osm` con el mismo nombre y formato que produce el descargador
de Overpass. El resto del pipeline (OSMnx → topología) no cambia.

```bash
curl -L -o data/raw/pbf/peru-latest.osm.pbf \
  https://download.geofabrik.de/south-america/peru-latest.osm.pbf
python scripts/00_cortar_extracto.py        # zonas de extensión
python scripts/01_red_vial.py               # topología
python scripts/06_integrar_viajes.py        # benchmark
```

> **Nota de procedencia.** El corredor norte proviene de Overpass (16/10/2026) y
> las zonas nuevas del extracto de Geofabrik (26/10/2026). Ambos son el mismo
> dataset OSM y los identificadores de nodo son globales, por lo que la
> composición es consistente; la diferencia de un día no afecta a ninguna vía
> del ámbito.

## 4. Resultado de la topología

| Indicador | Antes (solo corredor norte) | Ahora (cobertura completa) |
|---|---|---|
| Nodos OSM procesados | 248 723 | **339 863** |
| Nodos operativos | 13 918 | **17 833** |
| Nodos de decisión | 13 905 | **17 806** |
| Tramos | 21 918 | **28 312** |
| Longitud total | 10 547.9 km | **14 019.6 km** |
| Ciudades | 6 | **8** (Huancayo y Paita como zonas de extensión) |
| Distribuidores | 17 | **17** (sin cambios) |
| Conexo | sí | **sí** |
| Completitud | 100 % | **100 %** |
| Tramos con desvío alterno | 94.9 % | **95.4 %** |

Los macro-tramos del corredor declarado **no cambian**, porque las zonas nuevas
no alteran la ruta óptima entre las seis ciudades del plan de tesis:

| Macro-tramo | Distancia | Tiempo |
|---|---|---|
| Lima → Trujillo | 545.4 km | 7.6 h |
| Trujillo → Chiclayo | 202.8 km | 2.7 h |
| Chiclayo → Piura | 212.7 km | 2.4 h |
| Piura → Sullana | 39.1 km | 0.5 h |
| Piura → Chulucanas | 61.5 km | 0.7 h |
| **Total** | **1 061.5 km** | **13.9 h** |

### 4.1 Nuevas conexiones verificadas

| Par | Distancia por red | Tiempo | Referencia |
|---|---|---|---|
| Lima → Huancayo | **303.3 km** | 5.0 h | ~300 km, 5–6 h por la Carretera Central |
| Piura → Paita | **57.7 km** | 0.6 h | ~57 km por la PE-1N |

## 5. Efecto sobre el benchmark

| Indicador | Antes | Ahora |
|---|---|---|
| Puntos fuera de cobertura | 4 de 25 | **0 de 25** |
| Viajes reconstruidos | 313 de 500 (62.6 %) | **500 de 500 (100 %)** |
| Viajes con error de ruta | 0 | **0** |
| Snap del CEDI | 12 239 m | **368 m** |
| Snap medio de los 25 puntos | 1 890 m | **242 m** (mediana 116 m) |
| Snap máximo | 176 642 m | **1 194.5 m** (GEO-010, Trujillo) |
| Km medio por red | 890.4 km | **830.8 km** |
| Km medio geodésico | 783.7 km | **704.2 km** |
| Ratio red / geodésica | 1.136 | **1.18** |
| Duración media | 661.5 min | **628 min (10.5 h)** |

El ratio red/geodésica sube de 1.136 a 1.18 porque ahora entran los viajes a
Huancayo, que recorren la Carretera Central: una vía de montaña con sinuosidad
muy superior a la Panamericana. Por parada, la distribución queda en
**mediana 1.15** y percentil 75 de 1.21, sin valores atípicos por encima de 2.5
salvo los tramos urbanos de un solo sentido.

## 6. Limitaciones que permanecen

1. **Pares con coordenada colapsada**: GEO-008/GEO-009 (Trujillo) y
   GEO-018/GEO-021 (Chulucanas) comparten coordenada en el archivo original, por
   lo que el tramo entre ellos mide 0 km (39 paradas). Se documenta como
   limitación del dato de origen, no del modelo de red.
2. **Acceso terminal del CEDI**: el depósito está sobre una vía local; el nodo
   de la red de carga más próximo queda a 368 m. Se acepta como acceso terminal.
3. **Sin GPS continuo**: la discretización sigue siendo por tramos con tiempos de
   flujo libre; la estancia observada (`llegada_real` → `fin_descarga`) se usa
   como servicio + espera.
4. **Zonas de extensión sin distribuidores**: Huancayo y Paita entran al
   escenario con `rol: zona_extension` y `distribuidores: 0`. No forman parte de
   los 17 distribuidores del plan de tesis; solo habilitan la reconstrucción de
   los viajes reales y el etiquetado de zona de sus tramos.
