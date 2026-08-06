#!/usr/bin/env python3
"""Tiny HTTP endpoint so the ESP32 display watchdog can show the Pi's REAL uptime.

WHY: the ESP32 only pings the Pi's port 22 (liveness) — it can't read /proc/uptime.
This serves it: GET /  ->  plain text = integer uptime seconds (from /proc/uptime).
Plain text (not JSON) = trivial to parse on the ESP32 (atol the body).

stdlib only. Runs as adsb-uptime.service (Type=simple, Restart=always).
Display-only + best-effort — NOT the watchdog's liveness check (that stays TCP:22,
because an app-level server can crash while the Pi itself is fine → false reset).
"""
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8099   # LAN only; ต้องตรงกับ PI_INFO_PORT ฝั่ง ESP32

# คุม coffee break ของ Pixoo จากปุ่มบน ESP32: endpoint นี้เขียนไฟล์, pixoo/main.py อ่าน (ทั้งคู่ User=arin).
# "1"/"0" — persistent ข้าม reboot. ไฟล์หาย = ถือว่า off (ตรงกับ COFFEE_ENABLE default)
COFFEE_FILE = "/home/arin/pixoo_coffee"


def uptime_s():
    with open("/proc/uptime") as f:
        return int(float(f.read().split()[0]))


def coffee_on():
    try:
        return open(COFFEE_FILE).read().strip() == "1"
    except OSError:
        return False


def set_coffee(on):
    with open(COFFEE_FILE, "w") as f:
        f.write("1" if on else "0")


class Handler(BaseHTTPRequestHandler):
    def _text(self, s):
        body = (str(s) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        try:
            if p == "/":                          # uptime (เดิม)
                self._text(uptime_s())
            elif p == "/coffee":                  # อ่าน state
                self._text(1 if coffee_on() else 0)
            elif p == "/coffee/toggle":           # สลับ
                set_coffee(not coffee_on()); self._text(1 if coffee_on() else 0)
            elif p == "/coffee/on":
                set_coffee(True); self._text(1)
            elif p == "/coffee/off":
                set_coffee(False); self._text(0)
            else:
                self.send_error(404)
        except Exception:
            self.send_error(500)

    def log_message(self, *a):
        pass   # เงียบ — ไม่ต้อง log ทุก request (ESP32 ยิงทุก 30 วิ)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
