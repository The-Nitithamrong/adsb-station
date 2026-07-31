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


def uptime_s():
    with open("/proc/uptime") as f:
        return int(float(f.read().split()[0]))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = f"{uptime_s()}\n".encode()
        except Exception:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass   # เงียบ — ไม่ต้อง log ทุก request (ESP32 ยิงทุก 30 วิ)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
