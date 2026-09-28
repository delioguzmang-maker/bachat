#!/usr/bin/env python3
"""
Download every video of a Uscreen category (e.g. "Partner Work") with the
video's real title as the file name.

How it works (same steps you did by hand, just automated):
  1. List all videos in the category (all pages).
  2. For each video, open its "program_content" page, which contains the
     signed  https://stream.mux.com/....m3u8?token=...  link.
  3. Pick the best quality (or the one you ask for) from the m3u8 playlist.
  4. Download all .ts segments in parallel (with retries), join them and
     remux to .mp4 with ffmpeg (no re-encoding, same as `-c copy`).

Videos that already exist in the output folder are skipped, so you can simply
re-run the script to resume or to fetch new classes.

Authentication: you need your logged-in browser cookies. Either
  --har  path/to/file.har      (a HAR export while logged in, like the one you made)
  --cookie "name=value; ..."   (the Cookie request header copied from DevTools)

Examples:
  python bachata_downloader.py --har site.har --out "/content/drive/MyDrive/Bachata"
  python bachata_downloader.py --har site.har --list            # only show titles
  python bachata_downloader.py --har site.har --max-height 720  # smaller files
"""

from __future__ import annotations

import argparse
import html
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
USCREEN_API = "https://api.uscreencdn.com"


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

@dataclass
class Auth:
    base_url: str        # e.g. https://sms.com
    cookie: str          # raw Cookie header value
    store: str           # value for the x-fastly-origin header (e.g. "sms")


def auth_from_har(path: str, base_url: str | None = None) -> Auth:
    """Take the most recent Cookie header sent to the site from a HAR file."""
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    entries = har["log"]["entries"]

    if not base_url:
        # The site is the host that received the login cookie.
        for e in reversed(entries):
            cookie = _header(e["request"], "cookie") or ""
            if "remember_user_token" in cookie or "_uscreen2_session" in cookie:
                u = urlparse(e["request"]["url"])
                base_url = f"{u.scheme}://{u.netloc}"
                break
    if not base_url:
        sys.exit("Could not find a logged-in request in the HAR file. "
                 "Make sure you were logged in when you exported it.")
    host = urlparse(base_url).netloc

    cookie, store = None, None
    for e in entries:
        req = e["request"]
        if urlparse(req["url"]).netloc == host:
            cookie = _header(req, "cookie") or cookie
        store = _header(req, "x-fastly-origin") or store
    if not cookie:
        sys.exit(f"No cookies for {host} found in the HAR file.")
    return Auth(base_url.rstrip("/"), cookie, store or host.split(".")[0])


def _header(req: dict, name: str) -> str | None:
    for h in req.get("headers", []):
        if h["name"].lower() == name:
            return h["value"]
    return None


# --------------------------------------------------------------------------
# Site scraping
# --------------------------------------------------------------------------

@dataclass
class Video:
    title: str
    path: str            # "/programs/<slug>?category_id=..."
    kind: str = "video"  # "video" or "collection"
    published_at: str = ""


CARD_RE = re.compile(r'<swiper-slide\b(?P<attrs>[^>]*)>(?P<body>.*?)</swiper-slide>', re.S)
TITLE_LINK_RE = re.compile(r'<a\b[^>]*class="card-title"[^>]*>', re.S)
ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')
NEXT_PAGE_RE = re.compile(r'src="[^"]*[?&;]page=(\d+)[^"]*"')
SOURCE_RE = re.compile(r'<source[^>]+src="([^"]+\.m3u8[^"]*)"')
H1_RE = re.compile(r'<h1[^>]*program-title[^>]*>(.*?)</h1>', re.S)


def _attrs(tag: str) -> dict:
    return {k: html.unescape(v) for k, v in ATTR_RE.findall(tag)}


