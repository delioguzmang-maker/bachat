#!/usr/bin/env python3
"""Regenerate vdance_colab.ipynb from vdance_downloader.py.

Run this after editing vdance_downloader.py so the notebook (which embeds
the script) stays in sync:   python build_vdance_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
script = (HERE / "vdance_downloader.py").read_text()


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text, hidden=False):
    meta = {"cellView": "form"} if hidden else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None,
            "outputs": [], "source": text.strip("\n").splitlines(True)}


RUN = """
import importlib, sys
sys.path.insert(0, "/content")
import vdance_downloader as vd
importlib.reload(vd)

def selection_args():
    a = []
    for c in COURSE_IDS.split(","):
        if c.strip():
            a += ["--course", c.strip()]
    for c in CATEGORY.split(","):
        if c.strip():
            a += ["--category", c.strip()]
    for c in ARTIST.split(","):
        if c.strip():
            a += ["--artist", c.strip()]
    if MY_COURSES:
        a += ["--my-courses"]
    if LANGUAGE != "any":
        a += ["--language", LANGUAGE]
    return a

def run(args):
    try:
        vd.main(AUTH_ARGS + args)
    except SystemExit as e:
        if e.code not in (0, None): print(e.code)
"""

cells = [
    md("""
# 💃 Dance course downloader (courses & lessons site)

Downloads whole **courses** (all their lessons) into your **Google Drive**:

```
Dance Courses/Bachata/Advanced - Pablo & Raquel/01 - Hands game and chest isolation.mp4
Dance Courses/Bachata/Advanced - Pablo & Raquel/02 - Block and rewind.mp4
...
```

Lessons you already have are skipped, so re-run any time (or after Colab disconnects).

⚠️ The whole catalog is ~110 courses / ~1,700 lessons / ~400 hours ≈ **770 GB in 1080p**,
so choose the courses you want (step 5 shows the size before downloading).

**Run the cells from top to bottom** (▶ button on each cell).
"""),
    md("## 1. Connect Google Drive"),
    code("""
from google.colab import drive
drive.mount('/content/drive')
"""),
    md("""
## 2. Log in (give the notebook your session)

**A) Upload a HAR file** (like you did before):
1. Log in to the app in your browser and go to the courses page.
2. Open DevTools (F12) → **Network** tab → reload the page.
3. Right‑click any request → **Save All As HAR**.
4. Run the cell below and upload the file.

**B) Paste the token**: DevTools → Network → click any request to the `api.` site →
*Request Headers* → **Authorization** → copy the part after `Bearer `.
Set `METHOD` to `paste token` and paste it when asked.

⚠️ The HAR / token is your logged‑in account (the HAR also has your email etc.) — don't share it.
If the notebook later says *"token has expired"*, just repeat this step.
"""),
    code("""
METHOD = "upload HAR"  #@param ["upload HAR", "paste token"]
#@markdown Only needed for "paste token" (the site's addresses, as seen in DevTools):
API_URL = "https://api.3973.sms"  #@param {type:"string"}
APP_URL = "https://app.sms"  #@param {type:"string"}

import os
if METHOD == "upload HAR":
    from google.colab import files
    uploaded = files.upload()
    os.replace(next(iter(uploaded)), "/content/vdance_session.har")
    AUTH_ARGS = ["--har", "/content/vdance_session.har"]
    print("HAR uploaded.")
else:
    from getpass import getpass
    AUTH_ARGS = ["--token", getpass("Paste the token: "), "--api", API_URL, "--app", APP_URL]
    print("Token saved.")
""", hidden=True),
    md("## 3. Load the downloader code (just run it)"),
    code("%%writefile /content/vdance_downloader.py\n" + script),
    code(RUN),
    md("""
## 4. See all courses (to find the IDs you want)
"""),
    code("run([\"--list\"])"),
    md("""
## 5. Choose what to download

Fill in any combination (they add up):

* **COURSE_IDS**: IDs from the list above, separated by commas, e.g. `12, 40, 17`
* **CATEGORY**: e.g. `Bachata` (all Bachata courses), or `Bachata, Salsa`
* **ARTIST**: e.g. `Pablo & Raquel`
* **MY_COURSES**: tick to include the courses in your "My courses"
* **LANGUAGE**: many courses exist in both English and Spanish; pick one to avoid duplicates
* **MAX_HEIGHT**: `1080` ≈ 1.9 GB/hour, `720` ≈ 0.8 GB/hour, `540` ≈ 0.45 GB/hour

Run the cell to see how many lessons/GB your selection is **before** downloading.
A free Google Drive has 15 GB.
"""),
    code("""
COURSE_IDS = "12"  #@param {type:"string"}
CATEGORY = ""  #@param {type:"string"}
ARTIST = ""  #@param {type:"string"}
MY_COURSES = False  #@param {type:"boolean"}
LANGUAGE = "any"  #@param ["any", "en", "es"]
MAX_HEIGHT = "1080"  #@param ["1080", "720", "540", "360"]
OUTPUT_FOLDER = "/content/drive/MyDrive/Dance Courses"  #@param {type:"string"}

run(selection_args() + ["--max-height", MAX_HEIGHT, "--list"])
""", hidden=True),
    md("""
## 6. Download 🚀

Keep the tab open (Colab stops after ~90 min idle or ~12 h). If it stops, run this cell
again — it continues where it left off. Tip: add `"--limit", "1"` to try one lesson first.
"""),
    code("""
run(selection_args() + [
    "--out", OUTPUT_FOLDER,
    "--max-height", MAX_HEIGHT,
    "--tmp-dir", "/content",   # download to the fast local disk, then move to Drive
])
"""),
]

nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "vdance_colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}
(HERE / "vdance_colab.ipynb").write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
print("wrote vdance_colab.ipynb")
