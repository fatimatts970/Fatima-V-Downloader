from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
import re
import random
import requests

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 10000))
HTML_DIR = os.getcwd()

TIKTOK_URL_PATTERN = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.IGNORECASE)
TIKWM_API = "https://www.tikwm.com/api/"
ALLOWED_SRC_HOSTS = ("tikwm.com", "www.tikwm.com")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
}


def random_filename(ext):
    number = "".join(str(random.randint(0, 9)) for _ in range(25))
    return f"TikTokVideoDownloader-{number}.{ext}"


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
        resp = requests.post(
            TIKWM_API,
            data={"url": url, "hd": 1},
            headers=BROWSER_HEADERS,
            timeout=20,
        )
        result = resp.json()
    except Exception:
        return jsonify({"success": False, "error": "Could not reach the video service. Please try again."})

    if result.get("code") != 0 or not result.get("data"):
        return jsonify({"success": False, "error": "Could not process this link. It may be private or unavailable."})

    d = result["data"]
    video_url = d.get("hdplay") or d.get("play")
    audio_url = d.get("music")

    if not video_url:
        return jsonify({"success": False, "error": "Could not find a downloadable video for this link."})

    author = d.get("author") or {}

    return jsonify({
        "success": True,
        "title": d.get("title") or "TikTok video",
        "thumbnail": d.get("cover"),
        "duration": d.get("duration"),
        "uploader": author.get("unique_id") or author.get("nickname"),
        "video_url": video_url,
        "audio_url": audio_url,
    })


@app.route("/api/download")
def download():
    src = (request.args.get("src") or "").strip()
    kind = request.args.get("type", "video")

    if not src:
        return jsonify({"success": False, "error": "Missing source link."}), 400

    from urllib.parse import urlparse
    host = urlparse(src).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_SRC_HOSTS):
        return jsonify({"success": False, "error": "Invalid source."}), 400

    ext = "mp3" if kind == "audio" else "mp4"
    filename = random_filename(ext)
    content_type = "audio/mpeg" if kind == "audio" else "video/mp4"

    try:
        upstream = requests.get(src, headers=BROWSER_HEADERS, stream=True, timeout=30)
        upstream.raise_for_status()
    except Exception:
        return jsonify({"success": False, "error": "Download failed. Please try again."}), 502

    def generate():
        for chunk in upstream.iter_content(chunk_size=65536):
            if chunk:
                yield chunk

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
