#!/usr/bin/env python3
"""flight_watcher.py — เตือนเมื่อเที่ยวบินการบินไทย (THA) inbound เข้า VTBS เหลือ ETA <= 30 นาที
อ่าน SBS stream (30003) แบบ line-by-line, filter THA + inbound, คำนวณ ETA,
dedupe 1 ครั้ง/เที่ยว, ยิง Telegram + บันทึก SQLite

รัน: python3 flight_watcher.py   (Ctrl+C ออก)
walk-before-run: ทดสอบบนสถานีบ้านก่อน แล้วย้าย reference ไป OPC ทีหลัง
"""
import socket, time, math, sqlite3, urllib.request, urllib.parse, json, os

# ---------- config ----------
HOST, PORT = "127.0.0.1", 30003
DEST_LAT, DEST_LON = 13.6900, 100.7501   # VTBS สุวรรณภูมิ (เปลี่ยนเป็นพิกัด OPC จริงได้)
WATCH_PREFIX = "THA"                      # การบินไทย (ไม่ปน AIQ/NOK/BKP)
ETA_ALERT_MIN = 30                        # ยิงเมื่อ ETA <= นาที
ETA_DESCENT_FPM = 900                     # ft/min เฉลี่ยขณะ descend — ใช้ทำ ETA จากความสูง
#   จูนจากข้อมูลจริง 1146 เที่ยว THA (track_stats): 750 คิด ETA ยาวไปเฉลี่ย ~4.3 นาที; อัตราร่อนจริงจาก
#   alt@STAR-gate ÷ เวลาถึงพื้น ≈ 863-969 fpm (เฉลี่ย ~890) → 900 ปิด gap ได้เกือบหมด.
#   anchor จริง (STAR EASTE 1C RNAV เข้า VTBS): ผ่าน STAR ~16000-18000 ft → เหลือ ~20-25 นาทีถึงพื้น
#   16000/750=21m, 18000/750=24m ✓  (dist/gs เชื่อไม่ได้: STAR ไม่บินตรง + gs ลดตอน descend)
MAX_RANGE_NM  = 250
CLEAR_SEC     = 300                       # ไม่เห็นเกินนี้ = ลบ state (ให้ arrival รอบใหม่ยิงได้อีก)
HIST_MIN_SEC  = 15                        # เว้นระยะเวลาเก็บ dist/alt history อย่างน้อยเท่านี้ (ดู parse)
VRATE_CLIMB_FPM = 300                     # vertical rate เกินนี้ = ไต่ขึ้น (ขาออก) → ตัดออกจาก inbound
VRATE_DESC_FPM  = 200                     # vertical rate ต่ำกว่า -นี้ = ร่อนลง (ขาเข้า) โดยตรง
# จุดเข้า STAR ของ VTBS (ทุกจุด FL180) — จากชาร์ต RNAV. ใช้ tag ว่าเครื่องเข้า arrival ทางไหน + กี่โมง
STAR_FIXES = {
    "WILLA": (14.405, 100.060),   # NW  N14 24.3 E100 03.6
    "NORTA": (14.718, 100.638),   # N   N14 43.1 E100 38.3
    "EASTE": (14.310, 101.287),   # NE  N14 18.6 E101 17.2
    "TUMGA": (13.355, 101.202),   # SE  N13 21.3 E101 12.1
    "LEBIM": (13.087, 100.473),   # SW  N13 05.2 E100 28.4
}
STAR_FIX_RADIUS_NM = 8                     # ผ่านใกล้ fix ในรัศมีนี้ = ถือว่าเข้า STAR ที่ gate นั้น
#   หมายเหตุ: gate ไกล (EASTE 48nm / WILLA-NORTA 59-62nm) อยู่ FL180 ซึ่ง "ต่ำกว่า" radio horizon
#   ที่ระยะนั้น (จากข้อมูลจริง floor 40-60nm ~21000ft) → มักจับไม่ได้ที่ตัว gate. TUMGA(33)/LEBIM(40) มีโอกาสกว่า
ENV_FILE      = "/etc/fr24-watchdog.env"  # reuse TG_API / TG_CHAT ตัวเดิม
DB_FILE       = "/home/arin/flightwatch.db"
INBOUND_FILE  = "/run/flight-watcher/inbound.json"  # Pixoo THA-inbound page อ่านไฟล์นี้ (RuntimeDirectory)
INBOUND_MAX_MIN = 90                      # เขียนให้ Pixoo เฉพาะ ETA <= นี้ (ไกลกว่านั้นยังไม่โชว์)
INBOUND_WRITE_SEC = 10                    # throttle การเขียน inbound.json
LIST_MAX = 5                              # กี่เครื่องใน list บน Pixoo (แถวที่พอดีจอ)
TRACK_NEAR_NM = 60                        # บันทึก track เฉพาะเที่ยวที่เข้าใกล้ VTBS <= นี้ (approach/arrival; กัน overflight ไกล)
TRACK_MIN_SAMPLES = 5                     # ต้องมี position fix อย่างน้อยเท่านี้ถึงบันทึก (กัน noise)
FIELDS = {"callsign": 10, "alt": 11, "gs": 12, "trk": 13, "lat": 14, "lon": 15, "vrate": 16}

