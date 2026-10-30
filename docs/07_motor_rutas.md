# Motor de rutas: rutas principales y alternativas

Este documento fija la decisión de incorporar un motor de ruteo externo
(**Valhalla**, perfil `truck`) al pipeline, y sobre todo **dónde** se lo usa y
dónde no.

## 1. El problema que resuelve

La red consolidada ya calcula rutas mínimas con Dijkstra, y lo hace bien: el
contraste con OSRM da un desvío de −0,6 % en el total del corredor
(`docs/01_diccionario_datos.md` §4.1). Lo que Dijkstra no da es:

1. una **referencia externa** con perfil de camión real, contra la que comparar
   la política aprendida;
2. un conjunto de **alternativas de calidad**, es decir rutas que se separan de
   la principal sin ser un rodeo absurdo;
3. respeto nativo de las **restricciones de pesados** (`hgv`, `maxweight`,
   `maxheight`), que es justo lo que se incorporó a la red en la corrección de
   octubre de 2026.

## 2. Dónde va el motor (y dónde no)

**No va dentro del bucle de aprendizaje.** Si el motor eligiera la ruta en cada
paso, el agente dejaría de tener sentido: el problema se reduciría a ejecutar un
planificador. El agente debe aprender *cuándo* desviarse, no *por dónde* ir.

Va **fuera**, en tres papeles:

| Papel | Qué produce | Dónde se usa |
|---|---|---|
| **Línea de base** | Ruta de referencia por par OD con perfil camión | Comparación contra la política MARL (lo que hoy falta en el bloque 2 del dashboard) |
| **Generación de candidatas** | K alternativas reales por par OD | Definen el conjunto de caminos sobre los que el agente decide; validan `tiene_alterna` contra un tercero |
| **Validación independiente** | Distancia y tiempo por un motor ajeno a la construcción | Prueba de que la topología derivada de OSM es razonable |

## 3. Elección del motor

| Criterio | OSRM | **Valhalla** | GraphHopper |
|---|---|---|---|
| Perfil camión | requiere Lua a mano | **nativo** (`truck`: `max_weight`, `max_height`, `max_width`, `max_length`, `axle_load`, `hazmat`) | vía *custom model* |
| Alternativas | `alternatives` limitado; suelen solaparse | **`alternates` con control de solapamiento** | `alternative_route` con `max_weight_factor` / `max_share_factor` |
| Costo propio | difícil | `costing_options` | **el más flexible** (penalización por arista) |
| Operación | muy ligero | media (Docker) | Java, la más pesada |
| Entrada | PBF | PBF | PBF / OSM |

**Decisión: Valhalla.** Resuelve las dos cosas que se necesitan en una sola
llamada —perfil de pesados real y K alternativas— y se construye desde el PBF que
ya está en el proyecto (`data/raw/pbf/peru-latest.osm.pbf`, 257 MB). GraphHopper
queda como alternativa si más adelante se quiere inyectar un costo propio (riesgo,
congestión). OSRM se mantiene únicamente como validador de distancias, papel que
ya cumple.

## 4. Puesta en marcha

```bash
cp data/raw/pbf/peru-latest.osm.pbf data/raw/valhalla/   # una sola vez
cd infra/valhalla
docker compose up -d          # la primera vez construye los tiles (~20-40 min)
docker compose logs -f        # seguir el avance
```

Los tiles se escriben en `data/raw/valhalla/` (ignorado por git). El servidor
queda en `http://localhost:8002`, con documentación interactiva en `/docs`.

> **El PBF debe estar dentro de `/custom_files`.** La imagen solo busca `*.pbf`
> en ese directorio; `tile_urls` sirve exclusivamente para descargar desde una
> URL, no para apuntar a una ruta local (si se le pasa una ruta, `curl` falla
> con *"URL rejected: No host part in the URL"*).

Requisitos: Docker con al menos **4 GB** libres para la construcción inicial de
Perú; 8 GB es lo cómodo.

## 5. Uso

```bash
python scripts/08_rutas_valhalla.py                     # 7 pares OD, 2 alternativas
python scripts/08_rutas_valhalla.py --alternativas 3 --peso-t 48
python scripts/08_rutas_valhalla.py --comparar-auto --sensibilidad-peso
```

Parámetros del perfil: peso bruto 36 t, alto 4,1 m, ancho 2,6 m, largo 18 m
(articulado peruano típico). Se pueden ajustar por línea de comandos.

- `--comparar-auto` añade el perfil `auto` como contraste, para separar el error
  de topología del efecto de las restricciones de pesados.
- `--sensibilidad-peso` barre el peso bruto y localiza el umbral en que el
  corredor deja de ser transitable.

Salidas en `data/processed/valhalla/`:

| Archivo | Contenido |
|---|---|
| `rutas_od.csv` | Por par OD: km y min del motor, km de la red propia, desvío porcentual, y km/min de cada alternativa |
| `rutas_od.json` | Lo mismo con los parámetros usados, para trazabilidad |
| `rutas_od.geojson` | Trazas de la ruta principal y de cada alternativa, listas para QGIS |
| `sensibilidad_peso.csv` | Barrido peso × par OD, solo con `--sensibilidad-peso` |

