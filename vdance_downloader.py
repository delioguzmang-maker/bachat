#!/usr/bin/env python3
"""
Download courses (all their lessons) from the dance-course app whose API you
captured in the HAR file (app + api.<...> + Bunny CDN "vdance-pull.b-cdn.net").

This site is organised as COURSES made of LESSONS, so files are saved as

    <out>/<Category>/<Course title> - <Artist>/<NN> - <Lesson title>.mp4

e.g.  Bachata/Advanced - Pablo & Raquel/01 - Hands game and chest isolation.mp4

How it works:
  1. Get the course catalog from the site's API (with your login token).
  2. For each lesson ask the API for its signed .m3u8 link.
  3. Pick the best quality (or the one you ask for). On this site video and
     audio are separate streams, so both are downloaded (in parallel, with
     retries) and then merged into one .mp4 with ffmpeg (no re-encoding).

Lessons already in the output folder are skipped, so re-running resumes.

The full catalog is ~400 hours of video (hundreds of GB), so choose what to
download:
  --list                     show all courses with their id, lessons and hours
  --course 12 --course 40    specific courses (id or app URL)
  --category Bachata         every course in a category
  --artist "Pablo & Raquel"  every course by an artist
  --my-courses               the courses you have started ("My courses")
  --all                      everything (huge!)

Authentication:
  --har  file.har            a HAR export while logged in (like the one you made)
  --token "12345|abcdef..."     the value after "Bearer " in the Authorization header
                             (then also pass --api https://api.<site> --app https://app.<site>)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) "
    "Gecko/20100101 Firefox/140.0"
)
# rough size per hour of video for each max height (from the playlists' bitrates)
GB_PER_HOUR = {1080: 1.9, 720: 0.8, 540: 0.45, 360: 0.27, 240: 0.17}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

@dataclass
class Auth:
    api: str             # e.g. https://api.3973.sms
    app: str             # e.g. https://app.sms   (sent as Origin/Referer)
    token: str           # Bearer token
    cookie: str = ""


def auth_from_har(path: str) -> Auth:
    """Use the most recent API request that carried an 'Authorization: Bearer' header."""
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)["log"]["entries"]
    for e in reversed(entries):
        req = e["request"]
        auth = _header(req, "authorization") or ""
        if auth.lower().startswith("bearer ") and "/api/" in req["url"]:
            u = urlparse(req["url"])
            origin = _header(req, "origin") or (_header(req, "referer") or "").rstrip("/")
            return Auth(api=f"{u.scheme}://{u.netloc}",
                        app=origin.rstrip("/"),
                        token=auth.split(" ", 1)[1].strip(),
                        cookie=_header(req, "cookie") or "")
    sys.exit("No logged-in API request (Authorization: Bearer ...) found in the HAR file.\n"
             "Log in, open the courses page, reload it with DevTools > Network open, "
             "then export the HAR again.")


def _header(req: dict, name: str) -> str | None:
    for h in req.get("headers", []):
        if h["name"].lower() == name:
            return h["value"]
    return None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@dataclass
class Lesson:
    id: int
    title: str
    order: int
    duration: int


@dataclass
class Course:
    id: int
    title: str
    artist: str
    category: str
    locale: str
    lessons: list[Lesson]

    @property
    def hours(self) -> float:
        return sum(l.duration or 0 for l in self.lessons) / 3600


def parse_course(c: dict) -> Course:
    lessons = [Lesson(l["id"], (l.get("title") or f"Lesson {l['id']}").strip(),
                      l.get("order") if l.get("order") is not None else i,
                      l.get("duration") or 0)
               for i, l in enumerate(c.get("lessons") or [])]
    lessons.sort(key=lambda l: (l.order, l.id))
    return Course(c["id"], (c.get("title") or f"Course {c['id']}").strip(),
                  ((c.get("artist") or {}).get("name") or "").strip(),
                  ((c.get("category") or {}).get("title") or "Other").strip(),
                  c.get("locale") or "", lessons)


class Api:
    def __init__(self, auth: Auth):
        self.auth = auth
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {auth.token}",
            "Origin": auth.app,
            "Referer": auth.app + "/",
        })
        if auth.cookie:
            self.s.headers["Cookie"] = auth.cookie

    def get(self, path: str):
        url = self.auth.api + path
        for attempt in range(5):
            try:
                r = self.s.get(url, timeout=60)
            except requests.RequestException as e:
                if attempt == 4:
                    raise
                print(f"   network error ({e}), retrying...")
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 419):
                sys.exit("The site says you are not logged in: your login token has expired.\n"
                         "Export a fresh HAR (or copy a new token) and try again.")
            if r.status_code >= 500 and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()

    def all_courses(self) -> list[Course]:
        return [parse_course(c) for c in self.get("/api/courses/get/999/1")]

    def my_course_ids(self) -> list[int]:
        return [c["id"] for c in self.get("/api/courses/get-my-courses")]

    def course(self, course_id: int) -> Course:
        return parse_course(self.get(f"/api/courses/get-course/{course_id}"))

    def lesson_m3u8(self, lesson_id: int) -> str | None:
        data = self.get(f"/api/video/get-data/{lesson_id}") or {}
        return data.get("hls") or data.get("dash")


# --------------------------------------------------------------------------
# HLS download (separate video + audio renditions)
# --------------------------------------------------------------------------

def with_query(url: str, query: str) -> str:
    """Bunny signed URLs: child playlists/segments need the master's ?token=... too."""
    if not query or urlparse(url).query:
        return url
    return f"{url}?{query}"


