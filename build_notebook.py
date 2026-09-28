#!/usr/bin/env python3
"""Regenerate bachata_colab.ipynb from bachata_downloader.py.

Run this after editing bachata_downloader.py so the notebook (which embeds
the script) stays in sync:   python build_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
script = (HERE / "bachata_downloader.py").read_text()


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text, hidden=False):
    meta = {"cellView": "form"} if hidden else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None,
            "outputs": [], "source": text.strip("\n").splitlines(True)}


cells = [
    md("""
# 💃 Bachata class downloader

Downloads **every video of a category** (e.g. *Partner Work*) straight into your
**Google Drive**, named with the video title
(`Marco & Sara - Bachata Partner Work - 27-08-2026.mp4`).

Videos you already have are skipped, so just run it again whenever new classes come out
(or if Colab disconnects in the middle).

**Run the cells from top to bottom** (▶ button on each cell).
"""),
    md("## 1. Connect Google Drive"),
    code("""
from google.colab import drive
drive.mount('/content/drive')
"""),
    md("""
## 2. Settings

* **CATEGORY**: the part after `/categories/` in the site URL (`https://sms.com/categories/partnerwork` → `partnerwork`).
  Several categories: separate with commas.
* **MAX_HEIGHT**: `1080` ≈ 800 MB per 35‑min class, `720` ≈ 400 MB, `480` ≈ 200 MB.
  A free Google Drive has 15 GB, so ~45 classes in 1080p won't fit — use 720 or upgrade Drive.
* **STRIP_PREFIX**: optional text to remove from the start of names, e.g. `Marco & Sara - `.
"""),
    code("""
CATEGORY = "partnerwork"  #@param {type:"string"}
OUTPUT_FOLDER = "/content/drive/MyDrive/Bachata/Partner Work"  #@param {type:"string"}
MAX_HEIGHT = "1080"  #@param ["1080", "720", "480"]
STRIP_PREFIX = ""  #@param {type:"string"}
""", hidden=True),
    md("""
## 3. Log in (give the notebook your browser session)

The notebook needs your login cookies. Pick one method:

**A) Upload a HAR file** (what you already did):
1. In Firefox/Chrome, log in to the site and open any page (e.g. the category page).
2. Open DevTools (F12) → **Network** tab → reload the page.
3. Right‑click any request → **Save All As HAR** (Firefox) / **Save all as HAR with content** (Chrome).
4. Run the cell below and upload the file.

**B) Paste the Cookie header**: DevTools → Network → click a request to the site →
*Request Headers* → right‑click **Cookie** → copy value. Set `METHOD` to `paste cookie` and
paste it when asked (it stays hidden).

⚠️ The HAR / cookie is your logged‑in session — don't share it with anyone.
Cookies expire after a while; if downloads start failing with *"cookies expired"*, just do this step again.
"""),
    code("""
METHOD = "upload HAR"  #@param ["upload HAR", "paste cookie"]
SITE = "https://sms.com"  #@param {type:"string"}

import os
if METHOD == "upload HAR":
    from google.colab import files
    uploaded = files.upload()
    har_name = next(iter(uploaded))
    os.replace(har_name, "/content/session.har")
    AUTH_ARGS = ["--har", "/content/session.har", "--site", SITE]
    print("HAR uploaded.")
else:
    from getpass import getpass
    AUTH_ARGS = ["--cookie", getpass("Paste the Cookie header value: "), "--site", SITE]
    print("Cookie saved.")
""", hidden=True),
    md("## 4. Load the downloader code (just run it)"),
    code("%%writefile /content/bachata_downloader.py\n" + script),
    md("## 5. (Optional) See which videos will be downloaded"),
    code("""
import importlib, sys
sys.path.insert(0, "/content")
import bachata_downloader as bd
importlib.reload(bd)

cat_args = [a for c in CATEGORY.split(",") if c.strip() for a in ("--category", c.strip())]
try:
    bd.main(AUTH_ARGS + cat_args + ["--list"])
except SystemExit as e:
    if e.code not in (0, None): print(e.code)
"""),
    md("""
## 6. Download 🚀

Leave the tab open while it runs (Colab stops after ~90 min of inactivity or ~12 h total).
If it stops, just run this cell again — it continues where it left off.
Tip: add `"--limit", "1"` to the list below to try a single video first.
"""),
    code("""
import importlib, sys
sys.path.insert(0, "/content")
import bachata_downloader as bd
importlib.reload(bd)

cat_args = [a for c in CATEGORY.split(",") if c.strip() for a in ("--category", c.strip())]
args = AUTH_ARGS + cat_args + [
    "--out", OUTPUT_FOLDER,
    "--max-height", MAX_HEIGHT,
    "--strip-prefix", STRIP_PREFIX,
    "--tmp-dir", "/content",   # download to the fast local disk, then move to Drive
]
try:
    bd.main(args)
except SystemExit as e:
    if e.code not in (0, None): print(e.code)
"""),
]

nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "bachata_colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}
(HERE / "bachata_colab.ipynb").write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
print("wrote bachata_colab.ipynb")
