import subprocess, os, time

subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

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

PROXY_URL = os.environ.get("PROXY_URL", "").strip()
if PROXY_URL:
    print(f"[PROXY] Using proxy: {PROXY_URL[:40]}...")
else:
    print("[PROXY] No PROXY_URL set")

PROXY_LIST = [p.strip() for p in PROXY_URL.split(",") if p.strip()] if PROXY_URL else []


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
    return {
        "cookie_files": cookie_files,
        "env_vars": env_vars,
        "proxy_count": len(PROXY_LIST),
        "proxy_set": bool(PROXY_LIST),
    }


# Public Invidious instances — used as YouTube fallback
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacydev.net",
    "https://yt.drgnz.club",
    "https://invidious.io.lol",
]


def fetch_from_invidious(video_id: str):
    import urllib.request, json
    for instance in INVIDIOUS_INSTANCES:
        try:
            req = urllib.request.Request(
                f"{instance}/api/v1/videos/{video_id}?fields=title,author,lengthSeconds,videoThumbnails,adaptiveFormats,formatStreams",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())

            formats = []

            # formatStreams = muxed (video+audio together), always lower quality
            # but NO auth needed — this is what savefrom.net serves
            for f in data.get("formatStreams", []):
                url = f.get("url")
                if not url:
                    continue
                height = f.get("resolution", "").replace("p", "")
                try:
                    height = int(height)
                except Exception:
                    height = 0
                formats.append({
                    "label": f.get("resolution") or "SD",
                    "container": f.get("container", "mp4"),
                    "downloadUrl": url,
                    "size": f.get("clen"),
                    "isAudio": False,
                    "height": height,
                })

            # adaptiveFormats = separate video+audio streams, higher quality
            # may require auth for age-restricted — add anyway, user can try
            for f in data.get("adaptiveFormats", []):
                url = f.get("url")
                if not url:
                    continue
                height = f.get("resolution", "").replace("p", "")
                try:
                    height = int(height)
                except Exception:
                    height = 0
                has_video = "video" in f.get("type", "")
                formats.append({
                    "label": f.get("resolution") or f.get("bitrate") or "HD",
                    "container": f.get("container", "mp4"),
                    "downloadUrl": url,
                    "size": f.get("clen"),
                    "isAudio": not has_video,
                    "height": height,
                })

            if formats:
                # Sort: muxed SD first (most compatible), then HD adaptive
                formats.sort(key=lambda x: (x["isAudio"], -x["height"]))
                return {
                    "title": data.get("title", "YouTube Video"),
                    "thumbnail": (data.get("videoThumbnails") or [{}])[0].get("url", ""),
                    "duration": str(data.get("lengthSeconds", "")),
                    "author": data.get("author", ""),
                    "formats": formats[:8],
                }
        except Exception as e:
            print(f"[Invidious] {instance} failed: {e}")
            continue
    return None


def extract_video_id(url: str):
    import re
    patterns = [
        r"(?:v=|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def detect_platform(url):
    u = url.lower()
    if "instagram.com" in u: return "instagram"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u: return "tiktok"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    return "other"


def build_ydl_opts(platform, proxy=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 3,
        'socket_timeout': 30,
        'http_headers': {
            'Accept-Language': 'en-US,en;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/124.0.0.0 Safari/537.36',
        },
    }

    if proxy:
        opts['proxy'] = proxy

    if platform == "youtube":
        opts['format'] = 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best'
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['ios', 'web_creator', 'tv_embedded', 'tv', 'mweb'],
                'player_skip': ['webpage', 'config'],
            }
        }
        opts['http_headers'].update({
            'User-Agent': 'com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)',
            'Referer': 'https://www.youtube.com/',
            'Origin': 'https://www.youtube.com',
        })
        if os.path.exists("youtube_cookies.txt"):
            opts['cookiefile'] = 'youtube_cookies.txt'

    elif platform == "instagram":
        opts['http_headers']['User-Agent'] = (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/17.4 Mobile/15E148 Safari/604.1'
        )
        opts['format'] = 'best[ext=mp4]/best'
        if os.path.exists("instagram_cookies.txt"):
            opts['cookiefile'] = 'instagram_cookies.txt'

    elif platform == "tiktok":
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'
        opts['http_headers']['User-Agent'] = (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/17.4 Mobile/15E148 Safari/604.1'
        )
        opts['format'] = 'best[ext=mp4]/best'

    elif platform == "twitter":
        opts['format'] = 'best[ext=mp4]/best'
        if os.path.exists("twitter_cookies.txt"):
            opts['cookiefile'] = 'twitter_cookies.txt'

    elif platform == "facebook":
        opts['format'] = 'best[ext=mp4]/best'
        if os.path.exists("facebook_cookies.txt"):
            opts['cookiefile'] = 'facebook_cookies.txt'

    else:
        opts['format'] = 'best[ext=mp4]/best'

    return opts