def _attr(line: str, name: str) -> str | None:
    m = re.search(rf'{name}=("([^"]*)"|[^,]*)', line)
    return (m.group(2) if m.group(2) is not None else m.group(1)) if m else None


def pick_renditions(master: str, master_url: str, max_height: int | None):
    """Return (video_playlist_url, audio_playlist_url or None, 'WxH@fps')."""
    query = urlparse(master_url).query
    audio_groups: dict[str, str] = {}
    variants = []
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:") and _attr(line, "TYPE") == "AUDIO" and _attr(line, "URI"):
            audio_groups.setdefault(_attr(line, "GROUP-ID"), _attr(line, "URI"))
        elif line.startswith("#EXT-X-STREAM-INF:"):
            res = _attr(line, "RESOLUTION") or "0x0"
            height = int(res.split("x")[1]) if "x" in res else 0
            bw = int(_attr(line, "BANDWIDTH") or 0)
            uri = next(l.strip() for l in lines[i + 1:] if l.strip() and not l.startswith("#"))
            fps = _attr(line, "FRAME-RATE")
            label = res + (f"@{float(fps):g}fps" if fps else "")
            variants.append((height, bw, uri, _attr(line, "AUDIO"), label))
    if not variants:  # already a media playlist
        return master_url, None, "?"
    ok = [v for v in variants if not max_height or v[0] <= max_height] or [min(variants)]
    height, bw, uri, group, label = max(ok, key=lambda v: (v[0], v[1]))
    video = with_query(urljoin(master_url, uri), query)
    audio = audio_groups.get(group) if group else None
    return video, with_query(urljoin(master_url, audio), query) if audio else None, label


def segment_urls(media: str, media_url: str) -> list[str]:
    if "#EXT-X-KEY" in media and "METHOD=NONE" not in media:
        raise RuntimeError("encrypted stream (EXT-X-KEY) - not supported")
    if "#EXT-X-MAP" in media:
        raise RuntimeError("fMP4 stream (EXT-X-MAP) - not supported")
    query = urlparse(media_url).query
    return [with_query(urljoin(media_url, l.strip()), query)
            for l in media.splitlines() if l.strip() and not l.startswith("#")]


def fetch(session: requests.Session, url: str, retries: int = 10) -> bytes:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 403:
                raise RuntimeError("403 Forbidden from the video CDN (link expired?)")
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise AssertionError("unreachable")


def download_track(s: requests.Session, segs: list[str], out_path: str, work: str,
                   tag: str, pool: ThreadPoolExecutor, progress) -> None:
    def one(i, url):
        part = os.path.join(work, f"{tag}{i:06d}.ts")
        if not (os.path.exists(part) and os.path.getsize(part) > 0):
            data = fetch(s, url)
            with open(part + ".tmp", "wb") as f:
                f.write(data)
            os.replace(part + ".tmp", part)
        progress()

    for fut in as_completed([pool.submit(one, i, u) for i, u in enumerate(segs)]):
        fut.result()
    with open(out_path, "wb") as out:
        for i in range(len(segs)):
            part = os.path.join(work, f"{tag}{i:06d}.ts")
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out)
            os.remove(part)


