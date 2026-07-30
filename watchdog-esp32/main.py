"""main.py — ESP32 external watchdog (MicroPython): เฝ้า Pi — ถ้าหาย > DOWN_S → power-cycle ปลั๊ก Tuya ที่จ่ายไฟ Pi.

ชั้นป้องกันสุดท้าย (layer 4): กู้ได้แม้ Pi ค้างทั้ง kernel (ไฟเขียวค้าง) — ซึ่ง watchdog ในเครื่องเองกู้ไม่ได้
เพราะเครื่องแข็งตายไปด้วย. เช็ค LAN ตรงๆ (TCP ไป Pi:22) ไม่พึ่ง cloud → ง่าย+ทน.

⚠️ ความปลอดภัย:
- ESP32 ต้องเสียบไฟ **คนละแหล่ง** กับ Pi — ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด (ไม่งั้นตัดไฟตัวเองตาย!)
- ตั้ง "power-on state" ของปลั๊ก Tuya = ON (หรือ last) ในแอป → Pi ได้ไฟกลับเสมอแม้ ESP32 รีบูต
- power-cycle = hard reset (เสี่ยงไฟล์พัง) → ตั้ง DOWN_S/COOLDOWN นานพอให้เป็น "ทางสุดท้าย" ไม่ใช่ตัวหลัก
"""
import network, socket, time
try:
    import ntptime
except ImportError:
    ntptime = None
import tuya33
import config as C


def log(*a):
    print("%d" % time.ticks_ms(), *a)


def wifi_connect():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if not w.isconnected():
        w.connect(C.WIFI_SSID, C.WIFI_PASS)
        for _ in range(30):                    # รอ associate สูงสุด 30 วิ
            if w.isconnected():
                break
            time.sleep(1)
    return w.isconnected()


def pi_alive():
    """TCP connect Pi:PORT — True ถ้าต่อได้ = Pi ยังมีชีวิตระดับ network (ไม่ใช่ค้าง/WiFi ตาย)."""
    s = socket.socket()
    try:
        s.settimeout(C.TCP_TIMEOUT)
        s.connect((C.PI_IP, C.PI_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def power_cycle():
    log("power-cycle: OFF")
    for _ in range(3):                         # retry เผื่อ frame แรกหาย
        if tuya33.set_switch(C.TUYA_IP, C.TUYA_ID, C.TUYA_KEY, False, C.TUYA_DP):
            break
        time.sleep(2)
    time.sleep(C.OFF_SECONDS)
    log("power-cycle: ON")
    for _ in range(3):
        if tuya33.set_switch(C.TUYA_IP, C.TUYA_ID, C.TUYA_KEY, True, C.TUYA_DP):
            break
        time.sleep(2)


def main():
    while not wifi_connect():
        log("wifi ยังไม่ติด — retry")
        time.sleep(5)
    if ntptime:
        try:
            ntptime.settime()                  # เวลาไว้ใส่ field 't' ของ Tuya (best-effort)
        except Exception:
            pass
    log("watchdog started — เฝ้า", C.PI_IP)

    last_ok = time.ticks_ms()
    last_cycle = time.ticks_add(last_ok, -C.COOLDOWN_S * 1000)   # ยอม cycle ครั้งแรกได้ทันที
    misses = 0
    w = network.WLAN(network.STA_IF)

    while True:
        # WiFi ของ ESP32 เองหลุด (router รีบูต) → reconnect + รีเซ็ตตัวนับ (อย่าโทษ Pi ตอนที่ตัวเองก็ออฟไลน์)
        if not w.isconnected():
            log("wifi ตัวเองหลุด — reconnect")
            wifi_connect()
            last_ok = time.ticks_ms()
            time.sleep(C.CHECK_S)
            continue

        if pi_alive():
            if misses:
                log("Pi กลับมา ok")
            misses = 0
            last_ok = time.ticks_ms()
        else:
            misses += 1
            down_s = time.ticks_diff(time.ticks_ms(), last_ok) // 1000
            cool_s = time.ticks_diff(time.ticks_ms(), last_cycle) // 1000
            log("Pi เงียบ", down_s, "วิ (miss", misses, ")")
            if down_s >= C.DOWN_S and cool_s >= C.COOLDOWN_S:
                log("เงียบเกิน", C.DOWN_S, "วิ → power-cycle Pi")
                power_cycle()
                last_cycle = time.ticks_ms()
                last_ok = time.ticks_ms()      # ให้ Pi boot ก่อนเริ่มนับใหม่
                misses = 0

        time.sleep(C.CHECK_S)


main()
