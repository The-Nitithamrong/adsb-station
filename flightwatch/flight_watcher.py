#!/usr/bin/env python3
"""flight_watcher.py — เตือนเมื่อเที่ยวบินการบินไทย (THA) inbound เข้า VTBS เหลือ ETA <= 30 นาที
อ่าน SBS stream (30003) แบบ line-by-line, filter THA + inbound, คำนวณ ETA,
dedupe 1 ครั้ง/เที่ยว, ยิง Telegram + บันทึก SQLite

รัน: python3 flight_watcher.py   (Ctrl+C ออก)
walk-before-run: ทดสอบบนสถานีบ้านก่อน แล้วย้าย reference ไป OPC ทีหลัง
"""
import socket, time, math, sqlite3, urllib.request, urllib.parse

# ---------- config ----------
HOST, PORT = "127.0.0.1", 30003
DEST_LAT, DEST_LON = 13.6900, 100.7501   # VTBS สุวรรณภูมิ (เปลี่ยนเป็นพิกัด OPC จริงได้)
WATCH_PREFIX = "THA"                      # การบินไทย (ไม่ปน AIQ/NOK/BKP)
ETA_ALERT_MIN = 30                        # ยิงเมื่อ ETA <= นาที
MAX_RANGE_NM  = 250
CLEAR_SEC     = 300                       # ไม่เห็นเกินนี้ = ลบ state (ให้ arrival รอบใหม่ยิงได้อีก)
ENV_FILE      = "/etc/fr24-watchdog.env"  # reuse TG_API / TG_CHAT ตัวเดิม
DB_FILE       = "/home/arin/flightwatch.db"
FIELDS = {"callsign": 10, "alt": 11, "gs": 12, "trk": 13, "lat": 14, "lon": 15}

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
    ah = p["alt_hist"]
    descending = len(ah) >= 2 and ah[-1] <= ah[0]
    low = p.get("alt") is not None and p["alt"] < 25000
    return closing and (descending or low)

def eta_min(p):
    if not p.get("gs") or p["gs"] <= 0 or p.get("dist") is None:
        return None
    return p["dist"] / p["gs"] * 60.0

def parse(line):
    f = line.split(",")
    if len(f) < 22 or f[0] != "MSG":
        return
    hexid = f[4].strip()
    if not hexid:
        return
    p = flights.setdefault(hexid, {"callsign": "", "alt": None, "gs": None,
                                   "lat": None, "lon": None, "dist": None,
                                   "dist_hist": [], "alt_hist": [], "notified": False})
    for k, i in FIELDS.items():
        v = f[i].strip()
        if not v:
            continue
        if k == "callsign":
            p["callsign"] = v
        elif k in ("alt", "gs", "trk"):
            try: p[k] = int(v)
            except ValueError: pass
        else:
            try: p[k] = float(v)
            except ValueError: pass
    p["ts"] = time.time()

    # อัปเดตระยะเมื่อมี position ใหม่
    if p["lat"] is not None and p["lon"] is not None:
        p["dist"] = haversine_nm(p["lat"], p["lon"], DEST_LAT, DEST_LON)
        p["dist_hist"] = (p["dist_hist"] + [p["dist"]])[-6:]
        if p["alt"] is not None:
            p["alt_hist"] = (p["alt_hist"] + [p["alt"]])[-6:]
        check(hexid, p)

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
        msg = (f"✈️ {cs} inbound VTBS\n"
               f"ETA ~{e:.0f} นาที | {p['dist']:.0f} nm | "
               f"FL{(p['alt'] or 0)//100:03d} | {p['gs']} kt\n"
               f"hex {hexid} | {time.strftime('%H:%M')}")
        notify(msg)
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                   (int(time.time()), cs, hexid, round(e, 1),
                    round(p["dist"], 1), p["gs"], p["alt"]))
        db.commit()

def prune():
    now = time.time()
    for h in [h for h, p in flights.items() if now - p.get("ts", 0) > CLEAR_SEC]:
        del flights[h]

# ---------- main ----------
def main():
    print(f"flight_watcher: THA inbound VTBS, alert ETA<={ETA_ALERT_MIN}m "
          f"| TG={'on' if TG_API else 'OFF'}")
    last_prune = 0
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
        except (OSError, ConnectionError) as e:
            print("reconnect in 5s:", e); time.sleep(5)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\nออกแล้ว")