def extract_formats(info):
    all_fmts = [f for f in info.get('formats', []) if f.get('url')]

    def has_video(f): return f.get('vcodec') and f.get('vcodec') != 'none'
    def is_muxed(f):
        return has_video(f) and f.get('acodec') and f.get('acodec') != 'none'

    candidates = [f for f in all_fmts if is_muxed(f)] or all_fmts

    out, seen_urls, seen_labels = [], set(), set()
    for f in reversed(candidates):
        url = f.get('url', '')
        if not url or url in seen_urls:
            continue
        height = f.get('height')
        label = (
            f"{height}p" if height
            else (f"{int(f['tbr'])}kbps" if f.get('tbr')
                  else f.get('format_note') or f.get('format_id') or 'Best')
        )
        base = label; c = 2
        while label in seen_labels:
            label = f"{base}_{c}"; c += 1
        seen_urls.add(url)
        seen_labels.add(label)
        out.append({
            "label": label,
            "container": f.get('ext', 'mp4'),
            "downloadUrl": url,
            "size": f.get('filesize') or f.get('filesize_approx'),
            "isAudio": not has_video(f),
            "height": height or 0,
        })

    if not out and info.get('url'):
        out.append({
            "label": "Best",
            "container": info.get('ext', 'mp4'),
            "downloadUrl": info['url'],
            "size": None, "isAudio": False, "height": 0,
        })

    return out[:8]


def extract_with_retry(url, platform):
    proxies_to_try = PROXY_LIST + [None]
    last_error = None

    for attempt, proxy in enumerate(proxies_to_try):
        try:
            print(f"[yt-dlp] Attempt {attempt+1} — proxy: {proxy or 'none'}")
            opts = build_ydl_opts(platform, proxy=proxy)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                return info, None

        except yt_dlp.utils.DownloadError as e:
            last_error = e
            msg = str(e).lower()
            retriable = [
                "remote end closed", "transport", "connection",
                "timeout", "proxy", "sign in", "login",
                "age", "confirm you're not a bot", "cookie",
                "this video is not available", "unavailable",
            ]
            if any(k in msg for k in retriable):
                print(f"[yt-dlp] Retriable error on attempt {attempt+1}: {str(e)[:80]}")
                time.sleep(1)
                continue
            return None, e

        except Exception as e:
            last_error = e
            print(f"[yt-dlp] Unexpected error on attempt {attempt+1}: {str(e)[:80]}")
            time.sleep(1)
            continue

    return None, last_error


@app.get("/download")
async def get_media_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    platform = detect_platform(url)

    # For YouTube: try Invidious first (bypasses datacenter IP block entirely)
    if platform == "youtube":
        video_id = extract_video_id(url)
        if video_id:
            print(f"[Invidious] Trying for video_id: {video_id}")
            inv_result = fetch_from_invidious(video_id)
            if inv_result:
                print("[Invidious] Success!")
                return {
                    "status": "success",
                    "platform": "youtube",
                    "title": inv_result["title"],
                    "thumbnail": inv_result["thumbnail"],
                    "duration": inv_result["duration"],
                    "author": inv_result["author"],
                    "formats": inv_result["formats"],
                    "download_url": inv_result["formats"][0]["downloadUrl"],
                    "ext": inv_result["formats"][0]["container"],
                }
            print("[Invidious] All instances failed, falling back to yt-dlp...")

    # Fall back to yt-dlp for all other platforms (and YouTube if Invidious fails)
    info, error = extract_with_retry(url, platform)

    if error:
        msg = str(error).lower()
        if "private" in msg:
            return {"status": "error", "message": "This content is private."}
        if "geo" in msg or "not available in your country" in msg:
            return {"status": "error", "message": "This content is not available in this region."}
        if any(k in msg for k in ["login", "cookie", "sign in", "age", "confirm"]):
            return {"status": "error", "message": "This video is age-restricted or members-only."}
        return {"status": "error", "message": str(error)}

    if not info:
        return {"status": "error", "message": "Could not fetch video info."}

    formats = extract_formats(info)
    if not formats:
        return {"status": "error", "message": "No downloadable stream found."}

    return {
        "status": "success",
        "platform": platform,
        "title": info.get('title', 'Media'),
        "thumbnail": info.get('thumbnail'),
        "duration": str(info.get('duration_string') or info.get('duration') or ''),
        "author": info.get('uploader') or info.get('channel') or '',
        "formats": formats,
        "download_url": formats[0]['downloadUrl'],
        "ext": formats[0]['container'],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
