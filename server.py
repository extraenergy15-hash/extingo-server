#!/usr/bin/env python3
"""
Extingo v5.6 — Backend Server (http.server — NOT Flask)
Upgrades: B2 key remap, B3 webhook, B4 /api/command,
          Upgrade 2 (OpenCV capture), Upgrade 3 (Telegram),
          Upgrade 4 (CallMeBot)
Run: python3 server.py
Deps: pip install opencv-python requests
"""

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Optional deps (graceful degradation if missing) ───────────────────────────
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[VISION] opencv-python not installed — webcam capture disabled")

try:
    import requests as req_lib
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[WARN] requests library not installed — Telegram/CallMeBot disabled")

# =============================================================================
# CONFIGURATION
# =============================================================================
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000

# ── Telegram (Upgrade 3) ──────────────────────────────────────────────────────
# All secrets/config below are loaded from environment variables — see
# .env.example for the full list and how to set them before running.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
NGROK_URL          = os.environ.get("NGROK_URL", "")

# ── CallMeBot (Upgrade 4) ─────────────────────────────────────────────────────
CALLMEBOT_TELEGRAM_USERNAME = os.environ.get("CALLMEBOT_TELEGRAM_USERNAME", "YOUR_TELEGRAM_USERNAME")   # without @

# ── Startup config check ──────────────────────────────────────────────────────
_missing = [name for name, val in {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN if TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" else "",
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    "NGROK_URL": NGROK_URL,
    "CALLMEBOT_TELEGRAM_USERNAME": CALLMEBOT_TELEGRAM_USERNAME if CALLMEBOT_TELEGRAM_USERNAME != "YOUR_TELEGRAM_USERNAME" else "",
}.items() if not val]
if _missing:
    print(f"[CONFIG] Warning: env vars not set (features will be skipped): {', '.join(_missing)}")
    print("[CONFIG] Copy .env.example to .env, fill in values, and export them before running.")

# ── Alert image path ──────────────────────────────────────────────────────────
ALERT_IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "extingo_alert.jpg")

# =============================================================================
# IN-MEMORY STATE
# =============================================================================
latest_telemetry: dict = {}
latest_alert:     dict = {}
latest_command:   dict = {}   # Bug B4
_command_lock = threading.Lock()   # BUG FIX: prevent race between GET /api/command and POST /api/command
_smoke_warned = False   # warn about missing MQ-2 only once

# =============================================================================
# UPGRADE 2 — OpenCV Webcam Verification
# =============================================================================
def capture_verification_frame() -> bool:
    """Capture a webcam frame and save to extingo_alert.jpg.
    Reads 5 warm-up frames first so auto-exposure settles.
    Returns True on success, False on failure.
    """
    if not CV2_AVAILABLE:
        print("[VISION] cv2 not available — skipping capture")
        return False

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[VISION] ERROR: Could not open webcam index 0")
        return False

    # Warm-up: discard 5 frames so auto-exposure settles
    for i in range(5):
        ret, _ = cap.read()
        if not ret:
            print(f"[VISION] Warm-up frame {i} failed")

    # Capture final frame
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        print("[VISION] ERROR: Final frame capture failed")
        return False

    cv2.imwrite(ALERT_IMAGE_PATH, frame)
    print(f"[VISION] Frame saved → {ALERT_IMAGE_PATH}")
    return True


# =============================================================================
# UPGRADE 3 — Telegram Bot
# =============================================================================
def send_telegram_photo(caption_text: str) -> None:
    """Send extingo_alert.jpg to the configured Telegram chat."""
    if not REQUESTS_AVAILABLE or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[TELEGRAM] Not configured or requests unavailable — skipping")
        return
    try:
        # BUG FIX: Guard against FileNotFoundError if webcam capture failed or
        # capture_verification_frame() was not called yet.
        if not os.path.isfile(ALERT_IMAGE_PATH):
            print(f"[TELEGRAM] Alert image not found at {ALERT_IMAGE_PATH} — sending text instead")
            send_telegram_message(f"🔥 EXTINGO FIRE ALERT (no image)\n{caption_text}")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        caption = f"<b>🔥 EXTINGO FIRE ALERT</b>\n{caption_text}"
        with open(ALERT_IMAGE_PATH, "rb") as img:
            resp = req_lib.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID,
                      "caption": caption,
                      "parse_mode": "HTML"},
                files={"photo": img},
                timeout=15
            )
        print(f"[TELEGRAM] Photo sent — HTTP {resp.status_code}")
    except Exception as e:
        print(f"[TELEGRAM] ERROR sending photo: {e}")


def send_telegram_message(text: str) -> None:
    """Send a plain-text message to the configured Telegram chat."""
    if not REQUESTS_AVAILABLE or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = req_lib.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10
        )
        print(f"[TELEGRAM] Message sent — HTTP {resp.status_code}")
    except Exception as e:
        print(f"[TELEGRAM] ERROR sending message: {e}")


