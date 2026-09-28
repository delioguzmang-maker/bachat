#!/usr/bin/env python3
"""Regenerate dce_colab.ipynb from dce_downloader.py.

Run this after editing dce_downloader.py so the notebook (which embeds
the script) stays in sync:   python build_dce_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
script = (HERE / "dce_downloader.py").read_text()


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text, hidden=False):
    meta = {"cellView": "form"} if hidden else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None,
            "outputs": [], "source": text.strip("\n").splitlines(True)}


cells = [
    md("""
# 🎓 Descargar clases (Matterhorn DCE) a Google Drive — una carpeta por curso

Para cada curso crea una carpeta en tu Drive y guarda **cada clase** con su número y fecha:

```
Clases/GOVT E-1280 - Government and Politics of China/
    L01 - 2026-09-02 - Lecture - Pantalla.mp4      ← computadora/diapositivas (1920x1080)
    L01 - 2026-09-02 - Lecture - Presentador.mp4   ← cámara del profesor (1280x720)
    L01 - 2026-09-02 - Lecture.txt  /  .vtt        ← transcripción
    ...
    Indice de videos.csv
```

* Siempre la **resolución más alta**, verificada por tamaño exacto y con **MD5** después de bajarla.
* Lo que ya está en Drive se salta; si Colab se desconecta, **vuelve a ejecutar** y continúa.
* No necesita iniciar sesión: estas grabaciones son públicas en el sitio.

**Ejecuta las celdas de arriba hacia abajo** (botón ▶ de cada celda).
Si vas a transcribir: *Entorno de ejecución → Cambiar tipo de entorno → GPU T4* (Whisper es ~10x más rápido).
"""),
    md("## 1. Conectar Google Drive"),
    code("""
from google.colab import drive
drive.mount('/content/drive')
"""),
    md("## 2. Cargar el código (solo ejecútala)"),
    code("%%writefile /content/dce_downloader.py\n" + script),
    code("""
import importlib, sys
sys.path.insert(0, "/content")
import dce_downloader as dce
importlib.reload(dce)

def run(args):
    try:
        dce.main(args)
    except SystemExit as e:
        if e.code not in (0, None): print(e.code)
"""),
    md("""
## 3. (Opcional) Ver los cursos del término, para encontrar el ID

El ID del curso también sale de su URL: `.../engage/ui/index.html#/2027/01/17541` → `20270117541`.
"""),
    code("""
TERMINO = "202701"  #@param {type:"string"}
HOST = "https://matterhorn.dce.sms.com"  #@param {type:"string"}
run(["--list-courses", "--term", TERMINO, "--host", HOST])
""", hidden=True),
    md("""
## 4. Configurar y descargar 🚀

* **CURSOS**: separados por comas. Acepta el ID (`20270117541`), la URL del curso o el código (`GOVT E-1280`, usa el TERMINO de arriba).
* **SOLO_MOSTRAR**: márcalo solo si quieres ver qué se va a bajar sin descargar nada.
* **Espacio**: cada clase de ~1 hora ocupa ~0.9 GB (≈0.7 GB presentador + 0.2 GB pantalla).
  Un curso completo de 13 clases ≈ 12 GB. Si no te cabe, desmarca `DESCARGAR_PRESENTADOR`.
"""),
    code("""
CURSOS = "20270117541"  #@param {type:"string"}
CARPETA_DRIVE = "/content/drive/MyDrive/Clases"  #@param {type:"string"}
DESCARGAR_PANTALLA = True  #@param {type:"boolean"}
DESCARGAR_PRESENTADOR = True  #@param {type:"boolean"}
TRANSCRIBIR = "subtitulos del sitio o Whisper"  #@param ["subtitulos del sitio o Whisper", "solo subtitulos del sitio", "no"]
MODELO_WHISPER = "base"  #@param ["tiny", "base", "small", "medium"]
SOLO_MOSTRAR = False  #@param {type:"boolean"}
TERMINO = "202701"  #@param {type:"string"}
HOST = "https://matterhorn.dce.sms.com"  #@param {type:"string"}
#@markdown Opcional, solo si el sitio empezara a pedir inicio de sesion:
USAR_COOKIE = False  #@param {type:"boolean"}

vistas = [v for v, on in (("presentation", DESCARGAR_PANTALLA),
                          ("presenter", DESCARGAR_PRESENTADOR)) if on]
args = ["--out", CARPETA_DRIVE, "--host", HOST, "--term", TERMINO,
        "--views", ",".join(vistas),
        "--transcribe", {"subtitulos del sitio o Whisper": "auto",
                         "solo subtitulos del sitio": "site", "no": "no"}[TRANSCRIBIR],
        "--whisper-model", MODELO_WHISPER,
        "--tmp-dir", "/content/tmp_clases"]   # disco local rapido; luego se copia a Drive
for c in CURSOS.split(","):
    if c.strip():
        args += ["--course", c.strip()]
if SOLO_MOSTRAR:
    args.append("--list")
if USAR_COOKIE:
    from getpass import getpass
    args += ["--cookie", getpass("Pega el valor de la cookie: ")]

if not vistas:
    print("Marca al menos DESCARGAR_PANTALLA o DESCARGAR_PRESENTADOR.")
else:
    run(args)
""", hidden=True),
]

nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "dce_colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
        "accelerator": "GPU",
    },
    "cells": cells,
}
(HERE / "dce_colab.ipynb").write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
print("wrote dce_colab.ipynb")
