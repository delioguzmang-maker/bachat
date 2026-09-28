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