def set_telegram_webhook() -> None:
    """Register the ngrok webhook URL with Telegram at startup."""
    if not REQUESTS_AVAILABLE or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[TELEGRAM] Webhook not configured — skipping registration")
        return
    try:
        webhook_url = f"{NGROK_URL}/api/webhook"
        # FIX 9: Pass url as a query param dict — requests handles encoding correctly.
        # The previous urllib.parse.quote(safe=':/') could double-encode slashes.
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
        resp = req_lib.get(api_url, params={"url": webhook_url}, timeout=10)
        print(f"[TELEGRAM] Webhook set → {webhook_url}  HTTP {resp.status_code}")
    except Exception as e:
        print(f"[TELEGRAM] ERROR setting webhook: {e}")


# =============================================================================
# UPGRADE 4 — CallMeBot Voice Call
# =============================================================================
def send_callmebot_voice_call(message_text: str) -> None:
    """Trigger a Telegram voice call via CallMeBot API."""
    if not REQUESTS_AVAILABLE or CALLMEBOT_TELEGRAM_USERNAME == "YOUR_TELEGRAM_USERNAME":
        print("[CALLMEBOT] Not configured — skipping")
        return
    try:
        encoded = urllib.parse.quote(message_text)
        url = (f"https://api.callmebot.com/telegram.php"
               f"?user=@{CALLMEBOT_TELEGRAM_USERNAME}&type=voice&text={encoded}")
        resp = req_lib.get(url, timeout=10)
        print(f"[CALLMEBOT] Call triggered — HTTP {resp.status_code}")
    except Exception as e:
        print(f"[CALLMEBOT] ERROR: {e}")


# =============================================================================
# FIRE EVENT HANDLER — centralises post-detection actions
# Runs in a background thread so HTTP response is never delayed.
# =============================================================================
def _fire_event_thread(telemetry_snapshot: dict) -> None:
    """Blocking operations run off the main HTTP thread."""
    # Upgrade 2 — capture
    capture_ok = capture_verification_frame()

    # Upgrade 3 — Telegram photo
    caption = (f"Sensors: flame={telemetry_snapshot.get('flame','?')}  "
               f"motion={telemetry_snapshot.get('motion','?')}  "
               f"heat={telemetry_snapshot.get('heat','?')}")
    if capture_ok:
        send_telegram_photo(caption)
    else:
        send_telegram_message(f"🔥 EXTINGO FIRE ALERT (no image)\n{caption}")

    # Upgrade 4 — voice call
    send_callmebot_voice_call(
        "EMERGENCY. EXTINGO FIRE SYSTEM ALERT. "
        "FIRE DETECTED AT YOUR LOCATION. IMMEDIATE ACTION REQUIRED."
    )


# =============================================================================
# HTTP REQUEST HANDLER
# =============================================================================
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

