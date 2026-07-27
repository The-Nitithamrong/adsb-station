"""main.py — loop: อ่าน status.json -> วาด header + หน้าปัจจุบัน -> push ทั้งเฟรม
รันคู่กับ divoom-sync เดิมได้ (คนละ script) หรือรวมเป็นหน้าใน rotation เดียวก็ได้
"""
import json, time, datetime, subprocess
from pixoo import Pixoo

import renderer as R
from pages import PAGES

PIXOO_IP   = "192.168.41.143"
STATUS_F   = "/run/fr24-watchdog/status.json"
THA_F      = "/run/flight-watcher/inbound.json"   # THA inbound (เขียนโดย flight_watcher.py)
AGENDA_F   = "/run/agenda/next.json"               # เที่ยวบินถัดไป (เขียนโดย agenda_fetch.py)
STALE_SEC  = 20 * 60          # ถ้า status เก่ากว่านี้ = ถือว่า stale
THA_STALE_SEC = 5 * 60        # inbound เก่ากว่านี้ = ถือว่าไม่มี THA inbound แล้ว
AGENDA_STALE_SEC = 6 * 3600   # agenda เก่ากว่านี้ (fetch ตายไปนาน) = ไม่โชว์
REFRESH    = 10               # วินาที/เฟรม (โชว์แค่ HH:MM ไม่ต้องถี่)
PAGE_HOLD  = 8                # กี่รอบต่อ 1 หน้า (ตอนมีหน้าเดียวไม่มีผล)
UPTIME_SVC = "fr24feed"       # service ที่โชว์ uptime บนหน้า UP (FDR) — เปลี่ยนเป็น flight-watcher ได้
ROTATE     = 180              # องศาหมุนเฟรมก่อน push (จอติดกลับหัว = 180; ปกติ = 0)


def read_uptime():
    # วินาทีตั้งแต่ Pi boot ล่าสุด (/proc/uptime — world-readable)
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return 0


def read_temp():
    # อุณหภูมิ CPU Pi (°C) จาก thermal_zone0 (millidegree)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def read_svc_uptime(svc, sys_uptime_s):
    # วินาทีที่ service active มาแล้ว = uptime ระบบ − เวลาที่ service เข้าสถานะ active (monotonic)
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", svc],
            capture_output=True, text=True, timeout=5).stdout.strip()
        mono_us = int(out)
        if mono_us <= 0:
            return None
        return max(0, int(sys_uptime_s - mono_us / 1_000_000))
    except Exception:
        return None


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
    # inbound.json ทั้งก้อน (THA soonest + list เครื่องที่รับได้) — {} ถ้า stale/อ่านไม่ได้
    try:
        with open(THA_F) as f:
            d = json.load(f)
        if time.time() - d.get("ts", 0) > THA_STALE_SEC:
            return {}
        return d
    except Exception:
        return {}


def read_agenda():
    # next.json (เที่ยวบินถัดไป) — คืน dict พร้อม in_min ที่คำนวณสดจาก start_ts, หรือ None
    try:
        with open(AGENDA_F) as f:
            a = json.load(f)
        if time.time() - a.get("ts", 0) > AGENDA_STALE_SEC or not a.get("summary"):
            return None
        st = a.get("start_ts")
        if st is not None:
            a["in_min"] = int((st - time.time()) / 60)   # นับถอยหลังสด (ไม่ใช้ค่าเก่าตอน fetch)
        return a
    except Exception:
        return None


def main():
    pixoo = Pixoo(PIXOO_IP)
    tick = 0
    while True:
        data = read_status()
        inb = read_inbound()
        data["tha"] = inb if inb.get("flight") else None   # THA inbound (หรือ None)
        data["flights"] = inb.get("list", [])              # list เครื่องที่รับได้
        data["nrx"] = inb.get("nrx", 0)
        up = read_uptime()                                 # uptime + เวลา boot ล่าสุด
        data["uptime_s"] = up
        data["boot_str"] = (datetime.datetime.now() - datetime.timedelta(seconds=up)).strftime("%d/%m %H:%M")
        data["temp_c"] = read_temp()                       # อุณหภูมิ CPU
        data["svc_uptime_s"] = read_svc_uptime(UPTIME_SVC, up)
        data["svc_name"] = "FDR"
        data["agenda"] = read_agenda()                     # เที่ยวบินถัดไป (Google Calendar)
        page = PAGES[(tick // PAGE_HOLD) % len(PAGES)]

        img, d = R.new_frame()
        R.draw_header(d, datetime.datetime.now())
        R.draw_status_frame(d, data.get("health", "stale"))
        page(d, data)

        if ROTATE:
            img = img.rotate(ROTATE)     # จอติดกลับหัว → หมุนเฟรมชดเชย
        pixoo.draw_image(img)     # ปรับตาม API lib ของคุณถ้าต่าง (บางรุ่น draw_image_at_location)
        pixoo.push()

        tick += 1
        time.sleep(REFRESH)


if __name__ == "__main__":
    main()
