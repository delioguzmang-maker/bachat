#!/usr/bin/env python3
"""
Descarga las grabaciones de clases de Matterhorn / Opencast (DCE) a Google Drive
(o a tu computadora), con UNA CARPETA POR CURSO:

    Clases/GOVT E-1280 - Government and Politics of China/
        L01 - 2026-09-02 - Lecture - Pantalla.mp4       (computadora/diapositivas, 1920x1080)
        L01 - 2026-09-02 - Lecture - Presentador.mp4    (camara del profesor, 1280x720)
        L01 - 2026-09-02 - Lecture.txt / .vtt           (transcripcion)
        ...
        Indice de videos.csv

Como encuentra los videos (el JSON del sitio NO trae la URL de los mp4):
  * Cada clase publica miniaturas de la pantalla y del presentador. Cada miniatura
    tiene un campo "ref" que apunta al track de MAXIMA resolucion del que se saco
    (p. ej. track:e18059a6...), y su nombre de archivo es el del video
    (2548640506.segment_0-presenter-1500k-25fps_10.0p-search.jpg).
    En el CDN el video esta en  <cdn>/<id-clase>/<id-del-ref>/<nombre>.mp4
  * Tambien prueba /search/episode.json (el endpoint del reproductor) y, para la
    pantalla, el archivo segments.xml.
  * Cada URL se VERIFICA: el tamano del archivo en el CDN debe ser identico al del
    track de mayor resolucion del JSON, y despues de bajarlo se comprueba el MD5.

Lo ya descargado se salta, y las descargas cortadas se reanudan.

Ejemplos:
  python dce_downloader.py --course 20270117541 --out Clases
  python dce_downloader.py --course "GOVT E-1280" --term 202701 --list   # solo mostrar
  python dce_downloader.py --list-courses --term 202701                  # ver cursos del termino
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

DEFAULT_HOST = "https://matterhorn.dce.sms.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
VIEW_NAMES = {"presentation": "Pantalla", "presenter": "Presentador"}


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def clean(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(name))
    return re.sub(r"\s+", " ", name).strip(" .-")[:150] or "sin nombre"


def mb(n) -> str:
    return f"{n / 1e6:,.0f} MB" if n else "?"


def resolution(track: dict) -> str:
    v = track.get("video") or {}
    if isinstance(v, list):
        v = v[0] if v else {}
    return str(v.get("resolution") or "?")


def pixels(track: dict) -> int:
    m = re.match(r"(\d+)x(\d+)", resolution(track))
    return int(m.group(1)) * int(m.group(2)) if m else 0


# --------------------------------------------------------------------------
# Site access
# --------------------------------------------------------------------------

class Site:
    def __init__(self, host: str, cookie: str = ""):
        self.host = host.rstrip("/")
        self.cookie = cookie.strip()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Referer": self.host + "/"})

    def get(self, url: str, site: bool = True, **kw) -> requests.Response:
        """GET with retries. `site=True` sends the (optional) cookie."""
        if url.startswith("/"):
            url = self.host + url
        headers = dict(kw.pop("headers", {}) or {})
        if site and self.cookie:
            headers["Cookie"] = self.cookie
        kw.setdefault("timeout", 60)
        for attempt in range(5):
            try:
                r = self.s.get(url, headers=headers, **kw)
                if r.status_code < 500:
                    return r
            except requests.RequestException:
                if attempt == 4:
                    raise
            time.sleep(2 ** attempt)
        return r

    # ---- courses ----
    def current_term(self) -> str:
        try:
            t = self.get("/otherpubs/currenttermfilter").text.strip()
            return t if re.fullmatch(r"\d{6}", t) else ""
        except requests.RequestException:
            return ""

    def term_courses(self, term: str) -> list[dict]:
        r = self.get(f"/series/allInYearTerm.json?action=get&includeDeleted=false&yearTerm={term}")
        r.raise_for_status()
        out = []
        for item in r.json():
            dc = item.get("http://purl.org/dc/terms/", {})
            val = lambda k: (dc.get(k) or [{}])[0].get("value", "")
            out.append({"id": val("identifier"), "code": val("subject"),
                        "title": val("title"), "instructor": val("creator")})
        return out

    def resolve_course(self, value: str, term: str) -> str:
        """Series id (20270117541), player URL (...#/2027/01/17541) or code ('GOVT E-1280')."""
        v = str(value).strip()
        if re.fullmatch(r"\d{11}", v):
            return v
        m = re.search(r"(\d{4})/(\d{2})/(\d{5})", v)
        if m:
            return "".join(m.groups())
        term = term or self.current_term()
        if not term:
            sys.exit(f"Para buscar '{v}' por codigo necesito el termino (ej. --term 202701).")
        wanted = re.sub(r"\s+", "", v).upper()
        for c in self.term_courses(term):
            if re.sub(r"\s+", "", c["code"]).upper() == wanted:
                return c["id"]
        sys.exit(f"No encontre el curso '{v}' en el termino {term}. "
                 f"Usa --list-courses --term {term} para ver los codigos.")

    def series(self, sid: str) -> tuple[dict, list[dict]]:
        url = (f"/search/series.json?action=get&admin=false&episodes=true&series=true"
               f"&id={sid}&limit=600&offset=0&removeOcMpXml=true"
               f"&reFreshData={int(time.time() * 1000)}")
        r = self.get(url)
        r.raise_for_status()
        results = as_list(r.json().get("search-results", {}).get("result"))
        serie = next((x for x in results if x.get("mediaType") == "Series"), {})
        episodes = [x for x in results if "mediapackage" in x]
        episodes.sort(key=lambda x: x.get("dcCreated") or x["mediapackage"].get("start", ""))
        return serie, episodes

    def episode_urls(self, mpid: str) -> dict[str, str]:
        """track id -> url from the player's endpoint, if the site gives them out."""
        try:
            r = self.get(f"/search/episode.json?id={mpid}", allow_redirects=False)
            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                return {}
            urls = {}
            for res in as_list(r.json().get("search-results", {}).get("result")):
                media = (res.get("mediapackage") or {}).get("media") or {}
                for t in as_list(media.get("track")):
                    if t.get("url") and t.get("id"):
                        urls[t["id"]] = t["url"]
            return urls
        except (requests.RequestException, ValueError):
            return {}

    def remote_size(self, url: str) -> int | None:
        """Size of a remote file (asks for 1 byte), None if it doesn't exist."""
        try:
            with self.s.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=30) as r:
                if r.status_code == 206:
                    return int(r.headers["Content-Range"].split("/")[-1])
                if r.status_code == 200:
                    return int(r.headers.get("Content-Length") or 0) or None
        except (requests.RequestException, ValueError, KeyError):
            pass
        return None


