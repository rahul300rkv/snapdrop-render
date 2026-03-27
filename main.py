import subprocess
import os

# Auto-update yt-dlp on startup to avoid stale extractors
subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import yt_dlp

app = FastAPI()

# Serve static files (your existing frontend)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

OUTPUT_DIR = "/tmp/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.get("/")
async def home():
    return {"message": "Snapdrop API is Running!"}


@app.get("/download")
async def get_media_link(url: str):
    """
    Extract direct download URL from Instagram, YouTube, or other supported sites.
    Returns media info without downloading to server.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo[ext=mp4]+bestaudio/best',
        'merge_output_format': 'mp4',
        # Mimic a mobile Safari browser to reduce blocking
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                'Version/17.4 Mobile/15E148 Safari/604.1'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        # Allow fallback formats
        'extractor_retries': 3,
        'fragment_retries': 3,
    }

    # Use cookies file if provided (place instagram_cookies.txt in project root)
    if os.path.exists("instagram_cookies.txt"):
        ydl_opts['cookiefile'] = 'instagram_cookies.txt'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Handle playlists / multi-format responses
            if 'entries' in info:
                info = info['entries'][0]

            # Get best format URL
            formats = info.get('formats', [])
            best_url = info.get('url')

            # Try to find a direct mp4 URL
            for f in reversed(formats):
                if f.get('ext') == 'mp4' and f.get('url'):
                    best_url = f['url']
                    break

            return {
                "status": "success",
                "title": info.get('title', 'Media'),
                "download_url": best_url,
                "thumbnail": info.get('thumbnail'),
                "duration": info.get('duration'),
                "uploader": info.get('uploader'),
                "ext": info.get('ext', 'mp4'),
            }

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "login" in error_msg.lower() or "cookie" in error_msg.lower():
            return {
                "status": "error",
                "message": "This content requires authentication. Please add instagram_cookies.txt to the project.",
                "detail": error_msg,
            }
        return {"status": "error", "message": error_msg}

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
