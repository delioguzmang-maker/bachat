# Bachata class downloader

Automatically downloads all videos from a category on the class site (a Uscreen
store, e.g. `https://sms.com/categories/partnerwork`) and names each file with the
video's title, e.g. `Marco & Sara - Bachata Partner Work - 27-08-2026.mp4`.
The `/` in dates becomes `-` because file names can't contain `/`.

It does the same thing you used to do by hand, but automatically:

1. It lists every video in the category, across all pages.
2. For each video it opens the page that holds the signed
   `https://stream.mux.com/....m3u8?token=...` link.
3. It picks the best quality, or the one you ask for.
4. It downloads the `.ts` segments in parallel and retries any that fail, so the
   *"Connection reset by peer"* errors you saw no longer matter. Then it joins them
   into an `.mp4` with ffmpeg. There is no re-encoding, so the result is the same as
   `-c copy`.

Videos you already have are skipped, so you can run it again any time to pick up new
classes, or to continue after an interruption.

## Option 1: Google Colab + Google Drive (recommended)

The videos go straight to Drive and never use space on your computer.

1. Open [`bachata_colab.ipynb`](bachata_colab.ipynb) in Colab
   (File → Upload notebook in Colab, or open it from GitHub).
2. Run the cells from top to bottom:
   - Connect Google Drive.
   - Choose the category, the Drive folder and the quality.
   - Upload a HAR file, or paste your Cookie header, so the notebook is logged in.
   - Press download.

**Storage:** a 35-minute class is about 800 MB in 1080p, 400 MB in 720p and 200 MB in
480p. The category has about 45 videos, which is about 35 GB in 1080p. A free Drive
holds only 15 GB, so either choose `720` or `480`, or get more Drive storage.

## Option 2: On your computer