def parse_listing(page_html: str) -> tuple[list[Video], int | None]:
    """Return the videos on one listing page and the next page number (if any)."""
    videos = []
    for m in CARD_RE.finditer(page_html):
        card = _attrs(m.group("attrs"))
        link = TITLE_LINK_RE.search(m.group("body"))
        if not link:
            continue
        a = _attrs(link.group(0))
        if not a.get("href", "").startswith("/programs/"):
            continue
        kind = card.get("data-catalog--card-analytics-content-type-value", "video")
        videos.append(Video(a.get("title", "").strip(), a["href"], kind,
                            card.get("data-published-at", "")))
    nxt = NEXT_PAGE_RE.search(page_html)
    return videos, int(nxt.group(1)) if nxt else None


def parse_program_content(page_html: str) -> tuple[str | None, str | None]:
    """Return (title, m3u8 url) from a program_content page."""
    src = SOURCE_RE.search(page_html)
    h1 = H1_RE.search(page_html)
    title = html.unescape(re.sub(r"\s+", " ", h1.group(1))).strip() if h1 else None
    return title, html.unescape(src.group(1)) if src else None


class Site:
    def __init__(self, auth: Auth):
        self.auth = auth
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html, application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def _get(self, url: str, headers: dict | None = None, cookies=True) -> requests.Response:
        h = dict(headers or {})
        if cookies:
            h["Cookie"] = self.auth.cookie
        for attempt in range(4):
            try:
                r = self.s.get(url, headers=h, timeout=60)
                if r.status_code < 500:
                    return r
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                print(f"   network error ({e}), retrying...")
            time.sleep(2 ** attempt)
        return r

    def _listing_page(self, category: str, page: int) -> str:
        q = (f"action={'show' if page == 1 else 'search'}"
             f"&controller=storefront%2Fcategories&format=turbo_stream&id={category}")
        if page > 1:
            q += f"&page={page}"
        path = f"/categories/{category}/search?{q}"
        turbo = {"Turbo-Frame": "category_content",
                 "Accept": "text/vnd.turbo-stream.html, text/html, application/xhtml+xml"}
        # 1) same host as the site (sends your cookies)
        r = self._get(self.auth.base_url + path, turbo)
        if r.ok and "<swiper-slide" in r.text:
            return r.text
        # 2) the Uscreen API host the browser actually uses
        r = self._get(USCREEN_API + path,
                      {**turbo, "X-Fastly-Origin": self.auth.store,
                       "Origin": self.auth.base_url, "Referer": self.auth.base_url + "/"},
                      cookies=False)
        r.raise_for_status()
        return r.text

    def list_category(self, category: str) -> list[Video]:
        videos: list[Video] = []
        seen = set()
        page = 1
        while page:
            new, next_page = parse_listing(self._listing_page(category, page))
            new = [v for v in new if v.path not in seen]
            if not new:
                break
            for v in new:
                seen.add(v.path)
            videos += new
            print(f"   page {page}: {len(new)} videos")
            page = next_page if next_page and next_page > page else None
        return videos

    def stream_url(self, video: Video) -> tuple[str | None, str | None]:
        path, _, query = video.path.partition("?")
        extra = "playlist_position=sidebar&preview=false"
        url = f"{self.auth.base_url}{path}/program_content?{query + '&' if query else ''}{extra}"
        r = self._get(url, {"Turbo-Frame": "program_content",
                            "Referer": self.auth.base_url + video.path})
        if r.status_code in (401, 403) or "/sign_in" in r.url:
            sys.exit("The site says you are not logged in: your cookies have expired. "
                     "Export a fresh HAR / Cookie header and try again.")
        r.raise_for_status()
        return parse_program_content(r.text)


# --------------------------------------------------------------------------
# HLS download
# --------------------------------------------------------------------------

def pick_variant(master: str, master_url: str, max_height: int | None) -> tuple[str, str]:
    """Choose the best rendition not taller than max_height. Returns (url, resolution)."""
    variants = []
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            bw = int(re.search(r"BANDWIDTH=(\d+)", line).group(1))
            res = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
            height = int(res.group(2)) if res else 0
            uri = next(l for l in lines[i + 1:] if l.strip() and not l.startswith("#"))
            variants.append((height, bw, urljoin(master_url, uri.strip()),
                             res.group(0).split("=")[1] if res else "?"))
    if not variants:  # already a media playlist
        return master_url, "?"
    ok = [v for v in variants if not max_height or v[0] <= max_height] or \
        [min(variants)]  # nothing small enough: take the smallest
    best = max(ok, key=lambda v: (v[0], v[1]))
    return best[2], best[3]


