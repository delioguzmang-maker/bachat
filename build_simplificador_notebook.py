#!/usr/bin/env python3
"""Regenerate simplificador_colab.ipynb from simplificador_clases.py.

Run this after editing simplificador_clases.py so the notebook (which embeds
the script) stays in sync:   python build_simplificador_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
script = (HERE / "simplificador_clases.py").read_text(encoding="utf-8")


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text, hidden=False):
    meta = {"cellView": "form"} if hidden else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None,
            "outputs": [], "source": text.strip("\n").splitlines(True)}


cells = [
    md("""
# 🧠 Clases explicadas en español sencillo + Audio Overview (Gemini Notebook)

Toma las transcripciones en inglés que dejó el notebook de descarga
(`Clases/<curso>/L01 - 2026-01-27 - Lecture.txt`) y, para **cada clase de cada carpeta**:

1. Pasa la clase, por fragmentos de unos 20 minutos, por **los 7 subagentes del Topic Simplifier
   y el agente principal**, **uno detrás de otro**:
   Deep Researcher → Concept Analyzer → Contextualizer → Analogy Creator → Jargon Buster →
   agente principal (síntesis) → Format Enforcer → Vocabulary Checker.
2. Guarda la explicación final junto a la transcripción: `L01 - 2026-01-27 - Lecture_simplificado_es.txt`.
3. Crea el cuaderno **“HIST E-1900 · L01 - 2026-01-27 - Lecture”** en Gemini Notebook, con 2 fuentes
   (transcripción + explicación), y pide el **Audio Overview en español**.

Lo que entrega cada subagente queda en `Clases/_simplificador/pasos/...`, así puedes comprobar que
trabajaron todos. Si Colab se desconecta, **vuelve a ejecutar la celda 6**: sigue desde el último paso guardado.

**¿Por qué no se llama directamente a tu agente?** Por la API, tu agente le pasaba la clase a **un solo**
subagente (el Deep Researcher), y la respuesta de ese subagente era la que volvía. Por eso te llegaban
“Informes de Investigación Profunda” en vez de la explicación. Aquí el notebook hace de orquestador:
llama a cada subagente, en orden, con sus instrucciones.

**Ejecuta las celdas de arriba hacia abajo** (botón ▶ de cada celda). No hace falta GPU.
"""),
    md("## 1. Conectar Google Drive (la cuenta donde está la carpeta de clases)"),
    code("""
from google.colab import drive
drive.mount('/content/drive')
"""),
    md("""
## 2. Iniciar sesión con la cuenta de **Gemini Enterprise**

Abre el enlace, entra con la cuenta que usa Gemini Enterprise (puede ser distinta a la de Drive),
copia el código y pégalo en el recuadro.
"""),
    code("""
!gcloud auth login --no-launch-browser --quiet
!gcloud auth list
"""),
    md("## 3. Cargar el código (solo ejecútala)"),
    code("%%writefile /content/simplificador_clases.py\n" + script),
    code("""
import importlib, sys
sys.path.insert(0, "/content")
import simplificador_clases as sc
importlib.reload(sc)

def run(args):
    try:
        return sc.main(args)
    except SystemExit as e:
        if e.code not in (0, None): print(e.code)
"""),
    md("""
## 4. Configuración

* **CARPETA_CLASES**: la carpeta con una subcarpeta por curso (`Clases` o `clases`, da igual).
* **PROJECT_ID / PROJECT_NUMBER / APP_ID**: déjalos vacíos y se detectan solos.
* **SOLO_CURSO**: vacío = todos los cursos. Si escribes, por ejemplo, `HIST E-1900`, solo hace ese curso.
* **FRAGMENTOS_A_LA_VEZ**: cuántos fragmentos de una misma clase avanzan a la vez (5 = una clase entera de ~1,5 h).
* **CLASES_A_LA_VEZ**: cuántas clases avanzan a la vez. Con 2 va el doble de rápido; si ves muchos
  avisos `HTTP 429`, bájalo a 1.
* **BORRAR_CHATS_TEMPORALES**: cada paso crea un chat en tu historial de Gemini Enterprise; si está
  marcado, el notebook los borra al terminar cada paso (no toca tus otros chats).
"""),
    code("""
CARPETA_CLASES = "/content/drive/MyDrive/Clases"  #@param {type:"string"}
PROJECT_ID = ""  #@param {type:"string"}
PROJECT_NUMBER = ""  #@param {type:"string"}
APP_ID = ""  #@param {type:"string"}
UBICACION = "global"  #@param ["global", "us", "eu"]
SOLO_CURSO = ""  #@param {type:"string"}
BUSQUEDA_WEB = True  #@param {type:"boolean"}
BORRAR_CHATS_TEMPORALES = True  #@param {type:"boolean"}
FRAGMENTO_CARACTERES = 15000  #@param {type:"integer"}
FRAGMENTOS_A_LA_VEZ = 5  #@param {type:"integer"}
CLASES_A_LA_VEZ = 2  #@param {type:"integer"}
#@markdown Opcional: modelo del asistente (vacío = el de tu app de Gemini Enterprise)
MODELO = ""  #@param {type:"string"}

BASE = ["--carpeta", CARPETA_CLASES, "--location", UBICACION,
        "--bloque-chars", str(FRAGMENTO_CARACTERES), "--paralelo", str(FRAGMENTOS_A_LA_VEZ),
        "--clases-a-la-vez", str(CLASES_A_LA_VEZ)]
