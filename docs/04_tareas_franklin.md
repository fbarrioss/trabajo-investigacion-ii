# Tareas de Franklin — cierre de la reunión del 16/10/2026

> Documento de trabajo para la sesión del martes. Resume qué se acordó, qué
> quedó resuelto y qué depende de terceros.

---

## 1. Acuerdos de la reunión

| # | Acuerdo | Responsable | Fecha límite |
|---|---|---|---|
| A1 | Documento para el Comité (semana 8) | Kenin | martes (revisión) → miércoles (envío) |
| A2 | Recursos y operación (entidades 1 y 2 del modelo) | Wilson | martes |
| A3 | **Red vial: nodos y tramos** | **Franklin** | **martes** |
| A4 | Definir el algoritmo MARL | Franklin + equipo | **resuelto** |
| A5 | Acotar el alcance geográfico | Equipo | **resuelto** |
| A6 | Revisar simuladores existentes | Wilson | pendiente |

### Observaciones del profesor (Wester Zela)

1. **El alcance debe acotarse a una zona con datos reales.** No se acepta
   "transporte de carga en el Perú" como alcance: *"El Perú es grande… no es el
   Perú, es una zona en particular"*. También advirtió que el título de la tesis
   debe cambiar en consecuencia.
2. **No se aceptan datos simulados para una tesis.** *"Para una publicación datos
   simulados se puede aceptar, pero para una tesis no; tiene que ser datos
   reales."* Se admite combinar datos históricos reales con datos
   semi-sintéticos, declarando la naturaleza de cada fuente.
3. **Revisar si existen simuladores** para este tipo de problemas antes de
   construir uno desde cero.
4. **La semana 8 es el examen parcial**: el modelo con datos preprocesados debe
   estar resuelto. Quedan 3 semanas.

---

## 2. Lo que se entregó (A3 + A4 + A5)

### 2.1 Alcance geográfico acotado (A5)

Corredor **Lima → Trujillo → Chiclayo → Piura → Sullana → Chulucanas**
(Panamericana Norte, PE-1N, + ramal a Chulucanas). Es la zona donde la empresa
concentra el 100 % de sus viajes registrados.

| Ciudad | Distribuidores |
|---|---|
| Lima (hub de origen) | — |
| Trujillo | 7 |
| Chiclayo | 3 |
| Piura | 3 |
| Sullana | 2 |
| Chulucanas | 2 |
| **Total** | **17** |

### 2.2 Red vial construida (A3)

| Entregable | Archivo | Estado |
|---|---|---|
| Maestro de nodos | `data/processed/red_vial/nodos.csv` | ✅ 42 nodos |
| Maestro de tramos | `data/processed/red_vial/tramos.csv` | ✅ 72 tramos |
| Grafo navegable | `data/processed/red_vial/red_vial.graphml` | ✅ |
| Reporte de validación | salida de `scripts/01_red_vial.py` | ✅ OK |
| Diccionario de datos | `docs/01_diccionario_datos.md` | ✅ |

**Atributos por tramo:** distancia, velocidad de flujo, tiempo base, peaje,
tipo de vía, zona, sinuosidad y riesgo base. Cada segmento interurbano de más de
35 km tiene además una **vía alterna** (18 % más larga, 15 % más lenta), que es
lo que habilita la acción *"cambiar de tramo"* del modelo.

**Modos de construcción:**
```bash
python scripts/01_red_vial.py               # geometría real desde OpenStreetMap
python scripts/01_red_vial.py --modo curada # trayectoria real curada de la PE-1N
```

### 2.3 Algoritmo definido (A4)

Se evaluaron QMIX, MAPPO, MADDPG y VDN con una matriz de decisión de 5 criterios
ponderados (`docs/03_seleccion_algoritmo.md`):

| Algoritmo | Puntaje |
|---|---|
| **QMIX (elegido)** | **4.35** |
| VDN | 4.00 |
| MAPPO (contraste) | 3.60 |
| MADDPG (descartado) | 2.60 |

**QMIX** es el algoritmo principal; **MAPPO** se entrena como contraste interno
para robustecer la discusión de H5.

### 2.4 Todo lo necesario para entrenar