def segment_urls(media: str, media_url: str) -> list[str]:
    if "#EXT-X-KEY" in media and "METHOD=NONE" not in media:
        raise RuntimeError("encrypted stream (EXT-X-KEY) - not supported")
    return [urljoin(media_url, l.strip()) for l in media.splitlines()
            if l.strip() and not l.startswith("#")]


def fetch(session: requests.Session, url: str, retries: int = 10) -> bytes:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise AssertionError("unreachable")


def download_hls(m3u8_url: str, out_ts: str, work_dir: str, max_height: int | None,
                 workers: int) -> None:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    master = fetch(s, m3u8_url).decode()
    media_url, res = pick_variant(master, m3u8_url, max_height)
    segs = segment_urls(fetch(s, media_url).decode(), media_url)
    print(f"   quality {res}, {len(segs)} segments")

    def one(i_url):
        i, url = i_url
        part = os.path.join(work_dir, f"{i:06d}.ts")
        if os.path.exists(part) and os.path.getsize(part) > 0:
            return
        data = fetch(s, url)
        with open(part + ".tmp", "wb") as f:
            f.write(data)
        os.replace(part + ".tmp", part)

    done, last_pct = 0, -1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, x) for x in enumerate(segs)]):
            fut.result()
            done += 1
            pct = done * 100 // len(segs)
            if pct // 10 != last_pct // 10:
                print(f"   {pct:3d}%", flush=True)
                last_pct = pct

    with open(out_ts, "wb") as out:
        for i in range(len(segs)):
            part = os.path.join(work_dir, f"{i:06d}.ts")
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out)
            os.remove(part)