La columna `desvio_pct` es el contraste con `corredor_macro.csv`: si el motor y
la red propia coinciden dentro de unos pocos puntos porcentuales, la topología
construida desde OSM queda validada por una vía independiente.

## 5.1 Resultados medidos

Valhalla 3.5.1 sobre Perú (1 430 tiles, 1 623 444 nodos, 4 452 692 aristas
dirigidas). Perfil de referencia: 36 t, 4,1 m de alto, 2,6 m de ancho, 18 m de
largo.

| Par | Motor (km) | Red propia (km) | Desvío | Auto (km) | Sobrecosto pesados |
|---|---:|---:|---:|---:|---:|
| LIM–TRU | 609,6 | 547,6 | **+11,32 %** | 560,1 | +49,5 km |
| TRU–CHI | 208,6 | 203,2 | +2,66 % | 208,6 | 0,0 km |
| CHI–PIU | 217,6 | 212,8 | +2,26 % | 215,6 | +2,0 km |
| PIU–SUL | 39,0 | 39,3 | −0,76 % | 39,0 | 0,0 km |
| PIU–CHU | 60,9 | 61,6 | −1,14 % | 60,9 | 0,0 km |
| LIM–HUY | 306,6 | 303,4 | +1,05 % | 306,1 | +0,5 km |
| PIU–PAI | 58,0 | 57,8 | +0,35 % | 57,7 | +0,3 km |

**Dos lecturas, y la segunda es la importante:**

1. **Contra el perfil `auto` el desvío medio es +0,73 %.** Es decir: la red
   propia reproduce la topología real del corredor con menos de un punto
   porcentual de error, medido por un motor ajeno a su construcción. Eso cierra
   la validación topológica.
2. **El desvío de +2,25 % del perfil `truck` no es un error de la red: es la
   restricción de pesados.** Casi todo el sobrecosto (+52,3 km de 55,3) está en
   la salida de Lima, donde las expresas tienen `hgv=no`. Un camión de 36 t no
   puede usar la Vía Expresa ni Javier Prado y debe rodear. Es exactamente el
   hallazgo de la auditoría previa, ahora cuantificado por un tercero.

### El corredor se rompe a 37 t

Barrido de peso bruto sobre los 7 pares (`--sensibilidad-peso`):

| Peso | LIM–TRU | Resto de pares |
|---:|---:|---|
| 30 t | 609,6 km | sin cambio |
| 36 t | 609,6 km | sin cambio |
| **37 t** | **953,1 km** (+56 %) | sin cambio |
| 40 t | 953,1 km | sin cambio |
| **42 t** | **1 027,2 km** (+68 %) | sin cambio |
| 48 t | 1 027,2 km | sin cambio |

El umbral está en **36 t**, y no es un artefacto del motor: la red propia tiene
`peso_max_t = 36` en cinco tramos de la Panamericana Norte (TR-10674, TR-11249,
TR-11300, TR-11438, TR-14761). Un camión de 37 t no puede usar la ruta directa y
debe desviarse 343 km. **Ningún otro par del corredor es sensible al peso.**

Consecuencia para el escenario: el camión de referencia debe quedar en 36 t o
menos. Si se sube a 42 t, el corredor Lima–Trujillo deja de existir como tal y el
problema de ruteo cambia de naturaleza.

### Alternativas: escasas, y eso también informa

`alternates` devolvió rutas solo en 2 de 7 pares (PIU–SUL con 1, LIM–HUY con 1);
en los otros 5 devolvió 0. No es un fallo de configuración: con perfil `auto`
LIM–TRU sí devuelve 1 alternativa, y con `truck` devuelve 0 para cualquier peso
entre 7,5 y 48 t. La lectura es que **para un camión pesado el corredor es
prácticamente un eje único**: no hay un segundo camino razonable que respete las
restricciones. Eso refuerza la premisa del problema MARL — el agente decide
*cuándo* desviarse porque las opciones de desvío son pocas y costosas.

## 6. Qué falta para cerrarlo del todo

- **Agente histórico**: reproducir los 500 viajes registrados *dentro* del
  entorno para obtener una recompensa comparable a la de la política aprendida.
  Valhalla aporta la ruta de referencia, pero la comparación pareada exige
  ejecutar la política histórica en el simulador.
- **Candidatas por tramo**: hoy las alternativas se piden por par OD, y Valhalla
  devuelve muy pocas (2 de 7 pares) porque el corredor no tiene segundos caminos
  para camión. Para alimentar el espacio de acciones conviene generarlas sobre la
  **red propia** (K caminos más cortos, p. ej. Yen sobre el grafo consolidado,
  aplicando `penalizacion_pesados`), y dejar a Valhalla como contraste externo.
  Así las candidatas son secuencias de `id_tramo` que el simulador puede
  ejecutar, cosa que una traza externa no garantiza.
