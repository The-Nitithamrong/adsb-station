#!/usr/bin/env python3
"""eta_push.py — ส่ง ETA เที่ยวบินการบินไทย (THA) inbound VTBS ไป geofence/shuttle worker (busandgo).

ป้อน crew-transport (shuttle) ที่ต้องรู้ว่าเครื่อง TG จะถึงกี่โมง → ทริก geofence.
ต่างจากตัวอื่นที่ยิงขึ้น cloud:
  - inbound_push.py → Cloudflare D1, "ทุกสายการบิน", live table (ให้ web app อ่าน)
  - outbox.py       → Cloudflare D1, history (events+tracks) แบบ append
  - ตัวนี้           → HTTP POST ตรงไป Cloudflare Worker /flights/eta, "เฉพาะ THA", live ETA

สัญญา (contract) ฝั่ง worker:
  POST <ETA_INGEST_URL>   Authorization: Bearer <ETA_INGEST_KEY>
  {"source":"pi-radar","updates":[{"flight_number":"TG476","eta":"16:58"}, ...]}
  worker upsert ตาม flight_number (idempotent) — ควร age-out entry ที่เก่าเอง (เราไม่ส่ง landed)

ที่มา + การปรับ:
  - แหล่ง: /run/flight-watcher/inbound.json → inbound_all → กรอง callsign ขึ้นต้น THA
  - flight_number: callsign THA476 → TG476 (ICAO→IATA prefix swap)
  - eta: เวลานาฬิกา BKK (now + eta_min) รูป "HH:MM"
  - adjust จากสถิติ: บวก bias = median(จริง−คำนวณ) จากตาราง tracks (track_stats §4) → ETA ตรงเวลาจริง
    ถึงพื้นมากขึ้น. self-calibrate: track สะสมมากขึ้น bias แม่นขึ้น. ข้อมูลน้อย/เพี้ยน → 0 (ไม่ปรับ)

stdlib ล้วน (urllib+sqlite3). daemon (Type=simple, User=arin) ยิงทุก ~30s เฉพาะเมื่อมี THA inbound.
config/secrets ใน /etc/fr24-watchdog.env: ETA_INGEST_KEY (ลับ), ETA_INGEST_URL (opt, มี default).
"""
import json
import os
import signal
import sqlite3
import statistics
import time
import urllib.error
import urllib.request

ENV_FILE     = "/etc/fr24-watchdog.env"
INBOUND_FILE = "/run/flight-watcher/inbound.json"
DB_FILE      = "/home/arin/flightwatch.db"
DEFAULT_URL  = "https://busandgo-geofence-worker.doabusandgo.workers.dev/flights/eta"
SOURCE       = "pi-radar"
WATCH_PREFIX = "THA"          # การบินไทย (ICAO callsign) — ไม่ปน AIQ/NOK/BKP
IATA_PREFIX  = "TG"           # การบินไทย (IATA flight_number ที่ worker ใช้)
DESCENT_FPM  = 900            # ต้องตรงกับ flight_watcher.ETA_DESCENT_FPM (คิด final ที่มองไม่เห็น)
PUSH_EVERY_S     = 30         # ยิงทุกกี่วินาที
INBOUND_STALE_S  = 120        # inbound.json เก่ากว่านี้ = flight_watcher ไม่เดิน → ไม่ยิง
BIAS_REFRESH_S   = 600        # คำนวณ bias จาก DB ใหม่ทุก ~10 นาที (เปลี่ยนช้า ไม่ต้องทุกรอบ)
BIAS_MIN_SAMPLES = 20         # ต้องมี track อย่างน้อยเท่านี้ถึงปรับ (กันข้อมูลน้อยเกิน)
BIAS_OUTLIER_MIN = 30         # ทิ้ง diff ที่ |.| เกินนี้ (นาที) — track เพี้ยน/ค้าง


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
    """อ่าน config: env ของ process (EnvironmentFile) ก่อน แล้วค่อยไฟล์ .env (รัน manual ก็ได้)."""
    return os.environ.get(key) or ENV.get(key) or default


INGEST_URL = cfg("ETA_INGEST_URL", DEFAULT_URL)
INGEST_KEY = cfg("ETA_INGEST_KEY")