# --------------------------------------------------------------------------
# Finding the highest-resolution mp4 of each view
# --------------------------------------------------------------------------

PREVIEW_SUFFIX = re.compile(r"(_[\d.]+p-[a-z]+)?\.(jpg|jpeg|png)$", re.I)


def preview_candidates(mp: dict, view: str) -> list[str]:
    """<cdn>/<mpid>/<ref track id>/<preview name without suffix>.mp4 for each preview image."""
    mpid, out = mp["id"], []
    for a in as_list((mp.get("attachments") or {}).get("attachment")):
        if not a.get("type", "").startswith(view + "/") or not a.get("url"):
            continue
        ref = (a.get("ref") or "").split(";")[0]
        if not ref.startswith("track:") or f"/{mpid}/" not in a["url"]:
            continue
        base = a["url"].split(f"/{mpid}/")[0] + f"/{mpid}/"
        name = PREVIEW_SUFFIX.sub("", a["url"].rsplit("/", 1)[-1]) + ".mp4"
        url = f"{base}{ref[len('track:'):]}/{name}"
        if url not in out:
            out.append(url)
    return out


def segments_candidates(site: Site, mp: dict) -> list[str]:
    """The slide-detection catalog (segments.xml) names the presentation mp4."""
    mpid, out = mp["id"], []
    for c in as_list((mp.get("metadata") or {}).get("catalog")):
        if c.get("type") != "mpeg-7/segments" or not c.get("url"):
            continue
        try:
            xml = site.get(c["url"], site=False, timeout=30).text
        except requests.RequestException:
            continue
        base = c["url"].split(f"/{mpid}/")[0] + f"/{mpid}/"
        for uri in re.findall(r"<MediaUri>\s*(.*?)\s*</MediaUri>", xml):
            m = re.search(re.escape(mpid) + r"/([^/]+/[^/?]+\.mp4)", uri)
            if m and base + m.group(1) not in out:
                out.append(base + m.group(1))
    return out


