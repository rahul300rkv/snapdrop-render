import subprocess, os
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Upgrade yt-dlp on startup
subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

# Write cookies from Railway environment variables
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
    cookie_files = {p: {"exists": os.path.exists(f"{p}_cookies.txt")} for p in ["youtube", "instagram", "twitter", "facebook"]}
    return {"cookie_files": cookie_files}

def detect_platform(url):
    u = url.lower()
    if "instagram.com" in u: return "instagram"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u: return "tiktok"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    return "other"

def build_ydl_opts(platform):
    # 'best' ensures we look for formats that have both Video + Audio combined
    opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best', 
        'check_formats': False,
        'http_headers': {
            'Accept-Language': 'en-US,en;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        },
    }
    
    # Map cookie files if they exist
    cookie_file = f"{platform}_cookies.txt"
    if os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file
        
    if platform == "instagram":
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
    elif platform == "tiktok":
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'
        
    return opts

def extract_formats(info):
    out, seen_labels = [], set()
    formats = info.get('formats', [])
    
    # Sort formats: Higher resolution first
    formats.sort(key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)

    for f in formats:
        url = f.get('url', '')
        if not url or "manifest" in url or "m3u8" in url: continue
        
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        
        # Logic: We only want "Progressive" (V+A) or pure Audio
        is_audio = (vcodec == 'none' and acodec != 'none')
        is_video_with_audio = (vcodec != 'none' and acodec != 'none')
        
        if not (is_audio or is_video_with_audio):
            continue

        height = f.get('height')
        label = f"{height}p" if height else (f"{int(f['tbr'])}kbps" if f.get('tbr') else "Direct")
        
        if label in seen_labels: continue
        seen_labels.add(label)

        out.append({
            "label": label,
            "container": f.get('ext', 'mp4'),
            "downloadUrl": url,
            "size": f.get('filesize') or f.get('filesize_approx'),
            "isAudio": is_audio,
            "height": height or 0
        })
        
    # Fallback if no specific formats found but a general URL exists
    if not out and info.get('url'):
        out.append({"label": "Best", "container": "mp4", "downloadUrl": info['url'], "isAudio": False})
        
    return out[:8]

@app.get("/download")
async def get_media_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")
    
    platform = detect_platform(url)
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(platform)) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info: info = info['entries'][0]
            
            formats = extract_formats(info)
            if not formats:
                return {"status": "error", "message": "No playable formats found."}

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
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
