"""main.py — loop: อ่าน status.json -> วาด header + หน้าปัจจุบัน -> push ทั้งเฟรม
รันคู่กับ divoom-sync เดิมได้ (คนละ script) หรือรวมเป็นหน้าใน rotation เดียวก็ได้
"""
import json, time, datetime, subprocess, urllib.request, socket
import requests
from pixoo import Pixoo

import renderer as R
from pages import PAGES, coffee_break, knock_off

PIXOO_IP   = "192.168.41.143"
STATUS_F   = "/run/fr24-watchdog/status.json"
THA_F      = "/run/flight-watcher/inbound.json"   # THA inbound (เขียนโดย flight_watcher.py)
AGENDA_F   = "/run/agenda/next.json"               # เที่ยวบินถัดไป (เขียนโดย agenda_fetch.py)
FAN_F      = "/run/adsb-ha/fan.json"                # สถานะพัดลม (เขียนโดย mqtt_publish.py)
COFFEE_FILE = "/home/arin/pixoo_coffee"            # ปุ่ม coffee บน ESP32 เขียนผ่าน uptime_server (:8099) → runtime on/off
STALE_SEC  = 20 * 60          # ถ้า status เก่ากว่านี้ = ถือว่า stale
THA_STALE_SEC = 5 * 60        # inbound เก่ากว่านี้ = ถือว่าไม่มี THA inbound แล้ว
AGENDA_STALE_SEC = 6 * 3600   # agenda เก่ากว่านี้ (fetch ตายไปนาน) = ไม่โชว์
FAN_STALE_SEC = 5 * 60        # fan.json เก่ากว่านี้ (bridge ตาย) = ไม่รู้สถานะ
REFRESH    = 10               # วินาที/รอบ อ่านข้อมูล /run ใหม่ (การอ่าน/subprocess แพง ไม่ทำถี่)
ANIM_FPS   = 2                # เฟรม/วินาที สำหรับ animation (scanner) — push ถี่ขึ้นแต่ข้อมูลคงเดิม
PUSH_TIMEOUT = 5              # วินาที — timeout ทุก HTTP ไป Pixoo (กัน push ค้างตอน WiFi หลุด → block ตลอดกาล)
PUSH_FAIL_RECONNECT = 3       # push fail ติดกันกี่ครั้ง → สร้าง Pixoo() ใหม่ (reconnect + reset frame counter)
PAGE_HOLD  = 8                # กี่รอบข้อมูลต่อ 1 หน้า (1 รอบ = REFRESH วินาที)
UPTIME_SVC = "fr24feed"       # service ที่โชว์ uptime บนหน้า UP (FDR) — เปลี่ยนเป็น flight-watcher ได้
ROTATE     = 0                # องศาหมุนเฟรมก่อน push (จอติดกลับหัว = 180; ปกติ = 0)
COFFEE_ENABLE  = False       # ปิด coffee break (ทั้ง buzzer + หน้ากาแฟ). True = เปิดกลับ
COFFEE_START_H = 8            # หน้า coffee break เตือนช่วง [START:00..END:00] (เวลาเครื่อง = BKK)
COFFEE_END_H   = 20
COFFEE_EVERY_MIN = 60         # เตือนทุกกี่นาที (60 = ทุกชั่วโมงตรง :00)
COFFEE_SHOW_SEC = 30          # โชว์หน้ากาแฟนานเท่าไหร่ต่อครั้ง
KNOCKOFF_H = 22               # เตือน "เลิกงาน" 22:00 (4 ทุ่ม) — ครั้งเดียว/วัน (เวลาเครื่อง = BKK)
KNOCKOFF_SHOW_SEC = 60        # โชว์หน้าเลิกงานนานเท่าไหร่ (นานกว่า coffee — เตือนสำคัญ)
NAP_BEFORE_H = 24             # nap mode: เงียบ buzzer อัตโนมัติในช่วงกี่ชม.ก่อน event ถัดไปใน Google Calendar
#   (agenda_fetch เขียน /run/agenda/next.json) — กันปลุกตอนงีบก่อนบิน. 0 = ปิดฟีเจอร์นี้
# Divoom PlayBuzzer ใช้ ActiveTimeInCycle/OffTimeInCycle (ไม่ใช่ PlayPulseTime/PlayOffTime — ชื่อผิด=เงียบ).
# on 500ms / off 500ms วนจน PlayTotalTime=5000 → บี๊บเว้นจังหวะ ~5 บี๊บ ใน 5 วิ. (POST เดียว, เครื่องเล่นเอง)
COFFEE_BUZZ = {"Command": "Device/PlayBuzzer",
               "ActiveTimeInCycle": 500, "OffTimeInCycle": 500, "PlayTotalTime": 5000}


