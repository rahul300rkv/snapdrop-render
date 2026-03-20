import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Install ffmpeg on startup if not present
def ensure_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Installing ffmpeg...")
        subprocess.run(["apt-get", "update", "-qq"], check=False)
        subprocess.run(["apt-get", "install", "-y", "-qq", "ffmpeg"], check=False)

ensure_ffmpeg()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

def run_ytdlp(args):
    cmd = [sys.executable, "-m", "yt_dlp"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def format_bytes(b):
    if not b:
        return None
    b = int(b)
    if b >= 1_073_741_824:
        return f"{b/1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b/1_048_576:.0f} MB"
    return f"{b/1024:.0f} KB"

def format_duration(s):
    if not s:
        return ""
    s = int(s)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02}:{sec:02}"
    return f"{m}:{sec:02}"

@app.get("/api/info")
def get_info(url: str = Query(...)):
    is_yt = bool(re.search(r"youtube\.com|youtu\.be", url))
    is_ig = bool(re.search(r"instagram\.com", url))

    if not is_yt and not is_ig:
        raise HTTPException(400, "Only YouTube and Instagram URLs supported.")

    result = run_ytdlp([
        "--dump-json", "--no-playlist", "--quiet",
        "--extractor-args", "youtube:skip=dash",
        url
    ])

    if result.returncode != 0:
        err = result.stderr.strip()
        if "private" in err.lower():
            raise HTTPException(400, "This video is private.")
        if "not available" in err.lower():
            raise HTTPException(400, "Video not available.")
        raise HTTPException(500, "Could not fetch video info. " + err[:200])

    import json
    try:
        info = json.loads(result.stdout)
    except Exception:
        raise HTTPException(500, "Failed to parse video info.")

    thumbnails = info.get("thumbnails") or []
    thumbnail = None
    if thumbnails:
        best = sorted(thumbnails, key=lambda t: t.get("width") or 0, reverse=True)
        thumbnail = best[0].get("url")
    if not thumbnail:
        thumbnail = info.get("thumbnail")

    formats = []
    raw_formats = info.get("formats") or []

    if is_yt:
        wanted = [("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]
        seen = set()
        for label, height in wanted:
            for f in raw_formats:
                h = f.get("height") or 0
                if abs(h - height) <= 20 and f.get("vcodec", "none") != "none":
                    if label not in seen:
                        seen.add(label)
                        fid = f["format_id"]
                        formats.append({
                            "label": label,
                            "container": "mp4",
                            "format_id": fid,
                            "size": format_bytes(f.get("filesize") or f.get("filesize_approx")),
                            "downloadUrl": f"/api/download?url={url}&format_id={fid}&title={info.get('title','video')}",
                        })
                        break

        formats.append({
            "label": "Audio",
            "container": "mp3",
            "format_id": "bestaudio",
            "size": None,
            "isAudio": True,
            "downloadUrl": f"/api/download?url={url}&format_id=bestaudio&title={info.get('title','audio')}&audio=1",
        })

    else:
        best_vid = sorted(
            [f for f in raw_formats if f.get("vcodec", "none") != "none"],
            key=lambda f: f.get("height") or 0,
            reverse=True,
        )
        if best_vid:
            fid = best_vid[0]["format_id"]
            formats.append({
                "label": "Best Quality",
                "container": "mp4",
                "format_id": fid,
                "size": format_bytes(best_vid[0].get("filesize")),
                "downloadUrl": f"/api/download?url={url}&format_id={fid}&title={info.get('title','video')}",
            })
        else:
            formats.append({
                "label": "Download",
                "container": "mp4",
                "downloadUrl": f"/api/download?url={url}&format_id=best&title=video",
            })

    return {
        "platform": "youtube" if is_yt else "instagram",
        "title": info.get("title") or "Untitled",
        "author": info.get("uploader") or info.get("channel") or "",
        "duration": format_duration(info.get("duration")),
        "thumbnail": thumbnail,
        "formats": formats,
    }

@app.get("/api/download")
def download(
    url: str = Query(...),
    format_id: str = Query(...),
    title: str = Query("video"),
    audio: str = Query("0"),
):
    safe_title = re.sub(r"[^\w\s\-]", "", title).strip() or "video"
    ext = "mp3" if audio == "1" else "mp4"

    def stream():
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--format", format_id + "+bestaudio/best" if audio != "1" else format_id,
            "--merge-output-format", "mp4",
            "-o", "-",
            "--quiet",
            "--no-playlist",
        ]
        if audio == "1":
            cmd += ["--extract-audio", "--audio-format", "mp3"]
        cmd.append(url)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.stdout.close()
            proc.wait()

    return StreamingResponse(
        stream(),
        media_type="audio/mpeg" if audio == "1" else "video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}.{ext}"',
            "X-Accel-Buffering": "no",
        },
    )