for flag, valor in (("--project-id", PROJECT_ID), ("--project-number", PROJECT_NUMBER),
                    ("--engine-id", APP_ID), ("--solo-curso", SOLO_CURSO), ("--modelo", MODELO)):
    if valor.strip():
        BASE += [flag, valor.strip()]
if not BUSQUEDA_WEB:
    BASE.append("--sin-busqueda-web")
if not BORRAR_CHATS_TEMPORALES:
    BASE.append("--conservar-chats")
print("Configuración lista.")
""", hidden=True),
    md("""
## 5. Probar la conexión (10 segundos)

Comprueba el asistente de Gemini Enterprise, Gemini Notebook y la carpeta de Drive.
"""),
    code("""
run(BASE + ["--probar"])
""", hidden=True),
    md("""
## 6. Procesar las clases 🚀

* **MAX_CLASES**: empieza con `1` para revisar el resultado. Después pon `0` (= todas).
* **Cómo leer los mensajes**: cada clase se divide en ~5 fragmentos y **cada fragmento** pasa por los
  8 pasos. Por eso verás `paso 1/8 … paso 8/8` varias veces (una por fragmento): no es un bucle.
  Cada fragmento terminado dice `fragmento 3/5 terminado (listos: 3 de 5)`.
* **Tiempo**: una clase de ~1,5 h tarda unos 3–6 minutos. Con cientos de clases son muchas horas:
  Colab se desconecta a las pocas horas, pero **vuelve a ejecutar esta celda** y sigue donde quedó.
  Con **SOLO_CURSO** (celda 4) puedes hacer primero el curso que más necesitas.
* **Para detenerlo**: botón ■ de la celda. Se para al terminar la llamada en curso y todo lo hecho queda guardado.
* Si el filtro de contenido de Gemini Enterprise bloquea o corta una respuesta (pasa con descripciones
  de estatuas o temas delicados), se reintenta con lenguaje académico neutro; si aun así no se puede,
  ese trozo se parte en dos, y como último recurso queda una nota en la explicación.
* Las clases ya terminadas se saltan. Las explicaciones viejas que eran un “Informe” o tenían
  listas se detectan solas y se rehacen (la versión anterior se guarda en `_simplificador/pasos`).
* Si se acaba el cupo diario de Audio Overviews, las explicaciones y los cuadernos se siguen haciendo,
  y los audios que falten se piden la próxima vez que ejecutes esta celda.
* **ENFOQUE_AUDIO**: lo que les pides a los presentadores del Audio Overview.
"""),
    code("""
MAX_CLASES = 1  #@param {type:"integer"}
CREAR_CUADERNO = True  #@param {type:"boolean"}
CREAR_AUDIO = True  #@param {type:"boolean"}
REHACER_EXPLICACION = False  #@param {type:"boolean"}
IDIOMA_AUDIO = "es"  #@param {type:"string"}
ENFOQUE_AUDIO = "Expliquen esta clase en español a una persona sin conocimientos previos, como lo haría un simplificador experto. Hablen con oraciones cortas y directas, en una conversación fluida, sin enumerar listas. Usen solo hechos exactos y cifras concretas que estén en las fuentes, sin suposiciones ni palabras vagas. Cuando aparezca un término técnico, tradúzcanlo al lenguaje cotidiano, den una analogía sencilla de la vida diaria y expliquen para qué sirve. Expliquen también el porqué de cada hecho. Usen la explicación en español como guía y la transcripción en inglés para comprobar los detalles."  #@param {type:"string"}

args = BASE + ["--max-clases", str(MAX_CLASES), "--idioma-audio", IDIOMA_AUDIO,
               "--enfoque-audio", ENFOQUE_AUDIO]
if not CREAR_CUADERNO:
    args.append("--sin-cuaderno")
if not CREAR_AUDIO:
    args.append("--sin-audio")
if REHACER_EXPLICACION:
    args.append("--rehacer")
run(args)
""", hidden=True),
    md("""
## 7. (Opcional) Cuadernos repetidos de pruebas anteriores

Las pruebas anteriores crearon cuadernos (por ejemplo *“L01 - 2026-01-27 - Lecture”*) que quedaron
sin audio y con el informe viejo como fuente. Esta celda los **lista**; solo los borra si marcas **BORRAR**.
Nunca toca los cuadernos actuales de las clases ni tus otros cuadernos.
"""),
    code("""
BORRAR = False  #@param {type:"boolean"}
run(BASE + ["--limpiar"] + (["--borrar"] if BORRAR else []))
""", hidden=True),
    md("""
## Notas

* **Instrucciones de los subagentes**: la primera ejecución las guarda en
  `Clases/_simplificador/instrucciones_agentes.json`. Si quieres usar el texto exacto de tu agente,
  pégalo ahí (una entrada por subagente) y vuelve a ejecutar.
* **Registro**: `Clases/registro_clases_procesadas.json` guarda el cuaderno de cada clase y si el audio ya se pidió.
* **El audio** no se puede descargar por la API: aparece en el panel *Studio* del cuaderno unos minutos después.
"""),
]

nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "simplificador_colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}
(HERE / "simplificador_colab.ipynb").write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
print("wrote simplificador_colab.ipynb")