```bash
python scripts/01_red_vial.py                    # red vial
python scripts/02_dataset.py --viajes 180        # dataset + eventos
python scripts/03_entrenar.py --algoritmo qmix --episodios 2000 --semillas 1 2 3
python scripts/04_evaluar.py --episodios 30      # comparación con baselines
```

Incluye: entorno Dec-POMDP, recompensa, eventos calibrados con SUTRAN/MTC,
QMIX, MAPPO, tres baselines (estático, Dijkstra, VRP) y evaluación estadística
con Mann-Whitney.

---

## 3. Lo que falta y de quién depende

| Pendiente | Depende de | Impacto | Acción de Franklin |
|---|---|---|---|
| **Direcciones reales de los 17 distribuidores** | Wilson | Alto: hoy son coordenadas *placeholder* dentro del radio urbano | Reemplazar `lat`/`lon` en `config/escenario_norte.yaml` y reejecutar el paso 1 |
| **Excel de entidades (recursos y operación)** | Wilson | Alto: el dataset usa datos sintéticos calibrados | Sustituir los Parquet de `data/processed/dataset/` |
| **Datos GPS / tiempos reales de viaje** | Wilson | Medio: calibración de velocidades | Ajustar `velocidad_flujo_kmh` por tramo |
| **Documento para el Comité** | Kenin | Bloqueante para la presentación | Enviarle el resumen del comité (ver §4) |
| **Revisión de simuladores existentes** | Wilson | Medio: podría evitar construir el simulador | Buscar en la literatura (SUMO, MATSim, VRP-RL) |

### Nota metodológica para el Comité

La red vial se construye con **geometría real** (OpenStreetMap o trayectoria real
de la PE-1N); lo que es sintético es la **operación** (viajes, cargas, ventanas),
calibrada con los volúmenes declarados por la empresa. Esto es consistente con la
limitación ya declarada en el plan de tesis (sección 1.4.2): *"se utilizará una
combinación de datos históricos, semi-sintéticos y simulados, declarando
explícitamente la naturaleza de cada fuente"*.

---

## 4. Mensaje para Kenin (resumen del Comité)

> Kenin, esto es lo que el profesor indicó sobre el Comité y la semana 8:
>
> 1. **Plazo:** el documento para el Comité debe enviarse **esta semana**
>    (semana 5). La presentación de la tesis es en la **semana 8** y esa nota es
>    el **examen parcial**. Quedan 3 semanas.
> 2. **El Comité no revisa documentos incompletos.** Verificar que estén todas
>    las secciones, referencias y anexos.
> 3. **Formato:** revisar las guías 1 y 2 del curso para confirmar que el formato
>    del documento coincide con el solicitado (organización de imágenes, tablas,
>    numeración).
> 4. **Referencias y anexos:** validar que todos los enlaces funcionen (el
>    profesor no lo pidió explícitamente, pero es observación de Franklin por
>    errores 404 previos).
> 5. **Cambio de alcance:** el profesor exige acotar el problema a una zona con
>    datos reales. Hay que **cambiar el título de la tesis** y ajustar la
>    formulación del problema, los objetivos y el alcance. El nuevo alcance es el
>    corredor Lima–Trujillo–Chiclayo–Piura–Sullana–Chulucanas.
> 6. **Datos:** para tesis no se aceptan datos simulados; deben ser reales. Se
>    admite la combinación con semi-sintéticos si se declara la fuente.
>
> Con esto deberías poder cerrar el documento. Cualquier cosa me avisas.

---

## 5. Guion para la reunión del martes

1. **Mostrar la red vial** (2 min): 42 nodos, 72 tramos, 17 distribuidores,
   validación OK, cobertura exacta de las 6 ciudades de la imagen de rutas.
2. **Mostrar el alcance acotado** (1 min): ya no es "el Perú", es el corredor
   norte. Proponer el nuevo título.
3. **Mostrar la decisión de algoritmo** (2 min): matriz de decisión, QMIX
   elegido, MAPPO como contraste.
4. **Mostrar que el pipeline corre** (2 min): entrenamiento y evaluación contra
   baselines, con métricas del plan.
5. **Pedir a Wilson** (1 min): direcciones reales de los 17 distribuidores y el
   Excel de entidades, para reemplazar los datos sintéticos antes de la semana 8.
