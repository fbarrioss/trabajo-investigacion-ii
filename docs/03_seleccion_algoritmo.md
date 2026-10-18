# Selección del algoritmo MARL

> Tarea asignada a Franklin en la reunión del 16/10/2026: *"¿qué tipo de modelos
> piensan usar?"* — el profesor observó que la definición del algoritmo seguía
> pendiente. Este documento cierra esa decisión con criterios verificables.

---

## 1. Candidatos evaluados

El plan de tesis (OE5, matriz de consistencia) declara como estados de la
variable independiente: **DQN/DDQN, PPO/MAPPO, MADDPG, QMIX/VDN**.

| Algoritmo | Familia | Tipo de acción | Coordinación | Requiere |
|---|---|---|---|---|
| **QMIX** | Valor (off-policy) | Discreta | CTDE con mezclador monotónico | Estado global en entrenamiento |
| **MAPPO** | Actor-crítico (on-policy) | Discreta o continua | CTDE con crítico central | Muchas muestras |
| **MADDPG** | Actor-crítico (off-policy) | Continua | Críticos centralizados | Acciones continuas |
| **VDN** | Valor (off-policy) | Discreta | Suma de Q individuales | — |

---

## 2. Criterios de decisión

Los criterios se derivan directamente de las restricciones del problema descritas
en el plan de tesis (sección 4.2.9: escalabilidad, no estacionariedad,
asignación de crédito, restricciones de comunicación, sim-to-real gap).

| Criterio | Peso | Justificación |
|---|---|---|
| **C1. Naturaleza de la acción** | 25 % | El entorno tiene 4 acciones discretas (continuar / alterna / esperar / replanificar). |
| **C2. Eficiencia muestral** | 25 % | El simulador es costoso; se dispone de tiempo limitado hasta la semana 8. |
| **C3. Asignación de crédito** | 20 % | La recompensa es compartida; hay que atribuirla a cada vehículo. |
| **C4. Estabilidad de convergencia** | 15 % | Se requieren curvas de aprendizaje interpretables para la verificación. |
| **C5. Costo de implementación** | 15 % | El equipo debe poder defender el código ante el Comité. |

---

## 3. Matriz de decisión

Escala 1–5 (5 = mejor).

| Criterio | QMIX | MAPPO | MADDPG | VDN |
|---|---|---|---|---|
| C1. Acción discreta | **5** | **5** | 2 | **5** |
| C2. Eficiencia muestral | **5** | 2 | 3 | **5** |
| C3. Asignación de crédito | **4** | **4** | **4** | 2 |
| C4. Estabilidad | **4** | 3 | 2 | 3 |
| C5. Implementación | 3 | **4** | 2 | **5** |
| **Puntaje ponderado** | **4.40** | **3.55** | **2.60** | **4.00** |

**Cálculo QMIX:** 0.25·5 + 0.25·5 + 0.20·4 + 0.15·4 + 0.15·3 = 1.25 + 1.25 + 0.80 + 0.60 + 0.45 = **4.35**
**Cálculo MAPPO:** 0.25·5 + 0.25·2 + 0.20·4 + 0.15·3 + 0.15·4 = 1.25 + 0.50 + 0.80 + 0.45 + 0.60 = **3.60**

---

## 4. Decisión

### 4.1 Algoritmo principal: **QMIX**

**Razones:**

1. **Coherencia con el espacio de acciones.** Las decisiones operativas son
   categóricas y excluyentes; los métodos basados en valor las representan
   directamente sin necesidad de una cabeza gaussiana (que en MADDPG obligaría
   a discretizar de forma artificial).

2. **Eficiencia muestral.** QMIX es *off-policy*: reutiliza un buffer de
   repetición. En un simulador donde cada episodio cuesta ~0.5 s de cómputo y
   hacen falta miles de episodios para converger, esto es determinante frente a
   MAPPO (*on-policy*, descarta cada lote tras la actualización).

3. **Asignación de crédito vía mezclador monotónico.** La restricción
   $\partial Q_{tot}/\partial Q_i \geq 0$ garantiza **consistencia
   individual-global (IGM)**: la acción greedy individual coincide con la acción
   greedy global. Esto es exactamente lo que exige la ejecución descentralizada
   del esquema CTDE descrito en el plan.

4. **Alineación con la literatura del estado del arte.** Ren et al. (2022)
   —antecedente 2 del plan— aplican MARL con *route recorders* al ruteo de
   vehículos en cadena de suministro, y Zhang et al. (2020) —antecedente 4—
   usan MARL para VRPTW con ventanas suaves. Ambos son entornos cooperativos con
   recompensa compartida, el régimen donde QMIX reporta mejor desempeño.

### 4.2 Algoritmo de contraste: **MAPPO**

Se entrena como **contraste interno** para robustecer la discusión de H5, no como
candidato principal. Permite mostrar que la superioridad del artefacto no depende
de un único algoritmo.

### 4.3 Descartado: **MADDPG**

* Diseñado para acciones continuas; el problema es discreto.
* Alta varianza y baja estabilidad en entornos cooperativos con recompensa
  compartida.
* Costo de implementación y defensa ante el Comité desproporcionado al beneficio.

### 4.4 Descartado: **VDN**

La suma aditiva de Q individuales no captura interacciones no lineales entre
vehículos (por ejemplo, dos unidades compitiendo por el mismo tramo alterno).
QMIX generaliza VDN con el mezclador monotónico y cuesta lo mismo implementarlo.

---

## 5. Configuración adoptada

| Hiperparámetro | Valor | Fuente |
|---|---|---|
| `lr` | 5 × 10⁻⁴ | Rango estándar QMIX (Rashid et al., 2018) |
| `gamma` | 0.99 | Horizonte de decisión largo |
| `batch_size` | 32 | Restricción de memoria en CPU |
| `buffer_size` | 50 000 | ≈ 300 episodios |
| `target_update_interval` | 200 pasos | Estabilización del TD target |
| `hidden_dim` | 64 | Balance capacidad / sobreajuste |
| `mixing_embed_dim` | 32 | Mezclador |
| ε inicio → fin | 1.0 → 0.05 en 20 000 pasos | Exploración decreciente |

---

## 6. Riesgos y mitigación

| Riesgo | Mitigación implementada |
|---|---|
| Convergencia lenta en CPU | Buffer reutilizable + `batch_size` reducido |
| No estacionariedad entre agentes | Parámetros compartidos entre agentes (`RedAgente`) |
| Recompensa compartida diluye señal | Señal diferida por entrega (+5 / −3) |
| Sobreajuste a una semilla | Entrenamiento con 5 semillas (`--semillas 1 2 3 4 5`) |
| Sim-to-real gap | Declarado como limitación; datos reales reemplazables |

---

## 7. Referencias de soporte

* Rashid, T. et al. (2018). *QMIX: Monotonic Value Function Factorisation for
  Deep Multi-Agent Reinforcement Learning*. ICML.
* Yu, C. et al. (2022). *The Surprising Effectiveness of PPO in Cooperative
  Multi-Agent Games*. NeurIPS.
* Lowe, R. et al. (2017). *Multi-Agent Actor-Critic for Mixed
  Cooperative-Competitive Environments*. NeurIPS.
* Ren, L. et al. (2022). *A multi-agent reinforcement learning method with route
  recorders for vehicle routing in supply chain management*. IEEE T-ITS.
* Zhang, K. et al. (2020). *Multi-vehicle routing problems with soft time
  windows: A multi-agent reinforcement learning approach*. TR Part C.