MIME_TYPES = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
}

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ExtingoHandler(BaseHTTPRequestHandler):

    # ── Silence default request logging ──────────────────────────────────────
    def log_message(self, fmt, *args):
        pass  # Replace with: print(f"[HTTP] {self.address_string()} {fmt % args}")

    # ── CORS helpers ──────────────────────────────────────────────────────────
    def _send_cors(self):
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)

    def _ok(self, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # =========================================================================
    # OPTIONS (CORS preflight)
    # =========================================================================
    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    # =========================================================================
    # =========================================================================
    # GET
    # =========================================================================
    def do_GET(self):
        global latest_command   # <--- ADD THIS LINE HERE
        
        path = self.path.split("?")[0]

        # ── /api/data ─────────────────────────────────────────────────────────
        if path == "/api/data":
            event  = latest_alert.get("event", "")
            flame  = latest_telemetry.get("flame", 0)
            smoke  = latest_telemetry.get("smoke", 0)
            heat   = latest_telemetry.get("heat",  0)

            if event == "fire_detected" or flame == 1 or smoke > 400 or heat > 600:
                status = "EMERGENCY"
            else:
                status = "Normal"

            self._ok({
                "telemetry": latest_telemetry,
                "alert":     latest_alert,
                "status":    status,
                "command":   latest_command,   # B4 — expose command state
            })
            return

        # ── /api/command (B4 — COMMS board polls this) ────────────────────────
        if path == "/api/command":
            # FIX 11: Snapshot and clear in one step so the COMMS board only
            # executes the command once. Without this, spray_on would re-trigger
            # every 2 s indefinitely until a new command overwrites it.
            # BUG FIX: Use _command_lock to prevent race with POST /api/command.
            with _command_lock:
                cmd_snapshot = dict(latest_command)
                latest_command = {}   # clear immediately after read
            self._ok(cmd_snapshot)
            return

        # ── Static file serving ───────────────────────────────────────────────
        if path == "/" or path == "":
            path = "/index.html"

        file_path = os.path.join(STATIC_DIR, path.lstrip("/"))
        if os.path.isfile(file_path):
            ext  = os.path.splitext(file_path)[1].lower()
            mime = MIME_TYPES.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, "Not Found")

    # =========================================================================
    # POST
    # =========================================================================
    def do_POST(self):
        global latest_telemetry, latest_alert, latest_command, _smoke_warned
        path = self.path.split("?")[0]

        # ── /api/telemetry ────────────────────────────────────────────────────
        if path == "/api/telemetry":
            # [BUG FIX S2] Removed duplicate 'global _smoke_warned' here —
            # already declared in the do_POST signature above. Double-declaring
            # is harmless in Python but caused confusing lint warnings.
            data = self._read_body()
            flame  = 1 if data.get("ir",   1) == 0 else 0
            motion = 1 if data.get("pir",  0) == 1 else 0
            heat   = data.get("heat",  0)
            smoke  = data.get("smoke", 0)

            # FIX 8: Only warn once — was printing every 5 s, flooding the console
            if smoke == 0 and not _smoke_warned:
                print("[WARN] smoke=0 — no MQ-2 sensor on COMMS board (warning shown once)")
                _smoke_warned = True

            latest_telemetry = {
                "flame":  flame,
                "motion": motion,
                "heat":   heat,
                "smoke":  smoke,
                "ir_raw":    data.get("ir",    -1),
                "ir_dn_raw": data.get("ir_dn", -1),
            }
            print(f"[TELEMETRY] flame={flame} motion={motion} heat={heat} smoke={smoke}")
            self._ok({"status": "ok"})
            return

        # ── /api/alert ────────────────────────────────────────────────────────
        if path == "/api/alert":
            data  = self._read_body()
            event = data.get("event", "")
            latest_alert = data
            print(f"[ALERT] event={event}")

            if event in ("fire_detected", "rate_of_rise_alert"):
                snap = dict(latest_telemetry)
                # FIX 10: renamed from 't' which shadowed 't = latest_telemetry' in webhook handler
                thread = threading.Thread(target=_fire_event_thread,
                                          args=(snap,), daemon=True)
                thread.start()

            self._ok({"status": "ok"})
            return

        # ── /api/early_alert ──────────────────────────────────────────────────
        if path == "/api/early_alert":
            data  = self._read_body()
            latest_alert = data
            snap  = dict(latest_telemetry)
            # FIX 10: renamed from 't'
            thread = threading.Thread(target=_fire_event_thread,
                                      args=(snap,), daemon=True)
            thread.start()
            self._ok({"status": "ok"})
            return

        # ── /api/command (Bug B4 — dashboard POSTs here) ─────────────────────
        if path == "/api/command":
            data    = self._read_body()
            command = data.get("command", "")
            if command in ("spray_on", "spray_off", "mcb_toggle"):
                # BUG FIX: Use _command_lock to prevent race with GET /api/command.
                with _command_lock:
                    latest_command = {"command": command}
                print(f"[COMMAND] Queued: {command}")
                self._ok({"status": "ok", "command": command})
            else:
                self._ok({"status": "error", "reason": "unknown command"})
            return

        # ── /api/webhook (Upgrade 3 — Telegram bot) ───────────────────────────
        if path == "/api/webhook":
            data = self._read_body()

            # FIX 7: Extract text BEFORE sending the response.
            # _ok() calls end_headers() + wfile.write() — any write attempt after
            # that on a closed pipe raises BrokenPipeError. Parse first, respond second.
            text = ""
            try:
                text = data["message"]["text"].strip()
            except (KeyError, TypeError):
                pass

            self._ok({"ok": True})   # Respond to Telegram within 60 s deadline

            if text == "/start":
                send_telegram_message("Hello! The Extingo Backend Server is online and listening. Type /status to see current sensor readings.")

            elif text == "/status":
                tel = latest_telemetry
                alr = latest_alert
                msg = (
                    f"📡 EXTINGO STATUS\n"
                    f"Flame:  {tel.get('flame','?')}\n"
                    f"Motion: {tel.get('motion','?')}\n"
                    f"Heat:   {tel.get('heat','?')}\n"
                    f"Smoke:  {tel.get('smoke','?')}\n"
                    f"Alert:  {alr.get('event','none')}"
                )
                send_telegram_message(msg)
            return   # [BUG FIX S1] Removed unreachable duplicate /status block that appeared after this return.

        self.send_error(404, "Not Found")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    set_telegram_webhook()   # Upgrade 3 — register webhook at startup

    server = HTTPServer((SERVER_HOST, SERVER_PORT), ExtingoHandler)
    print(f"[SERVER] Extingo v5.6 running on {SERVER_HOST}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down")
        server.server_close()
