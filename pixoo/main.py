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
FAN_F      = "/run/adsb-ha/fan.json"                # สถานะพัดลม (เขียนโดย mqtt_publish.py)
STALE_SEC  = 20 * 60          # ถ้า status เก่ากว่านี้ = ถือว่า stale
THA_STALE_SEC = 5 * 60        # inbound เก่ากว่านี้ = ถือว่าไม่มี THA inbound แล้ว
AGENDA_STALE_SEC = 6 * 3600   # agenda เก่ากว่านี้ (fetch ตายไปนาน) = ไม่โชว์
FAN_STALE_SEC = 5 * 60        # fan.json เก่ากว่านี้ (bridge ตาย) = ไม่รู้สถานะ
REFRESH    = 10               # วินาที/รอบ อ่านข้อมูล /run ใหม่ (การอ่าน/subprocess แพง ไม่ทำถี่)
ANIM_FPS   = 2                # เฟรม/วินาที สำหรับ animation (scanner) — push ถี่ขึ้นแต่ข้อมูลคงเดิม
PAGE_HOLD  = 8                # กี่รอบข้อมูลต่อ 1 หน้า (1 รอบ = REFRESH วินาที)
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


def read_throttled():
    # vcgencmd get_throttled → int bitmask ("throttled=0x50005") หรือ None (ไม่มีสิทธิ์/ไม่ใช่ Pi)
    # ต้องอยู่กลุ่ม video: sudo usermod -aG video arin (ไม่งั้นได้ None)
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.split("=")[1], 16)
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


def read_fan():
    # สถานะพัดลม: True=หมุน / False=ปิด / None=ไม่รู้ (ไม่มีไฟล์ / stale / bridge ยังไม่ตั้ง)
    try:
        with open(FAN_F) as f:
            d = json.load(f)
        if time.time() - d.get("ts", 0) > FAN_STALE_SEC:
            return None
        return d.get("on")
    except Exception:
        return None


def main():
    pixoo = Pixoo(PIXOO_IP)
    frames_per_refresh = max(1, int(REFRESH * ANIM_FPS))
    anim_sleep = 1.0 / ANIM_FPS
    tick = 0        # นับรอบข้อมูล (ใช้เลือกหน้า)
    phase = 0       # เฟรม animation สะสม (ใช้ขยับ scanner)
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
        data["fan"] = read_fan()                           # สถานะพัดลมระบายความร้อน (จาก HA/Tuya)
        data["throttled"] = read_throttled()               # undervoltage / thermal throttle (Pi)
        page = PAGES[(tick // PAGE_HOLD) % len(PAGES)]
        health = data.get("health", "stale")
        scan_col = R.HEALTH.get(health, R.HEALTH["stale"])

        # push หลายเฟรมต่อ 1 รอบข้อมูล — เฉพาะ scanner (+ เวลา) ที่ขยับ, เนื้อหาหน้าคงเดิม
        for _ in range(frames_per_refresh):
            img, d = R.new_frame()
            R.draw_header(d, datetime.datetime.now())
            R.draw_status_frame(d, health)
            page(d, data)
            R.draw_scanner(d, phase, scan_col)             # ส่วนเคลื่อนไหว (ทุกหน้า)
            if ROTATE:
                img = img.rotate(ROTATE)                   # จอติดกลับหัว → หมุนชดเชย
            try:
                pixoo.draw_image(img)
                pixoo.push()
            except Exception as e:
                # Pixoo หลุด network (เคยเจอ No route to host) → อย่า crash/spin เร็ว
                print("pixoo push failed:", e)
                time.sleep(REFRESH)
                break
            phase += 1
            time.sleep(anim_sleep)

        tick += 1


if __name__ == "__main__":
    main()