# ---------- Telegram (reuse env เดิม) ----------
def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env

_env = load_env(ENV_FILE)
TG_API, TG_CHAT = _env.get("TG_API"), _env.get("TG_CHAT")

def notify(text):
    print(">>> ALERT:", text.replace("\n", " | "))
    if not TG_API:
        print("    (TG_API ไม่พบ — ข้ามการส่ง Telegram)")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
        urllib.request.urlopen(TG_API, data=data, timeout=15)
    except Exception as e:
        print("    telegram error:", e)

# ---------- DB ----------
db = sqlite3.connect(DB_FILE)
db.execute("""CREATE TABLE IF NOT EXISTS events(
    ts INTEGER, flight TEXT, hex TEXT, eta_min REAL, dist_nm REAL, gs INTEGER, alt INTEGER)""")
# tracks = สรุป 1 แถว/เที่ยว ตอนเครื่องหายไป — ไว้ calibrate ETA จริง + หา coverage floor
# (straight-line ETA เชื่อไม่ได้: STAR ไม่บินตรงเข้า + gs ลดตอน descend)
db.execute("""CREATE TABLE IF NOT EXISTS tracks(
    hex TEXT, flight TEXT, watched INTEGER,
    first_ts INTEGER, last_ts INTEGER, samples INTEGER,
    min_dist_nm REAL, alt_at_min INTEGER, min_alt INTEGER,
    last_dist_nm REAL, last_alt INTEGER, max_dist_nm REAL,
    alert_ts INTEGER, alert_eta REAL,
    star_fix TEXT, star_alt INTEGER, star_ts INTEGER)""")
# migrate DB เก่าที่ยังไม่มีคอลัมน์ star_* (idempotent)
for _col, _typ in (("star_fix", "TEXT"), ("star_alt", "INTEGER"), ("star_ts", "INTEGER")):
    try:
        db.execute(f"ALTER TABLE tracks ADD COLUMN {_col} {_typ}")
    except sqlite3.OperationalError:
        pass
db.commit()

# ---------- helpers ----------
def haversine_nm(la1, lo1, la2, lo2):
    R = 3440.065
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

flights = {}   # hex -> state

def is_inbound(p):
    dh = p["dist_hist"]
    if len(dh) < 3:
        return False
    closing = dh[-1] < dh[0] - 1          # ระยะลดลงจริง (>1nm กันสั่น)
    vr = p.get("vrate")                    # SBS field 16 (type 4 velocity): +ไต่ / −ร่อน ft/min
    if vr is not None and vr > VRATE_CLIMB_FPM:
        return False                       # กำลังไต่ขึ้น = ขาออก → ไม่ใช่ inbound (แม้ closing+low)
    ah = p["alt_hist"]
    # ร่อนลง = vrate ติดลบชัด (ตรง+ไว ต่อ message) หรือ fallback: alt แนวโน้มลด (ตอน vrate ยังไม่มา)
    descending = (vr is not None and vr < -VRATE_DESC_FPM) or (len(ah) >= 2 and ah[-1] <= ah[0])
    low = p.get("alt") is not None and p["alt"] < 25000
    return closing and (descending or low)

