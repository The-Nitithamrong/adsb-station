#!/usr/bin/env python3
"""reg_lookup.py — เติม registration (ทะเบียนเครื่อง) ให้แถวใน `sightings` แล้วปล่อยให้ outbox ส่งต่อ.

ทำไมต้องมีไฟล์นี้แยก: **ADS-B ไม่ได้ส่งทะเบียนมา** — บน 30003 มีแค่ callsign (THA476) กับ
ICAO 24-bit hex (8801f2) ซึ่ง hex คือ ID ของ "ลำ" จริง ๆ ส่วนทะเบียน (HS-TKF) ต้อง lookup เอา.
lookup = HTTP → ห้ามทำใน flight_watcher (parse() อยู่ใน hot loop ของ socket; บล็อกรอเน็ต =
อ่าน stream ไม่ทัน ข้อมูลหาย — บทเรียนเดียวกับที่ห้ามใช้ `nc | awk`). เลยแยกเป็น timer:
flight_watcher เขียนแถวไว้ก่อนโดย reg=NULL, ตัวนี้ค่อยตามมาเติม.

reg_state ใน sightings = สถานะการเติม (outbox จะส่งเฉพาะแถวที่ != 0 → ปลายทางไม่เห็นแถวครึ่ง ๆ):
  0 = pending  ยังไม่รู้ (flight_watcher เพิ่งเขียน)
  1 = resolved ได้ทะเบียนแล้ว
  2 = unknown  ถามแล้ว REG_MAX_TRIES ครั้งไม่เจอ (hex ใหม่/ทหาร/ไม่มีในฐาน) — เลิกถาม ส่งขึ้นไปโดย reg=NULL

cache: ตาราง `aircraft` (hex → reg) ถาวรในเครื่อง — ถาม upstream ครั้งเดียวต่อลำตลอดกาล
เครื่องบินลำเดิมบินซ้ำทุกวันก็ไม่ยิงเน็ตอีก (ลำที่เห็นบ่อยจะ hit cache ~100%).

stdlib ล้วน. รันด้วย systemd timer (User=arin) ทุก ~10 นาที คู่กับ outbox.
config (optional) ใน /etc/fr24-watchdog.env: REGDB_URL (default hexdb.io)
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

DB = "/home/arin/flightwatch.db"
ENV_FILE = "/etc/fr24-watchdog.env"
DEFAULT_REGDB_URL = "https://hexdb.io/api/v1/aircraft/{hex}"   # {hex} = ICAO 24-bit (hex ตัวเล็ก)
LOOKUP_MAX_PER_RUN = 80        # กี่ลำใหม่ต่อรอบ (สุภาพกับ service ฟรี; ที่เหลือค้างไว้รอบหน้า)
LOOKUP_SLEEP_S = 0.3           # เว้นระยะระหว่าง request
HTTP_TIMEOUT_S = 10
REG_MAX_TRIES = 3              # ถามไม่เจอกี่ครั้งถึงเลิก (reg_state=2)
# UA แบบ browser: บทเรียนจาก eta_push — บาง endpoint หลัง Cloudflare บล็อก `Python-urllib` ที่ขอบ
USER_AGENT = "Mozilla/5.0 (pi-radar; adsb-station reg_lookup)"


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
REGDB_URL = os.environ.get("REGDB_URL") or ENV.get("REGDB_URL") or DEFAULT_REGDB_URL


def log(*a):
    print(time.strftime("%F %T"), "reg_lookup:", *a)


def ensure_schema(db):
    """cache ทะเบียนต่อลำ (ถาวร ข้ามวัน) — tries/last_ts ไว้กันถามซ้ำลำที่ upstream ไม่มีข้อมูล."""
    db.execute("""CREATE TABLE IF NOT EXISTS aircraft(
        hex TEXT PRIMARY KEY, reg TEXT, type TEXT, tries INTEGER DEFAULT 0, last_ts INTEGER)""")
    db.commit()


# ฟิลด์ทะเบียน/ชนิดเครื่อง ชื่อไม่เหมือนกันในแต่ละ service (hexdb.io ใช้ Registration/ICAOTypeCode,
# adsbdb ใช้ registration/type) → ลองทีละชื่อ เปลี่ยน REGDB_URL ไป service อื่นได้โดยไม่ต้องแก้โค้ด
REG_KEYS = ("Registration", "registration", "reg", "r")
TYPE_KEYS = ("ICAOTypeCode", "icao_type_code", "type", "t", "Type")


def pick(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def fetch_reg(hexid):
    """ถาม upstream ว่า hex นี้คือทะเบียนอะไร → (reg, type) หรือ (None, None) ถ้าไม่เจอ.
    ยกเว้นเฉพาะกรณี 'ไม่เจอ' เท่านั้นที่คืน None — เน็ต/เซิร์ฟเวอร์ล่ม raise ขึ้นไปให้ผู้เรียกหยุดรอบ
    (ไม่งั้นจะนับ tries เพิ่มทั้งที่ยังไม่ได้ถามจริง แล้วเผลอ mark ลำดี ๆ เป็น unknown)."""
    req = urllib.request.Request(REGDB_URL.format(hex=hexid.lower()),
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            d = json.loads(r.read().decode(errors="ignore"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            return None, None                      # ไม่มีลำนี้ในฐาน = ตอบแล้วว่าไม่รู้
        raise
    except ValueError:
        return None, None                          # ตอบมาไม่ใช่ JSON = ถือว่าไม่รู้
    if isinstance(d, dict) and isinstance(d.get("response"), dict):
        d = d["response"]                          # adsbdb ห่อไว้อีกชั้น
    if isinstance(d, dict) and isinstance(d.get("aircraft"), dict):
        d = d["aircraft"]
    if not isinstance(d, dict):
        return None, None
    return pick(d, REG_KEYS), pick(d, TYPE_KEYS)


def resolve_unknown(db):
    """ลำที่ยังไม่มีใน cache (หรือมีแต่ยังไม่รู้ทะเบียนและยังไม่ครบโควตา) → ถาม upstream.
    คืน (ถามไปกี่ลำ, ได้ทะเบียนกี่ลำ)."""
    rows = db.execute("""
        SELECT DISTINCT s.hex FROM sightings s
        LEFT JOIN aircraft a ON a.hex = s.hex
        WHERE s.reg_state = 0
          AND (a.hex IS NULL OR (a.reg IS NULL AND a.tries < ?))
        LIMIT ?""", (REG_MAX_TRIES, LOOKUP_MAX_PER_RUN)).fetchall()
    asked = found = 0
    for (hexid,) in rows:
        try:
            reg, typ = fetch_reg(hexid)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log(f"เน็ต/upstream มีปัญหา ({e}) — หยุดรอบนี้ ค้างไว้รอบหน้า")
            break
        asked += 1
        if reg:
            found += 1
        db.execute("""INSERT INTO aircraft(hex,reg,type,tries,last_ts) VALUES (?,?,?,1,?)
                      ON CONFLICT(hex) DO UPDATE SET
                        reg=COALESCE(excluded.reg, aircraft.reg),
                        type=COALESCE(excluded.type, aircraft.type),
                        tries=aircraft.tries+1, last_ts=excluded.last_ts""",
                   (hexid, reg, typ, int(time.time())))
        db.commit()
        time.sleep(LOOKUP_SLEEP_S)
    return asked, found


def apply_cache(db):
    """เท cache ลง sightings ที่ยัง pending: รู้ทะเบียนแล้ว → reg_state=1, ถามครบแล้วยังไม่รู้ → 2.
    (คนละ statement เพราะ 2 = 'เลิกถาม' ต้องรอให้ tries ครบก่อน ไม่ใช่แค่ reg เป็น NULL)"""
    cur = db.execute("""
        UPDATE sightings SET reg = (SELECT a.reg FROM aircraft a WHERE a.hex = sightings.hex),
                             reg_state = 1
        WHERE reg_state = 0
          AND EXISTS (SELECT 1 FROM aircraft a WHERE a.hex = sightings.hex AND a.reg IS NOT NULL)""")
    resolved = cur.rowcount
    cur = db.execute("""
        UPDATE sightings SET reg_state = 2
        WHERE reg_state = 0
          AND EXISTS (SELECT 1 FROM aircraft a
                      WHERE a.hex = sightings.hex AND a.reg IS NULL AND a.tries >= ?)""",
                     (REG_MAX_TRIES,))
    gave_up = cur.rowcount
    db.commit()
    return resolved, gave_up


def main():
    if not os.path.exists(DB):
        return                                     # flight_watcher ยังไม่สร้าง DB
    db = sqlite3.connect(DB, timeout=30)           # รอ lock ได้ (flight_watcher เขียนพร้อมกัน)
    try:
        ensure_schema(db)
        pending = db.execute("SELECT COUNT(*) FROM sightings WHERE reg_state=0").fetchone()[0]
        if not pending:
            return                                 # ไม่มีอะไรค้าง = เงียบ (timer ทุก 10 นาที)
        asked, found = resolve_unknown(db)
        resolved, gave_up = apply_cache(db)
        if asked or resolved or gave_up:
            log(f"ถาม {asked} ลำ (เจอ {found}) → เติมทะเบียน {resolved} แถว, ยอมแพ้ {gave_up} แถว "
                f"(ค้างก่อนรอบนี้ {pending})")
    except sqlite3.Error as e:
        log("DB error:", e)
    finally:
        db.close()


if __name__ == "__main__":
    main()
