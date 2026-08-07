#!/usr/bin/env python3
"""inbound_push.py — ยิง "ทุกเครื่องที่กำลัง inbound เข้า VTBS ตอนนี้ (ทุกสายการบิน) + ETA" ขึ้น
Cloudflare D1 ทุก ~30s ให้ web app (โปรเจกต์อื่น) ดึงไปโชว์ real-time หลายลำ.

source = /run/flight-watcher/inbound.json → field `inbound_all` (flight_watcher เขียนสดทุก ~10s).
D1 table `inbound_live`: **หลายแถว** (1 แถว/เครื่อง, PK = station+hex). แต่ละรอบ:
  1. INSERT OR REPLACE ทุกเครื่องที่ inbound ตอนนี้ (push_ts = now)
  2. DELETE เครื่องที่ push_ts < now (ลำที่หายไป/ลงแล้ว) → ตารางสะท้อน "ชุดปัจจุบัน" เป๊ะ ไม่มีช่องว่าง
(ประวัติ/ground-truth มี tracks + outbox.py แล้ว — อันนี้ live-only). ไม่มีเครื่องเลย → ล้างหมด.

reuse D1 pattern + creds เดียวกับ heartbeat.py/outbox.py (`D1_*` ใน /etc/fr24-watchdog.env). stdlib ล้วน.
daemon (Type=simple, User=arin) loop ทุก PUSH_INTERVAL_S. สร้างตาราง D1 ครั้งเดียว (INBOUND_SCHEMA ท้ายไฟล์).
"""
import json
import os
import signal
import socket
import time
import urllib.error
import urllib.request

ENV_FILE = "/etc/fr24-watchdog.env"
INBOUND_F = "/run/flight-watcher/inbound.json"
PUSH_INTERVAL_S = int(os.environ.get("PUSH_INTERVAL_S", "30"))

# ลำดับคอลัมน์ต่อเครื่อง = ลำดับใน INSERT (ต้องตรงกับ schema)
COLS = ["station", "hex", "flight", "eta_min", "dist_nm", "alt", "gs", "push_ts"]


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
D1_ACC, D1_DB, D1_TOK = ENV.get("D1_ACCOUNT_ID"), ENV.get("D1_DATABASE_ID"), ENV.get("D1_API_TOKEN")


def log(*a):
    print(time.strftime("%F %T"), "inbound_push:", *a)


def read_inbound_all():
    try:
        with open(INBOUND_F) as f:
            return json.load(f).get("inbound_all", [])
    except (OSError, ValueError):
        return []


def d1_query(sql, params):
    if not (D1_ACC and D1_DB and D1_TOK):
        raise RuntimeError(f"ตั้ง D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN ใน {ENV_FILE} ก่อน")
    url = f"https://api.cloudflare.com/client/v4/accounts/{D1_ACC}/d1/database/{D1_DB}/query"
    req = urllib.request.Request(
        url, data=json.dumps({"sql": sql, "params": params}).encode(), method="POST",
        headers={"Authorization": f"Bearer {D1_TOK}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        raise RuntimeError(f"D1 error: {resp.get('errors')}")


def push(rows):
    """แทนที่ชุด inbound ปัจจุบันของสถานีใน D1 แบบไม่มีช่องว่าง (upsert ก่อน แล้วลบตัวเก่า)."""
    now = int(time.time())
    if rows:
        cell = "(" + ",".join(["?"] * len(COLS)) + ")"
        tuples, params = [], []
        for r in rows:
            tuples.append(cell)
            params += [STATION, r.get("hex"), r.get("flight"), r.get("eta_min"),
                       r.get("dist_nm"), r.get("alt"), r.get("gs"), now]
        d1_query(f"INSERT OR REPLACE INTO inbound_live ({','.join(COLS)}) VALUES {','.join(tuples)}",
                 params)
    d1_query("DELETE FROM inbound_live WHERE station = ? AND push_ts < ?", [STATION, now])


_stop = False


def _term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log(f"เริ่มทำงาน — ยิง inbound_all → D1 inbound_live ทุก {PUSH_INTERVAL_S}s (station={STATION})")
    while not _stop:
        try:
            push(read_inbound_all())
        except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
            log("ส่ง D1 ไม่สำเร็จ (เน็ตหลุด?) — ข้ามรอบนี้:", e)   # ไม่ crash รอบหน้าค่อยส่งใหม่
        for _ in range(PUSH_INTERVAL_S):     # sleep แบบตอบ SIGTERM ไว
            if _stop:
                break
            time.sleep(1)


# สร้างตารางใน D1 ครั้งเดียวก่อนใช้ (wrangler d1 execute <db> --remote --command "..."):
INBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_live (
  station TEXT, hex TEXT, flight TEXT, eta_min REAL, dist_nm REAL,
  alt INTEGER, gs INTEGER, push_ts INTEGER,
  PRIMARY KEY (station, hex)
);
CREATE INDEX IF NOT EXISTS idx_inbound_live_station ON inbound_live(station, eta_min);
"""

if __name__ == "__main__":
    main()