@dataclass
class Found:
    track: dict | None
    url: str | None = None
    size: int | None = None
    note: str = ""


def find_video(site: Site, ep: dict, view: str, cache: dict) -> Found:
    mp = ep["mediapackage"]
    tracks = [t for t in as_list((mp.get("media") or {}).get("track"))
              if t.get("type") == f"{view}/delivery" and t.get("mimetype") == "video/mp4"]
    if not tracks:
        return Found(None, note="esta clase no tiene este video")
    tracks.sort(key=lambda t: (pixels(t), t.get("size") or 0), reverse=True)
    best, by_size = tracks[0], {t.get("size"): t for t in tracks if t.get("size")}

    # 1) URL given directly by the site (track field or player endpoint)
    if "urls" not in cache:
        cache["urls"] = site.episode_urls(mp["id"])
    for t in tracks:
        url = t.get("url") or cache["urls"].get(t.get("id"))
        if url:
            return Found(t, url, site.remote_size(url) or t.get("size"), "URL del sitio")

    # 2) derived from preview images, 3) segments.xml (presentation only)
    candidates = preview_candidates(mp, view)
    if view == "presentation":
        if "segments" not in cache:
            cache["segments"] = segments_candidates(site, mp)
        candidates += [u for u in cache["segments"] if u not in candidates]

    fallback = None
    for url in candidates:
        size = site.remote_size(url)
        if size is None:
            continue
        t = by_size.get(size)
        if t is best:
            return Found(best, url, size, "verificado: maxima resolucion")
        if t is not None and fallback is None:
            fallback = Found(t, url, size, f"AVISO: solo se encontro {resolution(t)} "
                                            f"(la maxima es {resolution(best)})")
        elif t is None and fallback is None:
            fallback = Found(None, url, size, "AVISO: el tamano no coincide con ningun track")
    if fallback:
        return fallback
    return Found(best, None, best.get("size"),
                 "NO ENCONTRADO (ver instrucciones: prueba con COOKIE)")


# --------------------------------------------------------------------------
# Download / verify
# --------------------------------------------------------------------------

