#!/usr/bin/env python3
"""Regenerate simplificador_colab.ipynb from simplificador_clases.py.

Run this after editing simplificador_clases.py so the notebook (which embeds
the script) stays in sync:   python build_simplificador_notebook.py

Every code cell is a Colab form (cellView "form" + #@title), so the notebook shows
only titles, explanations and fields; the code stays hidden unless you open it.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
script = (HERE / "simplificador_clases.py").read_text(encoding="utf-8")
# The script is embedded as a raw string literal: it must not contain ''' or end in "\".
assert "'''" not in script and not script.rstrip("\n").endswith("\\")
assert "#@" not in script            # would be read as a Colab form field

ENFOQUE = ("Expliquen esta clase en español a una persona sin conocimientos previos, como lo haría "
           "un simplificador experto. Hablen con oraciones cortas y directas, en una conversación "
           "fluida, sin enumerar listas. Usen solo hechos exactos y cifras concretas que estén en "
           "las fuentes, sin suposiciones ni palabras vagas. Cuando aparezca un término técnico, "
           "tradúzcanlo al lenguaje cotidiano, den una analogía sencilla de la vida diaria y "
           "expliquen para qué sirve. Expliquen también el porqué de cada hecho. Usen la "
           "explicación en español como guía y la transcripción en inglés para comprobar los "
           "detalles.")


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def form(text):
    return {"cell_type": "code", "metadata": {"cellView": "form"}, "execution_count": None,
            "outputs": [], "source": text.strip("\n").splitlines(True)}


cells = [
    md("""
# 🧠 Clases explicadas en español sencillo + Audio Overview

Para cada clase de la carpeta de cursos (`Clases/<curso>/L01 - 2026-01-27 - Lecture.txt`, en inglés):

1. La pasa por **los 7 subagentes del Topic Simplifier y el agente principal**, uno detrás de otro, con
   **tus reglas de estilo**: párrafos fluidos, oraciones cortas, cada término técnico traducido al
   lenguaje de todos los días, con una analogía y para qué sirve.
2. Guarda la explicación junto a la transcripción: `L01 - 2026-01-27 - Lecture_simplificado_es.txt`.
3. Crea su cuaderno en **Gemini Notebook** (transcripción + explicación) y pide el **Audio Overview en español**.

**Ejecuta las celdas en orden** con el botón ▶. No hace falta GPU.
El código está oculto; si quieres verlo, haz doble clic en una celda.
"""),
    form("""
#@title 1. Conectar Google Drive (la cuenta donde está la carpeta de clases)
from google.colab import drive
drive.mount('/content/drive')
"""),
    form("""
#@title 2. Iniciar sesión con tu cuenta de Gemini Enterprise
#@markdown Abre el enlace que aparece, entra con la cuenta que usa **Gemini Enterprise** (puede ser distinta
#@markdown a la de Drive), copia el código y pégalo en el recuadro.
!gcloud auth login --no-launch-browser --quiet
!gcloud auth list --filter=status:ACTIVE --format="value(account)"
"""),
    form("""
#@title 3. Preparar y comprobar la conexión
#@markdown Carpeta de Drive que tiene una subcarpeta por curso (da igual `Clases` o `clases`):
CARPETA_CLASES = "/content/drive/MyDrive/Clases"  #@param {type:"string"}

CODIGO = r'''""" + script + """'''
with open("/content/simplificador_clases.py", "w", encoding="utf-8") as f:
    f.write(CODIGO)
import importlib, sys
sys.path.insert(0, "/content")
import simplificador_clases as sc
importlib.reload(sc)

def run(args):
    try:
        return sc.main(args)
    except SystemExit as e:
        if e.code not in (0, None):
            print(e.code)

if "OPCIONES" not in globals():
    OPCIONES = ["--clases-a-la-vez", "3"]
run(["--carpeta", CARPETA_CLASES, "--probar"] + OPCIONES)
"""),
    form("""
