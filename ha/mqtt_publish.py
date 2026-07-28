#!/usr/bin/env python3
"""mqtt_publish.py — ส่งข้อมูลสถานี ADS-B เข้า MQTT พร้อม Home Assistant discovery.

อ่าน /run/fr24-watchdog/status.json + /run/flight-watcher/inbound.json →
ยิง discovery config (retained) ให้ HA สร้าง sensor เอง + ยิง state.
ใช้ `mosquitto_pub` (apt: mosquitto-clients) — Python stdlib ล้วน. รันโดย systemd timer (User=arin).

config/secrets ใน /etc/fr24-watchdog.env:
  MQTT_HOST, MQTT_PORT(1883), MQTT_USER, MQTT_PASS, STATION_ID(optional=hostname)
"""
import json, os, socket, subprocess, sys, time

ENV_FILE = "/etc/fr24-watchdog.env"
STATUS_F = "/run/fr24-watchdog/status.json"
INBOUND_F = "/run/flight-watcher/inbound.json"
TEMP_F = "/sys/class/thermal/thermal_zone0/temp"   # CPU temp Pi (millidegree)
FAN_F = "/run/adsb-ha/fan.json"    # สถานะพัดลม (จาก HA ผ่าน MQTT) → Pixoo อ่านโชว์
FAN_LOG = "/home/arin/fan_events.jsonl"   # log ทุก on/off (persistent) → report/fan_stats.py
DISC = "homeassistant"          # HA discovery prefix (default)
EXPIRE = 300                    # sensor เป็น unavailable ถ้าไม่มี state ใหม่ใน 5 นาที (timer=1m)


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
STATION = ENV.get("STATION_ID") or socket.gethostname()
SID = "".join(c if c.isalnum() else "_" for c in STATION).lower()   # ปลอดภัยสำหรับ topic/unique_id
BASE = f"adsb/{SID}"
DEVICE = {"identifiers": [f"adsb_{SID}"], "name": f"ADS-B {STATION}",
          "model": "adsb-station", "manufacturer": "iamkkn"}

# sensor: (object_id, ชื่อ, state_src, key, unit, state_class, icon)
#   state_src = "feeder" (status.json) หรือ "inbound" (inbound.json)
SENSORS = [
    ("feeder_health",     "Feeder health",     "feeder",  "health",    None,    None,          "mdi:radar"),
    ("feeder_rate",       "Feeder rate",       "feeder",  "msg_per_s", "msg/s", "measurement", "mdi:speedometer"),
    ("feeder_aircraft",   "Aircraft (feeder)", "feeder",  "aircraft",  None,    "measurement", "mdi:airplane"),
    ("cpu_temp",          "CPU temperature",   "feeder",  "cpu_temp",  "°C",    "measurement", "mdi:thermometer"),
    ("throttle",          "Power/throttle",    "feeder",  "throttle",  None,    None,          "mdi:flash-alert"),
    ("received_aircraft", "Aircraft received", "inbound", "nrx",       None,    "measurement", "mdi:airplane-search"),
    ("tha_flight",        "THA inbound",       "inbound", "flight",    None,    None,          "mdi:airplane-landing"),
    ("tha_eta",           "THA ETA",           "inbound", "eta_min",   "min",   "measurement", "mdi:timer"),
    ("tha_dist",          "THA distance",      "inbound", "dist_nm",   "nm",    "measurement", "mdi:map-marker-distance"),
]


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_temp():
    # อุณหภูมิ CPU Pi (°C) — mqtt_publish รันบน Pi (User=arin) จึงอ่าน /sys ได้ตรง
    try:
        with open(TEMP_F) as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


# vcgencmd get_throttled — บิตปัจจุบัน (0-3) และประวัติ (+16)
_THR_NOW = [(0x1, "undervoltage"), (0x4, "throttled"), (0x2, "freq-capped"), (0x8, "soft-temp")]
_THR_EVER = [(0x10000, "undervoltage"), (0x40000, "throttled"), (0x20000, "freq-capped"), (0x80000, "soft-temp")]