def remux(ts_path: str, mp4_path: str, ffmpeg: str) -> None:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", ts_path,
           "-map", "0:v?", "-map", "0:a?", "-c", "copy",
           "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", mp4_path]
    subprocess.run(cmd, check=True)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    name = name.replace("/", "-")                      # 27/08/2026 -> 27-08-2026
    name = re.sub(r'[\\:*?"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or "video"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    auth = p.add_mutually_exclusive_group(required=True)
    auth.add_argument("--har", help="HAR file exported while logged in")
    auth.add_argument("--cookie", help='Cookie header value copied from the browser')
    p.add_argument("--site", help="site URL, e.g. https://sms.com (auto-detected from --har)")
    p.add_argument("--category", action="append",
                   help="category slug from the URL /categories/<slug> "
                        "(repeatable, default: partnerwork)")
    p.add_argument("--program", action="append", default=[],
                   help="download only this program URL or /programs/... path (repeatable)")
    p.add_argument("--out", default="videos", help="output folder (default: ./videos)")
    p.add_argument("--max-height", type=int,
                   help="max video height, e.g. 720 or 480 (default: best = 1080)")
    p.add_argument("--strip-prefix", default="",
                   help='remove this text from the start of titles, e.g. "Marco & Sara - "')
    p.add_argument("--list", action="store_true", help="only list the videos, don't download")
    p.add_argument("--limit", type=int, help="download at most N videos (handy for a test)")
    p.add_argument("--workers", type=int, default=8, help="parallel segment downloads")
    p.add_argument("--tmp-dir", help="scratch folder (default: system temp)")
    p.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    args = p.parse_args(argv)

    if args.har:
        a = auth_from_har(args.har, args.site)
    else:
        if not args.site:
            p.error("--site is required with --cookie")
        host = urlparse(args.site).netloc
        a = Auth(args.site.rstrip("/"), args.cookie.strip(), host.split(".")[0])
    print(f"Site: {a.base_url}")
    site = Site(a)

    # ---- collect videos ----
    videos: list[Video] = []
    if args.program:
        for prog in args.program:
            u = urlparse(prog)
            videos.append(Video("", u.path + (f"?{u.query}" if u.query else "")))
    else:
        for cat in args.category or ["partnerwork"]:
            print(f"Listing category '{cat}'...")
            try:
                videos += site.list_category(cat)
            except requests.RequestException as e:
                sys.exit(f"Could not load the category listing ({e}).\n"
                         f"Check the category name and that your cookies are fresh.")
    print(f"Found {len(videos)} items.\n")

    if args.list:
        for v in videos:
            flag = "" if v.kind == "video" else f"   [{v.kind} - not downloaded]"
            print(f" - {v.title}{flag}")
        return 0

    have_ffmpeg = shutil.which(args.ffmpeg) is not None
    if not have_ffmpeg:
        print("WARNING: ffmpeg not found - videos will be saved as .ts (plays fine in VLC).\n")
    ext = ".mp4" if have_ffmpeg else ".ts"

    os.makedirs(args.out, exist_ok=True)
    used_names: set[str] = set()
    ok, skipped, failed = 0, 0, []
    todo = videos[: args.limit] if args.limit else videos

    for n, v in enumerate(todo, 1):
        if v.kind != "video":
            print(f"[{n}/{len(todo)}] SKIP {v.title!r}: it's a {v.kind} "
                  f"(pass its individual videos with --program)")
            skipped += 1
            continue

        # Name from listing title if we have it (no request needed to skip existing files)
        title = v.title
        target = None
        if title:
            name = _unique(safe_filename(_strip(title, args.strip_prefix)), used_names)
            target = os.path.join(args.out, name + ext)
            if os.path.exists(target) and os.path.getsize(target) > 0:
                print(f"[{n}/{len(todo)}] already have: {name}{ext}")
                skipped += 1
                continue

        try:
            page_title, m3u8 = site.stream_url(v)
            if not m3u8:
                raise RuntimeError("no video link on the page (cookies expired, "
                                   "or not part of your subscription?)")
            if not target:
                title = page_title or v.path.split("/")[-1].split("?")[0]
                name = _unique(safe_filename(_strip(title, args.strip_prefix)), used_names)
                target = os.path.join(args.out, name + ext)
                if os.path.exists(target) and os.path.getsize(target) > 0:
                    print(f"[{n}/{len(todo)}] already have: {name}{ext}")
                    skipped += 1
                    continue
            print(f"[{n}/{len(todo)}] {name}{ext}")

            with tempfile.TemporaryDirectory(dir=args.tmp_dir, prefix="bachata-") as work:
                ts = os.path.join(work, "video.ts")
                download_hls(m3u8, ts, work, args.max_height, args.workers)
                final_tmp = os.path.join(work, "final" + ext)
                if have_ffmpeg:
                    print("   converting to mp4...")
                    remux(ts, final_tmp, args.ffmpeg)
                    os.remove(ts)
                else:
                    os.replace(ts, final_tmp)
                print("   saving...")
                shutil.move(final_tmp, target + ".part")
                os.replace(target + ".part", target)
            size = os.path.getsize(target) / 1e6
            print(f"   done ({size:.0f} MB)\n")
            ok += 1
        except SystemExit:
            raise
        except Exception as e:  # keep going with the next video
            print(f"   FAILED: {e}\n")
            failed.append(title or v.path)
            if ok == 0 and len(failed) >= 3 and "no video link" in str(e):
                sys.exit("The first videos all had no video link: your cookies have "
                         "probably expired. Export a fresh HAR / Cookie header and retry.")
        time.sleep(1)

    print(f"\nFinished: {ok} downloaded, {skipped} skipped, {len(failed)} failed.")
    for t in failed:
        print(f"  failed: {t}")
    if failed:
        print("Just run the script again to retry the failed ones.")
    return 1 if failed else 0


def _strip(title: str, prefix: str) -> str:
    return title[len(prefix):] if prefix and title.startswith(prefix) else title


def _unique(name: str, used: set[str]) -> str:
    base, i = name, 2
    while name.lower() in used:
        name = f"{base} ({i})"
        i += 1
    used.add(name.lower())
    return name


if __name__ == "__main__":
    sys.exit(main())
