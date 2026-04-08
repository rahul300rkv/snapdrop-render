import subprocess, os, time, base64, random, asyncio, json

subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], capture_output=True)

# ===================== COOKIE SETUP =====================
def setup_cookies():
    b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")
    if not b64:
        print("[COOKIE] Missing")
        return False
    try:
        with open("youtube_cookies.txt", "wb") as f:
            f.write(base64.b64decode(b64))
        print("[COOKIE] Loaded")
        return True
    except Exception as e:
        print("[COOKIE ERROR]", str(e))
        return False

setup_cookies()

# ===================== REDIS =====================
import redis

REDIS_URL = os.environ.get("REDIS_URL")
redis_client = None

if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL)
        print("[REDIS] Connected")
    except:
        print("[REDIS] Failed")

CACHE_TTL = 3600  # 1 hour

# ===================== FASTAPI =====================
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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

# ===================== PROXY =====================
PROXY_URL = os.environ.get("PROXY_URL", "")
PROXY_LIST = [p.strip() for p in PROXY_URL.split(",") if p.strip()]

# ===================== HELPERS =====================
def extract_video_id(url):
    import re
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else None

def detect_platform(url):
    u = url.lower()
    if "youtube" in u or "youtu.be" in u:
        return "youtube"
    if "instagram" in u:
        return "instagram"
    if "tiktok" in u:
        return "tiktok"
    return "other"

# ===================== CACHE =====================
def get_cache(key):
    if redis_client:
        val = redis_client.get(key)
        if val:
            return json.loads(val)
    return None

def set_cache(key, value):
    if redis_client:
        redis_client.setex(key, CACHE_TTL, json.dumps(value))

# ===================== INVIDIOUS =====================
INVIDIOUS = [
    "https://inv.nadeko.net",
    "https://invidious.privacydev.net",
]

def fetch_invidious(video_id):
    import urllib.request
    for inst in INVIDIOUS:
        try:
            req = urllib.request.Request(f"{inst}/api/v1/videos/{video_id}")
            data = json.loads(urllib.request.urlopen(req, timeout=5).read())

            fmts = []
            for f in data.get("formatStreams", []):
                if f.get("url"):
                    fmts.append({
                        "label": f.get("resolution"),
                        "container": "mp4",
                        "downloadUrl": f["url"],
                        "isAudio": False,
                        "height": int(f.get("resolution", "0").replace("p","") or 0)
                    })

            if fmts:
                return {
                    "title": data.get("title"),
                    "thumbnail": data.get("videoThumbnails")[0]["url"],
                    "formats": fmts
                }
        except:
            continue
    return None

# ===================== YT-DLP =====================
def build_opts(proxy=None, strategy=1):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "force_ipv4": True,
    }

    if proxy:
        opts["proxy"] = proxy

    if strategy == 1:
        client = ["tv_embedded"]
    elif strategy == 2:
        client = ["android"]
    else:
        client = ["web"]

    opts.update({
        "format": "best[ext=mp4]/best",
        "extractor_args": {
            "youtube": {"player_client": client}
        },
        "http_headers": {"User-Agent": "Mozilla/5.0"}
    })

    if strategy >= 2 and os.path.exists("youtube_cookies.txt"):
        opts["cookiefile"] = "youtube_cookies.txt"

    return opts

def extract_ytdlp(url):
    proxies = PROXY_LIST or [None]
    random.shuffle(proxies)

    for strategy in [1,2,3]:
        for proxy in proxies:
            try:
                with yt_dlp.YoutubeDL(build_opts(proxy, strategy)) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if "entries" in info:
                        info = info["entries"][0]
                    return info
            except:
                continue
    return None

# ===================== PLAYWRIGHT FALLBACK =====================
async def extract_playwright(url):
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=60000)

            await page.wait_for_timeout(5000)

            video_url = await page.evaluate("""
                () => {
                    const v = document.querySelector('video');
                    return v ? v.src : null;
                }
            """)

            await browser.close()

            if video_url:
                return {
                    "title": "YouTube Video",
                    "formats": [{
                        "label": "auto",
                        "container": "mp4",
                        "downloadUrl": video_url,
                        "isAudio": False
                    }]
                }
    except Exception as e:
        print("[PLAYWRIGHT FAIL]", e)

    return None

# ===================== FORMAT =====================
def format_output(info):
    fmts = []
    for f in info.get("formats", []):
        if f.get("url"):
            fmts.append({
                "label": f.get("format_note") or "Best",
                "container": f.get("ext", "mp4"),
                "downloadUrl": f["url"],
                "isAudio": f.get("vcodec") == "none",
                "height": f.get("height") or 0
            })
    fmts.sort(key=lambda x: (x["isAudio"], -x["height"]))
    return fmts[:6]

# ===================== API =====================
@app.get("/download")
async def download(url: str):

    cache_key = f"cache:{url}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    platform = detect_platform(url)

    # Invidious
    if platform == "youtube":
        vid = extract_video_id(url)
        if vid:
            inv = fetch_invidious(vid)
            if inv:
                result = {"status":"success", **inv}
                set_cache(cache_key, result)
                return result

    # yt-dlp
    info = extract_ytdlp(url)
    if info:
        result = {
            "status":"success",
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": format_output(info)
        }
        set_cache(cache_key, result)
        return result

    # Playwright fallback
    pw = await extract_playwright(url)
    if pw:
        result = {"status":"success", **pw}
        set_cache(cache_key, result)
        return result

    return {"status":"error","message":"Failed to fetch"}

# ===================== ROOT =====================
@app.get("/")
async def home():
    return FileResponse("static/index.html")

# ===================== RUN =====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