def download(site: Site, url: str, dest: str, expected: int | None) -> None:
    part = dest + ".part"
    for attempt in range(1, 11):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if expected and have >= expected:
            break
        try:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with site.s.get(url, headers=headers, stream=True, timeout=60) as r:
                if have and r.status_code != 206:
                    have = 0
                r.raise_for_status()
                done, mark = have, (have * 10 // expected if expected else 0)
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(4 * 1024 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if expected and done * 10 // expected > mark:
                            mark = done * 10 // expected
                            print(f"      {mark * 10:3d}%  ({mb(done)} de {mb(expected)})", flush=True)
            if not expected or os.path.getsize(part) >= expected:
                break
        except requests.RequestException as e:
            print(f"      intento {attempt} fallo ({e}); reintentando...")
            time.sleep(min(5 * attempt, 60))
    else:
        raise RuntimeError("no se pudo completar la descarga")
    os.replace(part, dest)


def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy_to(local: str, final: str) -> None:
    tmp = final + ".part"
    shutil.copyfile(local, tmp)
    os.replace(tmp, final)


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------

def vtt_to_text(vtt: str) -> str:
    lines, prev = [], None
    for l in vtt.splitlines():
        l = l.strip()
        if (not l or l == "WEBVTT" or "-->" in l or l.isdigit()
                or l.startswith(("NOTE", "Kind:", "Language:"))):
            continue
        l = re.sub(r"<[^>]+>", "", l)
        if l != prev:
            lines.append(l)
            prev = l
    return " ".join(lines)


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


_whisper = {}


def whisper_transcribe(media: str, language: str | None, model_name: str) -> dict:
    if "model" not in _whisper:
        try:
            import whisper  # noqa: F401
        except ImportError:
            print("    instalando Whisper (solo la primera vez)...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "openai-whisper"],
                           check=True)
        import torch
        import whisper
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"    cargando Whisper '{model_name}' en {device}"
              + ("" if device == "cuda" else " (sin GPU es lento: activa GPU T4)") + "...")
        _whisper["model"] = whisper.load_model(model_name, device=device)
        _whisper["fp16"] = device == "cuda"
    lang = (language or "")[:2] or None
    return _whisper["model"].transcribe(media, language=lang, fp16=_whisper["fp16"])


def write_whisper(result: dict, base_path: str) -> None:
    with open(base_path + ".txt", "w", encoding="utf-8") as f:
        f.write(result.get("text", "").strip() + "\n")
    with open(base_path + ".vtt", "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in result.get("segments", []):
            f.write(f"{_ts(seg['start'])} --> {_ts(seg['end'])}\n{seg['text'].strip()}\n\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

@dataclass
class Totals:
    ok: int = 0
    skipped: int = 0
    failed: list = field(default_factory=list)


def process_course(site: Site, sid: str, args, totals: Totals) -> None:
    serie, episodes = site.series(sid)
    code = serie.get("dcSubject", "")
    title = serie.get("dcTitle") or (episodes[0]["mediapackage"].get("seriestitle")
                                     if episodes else sid)
    folder = os.path.join(args.out, clean(f"{code} - {title}" if code else title))
    views = [v for v in ("presentation", "presenter") if v in args.views]

    print("\n" + "=" * 72)
    print(f"CURSO: {code} {title}   (serie {sid})")
    print(f"Carpeta: {folder}")
    print(f"Clases publicadas: {len(episodes)}")
    print("=" * 72)
    if not args.list:
        os.makedirs(folder, exist_ok=True)

    index, used, total_bytes = [], set(), 0
    budget = args.limit
    for n, ep in enumerate(episodes, 1):
        mp = ep["mediapackage"]
        number = ep.get("dcType") or mp.get("type") or f"C{n:02d}"
        date = (ep.get("dcCreated") or mp.get("start") or "")[:10]
        base = clean(f"{number} - {date} - {ep.get('dcTitle') or mp.get('title') or 'Clase'}")
        if base.lower() in used:
            base += f" - {mp['id'][:8]}"
        used.add(base.lower())
        print(f"\n  {base}   ({int(mp.get('duration') or 0) // 60000} min)")

        if budget is not None and budget <= 0:
            print("    (limite alcanzado, se salta)")
            continue

        cache, local_media, drive_media = {}, None, None
        for view in views:
            name = f"{base} - {VIEW_NAMES[view]}.mp4"
            final = os.path.join(folder, name)
            found = find_video(site, ep, view, cache)
            res = resolution(found.track) if found.track else "-"
            print(f"    {VIEW_NAMES[view]:<12} {res:<10} {mb(found.size):>9}  {found.note}")
            row = {"Clase": number, "Fecha": date, "Video": VIEW_NAMES[view], "Resolucion": res,
                   "Tamano": mb(found.size), "Archivo": name if found.url else "",
                   "Estado": found.note, "URL": found.url or ""}
            index.append(row)
            if not found.url:
                if found.track:
                    totals.failed.append(f"{code} {name}: {found.note}")
                continue
            total_bytes += found.size or 0
            if args.list:
                print(f"      {found.url}")
                continue

            if os.path.exists(final) and (os.path.getsize(final) == found.size
                                          or (not found.size and os.path.getsize(final) > 0)):
                print("      ya esta en la carpeta, se salta")
                row["Estado"] = "ya descargado"
                totals.skipped += 1
                drive_media = drive_media or final
                continue

            local = os.path.join(args.tmp_dir, name)
            try:
                download(site, found.url, local, found.size)
                md5 = ((found.track or {}).get("checksum") or {}).get("$")
                if md5 and found.track and found.track.get("size") == found.size:
                    if md5_of(local) != md5:
                        os.remove(local)
                        raise RuntimeError("el MD5 no coincide (archivo danado); se borro")
                    print("      MD5 correcto")
                print("      guardando en la carpeta...")
                copy_to(local, final)
                print(f"      listo: {name}")
                row["Estado"] = "descargado"
                totals.ok += 1
            except Exception as e:  # keep going with the rest
                print(f"      FALLO: {e}")
                row["Estado"] = f"fallo: {e}"
                totals.failed.append(f"{code} {name}: {e}")
                if os.path.exists(local):
                    os.remove(local)
                continue
            if local_media is None:
                local_media = local        # keep one local copy for the transcript
            else:
                os.remove(local)

        if budget is not None:
            budget -= 1

        if not args.list and args.transcribe != "no":
            try:
                transcribe_episode(site, ep, folder, base, local_media or drive_media,
                                   cache, args)
            except Exception as e:
                print(f"    transcripcion FALLO: {e}")
                totals.failed.append(f"{code} {base} (transcripcion): {e}")
        if local_media and os.path.exists(local_media):
            os.remove(local_media)

    if args.list:
        print(f"\n  Total a descargar en este curso: {total_bytes / 1e9:.1f} GB")
        return
    path = os.path.join(folder, "Indice de videos.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(index[0].keys()) if index else ["Clase"])
        w.writeheader()
        w.writerows(index)
    print(f"\n  Indice guardado: {path}")


def transcribe_episode(site: Site, ep: dict, folder: str, base: str, media: str | None,
                       cache: dict, args) -> None:
    out_base = os.path.join(folder, base)
    if os.path.exists(out_base + ".txt"):
        return
    mp = ep["mediapackage"]
    # 1) captions published by the site (only if it gives out their URL)
    for t in as_list((mp.get("media") or {}).get("track")):
        if t.get("type", "").startswith("captions/"):
            url = t.get("url") or cache.get("urls", {}).get(t.get("id"))
            if url:
                vtt = site.get(url, site=False).text
                with open(out_base + ".vtt", "w", encoding="utf-8") as f:
                    f.write(vtt)
                with open(out_base + ".txt", "w", encoding="utf-8") as f:
                    f.write(vtt_to_text(vtt) + "\n")
                print("    subtitulos del sitio guardados (.vtt + .txt)")
                return
    if args.transcribe != "auto":
        return
    if not media:
        print("    (sin video descargado para transcribir)")
        return
    print("    transcribiendo con Whisper...")
    result = whisper_transcribe(media, mp.get("language"), args.whisper_model)
    write_whisper(result, out_base)
    print(f"    transcripcion guardada: {base}.txt / .vtt")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", action="append", default=[],
                   help="ID de la serie (20270117541), URL del curso o codigo ('GOVT E-1280')")
    p.add_argument("--term", default="", help="termino para codigos, ej. 202701 (defecto: actual)")
    p.add_argument("--list-courses", action="store_true", help="ver los cursos del termino")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"defecto: {DEFAULT_HOST}")
    p.add_argument("--cookie", default="", help="cookie de sesion (opcional)")
    p.add_argument("--views", default="presentation,presenter",
                   help="presentation (pantalla), presenter (presentador) o ambos")
    p.add_argument("--out", default="Clases", help="carpeta de destino")
    p.add_argument("--transcribe", choices=["auto", "site", "no"], default="auto",
                   help="auto = subtitulos del sitio o Whisper; site = solo del sitio; no")
    p.add_argument("--whisper-model", default="base", help="tiny, base, small, medium...")
    p.add_argument("--list", action="store_true", help="solo mostrar (no descarga)")
    p.add_argument("--limit", type=int, help="procesar como maximo N clases por curso")
    p.add_argument("--tmp-dir", default=None, help="carpeta temporal (defecto: del sistema)")
    args = p.parse_args(argv)
    args.views = [v.strip() for v in args.views.split(",") if v.strip()]
    for v in args.views:
        if v not in VIEW_NAMES:
            p.error(f"vista desconocida: {v}")

    site = Site(args.host, args.cookie)
    if args.list_courses:
        term = args.term or site.current_term()
        print(f"Cursos del termino {term}:")
        for c in sorted(site.term_courses(term), key=lambda c: c["code"]):
            print(f"  {c['id']}  {c['code']:<14} {c['title']}  ({c['instructor']})")
        return 0
    if not args.course:
        p.error("indica al menos un --course (o usa --list-courses)")

    own_tmp = None
    if not args.list:
        if args.tmp_dir:
            os.makedirs(args.tmp_dir, exist_ok=True)
        else:
            own_tmp = args.tmp_dir = tempfile.mkdtemp(prefix="dce-")

    totals = Totals()
    try:
        for c in args.course:
            process_course(site, site.resolve_course(c, args.term), args, totals)
    finally:
        if own_tmp:
            shutil.rmtree(own_tmp, ignore_errors=True)

    if args.list:
        print("\n--- MODO SOLO MOSTRAR: no se descargo nada ---")
        return 0
    print(f"\n--- TERMINADO: {totals.ok} videos descargados, {totals.skipped} ya estaban, "
          f"{len(totals.failed)} problemas ---")
    for f in totals.failed:
        print(f"  * {f}")
    if totals.failed:
        print("Vuelve a ejecutar para reintentar (lo ya descargado se salta).")
    return 1 if totals.failed else 0


if __name__ == "__main__":
    sys.exit(main())