def eta_min(p):
    """ETA จากความสูง (ดีกว่าเส้นตรงสำหรับ STAR arrival): เหลือ ~ alt / descent_rate นาที.
    ถ้าไม่มี alt ค่อย fallback เป็นเส้นตรง dist/gs (เชื่อได้น้อย)."""
    a = p.get("alt")
    if a is not None and a > 0:
        return a / ETA_DESCENT_FPM
    if p.get("gs") and p["gs"] > 0 and p.get("dist") is not None:
        return p["dist"] / p["gs"] * 60.0
    return None

def parse(line):
    f = line.split(",")
    if len(f) < 22 or f[0] != "MSG":
        return
    hexid = f[4].strip()
    if not hexid:
        return
    p = flights.setdefault(hexid, {"callsign": "", "alt": None, "gs": None, "vrate": None,
                                   "lat": None, "lon": None, "dist": None,
                                   "dist_hist": [], "alt_hist": [], "notified": False,
                                   "first_ts": None, "samples": 0, "min_dist": None,
                                   "alt_at_min": None, "min_alt": None, "max_dist": None,
                                   "alert_ts": None, "alert_eta": None, "last_hist_ts": 0,
                                   "star_fix": None, "star_alt": None, "star_ts": None})
    for k, i in FIELDS.items():
        v = f[i].strip()
        if not v:
            continue
        if k == "callsign":
            p["callsign"] = v
        elif k in ("alt", "gs", "trk", "vrate"):
            try: p[k] = int(v)
            except ValueError: pass
        else:
            try: p[k] = float(v)
            except ValueError: pass
    p["ts"] = time.time()

    # อัปเดตระยะเมื่อมี position ใหม่
    if p["lat"] is not None and p["lon"] is not None:
        p["dist"] = haversine_nm(p["lat"], p["lon"], DEST_LAT, DEST_LON)
        # เก็บ dist/alt history แบบเว้นเวลา >= HIST_MIN_SEC (ไม่ใช่ทุก fix): dump1090 ส่ง position หลาย
        # fix/วินาที — ถ้าเก็บทุก fix, 6 ตัวหลังกินเวลา <1 วิ → ระยะแทบไม่เปลี่ยน → closing (>1nm) ไม่เคยจริง
        # → is_inbound False ตลอด → พลาด inbound เกือบทั้งหมด (obs: THA ลง VTBS 8 เที่ยว แต่ events=0).
        # เว้น 15 วิ → 6 ตัวกินเวลา ~75 วิ → closing วัดการเข้าใกล้จริงในช่วงเวลาที่มีความหมาย.
        if p["ts"] - p["last_hist_ts"] >= HIST_MIN_SEC:
            p["dist_hist"] = (p["dist_hist"] + [p["dist"]])[-6:]
            if p["alt"] is not None:
                p["alt_hist"] = (p["alt_hist"] + [p["alt"]])[-6:]
            p["last_hist_ts"] = p["ts"]
        accumulate(p)
        detect_star(p)
        check(hexid, p)

def detect_star(p):
    """ผ่านใกล้จุดเข้า STAR จุดไหน (จุดแรก) = tag arrival gate + เวลา + alt ตอนนั้น"""
    if p["star_fix"] is not None or p.get("lat") is None or p.get("lon") is None:
        return
    for name, (la, lo) in STAR_FIXES.items():
        if haversine_nm(p["lat"], p["lon"], la, lo) <= STAR_FIX_RADIUS_NM:
            p["star_fix"] = name
            p["star_ts"] = int(p["ts"])
            p["star_alt"] = p.get("alt")
            print(f"  STAR entry {p['callsign'].strip() or '?':8} via {name}  FL{(p.get('alt') or 0)//100:03d}")
            return

def accumulate(p):
    """สะสมสถิติ track: ระยะใกล้สุด + alt ที่จุดนั้น, alt ต่ำสุด, ระยะไกลสุด, จำนวน fix"""
    if p["first_ts"] is None:
        p["first_ts"] = p["ts"]
    p["samples"] += 1
    d = p["dist"]
    if p["min_dist"] is None or d < p["min_dist"]:
        p["min_dist"] = d
        p["alt_at_min"] = p.get("alt")     # ← ความสูงตอนเข้าใกล้สนามสุด = coverage floor
    if p["max_dist"] is None or d > p["max_dist"]:
        p["max_dist"] = d
    a = p.get("alt")
    if a is not None and (p["min_alt"] is None or a < p["min_alt"]):
        p["min_alt"] = a

