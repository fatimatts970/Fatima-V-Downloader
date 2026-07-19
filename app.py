from flask import Flask, request, jsonify, send_from_directory, send_file, after_this_request
import os
import re
import time
import uuid
import glob
import yt_dlp

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 10000))
HTML_DIR = os.getcwd()
TMP_DIR = "/tmp"

TIKTOK_URL_PATTERN = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.IGNORECASE)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Referer": "https://www.tiktok.com/",
}


def cleanup_old_files():
    now = time.time()
    for f in glob.glob(os.path.join(TMP_DIR, "fatima_ttk_*")):
        try:
            if now - os.path.getmtime(f) > 600:
                os.remove(f)
        except Exception:
            pass


def extract_tiktok_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": BROWSER_HEADERS,
        "extractor_args": {"tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return {
        "title": info.get("title") or "tiktok_video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("uploader_id"),
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
        return jsonify({"success": True, **result})
    except Exception:
        return jsonify({"success": False, "error": "Could not process this link. Double check it and try again."})


@app.route("/api/download")
def download():
    cleanup_old_files()
    url = (request.args.get("url") or "").strip()
    kind = request.args.get("type", "video")

    if not url or not TIKTOK_URL_PATTERN.search(url):
        return jsonify({"success": False, "error": "Invalid or missing link."}), 400

    uid = uuid.uuid4().hex
    out_template = os.path.join(TMP_DIR, f"fatima_ttk_{uid}.%(ext)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": BROWSER_HEADERS,
        "outtmpl": out_template,
        "extractor_args": {"tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]}},
    }

    if kind == "audio":
        ydl_opts["format"] = "bestaudio/best"
    else:
        ydl_opts["format"] = "best[ext=mp4]/best"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)

        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return jsonify({"success": False, "error": "Download failed, please try again."}), 500

        title = re.sub(r"[^a-zA-Z0-9]+", "_", info.get("title") or "fatima_tiktok")[:60]
        ext = filepath.rsplit(".", 1)[-1]
        download_name = f"{title}.{ext}"

        @after_this_request
        def cleanup(response):
            try:
                os.remove(filepath)
            except Exception:
                pass
            return response

        return send_file(filepath, as_attachment=True, download_name=download_name)
    except Exception:
        return jsonify({"success": False, "error": "Could not download this video. It may be private or unavailable."}), 500


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
