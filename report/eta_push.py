#!/usr/bin/env python3
"""eta_push.py — ส่ง ETA เที่ยวบินการบินไทย (THA) inbound VTBS ไป geofence/shuttle worker (busandgo).

ป้อน crew-transport (shuttle) ที่ต้องรู้ว่าเครื่อง TG จะถึงกี่โมง → ทริก geofence.
ต่างจากตัวอื่นที่ยิงขึ้น cloud:
  - inbound_push.py → Cloudflare D1, "ทุกสายการบิน", live table (ให้ web app อ่าน)
  - outbox.py       → Cloudflare D1, history (events+tracks) แบบ append
  - ตัวนี้           → HTTP POST ตรงไป Cloudflare Worker /flights/eta, "เฉพาะ THA", live ETA

สัญญา (contract) ฝั่ง worker:
  POST <ETA_INGEST_URL>   Authorization: Bearer <ETA_INGEST_KEY>
  {"source":"pi-radar","updates":[
     {"flight_number":"TG476","eta":"16:58",
      "lat":13.412,"lon":100.981,          ← ตำแหน่ง/ท่าทาง ทุกฟิลด์ optional
      "altitude_ft":12000,"ground_speed_kt":289,"track_deg":201,"distance_nm":47.3}, ...]}
  worker upsert ตาม flight_number (idempotent) — ควร age-out entry ที่เก่าเอง (เราไม่ส่ง landed)
  ฟิลด์ที่ยังไม่มีค่า "ไม่ส่งไปเลย" (ไม่ส่ง null) — upsert ด้วย null อาจไปทับค่าดีที่ worker เก็บไว้

ที่มา + การปรับ:
  - แหล่ง: /run/flight-watcher/inbound.json → inbound_all → กรอง callsign ขึ้นต้น THA
  - flight_number: callsign THA476 → TG476 (ICAO→IATA prefix swap)
  - eta: เวลานาฬิกา BKK (now + eta_min) รูป "HH:MM"
  - lat/lon/altitude_ft/ground_speed_kt/track_deg/distance_nm: ค่าดิบจาก SBS ณ ตอนนั้น
    (ไม่ปรับด้วย factor — factor แก้เฉพาะ ETA); distance_nm = ระยะถึง VTBS ไม่ใช่ถึงสถานี
  - adjust จากสถิติ: คูณ factor = median(จริง÷คำนวณ) จากตาราง tracks → ETA ตรงเวลาจริงถึงพื้นขึ้น.
    multiplicative (ไม่ใช่บวกคงที่) → สเกลตามขนาด ETA: ETA เล็ก (<10น.) ปรับน้อย ไม่ over-correct.
    self-calibrate: track สะสมมากขึ้น factor แม่นขึ้น. ข้อมูลน้อย/เพี้ยน → 1.0 (ไม่ปรับ)

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
FACTOR_REFRESH_S   = 600      # คำนวณ factor จาก DB ใหม่ทุก ~10 นาที (เปลี่ยนช้า ไม่ต้องทุกรอบ)
FACTOR_MIN_SAMPLES = 20       # ต้องมี track อย่างน้อยเท่านี้ถึงปรับ (กันข้อมูลน้อยเกิน)
FACTOR_LO, FACTOR_HI = 0.5, 1.5   # clamp ratio จริง/คำนวณ กัน track เพี้ยน (นอกช่วงนี้ทิ้ง)


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


def eta_factor():
    """median(จริง ÷ คำนวณ) จาก tracks THA — ตัวคูณปรับ ETA แบบ multiplicative (ผ่าน origin).
    ใช้คูณ ดีกว่าบวก: correction สเกลตามขนาด ETA → ETA เล็ก (<10น.) ปรับน้อย ไม่ over-correct.
    (เดิม bias −2น. แบบบวกคงที่ตัด ETA 3น. เหลือ 1น. = เพี้ยน 67%; factor ~0.92 → 2.76น. ตรงกว่า
     และที่ alt→0 ⇒ ETA→0 สมเหตุผล ไม่เหมือน offset ที่บอก "ลงไปแล้ว −2น.")
    actual = (last_ts−alert_ts)/60 + last_alt/900 (บวก final ที่มองไม่เห็น); ratio = actual / alert_eta.
    อ่าน DB read-only (flight_watcher เขียนอยู่). ข้อมูลน้อย/เพี้ยน → 1.0 (ไม่ปรับ)."""
    try:
        db = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return 1.0
    try:
        rows = db.execute(
            "SELECT alert_ts, alert_eta, last_ts, last_alt FROM tracks "
            "WHERE watched=1 AND alert_ts IS NOT NULL AND alert_eta IS NOT NULL "
            "AND last_ts IS NOT NULL").fetchall()
    except sqlite3.Error:
        return 1.0
    finally:
        db.close()
    ratios = []
    for alert_ts, alert_eta, last_ts, last_alt in rows:
        if not alert_eta or alert_eta < 5:        # กันหารด้วยค่าน้อย/ศูนย์ (alert_eta ปกติ 15-30)
            continue
        actual = (last_ts - alert_ts) / 60.0 + (last_alt or 0) / DESCENT_FPM
        r = actual / alert_eta
        if FACTOR_LO <= r <= FACTOR_HI:
            ratios.append(r)
    if len(ratios) < FACTOR_MIN_SAMPLES:
        return 1.0
    return statistics.median(ratios)


# ตำแหน่ง/ท่าทางของเครื่อง: คีย์ใน inbound_all → ชื่อฟิลด์ตามสัญญาของ worker (+วิธีปัด).
# ทุกฟิลด์ optional — ค่าไหนยังไม่มา (SBS ส่งคนละ message type) จะไม่ใส่ลง payload เลย
# (ไม่ส่ง null: worker upsert ตาม flight_number ค่า null อาจไปทับค่าดีที่มีอยู่)
POSITION_FIELDS = (
    ("lat",     "lat",             lambda v: round(float(v), 4)),
    ("lon",     "lon",             lambda v: round(float(v), 4)),
    ("alt",     "altitude_ft",     int),
    ("gs",      "ground_speed_kt", int),
    ("trk",     "track_deg",       int),
    ("dist_nm", "distance_nm",     lambda v: round(float(v), 1)),
)


def position_fields(a):
    """ดึงฟิลด์ตำแหน่งที่ 'มีจริง' จาก 1 ลำใน inbound_all → dict พร้อมใส่ใน update."""
    out = {}
    for src, dst, conv in POSITION_FIELDS:
        v = a.get(src)
        if v is None:
            continue
        try:
            out[dst] = conv(v)
        except (TypeError, ValueError):
            pass
    return out


def build_updates(inb, factor):
    """THA ทุกลำใน inbound_all → [{flight_number, eta:"HH:MM" BKK, +ตำแหน่ง}] (คูณ factor แล้ว).
    ตำแหน่ง/alt/gs/track/dist เป็นค่าดิบ ณ ตอนนั้น (ไม่ปรับด้วย factor — factor แก้ ETA เท่านั้น)."""
    ups = []
    for a in inb.get("inbound_all", []):
        fn = flight_number(a.get("flight"))
        if fn is None:
            continue
        em = a.get("eta_min")
        if em is None:
            continue
        corrected = max(0.0, em * factor)
        # เวลานาฬิกา BKK = gmtime(landing_epoch + 7ชม.) — ไม่ผูกกับ tz ของเครื่อง
        landing = time.gmtime(time.time() + corrected * 60 + 7 * 3600)
        up = {"flight_number": fn, "eta": time.strftime("%H:%M", landing)}
        up.update(position_fields(a))
        ups.append(up)
    return ups


# UA ที่ดูเหมือน browser — เลี่ยง Cloudflare Bot-Fight/Browser-Integrity ที่ตี default `Python-urllib`
# เป็น bot แล้วบล็อกที่ขอบก่อนถึง Worker (HTTP 403 "error code: 1010"). พิสูจน์แล้วว่าใส่แล้วผ่าน 200.
USER_AGENT = "Mozilla/5.0 (pi-radar; adsb-station eta_push)"


def post(updates):
    body = json.dumps({"source": SOURCE, "updates": updates}).encode()
    req = urllib.request.Request(
        INGEST_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": USER_AGENT,
                 "Authorization": f"Bearer {INGEST_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode(errors="ignore")


def applied_note(body):
    """สรุป applied/skipped จาก response ของ worker → บอกว่ามัน 'เก็บ' หรือ 'ทิ้ง' เที่ยวที่ส่งไป.
    HTTP 200 = worker รับ request เฉย ๆ; applied=0/skipped>0 = ทิ้ง (เที่ยวไม่อยู่ใน roster)."""
    try:
        j = json.loads(body)
    except ValueError:
        return ""
    if "applied" in j or "skipped" in j:
        return f" (applied {j.get('applied')}, skipped {j.get('skipped')})"
    return ""


_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    # key ต้องมี + เป็น ASCII: อักขระ non-ASCII (เช่น เผลอวาง placeholder ไทย) ใส่ใน HTTP header ไม่ได้
    # → เดิมทำ daemon crash-loop (UnicodeEncodeError ไม่ถูกจับ). กันไว้ที่นี่: ข้ามการส่งแทนที่จะพัง.
    key_ok = bool(INGEST_KEY) and INGEST_KEY.isascii()
    if not INGEST_KEY:
        print(f"eta_push: ETA_INGEST_KEY ไม่พบใน {ENV_FILE} — จะข้ามการส่งจนกว่าจะตั้งค่า (แล้ว restart)")
    elif not key_ok:
        print("eta_push: ETA_INGEST_KEY มีอักขระ non-ASCII (เผลอวาง placeholder?) — จะข้ามการส่ง (แก้ค่าแล้ว restart)")
    print(f"eta_push เริ่มทำงาน — THA→{INGEST_URL} ทุก {PUSH_EVERY_S}s")

    factor, factor_t = 1.0, 0.0
    while not _stop:
        now = time.time()
        if now - factor_t > FACTOR_REFRESH_S:
            factor, factor_t = eta_factor(), now
            print(f"eta factor refresh: ×{factor:.3f} (median จริง÷คำนวณ, tracks THA)")
        inb = read_inbound()
        if inb and key_ok:
            ups = build_updates(inb, factor)
            if ups:
                try:
                    st, body = post(ups)
                    print(f"pushed {len(ups)} THA ETA (×{factor:.3f}) → HTTP {st}{applied_note(body)}")
                except (urllib.error.URLError, OSError, UnicodeError) as e:
                    print("push ไม่สำเร็จ (retry รอบหน้า):", e)
        for _ in range(PUSH_EVERY_S):        # นอนสั้น ๆ ให้ตอบ SIGTERM ไว
            if _stop:
                break
            time.sleep(1)
    print("eta_push หยุด")


if __name__ == "__main__":
    main()
