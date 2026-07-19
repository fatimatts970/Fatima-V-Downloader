from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
import re
import requests
import yt_dlp

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 10000))
HTML_DIR = os.getcwd()

TIKTOK_URL_PATTERN = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.IGNORECASE)


CDN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Referer": "https://www.tiktok.com/",
}


def extract_tiktok_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": CDN_HEADERS,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_url = info.get("url")
    formats = info.get("formats") or []
    if not video_url and formats:
        no_watermark = [f for f in formats if f.get("format_note", "").lower() in ("no watermark", "download")]
        chosen = no_watermark[0] if no_watermark else formats[-1]
        video_url = chosen.get("url")

    audio_url = None
    for f in formats:
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
            audio_url = f.get("url")
            break

    return {
        "title": info.get("title") or "tiktok_video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("uploader_id"),
        "video_url": video_url,
        "audio_url": audio_url,
    }


@app.route("/")
def index():
    return send_from_directory(HTML_DIR, "index.html")


@app.route("/api/fetch", methods=["POST"])
def fetch():
    data = request.json or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "Please paste a TikTok link first."})

    if not TIKTOK_URL_PATTERN.search(url):
        return jsonify({"success": False, "error": "That doesn't look like a valid TikTok link."})

    try:
        result = extract_tiktok_info(url)
        if not result.get("video_url"):
            return jsonify({"success": False, "error": "Could not extract this video. It may be private or unavailable."})
        return jsonify({"success": True, **result})
    except Exception:
        return jsonify({"success": False, "error": "Could not process this link. Double check it and try again."})


@app.route("/api/download")
def proxy_download():
    src = request.args.get("src")
    filename = request.args.get("filename", "fatima-tiktok-video.mp4")
    kind = request.args.get("type", "video")

    if not src:
        return jsonify({"success": False, "error": "Missing source."}), 400

    if kind == "audio" and not filename.lower().endswith((".mp3", ".m4a")):
        filename = filename.rsplit(".", 1)[0] + ".mp3"
    elif kind == "video" and not filename.lower().endswith(".mp4"):
        filename = filename.rsplit(".", 1)[0] + ".mp4"

    def generate():
        with requests.get(src, stream=True, timeout=30, headers=CDN_HEADERS) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

    content_type = "audio/mpeg" if kind == "audio" else "video/mp4"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(stream_with_context(generate()), content_type=content_type, headers=headers)


@app.route("/about")
def about():
    return send_from_directory(HTML_DIR, "about.html")


@app.route("/privacy")
def privacy():
    return send_from_directory(HTML_DIR, "privacy.html")


@app.route("/terms")
def terms():
    return send_from_directory(HTML_DIR, "terms.html")


@app.route("/contact")
def contact():
    return send_from_directory(HTML_DIR, "contact.html")


@app.route("/robots.txt")
def robots():
    return send_from_directory(HTML_DIR, "robots.txt")


@app.route("/sitemap.xml")
def sitemap():
    return send_from_directory(HTML_DIR, "sitemap.xml")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(HTML_DIR, "favicon.ico")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
