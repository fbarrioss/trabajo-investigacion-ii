"""
Genera el documento Word de avance de investigación solicitado por el profesor.
Basado en la información real del repositorio marl-rutas-carga.
"""

from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
import os

# ──────────────────────────────────────────────────────────────────────────────
# Configuración del documento
# ──────────────────────────────────────────────────────────────────────────────

doc = Document()

# Estilo Normal
style = doc.styles['Normal']
font = style.font
font.name = 'Calibri'
font.size = Pt(11)
font.color.rgb = RGBColor(0x1a, 0x1a, 0x1a)
style.paragraph_format.space_after = Pt(6)
style.paragraph_format.line_spacing = 1.15

# Márgenes
for section in doc.sections:
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(3.0)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def add_heading(text, level=1):
    """Añade un encabezado con estilo consistente."""
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x0d, 0x3b, 0x66)
    return h


def add_paragraph(text, bold=False, italic=False, size=None, align=None):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if align:
        p.alignment = align
    return p


def add_table(headers, rows, col_widths=None):
    """Crea una tabla con encabezados sombreados."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Light Grid Accent 1'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    hdr_cells = table.rows[0].cells
    for i, header in enumerate(headers):
        hdr_cells[i].text = header
        for p in hdr_cells[i].paragraphs:
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(10)

    for row_data in rows:
        row_cells = table.add_row().cells
        for i, cell_text in enumerate(row_data):
            row_cells[i].text = str(cell_text)
            for p in row_cells[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)

    if col_widths:
        for row in table.rows:
            for i, width in enumerate(col_widths):
                row.cells[i].width = width

    return table


def add_bullet(text, level=0):
    p = doc.add_paragraph(text, style='List Bullet')
    if level > 0:
        p.paragraph_format.left_indent = Cm(1.5 * (level + 1))
    return p


def add_numbered(text):
    return doc.add_paragraph(text, style='List Number')


# ──────────────────────────────────────────────────────────────────────────────
# PORTADA
# ──────────────────────────────────────────────────────────────────────────────

doc.add_paragraph()
doc.add_paragraph()

add_paragraph(
    "SISTEMA MULTIAGENTE BASADO EN APRENDIZAJE POR REFUERZO\n"
    "PARA LA PLANIFICACIÓN Y MONITOREO DE RUTAS OPERATIVAS\n"
    "EN EL TRANSPORTE DE CARGA",
    bold=True, size=16, align=WD_ALIGN_PARAGRAPH.CENTER
)

doc.add_paragraph()

add_paragraph(
    "Informe de Avance de Investigación — Trabajo de Investigación II",
    italic=True, size=13, align=WD_ALIGN_PARAGRAPH.CENTER
)

doc.add_paragraph()
doc.add_paragraph()

add_paragraph("Corredor Norte del Perú: Lima → Trujillo → Chiclayo → Piura → Sullana → Chulucanas",
              size=12, align=WD_ALIGN_PARAGRAPH.CENTER)

doc.add_paragraph()
doc.add_paragraph()

add_paragraph("Universidad Nacional de Ingeniería — FIIS", size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
add_paragraph("Grupo 8", size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
add_paragraph("Repositorio: https://github.com/fbarrioss/trabajo-investigacion-ii.git", size=10,
              align=WD_ALIGN_PARAGRAPH.CENTER)
add_paragraph("Fecha: Noviembre 2026", size=11, align=WD_ALIGN_PARAGRAPH.CENTER)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# TABLA DE CONTENIDOS (manual)
# ──────────────────────────────────────────────────────────────────────────────

add_heading("Índice", level=1)

indice = [
    "1. Metodología ejecutada",
    "2. Actividades pendientes",
    "3. Fuente de datos",
    "4. Preprocesamiento realizado",
    "5. Experimentos realizados",
    "6. Resultados preliminares y análisis crítico",
    "7. Bitácora / control de versiones en GitHub",
    "8. Evidencia reproducible",
    "9. Notebook propuesto (anexo)",
]
for item in indice:
    add_paragraph(item)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 1. METODOLOGÍA EJECUTADA
# ──────────────────────────────────────────────────────────────────────────────

add_heading("1. Metodología ejecutada", level=1)

add_paragraph(
    "La investigación sigue un pipeline reproducible de seis fases, alineado a los "
    "objetivos específicos (OE1–OE6) del plan de tesis. A la fecha se han completado "
    "las fases 1 a 4 (construcción de la red vial, dataset operativo, simulador "
    "Dec-POMDP y benchmark con datos reales), y la fase 5 (entrenamiento del modelo "
    "MARL) cuenta con el código implementado y verificado, pendiente de ejecución "
    "completa con los datos reales de la empresa."
)

add_heading("1.1 Fases del pipeline", level=2)

fases = [
    ["Fase 1: Red vial real (OE1)", "OpenStreetMap → topología consolidada", "Completada"],
    ["Fase 2: Dataset operativo (OE1)", "Vehículos, conductores, viajes, cargas, eventos", "Completada (datos sintéticos calibrados)"],
    ["Fase 3: Simulador Dec-POMDP (OE2)", "Entorno multiagente con eventos dinámicos", "Completada y verificada"],
    ["Fase 4: Benchmark real (OE3)", "500 viajes reales encajados en la red", "Completada"],
    ["Fase 5: Entrenamiento MARL (OE4–OE5)", "QMIX (principal) + MAPPO (contraste)", "Pendiente de ejecución completa"],
    ["Fase 6: Evaluación estadística (OE6)", "Mann-Whitney vs. baselines", "Pendiente"],
]
add_table(["Fase", "Descripción", "Estado"], fases)

add_heading("1.2 Alcance geográfico", level=2)

add_paragraph(
    "El profesor Wester Zela indicó (sesión del 16/10/2026) que el alcance debía "
    "acotarse a una zona con datos reales, rechazando el alcance inicial de "
    "\"transporte de carga en el Perú\". El corredor seleccionado concentra el 100 % "
    "de los viajes registrados por la empresa:"
)

corredor = [
    ["Lima", "Hub de origen (CEDI)", "—"],
    ["Trujillo", "Ciudad destino", "7"],
    ["Chiclayo", "Ciudad destino", "3"],
    ["Piura", "Ciudad destino", "3"],
    ["Sullana", "Ciudad destino", "2"],
    ["Chulucanas", "Ciudad destino", "2"],
    ["Total", "", "17"],
]
add_table(["Ciudad", "Rol", "Distribuidores"], corredor)

add_paragraph(
    "El corredor sigue la Panamericana Norte (PE-1N) más el ramal Piura → Chulucanas. "
    "Adicionalmente, la red incluye la Carretera Central a Huancayo y el ramal "
    "Piura–Paita como zonas de extensión, necesarias para reconstruir el 100 % de "
    "los 500 viajes reales."
)

add_heading("1.3 Selección del algoritmo MARL", level=2)

add_paragraph(
    "Se evaluaron cuatro candidatos (QMIX, MAPPO, MADDPG, VDN) con una matriz de "
    "decisión de cinco criterios ponderados, derivados de las restricciones del "
    "plan de tesis (sección 4.2.9):"
)

criterios = [
    ["C1. Naturaleza de la acción (25%)", "5", "5", "2", "5"],
    ["C2. Eficiencia muestral (25%)", "5", "2", "3", "5"],
    ["C3. Asignación de crédito (20%)", "4", "4", "4", "2"],
    ["C4. Estabilidad de convergencia (15%)", "4", "3", "2", "3"],
    ["C5. Costo de implementación (15%)", "3", "4", "2", "5"],
    ["Puntaje ponderado", "4.35", "3.60", "2.60", "4.00"],
]
add_table(["Criterio", "QMIX", "MAPPO", "MADDPG", "VDN"], criterios)

add_paragraph(
    "Decisión: QMIX como algoritmo principal (cooperativo, CTDE, value-based, "
    "off-policy) y MAPPO como contraste interno para robustecer la discusión de "
    "la hipótesis H5. MADDPG se descartó por estar diseñado para acciones "
    "continuas y VDN por no capturar interacciones no lineales entre vehículos."
)

add_heading("1.4 Modelo formal: Dec-POMDP", level=2)

add_paragraph(
    "El plan de tesis define un MDP ⟨S, A, P, R, γ⟩. Al intervenir varias unidades "
    "vehiculares con información local, el modelo se extiende a un Dec-POMDP "
    "cooperativo: ⟨N, S, {A_i}, {O_i}, P, R, γ⟩"
)

mdp = [
    ["N", "Agentes (unidades vehiculares)", "3 por episodio"],
    ["S", "Estado global", "58 dimensiones"],
    ["A_i", "Acciones del agente i", "4 acciones discretas"],
    ["O_i", "Observación local", "20 dimensiones normalizadas"],
    ["P", "Transición", "Movimiento sobre el grafo + eventos"],
    ["R", "Recompensa cooperativa", "Promedio de recompensas individuales"],
    ["γ", "Descuento", "0.99"],
]
add_table(["Símbolo", "Elemento", "Implementación"], mdp)

add_paragraph("Espacio de acciones (4 discretas):")
add_bullet("0 — Continuar: seguir el plan vigente sin recalcular")
add_bullet("1 — Tomar alterna: rodear el tramo planificado (válido si tiene_alterna = True, 95.4% de la red)")
add_bullet("2 — Esperar: detenerse un paso (15 min), costo S/ 64/h")
add_bullet("3 — Replanificar: recalcular ruta con Dijkstra ponderado por eventos")

add_heading("1.5 Esquema CTDE (Centralized Training, Decentralized Execution)", level=2)

ctde = [
    ["Entrenamiento", "Estado global (58 dims)", "Mezclador QMIX / crítico MAPPO"],
    ["Ejecución", "Observación local (20 dims)", "Red de agente / actor"],
]
add_table(["Fase", "Información", "Implementación"], ctde)

add_paragraph(
    "Esto refleja la operación real: la torre de control conoce la flota completa, "
    "pero cada vehículo decide con su información local."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 2. ACTIVIDADES PENDIENTES
# ──────────────────────────────────────────────────────────────────────────────

add_heading("2. Actividades pendientes", level=1)

add_paragraph(
    "Las siguientes actividades dependen tanto del equipo como de terceros "
    "(empresa, comité). Se enumeran por orden de impacto sobre la investigación."
)

pendientes = [
    ["Direcciones reales de los 17 distribuidores", "Wilson Ibarra", "Alto",
     "Reemplazar coordenadas placeholder en config/escenario_norte.yaml y reejecutar el paso 1"],
    ["Excel de entidades (recursos y operación)", "Wilson Ibarra", "Alto",
     "Sustituir los Parquet de data/processed/dataset/ con datos reales"],
    ["Datos GPS / tiempos reales de viaje", "Wilson Ibarra", "Medio",
     "Ajustar velocidad_flujo_kmh por tramo con datos observados"],
    ["Documento para el Comité (semana 8)", "Kenin", "Bloqueante",
     "Enviarle el resumen del comité con todas las secciones, referencias y anexos"],
    ["Revisión de simuladores existentes", "Wilson Ibarra", "Medio",
     "Buscar en literatura: SUMO, MATSim, VRP-RL"],
    ["Entrenamiento completo de QMIX", "Franklin", "Alto",
     "Ejecutar 2000 episodios × 5 semillas. Código implementado y verificado"],
    ["Entrenamiento de MAPPO (contraste)", "Franklin", "Medio",
     "Ejecutar para comparar H5. Código implementado"],
    ["Evaluación estadística vs. baselines", "Franklin", "Alto",
     "Mann-Whitney, IC 95%, n_bootstrap=10000. Código implementado"],
    ["Conectar alternativas al entorno", "Franklin", "Medio",
     "alternativas_od.csv tiene 14 rutas ejecutables; falta que el simulador las consuma"],
    ["Bloqueo físico de tramos restringidos en el entorno", "Franklin", "Bajo",
     "entorno_rutas.py penaliza pero no bloquea hgv=no. Son 21 aristas; no cambia rutas principales"],
]
add_table(["Pendiente", "Depende de", "Impacto", "Acción"], pendientes)

add_paragraph(
    "Nota metodológica: la red vial se construye con geometría real (OpenStreetMap); "
    "lo que es sintético es la operación (viajes, cargas, ventanas), calibrada con "
    "los volúmenes declarados por la empresa. Esto es consistente con la limitación "
    "declarada en el plan de tesis (sección 1.4.2)."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 3. FUENTE DE DATOS
# ──────────────────────────────────────────────────────────────────────────────

add_heading("3. Fuente de datos", level=1)

add_heading("3.1 Resumen de fuentes", level=2)

fuentes = [
    ["Red vial (geometría y topología)", "OpenStreetMap (Overpass API + extracto Geofabrik Perú)",
     "339 863 nodos OSM → 18 167 nodos operativos / 28 642 tramos", "100% real"],
    ["Distribuidores (17 puntos)", "farthest point sampling sobre nodos OSM urbanos",
     "17/17 sobre intersecciones reales", "Real (placeholder por dirección real pendiente)"],
    ["Viajes registrados (benchmark)", "EYM_V5_Viajes_Puntos_Entrega_Secuencias.xlsx",
     "500 viajes / 1 982 paradas / 25 puntos de entrega", "Real (extracto de la empresa)"],
    ["Catálogo de puntos de entrega", "puntos_entrega.xlsx",
     "25 puntos con coordenadas", "Real"],
    ["Operación (vehículos, conductores, eventos)", "Sintético calibrado con SUTRAN/MTC",
     "3 unidades/día, 4 tipos de evento, tasa 27.6% rutas afectadas", "Semi-sintético declarado"],
    ["Motor de rutas de referencia", "Valhalla 3.5.1 (PBF Perú, 257 MB)",
     "1 430 tiles, 4 452 692 aristas dirigidas", "Software open source"],
]
add_table(["Componente", "Fuente", "Cantidad", "Naturaleza"], fuentes)

add_heading("3.2 Selección y procedencia", level=2)

add_paragraph(
    "La red vial se descargó de OpenStreetMap mediante dos canales complementarios:"
)

add_bullet(
    "Overpass API: 10 bandas solapadas del corredor norte (Lima–Chulucanas) más "
    "6 redes urbanas de las ciudades del corredor. Se usó rotación de espejos y "
    "coincidencias exactas de highway (las regex agotan la memoria de los "
    "servidores públicos)."
)
add_bullet(
    "Extracto de Geofabrik: peru-latest.osm.pbf (257 MB, snapshot 2026-10-26) para "
    "las zonas de extensión (Carretera Central, ramal Piura–Paita, acceso al CEDI). "
    "Ambos son el mismo dataset OSM y los IDs de nodo son globales, por lo que la "
    "composición es consistente."
)

add_paragraph(
    "Los 500 viajes reales provienen de un extracto entregado por la empresa "
    "(EYM_V5_Viajes_Puntos_Entrega_Secuencias.xlsx) que cubre el período "
    "2025-12-07 a 2026-08-18 (244 días con viajes, 9 vehículos, 2.05 viajes/día). "
    "No es el histórico completo; se documenta el marco muestral y la comparación "
    "se hace pareada para que el sesgo de selección se cancele."
)

add_heading("3.3 Marco muestral", level=2)

muestra = [
    ["Viajes", "500 (1 982 paradas)"],
    ["Ventana temporal", "2025-12-07 → 2026-08-18 (244 días)"],
    ["Ritmo", "2.05 viajes por día con actividad"],
    ["Flota", "9 vehículos (~56 viajes por unidad)"],
    ["Puntos de entrega", "25"],
    ["Tramos recorridos", "1 796 de 28 642 (6.3%)"],
    ["Kilómetros recorridos", "2 074.4 de 14 017.1 (14.8%)"],
    ["Reutilización media por tramo activo", "147 viajes"],
]
add_table(["Dimensión", "Valor"], muestra)

add_paragraph(
    "La cobertura del 6.3% de los tramos no indica falta de información: la muestra "
    "recorre el 35% de las autopistas y el 27% de las carreteras troncales, pero "
    "apenas roza la red terciaria urbana. El universo grande existe para que el "
    "agente tenga alternativas de desvío, no porque se espere tráfico en cada tramo."
)

add_paragraph(
    "El sesgo de selección se maneja con comparación pareada: el mismo viaje, con "
    "los mismos eventos y la misma ventana horaria, resuelto por la política "
    "histórica y por el MARL. Cualquier sesgo afecta a ambas políticas por igual "
    "y se cancela en la diferencia. Además, el histórico no se usa para entrenar "
    "(el modelo entrena con episodios sintéticos calibrados); el extracto sirve "
    "para calibrar distribuciones y validar contra la operación real."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 4. PREPROCESAMIENTO REALIZADO
# ──────────────────────────────────────────────────────────────────────────────

add_heading("4. Preprocesamiento realizado", level=1)

add_paragraph(
    "A la fecha, el preprocesamiento de la red vial y los mapas está completo. "
    "El preprocesamiento del modelo (dataset para entrenamiento) está implementado "
    "pero pendiente de ejecución con datos reales."
)

add_heading("4.1 Red vial: de OSM a topología operativa (COMPLETADO)", level=2)

add_paragraph("Pipeline de seis pasos sobre datos reales de OpenStreetMap:")

pasos_red = [
    ["1", "Descarga del corredor en 10 bandas solapadas", "Vías motorway→secondary Lima–Chulucanas"],
    ["2", "Descarga radial de la red urbana de 6 ciudades", "Calles de acceso a distribuidores"],
    ["3", "Composición (nx.compose) + componente débil mayor", "Un único grafo conexo"],
    ["4", "Filtro de red de carga (jerarquía ≥ tertiary)", "Elimina calles residenciales"],
    ["5", "Consolidación (contracción de cadenas de grado 2)", "Nodos de decisión + tramos"],
    ["6", "Ubicación de ciudades y 17 distribuidores sobre nodos OSM", "ciudades.csv, distribuidores.csv"],
]
add_table(["Paso", "Acción", "Resultado"], pasos_red)

add_paragraph("Reglas de limpieza adicionales implementadas:")
add_bullet("Lazos descartados: glorietas y retornos que producían self-loops (ini == fin)")
add_bullet("Corte por cambio de sentido: si el sentido cambia en un nodo de grado 2, ese nodo pasa a ser punto de decisión")
add_bullet("IDs deterministas: id_tramo se asigna al final, ordenando por nodos extremos y distancia")
add_bullet("Sentido de circulación real: derivado de las aristas dirigidas de OSM (oneway), no supuesto")
add_bullet("Restricciones de pesados: hgv, maxweight, maxheight, maxwidth, maxlength normalizados")

add_heading("4.2 Resultado de la red vial construida", level=2)

red_resultado = [
    ["Nodos operativos (extremos de tramo)", "18 167"],
    ["Nodos OSM procesados", "339 863"],
    ["Nodos de geometría contraídos", "321 664"],
    ["Tramos consolidados", "28 642"],
    ["Longitud total de la red", "14 017.1 km"],
    ["Ciudades asociadas", "8 (6 del corredor + Huancayo y Paita)"],
    ["Distribuidores ubicados", "17 / 17"],
    ["Grafo débilmente conexo", "Sí"],
    ["Completitud de atributos", "100%"],
    ["Tramos con desvío alterno (tiene_alterna)", "95.4%"],
    ["Tramos unidireccionales", "23 567 (82.3%)"],
    ["Tramos con restricción de pesados", "206 (102.4 km)"],
]
add_table(["Indicador", "Valor"], red_resultado)

add_heading("4.3 Validación de la red (H1)", level=2)

validacion = [
    ["Completitud de atributos", "≥ 90%", "100%"],
    ["Consistencia referencial (tramos → nodos)", "≥ 95%", "100%"],
    ["Duplicados de id_nodo / id_tramo", "0", "0"],
    ["Valores no positivos (distancia, tiempo, velocidad)", "0", "0"],
    ["Snap ciudad → red", "≤ 800 m", "máx. 227.5 m"],
    ["Grafo débilmente conexo", "Sí", "Sí"],
    ["Alcanzabilidad hub → distribuidor", "17/17", "17/17"],
]
add_table(["Indicador", "Umbral H1", "Valor actual"], validacion)

add_heading("4.4 Validación externa con motor independiente", level=2)

add_paragraph(
    "La red se construyó sin usar ningún motor de ruteo. Contrastarla después con "
    "OSRM y Valhalla es, por tanto, una prueba independiente:"
)

validacion_externa = [
    ["Lima → Trujillo", "547.6", "557.1 (OSRM)", "−1.7%"],
    ["Trujillo → Chiclayo", "203.2", "199.5 (OSRM)", "+1.9%"],
    ["Chiclayo → Piura", "212.8", "214.7 (OSRM)", "−0.9%"],
    ["Piura → Sullana", "39.3", "38.8 (OSRM)", "+1.3%"],
    ["Piura → Chulucanas", "61.6", "60.8 (OSRM)", "+1.3%"],
    ["Total corredor", "1 064.5", "1 070.9 (OSRM)", "−0.6%"],
]
add_table(["Tramo", "Red construida (km)", "OSRM (km)", "Desvío"], validacion_externa)

add_paragraph(
    "Contra Valhalla (perfil auto), el desvío medio es +0.73%, lo que confirma que "
    "la topología derivada de OSM reproduce el corredor real con menos de un punto "
    "porcentual de error, medido por un motor ajeno a su construcción."
)

add_heading("4.5 Integración de viajes reales (COMPLETADO)", level=2)

add_paragraph(
    "Los 500 viajes reales de la empresa se encajaron sobre el universo de 28 642 "
    "tramos mediante:"
)
add_bullet("Geocodificación con Nominatim (25 puntos, 3 variantes de consulta, umbral ≤500 m)")
add_bullet("Snapping a la red (haversine, tolerancia 2 000 m) → 0 puntos fuera de cobertura")
add_bullet("Reconstrucción de rutas: Dijkstra por tiempo depósito → parada 1 → … → última")
add_bullet("100% de los 500 viajes reconstruidos sobre la red real, sin errores de ruta")

integracion = [
    ["Viajes válidos", "500 de 500 (100%)"],
    ["Km medio por red", "850.6 km"],
    ["Km medio geodésico", "704.2 km"],
    ["Ratio red/geodésica", "1.21"],
    ["Duración media", "628 min (10.5 h)"],
    ["Paradas medias por viaje", "3.96"],
    ["Snap medio de los 25 puntos", "242 m (mediana 116 m)"],
    ["Snap máximo", "1 194.5 m (GEO-010, Trujillo)"],
]
add_table(["Indicador", "Valor"], integracion)

add_heading("4.6 Preprocesamiento del modelo (PENDIENTE)", level=2)

add_paragraph(
    "El preprocesamiento del dataset de entrenamiento está implementado en "
    "scripts/02_dataset.py y genera los Parquet de data/processed/dataset/ con "
    "vehículos, conductores, viajes, cargas y eventos calibrados con SUTRAN/MTC. "
    "Sin embargo, estos datos son sintéticos calibrados con los volúmenes "
    "declarados por la empresa. Cuando Wilson entregue el Excel de entidades "
    "real, se sustituirán los Parquet y se regenerará el dataset. El pipeline es "
    "idempotente: scripts/06_integrar_viajes.py reprocesa todo sin cambiar el modelo."
)

add_heading("4.7 Motor de rutas Valhalla (COMPLETADO)", level=2)

add_paragraph(
    "Se incorporó Valhalla 3.5.1 como motor de ruteo externo con perfil de camión "
    "(truck: max_weight, max_height, max_width, max_length, axle_load). Se usa "
    "fuera del bucle de aprendizaje, en tres papeles:"
)
add_bullet("Línea de base: ruta de referencia por par OD con perfil camión real")
add_bullet("Generación de candidatas: K alternativas reales por par OD")
add_bullet("Validación independiente: distancia y tiempo por un motor ajeno")

add_paragraph(
    "Hallazgo clave: el corredor se rompe a 37 toneladas. LIM–TRU son 609.6 km "
    "hasta 36 t y 953.1 km a partir de 37 t (+56%). Ningún otro par del corredor "
    "es sensible al peso. El camión de referencia debe quedar en 36 t o menos."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 5. EXPERIMENTOS REALIZADOS
# ──────────────────────────────────────────────────────────────────────────────

add_heading("5. Experimentos realizados", level=1)

add_paragraph(
    "A la fecha se han realizado experimentos relacionados con la red vial, mapas "
    "y benchmark. Los experimentos de entrenamiento del modelo MARL están "
    "implementados y verificados pero pendientes de ejecución completa con los "
    "datos reales."
)

add_heading("5.1 Experimentos de la red vial (COMPLETADOS)", level=2)

add_paragraph("Se han realizado los siguientes experimentos sobre la red:")

exp_red = [
    ["E1: Construcción de la red", "scripts/01_red_vial.py",
     "18 167 nodos, 28 642 tramos, 14 017.1 km, grafo conexo", "Completado"],
    ["E2: Validación con OSRM", "Contraste de macro-tramos",
     "Desvío total −0.6% (5 pares del corredor)", "Completado"],
    ["E3: Validación con Valhalla", "scripts/08_rutas_valhalla.py",
     "Desvío medio +0.73% (perfil auto), +2.25% (truck)", "Completado"],
    ["E4: Diagnóstico de movimiento", "scripts/05_diagnostico_movimiento.py",
     "3 agentes, 240 pasos, 986.8/994.9/563.1 km, 6/6 entregas", "Completado"],
    ["E5: Benchmark de viajes reales", "scripts/06_integrar_viajes.py",
     "500/500 viajes reconstruidos, 850.6 km medio", "Completado"],
    ["E6: Marco muestral", "scripts/07_cobertura_muestra.py",
     "6.3% tramos usados, 35.2% autopistas cubiertas", "Completado"],
    ["E7: Alternativas de ruta", "scripts/09_alternativas.py",
     "21 rutas (7 principales + 14 alternativas)", "Completado"],
    ["E8: Sensibilidad al peso", "scripts/08_rutas_valhalla.py --sensibilidad-peso",
     "Umbral en 36 t; LIM–TRU pasa de 609.6 a 953.1 km a 37 t", "Completado"],
    ["E9: Dashboard autocontenido", "dashboard/build_dashboard.py",
     "5.37 MB, 28 642 tramos embebidos, desplegado en Vercel", "Completado"],
]
add_table(["Experimento", "Script/Herramienta", "Resultado clave", "Estado"], exp_red)

add_heading("5.2 Diagnóstico de movimiento sobre la red real", level=2)

add_paragraph(
    "El script 05_diagnostico_movimiento.py ejecuta un episodio con la política "
    "trivial «continuar» y verifica que los vehículos recorren la topología OSM:"
)

diag = [
    ["Red cargada", "18 167 nodos, 28 642 tramos"],
    ["Episodio", "3 agentes, 240 pasos de 15 min (60 h)"],
    ["Km recorridos", "986.8 / 994.9 / 563.1 km por agente"],
    ["Entregas completadas", "6 de 6, sin pendientes"],
    ["Planes generados", "1 por agente (506, 499 y 278 nodos)"],
    ["Esperas / replanificaciones", "0 / 0 con política «continuar»"],
    ["Descanso de jornada", "45 min cada 5 h de conducción"],
]
add_table(["Verificación", "Resultado"], diag)

add_paragraph("Las cuatro acciones producen resultados distintos y coherentes:")

acciones = [
    ["0 · continuar", "986.8 / 994.9 / 563.1", "6 / 6"],
    ["1 · tomar alterna", "1 007.0 / 1 024.1 / 582.3 (+2.0%)", "6 / 6"],
    ["2 · esperar", "9.9 / 9.9 / 9.9 (239 esperas)", "0 / 6"],
    ["3 · replanificar", "986.8 / 994.9 / 563.1 (12 replanificaciones)", "6 / 6"],
]
add_table(["Acción", "Km (AG-0 / AG-1 / AG-2)", "Entregas"], acciones)

add_paragraph(
    "La acción «tomar alterna» no es una acción muerta: encarece la ruta un 2% "
    "real sobre el grafo OSM. La acción «esperar» agota el horizonte sin entregar."
)

add_heading("5.3 Experimentos del modelo MARL (PENDIENTES)", level=2)

add_paragraph(
    "El código de entrenamiento está implementado y verificado. Se han ejecutado "
    "pruebas preliminares con QMIX y MAPPO (logs disponibles en artifacts/logs/) "
    "que muestran que el pipeline funciona: las curvas de recompensa muestran "
    "tendencia de mejora en los primeros episodios. Sin embargo, falta la "
    "ejecución completa con los hiperparámetros del plan:"
)

plan_entrenamiento = [
    ["QMIX (principal)", "2000", "1, 2, 3", "5×10⁻⁴", "32 / 50 000"],
    ["MAPPO (contraste)", "2000", "1", "3×10⁻⁴", "2048 (on-policy)"],
]
add_table(["Algoritmo", "Episodios", "Semillas", "Learning rate", "Batch / Buffer"], plan_entrenamiento)

add_paragraph(
    "La evaluación se realizará contra tres baselines: ruta más corta (Dijkstra), "
    "heurística VRP (OR-Tools) y política estática, con Mann-Whitney (α=0.05) "
    "y bootstrap de 10 000 iteraciones para los intervalos de confianza."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 6. RESULTADOS PRELIMINARES Y ANÁLISIS CRÍTICO
# ──────────────────────────────────────────────────────────────────────────────

add_heading("6. Resultados preliminares y análisis crítico", level=1)

add_paragraph(
    "Los resultados preliminares corresponden a la red vial, el benchmark y las "
    "pruebas de movimiento. Los resultados del modelo MARL están pendientes."
)

add_heading("6.1 Resultados de la red vial", level=2)

add_paragraph("La red construida desde OSM alcanza los siguientes indicadores:")

resultados_red = [
    ["18 167 nodos / 28 642 tramos", "Red densa y navegable, equivalente a motores comerciales"],
    ["14 017.1 km de longitud total", "Cobertura completa del corredor + zonas de extensión"],
    ["100% completitud de atributos", "Supera el umbral H1 (≥ 90%)"],
    ["95.4% de tramos con desvío", "La acción «tomar alterna» tiene efecto medible en el 91% del recorrido óptimo"],
    ["82.3% tramos unidireccionales", "Sentido de circulación real de OSM, no supuesto"],
    ["206 tramos con restricción de pesados (102.4 km)", "Restricciones concentradas en salida de Lima (Vía Expresa, Javier Prado)"],
    ["Desvío total vs OSRM: −0.6%", "Validación externa independiente exitosa"],
    ["Desvío medio vs Valhalla auto: +0.73%", "La topología reproduce el corredor real con <1% de error"],
]
add_table(["Indicador", "Interpretación"], resultados_red)

add_heading("6.2 Resultados del benchmark con datos reales", level=2)

resultados_benchmark = [
    ["500/500 viajes reconstruidos", "Cobertura total del extracto de la empresa"],
    ["0 puntos fuera de cobertura", "Los 25 puntos están sobre la red real"],
    ["850.6 km medio por red", "Consistente con operación de larga distancia"],
    ["Ratio red/geodésica 1.21", "Coherente con sinuosidad de Carretera Central"],
    ["35.2% de autopistas cubiertas", "La muestra concentra tráfico de alta jerarquía"],
    ["1 796 tramos usados de 28 642", "El universo grande es para alternativas, no para tráfico"],
]
add_table(["Indicador", "Interpretación"], resultados_benchmark)

add_heading("6.3 Resultados de Valhalla: restricciones de pesados", level=2)

valhalla_resultados = [
    ["LIM–TRU", "609.6", "547.6", "+11.32%", "560.1", "+49.5 km"],
    ["TRU–CHI", "208.6", "203.2", "+2.66%", "208.6", "0.0 km"],
    ["CHI–PIU", "217.6", "212.8", "+2.26%", "215.6", "+2.0 km"],
    ["PIU–SUL", "39.0", "39.3", "−0.76%", "39.0", "0.0 km"],
    ["PIU–CHU", "60.9", "61.6", "−1.14%", "60.9", "0.0 km"],
    ["LIM–HUY", "306.6", "303.4", "+1.05%", "306.1", "+0.5 km"],
    ["PIU–PAI", "58.0", "57.8", "+0.35%", "57.7", "+0.3 km"],
]
add_table(["Par", "Valhalla truck (km)", "Red propia (km)", "Desvío", "Auto (km)", "Sobrecosto pesados"], valhalla_resultados)

add_paragraph(
    "Dos lecturas: (1) Contra el perfil auto, el desvío medio es +0.73% — la red "
    "propia reproduce la topología real con <1% de error. (2) El desvío de +2.25% "
    "del perfil truck no es un error de la red: es la restricción de pesados. "
    "Casi todo el sobrecosto (+52.3 km de 55.3) está en la salida de Lima, donde "
    "las expresas tienen hgv=no."
)

add_heading("6.4 Resultados preliminares del entrenamiento", level=2)

add_paragraph(
    "Se ejecutaron pruebas preliminares de QMIX (200 episodios) y MAPPO (25 "
    "episodios) con la semilla 1. Los logs están en artifacts/logs/. Las curvas "
    "muestran:"
)
add_bullet("QMIX: la recompensa promedio pasa de −10.76 (ep. 1) a valores positivos (+4 a +12) a partir del episodio ~60, con oscilación decreciente")
add_bullet("MAPPO: muestra una convergencia más rápida en los primeros 25 episodios pero con mayor varianza")
add_bullet("La pérdida de QMIX se estabiliza alrededor de 0.03–0.05 tras el episodio 50")
add_bullet("La puntualidad alcanza 100% en episodios donde el agente aprende a no desviarse innecesariamente")

add_paragraph(
    "Estos resultados son preliminares y se deben tomar con cautela: falta la "
    "ejecución completa con 2000 episodios × múltiples semillas y la evaluación "
    "estadística formal."
)

add_heading("6.5 Análisis crítico", level=2)

add_paragraph("Fortalezas identificadas:")

add_bullet("La red vial es 100% real y validada por dos motores independientes (OSRM y Valhalla) con error < 2%")
add_bullet("El benchmark cubre el 100% de los viajes reales de la empresa, sin puntos fuera de cobertura")
add_bullet("El simulador Dec-POMDP está verificado: las cuatro acciones producen resultados distintos y coherentes")
add_bullet("El esquema CTDE es consistente con la operación real (torre de control vs. decisión local)")
add_bullet("QMIX fue seleccionado con una matriz de decisión transparente y ponderada, no por preferencia subjetiva")
add_bullet("La reproducibilidad está garantizada: todas las fuentes de aleatoriedad derivan de la semilla configurada")

add_paragraph("Debilidades y riesgos:")

add_bullet("Los datos operativos (viajes, cargas, ventanas) son sintéticos calibrados, no reales — depende de Wilson")
add_bullet("Las direcciones de los 17 distribuidores son placeholders georreferenciados, no direcciones reales")
add_bullet("Sin GPS continuo: la discretización es por tramos con tiempos de flujo libre, no observados")
add_bullet("Pares con coordenada colapsada (GEO-008/009, GEO-018/021): limitación del dato de origen")
add_bullet("El entorno penaliza pero no bloquea físicamente los tramos con hgv=no (21 aristas, bajo impacto)")
add_bullet("Valhalla devuelve alternativas en solo 2 de 7 pares con perfil truck: el corredor es prácticamente un eje único para pesados")
add_bullet("El entrenamiento completo está pendiente y el plazo (semana 8) es ajustado")

add_paragraph(
    "El riesgo principal es la dependencia de Wilson para los datos reales. Si no "
    "se entregan antes de la semana 8, el modelo se entrenará con datos "
    "sintéticos calibrados, lo cual es metodológicamente válido (declarado en el "
    "plan de tesis) pero no cumple la exigencia del profesor de \"datos reales "
    "para tesis\". El pipeline está diseñado para incorporarlos sin cambios: "
    "solo se sustituyen los Parquet de data/processed/dataset/."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 7. BITÁCORA / CONTROL DE VERSIONES EN GITHUB
# ──────────────────────────────────────────────────────────────────────────────

add_heading("7. Bitácora / control de versiones en GitHub", level=1)

add_paragraph(
    "El repositorio se mantiene en GitHub con control de versiones desde el inicio "
    "del proyecto. Cada commit documenta un avance verificable:"
)

add_paragraph("Repositorio: https://github.com/fbarrioss/trabajo-investigacion-ii.git")
add_paragraph("Commits totales: 15")
add_paragraph("Periodo: 2026-10-17 a 2026-10-30")

add_heading("7.1 Historial de commits", level=2)

commits = [
    ["2026-10-17", "304cb63", "feat: red vial completa + dashboard responsive"],
    ["2026-10-27", "44baa7e", "feat(red): cobertura completa — Carretera Central, Paita y acceso al CEDI"],
    ["2026-10-27", "dddb46d", "fix(mapa): título del ámbito extendido en el mapa de la red"],
    ["2026-10-27", "ef67b5e", "feat(dashboard): red completa, tramos completos de extensión y descarga GeoJSON"],
    ["2026-10-27", "9fea3ca", "docs(entorno): la observación local tiene 20 dimensiones, no 18"],
    ["2026-10-27", "3eba842", "feat(dashboard): pestaña Modelo con los tres puntos del asesor"],
    ["2026-10-27", "fa8b645", "feat(benchmark): marco muestral del extracto de viajes"],
    ["2026-10-27", "06d49ef", "feat(dashboard): artefactos del benchmark descargables y despliegue en Vercel"],
    ["2026-10-30", "a830f0f", "fix(red): purga de lazos, sentido de circulación real y restricciones de pesados"],
    ["2026-10-30", "a3af222", "feat(rutas): motor Valhalla con perfil camión y alternativas por par OD"],
    ["2026-10-30", "d8dc4f1", "docs(readme): cómo levantar Valhalla y diferencia entre --recalcular y --reconstruir"],
    ["2026-10-30", "1798312", "fix(valhalla): montar el PBF dentro de custom_files en vez de tile_urls"],
    ["2026-10-30", "3f2f5fb", "feat(rutas): contraste auto y sensibilidad al peso en el motor Valhalla"],
    ["2026-10-30", "9cbe60b", "feat(rutas): K alternativas ejecutables sobre la red propia"],
    ["2026-10-30", "ef175c7", "docs(readme): sección completa de Valhalla con instalación y uso"],
]
add_table(["Fecha", "Hash", "Mensaje"], commits)

add_heading("7.2 Convenciones de commits", level=2)

add_paragraph(
    "Se siguen convenciones estándar de conventional commits (feat, fix, docs) "
    "con scope descriptivo (red, mapa, dashboard, benchmark, rutas, valhalla, "
    "readme, entorno). Cada commit es atómico y verificable."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 8. EVIDENCIA REPRODUCIBLE
# ──────────────────────────────────────────────────────────────────────────────

add_heading("8. Evidencia reproducible", level=1)

add_paragraph(
    "El repositorio contiene todo el código fuente, los datos preprocesados y los "
    "notebooks necesarios para reproducir los resultados. La reproducibilidad "
    "está garantizada por:"
)
add_bullet("Semilla fija (20261016) que controla todas las fuentes de aleatoriedad")
add_bullet("Pipeline idempotente: scripts reprocesan sin cambiar el modelo")
add_bullet("Caché de descargas OSM y geocodificación para evitar llamadas repetidas")
add_bullet("requirements.txt con todas las dependencias versionadas")
add_bullet("Docker Compose para Valhalla (infra/valhalla/docker-compose.yml)")

add_heading("8.1 Código fuente", level=2)

codigo = [
    ["src/red_vial/", "descargar_osm.py, construir_red.py, topologia.py, corredores.py, exportar.py, validar_red.py"],
    ["src/datos/", "esquema.py, eventos.py, generar_operacion.py, integrar_viajes.py"],
    ["src/entorno/", "entorno_rutas.py (simulador Dec-POMDP)"],
    ["src/agentes/", "qmix.py, mappo.py, baselines.py, redes.py"],
    ["src/entrenamiento/", "entrenar.py, evaluar.py"],
    ["src/comun/", "config.py, geo.py"],
    ["scripts/", "00_cortar_extracto.py → 09_alternativas.py (10 scripts CLI)"],
    ["dashboard/", "build_dashboard.py, index.html, plantilla.html"],
    ["config/", "escenario_norte.yaml, entrenamiento.yaml"],
    ["docs/", "01–07 documentación metodológica + diccionario_datos.json"],
    ["infra/valhalla/", "docker-compose.yml"],
]
add_table(["Directorio", "Archivos clave"], codigo)

add_heading("8.2 Datos preprocesados", level=2)

datos_proc = [
    ["data/processed/red_vial/nodos.csv", "18 167 nodos operativos (ID OSM, lat, lon, grado, tipo)"],
    ["data/processed/red_vial/tramos.csv", "28 642 tramos (distancia, tiempo, velocidad, sentido, restricciones)"],
    ["data/processed/red_vial/corredor_macro.csv", "7 tramos completos entre ciudades (5 corredor + 2 extensión)"],
    ["data/processed/red_vial/ciudades.csv", "8 ciudades con nodo OSM y desvío de snap"],
    ["data/processed/red_vial/distribuidores.csv", "17 distribuidores sobre intersecciones reales"],
    ["data/processed/red_vial/topologia.graphml", "Grafo dirigido para el simulador"],
    ["data/processed/red_vial/red_vial.geojson", "Trazas reales para QGIS / visualización"],
    ["data/processed/red_vial/resumen.json", "Métricas, macro-tramos y validación"],
    ["data/processed/benchmark/benchmark_viajes.csv", "500 viajes con km/min por red"],
    ["data/processed/benchmark/benchmark_paradas.csv", "1 982 paradas con nodo y tiempos"],
    ["data/processed/benchmark/puntos_snap.csv", "25 puntos con coordenada final y nodo"],
    ["data/processed/benchmark/cobertura_muestra.json", "Marco muestral del extracto"],
    ["data/processed/valhalla/rutas_od.csv", "Rutas de referencia con perfil camión"],
    ["data/processed/valhalla/sensibilidad_peso.csv", "Barrido peso × par OD"],
    ["data/processed/alternativas/alternativas_od.csv", "21 rutas (7 principales + 14 alternativas)"],
    ["artifacts/logs/qmix_semilla1.csv", "Log de entrenamiento QMIX (200 episodios)"],
    ["artifacts/logs/mappo_semilla1.csv", "Log de entrenamiento MAPPO (25 episodios)"],
    ["artifacts/modelos/qmix_s1.pt", "Modelo QMIX guardado (semilla 1)"],
]
add_table(["Archivo", "Contenido"], datos_proc)

add_heading("8.3 Comandos de reproducción", level=2)

add_paragraph("Pipeline completo desde cero:", bold=True)

comandos = [
    "1. Descargar la red: python -m src.red_vial.descargar_osm todo",
    "2. Construir la topología: python scripts/01_red_vial.py",
    "3. Generar el dataset: python scripts/02_dataset.py --viajes 180",
    "4. Verificar el simulador: python scripts/05_diagnostico_movimiento.py",
    "5. Benchmark de viajes reales: python scripts/06_integrar_viajes.py",
    "6. Marco muestral: python scripts/07_cobertura_muestra.py",
    "7. Rutas de referencia (Valhalla): python scripts/08_rutas_valhalla.py",
    "8. Alternativas: python scripts/09_alternativas.py",
    "9. Entrenar QMIX: python scripts/03_entrenar.py --algoritmo qmix --episodios 2000 --semillas 1 2 3",
    "10. Evaluar: python scripts/04_evaluar.py --modelo artifacts/modelos/qmix_s1.pt --episodios 30",
]
for cmd in comandos:
    add_paragraph(cmd)

add_heading("8.4 Notebook propuesto", level=2)

add_paragraph(
    "Se incluye un notebook Jupyter inicial (notebook_investigacion.ipynb) que "
    "resume el pipeline y permite ejecutar paso a paso los componentes principales. "
    "El notebook no requiere preprocesamiento previo: carga la red ya construida "
    "y muestra cómo se vería el flujo de investigación."
)

doc.add_page_break()


# ──────────────────────────────────────────────────────────────────────────────
# 9. CONCLUSIONES Y PRÓXIMOS PASOS
# ──────────────────────────────────────────────────────────────────────────────

add_heading("9. Conclusiones y próximos pasos", level=1)

add_paragraph(
    "El proyecto se encuentra en un estado avanzado de la fase de infraestructura: "
    "la red vial real está construida y validada, el benchmark con 500 viajes "
    "reales está completo, el simulador Dec-POMDP está verificado, y el código "
    "de entrenamiento QMIX/MAPPO está implementado. Lo que falta es la ejecución "
    "completa del entrenamiento y la evaluación estadística, que dependen "
    "principalmente de los datos reales pendientes de Wilson."
)

add_paragraph("Próximos pasos inmediatos:")
add_numbered("Recibir direcciones reales de los 17 distribuidores (Wilson)")
add_numbered("Recibir Excel de entidades (recursos y operación) (Wilson)")
add_numbered("Sustituir datos sintéticos por reales en data/processed/dataset/")
add_numbered("Ejecutar entrenamiento completo: QMIX 2000 episodios × 5 semillas")
add_numbered("Ejecutar entrenamiento MAPPO como contraste")
add_numbered("Realizar evaluación estadística: Mann-Whitney vs. 3 baselines")
add_numbered("Preparar presentación para la semana 8 (examen parcial)")

add_paragraph(
    "El pipeline está diseñado para que la incorporación de datos reales no "
    "requiera cambios de código: solo se sustituyen archivos Parquet y se "
    "re ejecutan los scripts. La idempotencia del pipeline garantiza la "
    "reproducibilidad de los resultados."
)


# ──────────────────────────────────────────────────────────────────────────────
# Guardar
# ──────────────────────────────────────────────────────────────────────────────

output_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Informe_Avance_Investigacion.docx"
)
doc.save(output_path)
print(f"Documento guardado en: {output_path}")
