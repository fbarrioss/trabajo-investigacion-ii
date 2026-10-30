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
```

Parámetros del perfil: peso bruto 36 t, alto 4,1 m, ancho 2,6 m, largo 18 m
(articulado peruano típico). Se pueden ajustar por línea de comandos.

Salidas en `data/processed/valhalla/`:

| Archivo | Contenido |
|---|---|
| `rutas_od.csv` | Por par OD: km y min del motor, km de la red propia, desvío porcentual, y km/min de cada alternativa |
| `rutas_od.json` | Lo mismo con los parámetros usados, para trazabilidad |
| `rutas_od.geojson` | Trazas de la ruta principal y de cada alternativa, listas para QGIS |

La columna `desvio_pct` es el contraste con `corredor_macro.csv`: si el motor y
la red propia coinciden dentro de unos pocos puntos porcentuales, la topología
construida desde OSM queda validada por una vía independiente.

## 6. Qué falta para cerrarlo del todo

- **Agente histórico**: reproducir los 500 viajes registrados *dentro* del
  entorno para obtener una recompensa comparable a la de la política aprendida.
  Valhalla aporta la ruta de referencia, pero la comparación pareada exige
  ejecutar la política histórica en el simulador.
- **Candidatas por tramo**: hoy las alternativas se piden por par OD. Para que
  alimenten el espacio de acciones conviene pedirlas también por tramo crítico
  (los 1 328 de corte) y guardarlas como un conjunto de rutas precalculadas.