def check(hexid, p):
    cs = p["callsign"].strip()
    if not cs.startswith(WATCH_PREFIX) or p["notified"]:
        return
    if p["dist"] is None or p["dist"] > MAX_RANGE_NM:
        return
    if not is_inbound(p):
        return
    e = eta_min(p)
    if e is None:
        return
    # log ระหว่าง track (เห็นตอนทดสอบว่ากำลังนับถอยหลัง)
    print(f"  tracking {cs:8} ETA {e:4.0f}m  {p['dist']:5.0f}nm  "
          f"FL{(p['alt'] or 0)//100:03d}  {p['gs']}kt")
    if e <= ETA_ALERT_MIN:
        p["notified"] = True
        p["alert_ts"] = int(time.time()); p["alert_eta"] = round(e, 1)   # ไว้เทียบ ETA จริงใน tracks
        # ไม่ยิง Telegram ต่อเที่ยวแล้ว (noise) — ย้ายไป daily digest 09:00 (report/daily_status.py).
        # ยังบันทึก events + inbound.json (Pixoo) + tracks ตามเดิม เพื่อวิเคราะห์/แสดงผล.
        print(f"  >>> {cs} inbound VTBS ETA ~{e:.0f}m (logged, no TG)")
        # ระบุชื่อคอลัมน์ชัด — outbox.py เติมคอลัมน์ `sent` (ALTER) → ตารางกลายเป็น 8 คอลัมน์;
        # `INSERT ... VALUES` แบบไม่ระบุชื่อจะพังเพราะให้ 7 ค่า (crash-loop). ระบุชื่อ → `sent` ได้ default.
        db.execute("INSERT INTO events (ts,flight,hex,eta_min,dist_nm,gs,alt) VALUES (?,?,?,?,?,?,?)",
                   (int(time.time()), cs, hexid, round(e, 1),
                    round(p["dist"], 1), p["gs"], p["alt"]))
        db.commit()

def record_track(hexid, p):
    """บันทึก 1 แถวลง tracks ตอนเครื่องหายไป — เฉพาะเที่ยวที่เข้าใกล้สนามพอควร"""
    if p.get("samples", 0) < TRACK_MIN_SAMPLES or p.get("min_dist") is None:
        return
    if p["min_dist"] > TRACK_NEAR_NM:      # ไม่เคยเข้าใกล้ VTBS → ไม่เก็บ (overflight ไกล)
        return
    cs = p.get("callsign", "").strip()
    db.execute(
        "INSERT INTO tracks (hex,flight,watched,first_ts,last_ts,samples,"
        "min_dist_nm,alt_at_min,min_alt,last_dist_nm,last_alt,max_dist_nm,"
        "alert_ts,alert_eta,star_fix,star_alt,star_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            hexid, cs, 1 if cs.startswith(WATCH_PREFIX) else 0,
            int(p["first_ts"]) if p.get("first_ts") else None,
            int(p.get("ts", time.time())), p["samples"],
            round(p["min_dist"], 1), p.get("alt_at_min"), p.get("min_alt"),
            round(p["dist"], 1) if p.get("dist") is not None else None, p.get("alt"),
            round(p["max_dist"], 1) if p.get("max_dist") is not None else None,
            p.get("alert_ts"), p.get("alert_eta"),
            p.get("star_fix"), p.get("star_alt"), p.get("star_ts")))
    db.commit()

def prune():
    now = time.time()
    for h in [h for h, p in flights.items() if now - p.get("ts", 0) > CLEAR_SEC]:
        record_track(h, flights[h])
        del flights[h]

def current_inbound():
    """THA inbound ที่ ETA น้อยสุด สำหรับโชว์บน Pixoo — แยกจาก alert logic
    (โชว์ได้แม้ยัง > ETA_ALERT_MIN และแม้ notified ไปแล้ว)"""
    best = None
    for hexid, p in flights.items():
        if not p.get("callsign", "").strip().startswith(WATCH_PREFIX):
            continue
        if p.get("dist") is None or p["dist"] > MAX_RANGE_NM or not is_inbound(p):
            continue
        e = eta_min(p)
        if e is None or e > INBOUND_MAX_MIN:
            continue
        if best is None or e < best["eta_min"]:
            best = {"flight": p["callsign"].strip(), "eta_min": round(e, 1),
                    "dist_nm": round(p["dist"], 1), "alt": p.get("alt"),
                    "gs": p.get("gs"), "hex": hexid, "ts": time.time()}
    return best

