from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import yt_dlp, subprocess, shutil, os

app = FastAPI()
OUTPUT_DIR = "/tmp/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.get("/download")
async def get_reel_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio/best',
        'outtmpl': f'{OUTPUT_DIR}/original.%(ext)s',
        'merge_output_format': 'mp4',
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)  # Just get link
            return {
                "status": "success",
                "title": info.get('title'),
                "download_url": info.get('url'),
                "thumbnail": info.get('thumbnail'),
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}
