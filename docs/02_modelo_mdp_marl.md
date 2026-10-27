# Modelo formal MDP / MARL de la ruta operativa

> Producto verificable del **OE2** ("Modelar el dominio operativo como entorno
> MDP/MARL"). Implementado en `src/entorno/entorno_rutas.py`.

---

## 1. Del MDP al Dec-POMDP

El plan de tesis (sección 4.2.7) define un MDP como la tupla ⟨S, A, P, R, γ⟩.
Como intervienen varias unidades vehiculares con información local, el modelo se
extiende a un **Dec-POMDP cooperativo**:

$$\mathcal{M} = \langle N, S, \{A_i\}, \{O_i\}, P, R, \gamma \rangle$$

| Símbolo | Elemento | Implementación |
|---|---|---|
| $N$ | Agentes = unidades vehiculares | `n_agentes` (3 por episodio) |
| $S$ | Estado global | `_estado_global()` → 58 dims |
| $A_i$ | Acciones del agente $i$ | 4 acciones discretas |
| $O_i$ | Observación local | 20 dims normalizadas |
| $P$ | Transición | movimiento sobre el grafo + eventos |
| $R$ | Recompensa cooperativa | `_recompensa()` |
| $\gamma$ | Descuento | 0.99 |

**Decisión de diseño (H2):** se modelan únicamente las unidades vehiculares como
agentes. Añadir agentes coordinador/monitoreo incrementaría la complejidad sin
datos que sustenten sus acciones y recompensas (acuerdo de la reunión del
16/10/2026: "no complicar la solución"). La coordinación se resuelve con CTDE:
el mezclador de QMIX observa el estado global durante el entrenamiento.

---

## 2. Espacio de acciones

| Código | Acción | Semántica operativa | Efecto |
|---|---|---|---|
| 0 | `continuar` | Seguir el plan vigente | Toma la arista del plan **sin recalcular** |
| 1 | `tomar_alterna` | Rodear el tramo planificado | Recalcula el camino penalizando el tramo siguiente (×1000), de modo que el vehículo toma un **desvío real**. Solo es válida si el tramo tiene `tiene_alterna` (95.4 % de la red) |
| 2 | `esperar` | Detenerse un paso (15 min) | Costo de detención S/ 64/h |
| 3 | `replanificar` | Recalcular ruta con el estado actual | Dijkstra ponderado por eventos |

La distinción entre **continuar** (plan congelado) y **replanificar** (ruta
recalculada) es la que da sentido al problema: si ambas fueran iguales, la
replanificación no tendría valor. El plan se congela al inicio del viaje y solo
cambia con la acción 3 o al completar una entrega.

### Máscara de acciones

`mascara_acciones()` desactiva:
* acción 1 si no existe tramo alterno hacia el siguiente nodo del plan;
* acción 3 si se agotó `max_replanificaciones`;
* todas las acciones si el agente ya terminó o está en tránsito.

---

## 3. Observación local (20 dimensiones)

| # | Variable | Normalización |
|---|---|---|
| 0 | Distancia restante al destino | / 1000 km |
| 1 | Tiempo restante estimado | / 1200 min |
| 2 | Horas hasta el cierre de ventana | / 12 (rango −1..1) |
| 3 | Proporción de entregas completadas | 0–1 |
| 4 | Costo acumulado | / 500 PEN |
| 5 | Retraso acumulado | / 240 min |
| 6 | Progreso de la ruta | 0–1 |
| 7–8 | Hora del día (sin/cos) | −1..1 |
| 9 | Congestión en el tramo actual | 0/1 |
| 10 | Bloqueo o avería en el tramo actual | 0/1 |
| 11 | Entregas pendientes | / 4 |
| 12 | Replanificaciones usadas | / máximo |
| 13 | Grado de salida del nodo actual | / 4 |
| 14 | ¿Está en el nodo de destino? | 0/1 |
| 15 | Progreso del tramo actual | 0–1 |
| 16 | Severidad del evento en curso | / 5 |
| 17 | ¿Está detenido en un nodo? | 0/1 |
| **18** | **Tiempo del tramo planificado (con eventos)** | / 300 min |
| **19** | **¿Admite desvío el tramo planificado?** | +1 sí / −1 no |

Las dimensiones 18 y 19 permiten **anticipar**: el agente conoce el costo
temporal del tramo que va a tomar y si dispone de un desvío antes de entrar en
él.

---

## 4. Función de recompensa

$$r_t = -\;w_c\,\frac{\Delta\text{costo}_t}{500} \;-\; w_t\,\frac{15}{600} \;-\; \text{penal}_{\text{retraso}} \;-\; w_d\,(0.002) \;+\; \phi_{\text{entrega}}$$

| Término | Peso | Valor |
|---|---|---|
| $w_c$ (costo incremental) | 0.60 | normalizado por S/ 500 |
| $w_t$ (tiempo) | 0.40 | normalizado por 600 min |
| $w_d$ (desvío) | 0.30 | penalización por tramo |
| $\phi_{\text{entrega}}$ | — | **+5.0** si cumple ventana, **−3.0** si no |
| Penalidad por bloqueo | 1.50 | 0.05 por paso bloqueado |
| Penalidad por replanificar | 0.10 | 0.01 por evento |

Se usan **incrementos** y no valores acumulados: de lo contrario el agente sería
penalizado simplemente por llevar más tiempo en la simulación, lo que sesga el
aprendizaje hacia episodios cortos y no hacia rutas eficientes.

**Recompensa cooperativa:** el valor escalar compartido es el promedio de las
recompensas individuales, lo que alinea los incentivos (equipo, no competencia).

---

## 5. Dinámica de transición

```
Para cada paso t (15 min de operación):
  1. Si el agente está en un nodo  → aplica la acción elegida
  2. Si el agente está en tránsito → avanza  v · Δt / factor_evento
  3. Si supera el umbral de conducción continua (5 h) → descanso de 45 min
  4. Si el tramo está bloqueado (factor ≥ 5) durante 4 pasos → retrocede al
     último nodo y recupera capacidad de decisión
  5. Al llegar al nodo de entrega → registra cumplimiento y recompensa diferida
  6. Avanza el reloj global; los eventos activos cambian
```

### Eventos dinámicos

Generados con `src/datos/eventos.py`, calibrados con la Tabla 1 del plan:

| Evento | Prob. base (24 h) | Duración | Severidad |
|---|---|---|---|
| Congestión | 0.24 | 1–4 h | ×1.3 – ×2.2 |
| Bloqueo | 0.055 | 2–12 h | ×8 – ×20 |
| Clima adverso | 0.09 | 1–6 h | ×1.2 – ×1.8 |
| Avería | 0.03 | 2–8 h | ×4 – ×9 |

La probabilidad se ajusta por **exposición** (longitud del tramo) y por
**riesgo de zona** (Lima 1.4, Piura 1.2, Sullana 0.9…). El resultado se valida
contra el 27.6 % de rutas afectadas reportado por SUTRAN para 2025.

---

## 6. CTDE

| Fase | Información | Implementación |
|---|---|---|
| Entrenamiento | Estado global (58 dims) | Mezclador QMIX / crítico MAPPO |
| Ejecución | Observación local (20 dims) | Red de agente / actor |

Esto refleja la operación real: la torre de control conoce la flota completa,
pero cada vehículo decide con su información local.

---

## 7. Métricas de evaluación (Tabla 3)

| Métrica | Definición |
|---|---|
| `costo_total_pen` | Suma de combustible, peaje, mantenimiento, conductor y detención |
| `tiempo_total_min` | Tiempo del agente más lento del episodio |
| `puntualidad_pct` | Entregas dentro de la ventana / total |
| `desviacion_ruta_km` | km reales − km planificados |
| `n_replanificaciones` | Conteo de acción 3 |
| `tiempo_detenido_min` | Pasos de espera × 15 min |
| `resiliencia_pct` | Puntualidad ponderada por eventos severos |
| `recompensa_promedio` | Retorno del episodio |