def read_inbound():
    try:
        with open(INBOUND_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - d.get("ts", 0) > INBOUND_STALE_S:   # flight_watcher หยุดเขียน → อย่ายิงของเก่า
        return None
    return d


def flight_number(callsign):
    """THA476 → TG476. ไม่ใช่ THA (สายอื่น) → None (ตัวนี้ THA-only)."""
    cs = (callsign or "").strip().upper()
    if cs.startswith(WATCH_PREFIX) and len(cs) > len(WATCH_PREFIX):
        return IATA_PREFIX + cs[len(WATCH_PREFIX):]
    return None


def eta_bias():
    """median(จริง − คำนวณ) จาก tracks THA — track_stats §4 (actual vs computed ETA).
    actual = (last_ts−alert_ts)/60 + last_alt/900 (บวก final ที่สัญญาณมองไม่เห็น); diff = actual − alert_eta.
    บวก bias นี้ให้ ETA ที่ส่งตรงเวลาจริงถึงพื้นขึ้น. อ่าน DB แบบ read-only (flight_watcher เขียนอยู่)."""
    try:
        db = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return 0.0
    try:
        rows = db.execute(
            "SELECT alert_ts, alert_eta, last_ts, last_alt FROM tracks "
            "WHERE watched=1 AND alert_ts IS NOT NULL AND alert_eta IS NOT NULL "
            "AND last_ts IS NOT NULL").fetchall()
    except sqlite3.Error:
        return 0.0
    finally:
        db.close()
    diffs = []
    for alert_ts, alert_eta, last_ts, last_alt in rows:
        actual = (last_ts - alert_ts) / 60.0 + (last_alt or 0) / DESCENT_FPM
        d = actual - alert_eta
        if abs(d) <= BIAS_OUTLIER_MIN:
            diffs.append(d)
    if len(diffs) < BIAS_MIN_SAMPLES:
        return 0.0
    return statistics.median(diffs)


def build_updates(inb, bias):
    """THA ทุกลำใน inbound_all → [{flight_number, eta:"HH:MM" BKK}] (ปรับ bias แล้ว)."""
    ups = []
    for a in inb.get("inbound_all", []):
        fn = flight_number(a.get("flight"))
        if fn is None:
            continue
        em = a.get("eta_min")
        if em is None:
            continue
        corrected = max(0.0, em + bias)
        # เวลานาฬิกา BKK = gmtime(landing_epoch + 7ชม.) — ไม่ผูกกับ tz ของเครื่อง
        landing = time.gmtime(time.time() + corrected * 60 + 7 * 3600)
        ups.append({"flight_number": fn, "eta": time.strftime("%H:%M", landing)})
    return ups


def post(updates):
    body = json.dumps({"source": SOURCE, "updates": updates}).encode()
    req = urllib.request.Request(
        INGEST_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {INGEST_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    if not INGEST_KEY:
        print(f"eta_push: ETA_INGEST_KEY ไม่พบใน {ENV_FILE} — จะข้ามการส่งจนกว่าจะตั้งค่า (แล้ว restart)")
    print(f"eta_push เริ่มทำงาน — THA→{INGEST_URL} ทุก {PUSH_EVERY_S}s")

    bias, bias_t = 0.0, 0.0
    while not _stop:
        now = time.time()
        if now - bias_t > BIAS_REFRESH_S:
            bias, bias_t = eta_bias(), now
            print(f"bias refresh: {bias:+.1f} นาที (median จริง−คำนวณ, tracks THA)")
        inb = read_inbound()
        if inb and INGEST_KEY:
            ups = build_updates(inb, bias)
            if ups:
                try:
                    st = post(ups)
                    print(f"pushed {len(ups)} THA ETA (bias {bias:+.1f}m) → HTTP {st}")
                except (urllib.error.URLError, OSError) as e:
                    print("push ไม่สำเร็จ (retry รอบหน้า):", e)
        for _ in range(PUSH_EVERY_S):        # นอนสั้น ๆ ให้ตอบ SIGTERM ไว
            if _stop:
                break
            time.sleep(1)
    print("eta_push หยุด")


if __name__ == "__main__":
    main()
