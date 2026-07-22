from flask import Flask, request, jsonify, send_from_directory, session, redirect, render_template_string
import os
import asyncio
import edge_tts
import random
import time
import hmac
import json
import requests
from functools import wraps
from werkzeug.utils import secure_filename
from mutagen.mp3 import MP3

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fatima-tts-dev-secret-change-me")

PORT = int(os.environ.get("PORT", 10000))
BASE_DIR = "/tmp"
HTML_DIR = os.getcwd()

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
ONLINE_TTL_SECONDS = 30

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


# ---------- Upstash Redis helper ----------
def redis_command(*args):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("REDIS_DEBUG: URL or TOKEN missing!")
        return None
    try:
        resp = requests.post(
            UPSTASH_URL,
            json=list(args),
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as e:
        print(f"REDIS_DEBUG: command={args[0] if args else '?'} error={e}")
        return None


# ---------- Visitor tracking & IP blocking ----------
def get_client_ip():
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def lookup_ip_info(ip):
    fields = "status,country,city,isp,org,as,reverse,proxy,hosting,mobile,timezone,query"
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields={fields}", timeout=3)
        data = r.json()
        if data.get("status") == "success":
            return {
                "country": data.get("country") or "Unknown",
                "city": data.get("city") or "Unknown",
                "location": f"{data.get('city','')}, {data.get('country','')}".strip(", ") or "Unknown",
                "isp": data.get("isp") or "Unknown",
                "org": data.get("org") or "Unknown",
                "asn": data.get("as") or "Unknown",
                "reverse_dns": data.get("reverse") or "N/A",
                "is_proxy": bool(data.get("proxy")),
                "is_hosting": bool(data.get("hosting")),
                "is_mobile": bool(data.get("mobile")),
                "timezone": data.get("timezone") or "Unknown",
            }
    except Exception:
        pass
    return {
        "country": "Unknown", "city": "Unknown", "location": "Unknown",
        "isp": "Unknown", "org": "Unknown", "asn": "Unknown",
        "reverse_dns": "N/A", "is_proxy": False, "is_hosting": False,
        "is_mobile": False, "timezone": "Unknown",
    }


def parse_user_agent(ua):
    ua = ua or ""
    ua_l = ua.lower()
    if "windows" in ua_l:
        os_name = "Windows"
    elif "android" in ua_l:
        os_name = "Android"
    elif "iphone" in ua_l or "ipad" in ua_l or "ios" in ua_l:
        os_name = "iOS"
    elif "mac os x" in ua_l or "macintosh" in ua_l:
        os_name = "macOS"
    elif "linux" in ua_l:
        os_name = "Linux"
    else:
        os_name = "Unknown"

    if "edg/" in ua_l:
        browser = "Edge"
    elif "opr/" in ua_l or "opera" in ua_l:
        browser = "Opera"
    elif "chrome/" in ua_l and "chromium" not in ua_l:
        browser = "Chrome"
    elif "firefox/" in ua_l:
        browser = "Firefox"
    elif "safari/" in ua_l and "chrome/" not in ua_l:
        browser = "Safari"
    else:
        browser = "Unknown"

    device_type = "Mobile" if ("mobile" in ua_l or "android" in ua_l or "iphone" in ua_l) else (
        "Tablet" if "ipad" in ua_l or "tablet" in ua_l else "Desktop"
    )
    return {"os": os_name, "browser": browser, "device_type": device_type}


def log_visitor(ip):
    existing = redis_command("HGET", "visitors", ip)
    now = int(time.time())
    ua = request.headers.get("User-Agent", "")
    ua_info = parse_user_agent(ua)
    headers_snapshot = {
        "user_agent": ua,
        "accept_language": request.headers.get("Accept-Language", "Unknown"),
        "referer": request.headers.get("Referer", "Direct / None"),
        "http_version": request.environ.get("SERVER_PROTOCOL", "Unknown"),
        "sec_ch_ua": request.headers.get("Sec-CH-UA", "N/A"),
        "sec_ch_ua_mobile": request.headers.get("Sec-CH-UA-Mobile", "N/A"),
        "sec_ch_ua_platform": request.headers.get("Sec-CH-UA-Platform", "N/A"),
    }
    if existing:
        try:
            record = json.loads(existing)
        except Exception:
            record = {"first_seen": now}
        record["last_seen"] = now
        record["visits"] = record.get("visits", 0) + 1
        record.update(ua_info)
        record.update(headers_snapshot)
        if record.get("isp", "Unknown") == "Unknown" or record.get("asn", "Unknown") == "Unknown":
            record.update(lookup_ip_info(ip))
    else:
        record = {
            "first_seen": now,
            "last_seen": now,
            "visits": 1,
            "generations": 0,
        }
        record.update(lookup_ip_info(ip))
        record.update(ua_info)
        record.update(headers_snapshot)
    redis_command("HSET", "visitors", ip, json.dumps(record))


def track_generation(ip):
    existing = redis_command("HGET", "visitors", ip)
    if not existing:
        return
    try:
        record = json.loads(existing)
    except Exception:
        return
    record["generations"] = record.get("generations", 0) + 1
    redis_command("HSET", "visitors", ip, json.dumps(record))


def track_voice_seconds(ip, seconds):
    existing = redis_command("HGET", "visitors", ip)
    if not existing:
        return
    try:
        record = json.loads(existing)
    except Exception:
        return
    record["voice_seconds"] = round(record.get("voice_seconds", 0) + seconds, 1)
    redis_command("HSET", "visitors", ip, json.dumps(record))


def delete_visitor(ip):
    existing = redis_command("HGET", "visitors", ip)
    if existing:
        try:
            record = json.loads(existing)
        except Exception:
            record = {}
        record["deleted_at"] = int(time.time())
        redis_command("HSET", "deleted_visitors", ip, json.dumps(record))
        redis_command("HDEL", "visitors", ip)


def restore_visitor(ip):
    existing = redis_command("HGET", "deleted_visitors", ip)
    if existing:
        try:
            record = json.loads(existing)
        except Exception:
            record = {}
        record.pop("deleted_at", None)
        redis_command("HSET", "visitors", ip, json.dumps(record))
        redis_command("HDEL", "deleted_visitors", ip)


def get_deleted_visitors():
    raw = redis_command("HGETALL", "deleted_visitors")
    out = []
    if isinstance(raw, list):
        for i in range(0, len(raw), 2):
            ip = raw[i]
            try:
                record = json.loads(raw[i + 1])
            except Exception:
                record = {}
            record["ip"] = ip
            out.append(record)
    out.sort(key=lambda v: v.get("deleted_at", 0), reverse=True)
    return out


def is_ip_blocked(ip):
    result = redis_command("SISMEMBER", "blocked_ips", ip)
    return result == 1


def get_site_status():
    result = redis_command("GET", "site_status")
    return result or "on"


def set_site_status(status):
    redis_command("SET", "site_status", status)


def get_total_visitors():
    result = redis_command("HLEN", "visitors")
    return result if isinstance(result, int) else 0


def get_all_visitors():
    raw = redis_command("HGETALL", "visitors")
    visitors = []
    if isinstance(raw, list):
        for i in range(0, len(raw), 2):
            ip = raw[i]
            try:
                record = json.loads(raw[i + 1])
            except Exception:
                record = {}
            record["ip"] = ip
            visitors.append(record)
    visitors.sort(key=lambda v: v.get("last_seen", 0), reverse=True)
    return visitors


def get_blocked_set():
    result = redis_command("SMEMBERS", "blocked_ips")
    return set(result) if isinstance(result, list) else set()


def get_blocked_visitors():
    blocked_ips = get_blocked_set()
    all_visitors = {v["ip"]: v for v in get_all_visitors()}
    out = []
    for ip in blocked_ips:
        record = dict(all_visitors.get(ip, {}))
        record["ip"] = ip
        out.append(record)
    return out


SITE_OFF_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>We'll Be Right Back - Fatima TTS Studio</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<meta http-equiv="refresh" content="20">
<style>
  body{font-family:'Poppins',sans-serif;background:linear-gradient(180deg,#155dfc 0%,#0a46c8 100%);min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:24px;margin:0;}
  .card{max-width:420px;width:100%;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.2);
        border-radius:22px;padding:40px 28px;text-align:center;backdrop-filter:blur(6px);}
  .orb{width:76px;height:76px;border-radius:22px;background:linear-gradient(135deg,#ffffff,#bfdbfe);
        display:flex;align-items:center;justify-content:center;margin:0 auto 22px;
        box-shadow:0 0 0 0 rgba(255,255,255,.5);animation:pulse 2.2s ease-in-out infinite;}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(255,255,255,.45)}70%{box-shadow:0 0 0 16px rgba(255,255,255,0)}100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}}
  .orb i{color:#155dfc;font-size:30px;}
  h1{color:#fff;font-size:22px;font-weight:800;margin:0 0 10px;}
  p{color:rgba(255,255,255,0.75);font-size:14px;line-height:1.6;margin:0;}
  .dots span{display:inline-block;width:6px;height:6px;border-radius:50%;background:#fff;margin:0 2px;animation:blink 1.4s infinite both;}
  .dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
</style>
</head>
<body>
  <div class="card">
    <div class="orb"><i class="fa-solid fa-microphone-lines"></i></div>
    <h1>Website Updating<span class="dots"><span></span><span></span><span></span></span></h1>
    <p>Please Wait..... We're making a few improvements behind the scenes. Fatima TTS Studio will be back online shortly.</p>
  </div>
</body>
</html>
"""


ACCESS_DENIED_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Access Denied - Fatima TTS Studio</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
  body{font-family:'Poppins',sans-serif;background:linear-gradient(180deg,#155dfc 0%,#0a46c8 100%);min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:24px;margin:0;}
  .card{max-width:420px;width:100%;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.2);
        border-radius:20px;padding:36px 28px;text-align:center;backdrop-filter:blur(6px);}
  .icon{width:72px;height:72px;border-radius:50%;background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.4);
        display:flex;align-items:center;justify-content:center;margin:0 auto 20px;}
  .icon i{color:#fca5a5;font-size:28px;}
  h1{color:#fff;font-size:22px;font-weight:700;margin:0 0 10px;}
  p{color:rgba(255,255,255,0.7);font-size:14px;line-height:1.6;margin:0 0 26px;}
  .wa-btn{display:inline-flex;align-items:center;gap:10px;background:#25D366;color:#fff;text-decoration:none;
          font-weight:600;font-size:14px;padding:13px 26px;border-radius:14px;transition:opacity .2s;}
  .wa-btn:hover{opacity:0.9;}
  .wa-btn i{font-size:20px;}
</style>
</head>
<body>
  <div class="card">
    <div class="icon"><i class="fa-solid fa-ban"></i></div>
    <h1>Access Denied</h1>
    <p>Your access to Fatima TTS Studio has been restricted. If you believe this is a mistake, please contact us on WhatsApp and we'll look into it.</p>
    <a class="wa-btn" href="https://wa.me/923051400055?text=Hello" target="_blank" rel="noopener">
      <i class="fa-brands fa-whatsapp"></i> Contact us on WhatsApp
    </a>
  </div>
</body>
</html>
"""


@app.before_request
def enforce_block_and_log():
    if request.path.startswith("/admin") or request.path.startswith("/static"):
        return
    if get_site_status() == "off":
        return render_template_string(SITE_OFF_HTML), 503
    ip = get_client_ip()
    if is_ip_blocked(ip):
        return render_template_string(ACCESS_DENIED_HTML), 403
    if request.path in ("/", "/generate", "/preview"):
        log_visitor(ip)


# ---------- Admin auth ----------
def admin_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper


ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Login - Fatima TTS</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<style>body{font-family:'Poppins',sans-serif;background:linear-gradient(180deg,#155dfc 0%,#0a46c8 100%);min-height:100vh;}</style>
</head>
<body class="flex items-center justify-center p-6">
  <div class="w-full max-w-sm bg-white/10 border border-white/20 rounded-2xl p-6 backdrop-blur">
    <h1 class="text-white text-xl font-bold mb-1">Admin Login</h1>
    <p class="text-white/60 text-xs mb-5">Fatima TTS Studio control panel</p>
    {% if error %}<div class="bg-red-500/20 border border-red-400/40 text-red-100 text-xs rounded-lg p-2.5 mb-4">{{ error }}</div>{% endif %}
    <form method="POST" class="space-y-3">
      <div class="flex gap-2 mb-2">
        <label class="flex-1 text-xs text-white/80"><input type="radio" name="method" value="email" checked class="mr-1"> Email</label>
        <label class="flex-1 text-xs text-white/80"><input type="radio" name="method" value="phone" class="mr-1"> Phone</label>
      </div>
      <input type="text" name="identifier" placeholder="Email or Pakistani phone number" required
        class="w-full px-4 py-3 bg-black/20 border border-white/20 rounded-xl text-white placeholder-white/40 text-sm focus:outline-none focus:border-white">
      <input type="password" name="password" placeholder="Password" required
        class="w-full px-4 py-3 bg-black/20 border border-white/20 rounded-xl text-white placeholder-white/40 text-sm focus:outline-none focus:border-white">
      <button type="submit" class="w-full py-3 bg-white text-[#155dfc] font-bold rounded-xl text-sm">Login</button>
    </form>
  </div>
</body>
</html>
"""

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="15">
<title>Admin Panel - Fatima TTS</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
body{font-family:'Poppins',sans-serif;background:linear-gradient(180deg,#155dfc 0%,#0a46c8 100%);min-height:100vh;}
details summary{cursor:pointer;list-style:none;}
details summary::-webkit-details-marker{display:none;}
</style>
</head>
<body class="p-4 md:p-6">
  <div class="max-w-2xl mx-auto">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-white text-lg font-bold">Visitor Control Panel</h1>
      <a href="/admin/logout" class="text-white/70 text-xs bg-white/10 border border-white/20 rounded-full px-3 py-1.5">Logout</a>
    </div>

    <div class="grid grid-cols-2 gap-2.5 mb-4">
      <div class="bg-white/10 border border-white/20 rounded-xl p-3.5 text-center">
        <p class="text-white/50 text-[10px] font-bold uppercase tracking-wide">Total Visitors</p>
        <p class="text-white text-2xl font-extrabold mt-1">{{ total_visitors }}</p>
      </div>
      <div class="bg-white/10 border border-white/20 rounded-xl p-3.5 text-center">
        <p class="text-white/50 text-[10px] font-bold uppercase tracking-wide">Online Now</p>
        <p class="text-green-300 text-2xl font-extrabold mt-1 flex items-center justify-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>{{ online_count }}
        </p>
      </div>
    </div>

    <form method="POST" action="/admin/toggle-site" class="mb-4">
      <button type="submit" class="w-full py-3 rounded-xl text-sm font-bold {{ 'bg-red-500 text-white' if site_status == 'on' else 'bg-green-500 text-white' }}">
        <i class="fa-solid {{ 'fa-power-off' if site_status == 'on' else 'fa-play' }} mr-1.5"></i>
        {{ 'Turn Website OFF' if site_status == 'on' else 'Turn Website ON' }}
      </button>
      <p class="text-white/40 text-[10px] text-center mt-1.5">
        Site is currently <span class="font-bold {{ 'text-green-300' if site_status == 'on' else 'text-red-300' }}">{{ 'ON' if site_status == 'on' else 'OFF' }}</span>
      </p>
    </form>

    <div class="flex gap-2 mb-4 text-xs font-semibold">
      <a href="/admin" class="flex-1 text-center py-2 rounded-lg {{ 'bg-white text-[#155dfc]' if tab=='overview' else 'bg-white/10 text-white' }}">Visitors</a>
      <a href="/admin/history" class="flex-1 text-center py-2 rounded-lg {{ 'bg-white text-[#155dfc]' if tab=='history' else 'bg-white/10 text-white' }}">History</a>
    </div>

    <div class="space-y-2.5">
      {% for v in visitors %}
      <div class="bg-white/10 border border-white/20 rounded-xl p-3.5">
        <div class="flex items-center justify-between">
          <div class="min-w-0">
            <p class="text-white text-sm font-semibold flex items-center gap-2">
              {{ v.ip }}
              {% if v.ip in online_ips %}
              <span class="inline-flex items-center gap-1 text-[10px] font-bold text-green-300 bg-green-500/20 border border-green-400/40 rounded-full px-2 py-0.5"><span class="w-1.5 h-1.5 rounded-full bg-green-400"></span>Online</span>
              {% endif %}
            </p>
            <p class="text-white/60 text-xs mt-0.5"><i class="fa-solid fa-location-dot mr-1"></i>{{ v.location or 'Unknown' }}</p>
            <p class="text-white/40 text-[10px] mt-0.5">{{ v.visits or 1 }} visit(s) &middot; {{ v.generations or 0 }} generation(s) &middot; {{ '%.1f'|format((v.voice_seconds or 0) / 60) }} min voice generated</p>
          </div>
          <div class="flex gap-1.5 shrink-0">
            <form method="POST" action="{{ '/admin/unblock' if v.ip in blocked else '/admin/block' }}"><input type="hidden" name="ip" value="{{ v.ip }}">
              <button type="submit" class="text-xs font-bold rounded-lg px-3 py-2 {{ 'bg-green-500 text-white' if v.ip in blocked else 'bg-red-500 text-white' }}">{{ 'Unblock' if v.ip in blocked else 'Block' }}</button></form>
            <form method="POST" action="/admin/delete" onsubmit="return confirm('Delete this IP record?');"><input type="hidden" name="ip" value="{{ v.ip }}">
              <button type="submit" class="text-xs font-bold rounded-lg px-3 py-2 bg-gray-500 text-white"><i class="fa-solid fa-trash"></i></button></form>
          </div>
        </div>

        <details class="mt-3">
          <summary class="text-white/70 text-xs font-semibold flex items-center gap-1"><i class="fa-solid fa-chevron-right text-[10px]"></i> Details</summary>
          <div class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] text-white/70 border-t border-white/10 pt-2.5">
            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px]">Network</p>
            <p>ISP: {{ v.isp or 'Unknown' }}</p>
            <p>Org: {{ v.org or 'Unknown' }}</p>
            <p>ASN: {{ v.asn or 'Unknown' }}</p>
            <p>Reverse DNS: {{ v.reverse_dns or 'N/A' }}</p>
            <p>Timezone (IP): {{ v.timezone or 'Unknown' }}</p>
            <p>Type: {{ 'Hosting/Datacenter' if v.is_hosting else ('Proxy/VPN flagged' if v.is_proxy else 'Residential/Mobile') }}</p>
            <p>Connection: {{ v.client.connection_type if v.client else 'Unknown' }}</p>
            <p>HTTP Version: {{ v.http_version or 'Unknown' }}</p>

            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px] mt-1.5">Device</p>
            <p>OS: {{ v.os or 'Unknown' }}</p>
            <p>Device Type: {{ v.device_type or 'Unknown' }}</p>
            {% if v.client %}
            <p>Screen: {{ v.client.screen_width }}x{{ v.client.screen_height }}</p>
            <p>Viewport: {{ v.client.viewport_width }}x{{ v.client.viewport_height }}</p>
            <p>Pixel Ratio: {{ v.client.device_pixel_ratio or 'Unknown' }}</p>
            <p>Color Depth: {{ v.client.color_depth or 'Unknown' }}-bit</p>
            <p>Orientation: {{ v.client.orientation or 'Unknown' }}</p>
            <p>Touch Support: {{ 'Yes' if v.client.touch_support else 'No' }}</p>
            <p>Max Touch Points: {{ v.client.max_touch_points if v.client.max_touch_points is not none else 'Unknown' }}</p>
            <p>CPU Cores: {{ v.client.cpu_cores or 'Unknown' }}</p>
            <p>Device Memory: {{ v.client.device_memory ~ ' GB' if v.client.device_memory else 'Unknown' }}</p>
            {% endif %}

            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px] mt-1.5">Browser &amp; Software</p>
            <p>Browser: {{ v.browser or 'Unknown' }}</p>
            {% if v.client %}
            <p>Dark Mode: {{ 'Yes' if v.client.prefers_dark else 'No' }}</p>
            <p>Reduced Motion: {{ 'Yes' if v.client.prefers_reduced_motion else 'No' }}</p>
            <p>Cookies Enabled: {{ 'Yes' if v.client.cookies_enabled else 'No' }}</p>
            <p>LocalStorage: {{ 'Yes' if v.client.local_storage else 'No' }}</p>
            <p>SessionStorage: {{ 'Yes' if v.client.session_storage else 'No' }}</p>
            <p>IndexedDB: {{ 'Yes' if v.client.indexed_db else 'No' }}</p>
            <p>WebRTC: {{ 'Yes' if v.client.webrtc_support else 'No' }}</p>
            <p>Service Worker: {{ 'Yes' if v.client.service_worker_support else 'No' }}</p>
            {% endif %}
            <p class="col-span-2">Sec-CH-UA: {{ v.sec_ch_ua or 'N/A' }}</p>
            <p>Mobile (CH): {{ v.sec_ch_ua_mobile or 'N/A' }}</p>
            <p>Platform (CH): {{ v.sec_ch_ua_platform or 'N/A' }}</p>

            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px] mt-1.5">Locale</p>
            <p class="col-span-2">Accept-Language Header: {{ v.accept_language or 'Unknown' }}</p>
            {% if v.client %}
            <p>Browser Locale: {{ v.client.language or 'Unknown' }}</p>
            <p>All Languages: {{ v.client.languages or 'Unknown' }}</p>
            <p>Timezone (Browser): {{ v.client.timezone or 'Unknown' }}</p>
            <p>UTC Offset: {{ v.client.utc_offset ~ ' min' if v.client.utc_offset is not none else 'Unknown' }}</p>
            {% endif %}

            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px] mt-1.5">Page &amp; Campaign</p>
            {% if v.client %}
            <p class="col-span-2">Landing Page: {{ v.client.landing_page or 'Unknown' }}</p>
            <p class="col-span-2">Page Title: {{ v.client.page_title or 'Unknown' }}</p>
            <p>UTM Source: {{ v.client.utm_source or 'N/A' }}</p>
            <p>UTM Medium: {{ v.client.utm_medium or 'N/A' }}</p>
            <p>UTM Campaign: {{ v.client.utm_campaign or 'N/A' }}</p>
            {% endif %}
            <p class="col-span-2 break-all">Referer: {{ v.referer or 'Direct / None' }}</p>

            <p class="col-span-2 text-white/50 font-bold uppercase text-[10px] mt-1.5">Raw Headers</p>
            <p class="col-span-2 break-all">User-Agent: {{ v.user_agent or 'Unknown' }}</p>
            {% if not v.client %}
            <p class="col-span-2 text-yellow-200/70 italic mt-1">Browser/device fields not available yet — visitor loaded the site before this update, or JS collector hasn't run for them.</p>
            {% endif %}
          </div>
        </details>
      </div>
      {% else %}
      <p class="text-white/50 text-sm text-center py-10">No visitors logged yet.</p>
      {% endfor %}
    </div>
  </div>
</body>
</html>
"""

ADMIN_HISTORY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="20">
<title>History - Fatima TTS Admin</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>body{font-family:'Poppins',sans-serif;background:linear-gradient(180deg,#155dfc 0%,#0a46c8 100%);min-height:100vh;}</style>
</head>
<body class="p-4 md:p-6">
  <div class="max-w-2xl mx-auto">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-white text-lg font-bold">Visitor Control Panel</h1>
      <a href="/admin/logout" class="text-white/70 text-xs bg-white/10 border border-white/20 rounded-full px-3 py-1.5">Logout</a>
    </div>

    <div class="flex gap-2 mb-4 text-xs font-semibold">
      <a href="/admin" class="flex-1 text-center py-2 rounded-lg bg-white/10 text-white">Visitors</a>
      <a href="/admin/history" class="flex-1 text-center py-2 rounded-lg bg-white text-[#155dfc]">History</a>
    </div>

    <p class="text-white/50 text-[11px] font-bold uppercase mb-2">Blocked IPs</p>
    <div class="space-y-2.5 mb-6">
      {% for v in blocked_visitors %}
      <div class="bg-white/10 border border-white/20 rounded-xl p-3.5 flex items-center justify-between">
        <div class="min-w-0">
          <p class="text-white text-sm font-semibold">{{ v.ip }}</p>
          <p class="text-white/60 text-xs mt-0.5"><i class="fa-solid fa-location-dot mr-1"></i>{{ v.location or 'Unknown' }}</p>
        </div>
        <form method="POST" action="/admin/unblock"><input type="hidden" name="ip" value="{{ v.ip }}">
          <button type="submit" class="text-xs font-bold rounded-lg px-3 py-2 bg-green-500 text-white">Unblock</button></form>
      </div>
      {% else %}
      <p class="text-white/50 text-sm text-center py-6">No blocked IPs.</p>
      {% endfor %}
    </div>

    <p class="text-white/50 text-[11px] font-bold uppercase mb-2">Deleted Records</p>
    <div class="space-y-2.5">
      {% for v in deleted_visitors %}
      <div class="bg-white/10 border border-white/20 rounded-xl p-3.5 flex items-center justify-between">
        <div class="min-w-0">
          <p class="text-white text-sm font-semibold">{{ v.ip }}</p>
          <p class="text-white/60 text-xs mt-0.5"><i class="fa-solid fa-location-dot mr-1"></i>{{ v.location or 'Unknown' }}</p>
        </div>
        <form method="POST" action="/admin/restore"><input type="hidden" name="ip" value="{{ v.ip }}">
          <button type="submit" class="text-xs font-bold rounded-lg px-3 py-2 bg-blue-500 text-white">Restore</button></form>
      </div>
      {% else %}
      <p class="text-white/50 text-sm text-center py-6">No deleted records.</p>
      {% endfor %}
    </div>
  </div>
</body>
</html>
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        valid_id = (identifier == ADMIN_EMAIL and ADMIN_EMAIL) or (identifier == ADMIN_PHONE and ADMIN_PHONE)
        valid_pw = ADMIN_PASSWORD and hmac.compare_digest(password, ADMIN_PASSWORD)
        if valid_id and valid_pw:
            session["is_admin"] = True
            return redirect("/admin")
        error = "Invalid email/phone or password."
    return render_template_string(ADMIN_LOGIN_HTML, error=error)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin/toggle-site", methods=["POST"])
@admin_login_required
def admin_toggle_site():
    new_status = "off" if get_site_status() == "on" else "on"
    set_site_status(new_status)
    return redirect("/admin")


@app.route("/admin")
@admin_login_required
def admin_dashboard():
    visitors = get_all_visitors()
    blocked = get_blocked_set()
    online_ips = get_online_ips()
    total_visitors = get_total_visitors()
    site_status = get_site_status()
    return render_template_string(
        ADMIN_DASHBOARD_HTML,
        visitors=visitors, blocked=blocked, online_ips=online_ips,
        tab="overview", total_visitors=total_visitors,
        online_count=len(online_ips), site_status=site_status,
    )


@app.route("/admin/history")
@admin_login_required
def admin_history():
    blocked_visitors = get_blocked_visitors()
    deleted_visitors = get_deleted_visitors()
    blocked = get_blocked_set()
    return render_template_string(
        ADMIN_HISTORY_HTML, blocked_visitors=blocked_visitors,
        deleted_visitors=deleted_visitors, blocked=blocked, tab="history"
    )


@app.route("/admin/block", methods=["POST"])
@admin_login_required
def admin_block():
    ip = request.form.get("ip", "").strip()
    if ip:
        redis_command("SADD", "blocked_ips", ip)
    return redirect(request.referrer or "/admin")


@app.route("/admin/unblock", methods=["POST"])
@admin_login_required
def admin_unblock():
    ip = request.form.get("ip", "").strip()
    if ip:
        redis_command("SREM", "blocked_ips", ip)
    return redirect(request.referrer or "/admin")


@app.route("/admin/delete", methods=["POST"])
@admin_login_required
def admin_delete():
    ip = request.form.get("ip", "").strip()
    if ip:
        delete_visitor(ip)
    return redirect(request.referrer or "/admin")


@app.route("/admin/restore", methods=["POST"])
@admin_login_required
def admin_restore():
    ip = request.form.get("ip", "").strip()
    if ip:
        restore_visitor(ip)
    return redirect("/admin/history")


def merge_client_info(ip, payload):
    existing = redis_command("HGET", "visitors", ip)
    if not existing:
        return
    try:
        record = json.loads(existing)
    except Exception:
        return
    allowed_keys = {
        "screen_width", "screen_height", "viewport_width", "viewport_height",
        "device_pixel_ratio", "color_depth", "orientation", "touch_support",
        "max_touch_points", "cpu_cores", "device_memory", "timezone",
        "utc_offset", "language", "languages", "prefers_dark", "prefers_reduced_motion",
        "connection_type", "cookies_enabled", "local_storage", "session_storage",
        "indexed_db", "webrtc_support", "service_worker_support", "landing_page",
        "page_title", "utm_source", "utm_medium", "utm_campaign",
    }
    client_data = {k: v for k, v in (payload or {}).items() if k in allowed_keys}
    record["client"] = client_data
    redis_command("HSET", "visitors", ip, json.dumps(record))


@app.route("/api/client-info", methods=["POST"])
def client_info():
    ip = get_client_ip()
    payload = request.json or {}
    merge_client_info(ip, payload)
    return jsonify({"success": True})


# ---------- Online users heartbeat ----------
def get_online_ips():
    keys = redis_command("KEYS", "online:*")
    if not isinstance(keys, list) or not keys:
        return set()
    values = redis_command("MGET", *keys)
    if not isinstance(values, list):
        return set()
    return {v for v in values if v}


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json or {}
    sid = str(data.get("session_id") or "")[:64]
    if not sid:
        return jsonify({"success": False, "count": 1}), 400
    ip = get_client_ip()
    redis_command("SET", f"online:{sid}", ip, "EX", str(ONLINE_TTL_SECONDS))
    keys = redis_command("KEYS", "online:*")
    count = len(keys) if isinstance(keys, list) else 1
    return jsonify({"success": True, "count": max(count, 1)})


# ---------- TTS core ----------
def cleanup_tmp():
    now = time.time()
    try:
        for file in os.listdir(BASE_DIR):
            if file.endswith(".mp3"):
                path = os.path.join(BASE_DIR, file)
                try:
                    if os.path.isfile(path) and now - os.path.getmtime(path) > 600:
                        os.remove(path)
                except Exception:
                    pass
    except Exception:
        pass


async def generate_voice_async(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


@app.route('/')
def index():
    return send_from_directory(HTML_DIR, 'index.html')


@app.route('/preview', methods=['POST'])
def preview():
    cleanup_tmp()
    data = request.json or {}
    voice = data.get('voice', 'ur-PK-UzmaNeural')
    if voice.startswith(("ur-PK", "ur-IN")):
        preview_text = "فاطمہ ٹی ٹی ایس اسٹوڈیو میں آپ کا خوش آمدید ہے۔"
    elif voice.startswith("hi-IN"):
        preview_text = "फ़ातिमा टीटीएस स्टूडियो में आपका स्वागत है।"
    elif voice.startswith("en-"):
        preview_text = "Welcome to the Fatima T.T.S. Studio."
    elif voice.startswith("es-"):
        preview_text = "Bienvenido a Fatima T.T.S. Studio."
    elif voice.startswith("ar-"):
        preview_text = "مرحباً بكم في استوديو فاطمة للأصوات."
    elif voice.startswith("af-"):
        preview_text = "Welkom by Fatima T.T.S. Studio."
    elif voice.startswith("he-"):
        preview_text = "ברוכים הבאים לסטודיו פאטימה."
    else:
        preview_text = "Welcome to Fatima TTS Studio."
    output_file = f"preview-{voice}.mp3"
    output_path = os.path.join(BASE_DIR, output_file)
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except Exception:
            pass
    try:
        asyncio.run(generate_voice_async(preview_text, voice, output_path))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return jsonify({"success": True, "audio_url": f"/download/{output_file}?v={os.urandom(4).hex()}"})
        return jsonify({"success": False, "error": "Zero-byte file generated."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/generate', methods=['POST'])
def generate():
    cleanup_tmp()
    data = request.json or {}
    text = data.get('text', '').strip()
    voice = data.get('voice', 'ur-PK-UzmaNeural')
    if not text:
        return jsonify({"success": False, "error": "Script is empty!"})
    if len(text) > 100000:
        return jsonify({"success": False, "error": "Maximum limit is 100000 characters."})
    random_num = random.randint(100000000000000000, 999999999999999999)
    output_file = f"FatimaTTS-{random_num}.mp3"
    output_path = os.path.join(BASE_DIR, output_file)
    try:
        asyncio.run(generate_voice_async(text, voice, output_path))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            ip = get_client_ip()
            track_generation(ip)
            try:
                duration = MP3(output_path).info.length
                track_voice_seconds(ip, duration)
            except Exception:
                pass
            return jsonify({"success": True, "audio_url": f"/download/{output_file}?v={os.urandom(4).hex()}", "filename": output_file})
        return jsonify({"success": False, "error": "Server failed to process TTS."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/stop', methods=['POST'])
def stop():
    return jsonify({"success": True})


@app.route('/download/<filename>')
def download_file(filename):
    cleanup_tmp()
    filename = secure_filename(filename)
    return send_from_directory(BASE_DIR, filename, as_attachment=False)


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
