from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
import re
import requests

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 10000))
HTML_DIR = os.getcwd()

TIKTOK_URL_PATTERN = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.IGNORECASE)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
}

TIKWM_API = "https://www.tikwm.com/api/"


def fetch_tikwm_data(tiktok_url):
    resp = requests.post(
        TIKWM_API,
        data={"url": tiktok_url, "hd": "1"},
        headers=BROWSER_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("code") != 0 or not payload.get("data"):
        raise ValueError(payload.get("msg") or "Video not found")

    data = payload["data"]
    base = "https://www.tikwm.com"

    def full_url(path):
        if not path:
            return None
        return path if path.startswith("http") else base + path

    return {
        "title": data.get("title") or "tiktok_video",
        "thumbnail": full_url(data.get("cover")),
        "duration": data.get("duration"),
        "uploader": (data.get("author") or {}).get("unique_id"),
        "video_url": full_url(data.get("hdplay") or data.get("play")),
        "audio_url": full_url(data.get("music")),
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
        result = fetch_tikwm_data(url)
        if not result.get("video_url"):
            return jsonify({"success": False, "error": "Could not find a downloadable video for this link."})
        return jsonify({"success": True, **result})
    except Exception:
        return jsonify({"success": False, "error": "Could not process this link. Double check it and try again."})


@app.route("/api/download")
def download():
    src = (request.args.get("src") or "").strip()
    kind = request.args.get("type", "video")
    filename = request.args.get("filename") or ("fatima_tiktok.mp4" if kind == "video" else "fatima_tiktok.mp3")

    if not src:
        return jsonify({"success": False, "error": "Missing source."}), 400

    try:
        upstream = requests.get(src, stream=True, headers=BROWSER_HEADERS, timeout=30)
        upstream.raise_for_status()
    except Exception:
        return jsonify({"success": False, "error": "Could not download this file. Please try again."}), 502

    def generate():
        for chunk in upstream.iter_content(chunk_size=8192):
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