def all_inbound(limit=30):
    """ทุกเครื่อง (ทุกสายการบิน) ที่กำลัง inbound เข้า VTBS ตอนนี้ + ETA เรียงใกล้ถึงก่อน.
    เหมือน current_inbound แต่ไม่กรอง THA — ป้อน inbound_push (web app ภายนอกที่อยากได้ทุกสาย)
    และ eta_push (กรอง THA แล้วส่งต่อ worker). lat/lon/trk ใส่มาด้วยเพื่อให้ผู้บริโภคปลายทางวาด
    ตำแหน่งจริงบนแผนที่ได้ ไม่ใช่แค่ตัวเลข ETA (เพิ่มคีย์อย่างเดียว = ของเดิมไม่พัง)."""
    out = []
    for hexid, p in flights.items():
        if p.get("dist") is None or p["dist"] > MAX_RANGE_NM or not is_inbound(p):
            continue
        e = eta_min(p)
        if e is None or e > INBOUND_MAX_MIN:
            continue
        out.append({"flight": p.get("callsign", "").strip() or hexid.upper(), "hex": hexid,
                    "eta_min": round(e, 1), "dist_nm": round(p["dist"], 1),
                    "alt": p.get("alt"), "gs": p.get("gs"),
                    # 4 ตำแหน่งทศนิยม ~11 m — พอสำหรับแผนที่/เส้นทาง และไฟล์ไม่บวม
                    "lat": round(p["lat"], 4) if p.get("lat") is not None else None,
                    "lon": round(p["lon"], 4) if p.get("lon") is not None else None,
                    "trk": p.get("trk")})
    out.sort(key=lambda x: x["eta_min"])
    return out[:limit]

def flights_list():
    """ทุกเครื่องที่มี position ตอนนี้ เรียงใกล้→ไกล (สำหรับหน้า list บน Pixoo)"""
    out = []
    for hexid, p in flights.items():
        if p.get("dist") is None:
            continue
        cs = (p.get("callsign", "").strip() or hexid.upper())[:6]
        out.append({"cs": cs, "dist": round(p["dist"]), "fl": (p.get("alt") or 0) // 100})
    out.sort(key=lambda x: x["dist"])
    return out

def write_inbound():
    """atomic write inbound.json: THA inbound (soonest) + list เครื่องที่รับได้ทั้งหมด"""
    data = current_inbound() or {"flight": None}
    data["ts"] = time.time()
    lst = flights_list()
    data["nrx"] = len(lst)               # จำนวนเครื่องที่รับได้ทั้งหมด
    data["list"] = lst[:LIST_MAX]        # เฉพาะที่โชว์บนจอ
    data["inbound_all"] = all_inbound()  # ทุกสายการบินที่ inbound + ETA (ป้อน inbound_push → D1)
    try:
        os.makedirs(os.path.dirname(INBOUND_FILE), exist_ok=True)
        tmp = INBOUND_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, INBOUND_FILE)
    except OSError as e:
        print("inbound write skipped:", e)   # เช่น รัน manual ไม่มี /run/flight-watcher

# ---------- main ----------
def main():
    print(f"flight_watcher: THA inbound VTBS, alert ETA<={ETA_ALERT_MIN}m "
          f"| TG={'on' if TG_API else 'OFF'}")
    last_prune = last_inbound = 0
    while True:
        try:
            s = socket.create_connection((HOST, PORT)); s.settimeout(30)
            buf = ""
            while True:
                data = s.recv(4096).decode(errors="ignore")
                if not data:
                    raise ConnectionError("stream closed")
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    parse(line.strip())
                if time.time() - last_prune > 30:
                    prune(); last_prune = time.time()
                if time.time() - last_inbound > INBOUND_WRITE_SEC:
                    write_inbound(); last_inbound = time.time()
        except (OSError, ConnectionError) as e:
            print("reconnect in 5s:", e); time.sleep(5)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\nออกแล้ว")
