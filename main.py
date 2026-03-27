import subprocess
import os

# Auto-update yt-dlp on startup to stay ahead of extractor changes
subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import yt_dlp

app = FastAPI()

OUTPUT_DIR = "/tmp/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def format_duration(seconds):
    if not seconds:
        return None
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_filesize(bytes_val):
    if not bytes_val:
        return None
    mb = bytes_val / (1024 * 1024)
    if mb >= 1000:
        return f"{mb/1024:.1f} GB"
    return f"{mb:.0f} MB"


def detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "instagram.com" in url:
        return "instagram"
    return "unknown"


def build_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 3,
        'fragment_retries': 3,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                'Version/17.4 Mobile/15E148 Safari/604.1'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
    }
    if os.path.exists("instagram_cookies.txt"):
        opts['cookiefile'] = 'instagram_cookies.txt'
    return opts


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def home():
    index = os.path.join("static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "SnapDrop API is Running!"}


@app.get("/api/info")
async def get_media_info(url: str):
    """
    Main endpoint called by the frontend.
    Returns title, thumbnail, duration, author, platform, and list of formats.
    Each format has: label, container, downloadUrl, size, isAudio.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    platform = detect_platform(url)
    if platform == "unknown":
        raise HTTPException(status_code=400, detail="Only YouTube and Instagram links are supported.")

    ydl_opts = build_ydl_opts()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if 'entries' in info:
                info = info['entries'][0]

            all_formats = info.get('formats', [])
            formats_out = []

            if platform == "youtube":
                seen_heights = set()
                video_formats = [
                    f for f in reversed(all_formats)
                    if f.get('vcodec') != 'none'
                    and f.get('acodec') == 'none'
                    and f.get('height')
                    and f.get('url')
                ]
                for f in video_formats:
                    h = f['height']
                    if h in seen_heights:
                        continue
                    if h not in (1080, 720, 480, 360):
                        continue
                    seen_heights.add(h)
                    formats_out.append({
                        "label": f"{h}p",
                        "container": f.get('ext', 'mp4'),
                        "downloadUrl": f['url'],
                        "size": format_filesize(f.get('filesize') or f.get('filesize_approx')),
                        "isAudio": False,
                    })

                formats_out.sort(key=lambda x: int(x['label'].replace('p', '')), reverse=True)

                audio_formats = [
                    f for f in all_formats
                    if f.get('acodec') != 'none'
                    and f.get('vcodec') == 'none'
                    and f.get('url')
                ]
                if audio_formats:
                    best_audio = max(audio_formats, key=lambda f: f.get('abr') or 0)
                    formats_out.append({
                        "label": "Audio",
                        "container": "mp3",
                        "downloadUrl": best_audio['url'],
                        "size": format_filesize(best_audio.get('filesize') or best_audio.get('filesize_approx')),
                        "isAudio": True,
                    })

                if not formats_out:
                    best = info.get('url') or (all_formats[-1]['url'] if all_formats else None)
                    if best:
                        formats_out.append({
                            "label": "Best",
                            "container": "mp4",
                            "downloadUrl": best,
                            "size": None,
                            "isAudio": False,
                        })

            elif platform == "instagram":
                video_formats = [
                    f for f in all_formats
                    if f.get('vcodec') != 'none' and f.get('url')
                ]
                if not video_formats and info.get('url'):
                    video_formats = [{'url': info['url'], 'ext': 'mp4', 'height': None, 'filesize': None}]

                for f in reversed(video_formats):
                    h = f.get('height')
                    formats_out.append({
                        "label": f"{h}p" if h else "HD",
                        "container": f.get('ext', 'mp4'),
                        "downloadUrl": f['url'],
                        "size": format_filesize(f.get('filesize')),
                        "isAudio": False,
                    })

            return {
                "status": "success",
                "platform": platform,
                "title": info.get('title') or info.get('description') or 'Instagram Reel',
                "thumbnail": info.get('thumbnail'),
                "duration": format_duration(info.get('duration')),
                "author": info.get('uploader') or info.get('channel'),
                "formats": formats_out,
            }

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "login" in error_msg.lower() or "cookie" in error_msg.lower() or "private" in error_msg.lower():
            raise HTTPException(status_code=403, detail="This content requires login. Add instagram_cookies.txt to enable private content.")
        raise HTTPException(status_code=422, detail=error_msg)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Legacy /download endpoint kept for backwards compatibility
@app.get("/download")
async def download_legacy(url: str):
    return await get_media_info(url)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
