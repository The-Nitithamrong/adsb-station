#!/usr/bin/env python3
"""inbound_push.py — ยิง "THA inbound เข้า VTBS ตอนนี้ + ETA" ขึ้น Cloudflare D1 ทุก ~30s
ให้ web app (โปรเจกต์อื่น) ดึงไปโชว์ real-time.

source = /run/flight-watcher/inbound.json (flight_watcher เขียนสดทุก ~10s) → D1 table `inbound_now`.
เก็บ **1 แถวต่อสถานี** (station = PK, INSERT OR REPLACE) → เป็น "ค่าล่าสุดตอนนี้" เสมอ ไม่สะสมประวัติ
(ประวัติ/ground-truth มี tracks + outbox.py แล้ว). ไม่มี inbound → flight = NULL.

`push_ts` = เวลาที่ยิง → ฝั่ง web ใช้เช็คความสด (ถ้า push_ts เก่า = pusher/feeder หยุด). eta_min เป็น
ค่า ณ ตอนอ่าน (flight_watcher คำนวณจาก altitude) — ยิงทุก 30s เลยสดพอ (ETA เปลี่ยนเป็นนาที).

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

# ลำดับคอลัมน์ = ลำดับใน INSERT (ต้องตรงกับ schema ใน D1)
COLS = ["station", "ts", "push_ts", "flight", "eta_min", "dist_nm", "alt", "gs", "hex", "nrx"]


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


def log(*a):
    print(time.strftime("%F %T"), "inbound_push:", *a)


def read_inbound():
    try:
        with open(INBOUND_F) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def row_from(inb):
    # inbound.json: {flight|null, eta_min, dist_nm, alt, gs, hex, ts, nrx, list}
    return {
        "station": STATION,
        "ts": int(inb["ts"]) if inb.get("ts") else None,
        "push_ts": int(time.time()),
        "flight": inb.get("flight"),
        "eta_min": inb.get("eta_min"),
        "dist_nm": inb.get("dist_nm"),
        "alt": inb.get("alt"),
        "gs": inb.get("gs"),
        "hex": inb.get("hex"),
        "nrx": inb.get("nrx"),      # จำนวนเครื่องที่รับได้ทั้งหมด (ทุกสายการบิน)
    }


def d1_replace(row):
    acc, dbid, tok = ENV.get("D1_ACCOUNT_ID"), ENV.get("D1_DATABASE_ID"), ENV.get("D1_API_TOKEN")
    if not (acc and dbid and tok):
        raise RuntimeError(f"ตั้ง D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN ใน {ENV_FILE} ก่อน")
    sql = (f"INSERT OR REPLACE INTO inbound_now ({','.join(COLS)}) "
           f"VALUES ({','.join(['?'] * len(COLS))})")
    params = [row.get(c) for c in COLS]
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{dbid}/query"
    req = urllib.request.Request(
        url, data=json.dumps({"sql": sql, "params": params}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        raise RuntimeError(f"D1 error: {resp.get('errors')}")


_stop = False


def _term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log(f"เริ่มทำงาน — ยิง inbound → D1 inbound_now ทุก {PUSH_INTERVAL_S}s (station={STATION})")
    while not _stop:
        row = row_from(read_inbound())
        try:
            d1_replace(row)
        except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
            log("ส่ง D1 ไม่สำเร็จ (เน็ตหลุด?) — ข้ามรอบนี้:", e)   # ไม่ crash รอบหน้าค่อยส่งใหม่
        for _ in range(PUSH_INTERVAL_S):     # sleep แบบตอบ SIGTERM ไว
            if _stop:
                break
            time.sleep(1)


# สร้างตารางใน D1 ครั้งเดียวก่อนใช้ (wrangler d1 execute <db> --command "..." / dashboard console):
INBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_now (
  station TEXT PRIMARY KEY, ts INTEGER, push_ts INTEGER,
  flight TEXT, eta_min REAL, dist_nm REAL, alt INTEGER, gs INTEGER, hex TEXT, nrx INTEGER
);
"""

if __name__ == "__main__":
    main()
