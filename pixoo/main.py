"""main.py — loop: อ่าน status.json -> วาด header + หน้าปัจจุบัน -> push ทั้งเฟรม
รันคู่กับ divoom-sync เดิมได้ (คนละ script) หรือรวมเป็นหน้าใน rotation เดียวก็ได้
"""
import json, time, datetime
from pixoo import Pixoo

import renderer as R
from pages import PAGES

PIXOO_IP   = "192.168.41.143"
STATUS_F   = "/run/fr24-watchdog/status.json"
THA_F      = "/run/flight-watcher/inbound.json"   # THA inbound (เขียนโดย flight_watcher.py)
STALE_SEC  = 20 * 60          # ถ้า status เก่ากว่านี้ = ถือว่า stale
THA_STALE_SEC = 5 * 60        # inbound เก่ากว่านี้ = ถือว่าไม่มี THA inbound แล้ว
REFRESH    = 10               # วินาที/เฟรม (โชว์แค่ HH:MM ไม่ต้องถี่)
PAGE_HOLD  = 8                # กี่รอบต่อ 1 หน้า (ตอนมีหน้าเดียวไม่มีผล)


def read_status():
    # --- แหล่งข้อมูล: อ่านไฟล์ local (Pi ตัวเดียวกับ watchdog) ---
    # ถ้าอยู่คนละ Pi เปลี่ยนเป็น HTTP:
    #   import urllib.request; return json.load(urllib.request.urlopen("http://<pi>:PORT/status.json"))
    try:
        with open(STATUS_F) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > STALE_SEC:
            data["health"] = "stale"
        return data
    except Exception:
        return {"health": "stale", "msg_per_s": 0, "aircraft": 0}


def read_inbound():
    # THA inbound ล่าสุด (flight=None หรือ stale = ไม่มี → page โชว์ "no inbound")
    try:
        with open(THA_F) as f:
            d = json.load(f)
        if not d.get("flight") or time.time() - d.get("ts", 0) > THA_STALE_SEC:
            return None
        return d
    except Exception:
        return None


def main():
    pixoo = Pixoo(PIXOO_IP)
    tick = 0
    while True:
        data = read_status()
        data["tha"] = read_inbound()
        page = PAGES[(tick // PAGE_HOLD) % len(PAGES)]

        img, d = R.new_frame()
        R.draw_header(d, datetime.datetime.now())
        R.draw_status_frame(d, data.get("health", "stale"))
        page(d, data)

        pixoo.draw_image(img)     # ปรับตาม API lib ของคุณถ้าต่าง (บางรุ่น draw_image_at_location)
        pixoo.push()

        tick += 1
        time.sleep(REFRESH)


if __name__ == "__main__":
    main()
