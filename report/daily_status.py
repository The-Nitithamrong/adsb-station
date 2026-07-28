#!/usr/bin/env python3
"""daily_status.py — สรุปสถานะสถานี (Pi + FR24 feeder) → Telegram วันละครั้ง (09:00 BKK).

แทน THA-inbound alert ต่อเที่ยว (noise) ด้วย digest สั้นๆ วันละครั้ง: feeder health/rate/aircraft
(จาก /run/fr24-watchdog/status.json) + Pi uptime/อุณหภูมิ/undervoltage-throttle/load/disk/RAM.
stdlib ล้วน (urllib) — reuse TG_API/TG_CHAT ใน /etc/fr24-watchdog.env. รันโดย systemd timer 09:00.
(watchdog station-down alert ยังทำงานแยกตามเดิม — อันนี้เป็น heartbeat ไม่ใช่ตัวแทน)
"""
import json, os, socket, subprocess, urllib.parse, urllib.request
import time as _time

ENV_FILE = "/etc/fr24-watchdog.env"
STATUS_F = "/run/fr24-watchdog/status.json"
STALE_SEC = 20 * 60          # status.json เก่ากว่านี้ = watchdog ไม่เดิน → stale
_THR = [(0x1, "undervoltage"), (0x4, "throttled"), (0x2, "freq-capped"), (0x8, "soft-temp")]
FEED_EMOJI = {"ok": "✅", "recovering": "⚠️", "dead": "🔴", "stale": "❓"}


def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


ENV = load_env(ENV_FILE)
TG_API, TG_CHAT = ENV.get("TG_API"), ENV.get("TG_CHAT")


def read_status():
    try:
        with open(STATUS_F) as f:
            d = json.load(f)
        if _time.time() - d.get("ts", 0) > STALE_SEC:
            d["health"] = "stale"
        return d
    except (OSError, ValueError):
        return {"health": "stale", "msg_per_s": 0, "aircraft": 0}


def read_uptime_s():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return 0


def fmt_uptime(s):
    d, r = divmod(int(s), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}วัน {h}ชม"
    if h:
        return f"{h}ชม {m}น"
    return f"{m}น"


def read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def read_throttle():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        raw = int(out.split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    now = [name for bit, name in _THR if raw & bit]
    return ",".join(now) if now else "ok"


def read_load():
    try:
        with open("/proc/loadavg") as f:
            return f.read().split()[0]
    except OSError:
        return "?"


def read_disk_pct():
    try:
        st = os.statvfs("/")
        used = (st.f_blocks - st.f_bfree) / st.f_blocks * 100
        return f"{used:.0f}%"
    except (OSError, ZeroDivisionError):
        return "?"


def read_mem_free():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return f"{int(ln.split()[1]) / 1024 / 1024:.1f}G"
    except (OSError, ValueError):
        pass
    return "?"


def build_message():
    st = read_status()
    health = st.get("health", "stale")
    up = read_uptime_s()
    temp = read_temp()
    thr = read_throttle()
    tstr = f"{temp:.0f}°C" if temp is not None else "?"
    thr_str = ("⚡ok" if thr == "ok" else f"⚠️ {thr}") if thr is not None else "⚡?"
    host = socket.gethostname()
    fan_line = ""
    try:
        import fan_stats
        fc, fm = fan_stats.today_stats()
        h, mm = divmod(fm, 60)
        dur = f"{h}ชม {mm}น" if h else f"{mm}น"
        fan_line = f"\n🌀 พัดลมวันนี้: {fc} ครั้ง · รวม {dur}"
    except Exception:
        pass
    return (
        f"📡 ADS-B {host} — {_time.strftime('%Y-%m-%d')}\n"
        f"{FEED_EMOJI.get(health, '❓')} Feeder: {health} · {st.get('msg_per_s', 0)} msg/s · "
        f"{st.get('aircraft', 0)} ลำ\n"
        f"🖥 Pi: up {fmt_uptime(up)} · {tstr} · {thr_str}\n"
        f"📊 load {read_load()} · disk {read_disk_pct()} · RAM {read_mem_free()} ว่าง"
        f"{fan_line}"
    )


def send(text):
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    urllib.request.urlopen(TG_API, data=data, timeout=15)


def main():
    msg = build_message()
    print(msg)
    if not TG_API:
        print("(TG_API ไม่พบ — ข้ามการส่ง Telegram)")
        return
    try:
        send(msg)
    except Exception as e:
        print("telegram error:", e)


if __name__ == "__main__":
    main()
