from fastapi import FastAPI, HTTPException
import yt_dlp
import os

app = FastAPI()
@app.get("/")
async def home():
    return {"message": "Insta Downloader API is Running!"}
@app.get("/download")
async def get_reel_link(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is missing")

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best',
        # 2026 Stealth: Pretend to be a mobile browser
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "status": "success",
                "title": info.get('title', 'Instagram Reel'),
                "download_url": info.get('url'),
                "thumbnail": info.get('thumbnail')
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    # Railway will automatically provide the PORT environment variable
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
