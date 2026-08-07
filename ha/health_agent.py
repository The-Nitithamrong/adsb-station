#!/usr/bin/env python3
"""health_agent.py — Pi#1 (สถานี ADS-B) ส่งชีพจรเข้า fleet MQTT ให้ Pi#2 peer-watchdog เฝ้า.

ต่างจาก mqtt_publish.py (ตัวนั้นยิง Home Assistant discovery/state บน broker ของ HA):
ตัวนี้อยู่บน "fleet plane" — คุยกับ broker บน Pi#2 (BROKER_HOST) ด้วย topic/timing จาก
shared/pylib/fleet_mqtt.py (SSOT เดียวกับ peer-watchdog → ไม่ drift). ทำ 3 อย่าง:

  1. heartbeat ทุก HEARTBEAT_INTERVAL_S (30s) → fleet/pi-adsb/health (ไม่ retained)
     payload = สุขภาพจริง: fr24_feed_ok (มาจาก status.json = data-flow จริงบน 30003, ไม่ใช่แค่
     process alive — flag ที่โกหกตอน dongle แฮงก์คือบั๊ก 21 ชม.เดิม), pixoo_ok, flight_watcher_ok,
     uptime_s (ใช้แยก hang จริง [uptime รีเซ็ต] ออกจากเน็ตหลุด [uptime เดินต่อ]), commit ที่ deploy อยู่.
  2. LWT: ถือ connection ค้างไว้ด้วย mosquitto_sub ที่ตั้ง will=offline(retained) บน
     fleet/pi-adsb/status — connection ตาย (Pi แฮงก์/agent ถูกฆ่า) → broker ยิง offline ให้เอง
     (fast-path เสริม; ตัวหลักคือ "ความเงียบ" ของ heartbeat). ตอน start ยิง online(retained).
  3. subscribe fleet/pi-adsb/cmd → รับ restart-services จาก peer-watchdog L1 → เรียก fleet-cmd
     (ตัวเดียวกับ SSH forced-command L2 ใช้ → คำสั่งที่อนุญาตอยู่ที่เดียว).

stdlib + mosquitto-clients (เหมือน mqtt_publish.py). รันเป็น daemon (Type=simple, User=arin).
config/secrets ใน /etc/fr24-watchdog.env: BROKER_HOST/PORT/USER/PASS (broker บน Pi#2), STATION_ID.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", "pylib"))

from fleet_mqtt import (CMD, HEALTH, HEARTBEAT_INTERVAL_S, STATUS)  # noqa: E402

ENV_FILE  = "/etc/fr24-watchdog.env"
STATUS_F  = "/run/fr24-watchdog/status.json"    # เขียนโดย watchdog: {ts, health, msg_per_s, aircraft}
REPO      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLEET_CMD = "/usr/local/bin/fleet-cmd"          # ตัวจำกัดคำสั่ง (sync โดย autoupdate); fallback = ในrepo
# watchdog เขียน status.json ทุก 5 นาที (fr24-watchdog.timer OnUnitActiveSec=5min) → ต้องเผื่อ.
# 660 = 2 รอบ watchdog + margin: เก่ากว่านี้แปลว่า watchdog เองหยุดเขียน (ตายจริง) → feeder ไม่ ok.
STALE_STATUS_S = 660


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


ENV     = load_env(ENV_FILE)
STATION = ENV.get("STATION_ID") or "pi-adsb"
BROKER  = ENV.get("BROKER_HOST", "127.0.0.1")
BPORT   = ENV.get("BROKER_PORT", "1883")
BUSER   = ENV.get("BROKER_USER", "")
BPASS   = ENV.get("BROKER_PASS", "")


def _auth():
    a = ["-h", BROKER, "-p", str(BPORT)]
    if BUSER:
        a += ["-u", BUSER, "-P", BPASS]
    return a


def publish(topic, payload, retain=False):
    argv = ["mosquitto_pub"] + _auth() + ["-t", topic, "-m", payload]
    if retain:
        argv.append("-r")
    try:
        subprocess.run(argv, check=False, timeout=10)
    except Exception as e:
        print("publish error:", e)


# ---------------- health snapshot ----------------
def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


def git_commit():
    try:
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def unit_active(unit):
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def feeder_ok():
    """data-flow จริง: status.json health=='ok' และไม่ค้างเก่า (watchdog เขียนทุก 5 นาที)."""
    s = read_json(STATUS_F)
    if not s:
        return False, s
    fresh = (time.time() - s.get("ts", 0)) < STALE_STATUS_S if s.get("ts") else False
    return (s.get("health") == "ok" and fresh), s


def snapshot():
    ok, s = feeder_ok()
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": git_commit(),
        "uptime_s": uptime_s(),
        "fr24_feed_ok": ok,
        "pixoo_ok": unit_active("pixoo"),
        "flight_watcher_ok": unit_active("flight-watcher"),
        "msg_per_s": s.get("msg_per_s"),
        "aircraft": s.get("aircraft"),
    }


# ---------------- cmd handling ----------------
def run_fleet_cmd(cmd):
    """เรียก fleet-cmd (ตัวจำกัดคำสั่ง). ถ้ายังไม่ได้ sync ไป /usr/local/bin ใช้ตัวในrepo."""
    exe = FLEET_CMD if os.path.exists(FLEET_CMD) else os.path.join(REPO, "deploy", "fleet-cmd")
    try:
        r = subprocess.run([exe, cmd], capture_output=True, text=True, timeout=60)
        print(f"fleet-cmd {cmd}: rc={r.returncode} {r.stdout.strip()} {r.stderr.strip()}")
    except Exception as e:
        print("fleet-cmd error:", e)


def handle_cmd(payload):
    try:
        d = json.loads(payload)
        cmd = d.get("cmd", "")
    except ValueError:
        cmd = payload.strip()
    if cmd == "restart-services":
        print("cmd: restart-services จาก peer-watchdog → เรียก fleet-cmd")
        run_fleet_cmd("restart-services")
    else:
        print(f"cmd: เพิกเฉยคำสั่งที่ไม่รู้จัก '{cmd}'")


# ---------------- persistent connection (LWT + cmd) ----------------
class Connection(threading.Thread):
    """ถือ connection ค้างด้วย mosquitto_sub ที่ตั้ง will=offline(retained) + subscribe CMD.
    connection นี้ = 'agent ยังมีชีวิต'; ตายเมื่อไหร่ broker ยิง will ให้ (fast-path)."""

    daemon = True

    def run(self):
        argv = ["mosquitto_sub"] + _auth() + [
            "--will-topic", STATUS, "--will-payload", "offline",
            "--will-qos", "1", "--will-retain",
            "-t", CMD, "-v",
        ]
        while True:
            try:
                p = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1)
                for line in p.stdout:
                    line = line.rstrip("\n")
                    if " " not in line:
                        continue
                    _topic, _, payload = line.partition(" ")
                    handle_cmd(payload)
            except Exception as e:
                print("connection error, retry ใน 5s:", e)
            time.sleep(5)   # broker หลุด → เชื่อมใหม่ (will ถูกตั้งใหม่ตอน reconnect)


# ---------------- main ----------------
_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    publish(STATUS, "online", retain=True)          # ประกาศออนไลน์ (retained)
    Connection().start()                            # ถือ LWT + รับ cmd
    print(f"health_agent เริ่มทำงาน — ส่ง heartbeat ทุก {HEARTBEAT_INTERVAL_S}s ไป broker {BROKER}")

    while not _stop:
        publish(HEALTH, json.dumps(snapshot()))
        # นอนเป็นช่วงสั้น ๆ เพื่อให้ตอบ SIGTERM ไว (ไม่ค้างครบ 30s)
        for _ in range(HEARTBEAT_INTERVAL_S):
            if _stop:
                break
            time.sleep(1)

    # ปิดแบบสุภาพ: ประกาศ offline เอง (will ไม่ยิงตอน disconnect สะอาด)
    publish(STATUS, "offline", retain=True)
    print("health_agent หยุด — ประกาศ offline แล้ว")


if __name__ == "__main__":
    main()
