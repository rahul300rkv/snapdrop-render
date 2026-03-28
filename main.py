import subprocess
import os

# Auto-update yt-dlp on every startup so extractors never go stale
subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import yt_dlp

app = FastAPI()

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def home():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"message": "Snapdrop API is Running!"}


def detect_platform(url: str) -> str:
    u = url.lower()
    if "instagram.com" in u:                     return "instagram"
    if "youtube.com" in u or "youtu.be" in u:    return "youtube"
    if "tiktok.com" in u:                        return "tiktok"
    if "twitter.com" in u or "x.com" in u:       return "twitter"
    if "facebook.com" in u or "fb.watch" in u:   return "facebook"
    return "other"


def build_ydl_opts(platform: str) -> dict:
    # Default: desktop Chrome — works best for YouTube, TikTok, Facebook, etc.
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 3,
        'fragment_retries': 3,
        'http_headers': {
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        },
    }

    if platform == "instagram":
        # Instagram responds better to mobile Safari
        opts['http_headers']['User-Agent'] = (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/17.4 Mobile/15E148 Safari/604.1'
        )
        if os.path.exists("instagram_cookies.txt"):
            opts['cookiefile'] = 'instagram_cookies.txt'

    elif platform == "youtube":
        # YouTube needs desktop Chrome; cookies help with age-restricted content
        if os.path.exists("youtube_cookies.txt"):
            opts['cookiefile'] = 'youtube_cookies.txt'

    elif platform == "tiktok":
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'

    elif platform == "twitter":
        if os.path.exists("twitter_cookies.txt"):
            opts['cookiefile'] = 'twitter_cookies.txt'

    elif platform == "facebook":
        if os.path.exists("facebook_cookies.txt"):
            opts['cookiefile'] = 'facebook_cookies.txt'

    return opts


def extract_formats(info: dict) -> list:
    """Normalize yt-dlp formats into a clean list, best quality first."""
    out = []
    seen_urls = set()
    seen_labels = set()

    for f in reversed(info.get('formats', [])):
        url = f.get('url', '')
        if not url or url in seen_urls:
            continue

        vcodec = f.get('vcodec', '')
        acodec = f.get('acodec', '')
        is_audio_only = (not vcodec or vcodec == 'none') and acodec not in ('none', '', None)

        height = f.get('height')
        ext    = f.get('ext', 'mp4')
        size   = f.get('filesize') or f.get('filesize_approx')
        tbr    = f.get('tbr')

        if height:
            label = f"{height}p"
        elif tbr:
            label = f"{int(tbr)}kbps"
        else:
            label = f.get('format_note') or 'Best'

        if label in seen_labels:
            continue

        seen_urls.add(url)
        seen_labels.add(label)
        out.append({
            "label":       label,
            "container":   ext,
            "downloadUrl": url,
            "size":        size,
            "isAudio":     is_audio_only,
            "height":      height or 0,
        })

    # Fallback: if yt-dlp returned a single merged URL instead of a formats list
    if not out and info.get('url'):
        out.append({
            "label":       "Best",
            "container":   info.get('ext', 'mp4'),
            "downloadUrl": info['url'],
            "size":        None,
            "isAudio":     False,
            "height":      0,
        })

    return out[:8]  # cap at 8 options


@app.get("/download")
async def get_media_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    platform = detect_platform(url)
    ydl_opts = build_ydl_opts(platform)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Unwrap playlists — take first entry
            if 'entries' in info:
                info = info['entries'][0]

            formats = extract_formats(info)

            return {
                "status":       "success",
                "platform":     platform,
                "title":        info.get('title', 'Media'),
                "thumbnail":    info.get('thumbnail'),
                "duration":     str(info.get('duration_string') or info.get('duration') or ''),
                "author":       (info.get('uploader') or info.get('channel') or info.get('creator') or ''),
                "formats":      formats,
                # Legacy single-URL fallback (keeps old frontend working)
                "download_url": formats[0]['downloadUrl'] if formats else None,
                "ext":          formats[0]['container']   if formats else 'mp4',
            }

    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if any(k in msg for k in ["login", "cookie", "sign in", "log in"]):
            return {
                "status":  "error",
                "message": f"This {platform} content requires login. Add {platform}_cookies.txt to the project.",
            }
        if "private" in msg:
            return {"status": "error", "message": "This content is private."}
        if "unavailable" in msg or "removed" in msg:
            return {"status": "error", "message": "This content is unavailable or has been removed."}
        return {"status": "error", "message": str(e)}

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