def read_throttled():
    # สถานะ power/thermal ของ Pi เป็นข้อความอ่านง่ายสำหรับ HA — "ok" / "undervoltage" / "ok (… occurred)"
    # ต้องอยู่กลุ่ม video (sudo usermod -aG video arin) ไม่งั้นได้ "unknown"
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        raw = int(out.split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return "unknown"
    now = [name for bit, name in _THR_NOW if raw & bit]
    if now:
        return ",".join(now)
    ever = [name for bit, name in _THR_EVER if raw & bit]
    if ever:
        return "ok (" + ",".join(ever) + " occurred)"
    return "ok"


def pub(topic, payload, retain=True):
    host = ENV.get("MQTT_HOST")
    cmd = ["mosquitto_pub", "-h", host, "-p", ENV.get("MQTT_PORT", "1883"), "-t", topic, "-m", payload]
    if ENV.get("MQTT_USER"):
        cmd += ["-u", ENV["MQTT_USER"]]
    if ENV.get("MQTT_PASS"):
        cmd += ["-P", ENV["MQTT_PASS"]]
    if retain:
        cmd += ["-r"]
    subprocess.run(cmd, check=True, timeout=10)


def sub_retained(topic):
    # อ่าน retained message ล่าสุดของ topic (มีทันทีถ้า retained) — คืน str หรือ None ถ้าไม่ม/หมดเวลา
    host = ENV.get("MQTT_HOST")
    cmd = ["mosquitto_sub", "-h", host, "-p", ENV.get("MQTT_PORT", "1883"),
           "-t", topic, "-C", "1", "-W", "2"]     # -C 1 เอา 1 ข้อความ · -W 2 รอ retained สูงสุด 2 วิ
    if ENV.get("MQTT_USER"):
        cmd += ["-u", ENV["MQTT_USER"]]
    if ENV.get("MQTT_PASS"):
        cmd += ["-P", ENV["MQTT_PASS"]]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _prev_fan_on():
    try:
        with open(FAN_F) as f:
            return json.load(f).get("on")
    except (OSError, ValueError):
        return None


def update_fan_state():
    # ดึงสถานะพัดลม (switch จริงจาก Tuya) ที่ HA publish ไว้ที่ adsb/<sid>/fan → เขียน /run/adsb-ha/fan.json
    # HA automation ส่ง payload "on"/"off" (retained) เมื่อ switch เปลี่ยน. ไม่มี topic = on:null (ไม่รู้)
    val = sub_retained(f"{BASE}/fan")
    on = None if val is None else (val.strip().lower() in ("on", "true", "1"))
    prev = _prev_fan_on()
    now = int(time.time())
    # log transition (on↔off) ลงไฟล์ persistent → ดูความถี่/เวลารวมต่อวันได้ (poll 1 นาที = ความละเอียดพอ)
    if on is not None and prev is not None and on != prev:
        try:
            with open(FAN_LOG, "a") as f:
                f.write(json.dumps({"ts": now, "on": on}) + "\n")
        except OSError:
            pass
    try:
        os.makedirs(os.path.dirname(FAN_F), exist_ok=True)
        tmp = FAN_F + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": now, "on": on}, f)
        os.replace(tmp, FAN_F)
        os.chmod(FAN_F, 0o644)
    except OSError:
        pass


def publish_discovery():
    for oid, name, src, key, unit, sclass, icon in SENSORS:
        cfg = {
            "name": name,
            "unique_id": f"adsb_{SID}_{oid}",
            "state_topic": f"{BASE}/{src}",
            "value_template": "{{ value_json.%s }}" % key,
            "expire_after": EXPIRE,
            "icon": icon,
            "device": DEVICE,
        }
        if unit:
            cfg["unit_of_measurement"] = unit
            # หมายเหตุ: จงใจ "ไม่" ตั้ง device_class=temperature ให้ cpu_temp —
            # device_class temperature ทำให้ HA แปลงหน่วยตาม unit system (°C↔°F) ซึ่ง
            # เพี้ยน/revert ได้ (เช่นตอน HA config เสียหลัง crash) → automation คุมพัดลม
            # (numeric_state) พังเงียบๆ. ปล่อยเป็นเลขดิบ °C เสมอ = automation เสถียร.
        if sclass:
            cfg["state_class"] = sclass
        pub(f"{DISC}/sensor/adsb_{SID}/{oid}/config", json.dumps(cfg), retain=True)


def publish_state():
    feeder = read_json(STATUS_F)
    inb = read_json(INBOUND_F)
    feeder_state = {
        "health": feeder.get("health", "stale"),
        "msg_per_s": feeder.get("msg_per_s", 0),
        "aircraft": feeder.get("aircraft", 0),
        "cpu_temp": read_temp(),
        "throttle": read_throttled(),
    }
    inbound_state = {
        "nrx": inb.get("nrx", 0),
        "flight": inb.get("flight") or "none",
        "eta_min": inb.get("eta_min"),
        "dist_nm": inb.get("dist_nm"),
    }
    pub(f"{BASE}/feeder", json.dumps(feeder_state))
    pub(f"{BASE}/inbound", json.dumps(inbound_state))


def main():
    if not ENV.get("MQTT_HOST"):
        print(f"mqtt_publish: ยังไม่ตั้ง MQTT_HOST ใน {ENV_FILE} — ข้าม")
        return
    try:
        publish_discovery()
        publish_state()
    except FileNotFoundError:
        print("mqtt_publish: ไม่พบ mosquitto_pub — sudo apt install mosquitto-clients")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"mqtt_publish: publish ล้มเหลว (broker/creds?) — {e}")
        sys.exit(1)
    update_fan_state()   # อ่านสถานะพัดลมกลับมาเขียน /run (best-effort — ไม่ทำให้ service fail)


if __name__ == "__main__":
    main()