def download_lesson(m3u8_url: str, mp4_path: str, work: str, auth: Auth,
                    max_height: int | None, workers: int, ffmpeg: str) -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Origin": auth.app, "Referer": auth.app + "/"})
    video_url, audio_url, label = pick_renditions(fetch(s, m3u8_url).decode(), m3u8_url, max_height)
    vsegs = segment_urls(fetch(s, video_url).decode(), video_url)
    asegs = segment_urls(fetch(s, audio_url).decode(), audio_url) if audio_url else []
    total = len(vsegs) + len(asegs)
    print(f"   quality {label}, {total} segments")

    state = {"done": 0, "last": -1}

    def progress():
        state["done"] += 1
        pct = state["done"] * 100 // total
        if pct // 10 != state["last"] // 10:
            print(f"   {pct:3d}%", flush=True)
            state["last"] = pct

    v_ts, a_ts = os.path.join(work, "video.ts"), os.path.join(work, "audio.ts")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        download_track(s, vsegs, v_ts, work, "v", pool, progress)
        if asegs:
            download_track(s, asegs, a_ts, work, "a", pool, progress)

    print("   merging video + audio into mp4...")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", v_ts]
    if asegs:
        cmd += ["-i", a_ts, "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v?", "-map", "0:a?"]
    cmd += ["-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", mp4_path]
    subprocess.run(cmd, check=True)
    os.remove(v_ts)
    if asegs:
        os.remove(a_ts)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    name = name.replace("/", "-")
    name = re.sub(r'[\\:*?"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150] or "untitled"


def course_folder_names(catalog: list[Course]) -> dict[int, str]:
    """'<Title> - <Artist>'. Some courses exist in English and Spanish with the same
    name: those get ' [EN]' / ' [ES]', and the id if that's still not unique."""
    def dupes(names: dict[int, str]) -> set[str]:
        counts: dict[str, int] = {}
        for n in names.values():
            counts[n.lower()] = counts.get(n.lower(), 0) + 1
        return {n for n, k in counts.items() if k > 1}

    names = {c.id: safe_filename(f"{c.title} - {c.artist}" if c.artist else c.title)
             for c in catalog}
    locale = {c.id: c.locale for c in catalog}
    d = dupes(names)
    names = {cid: (f"{n} [{locale[cid].upper()}]" if n.lower() in d and locale[cid] else n)
             for cid, n in names.items()}
    d = dupes(names)
    return {cid: (f"{n} ({cid})" if n.lower() in d else n) for cid, n in names.items()}


def course_id_from(value: str) -> int:
    m = re.search(r"(\d+)\s*$", value.strip().rstrip("/")) or re.search(r"/(\d+)(?:/|$|\?)", value)
    if not m:
        raise argparse.ArgumentTypeError(f"can't find a course id in {value!r}")
    return int(m.group(1))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--har", help="HAR file exported while logged in")
    p.add_argument("--token", help='login token (the part after "Bearer ")')
    p.add_argument("--api", help="API base URL, e.g. https://api.3973.example (with --token)")
    p.add_argument("--app", help="app URL, e.g. https://app.example (with --token)")
    p.add_argument("--list", action="store_true", help="list courses and exit")
    p.add_argument("--course", action="append", default=[], type=course_id_from,
                   help="course id or URL (repeatable)")
    p.add_argument("--category", action="append", default=[], help="e.g. Bachata (repeatable)")
    p.add_argument("--artist", action="append", default=[], help='e.g. "Pablo & Raquel"')
    p.add_argument("--language", help="only courses in this language: en or es")
    p.add_argument("--my-courses", action="store_true", help='the courses in "My courses"')
    p.add_argument("--all", action="store_true", help="every course (hundreds of GB!)")
    p.add_argument("--out", default="courses", help="output folder (default: ./courses)")
    p.add_argument("--max-height", type=int,
                   help="max video height: 1080 (default, best), 720, 540, 360, 240")
    p.add_argument("--limit", type=int, help="download at most N lessons (handy for a test)")
    p.add_argument("--workers", type=int, default=8, help="parallel segment downloads")
    p.add_argument("--tmp-dir", help="scratch folder (default: system temp)")
    p.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    args = p.parse_args(argv)

    if args.har:
        auth = auth_from_har(args.har)
    elif args.token and args.api and args.app:
        auth = Auth(args.api.rstrip("/"), args.app.rstrip("/"), args.token.strip())
    else:
        p.error("give --har, or --token together with --api and --app")
    print(f"API: {auth.api}")
    api = Api(auth)

    catalog = api.all_courses()
    folders = course_folder_names(catalog)
    by_id = {c.id: c for c in catalog}

    # ---- select courses ----
    wanted: list[int] = list(args.course)
    if args.my_courses:
        wanted += api.my_course_ids()
    cats = {c.lower() for c in args.category}
    artists = {a.lower() for a in args.artist}
    for c in catalog:
        if args.all or c.category.lower() in cats or c.artist.lower() in artists:
            wanted.append(c.id)
    wanted = list(dict.fromkeys(wanted))  # dedupe, keep order
    if args.language:
        wanted = [cid for cid in wanted if cid not in by_id
                  or by_id[cid].locale.lower() == args.language.lower()]

    selected_something = bool(args.course or args.my_courses or cats or artists or args.all)
    if selected_something and not wanted:
        sys.exit("No courses match your selection (check the spelling, or run with --list).")

    if args.list or not wanted:
        shown = [by_id[cid] for cid in wanted if cid in by_id] if wanted else catalog
        print(f"\n{'ID':>4}  {'Category':<8} {'Lang':<4} {'Lessons':>7} {'Hours':>5}  Course - Artist")
        for c in sorted(shown, key=lambda c: (c.category, c.title, c.artist)):
            print(f"{c.id:>4}  {c.category:<8} {c.locale:<4} {len(c.lessons):>7} "
                  f"{c.hours:>5.1f}  {c.title} - {c.artist}")
        hours = sum(c.hours for c in shown)
        gb = hours * GB_PER_HOUR.get(args.max_height or 1080, 1.9)
        print(f"\n{len(shown)} courses, {sum(len(c.lessons) for c in shown)} lessons, "
              f"{hours:.0f} hours ≈ {gb:.0f} GB at {args.max_height or 1080}p")
        if not wanted and not args.list:
            print("\nNothing selected: pass --course ID, --category, --artist, "
                  "--my-courses or --all.")
        return 0

    if not shutil.which(args.ffmpeg):
        sys.exit("ffmpeg is needed to merge video and audio. Install it and retry.")

    # ---- download ----
    ok, skipped, failed = 0, 0, []
    budget = args.limit
    for cid in wanted:
        course = api.course(cid)
        cat = safe_filename(course.category or (by_id[cid].category if cid in by_id else "Other"))
        folder = os.path.join(args.out, cat, folders.get(cid) or safe_filename(
            f"{course.title} - {course.artist}"))
        print(f"\n=== {course.title} - {course.artist}  ({len(course.lessons)} lessons, "
              f"{course.hours:.1f} h)\n    -> {folder}")
        os.makedirs(folder, exist_ok=True)

        for n, lesson in enumerate(course.lessons, 1):
            name = f"{n:02d} - {safe_filename(lesson.title)}.mp4"
            target = os.path.join(folder, name)
            if os.path.exists(target) and os.path.getsize(target) > 0:
                print(f"[{n}/{len(course.lessons)}] already have: {name}")
                skipped += 1
                continue
            if budget is not None:
                if budget <= 0:
                    break
                budget -= 1
            print(f"[{n}/{len(course.lessons)}] {name}")
            try:
                m3u8 = api.lesson_m3u8(lesson.id)
                if not m3u8:
                    raise RuntimeError("the API returned no video link for this lesson")
                with tempfile.TemporaryDirectory(dir=args.tmp_dir, prefix="vdance-") as work:
                    tmp_mp4 = os.path.join(work, "final.mp4")
                    download_lesson(m3u8, tmp_mp4, work, auth, args.max_height,
                                    args.workers, args.ffmpeg)
                    print("   saving...")
                    shutil.move(tmp_mp4, target + ".part")
                    os.replace(target + ".part", target)
                print(f"   done ({os.path.getsize(target) / 1e6:.0f} MB)")
                ok += 1
            except SystemExit:
                raise
            except Exception as e:  # keep going with the next lesson
                print(f"   FAILED: {e}")
                failed.append(f"{course.title} - {course.artist} / {name}")
            time.sleep(1)
        if budget is not None and budget <= 0:
            break

    print(f"\nFinished: {ok} downloaded, {skipped} skipped, {len(failed)} failed.")
    for t in failed:
        print(f"  failed: {t}")
    if failed:
        print("Just run it again to retry the failed ones.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