#@title 4. Elegir el curso
#@markdown Ejecuta la celda y elige el curso en el menú que aparece abajo. Junto a cada curso se ve
#@markdown cuántas clases ya están terminadas (explicación + cuaderno + audio).
import ipywidgets as widgets
from IPython.display import display

_cursos = sc.lista_cursos(sc.Config(carpeta=CARPETA_CLASES))
_opciones = [(f"Todos los cursos ({sum(t for _, t, _ in _cursos)} clases)", "")]
_opciones += [(f"{nombre}   ({hechas} de {total} terminadas)", nombre)
              for nombre, total, hechas in _cursos if nombre]
CURSO_ELEGIDO = widgets.Dropdown(options=_opciones, value="", description="Curso:",
                                 layout=widgets.Layout(width="95%"))
display(CURSO_ELEGIDO)
print("Elige el curso y sigue con la celda 5. (Si cambias de curso, no hace falta volver a ejecutar esta celda.)")
"""),
    form("""
#@title (Opcional) Opciones avanzadas: normalmente no hace falta tocar nada
#@markdown Si cambias algo aquí, ejecuta esta celda antes de la celda 5.
#@markdown
#@markdown ---
#@markdown **Proyecto y app de Gemini Enterprise.** Gemini Enterprise funciona dentro de un *proyecto*
#@markdown de Google Cloud, y tu asistente es una *app* dentro de ese proyecto. La celda 3 los encuentra
#@markdown sola y te dice cuáles usó. Escribe algo aquí solo si tienes varios y eligió el que no era.
PROYECTO = ""  #@param {type:"string"}
NUMERO_DEL_PROYECTO = ""  #@param {type:"string"}
APP = ""  #@param {type:"string"}
REGION = "global"  #@param ["global", "us", "eu"]
#@markdown ---
#@markdown **Velocidad.** Cuántas clases avanzan a la vez, y cuántas partes de cada clase se investigan
#@markdown a la vez. Si ves muchos avisos `HTTP 429` (demasiadas peticiones), bájalos.
CLASES_A_LA_VEZ = 3  #@param {type:"integer"}
PARTES_A_LA_VEZ = 3  #@param {type:"integer"}
#@markdown **Tamaño de cada parte** de la clase, en caracteres (15000 son unos 20 minutos de clase):
TAMANO_PARTE = 15000  #@param {type:"integer"}
#@markdown ---
#@markdown **Búsqueda en Google** para los 4 primeros subagentes (para verificar nombres, fechas y cifras):
BUSQUEDA_WEB = True  #@param {type:"boolean"}
#@markdown Cada paso crea un chat en tu historial de Gemini Enterprise. Si está marcado, el notebook
#@markdown borra esos chats al terminar cada paso (no toca tus otros chats):
BORRAR_CHATS_TEMPORALES = True  #@param {type:"boolean"}
#@markdown Modelo del asistente (vacío = el que usa tu app):
MODELO = ""  #@param {type:"string"}
#@markdown ---
#@markdown **Audio Overview**: idioma y lo que les pides a los presentadores.
IDIOMA_AUDIO = "es"  #@param {type:"string"}
ENFOQUE_AUDIO = \"""" + ENFOQUE + """\"  #@param {type:"string"}

OPCIONES = ["--location", REGION, "--clases-a-la-vez", str(CLASES_A_LA_VEZ),
            "--paralelo", str(PARTES_A_LA_VEZ), "--bloque-chars", str(TAMANO_PARTE),
            "--idioma-audio", IDIOMA_AUDIO, "--enfoque-audio", ENFOQUE_AUDIO]
for _flag, _valor in (("--project-id", PROYECTO), ("--project-number", NUMERO_DEL_PROYECTO),
                      ("--engine-id", APP), ("--modelo", MODELO)):
    if _valor.strip():
        OPCIONES += [_flag, _valor.strip()]
if not BUSQUEDA_WEB:
    OPCIONES.append("--sin-busqueda-web")
