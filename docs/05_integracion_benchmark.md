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
| `benchmark/cobertura_muestra.json` | Marco muestral: ventana temporal, flota y cobertura sobre el universo de tramos |

## 5.1 Marco muestral: la empresa no entrega el histórico completo

El registro de la empresa es un **extracto**, no la operación íntegra. Eso no
invalida el benchmark, pero obliga a declarar qué se tiene y qué no. Medido con
`scripts/07_cobertura_muestra.py`:

| Dimensión | Valor |
|---|---|
| Viajes | 500 (1 982 paradas) |
| Ventana | 2025-12-07 → 2026-08-18 · **244 días con viajes** |
| Ritmo | 2.05 viajes por día con actividad |
| Flota | 9 vehículos (≈ 56 viajes por unidad) |
| Puntos de entrega | 25 |
| Tramos recorridos | **1 687 de 28 312 (6.0 %)** |
| Kilómetros recorridos | **2 050.9 de 14 019.6 (14.6 %)** |
| Reutilización | 163 viajes por tramo usado (mediana del tramo activo) |

La lectura correcta de ese 6 % es la que importa: **la muestra no toca el 94 %
restante de los tramos porque ese resto no es ruta operativa**, no porque falte
información. La cobertura se concentra donde está la decisión:

| Jerarquía | Cobertura de la muestra |
|---|---|
| Autopista (`motorway`) | **38.3 %** (204 de 532) |
| Carretera troncal (`trunk`) | **28.5 %** (649 de 2 280) |
| Primaria (`primary`) | 6.7 % (392 de 5 878) |
| Secundaria (`secondary`) | 3.1 % (280 de 9 054) |
| Terciaria (`tertiary`) | 1.5 % (105 de 7 165) |

Es decir: la muestra recorre el 38 % de las autopistas y el 28 % de las
troncales, y apenas roza la red terciaria urbana. El universo grande existe para
que el agente tenga **alternativas de desvío**, no porque se espere tráfico en
cada tramo.

**Cómo se maneja el sesgo de selección.** La comparación es *pareada*: el mismo
viaje, con los mismos eventos y la misma ventana horaria, resuelto por la
política histórica y por el MARL. Cualquier sesgo de selección afecta a ambas
políticas por igual y se cancela en la diferencia. Además, el histórico **no se
usa para entrenar** —el modelo entrena con episodios sintéticos calibrados—;
el extracto sirve para calibrar distribuciones y para validar contra la
operación real.

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
5. **Muestra, no censo**: los 500 viajes son el extracto que la empresa pudo
   entregar, no el histórico completo. Se documenta el marco muestral (§5.1) y
   la comparación se hace pareada para que el sesgo se cancele. Si la empresa
   entrega más registros, `scripts/06_integrar_viajes.py` reprocesa todo sin
   cambiar el modelo: el pipeline es idempotente.

## 7. Reproducir

```bash
python scripts/06_integrar_viajes.py      # reconstruye el benchmark
python scripts/07_cobertura_muestra.py    # mide el marco muestral
```

Requiere `geopy` y `openpyxl` (ya en `requirements.txt`). El caché de
geocodificación queda en `data/raw/geocoding_cache.json`.
