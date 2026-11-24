#!/usr/bin/env python3
"""Tiny HTTP endpoint so the ESP32 display watchdog can show the Pi's REAL uptime.

WHY: the ESP32 only pings the Pi's port 22 (liveness) — it can't read /proc/uptime.
This serves it: GET /  ->  plain text = integer uptime seconds (from /proc/uptime).
Plain text (not JSON) = trivial to parse on the ESP32 (atol the body).

GET /weather -> the forecast weather_fetch.py already wrote (/run/weather/forecast.json), re-shaped
as plain text lines for the ESP32 panel's screensaver (see weather_text()). Same reason as "/":
the ESP32 parses it with sscanf, no JSON library needed. The Pi stays the ONLY thing that talks to
TMD / Open-Meteo — no token and no home coordinates ever go onto the ESP32.

stdlib only. Runs as adsb-uptime.service (Type=simple, Restart=always).
Display-only + best-effort — NOT the watchdog's liveness check (that stays TCP:22,
because an app-level server can crash while the Pi itself is fine → false reset).
"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8099   # LAN only; ต้องตรงกับ PI_INFO_PORT ฝั่ง ESP32

# คุม coffee break ของ Pixoo จากปุ่มบน ESP32: endpoint นี้เขียนไฟล์, pixoo/main.py อ่าน (ทั้งคู่ User=arin).
# "1"/"0" — persistent ข้าม reboot. ไฟล์หาย = ถือว่า off (ตรงกับ COFFEE_ENABLE default)
COFFEE_FILE = "/home/arin/pixoo_coffee"

# พยากรณ์อากาศ (เขียนโดย weather/weather_fetch.py ทุก ~1 ชม.) — endpoint นี้แค่ "อ่านแล้วจัดรูป" ไม่ดึงเอง
WX_FILE = "/run/weather/forecast.json"
WX_KINDS = {"clear", "partly", "cloudy", "rain", "heavy", "storm", "fog", "snow", "cool", "hot"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _word(v, n):
    """ข้อความ → token ASCII ล้วน ไม่มีช่องว่าง ยาวไม่เกิน n ('-' = ไม่มีค่า).
    ESP32 แยก field ด้วยช่องว่าง + font บนจอมีแต่ ASCII → ตัวอื่น (เช่น label ภาษาไทย) ตัดทิ้งตรงนี้"""
    return re.sub(r"[^A-Za-z0-9]", "", str(v or ""))[:n] or "-"


def _int(v):
    """ปัดแบบเดียวกับหน้า WX ของ Pixoo (int(round())) → สองจอโชว์เลขเดียวกันเสมอ"""
    return "-" if not isinstance(v, (int, float)) or isinstance(v, bool) else str(int(round(v)))


def _wx_line(tag, src, label, day):
    d = day or {}
    date = d.get("date") if _DATE_RE.match(str(d.get("date") or "")) else "-"
    kind = d.get("kind") if d.get("kind") in WX_KINDS else "-"
    mm = d.get("rain_mm")
    mm = "-" if not isinstance(mm, (int, float)) or isinstance(mm, bool) else f"{mm:.1f}"
    return " ".join([tag, _word(src, 4), _word(label, 6), date, kind,
                     _int(d.get("tmax")), _int(d.get("tmin")), mm, _int(d.get("rain_pct"))])


def weather_text(path=WX_FILE):
    """forecast.json → บรรทัดข้อความ (field คั่นด้วยช่องว่าง, '-' = ไม่มีค่า):
        wx1 <ts>                                                     หัว: รุ่นของ format + ts ของไฟล์
        H <src> <label> <date> <kind> <tmax> <tmin> <rain_mm> <rain_pct>   บ้าน — 1 บรรทัดต่อวัน
        D <src> <label> <date> <kind> <tmax> <tmin> <rain_mm> <rain_pct>   ปลายทาง — วันที่บินถึง
    ส่ง "ทุกวัน" ของบ้านพร้อมวันที่ ให้ ESP32 เลือกวันเองตามนาฬิกาของมัน (เหมือน Pixoo): ข้ามเที่ยงคืน
    แล้วเปลี่ยนวันเอง และ Pi ดับไปครึ่งวันจอก็ยังมีของวันนี้โชว์. ไม่ตัดสิน "เก่าเกินไป" ที่นี่ —
    ส่ง ts ไปให้ปลายทางตัดสิน (ไฟล์ไม่มี/พัง → "wx1 0" = ไม่มีข้อมูล ซึ่งต่างจากดึงไม่ได้)."""
    try:
        with open(path, encoding="utf-8") as f:
            w = json.load(f)
        ts = w.get("ts")
        lines = [f"wx1 {int(ts) if isinstance(ts, (int, float)) else 0}"]
        home = w.get("home") or {}
        for day in (home.get("days") or [])[:4]:
            lines.append(_wx_line("H", home.get("src"), home.get("label"), day))
        dest = w.get("dest")
        if dest:
            hit = next((d for d in dest.get("days") or [] if d.get("date") == dest.get("date")), None)
            # ไม่มีพยากรณ์ของวันที่บินถึง (ไกลเกิน/ดึงไม่ได้) → ส่งแค่ชื่อเมือง+วันที่ ค่าที่เหลือ '-' ไม่เดา
            lines.append(_wx_line("D", dest.get("src"), dest.get("label"), hit or {"date": dest.get("date")}))
        return "\n".join(lines)
    except Exception:
        # ไฟล์หาย / JSON พัง / โครงสร้างไม่ใช่ที่คาด (เช่น days เป็น dict) — ทุกแบบแปลว่า "ไม่มีข้อมูลที่ใช้ได้".
        # จับกว้างโดยตั้งใจ: route นี้มีไว้โชว์อย่างเดียว และ ESP32 เก็บชุดเดิมไว้เมื่อได้ "wx1 0" (ไม่เอาไปทับ)
        return "wx1 0"


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
            elif p == "/weather":                 # พยากรณ์ บ้าน+ปลายทาง สำหรับ screensaver ของ ESP32
                self._text(weather_text())
            else:
                self.send_error(404)
        except Exception:
            self.send_error(500)

    def log_message(self, *a):
        pass   # เงียบ — ไม่ต้อง log ทุก request (ESP32 ยิงทุก 30 วิ)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