# --- robustness: pixoo lib เรียก requests แบบไม่ตั้ง timeout → WiFi/router สะดุดกลาง push = block ตลอดกาล
#     (จอค้างเฟรมเดิม, service ยัง active แต่ loop ตายอยู่ที่ socket — ไม่ throw, except เดิมเลยไม่ทำงาน).
#     ยัด default timeout ให้ requests ทุกตัว (patch Session.request ครอบทั้ง requests.post/session) →
#     push ค้างจะ raise Timeout หลัง PUSH_TIMEOUT → except จับได้ → retry/reconnect. + socket default กันเผื่อ.
socket.setdefaulttimeout(PUSH_TIMEOUT)
_orig_request = requests.Session.request
def _request_with_timeout(self, *a, **kw):
    kw.setdefault("timeout", PUSH_TIMEOUT)
    return _orig_request(self, *a, **kw)
requests.Session.request = _request_with_timeout


def buzz(ip):
    # สั่ง buzzer บน Pixoo (Divoom API) — fire-and-forget, เครื่องเล่น pattern เอง ~5 วิ
    try:
        req = urllib.request.Request(f"http://{ip}/post", data=json.dumps(COFFEE_BUZZ).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("buzz failed:", e)


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


def coffee_enabled():
    # runtime on/off จากปุ่ม ESP32 (uptime_server เขียน COFFEE_FILE) — ไฟล์หาย = ใช้ค่า COFFEE_ENABLE
    try:
        return open(COFFEE_FILE).read().strip() == "1"
    except OSError:
        return COFFEE_ENABLE


def main():
    pixoo = Pixoo(PIXOO_IP)
    frames_per_refresh = max(1, int(REFRESH * ANIM_FPS))
    anim_sleep = 1.0 / ANIM_FPS
    fails = 0       # นับ push fail ติดกัน → ถึง PUSH_FAIL_RECONNECT แล้วสร้าง Pixoo() ใหม่
    tick = 0        # นับรอบข้อมูล (ใช้เลือกหน้า)
    phase = 0       # เฟรม animation สะสม (ใช้ขยับ scanner)
    _n = datetime.datetime.now()                               # init slot ทุก 30 นาที (กัน beep ซ้ำ/ตอน restart)
    last_slot = f"{_n:%Y%m%d}-{(_n.hour * 60 + _n.minute) // COFFEE_EVERY_MIN}"
    coffee_until = 0.0                                          # โชว์หน้ากาแฟจนถึง ts นี้
    knockoff_until = 0.0                                        # โชว์หน้าเลิกงานจนถึง ts นี้
    knockoff_min = KNOCKOFF_H * 60                             # 22:00 เป็นนาทีของวัน
    # init: ถ้าเริ่มมาหลัง 22:00 แล้ว = ถือว่าเตือนไปแล้ววันนี้ (กัน restart ดึกๆ แล้วเด้งซ้ำ)
    last_knockoff_day = f"{_n:%Y%m%d}" if (_n.hour * 60 + _n.minute) >= knockoff_min else ""
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

        # nap mode: เงียบ buzzer อัตโนมัติในช่วง NAP_BEFORE_H ชม. ก่อน event ถัดไป (งีบก่อนบิน)
        _ag = data.get("agenda")
        napping = bool(NAP_BEFORE_H and _ag and 0 <= _ag.get("in_min", 1 << 30) <= NAP_BEFORE_H * 60)

        now_dt = datetime.datetime.now()
        mins = now_dt.hour * 60 + now_dt.minute
        # coffee break: ทุกชั่วโมง ช่วง 08:00–20:00 → beep + โชว์หน้ากาแฟ (ข้ามถ้า napping ·
        # เปิด/ปิด runtime ด้วยปุ่มบน ESP32 → COFFEE_FILE; ไฟล์หาย = ใช้ค่า COFFEE_ENABLE)
        coffee_on = coffee_enabled()
        if coffee_on:
            slot = f"{now_dt:%Y%m%d}-{mins // COFFEE_EVERY_MIN}"
            if slot != last_slot:
                if COFFEE_START_H * 60 <= mins <= COFFEE_END_H * 60 and not napping:
                    coffee_until = time.time() + COFFEE_SHOW_SEC
                    buzz(PIXOO_IP)
                last_slot = slot

        # เลิกงาน: ครั้งเดียว/วัน เมื่อนาฬิกาถึง 22:00 → beep + โชว์หน้าเลิกงาน (ข้ามถ้า napping)
        today = f"{now_dt:%Y%m%d}"
        if today != last_knockoff_day and mins >= knockoff_min and not napping:
            knockoff_until = time.time() + KNOCKOFF_SHOW_SEC
            buzz(PIXOO_IP)
            last_knockoff_day = today

        # ลำดับความสำคัญ: เลิกงาน > coffee > รอบหมุนปกติ
        now_t = time.time()
        if now_t < knockoff_until:
            page = knock_off
        elif now_t < coffee_until:
            page = coffee_break
        else:
            page = PAGES[(tick // PAGE_HOLD) % len(PAGES)]
        health = data.get("health", "stale")
        scan_col = R.HEALTH.get(health, R.HEALTH["stale"])

        # push หลายเฟรมต่อ 1 รอบข้อมูล — เฉพาะ scanner (+ เวลา) ที่ขยับ, เนื้อหาหน้าคงเดิม
        for _ in range(frames_per_refresh):
            data["anim"] = phase                           # เฟรม animation ให้หน้าใช้ (พัดลมหมุน ฯลฯ)
            img, d = R.new_frame()
            R.draw_header(d, datetime.datetime.now())
            R.draw_status_frame(d, health)
            page(d, data)
            R.draw_scanner(d, phase, scan_col)             # ส่วนเคลื่อนไหว (ทุกหน้า)
            if napping:
                R.draw_nap(d)                              # 🌙z มุมขวาบน = โหมดงีบ (buzzer เงียบ)
            if ROTATE:
                img = img.rotate(ROTATE)                   # จอติดกลับหัว → หมุนชดเชย
            try:
                pixoo.draw_image(img)
                pixoo.push()
                fails = 0
            except Exception as e:
                # Pixoo หลุด network (WiFi/router สะดุด, No route to host, timeout) → อย่า crash/spin เร็ว
                fails += 1
                print(f"pixoo push failed ({fails}):", e)
                if fails >= PUSH_FAIL_RECONNECT:
                    try:
                        pixoo = Pixoo(PIXOO_IP)   # สร้างใหม่ = reconnect + reset frame counter (กัน counter desync)
                        fails = 0
                        print("pixoo reconnected")
                    except Exception as e2:
                        print("pixoo reconnect failed:", e2)   # ยังหลุดอยู่ — รอบหน้าค่อยลองใหม่
                time.sleep(REFRESH)
                break
            phase += 1
            time.sleep(anim_sleep)

        tick += 1


if __name__ == "__main__":
    main()
