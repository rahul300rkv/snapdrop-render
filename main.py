import subprocess, os

subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

# Write cookies from Railway environment variables to files on disk
print("=== COOKIE ENV CHECK ===")
for _platform in ["youtube", "instagram", "twitter", "facebook", "tiktok"]:
    _env_key = f"{_platform.upper()}_COOKIES"
    _val = os.environ.get(_env_key, "")
    if _val:
        with open(f"{_platform}_cookies.txt", "w") as _f:
            _f.write(_val)
        print(f"[OK] Written {_platform}_cookies.txt — {len(_val)} chars")
    else:
        print(f"[MISSING] {_env_key} not set")
print("=== END COOKIE CHECK ===")

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def home():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"message": "Snapdrop API Running"}


@app.get("/debug")
async def debug():
    cookie_files = {}
    for platform in ["youtube", "instagram", "twitter", "facebook"]:
        path = f"{platform}_cookies.txt"
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        cookie_files[platform] = {"exists": exists, "size_bytes": size}

    env_vars = {}
    for platform in ["youtube", "instagram", "twitter", "facebook"]:
        key = f"{platform.upper()}_COOKIES"
        val = os.environ.get(key, "")
        env_vars[key] = f"{len(val)} chars" if val else "NOT SET"

    return {"cookie_files": cookie_files, "env_vars": env_vars}


def detect_platform(url):
    u = url.lower()
    if "instagram.com" in u: return "instagram"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u: return "tiktok"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    return "other"


def build_ydl_opts(platform):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 3,
        # Single-stream format — no ffmpeg merge needed
        'format': 'best[ext=mp4]/best[ext=webm]/best',
        'http_headers': {
            'Accept-Language': 'en-US,en;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        },
    }
    if platform == "instagram":
        opts['http_headers']['User-Agent'] = (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
        )
        if os.path.exists("instagram_cookies.txt"):
            opts['cookiefile'] = 'instagram_cookies.txt'

    elif platform == "youtube":
        # Prefer muxed mp4 streams to avoid needing ffmpeg.
        # Falls back through progressively simpler options.
        opts['format'] = (
            'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]'
            '/bestvideo[ext=mp4]+bestaudio'
            '/best[ext=mp4][height<=1080]'
            '/best[ext=mp4]'
            '/best'
        )
        if os.path.exists("youtube_cookies.txt"):
            opts['cookiefile'] = 'youtube_cookies.txt'
            print("[yt-dlp] Using youtube_cookies.txt")
        else:
            print("[yt-dlp] No youtube_cookies.txt found!")

    elif platform == "tiktok":
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'

    elif platform == "twitter":
        if os.path.exists("twitter_cookies.txt"):
            opts['cookiefile'] = 'twitter_cookies.txt'

    elif platform == "facebook":
        if os.path.exists("facebook_cookies.txt"):
            opts['cookiefile'] = 'facebook_cookies.txt'

    return opts


def extract_formats(info):
    out, seen_urls, seen_labels = [], set(), set()
    for f in reversed(info.get('formats', [])):
        url = f.get('url', '')
        if not url or url in seen_urls:
            continue
        vcodec = f.get('vcodec', '')
        acodec = f.get('acodec', '')
        # Skip video-only streams — they need ffmpeg to merge audio in
        if vcodec and vcodec != 'none' and (not acodec or acodec == 'none'):
            continue
        height = f.get('height')
        label = (
            f"{height}p" if height
            else (f"{int(f['tbr'])}kbps" if f.get('tbr')
                  else f.get('format_note') or 'Best')
        )
        if label in seen_labels:
            continue
        seen_urls.add(url)
        seen_labels.add(label)
        out.append({
            "label": label,
            "container": f.get('ext', 'mp4'),
            "downloadUrl": url,
            "size": f.get('filesize') or f.get('filesize_approx'),
            "isAudio": (not vcodec or vcodec == 'none'),
            "height": height or 0,
        })
    # Fallback if no muxed formats found
    if not out and info.get('url'):
        out.append({
            "label": "Best",
            "container": info.get('ext', 'mp4'),
            "downloadUrl": info['url'],
            "size": None,
            "isAudio": False,
            "height": 0,
        })
    return out[:8]


@app.get("/download")
async def get_media_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")
    platform = detect_platform(url)
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(platform)) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            formats = extract_formats(info)
            return {
                "status": "success",
                "platform": platform,
                "title": info.get('title', 'Media'),
                "thumbnail": info.get('thumbnail'),
                "duration": str(info.get('duration_string') or info.get('duration') or ''),
                "author": info.get('uploader') or info.get('channel') or '',
                "formats": formats,
                "download_url": formats[0]['downloadUrl'] if formats else None,
                "ext": formats[0]['container'] if formats else 'mp4',
            }
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if any(k in msg for k in ["login", "cookie", "sign in"]):
            return {"status": "error", "message": f"This {platform} content requires login cookies."}
        if "private" in msg:
            return {"status": "error", "message": "This content is private."}
        if "requested format" in msg or "format" in msg:
            return {"status": "error", "message": "No downloadable format found for this video."}
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
