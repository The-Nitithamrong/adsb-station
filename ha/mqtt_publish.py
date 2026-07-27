#!/usr/bin/env python3
"""mqtt_publish.py — ส่งข้อมูลสถานี ADS-B เข้า MQTT พร้อม Home Assistant discovery.

อ่าน /run/fr24-watchdog/status.json + /run/flight-watcher/inbound.json →
ยิง discovery config (retained) ให้ HA สร้าง sensor เอง + ยิง state.
ใช้ `mosquitto_pub` (apt: mosquitto-clients) — Python stdlib ล้วน. รันโดย systemd timer (User=arin).

config/secrets ใน /etc/fr24-watchdog.env:
  MQTT_HOST, MQTT_PORT(1883), MQTT_USER, MQTT_PASS, STATION_ID(optional=hostname)
"""
import json, socket, subprocess, sys

ENV_FILE = "/etc/fr24-watchdog.env"
STATUS_F = "/run/fr24-watchdog/status.json"
INBOUND_F = "/run/flight-watcher/inbound.json"
TEMP_F = "/sys/class/thermal/thermal_zone0/temp"   # CPU temp Pi (millidegree)
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
            if unit == "°C":
                cfg["device_class"] = "temperature"   # HA แสดง/กราฟเป็นอุณหภูมิถูกต้อง
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


if __name__ == "__main__":
    main()
