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
COLS = ["station", "hex", "flight", "direction", "eta_min", "dist_nm", "alt", "gs",
        "lat", "lon", "trk", "push_ts"]
# D1 จำกัด bound params ~100/query → ต้องหั่นเป็นก้อน (เหมือน outbox.D1_MAX_PARAMS)
# ของเดิมยัดทุกลำใน INSERT เดียว: ~12 ลำยังผ่าน แต่ชั่วโมงเร่งด่วน 22 ลำ = 176 params →
# D1 ตอบ 400 "too many SQL variables" ทุกรอบ ตารางเลยว่างตลอด
D1_MAX_PARAMS = 90


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


def cfg(key, default=None):
    """env ของ process (systemd EnvironmentFile) ก่อน แล้วค่อยอ่านไฟล์เอง — เหมือน outbox/heartbeat/
    eta_push. ตัวนี้เป็น D1 script ตัวสุดท้ายที่ยังอ่านจากไฟล์อย่างเดียว ทำให้ทดสอบนอกเครื่องไม่ได้
    และพังเงียบถ้าวันหนึ่งสิทธิ์ไฟล์เปลี่ยน"""
    return os.environ.get(key) or ENV.get(key) or default


STATION = cfg("STATION_ID") or socket.gethostname()
D1_ACC, D1_DB, D1_TOK = cfg("D1_ACCOUNT_ID"), cfg("D1_DATABASE_ID"), cfg("D1_API_TOKEN")


def log(*a):
    print(time.strftime("%F %T"), "inbound_push:", *a)


def read_live():
    """inbound + outbound จากไฟล์เดียว ติดป้าย direction ให้แต่ละลำ.
    ขาออกไม่มี eta_min (ไม่ได้กำลังจะถึง) → คอลัมน์นั้นเป็น NULL"""
    try:
        with open(INBOUND_F) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    rows = [dict(a, direction="inbound") for a in d.get("inbound_all", [])]
    rows += [dict(a, direction="outbound", eta_min=None) for a in d.get("outbound_all", [])]
    return rows


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


def ensure_table():
    """สร้างตาราง D1 เองครั้งเดียว (best-effort) — ไม่ต้องใช้ wrangler แยก."""
    for stmt in INBOUND_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            d1_query(stmt, [])


def push(rows):
    """แทนที่ชุดเครื่องบินปัจจุบันของสถานีใน D1 แบบไม่มีช่องว่าง (upsert ก่อน แล้วลบตัวเก่า).
    DELETE ทำหลัง INSERT ครบทุกก้อนเท่านั้น — ถ้าก้อนใดพัง exception จะเด้งออกไปก่อนถึง DELETE
    ทำให้ตารางยังเป็นชุดเก่าทั้งชุด (ข้อมูลเก่า 30 วิ ดีกว่าตารางแหว่งครึ่ง ๆ)"""
    now = int(time.time())
    per = max(1, D1_MAX_PARAMS // len(COLS))
    cell = "(" + ",".join(["?"] * len(COLS)) + ")"
    for i in range(0, len(rows), per):
        chunk = rows[i:i + per]
        params = []
        for r in chunk:
            params += [STATION, r.get("hex"), r.get("flight"), r.get("direction"),
                       r.get("eta_min"), r.get("dist_nm"), r.get("alt"), r.get("gs"),
                       r.get("lat"), r.get("lon"), r.get("trk"), now]
        d1_query(f"INSERT OR REPLACE INTO inbound_live ({','.join(COLS)}) VALUES "
                 f"{','.join([cell] * len(chunk))}", params)
    d1_query("DELETE FROM inbound_live WHERE station = ? AND push_ts < ?", [STATION, now])


_stop = False


def _term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log(f"เริ่มทำงาน — ยิง inbound_all → D1 inbound_live ทุก {PUSH_INTERVAL_S}s (station={STATION})")
    table_ready = False
    while not _stop:
        try:
            if not table_ready:              # สร้างตารางเอง — ลองซ้ำจนสำเร็จ (กันเน็ตหลุดตอน boot)
                ensure_table()
                table_ready = True
                log("ตาราง inbound_live พร้อม")
            push(read_live())
        except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
            log("ส่ง D1 ไม่สำเร็จ (เน็ตหลุด?) — ข้ามรอบนี้:", e)   # ไม่ crash รอบหน้าค่อยส่งใหม่
        for _ in range(PUSH_INTERVAL_S):     # sleep แบบตอบ SIGTERM ไว
            if _stop:
                break
            time.sleep(1)


# สร้างตารางใน D1 ครั้งเดียวก่อนใช้ (wrangler d1 execute <db> --remote --command "..."):
INBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_live (
  station TEXT, hex TEXT, flight TEXT, direction TEXT, eta_min REAL, dist_nm REAL,
  alt INTEGER, gs INTEGER, lat REAL, lon REAL, trk INTEGER, push_ts INTEGER,
  PRIMARY KEY (station, hex)
);
CREATE INDEX IF NOT EXISTS idx_inbound_live_station ON inbound_live(station, eta_min);
"""
# ตารางที่สร้างไว้ก่อนมีคอลัมน์พวกนี้ (ensure_table ใช้ CREATE IF NOT EXISTS จึงไม่เพิ่มให้เอง):
#   ALTER TABLE inbound_live ADD COLUMN direction TEXT;   -- inbound / outbound
#   ALTER TABLE inbound_live ADD COLUMN lat REAL;
#   ALTER TABLE inbound_live ADD COLUMN lon REAL;
#   ALTER TABLE inbound_live ADD COLUMN trk INTEGER;

if __name__ == "__main__":
    main()