You need Python 3, `pip install requests`, and [ffmpeg](https://ffmpeg.org/download.html).

```bash
python bachata_downloader.py --har session.har --list                      # just show titles
python bachata_downloader.py --har session.har --out "Bachata/Partner Work"
python bachata_downloader.py --har session.har --max-height 720 --strip-prefix "Marco & Sara - "
python bachata_downloader.py --cookie "remember_user_token=...; _uscreen2_session=..." --site https://sms.com
python bachata_downloader.py --har session.har --program "https://sms.com/programs/some-video-slug"
```

If you install Google Drive for desktop and choose "stream files", you can point `--out`
at your Drive folder. The files then upload to the cloud and don't stay on your disk.

## Getting the HAR file / cookies

1. Log in to the site in your browser and open the category page.
2. Press F12 to open DevTools, go to the **Network** tab and reload the page.
3. To save a HAR, right-click any request and choose **Save All As HAR**.
   Or, to copy the cookie instead, click a request to the site, go to *Request Headers*,
   and copy the value of **Cookie**.

The HAR file and the cookie are your logged-in session, so keep them private. They expire
after a while. When the script says *"cookies expired"*, export a fresh one.

## Notes

- *Collections* (multi-lesson programs such as "Bachata Fundamentals") are listed but not
  downloaded automatically. Open each lesson in the browser and pass its URL with
  `--program`.
- Subtitles are not downloaded.
- The files are only for your personal use with your own subscription.

## Development

`bachata_colab.ipynb` contains a copy of `bachata_downloader.py`. After you edit the
script, run `python build_notebook.py` to regenerate the notebook.

---

# Second site: courses & lessons app (`vdance_downloader.py`)

This is a different platform: a JSON API with a login token and a Bunny CDN, where video
and audio come as separate streams. It needs its own script and notebook. The files above
for the first site are unchanged.

- **Notebook:** [`vdance_colab.ipynb`](vdance_colab.ipynb). Run the cells from top to bottom:
  1. Connect Google Drive.
  2. Upload a HAR file (or paste your token).
  3. List all courses.
  4. Choose courses and check the size.
  5. Download.
- **Script:** `vdance_downloader.py`.

Files are saved as `Bachata/Advanced - Pablo & Raquel/01 - Hands game and chest isolation.mp4`.
When a course exists in both English and Spanish, its folder gets `[EN]` or `[ES]`.

The whole catalog is about 110 courses, 1,700 lessons and 400 hours, which is about
770 GB in 1080p. So choose what to download:

```bash
python vdance_downloader.py --har app.har --list                        # all courses with ids & hours
python vdance_downloader.py --har app.har --course 12 --course 40       # specific courses
python vdance_downloader.py --har app.har --category Bachata --language en --max-height 720
python vdance_downloader.py --har app.har --artist "Pablo & Raquel"
python vdance_downloader.py --har app.har --my-courses
```

Adding `--list` to a selection shows the number of lessons and the estimated size without
downloading anything. Sizes are about 1.9 GB/hour in 1080p, 0.8 GB/hour in 720p and
0.45 GB/hour in 540p.

Lessons you already have are skipped, so run it again to resume. You need ffmpeg, which
merges the video and audio (Colab already has it). After you edit the script, run
`python build_vdance_notebook.py` to regenerate the notebook.

---

# Tercer sitio: grabaciones de clases Matterhorn / Opencast DCE (`dce_downloader.py`)

- **Notebook:** [`dce_colab.ipynb`](dce_colab.ipynb). Está en español; ejecuta las celdas de arriba hacia abajo.
- **Script:** `dce_downloader.py`.

Crea una carpeta por curso en Drive y guarda cada clase con su número y su fecha:
`Clases/GOVT E-1280 - Government and Politics of China/L01 - 2026-09-02 - Lecture - Pantalla.mp4`.
Hay un archivo `- Presentador.mp4` con la cámara del profesor, la transcripción en `.txt`/`.vtt`
y un `Indice de videos.csv`.

- **Máxima resolución:** pantalla en 1920x1080 y presentador en 1280x720. Cada URL se verifica
  comprobando que el tamaño en el CDN es exactamente el del track de mayor resolución, y después
  de la descarga se comprueba el MD5.
- **Video del presentador:** el JSON del sitio no incluye las URLs de los mp4. Las miniaturas
  del presentador sí apuntan (`ref`) al track de máxima resolución, y ese id es la carpeta del
  CDN donde está el mp4. El nombre del mp4 se obtiene del nombre de la miniatura.
- **Transcripción:** usa los subtítulos del sitio si el sitio publica su URL. Si no, usa Whisper.
  Conviene activar la GPU T4 en Colab.
- **Reanudación:** lo que ya está descargado se salta y las descargas cortadas se reanudan.
- **Sin inicio de sesión:** estas grabaciones son públicas (`ROLE_ANONYMOUS`).

```bash
python dce_downloader.py --list-courses --term 202701                   # ver cursos e IDs
python dce_downloader.py --course 20270117541 --out Clases              # descargar
python dce_downloader.py --course "GOVT E-1280" --term 202701 --list    # solo mostrar
python dce_downloader.py --course 20270117541 --views presenter         # solo el presentador
```

Si editas el script, ejecuta `python build_dce_notebook.py` para regenerar el notebook.

---

# Cuarto: clases explicadas en español sencillo + Audio Overview (`simplificador_clases.py`)

Usa las transcripciones `.txt` (en inglés) que deja `dce_downloader.py`. Para cada clase de cada curso:

1. Divide la transcripción en fragmentos de ~15.000 caracteres (unos 20 minutos de clase) y pasa cada
   fragmento por los 7 subagentes del *Topic Simplifier Agent* y por el agente principal, **en orden**:
   Deep Researcher → Concept Analyzer → Contextualizer → Analogy Creator → Jargon Buster →
   agente principal (síntesis) → Format Enforcer → Vocabulary Checker.
   Cada paso es una llamada al asistente de Gemini Enterprise (`streamAssist`) con las instrucciones
   de ese subagente y lo que entregaron los pasos anteriores.
2. Guarda la explicación final: `Clases/<curso>/L01 - 2026-01-27 - Lecture_simplificado_es.txt`.
3. Crea el cuaderno `HIST E-1900 · L01 - 2026-01-27 - Lecture` en Gemini Notebook (NotebookLM Enterprise)
   con 2 fuentes (transcripción + explicación) y pide el Audio Overview en español.

- **Notebook:** [`simplificador_colab.ipynb`](simplificador_colab.ipynb). Está en español; ejecuta las
  celdas de arriba hacia abajo. Necesita dos inicios de sesión: Drive (la cuenta de las clases) y
  `gcloud auth login` (la cuenta de Gemini Enterprise). El proyecto, la ubicación y la app se detectan solos.
- **Script:** `simplificador_clases.py`.

**Por qué no se llama directamente al agente del diseñador de agentes:** por la API, el agente
principal le pasa la clase a un solo subagente (el Deep Researcher), y la respuesta de ese subagente es la que vuelve.
Por eso salían "Informes de Investigación Profunda". Aquí el script hace de orquestador y llama a
cada subagente. Lo que entrega cada uno queda en `Clases/_simplificador/pasos/<curso>/<clase>/`
(`b01_1_deep_researcher.md` … `b01_8_vocabulary_checker.md`). Las instrucciones se pueden editar en
`Clases/_simplificador/instrucciones_agentes.json`.

**Audio Overview:** la API pide `{"generationOptions": {"episodeFocus": ..., "languageCode": "es"}}`.
Si `languageCode` va fuera de `generationOptions`, responde 400 *Unknown name "languageCode"*.
Hay un solo audio por cuaderno, y no se puede descargar por la API: aparece en el panel *Studio*.

- **Reanudación:** cada paso se guarda en Drive. Si Colab se corta, vuelve a ejecutar y sigue donde quedó.
- **Explicaciones viejas:** si un `_simplificado_es.txt` es un informe o tiene listas, se rehace solo.
  La versión anterior se guarda en `_simplificador/pasos`.
- **Cupo de audio:** si se acaba el cupo diario, las explicaciones y los cuadernos se siguen haciendo, y los
  audios que falten se piden en la siguiente ejecución.
- **Cuadernos repetidos** de pruebas anteriores: `--limpiar` los lista, y `--limpiar --borrar` los borra.
- **Filtro de contenido:** a veces Gemini Enterprise bloquea o corta una respuesta ("This response was
  blocked by your organization's content policy"). El script lo detecta y reintenta con lenguaje académico
  neutro. Si no funciona, parte el fragmento en dos, y como último recurso deja una nota en la explicación.
- **Respuestas de chat:** se quitan los saludos, los comentarios del asistente ("Aquí tienes…", "¿Qué te
  parece…?"), los emojis y los títulos markdown. Un paso que llegó cortado o bloqueado se rehace, y también los siguientes.
- **Velocidad:** `--paralelo` (fragmentos de una clase a la vez, 5 por defecto) y `--clases-a-la-vez`.
  Al detener la celda, se para de verdad y conserva lo hecho.

```bash
python simplificador_clases.py --carpeta /content/drive/MyDrive/Clases --probar        # comprobar conexión
python simplificador_clases.py --carpeta /content/drive/MyDrive/Clases --max-clases 1  # una clase de prueba
python simplificador_clases.py --carpeta /content/drive/MyDrive/Clases --max-clases 0  # todas
python simplificador_clases.py --carpeta ... --solo-curso "HIST E-1900" --sin-audio
python simplificador_clases.py --carpeta ... --limpiar --borrar
```

Si editas el script, ejecuta `python build_simplificador_notebook.py` para regenerar el notebook.
