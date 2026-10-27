# Integración de viajes reales → red vial (benchmark MARL)

Este documento describe cómo se encajaron los **500 viajes registrados** de la
empresa sobre el **universo de tramos** (28 312 tramos) y qué limitaciones
quedan pendientes.

## 1. Fuentes

| Archivo | Contenido | Filas |
|---|---|---|
| `EYM_V5_Viajes_Puntos_Entrega_Secuencias.xlsx` | `PUNTOS_POR_VIAJE` (paradas), `RESUMEN_VIAJES` (viajes), `NOTAS_Y_VALIDACION` | 1 982 / 500 / 13 |
| `puntos_entrega.xlsx` | Catálogo de 25 puntos de entrega | 25 |
| `data/processed/red_vial/` | Topología OSM con cobertura completa | 17 833 nodos, 28 312 tramos |

Depósito (origen): **−12.2302778, −76.9655556** (sur de Lima).

## 2. Geocodificación

Se consultó **Nominatim** para los 25 puntos con tres variantes de consulta
(dirección completa, calle sin número, dirección + Perú). El resultado se
aceptó solo si caía a **≤500 m** de la coordenada observada en los viajes,
para evitar aceptar el centroide de la ciudad o una calle homónima.

Resultado de la fuente final por punto:

| Fuente | Puntos | Motivo |
|---|---|---|
| `viajes` | 21 | Coordenada observada real; Nominatim no resolvió con precisión |
| `catalogo` | 4 | Descolapso de pares que compartían coordenada en viajes |

> Las direcciones peruanas con formato `MZ / LOTE / URB / P.J.` no se resuelven
> con precisión suficiente en Nominatim. La coordenada registrada en los viajes
> es la fuente más fiable y específica; Nominatim se usó como validación cruzada.

## 3. Snapping a la red

Cada punto se asignó al nodo más cercano de la red (haversine). Tolerancia:
**2 000 m**. Resultado:

| Punto | Ciudad | Dist. snap | Estado |
|---|---|---|---|
| GEO-001 … GEO-018, GEO-020, GEO-022 | Corredor norte | 8–1 194 m | ✅ cubierto |
| GEO-019 | Paita | 227 m | ✅ cubierto (ramal Piura–Paita) |
| GEO-023 / 024 / 025 | Huancayo | 112 / 63 / 315 m | ✅ cubierto (Carretera Central) |
| CEDI (origen) | Sur de Lima | 368 m | ✅ cubierto (acceso terminal) |

**0 puntos fuera de cobertura** → los 500 viajes se reconstruyen sobre la red.
Snap medio 242 m, mediana 116 m, máximo 1 194.5 m (GEO-010, Trujillo).

> La ampliación de la red (Carretera Central, ramal a Paita y acceso al CEDI) se
> documenta en `docs/06_cobertura_extendida.md`.

## 4. Reconstrucción de rutas

Para cada viaje se calculó la ruta mínima (Dijkstra por tiempo) depósito →
parada 1 → parada 2 → … → última, sobre el grafo real. Se acumuló distancia y
tiempo **por red**, no geodésico.

| Indicador | Valor |
|---|---|
| Viajes válidos | **500** de 500 (100 %) |
| Km medio por red | **830.8 km** |
| Km medio geodésico | 704.2 km |
| Ratio red/geodésica | **1.18** (mediana por parada 1.15) |
| Duración media | 628 min (10.5 h) |
| Paradas medias | 3.96 |

## 5. Salidas

| Archivo | Contenido |
|---|---|
| `benchmark/puntos_snap.csv` | 25 puntos con coordenada final, fuente, nodo y distancia de snapping |
| `benchmark/benchmark_viajes.csv` | 500 viajes con km/min por red, km geodésico y flags |
| `benchmark/benchmark_paradas.csv` | 1 982 paradas con nodo, distancia acumulada y tiempos reales |
| `benchmark/benchmark_resumen.json` | Métricas agregadas |

## 6. Limitaciones y siguientes pasos

1. **Pares colapsados**: GEO-008/GEO-009 (Trujillo) y GEO-018/GEO-021
   (Chulucanas) comparten coordenada en los datos originales; el tramo entre
   ellos mide 0 km (39 paradas). Es una limitación del dato de origen.
2. **Acceso terminal del CEDI**: el depósito está sobre una vía local y el nodo
   de la red de carga más próximo queda a 368 m. Se acepta como acceso terminal
   documentado.
3. **Sin GPS continuo**: la discretización es por tramos de red con tiempos de
   flujo libre; la estancia observada (`llegada_real` → `fin_descarga`) se usa
   como servicio + espera.
4. **Siguiente paso**: usar este benchmark como línea base de la operación
   manual para comparar la política MARL (mismos viajes, mismas ventanas).

## 7. Reproducir

```bash
python scripts/06_integrar_viajes.py
```

Requiere `geopy` y `openpyxl` (ya en `requirements.txt`). El caché de
geocodificación queda en `data/raw/geocoding_cache.json`.