if not BORRAR_CHATS_TEMPORALES:
    OPCIONES.append("--conservar-chats")
print("Opciones guardadas.")
"""),
    form("""
#@title 5. Procesar las clases 🚀
#@markdown **MAX_CLASES**: empieza con `1` para revisar cómo queda. Después pon `0` (= todas las del curso elegido).
MAX_CLASES = 1  #@param {type:"integer"}
#@markdown Crear el cuaderno en Gemini Notebook y pedir el Audio Overview:
CREAR_CUADERNO = True  #@param {type:"boolean"}
CREAR_AUDIO = True  #@param {type:"boolean"}
#@markdown Rehacer la explicación aunque ya exista. Normalmente no hace falta: las explicaciones hechas
#@markdown con versiones anteriores del notebook se rehacen solas.
REHACER_EXPLICACION = False  #@param {type:"boolean"}
#@markdown Mostrar lo que hace cada subagente en cada parte (si no, solo se ve el progreso):
MOSTRAR_DETALLE = False  #@param {type:"boolean"}

if "OPCIONES" not in globals():
    OPCIONES = ["--clases-a-la-vez", "3"]
_curso = CURSO_ELEGIDO.value if "CURSO_ELEGIDO" in globals() else ""
_args = ["--carpeta", CARPETA_CLASES, "--max-clases", str(MAX_CLASES)] + OPCIONES
if _curso:
    _args += ["--curso", _curso]
if not CREAR_CUADERNO:
    _args.append("--sin-cuaderno")
if not CREAR_AUDIO:
    _args.append("--sin-audio")
if REHACER_EXPLICACION:
    _args.append("--rehacer")
if MOSTRAR_DETALLE:
    _args.append("--detalle")
run(_args)
"""),
    md("""
**Cómo leer los mensajes de la celda 5**

* Cada clase se divide en ~5 partes de unos 20 minutos. Primero se investigan todas las partes
  (Deep Researcher, Concept Analyzer, Contextualizer). Después se redactan **en orden**: analogías,
  jerga, el agente principal escribe, el Jargon Buster revisa que no queden palabras técnicas sin explicar,
  el Format Enforcer deja oraciones cortas y fluidas y el Vocabulary Checker cambia las palabras vagas.
* Cada parte terminada dice algo como `parte 2/5 lista (14 palabras por oración …)`: así ves si el texto
  queda fácil de leer.
* Se puede detener con el botón ■ y volver a ejecutar: sigue donde quedó.
* Si el filtro de contenido de Gemini Enterprise bloquea una respuesta (pasa con estatuas, cuerpos o
  temas delicados), se reintenta pidiendo describir solo eso de forma indirecta.
* Si se acaba el cupo diario de Audio Overviews, las explicaciones y los cuadernos se siguen haciendo, y
  los audios que falten se piden la próxima vez.
"""),
    form("""
#@title 6. (Opcional) Borrar cuadernos repetidos de pruebas anteriores
#@markdown Primero ejecútala sin marcar nada: solo **lista** los cuadernos que sobran (de pruebas anteriores
#@markdown o reemplazados por una versión nueva). Si estás de acuerdo, marca BORRAR y vuelve a ejecutarla.
#@markdown Nunca toca los cuadernos actuales de las clases ni tus otros cuadernos.
BORRAR = False  #@param {type:"boolean"}
if "OPCIONES" not in globals():
    OPCIONES = []
run(["--carpeta", CARPETA_CLASES, "--limpiar"] + (["--borrar"] if BORRAR else []) + OPCIONES)
"""),
    md("""
## Notas

* **Lo que entrega cada subagente** queda en `Clases/_simplificador/pasos/<curso>/<clase>/`.
* **Instrucciones de los subagentes y tus reglas de estilo**: están en
  `Clases/_simplificador/instrucciones_agentes.json`. Si cambias un texto ahí, se usa tu versión; lo que no
  cambies se actualiza solo con las versiones nuevas del notebook.
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
