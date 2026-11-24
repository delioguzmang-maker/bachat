#!/usr/bin/env python3
"""
Explica cada clase en espanol sencillo y le crea un cuaderno con Audio Overview
en Gemini Notebook (NotebookLM Enterprise).

Toma las transcripciones que deja dce_downloader.py (en ingles):

    Clases/HIST E-1900 - Not Like US- .../L01 - 2026-01-27 - Lecture.txt

y para cada una:

  1. Divide la transcripcion en partes (~15.000 caracteres, unos 20 minutos de clase)
     y pasa cada parte por los 7 subagentes del "Topic Simplifier Agent" y por el
     agente principal, esperando a que termine cada uno:

        1 Deep Researcher        (con busqueda en Google)   \
        2 Concept Analyzer       (con busqueda en Google)    > fase 1: todas las partes
        3 Contextualizer         (con busqueda en Google)   /  a la vez (investigacion)
        4 Analogy Creator        (con busqueda en Google)   \
        5 Jargon Buster                                       \
        6 Agente principal       (escribe la explicacion)      > fase 2: parte por parte,
        7 Format Enforcer        (oraciones cortas)          /  EN ORDEN
        8 Vocabulary Checker     (palabras sencillas)       /

     En la fase 2 cada parte recibe la lista de conceptos que ya se explicaron y el
     final de la parte anterior: asi no se repiten definiciones ni analogias y el
     texto sigue el hilo de la clase.

     Cada paso es una llamada al asistente de Gemini Enterprise (streamAssist) con
     las instrucciones de ese subagente y lo que entregaron los pasos anteriores. El
     asistente escribe su resultado entre <entregable> y </entregable>; lo que ponga
     fuera (saludos, "Aqui tienes...", preguntas) se descarta sin tocar el texto.
     Lo que devuelve cada subagente se guarda en

        Clases/_simplificador/pasos/<curso>/<clase>/b01_1_deep_researcher.md ...

     asi puedes ver que trabajaron todos. Si Colab se desconecta, al volver a
     ejecutar se sigue desde el ultimo paso guardado. Un paso que fallo nunca se
     guarda: se vuelve a intentar.

  2. Guarda la explicacion final junto a la transcripcion:
        .../L01 - 2026-01-27 - Lecture_simplificado_es.txt

  3. Crea un cuaderno "HIST E-1900 · L01 - 2026-01-27 - Lecture" en Gemini Notebook,
     le sube 2 fuentes (transcripcion en ingles + explicacion en espanol) y pide el
     Audio Overview en espanol con un enfoque automatico.

Por que no se llama directamente al agente del disenador de agentes: ese agente es
un "chat agent". Por la API, el agente principal le pasa la pregunta a UN subagente
(el Deep Researcher) y la respuesta de ese subagente es la que vuelve. Por eso salian
"Informes de Investigacion Profunda" en vez de la explicacion final. Aqui el script
hace de orquestador: llama a cada subagente, uno detras de otro, con sus instrucciones.

Las instrucciones de los subagentes se guardan en
    Clases/_simplificador/instrucciones_agentes.json
Si editas alguna ahi (por ejemplo, pegando el texto exacto de tu agente), se usa tu
version. Las que no editaste se actualizan solas cuando el script trae una nueva.

Uso (en Colab, despues de `gcloud auth login` con la cuenta de Gemini Enterprise):
    python simplificador_clases.py --carpeta /content/drive/MyDrive/Clases --probar
    python simplificador_clases.py --carpeta /content/drive/MyDrive/Clases --max-clases 1
    python simplificador_clases.py --carpeta ... --limpiar            # ver cuadernos repetidos
    python simplificador_clases.py --carpeta ... --limpiar --borrar   # y borrarlos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

HOST_GLOBAL = "discoveryengine.googleapis.com"
CARPETA_INTERNA = "_simplificador"
SUFIJO_EXPLICACION = "_simplificado_es.txt"
REGISTRO = "registro_clases_procesadas.json"
INSTRUCCIONES = "instrucciones_agentes.json"

# --------------------------------------------------------------------------
# Instrucciones del agente y de los subagentes (se pueden editar en Drive)
# --------------------------------------------------------------------------

AGENTE_PRINCIPAL = (
    "Eres el Topic Simplifier Agent: un experto en simplificar temas y el orquestador de "
    "7 subagentes. Tu objetivo es ayudar a entender temas complejos dividiéndolos en "
    "explicaciones muy simples. El resultado debe ser extremadamente fácil de leer, aprender "
    "y comprender. Eres el ÚNICO agente que se comunica con el usuario: sintetizas los "
    "hallazgos de todos tus subagentes en una respuesta final cohesiva que explica todos los "
    "términos técnicos. El formato final no usa listas ni viñetas, responde al 'por qué' de "
    "las cosas, evita palabras genéricas y está completamente en español."
)

INSTRUCCIONES_POR_DEFECTO = {
    "agente_principal": AGENTE_PRINCIPAL,
    "1_deep_researcher": (
        "Eres el Deep Researcher (Investigador Profundo). Tu trabajo es analizar profundamente "
        "el texto proporcionado e identificar cualquier pregunta sin respuesta, referencias "
        "vagas, términos técnicos o procesos que necesiten explicación. Debes investigar las "
        "afirmaciones técnicas usando Google Search para encontrar respuestas definitivas y "
        "fácticas. No adivines ni des ejemplos hipotéticos. Entrega un reporte de investigación "
        "completo en español con detalles y hechos exactos (enumerando las preguntas y términos "
        "junto con sus respuestas fácticas) para que lo usen los demás subagentes."
    ),
    "2_concept_analyzer": (
        "Eres el Concept Analyzer (Analizador de Conceptos). Dado un tema complejo, tu trabajo "
        "es identificar y enumerar los conceptos o términos centrales que son esenciales para "
        "comprender el tema pero que podrían ser difíciles para un principiante. Puedes usar "
        "Google Search. Entrega una lista clara de esos conceptos, enfocandote en los difíciles "
        "para principiantes."
    ),
    "3_contextualizer": (
        "Eres el Contextualizer (Contextualizador). Tu rol es dar un contexto o trasfondo muy "
        "breve y simple del tema para conectarlo con la realidad. Explica por qué el tema "
        "importa o de dónde viene, investigando y explicando con mucho detalle los motivos "
        "('por qué' y 'para qué'). Identifica detalles específicos (lugares, acciones, objetos, "
        "personas) y responde de forma proactiva las dudas que tendría un lector (por ejemplo, "
        "'¿por qué este lugar?', '¿por qué hicieron esto?'). Debes tener certeza absoluta y usar "
        "hechos definitivos. Escribe en español simple, sin jerga compleja, sin palabras "
        "genéricas y sin listas."
    ),
    "4_analogy_creator": (
        "Eres el Analogy Creator (Creador de Analogías). Tu rol es tomar un concepto complejo y "
        "crear una analogía simple y cotidiana que lo haga fácil de entender para alguien sin "
        "conocimientos previos. Puedes usar Google Search. Las analogías deben estar en español, "
        "sin listas ni palabras genéricas."
    ),
    "5_jargon_buster": (
        "Eres el Jargon Buster (Cazador de Jerga). Tu rol es tomar términos técnicos, jerga y "
        "frases formales complejas y traducirlos al español cotidiano y simple. Da equivalentes "
        "simples y reformula las estructuras complejas para que un principiante las entienda. "
        "Además, aclara cada término explicando exactamente cómo funciona y qué hace en la "
        "práctica. No des definiciones complejas."
    ),
    "7_format_enforcer": (
        "Eres el Format Enforcer y Optimizador de Legibilidad. Tu rol es revisar el texto "
        "borrador y reescribirlo para asegurar la máxima legibilidad en español. Divide las "
        "oraciones largas y complejas en oraciones más cortas e impactantes, fáciles de leer, "
        "aprender y entender, y asegúrate de que no haya listas ni formatos parecidos a listas. "
        "El texto debe fluir de forma natural como prosa continua, sin viñetas, sin listas "
        "numeradas y sin saltos de línea innecesarios."
    ),
    "8_vocabulary_checker": (
        "Eres el Vocabulary Checker (Verificador de Vocabulario). Revisa el texto borrador en "
        "español. Identifica cualquier palabra genérica, vaga o cliché (como 'cosas', 'hacer', "
        "'bueno', 'malo') y reemplázala con vocabulario preciso, específico y descriptivo que "
        "agregue claridad sin agregar complejidad, manteniendo el significado original. Devuelve "
        "únicamente el texto mejorado, sin introducciones ni despedidas."
    ),
    # Las reglas que diste para la explicacion: se aplican al redactar y en cada revision.
    "reglas_de_estilo": (
        "Actúa como un simplificador experto para alguien sin conocimientos técnicos previos. "
        "Escribe todo exclusivamente en párrafos fluidos de lectura rápida, usando oraciones "
        "cortas y directas. No uses viñetas, listas ni numeración en ninguna parte de la "
        "respuesta. Explica qué encontraste usando solo hechos exactos y cifras concretas, sin "
        "suposiciones ni palabras vagas. Cuando menciones términos técnicos tradúcelos de forma "
        "natural al lenguaje cotidiano, incluye una analogía sencilla de la vida diaria y explica "
        "para qué sirve cada cosa."
    ),
}

# Textos por defecto que tuvieron versiones anteriores del script. Si tu
# instrucciones_agentes.json tiene uno de estos, no lo editaste tu: se usa el actual.
DEFECTOS_ANTERIORES = {
    "agente_principal": [AGENTE_PRINCIPAL.replace("explicaciones muy", "explicaciónes muy")],
}

ENFOQUE_AUDIO = (
    "Expliquen esta clase en español a una persona sin conocimientos previos, como lo haría un "
    "simplificador experto. Hablen con oraciones cortas y directas, en una conversación fluida, "
    "sin enumerar listas. Usen solo hechos exactos y cifras concretas que estén en las fuentes, "
    "sin suposiciones ni palabras vagas. Cuando aparezca un término técnico, tradúzcanlo al "
    "lenguaje cotidiano, den una analogía sencilla de la vida diaria y expliquen para qué sirve. "
    "Expliquen también el porqué de cada hecho. Usen la explicación en español como guía y la "
    "transcripción en inglés para comprobar los detalles."
)


# Version del metodo. Las explicaciones hechas con una version anterior se rehacen
# (reutilizando lo guardado de los pasos cuya version no cambio).
METODO = 4


@dataclass
class Paso:
    clave: str
    nombre: str
    web: bool
    entradas: list
    tarea: str
    version: int = 1    # si cambia, los resultados guardados de este paso se rehacen
    rol: str = ""       # que instrucciones de instrucciones_agentes.json usa (por defecto, clave)

    @property
    def instrucciones(self) -> str:
        return self.rol or self.clave


PASOS = [
    Paso("1_deep_researcher", "Deep Researcher", True, ["transcripcion"],
         "Analiza esta parte de la transcripción. Es una transcripción automática en inglés de "
         "una clase universitaria, así que puede tener nombres y palabras mal reconocidos: "
         "identifícalos y corrígelos también. Entrega tu informe en español."),
    Paso("2_concept_analyzer", "Concept Analyzer", True, ["transcripcion", "1_deep_researcher"],
         "Identifica los conceptos y términos centrales de esta parte de la clase."),
    Paso("3_contextualizer", "Contextualizer", True,
         ["transcripcion", "1_deep_researcher", "2_concept_analyzer"],
         "Da el contexto y los motivos (el porqué y el para qué) de lo que se explica en esta "
         "parte de la clase."),
    Paso("4_analogy_creator", "Analogy Creator", True,
         ["memoria", "transcripcion", "1_deep_researcher", "2_concept_analyzer", "3_contextualizer"],
         "Crea una analogía cotidiana en español para cada concepto central que identificó el "
         "Concept Analyzer en esta parte de la clase. Apóyate en los ejemplos que usó el profesor "
         "y en los hechos del Deep Researcher, para que cada analogía encaje con lo que se dijo "
         "en clase. No les inventes otra analogía a los conceptos que ya se explicaron en partes "
         "anteriores.", version=3),
    Paso("5_jargon_buster", "Jargon Buster", False,
         ["memoria", "transcripcion", "1_deep_researcher", "2_concept_analyzer"],
         "Traduce al español de todos los días cada término técnico, palabra de jerga y frase "
         "formal de esta parte de la clase, y explica qué hace en la práctica. No repitas los "
         "conceptos que ya se explicaron en partes anteriores.", version=3),
    Paso("6_agente_principal", "Agente principal (redacta)", False,
         ["reglas_de_estilo", "memoria", "anterior", "transcripcion", "1_deep_researcher",
          "2_concept_analyzer", "3_contextualizer", "4_analogy_creator", "5_jargon_buster"],
         "Ya recibiste los resultados de tus 5 primeros subagentes. Escribe la explicación de "
         "ESTA parte de la clase, siguiendo el orden en que el profesor presentó los temas y "
         "cumpliendo las reglas de estilo del usuario. Escribe como un buen profesor particular "
         "que le cuenta la clase a un amigo que no sabe nada del tema: en tercera persona sobre "
         "lo que dijo el profesor (\"el profesor explica que...\"), nunca como si fueras el "
         "profesor ni con \"nosotros\". Explica todo lo que se enseñó, sin relleno ni frases "
         "decorativas. Lo administrativo (micrófonos, saludos, avisos) va en una frase como "
         "mucho; lo importante del curso (de qué trata, cómo se evalúa) sí explícalo. La primera "
         "vez que aparece un término técnico, tradúcelo al lenguaje de todos los días (usa la "
         "traducción del Jargon Buster), da su analogía (del Analogy Creator) y di para qué "
         "sirve y por qué importa (del Contextualizer); después úsalo con naturalidad. Usa los "
         "hechos exactos del Deep Researcher, con los nombres y las cifras corregidos. Los "
         "conceptos que ya se explicaron en partes anteriores solo se nombran, sin volver a "
         "definirlos. No menciones que la clase está dividida en partes (nada de \"en esta "
         "parte\" ni \"esta sección\"): el lector verá una sola explicación continua. Párrafos "
         "fluidos de oraciones cortas y palabras de uso diario: sin listas, viñetas, títulos ni "
         "imágenes.", version=4, rol="agente_principal"),
    Paso("6b_jerga_en_borrador", "Jargon Buster (revisa el borrador)", False,
         ["reglas_de_estilo", "memoria", "6_agente_principal"],
         "Revisa el texto de abajo, que ya está en español, y busca las palabras técnicas, "
         "cultas o formales que una persona sin estudios del tema no usaría al hablar con un "
         "amigo (por ejemplo: efigie, teocracia, vestigios, anverso, prócer, lúdico, "
         "transfronterizo, metrológico, epigráfico, hidrodinámica, sesgo, legitimar, "
         "paradigma, hegemonía). Si es un concepto que la clase enseña y hay que aprender, "
         "déjalo, pero la primera vez explícalo con palabras simples y di para qué sirve (si ya "
         "está en la lista de conceptos ya explicados, no lo vuelvas a explicar). Si no, cámbialo "
         "por una palabra de todos los días (efigie: retrato; vestigios: restos; anverso: la cara "
         "de la moneda; lúdico: de juego). No cambies nada más: devuelve el texto completo, con "
         "los mismos párrafos, datos y analogías.", version=4, rol="5_jargon_buster"),
    Paso("7_format_enforcer", "Format Enforcer", False,
         ["reglas_de_estilo", "6b_jerga_en_borrador"],
         "Reescribe el texto de abajo para que se lea rápido y con fluidez: oraciones de 10 a 20 "
         "palabras, ninguna de más de 25. Cada oración debe tener sentido completo por sí sola: "
         "no la partas en trozos sueltos (\"Depende de los restos...\", \"Es como pensarlo "
         "porque...\"). Une las ideas con conectores sencillos (por eso, entonces, además, pero, "
         "o sea) y no empieces varias oraciones seguidas igual (\"Es como...\", \"El "
         "profesor...\"). Mantén la tercera persona, el tono cercano y todas las explicaciones, "
         "datos, nombres, cifras y analogías. Puedes quitar frases de relleno.", version=4),
    Paso("8_vocabulary_checker", "Vocabulary Checker", False,
         ["reglas_de_estilo", "7_format_enforcer"],
         "Cambia solo las palabras genéricas o vagas (cosas, hacer, bueno, malo, algo, tema, "
         "elemento, aspecto...) por palabras concretas pero sencillas, de las que se usan todos "
         "los días. Nunca cambies una palabra simple por otra culta o técnica. No alargues las "
         "oraciones ni quites contenido. Devuelve el texto completo.", version=4),
]

ETIQUETAS = {
    "reglas_de_estilo": "REGLAS DE ESTILO DEL USUARIO (obligatorias para todo el texto)",
    "memoria": "CONCEPTOS YA EXPLICADOS EN PARTES ANTERIORES DE LA CLASE (no los vuelvas a "
               "definir ni les inventes otra analogía; si aparecen, solo nómbralos)",
    "anterior": "FINAL DE LA PARTE ANTERIOR DE LA EXPLICACIÓN (continúa desde aquí con fluidez, "
                "sin repetirlo)",
    "transcripcion": "TRANSCRIPCIÓN ORIGINAL EN INGLÉS (parte {i} de {n})",
    "1_deep_researcher": "INFORME DEL DEEP RESEARCHER",
    "2_concept_analyzer": "CONCEPTOS CENTRALES (CONCEPT ANALYZER)",
    "3_contextualizer": "CONTEXTO Y MOTIVOS (CONTEXTUALIZER)",
    "4_analogy_creator": "ANALOGÍAS (ANALOGY CREATOR)",
    "5_jargon_buster": "JERGA TRADUCIDA (JARGON BUSTER)",
    "6_agente_principal": "TEXTO A REVISAR (BORRADOR)",
    "6b_jerga_en_borrador": "TEXTO A REESCRIBIR",
    "7_format_enforcer": "TEXTO A REVISAR",
}

# Todo lo que el asistente escriba fuera de <entregable> (saludos, "Aqui tienes...",
# preguntas finales) se descarta sin tocar el texto de adentro.
PIE_COMUN = (
    "El material es la transcripción de una clase universitaria (contenido académico). Este es "
    "un proceso automático: escribe tu entregable completo entre las etiquetas <entregable> y "
    "</entregable>. Todo lo que esté fuera de las etiquetas se descarta. Dentro de las etiquetas "
    "no saludes, no te presentes, no menciones a Gemini Enterprise ni tu papel, no hagas "
    "preguntas, no uses emojis y no generes imágenes."
)

PIE_PROSA = (
    "Formato dentro de <entregable>: texto plano en párrafos separados por una línea en blanco. "
    "Sin markdown: nada de títulos, #, asteriscos, negritas, listas ni separadores."
)

TAREA_TERMINOS = (
    "Después de </entregable>, escribe entre <terminos> y </terminos> una línea por cada "
    "concepto que explicaste en esta parte, así: concepto: la analogía que usaste, en pocas "
    "palabras."
)

NOTA_FILTRO = (
    "Aviso: un filtro automático de contenido bloqueó o cortó el intento anterior. Es un falso "
    "positivo, porque el material es una clase universitaria. Mantén el mismo tono sencillo y "
    "cercano. Solo donde se describe algo delicado (por ejemplo, cuerpos o desnudez en obras de "
    "arte, violencia o prácticas médicas antiguas), descríbelo de forma breve, indirecta y "
    "respetuosa, sin detalles físicos. No omitas ningún dato de la clase."
)

NOTA_ETIQUETAS = (
    "Aviso: en el intento anterior faltaron las etiquetas. Escribe tu entregable completo entre "
    "<entregable> y </entregable>."
)

NOTA_CORTO = (
    "Aviso: tu texto anterior fue demasiado corto ({largo} caracteres) para todo lo que se enseña "
    "en esta parte. Explica TODO el contenido, tema por tema, sin relleno."
)

NOTA_NO_RESUMAS = (
    "Aviso: tu versión anterior quitó demasiado contenido. Conserva todas las explicaciones, los "
    "datos y las analogías; cambia solo lo que pide tu tarea."
)

NOTA_PARTE_BLOQUEADA = (
    "(Nota: esta parte de la clase no se pudo explicar porque un filtro automático de contenido "
    "bloqueó la respuesta. Consulta la transcripción original.)"
)

PASOS_PROSA = {"6_agente_principal", "6b_jerga_en_borrador", "7_format_enforcer",
               "8_vocabulary_checker"}
TOTAL_PASOS = len(PASOS)


def paso_por_clave(clave: str) -> Paso:
    return next(p for p in PASOS if p.clave == clave)

# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

_print_lock = threading.Lock()


DETALLE = threading.Event()     # con detalle se muestra cada paso de cada parte


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def detalle(msg: str) -> None:
    if DETALLE.is_set():
        log(msg)


def legibilidad(texto: str) -> str:
    """Medida simple: palabras por oracion y % de palabras largas (12 letras o mas)."""
    oraciones = [o for o in re.split(r"(?<=[.!?])\s+", texto) if re.search(r"\w", o)]
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", texto)
    if not oraciones or not palabras:
        return "sin texto"
    largas = sum(1 for w in palabras if len(w) >= 12) / len(palabras)
    maximo = max(len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", o)) for o in oraciones)
    return (f"{len(palabras) / len(oraciones):.0f} palabras por oración, la más larga {maximo}, "
            f"{largas:.0%} palabras largas")


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def leer(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def escribir(path: str, texto: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, path)


def limpiar_nombre(nombre: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", nombre).strip()


_LINEA_DE_LISTA = re.compile(r"(?m)^\s*(#{1,6}\s|[-*•]\s|\d{1,2}[.)]\s|-{3,}\s*$)")
_MARCAS_INVALIDAS = ("<end_of_image>", "Informe de Investigaci", "Estado del Informe",
                     "Cumplimiento Total", "Cumplimiento Parcial", "Full Fulfillment",
                     "<entregable>", "</entregable>")

# Gemini Enterprise corta la respuesta y agrega este aviso cuando la bloquea su filtro.
_BLOQUEO = re.compile(r"blocked by (your|the) organi[sz]ation|organi[sz]ation'?s content polic"
                      r"|bloquead[oa] por la pol[ií]tica de contenido", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")
# Solo para el caso raro de una respuesta sin etiquetas: charla de chat en la primera o la
# ultima oracion. Nunca se borran oraciones del medio del texto.
_CHARLA_INICIO = re.compile(r"^\s*(¡\s*hola\b[^.!?]*[.!?]|aquí (tienes|está|va)\b[^.!?:]*[.!?:])\s*", re.I)
_CHARLA_FINAL = re.compile(r"\s*(¿\s*(qué te parece|quieres que|te gustaría|deseas)[^?]*\?"
                           r"|quedo (muy |más que )?atent\w*[^.!]*[.!]|¡\s*habl(amos|emos)\W*)\s*$", re.I)


def lineas_de_lista(texto: str) -> int:
    return len(_LINEA_DE_LISTA.findall(texto))


def parece_cortado(texto: str) -> bool:
    """Un texto que termina a mitad de palabra o de frase (respuesta cortada)."""
    return not re.search(r"[.!?…»\")\]]\s*$", texto.strip())


def extraer_entre(crudo: str, etiqueta: str) -> tuple[str, str]:
    """Devuelve (texto, estado): 'ok', 'cortado' (abrio la etiqueta y no la cerro) o
    'sin_etiquetas'."""
    t = re.sub(r"\\(?=[<>/])", "", crudo).replace("&lt;", "<").replace("&gt;", ">")
    bloques = re.findall(rf"<{etiqueta}>(.*?)</{etiqueta}>", t, re.S | re.I)
    if bloques:
        return "\n\n".join(b.strip() for b in bloques if b.strip()), "ok"
    m = re.search(rf"<{etiqueta}>", t, re.I)
    if m:
        return t[m.end():].strip(), "cortado"
    return t.strip(), "sin_etiquetas"


def limpiar_ligero(texto: str, prosa: bool, sin_etiquetas: bool = False) -> str:
    """Solo quita lo que nunca es contenido: emojis, cercos de codigo y, en prosa, los titulos
    markdown, separadores y negritas. No borra oraciones (salvo charla de chat al principio o al
    final de una respuesta que vino sin etiquetas)."""
    t = _EMOJI.sub("", texto.replace("<end_of_image>", "")).strip()
    t = re.sub(r"^```\w*\s*|\s*```$", "", t)
    if prosa:
        lineas = [l for l in t.splitlines()
                  if not re.match(r"\s*#{1,6}\s", l) and not re.fullmatch(r"\s*([-*_]\s*){3,}", l)]
        t = "\n".join(lineas).replace("**", "").replace("__", "")
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        # Un parrafo de una sola linea corta y sin punto final es un titulo: se quita.
        t = "\n\n".join(pa for pa in t.split("\n\n")
                        if not ("\n" not in pa and len(pa) < 100 and parece_cortado(pa)))
    if sin_etiquetas:
        anterior = None
        while anterior != t:
            anterior = t
            t = _CHARLA_INICIO.sub("", t, count=1)
            t = _CHARLA_FINAL.sub("", t, count=1)
    return t.strip()


def es_fallo_guardado(texto: str) -> bool:
    """Restos de versiones anteriores: avisos de 'no devolvio resultado' o texto bloqueado."""
    t = texto.strip()
    return (not t or bool(_BLOQUEO.search(t))
            or (t.startswith("(") and len(t) < 600 and "no devolvi" in t[:120]))


def fragmento_valido(texto: str) -> bool:
    t = (texto or "").strip()
    return (not es_fallo_guardado(t) and lineas_de_lista(t) <= 2 and not parece_cortado(t)
            and not any(m.lower() in t.lower() for m in _MARCAS_INVALIDAS))


def es_explicacion_valida(texto: str) -> bool:
    """Prosa en parrafos: ni informes, ni listas, ni titulos, ni texto bloqueado."""
    t = (texto or "").strip()
    if len(t) < 400:
        return False
    if any(m.lower() in t.lower() for m in _MARCAS_INVALIDAS) or _BLOQUEO.search(t):
        return False
    return lineas_de_lista(t) <= 2


def quitar_formato(texto: str) -> str:
    """Ultimo recurso: quita titulos, vinetas y negritas de markdown sin tocar las palabras."""
    salida = []
    for linea in texto.splitlines():
        if re.match(r"\s*#{1,6}\s", linea) or re.fullmatch(r"\s*-{3,}\s*", linea):
            salida.append("")
            continue
        l = re.sub(r"^\s*([-*•]\s+|\d{1,2}[.)]\s+)", "", linea)
        l = l.replace("**", "").replace("__", "")
        salida.append(l)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(salida)).strip()


def dividir_en_bloques(texto: str, max_chars: int) -> list[str]:
    """Corta en fin de parrafo o de oracion, nunca a mitad de una idea."""
    texto = texto.strip()
    bloques, inicio, n = [], 0, len(texto)
    while inicio < n:
        fin = min(inicio + max_chars, n)
        if fin < n:
            minimo = inicio + int(max_chars * 0.6)
            corte = texto.rfind("\n\n", minimo, fin)
            if corte == -1:
                corte = max(texto.rfind(s, minimo, fin) for s in (". ", "? ", "! ", ".\n"))
            if corte != -1:
                fin = corte + 1
        bloque = texto[inicio:fin].strip()
        if bloque:
            bloques.append(bloque)
        inicio = fin
    return bloques


# --------------------------------------------------------------------------
# Cliente de la API de Gemini Enterprise (Discovery Engine)
# --------------------------------------------------------------------------

class ApiError(RuntimeError):
    def __init__(self, r: requests.Response, que: str):
        self.status = r.status_code
        self.texto = r.text
        super().__init__(f"{que}: HTTP {r.status_code} {r.text[:600]}")


class Bloqueado(RuntimeError):
    """El asistente no contesto (filtro de contenido, respuesta vacia o cortada)."""


class Interrumpido(RuntimeError):
    """El usuario detuvo la celda."""


PARAR = threading.Event()


class CupoAgotado(RuntimeError):
    pass


@dataclass
class Respuesta:
    texto: str
    estado: str
    motivos: list
    sesion: str | None
    veredicto: str = ""


def leer_stream(raw: str) -> list[dict]:
    """streamAssist devuelve un arreglo JSON de respuestas parciales (o lineas SSE)."""
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except ValueError:
        pass
    partes = []
    for linea in raw.splitlines():
        linea = linea.strip().rstrip(",")
        if linea.startswith("data:"):
            linea = linea[5:].strip()
        linea = linea.lstrip("[").rstrip("]")
        if linea.startswith("{"):
            try:
                partes.append(json.loads(linea))
            except ValueError:
                pass
    if not partes:
        raise RuntimeError(f"respuesta no reconocida del asistente: {raw[:300]}")
    return partes


def unir_respuesta(chunks: list[dict]) -> Respuesta:
    textos, estado, motivos, sesion, veredicto = [], "", [], None, ""
    for ch in chunks:
        ans = ch.get("answer") or {}
        estado = ans.get("state") or estado
        motivos += ans.get("assistSkippedReasons") or []
        veredicto = (ans.get("customerPolicyEnforcementResult") or {}).get("verdict") or veredicto
        sesion = (ch.get("sessionInfo") or {}).get("session") or sesion
        for rep in ans.get("replies") or []:
            c = (rep.get("groundedContent") or {}).get("content") or {}
            if c.get("thought"):      # razonamiento interno, no es parte de la respuesta
                continue
            if c.get("text"):
                textos.append(c["text"])
    texto = "".join(textos).replace("<end_of_image>", "").strip()
    return Respuesta(texto, estado, motivos, sesion, veredicto)


@dataclass
class Config:
    carpeta: str = "/content/drive/MyDrive/Clases"
    project_id: str = ""
    project_number: str = ""
    location: str = "global"
    engine_id: str = ""
    modelo: str = ""
    max_clases: int = 1
    solo_curso: str = ""
    curso: str = ""                 # nombre exacto de la carpeta del curso (vacio = todos)
    detalle: bool = False
    bloque_chars: int = 15000
    paralelo: int = 3
    clases_a_la_vez: int = 1
    busqueda_web: bool = True
    borrar_chats: bool = True
    crear_cuaderno: bool = True
    crear_audio: bool = True
    idioma_audio: str = "es"
    enfoque_audio: str = ENFOQUE_AUDIO
    rehacer: bool = False

    @property
    def host(self) -> str:
        return HOST_GLOBAL if self.location == "global" else f"{self.location}-{HOST_GLOBAL}"


class Api:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.web = cfg.busqueda_web
        self._token, self._hora = None, 0.0
        self._lock = threading.Lock()
        self._aviso_chats = False

    # -- autenticacion --------------------------------------------------
    def token(self, renovar: bool = False) -> str:
        with self._lock:
            if renovar or not self._token or time.time() - self._hora > 40 * 60:
                try:
                    self._token = subprocess.check_output(
                        ["gcloud", "auth", "print-access-token"], text=True,
                        stderr=subprocess.PIPE).strip()
                except (OSError, subprocess.CalledProcessError) as e:
                    raise SystemExit(
                        "No hay sesion de gcloud. Ejecuta antes la celda de "
                        "'gcloud auth login' con tu cuenta de Gemini Enterprise.\n"
                        f"Detalle: {getattr(e, 'stderr', '') or e}")
                self._hora = time.time()
            return self._token

    def headers(self, proyecto: str | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": proyecto or self.cfg.project_id}

    def request(self, metodo: str, url: str, body: dict | None = None, timeout: int = 120,
                intentos: int = 5, reintentar_429: bool = True,
                proyecto: str | None = None) -> requests.Response:
        ultimo = None
        for n in range(intentos):
            try:
                r = requests.request(metodo, url, headers=self.headers(proyecto), json=body,
                                     timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                ultimo = e
                espera = 20 * (n + 1)
                log(f"         (red: {type(e).__name__}; reintento en {espera}s)")
                time.sleep(espera)
                continue
            if r.status_code == 401 and n == 0:
                self.token(renovar=True)
                continue
            reintentable = r.status_code in (500, 502, 503, 504) or (
                r.status_code == 429 and reintentar_429)
            if reintentable and n < intentos - 1:
                espera = min(30 * 2 ** n, 300)
                log(f"         (HTTP {r.status_code}; reintento en {espera}s)")
                time.sleep(espera)
                continue
            return r
        raise RuntimeError(f"sin respuesta del servidor tras {intentos} intentos: {ultimo}")

    # -- asistente (streamAssist) ---------------------------------------
    def url_asistente(self) -> str:
        c = self.cfg
        return (f"https://{c.host}/v1/projects/{c.project_id}/locations/{c.location}/"
                f"collections/default_collection/engines/{c.engine_id}/"
                f"assistants/default_assistant:streamAssist")

    def preguntar(self, texto: str, web: bool = False) -> Respuesta:
        body = {"query": {"text": texto}}
        if web and self.web:
            body["toolsSpec"] = {"webGroundingSpec": {}}
        if self.cfg.modelo:
            body["generationSpec"] = {"modelId": self.cfg.modelo}
        r = self.request("POST", self.url_asistente(), body, timeout=900, intentos=4)
        # Solo si el error dice que la app no admite la busqueda web se apaga (para todas las
        # llamadas). Cualquier otro 400 es un error de esta llamada y no toca la busqueda web.
        if (r.status_code == 400 and "toolsSpec" in body
                and re.search(r"grounding|tools_?spec", r.text, re.I)):
            if self.web:
                self.web = False
                log("         (tu app de Gemini Enterprise no tiene activada la busqueda web; "
                    f"se sigue sin ella. Detalle: {r.text[:200]})")
            body.pop("toolsSpec")
            r = self.request("POST", self.url_asistente(), body, timeout=900, intentos=4)
        if not r.ok:
            raise ApiError(r, "streamAssist")
        resp = unir_respuesta(leer_stream(r.text))
        if self.cfg.borrar_chats and resp.sesion:
            self.borrar_chat(resp.sesion)
        if (_BLOQUEO.search(resp.texto) or resp.veredicto == "BLOCK"
                or "CUSTOMER_POLICY_VIOLATION" in resp.motivos):
            raise Bloqueado("el filtro de contenido de Gemini Enterprise bloqueo la respuesta")
        if not resp.texto:
            motivo = ", ".join(resp.motivos) or resp.estado or "respuesta vacia"
            raise Bloqueado(motivo)
        if resp.estado and resp.estado != "SUCCEEDED":
            raise Bloqueado(f"respuesta incompleta (estado {resp.estado})")
        return resp

    def borrar_chat(self, sesion: str) -> None:
        """Cada llamada crea un chat en tu historial de Gemini Enterprise; se borra al terminar."""
        try:
            r = requests.delete(f"https://{self.cfg.host}/v1/{sesion}", headers=self.headers(),
                                timeout=60)
            ok = r.ok
        except requests.RequestException:
            ok = False
        if not ok and not self._aviso_chats:
            self._aviso_chats = True
            log("         (no se pudieron borrar los chats temporales; quedan en tu historial)")

    # -- Gemini Notebook (NotebookLM Enterprise) ------------------------
    def url_cuadernos(self) -> str:
        c = self.cfg
        return f"https://{c.host}/v1alpha/projects/{c.project_number}/locations/{c.location}/notebooks"

    def crear_cuaderno(self, titulo: str) -> str:
        r = self.request("POST", self.url_cuadernos(), {"title": titulo})
        if not r.ok:
            raise ApiError(r, "crear cuaderno")
        j = r.json()
        return j.get("notebookId") or j.get("name", "").rsplit("/", 1)[-1]

    def cuaderno(self, nb: str) -> dict | None:
        r = self.request("GET", f"{self.url_cuadernos()}/{nb}")
        if r.status_code == 404:
            return None
        if not r.ok:
            raise ApiError(r, "leer cuaderno")
        return r.json()

    def listar_cuadernos(self) -> list[dict]:
        r = self.request("GET", f"{self.url_cuadernos()}:listRecentlyViewed")
        if not r.ok:
            raise ApiError(r, "listar cuadernos")
        return r.json().get("notebooks", [])

    def borrar_cuadernos(self, ids: list[str]) -> None:
        nombres = [f"projects/{self.cfg.project_number}/locations/{self.cfg.location}/notebooks/{i}"
                   for i in ids]
        r = self.request("POST", f"{self.url_cuadernos()}:batchDelete", {"names": nombres})
        if not r.ok:
            raise ApiError(r, "borrar cuadernos")

    def agregar_textos(self, nb: str, textos: list[tuple[str, str]]) -> list[dict]:
        body = {"userContents": [{"textContent": {"sourceName": nombre, "content": contenido}}
                                 for nombre, contenido in textos]}
        r = self.request("POST", f"{self.url_cuadernos()}/{nb}/sources:batchCreate", body,
                         timeout=300)
        if not r.ok:
            raise ApiError(r, "subir fuentes")
        return r.json().get("sources", [])

    def estado_fuente(self, nb: str, sid: str) -> str:
        r = self.request("GET", f"{self.url_cuadernos()}/{nb}/sources/{sid}")
        if not r.ok:
            return ""
        j = r.json()
        fuente = (j.get("sources") or [j])[0]
        return (fuente.get("settings") or {}).get("status", "")

    def esperar_fuentes(self, nb: str, ids: list[str], maximo: int = 300) -> None:
        inicio, pendientes = time.time(), list(ids)
        while pendientes and time.time() - inicio < maximo:
            for sid in list(pendientes):
                st = self.estado_fuente(nb, sid)
                if st.endswith("COMPLETE"):
                    pendientes.remove(sid)
                elif "ERROR" in st or "FAIL" in st:
                    raise RuntimeError(f"la fuente {sid} no se pudo procesar ({st})")
            if pendientes:
                time.sleep(8)
        if pendientes:
            log("       (las fuentes siguen procesandose; se pide el audio igualmente)")

    def crear_audio(self, nb: str) -> dict:
        # Segun la referencia REST, todo va dentro de generationOptions.
        # (Si languageCode va fuera, la API responde 400 "Unknown name languageCode".)
        body = {"generationOptions": {"episodeFocus": self.cfg.enfoque_audio,
                                      "languageCode": self.cfg.idioma_audio}}
        r = self.request("POST", f"{self.url_cuadernos()}/{nb}/audioOverviews", body,
                         intentos=3, reintentar_429=False)
        if r.ok:
            return r.json().get("audioOverview", {})
        t = r.text.lower()
        if r.status_code == 409 or "already" in t or "already_exists" in t:
            return {"status": "ya existia"}
        if r.status_code == 429 or "resource_exhausted" in t:
            raise CupoAgotado(r.text[:300])
        raise ApiError(r, "crear Audio Overview")

    # -- deteccion automatica del proyecto --------------------------------
    def detectar(self) -> None:
        c = self.cfg
        try:
            proyectos = json.loads(subprocess.check_output(
                ["gcloud", "projects", "list", "--format=json"], text=True))
        except (OSError, subprocess.CalledProcessError, ValueError) as e:
            raise SystemExit(f"No se pudieron listar tus proyectos con gcloud: {e}")
        if c.project_id:
            proyectos = [p for p in proyectos if p.get("projectId") == c.project_id]
        for p in proyectos:
            pid, num = p.get("projectId"), str(p.get("projectNumber", ""))
            for loc in dict.fromkeys([c.location or "global", "global", "us", "eu"]):
                host = HOST_GLOBAL if loc == "global" else f"{loc}-{HOST_GLOBAL}"
                url = (f"https://{host}/v1/projects/{pid}/locations/{loc}/collections/"
                       f"default_collection/engines")
                try:
                    r = self.request("GET", url, intentos=1, proyecto=pid)
                except RuntimeError:
                    continue
                motores = r.json().get("engines", []) if r.ok else []
                if not motores:
                    continue
                ids = [m.get("name", "").rsplit("/", 1)[-1] for m in motores]
                elegido = c.engine_id if c.engine_id in ids else next(
                    (i for i in ids if "agentspace" in i), ids[0])
                c.project_id, c.project_number, c.location, c.engine_id = pid, num, loc, elegido
                log(f"Proyecto detectado: {pid} (numero {num}), ubicacion '{loc}', "
                    f"app '{elegido}'" + (f"  [otras apps: {', '.join(ids)}]" if len(ids) > 1 else ""))
                return
        raise SystemExit("No encontre ninguna app de Gemini Enterprise en tus proyectos. "
                         "Comprueba que iniciaste sesion en gcloud con la cuenta de Gemini Enterprise.")

    def preparar(self) -> None:
        c = self.cfg
        if not (c.project_id and c.project_number and c.engine_id):
            self.detectar()


# --------------------------------------------------------------------------
# Orquestacion de los subagentes (un fragmento de clase)
# --------------------------------------------------------------------------

@dataclass
class Clase:
    ruta: str             # .../Clases/<curso>/L01 - ... - Lecture.txt
    clave: str            # ruta relativa a la carpeta Clases
    curso: str            # nombre de la carpeta del curso
    base: str             # L01 - 2026-01-27 - Lecture

    @property
    def codigo_curso(self) -> str:
        return self.curso.split(" - ")[0].strip() or self.curso

    @property
    def corto(self) -> str:
        return f"{self.codigo_curso} {self.base.split(' - ')[0]}".strip()

    @property
    def titulo(self) -> str:
        return f"{self.codigo_curso} · {self.base}" if self.curso else self.base

    @property
    def ruta_explicacion(self) -> str:
        return os.path.join(os.path.dirname(self.ruta), self.base + SUFIJO_EXPLICACION)


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def armar_prompt(paso: Paso, instr: dict, salidas: dict, clase: Clase, i: int, n: int,
                 nota: str = "") -> str:
    cabecera = f'Clase: "{clase.base}" del curso "{clase.curso}".'
    if n > 1:
        cabecera += f" Esta es la parte {i} de {n} de la clase."
    partes = [instr[paso.instrucciones], cabecera, paso.tarea]
    if paso.clave == "6_agente_principal":
        if n > 1 and i > 1:
            partes.append("Continúa la explicación de la parte anterior: no vuelvas a presentar "
                          "la clase ni a los profesores.")
        if n > 1 and i < n:
            partes.append("No escribas una conclusión final: la clase sigue en la parte siguiente.")
    partes.append(PIE_COMUN)
    if paso.clave in PASOS_PROSA:
        partes.append(PIE_PROSA)
    if paso.clave == "6_agente_principal":
        partes.append(TAREA_TERMINOS)
    if nota:
        partes.append(nota)
    for clave in paso.entradas:
        valor = (instr.get(clave) if clave == "reglas_de_estilo" else salidas.get(clave)) or ""
        valor = valor.strip()
        if valor:                   # en la primera parte no hay memoria ni parte anterior
            partes.append(f"=== {ETIQUETAS[clave].format(i=i, n=n)} ===\n{valor}")
    return "\n\n".join(partes)


class SinSintesis(RuntimeError):
    """El agente principal no pudo escribir la explicacion de esta parte."""


@dataclass
class Resultado:
    texto: str      # lo que estaba dentro de <entregable>
    crudo: str      # la respuesta completa (para leer <terminos>)


def ejecutar_paso(api: Api, paso: Paso, armar, prefijo: str, nota: str = "") -> Resultado:
    """Llama al subagente y devuelve solo su <entregable>. Si el filtro de contenido bloquea o
    corta la respuesta, reintenta (hasta 3 veces) pidiendo describir lo delicado de forma
    indirecta, sin cambiar el tono."""
    prosa = paso.clave in PASOS_PROSA
    notas, sin_etiquetas = [], 0
    ultimo: Exception = Bloqueado("sin respuesta")
    for intento in range(1, 4):
        if PARAR.is_set():
            raise Interrumpido("detenido")
        extra = "\n\n".join(x for x in [nota] + notas if x)
        try:
            crudo = api.preguntar(armar(extra), web=paso.web).texto
        except Bloqueado as e:
            ultimo = e
            if NOTA_FILTRO not in notas:
                notas.append(NOTA_FILTRO)
            if intento < 3:
                log(f"{prefijo}   {e}; reintento {intento + 1}/3...")
                time.sleep(10)
            continue
        texto, estado = extraer_entre(crudo, "entregable")
        if estado == "cortado":
            ultimo = Bloqueado("la respuesta llego cortada")
            if NOTA_FILTRO not in notas:
                notas.append(NOTA_FILTRO)
            if intento < 3:
                log(f"{prefijo}   la respuesta llego cortada; reintento {intento + 1}/3...")
            continue
        if estado == "sin_etiquetas":
            sin_etiquetas += 1
            if sin_etiquetas == 1 and intento < 3:
                notas.append(NOTA_ETIQUETAS)
                log(f"{prefijo}   la respuesta vino sin <entregable>; se pide de nuevo...")
                continue
        texto = limpiar_ligero(texto, prosa, sin_etiquetas=estado == "sin_etiquetas")
        if not texto:
            ultimo = Bloqueado("respuesta vacia")
            continue
        if prosa and parece_cortado(texto):
            ultimo = Bloqueado("la respuesta llego cortada")
            if NOTA_FILTRO not in notas:
                notas.append(NOTA_FILTRO)
            if intento < 3:
                log(f"{prefijo}   la respuesta llego cortada; reintento {intento + 1}/3...")
            continue
        return Resultado(texto, crudo)
    raise ultimo


def nombre_archivo(etiqueta: str, clave: str, version: int) -> str:
    # Los pasos de la version 1 (1 a 3) conservan el nombre viejo para reutilizarlos.
    return f"{etiqueta}_{clave}" + ("" if version == 1 else f".v{version}") + ".md"


# Cuanto texto puede quitar cada revisor (respecto al paso anterior) antes de pedirselo de nuevo.
MINIMO_REVISOR = {"6b_jerga_en_borrador": 0.6, "7_format_enforcer": 0.4, "8_vocabulary_checker": 0.6}


def no_resumir(api: Api, paso: Paso, armar, prefijo: str, resultado: str, referencia: str,
               minimo: float) -> tuple[str, bool]:
    """Un revisor puede quitar relleno, pero si borra demasiado se le pide otra vez; si
    insiste, se conserva el texto anterior (y no se guarda como resultado del revisor)."""
    if len(resultado) >= minimo * len(referencia):
        return resultado, True
    log(f"{prefijo}   {paso.nombre} quito demasiado contenido ({_miles(len(resultado))} de "
        f"{_miles(len(referencia))} caracteres); se le pide de nuevo sin resumir...")
    try:
        otro = ejecutar_paso(api, paso, armar, prefijo, nota=NOTA_NO_RESUMAS).texto
        if len(otro) >= minimo * len(referencia):
            return otro, True
    except Bloqueado:
        pass
    log(f"{prefijo}   se conserva el texto del paso anterior")
    return referencia, False


def procesar_bloque(api: Api, instr: dict, clase: Clase, texto: str, etiqueta: str,
                    i: int, n: int, dir_pasos: str, hasta: int = TOTAL_PASOS, memoria: str = "",
                    anterior: str = "") -> tuple[str, str]:
    """Pasa una parte de la clase por los pasos 1..hasta. Devuelve (explicacion, terminos)."""
    prefijo = f"   [{clase.corto} · parte {i}{etiqueta[3:]}/{n}]"
    ruta_final = os.path.join(dir_pasos, f"{etiqueta}_final.v{METODO}.md")
    ruta_terminos = os.path.join(dir_pasos, f"{etiqueta}_terminos.v{METODO}.md")
    if hasta >= TOTAL_PASOS and os.path.exists(ruta_final) and fragmento_valido(leer(ruta_final)):
        detalle(f"{prefijo} ya estaba terminada (se recupera de Drive)")
        return (leer(ruta_final).strip(),
                leer(ruta_terminos).strip() if os.path.exists(ruta_terminos) else "")

    salidas = {"transcripcion": texto, "memoria": memoria, "anterior": anterior, "terminos": ""}
    rehacer, completo = False, True     # completo = ningun paso fallo ni se reemplazo
    for k, paso in enumerate(PASOS[:hasta], 1):
        if PARAR.is_set():
            raise Interrumpido("detenido")
        prosa = paso.clave in PASOS_PROSA
        ruta = os.path.join(dir_pasos, nombre_archivo(etiqueta, paso.clave, paso.version))
        if not rehacer and os.path.exists(ruta) and not es_fallo_guardado(leer(ruta)) and not (
                prosa and parece_cortado(leer(ruta))):
            salidas[paso.clave] = limpiar_ligero(leer(ruta), prosa)
            if paso.clave == "6_agente_principal" and os.path.exists(ruta_terminos):
                salidas["terminos"] = leer(ruta_terminos).strip()
            detalle(f"{prefijo} paso {k}/{TOTAL_PASOS} {paso.nombre}: recuperado de Drive")
            continue
        rehacer = True              # si se rehace un paso, los siguientes tambien
        detalle(f"{prefijo} paso {k}/{TOTAL_PASOS} {paso.nombre}..."
                + (" (con busqueda web)" if paso.web and api.web else ""))
        t0 = time.time()

        def armar(nota, paso=paso):
            return armar_prompt(paso, instr, salidas, clase, i, n, nota)
        guardar = True
        try:
            res = ejecutar_paso(api, paso, armar, prefijo)
            resultado = res.texto
            if paso.clave == "6_agente_principal":
                if len(resultado) < 0.12 * len(texto):    # casi vacio: algo fallo
                    log(f"{prefijo}   la explicacion quedo muy corta ({_miles(len(resultado))} "
                        "caracteres); se pide de nuevo...")
                    try:
                        otro = ejecutar_paso(api, paso, armar, prefijo,
                                             nota=NOTA_CORTO.format(largo=_miles(len(resultado))))
                        if len(otro.texto) > len(resultado):
                            res, resultado = otro, otro.texto
                    except Bloqueado:
                        pass
                salidas["terminos"] = extraer_entre(res.crudo, "terminos")[0] \
                    if "<terminos>" in res.crudo.lower() else ""
            elif paso.clave in MINIMO_REVISOR:
                # cada revisor trabaja sobre el texto del paso anterior y no deberia vaciarlo
                anterior_clave = PASOS[k - 2].clave
                resultado, guardar = no_resumir(api, paso, armar, prefijo, resultado,
                                                salidas[anterior_clave], MINIMO_REVISOR[paso.clave])
        except Bloqueado as e:
            guardar = False         # un fallo nunca se guarda: la proxima vez se reintenta
            if k <= 5:
                log(f"{prefijo}   {paso.nombre} no devolvio nada ({e}); se sigue sin su aporte")
                resultado = f"({paso.nombre} no devolvio resultado para esta parte.)"
            elif paso.clave == "6_agente_principal":
                raise SinSintesis(str(e))
            else:
                log(f"{prefijo}   {paso.nombre} no devolvio nada ({e}); se usa el texto del paso anterior")
                resultado = salidas[PASOS[k - 2].clave]

        salidas[paso.clave] = resultado
        if guardar:
            escribir(ruta, resultado)
            if paso.clave == "6_agente_principal":
                escribir(ruta_terminos, salidas["terminos"])
        else:
            completo = False
        detalle(f"{prefijo}   listo en {int(time.time() - t0)} s ({_miles(len(resultado))} caracteres)")

    if hasta < TOTAL_PASOS:
        return "", ""
    final = salidas["8_vocabulary_checker"]
    if lineas_de_lista(final) > 2:
        log(f"{prefijo} quedaron listas; pasada extra del Format Enforcer...")
        extra = paso_por_clave("7_format_enforcer")
        entrada = extra.entradas[-1]
        try:
            otro = ejecutar_paso(api, extra, lambda nota: armar_prompt(
                extra, instr, {entrada: final}, clase, i, n, nota), prefijo).texto
            if len(otro) >= 0.6 * len(final):
                final = otro
        except Bloqueado:
            pass
        final = quitar_formato(final)
    terminos = salidas["terminos"] or salidas.get("2_concept_analyzer", "")[:1500]
    if completo:
        escribir(ruta_final, final)
    return final.strip(), terminos


def procesar_bloque_dividiendo(api: Api, instr: dict, clase: Clase, texto: str, etiqueta: str,
                               i: int, n: int, dir_pasos: str, memoria: str,
                               anterior: str) -> tuple[str, str]:
    """Si el agente principal no puede con una parte, la parte en dos y lo intenta otra vez."""
    try:
        return procesar_bloque(api, instr, clase, texto, etiqueta, i, n, dir_pasos,
                               memoria=memoria, anterior=anterior)
    except SinSintesis as e:
        prefijo = f"   [{clase.corto} · parte {i}{etiqueta[3:]}/{n}]"
        if len(texto) < 3000:
            log(f"{prefijo} no se pudo explicar ni en trozos pequenos ({e}); se deja una nota "
                "en la explicacion y se sigue")
            return NOTA_PARTE_BLOQUEADA, ""
        log(f"{prefijo} el agente principal no pudo con esta parte ({e}); se divide en dos mitades")
        textos, terminos = [], []
        for j, mitad in enumerate(dividir_en_bloques(texto, len(texto) // 2 + 200)):
            t, term = procesar_bloque_dividiendo(
                api, instr, clase, mitad, etiqueta + "abcdefgh"[j], i, n, dir_pasos,
                unir_memoria([memoria] + terminos), textos[-1] if textos else anterior)
            textos.append(t)
            terminos.append(term)
        return "\n\n".join(textos), "\n".join(x for x in terminos if x)


def unir_memoria(partes: list[str], maximo: int = 4000) -> str:
    """Lista de conceptos ya explicados (los mas recientes si es muy larga)."""
    lineas, vistas = [], set()
    for p in partes:
        for l in (p or "").splitlines():
            clave = l.split(":", 1)[0].strip(" -*•").lower()
            if l.strip() and clave not in vistas:
                vistas.add(clave)
                lineas.append(l.strip(" -*•"))
    texto = "\n".join(lineas)
    return texto[-maximo:].split("\n", 1)[-1] if len(texto) > maximo else texto


def cola(texto: str, maximo: int = 800) -> str:
    """Los ultimos parrafos de la parte anterior, para continuar con fluidez."""
    parrafos, total = [], 0
    for pa in reversed([p for p in texto.split("\n\n") if p.strip()]):
        if parrafos and total + len(pa) > maximo:
            break
        parrafos.insert(0, pa.strip())
        total += len(pa)
    return "\n\n".join(parrafos)[-maximo * 2:]


def en_paralelo(func, items: list, hilos: int) -> list:
    """Como ThreadPoolExecutor.map, pero si detienes la celda se detiene de verdad."""
    ex = ThreadPoolExecutor(max_workers=max(1, hilos))
    futuros = [ex.submit(func, x) for x in items]
    try:
        return [f.result() for f in futuros]
    except (KeyboardInterrupt, SystemExit, Interrumpido):
        PARAR.set()
        raise
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def dir_pasos_de(cfg: Config, clase: Clase) -> str:
    return os.path.join(cfg.carpeta, CARPETA_INTERNA, "pasos", limpiar_nombre(clase.curso),
                        limpiar_nombre(clase.base))


def leer_plan(dir_pasos: str) -> dict:
    ruta = os.path.join(dir_pasos, "plan.json")
    try:
        return json.loads(leer(ruta)) if os.path.exists(ruta) else {}
    except ValueError:
        return {}


def explicar_clase(api: Api, cfg: Config, instr: dict, clase: Clase, transcripcion: str) -> str:
    pre = f"   [{clase.corto}]"
    dir_pasos = dir_pasos_de(cfg, clase)
    plan_previo = leer_plan(dir_pasos)
    if os.path.exists(clase.ruta_explicacion):
        previo = leer(clase.ruta_explicacion)
        al_dia = plan_previo.get("explicacion_metodo") == METODO
        if es_explicacion_valida(previo) and al_dia and not cfg.rehacer:
            log(f"{pre} explicacion en espanol ya hecha (se reutiliza)")
            return previo
        motivo = ("se pidio rehacerla" if cfg.rehacer else
                  "se hizo con una version anterior del metodo" if not al_dia else
                  "era un informe, tenia listas o un texto bloqueado")
        respaldo = os.path.join(dir_pasos, f"version_anterior_{time.strftime('%Y%m%d-%H%M%S')}.txt")
        os.makedirs(dir_pasos, exist_ok=True)
        shutil.move(clase.ruta_explicacion, respaldo)
        log(f"{pre} la explicacion anterior se rehace ({motivo}); se guardo en {respaldo}")

    bloques = dividir_en_bloques(transcripcion, cfg.bloque_chars)
    plan = {"transcripcion": sha(transcripcion), "bloque_chars": cfg.bloque_chars,
            "bloques": len(bloques)}
    if os.path.isdir(dir_pasos) and ({k: plan_previo.get(k) for k in plan} != plan or cfg.rehacer):
        # Los pasos guardados son de otra division del texto: se empieza de cero.
        for f in os.listdir(dir_pasos):
            if f.startswith("b") and f.endswith(".md"):
                os.remove(os.path.join(dir_pasos, f))
    escribir(os.path.join(dir_pasos, "plan.json"), json.dumps(plan, indent=2))
    n = len(bloques)

    # Fase 1: la investigacion (pasos 1-3) no depende de las otras partes: va en paralelo.
    log(f"{pre} {n} parte(s) de unos 20 minutos. Investigando (Deep Researcher, Concept Analyzer, "
        f"Contextualizer)...")
    detalle(f"{pre} Lo que entrega cada subagente se guarda en: {dir_pasos}")
    en_paralelo(lambda ib: procesar_bloque(api, instr, clase, ib[1], f"b{ib[0]:02d}", ib[0], n,
                                           dir_pasos, hasta=3),
                list(enumerate(bloques, 1)), cfg.paralelo)

    # Fase 2: analogias, jerga y redaccion, EN ORDEN: cada parte sabe que conceptos ya se
    # explicaron y como termino la anterior, para no repetir definiciones.
    log(f"{pre} Redactando parte por parte, en orden (analogias, jerga, redaccion y revisiones)...")
    partes, memorias = [], []
    for i, b in enumerate(bloques, 1):
        if PARAR.is_set():
            raise Interrumpido("detenido")
        texto, terminos = procesar_bloque_dividiendo(
            api, instr, clase, b, f"b{i:02d}", i, n, dir_pasos,
            memoria=unir_memoria(memorias), anterior=cola(partes[-1]) if partes else "")
        partes.append(texto)
        memorias.append(terminos)
        log(f"{pre} parte {i}/{n} lista: {legibilidad(texto)}")

    explicacion = "\n\n".join(p.strip() for p in partes if p.strip()) + "\n"
    escribir(clase.ruta_explicacion, explicacion)
    plan["explicacion_metodo"] = METODO
    escribir(os.path.join(dir_pasos, "plan.json"), json.dumps(plan, indent=2))
    log(f"{pre} explicacion guardada ({_miles(len(explicacion))} caracteres): "
        f"{os.path.basename(clase.ruta_explicacion)}\n{pre} lectura: {legibilidad(explicacion)}")
    return explicacion


# --------------------------------------------------------------------------
# Cuaderno + Audio Overview
# --------------------------------------------------------------------------

class Registro:
    def __init__(self, path: str):
        self.path = path
        self.datos = json.loads(leer(path)) if os.path.exists(path) else {}

    def get(self, clave: str) -> dict:
        return self.datos.setdefault(clave, {})

    def guardar(self) -> None:
        escribir(self.path, json.dumps(self.datos, indent=2, ensure_ascii=False))


@dataclass
class EstadoAudio:
    cupo_agotado: bool = False
    creados: int = 0


def cuaderno_y_audio(api: Api, cfg: Config, clase: Clase, transcripcion: str, explicacion: str,
                     reg: Registro, est: EstadoAudio) -> None:
    e = reg.get(clase.clave)
    huella = sha(explicacion)

    # Entradas de versiones anteriores del script, o cuaderno con otra explicacion:
    # se crea uno nuevo y el viejo queda anotado para borrarlo con --limpiar.
    if e.get("notebook_id") and e.get("fuentes_sha") not in (None, huella):
        e.setdefault("cuadernos_reemplazados", []).append(e["notebook_id"])
        for k in ("notebook_id", "fuentes", "fuentes_sha", "audio", "audio_id"):
            e.pop(k, None)
    elif e.get("notebook_id") and "titulo" not in e:
        e.setdefault("cuadernos_reemplazados", []).append(e.pop("notebook_id"))
        for k in ("source_ids", "audio_ok", "fecha"):
            e.pop(k, None)

    nb = e.get("notebook_id")
    if nb and api.cuaderno(nb) is None:
        log(f"   [{clase.corto}] el cuaderno registrado ya no existe; se crea otro")
        nb = None
        for k in ("notebook_id", "fuentes", "fuentes_sha", "audio", "audio_id"):
            e.pop(k, None)
    if not nb:
        nb = api.crear_cuaderno(clase.titulo)
        e.update(notebook_id=nb, titulo=clase.titulo, creado=time.strftime("%Y-%m-%d %H:%M"))
        reg.guardar()
        log(f"   [{clase.corto}] cuaderno creado: {clase.titulo}  (id {nb})")
    else:
        log(f"   [{clase.corto}] cuaderno existente: {clase.titulo}  (id {nb})")

    if e.get("fuentes_sha") != huella:
        nombre_t = f"{clase.base} - Transcripción original (EN)"
        nombre_e = f"{clase.base} - Explicación sencilla (ES)"
        ya = {s.get("title") for s in (api.cuaderno(nb) or {}).get("sources", [])}
        nuevas = [(nm, tx) for nm, tx in ((nombre_t, transcripcion), (nombre_e, explicacion))
                  if nm not in ya]
        fuentes = api.agregar_textos(nb, nuevas) if nuevas else []
        ids = [(s.get("sourceId") or {}).get("id") or s.get("name", "").rsplit("/", 1)[-1]
               for s in fuentes]
        e.update(fuentes=[i for i in ids if i], fuentes_sha=huella)
        reg.guardar()
        log(f"   [{clase.corto}] fuentes subidas: transcripcion + explicacion")
        api.esperar_fuentes(nb, e["fuentes"])

    if not cfg.crear_audio or e.get("audio") == "creado":
        return
    if est.cupo_agotado:
        e["audio"] = "pendiente (cupo diario agotado)"
        reg.guardar()
        return
    try:
        res = api.crear_audio(nb)
        e.update(audio="creado", audio_id=res.get("audioOverviewId", ""),
                 audio_pedido=time.strftime("%Y-%m-%d %H:%M"))
        est.creados += 1
        log(f"   [{clase.corto}] Audio Overview en espanol pedido: estara listo en unos minutos "
            "en el panel Studio")
    except CupoAgotado as err:
        est.cupo_agotado = True
        e["audio"] = "pendiente (cupo diario agotado)"
        log(f"   [{clase.corto}] cupo diario de Audio Overviews agotado; se pedira en la proxima "
            f"ejecucion ({err})")
    except ApiError as err:
        e["audio"] = f"error: {err}"[:400]
        log(f"   [{clase.corto}] FALLO el Audio Overview: {err}")
    reg.guardar()


# --------------------------------------------------------------------------
# Busqueda de clases y ejecucion
# --------------------------------------------------------------------------

def orden_natural(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def buscar_clases(carpeta: str, solo_curso: str = "", curso: str = "") -> list[Clase]:
    clases = []
    for raiz, dirs, archivos in os.walk(carpeta):
        dirs[:] = sorted((d for d in dirs if not d.startswith((".", "_"))), key=orden_natural)
        for nombre in sorted(archivos, key=orden_natural):
            if not nombre.endswith(".txt") or nombre.endswith(SUFIJO_EXPLICACION):
                continue
            if nombre.startswith(("_", ".")) or nombre.endswith(".tmp"):
                continue
            ruta = os.path.join(raiz, nombre)
            clave = os.path.relpath(ruta, carpeta)
            if solo_curso and solo_curso.lower() not in clave.lower():
                continue
            carpeta_curso = os.path.relpath(raiz, carpeta)
            carpeta_curso = "" if carpeta_curso == "." else carpeta_curso
            if curso and carpeta_curso != curso:
                continue
            clases.append(Clase(ruta, clave, carpeta_curso, nombre[:-4]))
    return clases


def cargar_instrucciones(cfg: Config) -> dict:
    """Usa las instrucciones del script, salvo las que TU editaste en instrucciones_agentes.json.
    Asi, si el script trae instrucciones nuevas, se usan sin pisar tus cambios."""
    ruta = os.path.join(cfg.carpeta, CARPETA_INTERNA, INSTRUCCIONES)
    datos = {}
    if os.path.exists(ruta):
        try:
            datos = json.loads(leer(ruta))
        except ValueError as e:
            log(f"(no se pudo leer {ruta}: {e}; se usan las instrucciones del script)")
            datos = {}
    original = datos.get("_original_del_script") or {}
    instr, editadas = dict(INSTRUCCIONES_POR_DEFECTO), []
    for clave, defecto in INSTRUCCIONES_POR_DEFECTO.items():
        valor = datos.get(clave)
        if not isinstance(valor, str) or not valor.strip():
            continue
        conocidas = [defecto, original.get(clave) or "", *DEFECTOS_ANTERIORES.get(clave, [])]
        if valor.strip() in {c.strip() for c in conocidas}:
            continue                    # no la editaste: vale la version actual del script
        instr[clave] = valor
        editadas.append(clave)
    nuevo = {"_ayuda": "Edita el texto de cualquier subagente y se usara tu version. No toques "
                       "_original_del_script: sirve para saber que cambiaste tu.",
             **{k: instr[k] for k in INSTRUCCIONES_POR_DEFECTO},
             "_original_del_script": INSTRUCCIONES_POR_DEFECTO}
    if nuevo != datos:
        escribir(ruta, json.dumps(nuevo, indent=2, ensure_ascii=False))
    if editadas:
        log(f"Se usan tus instrucciones editadas para: {', '.join(editadas)}")
    elif not datos:
        log(f"Instrucciones de los subagentes guardadas en {ruta} (puedes editarlas ahi).")
    return instr


def clase_completa(cfg: Config, clase: Clase, reg: Registro) -> bool:
    if cfg.rehacer or not os.path.exists(clase.ruta_explicacion):
        return False
    if leer_plan(dir_pasos_de(cfg, clase)).get("explicacion_metodo") != METODO:
        return False                    # hecha con una version anterior: se rehace
    texto = leer(clase.ruta_explicacion)
    if not es_explicacion_valida(texto):
        return False
    if not cfg.crear_cuaderno:
        return True
    e = reg.datos.get(clase.clave, {})
    if not e.get("notebook_id") or e.get("fuentes_sha") != sha(texto):
        return False
    return not cfg.crear_audio or e.get("audio") == "creado"


def lista_cursos(cfg: Config) -> list[tuple[str, int, int]]:
    """(carpeta del curso, clases, clases terminadas), para elegir un curso en el notebook."""
    cfg.carpeta = resolver_carpeta(cfg.carpeta)
    reg = Registro(os.path.join(cfg.carpeta, REGISTRO))
    cursos: dict[str, list[int]] = {}
    for c in buscar_clases(cfg.carpeta):
        cuenta = cursos.setdefault(c.curso, [0, 0])
        cuenta[0] += 1
        cuenta[1] += clase_completa(cfg, c, reg)
    return [(k, v[0], v[1]) for k, v in sorted(cursos.items(), key=lambda kv: orden_natural(kv[0]))]


def resolver_carpeta(carpeta: str) -> str:
    """Acepta 'clases' aunque en Drive se llame 'Clases' (y al reves)."""
    carpeta = carpeta.rstrip("/")
    if os.path.isdir(carpeta):
        return carpeta
    padre, nombre = os.path.split(carpeta)
    if os.path.isdir(padre):
        for d in os.listdir(padre):
            if d.lower() == nombre.lower() and os.path.isdir(os.path.join(padre, d)):
                return os.path.join(padre, d)
    raise SystemExit(f"No existe la carpeta {carpeta}. Revisa el nombre y que Google Drive "
                     "este conectado (celda 1).")


def ejecutar(cfg: Config) -> int:
    cfg.carpeta = resolver_carpeta(cfg.carpeta)
    api = Api(cfg)
    api.preparar()
    instr = cargar_instrucciones(cfg)
    reg = Registro(os.path.join(cfg.carpeta, REGISTRO))
    clases = buscar_clases(cfg.carpeta, cfg.solo_curso, cfg.curso)
    if cfg.curso:
        print(f"\nCurso: {cfg.curso}")
        if not clases:
            print("No encontre transcripciones .txt en esa carpeta. Revisa el nombre del curso.")
            return 1
    pendientes = [c for c in clases if not clase_completa(cfg, c, reg)]
    print(f"\nClases encontradas: {len(clases)}   completas: {len(clases) - len(pendientes)}   "
          f"pendientes: {len(pendientes)}")
    if cfg.max_clases:
        print(f"En esta ejecucion se procesan hasta {cfg.max_clases}.")

    DETALLE.set() if cfg.detalle else DETALLE.clear()
    lote = pendientes[:cfg.max_clases] if cfg.max_clases else pendientes
    hechas = len(lote)
    est, fallos, lock_cuadernos = EstadoAudio(), [], threading.Lock()
    PARAR.clear()
    if cfg.clases_a_la_vez > 1 and len(lote) > 1:
        print(f"Se procesan {min(cfg.clases_a_la_vez, len(lote))} clases a la vez.")

    def trabajar(clase: Clase):
        if PARAR.is_set():
            return None
        log("\n" + "=" * 72 + f"\nEMPIEZA  {clase.clave}\n" + "=" * 72)
        try:
            transcripcion = leer(clase.ruta).strip()
            if len(transcripcion) < 200:
                log(f"   [{clase.corto}] transcripcion vacia o muy corta; se salta")
                return None
            explicacion = explicar_clase(api, cfg, instr, clase, transcripcion)
            if cfg.crear_cuaderno:
                with lock_cuadernos:          # el registro se escribe de a uno
                    cuaderno_y_audio(api, cfg, clase, transcripcion, explicacion, reg, est)
            log(f"TERMINADA  {clase.clave}")
            return None
        except Interrumpido:
            return None
        except (ApiError, RuntimeError, OSError, ValueError) as e:
            log(f"   [{clase.corto}] FALLO: {e}")
            return f"{clase.clave}: {e}"

    try:
        fallos = [f for f in en_paralelo(trabajar, lote, cfg.clases_a_la_vez) if f]
    except (KeyboardInterrupt, Interrumpido):
        PARAR.set()
        print("\nDetenido. Lo que ya estaba hecho quedo guardado en Drive; vuelve a ejecutar "
              "la celda para seguir desde ahi.")
        return 130

    restantes = len(pendientes) - hechas
    print("\n" + "=" * 72)
    print(f"Procesadas en esta ejecucion: {hechas}   audios pedidos: {est.creados}   "
          f"fallos: {len(fallos)}   quedan: {max(restantes, 0)}")
    if est.cupo_agotado:
        print("Se agoto el cupo diario de Audio Overviews: vuelve a ejecutar manana y se piden "
              "los que faltan (las explicaciones y los cuadernos ya quedan hechos).")
    for f in fallos:
        print(f"  FALLO {f}")
    if restantes > 0:
        print("Vuelve a ejecutar para seguir con las siguientes clases.")
    return 1 if fallos else 0


def probar(cfg: Config) -> int:
    cfg.carpeta = resolver_carpeta(cfg.carpeta)
    api = Api(cfg)
    api.preparar()
    print("\n1) Asistente de Gemini Enterprise (streamAssist)...")
    try:
        resp = api.preguntar("Responde solo con la palabra: listo", web=False)
        print(f"   OK -> '{resp.texto[:80]}' (estado {resp.estado})")
    except (ApiError, Bloqueado, RuntimeError) as e:
        print(f"   FALLO: {e}")
        return 1
    print("2) Gemini Notebook (NotebookLM Enterprise)...")
    try:
        print(f"   OK -> {len(api.listar_cuadernos())} cuadernos recientes")
    except (ApiError, RuntimeError) as e:
        print(f"   FALLO: {e}")
        return 1
    clases = buscar_clases(cfg.carpeta, cfg.solo_curso, cfg.curso)
    print(f"3) Carpeta {cfg.carpeta}: {len(clases)} transcripciones .txt")
    for c in clases[:5]:
        print(f"   {c.clave}")
    if len(clases) > 5:
        print("   ...")
    print("\nTodo listo.")
    return 0


def limpiar(cfg: Config, borrar: bool) -> int:
    """Cuadernos que sobran: los de pruebas anteriores con el mismo nombre de una clase."""
    cfg.carpeta = resolver_carpeta(cfg.carpeta)
    api = Api(cfg)
    api.preparar()
    reg = Registro(os.path.join(cfg.carpeta, REGISTRO))
    clases = buscar_clases(cfg.carpeta, cfg.solo_curso, cfg.curso)
    titulos = {c.titulo for c in clases} | {c.base for c in clases}
    actuales = {e.get("notebook_id") for e in reg.datos.values() if e.get("notebook_id")}
    reemplazados = {i for e in reg.datos.values() for i in e.get("cuadernos_reemplazados", [])}
    sobran = []
    for nb in api.listar_cuadernos():
        i = nb.get("notebookId") or nb.get("name", "").rsplit("/", 1)[-1]
        if i in actuales:
            continue
        if i in reemplazados or nb.get("title") in titulos:
            sobran.append((i, nb.get("title", ""), (nb.get("metadata") or {}).get("createTime", "")))
    if not sobran:
        print("No hay cuadernos repetidos ni de pruebas anteriores.")
        return 0
    print(f"Cuadernos que sobran ({len(sobran)}): de pruebas anteriores o reemplazados.")
    for i, t, ct in sobran:
        print(f"   {t}   (id {i}, creado {ct[:16]})")
    if not borrar:
        print("\nNo se borro nada. Marca BORRAR y vuelve a ejecutar para borrarlos.")
        return 0
    ids = [i for i, _, _ in sobran]
    for k in range(0, len(ids), 50):
        api.borrar_cuadernos(ids[k:k + 50])
    for e in reg.datos.values():
        if e.get("cuadernos_reemplazados"):
            e["cuadernos_reemplazados"] = [i for i in e["cuadernos_reemplazados"] if i not in ids]
    reg.guardar()
    print(f"Borrados {len(ids)} cuadernos.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--carpeta", default=Config.carpeta, help="carpeta con un subcarpeta por curso")
    p.add_argument("--project-id", default="", help="se detecta solo si se deja vacio")
    p.add_argument("--project-number", default="")
    p.add_argument("--location", default="global", choices=["global", "us", "eu"])
    p.add_argument("--engine-id", default="", help="app de Gemini Enterprise (se detecta sola)")
    p.add_argument("--modelo", default="", help="modelo del asistente (vacio = el de tu app)")
    p.add_argument("--max-clases", type=int, default=1, help="0 = todas")
    p.add_argument("--solo-curso", default="", help="solo rutas que contengan este texto")
    p.add_argument("--curso", default="", help="nombre exacto de la carpeta del curso")
    p.add_argument("--cursos", action="store_true", help="lista los cursos y cuantas clases faltan")
    p.add_argument("--detalle", action="store_true", help="muestra cada paso de cada parte")
    p.add_argument("--bloque-chars", type=int, default=15000)
    p.add_argument("--paralelo", type=int, default=3,
                   help="partes de una clase investigadas a la vez (fase 1)")
    p.add_argument("--clases-a-la-vez", type=int, default=1, help="clases procesadas a la vez")
    p.add_argument("--sin-busqueda-web", action="store_true")
    p.add_argument("--conservar-chats", action="store_true",
                   help="no borrar los chats que se crean en tu historial de Gemini Enterprise")
    p.add_argument("--sin-cuaderno", action="store_true", help="solo las explicaciones")
    p.add_argument("--sin-audio", action="store_true")
    p.add_argument("--idioma-audio", default="es")
    p.add_argument("--enfoque-audio", default=ENFOQUE_AUDIO)
    p.add_argument("--rehacer", action="store_true", help="rehace la explicacion aunque ya exista")
    p.add_argument("--probar", action="store_true", help="solo comprueba la conexion")
    p.add_argument("--limpiar", action="store_true", help="lista cuadernos repetidos")
    p.add_argument("--borrar", action="store_true", help="con --limpiar: los borra")
    a = p.parse_args(argv)

    cfg = Config(carpeta=a.carpeta, project_id=a.project_id, project_number=a.project_number,
                 location=a.location, engine_id=a.engine_id, modelo=a.modelo,
                 max_clases=a.max_clases, solo_curso=a.solo_curso, curso=a.curso,
                 detalle=a.detalle, bloque_chars=a.bloque_chars,
                 paralelo=a.paralelo, clases_a_la_vez=a.clases_a_la_vez,
                 busqueda_web=not a.sin_busqueda_web,
                 borrar_chats=not a.conservar_chats, crear_cuaderno=not a.sin_cuaderno,
                 crear_audio=not (a.sin_audio or a.sin_cuaderno), idioma_audio=a.idioma_audio,
                 enfoque_audio=a.enfoque_audio, rehacer=a.rehacer)
    if a.cursos:
        for nombre, total, hechas in lista_cursos(cfg):
            print(f"{hechas:>3}/{total:<3} {nombre or '(sin carpeta de curso)'}")
        return 0
    if a.probar:
        return probar(cfg)
    if a.limpiar:
        return limpiar(cfg, a.borrar)
    return ejecutar(cfg)


if __name__ == "__main__":
    sys.exit(main())
