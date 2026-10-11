#!/usr/bin/env python3
"""weather_fetch.py — พยากรณ์อากาศ "ที่บ้าน" + "เมืองปลายทางของเที่ยวบินถัดไป" → Pixoo.

เขียน /run/weather/forecast.json ให้ pixoo/main.py อ่าน (สัญญา JSON ใน /run แบบเดียวกับ agenda_fetch).
รันโดย systemd timer ทุก ~1 ชม. — แบบจำลองของกรมอุตุฯ รันวันละ 4 รอบ (00/06/12/18 UTC) ถี่กว่านี้ไม่ได้อะไร.

แหล่งข้อมูล (เลือกตามประเทศของสนามบิน ไม่ใช่ตามความชอบ):
  - ในไทย  → TMD NWP API (กรมอุตุนิยมวิทยา) แบบรายวัน. ต้องมี TMD_TOKEN ใน /etc/fr24-watchdog.env
             (สมัคร https://data.tmd.go.th/nwpapi/register → Create New Token — โชว์ครั้งเดียว).
  - นอกไทย → Open-Meteo (ไม่ต้องใช้ key, ฟรีสำหรับใช้ส่วนตัว < 10,000 ครั้ง/วัน, ข้อมูล CC BY 4.0).
  ทำไมไม่ใช้ TMD ทั้งหมด: domain ใหญ่สุดของแบบจำลองคือ "เอเชียตะวันออกเฉียงใต้" (~4–22°N, ~96–106°E
  จาก grid CSV ของ hpc.tmd.go.th) — ไทเป/โตเกียว/ฮ่องกง/สิงคโปร์/ยุโรป อยู่นอกทั้งหมด. /at ของ TMD
  "snap ไป grid ที่ใกล้สุด" เสมอ → ส่งพิกัดนอก domain ไปจะได้ค่าของจุดขอบแผนที่กลับมาเงียบ ๆ (ไม่ error)
  จึงต้องเลือกแหล่งจากประเทศ + เช็คว่าจุดที่ได้กลับมาอยู่ใกล้จุดที่ขอจริง (MAX_SNAP_KM).

บ้าน: ขอ TMD ด้วยชื่ออำเภอ (WX_HOME_PROVINCE/WX_HOME_AMPHOE) ไม่ใช่พิกัด — repo นี้ public,
  พิกัดบ้านห้ามอยู่ใน git. ขอ HOME_DAYS วัน (วันนี้ + เผื่อ) ให้ Pixoo เลือกวันเองตามนาฬิกา →
  TMD ล่มไปครึ่งวันจอก็ยังมีของวันนี้โชว์ (เก็บของเดิมไว้ถ้าดึงไม่ได้ — KEEP_SEC).

ปลายทาง: route ของเที่ยวบินถัดไปจาก /run/agenda/next.json (เช่น "BKK-TPE" → TPE) → พิกัดจาก
  weather/airports.csv (OurAirports, public domain — สร้างด้วย build_airports.py). พยากรณ์ของ
  "วันที่บินถึง" ตามเวลาท้องถิ่นปลายทาง (end_ts ของ event = เวลาถึง; ไม่มีก็ใช้ start_ts).
  เกินระยะพยากรณ์ (TMD ~10 วัน, Open-Meteo 16 วัน) → days ว่าง + note "beyond" แทนที่จะเดา.

`--probe [IATA ...]` = ทดสอบกับ API จริงโดยไม่แตะ /run (ใช้ใน GitHub Actions ด้วย secret TMD_TOKEN):
  พิมพ์ HTTP status / rate-limit / โครงสร้าง JSON ดิบ + ผลที่ parse แล้ว. token ไม่ถูกพิมพ์ออกมาเลย.
stdlib ล้วน.
"""
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENV_FILE = "/etc/fr24-watchdog.env"
AGENDA_F = "/run/agenda/next.json"
OUT_DIR = "/run/weather"
OUT_F = os.path.join(OUT_DIR, "forecast.json")
AIRPORTS_F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "airports.csv")

TMD_BASE = "https://data.tmd.go.th/nwpapi/v1/forecast/location/daily"
TMD_FIELDS = "tc_max,tc_min,rain,cond"      # datapoint = สถานที่ × วัน × field (โควตา 100,000/ชม.)
OM_URL = "https://api.open-meteo.com/v1/forecast"
OM_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"
OM_DAYS = 16                                 # สูงสุดที่ Open-Meteo ให้
HOME_DAYS = 3                                # วันนี้ + 2 วัน (เผื่อ TMD ล่ม)
TH_TZ = dt.timezone(dt.timedelta(hours=7))   # TMD daily ประทับวันที่เป็นเวลาไทย (+07:00)
MAX_SNAP_KM = 50                             # grid ที่ TMD ตอบกลับต้องอยู่ใกล้จุดที่ขอ ไม่งั้น = นอก domain
KEEP_SEC = 24 * 3600                         # ดึงใหม่ไม่ได้ → ใช้ของเดิมต่อได้นานสุดเท่านี้
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (pi-radar; adsb-station weather)"

# รหัสสภาพอากาศ → "kind" ตัวเดียวที่ Pixoo รู้จัก (หน้าจอไม่ต้องรู้ว่ามาจากแหล่งไหน)
# TMD cond 1–12 (doc: data.tmd.go.th/nwpapi/doc/apidoc/forecast_location.html):
#   9–11 หนาวจัด/หนาว/เย็น และ 12 ร้อนจัด เป็นคำบอก "อุณหภูมิ" ไม่ใช่ท้องฟ้า — ไทยหน้าหนาวฟ้ามักโปร่ง
#   จึงใช้ไอคอนพระอาทิตย์แต่คนละสี (ไม่ใช้เกล็ดหิมะ: ในไทยไม่มีหิมะ จะสื่อผิด)
TMD_KIND = {1: "clear", 2: "partly", 3: "cloudy", 4: "cloudy", 5: "rain", 6: "rain", 7: "heavy",
            8: "storm", 9: "cool", 10: "cool", 11: "cool", 12: "hot"}


def om_kind(code):
    """WMO weather_code (Open-Meteo) → kind"""
    if code is None:
        return None
    c = int(code)
    if c in (0, 1):
        return "clear"
    if c == 2:
        return "partly"
    if c == 3:
        return "cloudy"
    if c in (45, 48):
        return "fog"
    if c in (65, 67, 82):
        return "heavy"
    if 51 <= c <= 67 or 80 <= c <= 81:
        return "rain"
    if 71 <= c <= 77 or c in (85, 86):
        return "snow"
    if c >= 95:
        return "storm"
    return None


# ---------------- config ----------------
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
        pass                      # root-only (chmod 600) → ค่ามาทาง EnvironmentFile ของ unit แทน
    return env


ENV = load_env(ENV_FILE)


def cfg(key, default=None):
    # os.environ ก่อน: unit รันเป็น arin แต่ไฟล์ env เป็น root-only → systemd (EnvironmentFile) ส่งเข้ามา
    # (เหมือน outbox.cfg — อ่านไฟล์เองเงียบ ๆ ได้ {} แล้วฟ้อง "ยังไม่ตั้ง" ทั้งที่ตั้งแล้ว)
    return os.environ.get(key) or ENV.get(key) or default


# ---------------- HTTP ----------------
class Fetch:
    """ผลการดึง 1 ครั้ง — เก็บ status + header rate-limit ไว้ให้ --probe รายงาน"""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = None
        self.error = None


def http_json(url, headers=None):
    f = Fetch()
    h = {"accept": "application/json", "User-Agent": USER_AGENT}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT) as r:
            f.status = r.status
            f.headers = dict(r.headers)
            f.body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        f.status = e.code
        f.headers = dict(e.headers or {})
        try:
            f.error = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            f.error = str(e)
    except Exception as e:          # DNS / timeout / JSON เสีย
        f.error = f"{type(e).__name__}: {e}"
    return f


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


# ---------------- TMD ----------------
def tmd_locations(doc):
    """รายการ location จาก response — รับได้ทั้ง 2 รูปแบบ เพราะ doc กับของจริงไม่ตรงกัน:
    ของจริง {"WeatherForecasts":[...]} (มีคนวัดจาก API จริง) · ตัวอย่างใน doc {"weather_forecast":{"locations":[...]}}"""
    if not isinstance(doc, dict):
        return []
    locs = doc.get("WeatherForecasts")
    if locs is None:
        locs = (doc.get("weather_forecast") or {}).get("locations")
    return locs if isinstance(locs, list) else []


def tmd_days(loc):
    days = []
    for fc in loc.get("forecasts") or []:
        data = fc.get("data") or {}
        cond = data.get("cond")
        days.append({
            "date": str(fc.get("time", ""))[:10],        # "2026-10-11T00:00:00+07:00" → วันที่ไทย
            "tmax": _num(data.get("tc_max")),
            "tmin": _num(data.get("tc_min")),
            "rain_mm": _num(data.get("rain")),
            "rain_pct": None,                             # TMD ไม่มี "โอกาสฝน %" — มีแต่ปริมาณ มม.
            "code": cond,
            "kind": TMD_KIND.get(int(cond)) if _num(cond) is not None else None,
        })
    return days


def tmd_daily(path, params, token):
    """คืน (location dict, days, Fetch)"""
    q = dict(params, fields=TMD_FIELDS)
    f = http_json(f"{TMD_BASE}/{path}?{urllib.parse.urlencode(q)}",
                  {"authorization": f"Bearer {token}"})
    locs = tmd_locations(f.body)
    if not locs:
        return None, [], f
    return locs[0].get("location") or {}, tmd_days(locs[0]), f


def fetch_home(token):
    province = cfg("WX_HOME_PROVINCE", "กรุงเทพมหานคร")
    amphoe = cfg("WX_HOME_AMPHOE", "คลองสามวา")
    out = {"label": cfg("WX_HOME_LABEL", "BKK"), "src": "TMD", "days": []}
    if not token:
        return out, "ยังไม่ตั้ง TMD_TOKEN", None
    today = dt.datetime.now(TH_TZ).date().isoformat()
    _loc, days, f = tmd_daily("place", {"province": province, "amphoe": amphoe,
                                        "date": today, "duration": HOME_DAYS}, token)
    if not days:
        return out, f"TMD บ้าน HTTP {f.status} {f.error or 'ไม่มีข้อมูลใน response'}", f
    out["days"] = days
    out["fetched_ts"] = int(time.time())
    return out, None, f


# ---------------- Open-Meteo ----------------
def om_daily(lat, lon):
    """คืน (days, utc_offset_seconds, Fetch)"""
    q = {"latitude": lat, "longitude": lon, "daily": OM_DAILY,
         "timezone": "auto", "forecast_days": OM_DAYS}
    f = http_json(f"{OM_URL}?{urllib.parse.urlencode(q)}")
    b = f.body if isinstance(f.body, dict) else {}
    d = b.get("daily") or {}
    days = []
    for i, date in enumerate(d.get("time") or []):
        def col(k, i=i):
            v = d.get(k) or []
            return v[i] if i < len(v) else None
        code = col("weather_code")
        days.append({"date": date, "tmax": _num(col("temperature_2m_max")),
                     "tmin": _num(col("temperature_2m_min")),
                     "rain_mm": _num(col("precipitation_sum")),
                     "rain_pct": _num(col("precipitation_probability_max")),
                     "code": code, "kind": om_kind(code)})
    return days, b.get("utc_offset_seconds"), f


# ---------------- ปลายทาง ----------------
def load_airports(path=AIRPORTS_F):
    ap = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ap[r["iata"]] = (float(r["lat"]), float(r["lon"]), r["country"])
    return ap


_ROUTE_RE = re.compile(r"^([A-Z]{3})-([A-Z]{3})$")


def dest_from_agenda(agenda):
    """next.json → (IATA ปลายทาง, เวลาถึง epoch) หรือ (None, None).
    1 event = 1 ขา (route "BKK-TPE") → ปลายทาง = รหัสตัวที่ 2."""
    m = _ROUTE_RE.match((agenda or {}).get("route") or "")
    if not m:
        return None, None
    return m.group(2), agenda.get("end_ts") or agenda.get("start_ts")


def fetch_dest(iata, arr_ts, airports, token):
    arr_ts = int(arr_ts or time.time())
    out = {"label": iata, "iata": iata, "arr_ts": arr_ts, "days": []}
    if iata not in airports:
        return out, f"ไม่รู้จักสนามบิน {iata} (ไม่มีใน airports.csv)", None
    lat, lon, country = airports[iata]

    if country == "TH" and token:
        date = dt.datetime.fromtimestamp(arr_ts, TH_TZ).date().isoformat()
        loc, days, f = tmd_daily("at", {"lat": lat, "lon": lon, "date": date, "duration": 1}, token)
        glat, glon = _num((loc or {}).get("lat")), _num((loc or {}).get("lon"))
        snap_ok = glat is None or glon is None or _km(lat, lon, glat, glon) <= MAX_SNAP_KM
        if days and snap_ok:
            out.update(src="TMD", date=date, days=days, fetched_ts=int(time.time()))
            return out, None, f
        # TMD ใช้ไม่ได้ (ล่ม / HTTP 422 = วันที่เกินช่วง ~10 วันที่ TMD มี / grid ไกลผิดปกติ)
        # → ตกไป Open-Meteo ข้างล่าง: พิกัดมีอยู่แล้ว และ Open-Meteo พยากรณ์ได้ไกลกว่า (16 วัน)

    days, off, f = om_daily(lat, lon)
    if not days:
        return out, f"Open-Meteo {iata} HTTP {f.status} {f.error or 'ไม่มีข้อมูล'}", f
    date = dt.datetime.fromtimestamp(arr_ts + (off or 0), dt.timezone.utc).date().isoformat()
    hit = [d for d in days if d["date"] == date]
    out.update(src="OM", date=date, days=hit, fetched_ts=int(time.time()))
    if not hit:
        out["note"] = "beyond"     # บินไกลกว่าที่พยากรณ์ได้ — โชว์ "--" ไม่เดา
    return out, None, f


# ---------------- I/O ----------------
def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def keep_or(new, old, err):
    """ดึงใหม่ไม่ได้ → ใช้ของเดิม (ถ้ายังสดพอ และเป็นที่เดิม + เที่ยวบินเดิม) ดีกว่าจอว่าง.
    เทียบ arr_ts ด้วย: บินไปเมืองเดิมคนละวัน ห้ามโชว์พยากรณ์ของเที่ยวก่อน"""
    if not err:
        return new
    if (old and old.get("days") and old.get("label") == new.get("label")
            and old.get("arr_ts") == new.get("arr_ts")
            and time.time() - old.get("fetched_ts", 0) < KEEP_SEC):
        return old
    return new


def write_out(payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = OUT_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, OUT_F)
    try:
        os.chmod(OUT_F, 0o644)
    except OSError:
        pass


def main():
    token = cfg("TMD_TOKEN")
    old = read_json(OUT_F)
    errors = {}

    home, err, _ = fetch_home(token)
    if err:
        errors["home"] = err
    home = keep_or(home, old.get("home"), err)

    dest = None
    iata, arr_ts = dest_from_agenda(read_json(AGENDA_F))
    if iata:
        dest, err, _ = fetch_dest(iata, arr_ts, load_airports(), token)
        if err:
            errors["dest"] = err
        dest = keep_or(dest, old.get("dest"), err)

    write_out({"ts": int(time.time()), "home": home, "dest": dest, "errors": errors})
    for k, v in errors.items():
        print(f"weather_fetch: {k}: {v}")
    print(f"weather_fetch: home={len(home['days'])}d dest={iata or '-'}"
          f"{'' if not dest else ' ' + str(dest.get('src')) + ' ' + str(dest.get('date'))}")
    if "home" in errors and not home.get("days"):
        sys.exit(1)        # ให้ systemd บันทึกว่า fail — จะได้เห็นใน `systemctl --failed`


# ---------------- --probe (GitHub Actions / ทดสอบมือ) ----------------
def _probe_report(name, f, parsed):
    print(f"\n=== {name}")
    if f is None:
        print("  (ไม่ได้เรียก API)")
    else:
        keep = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Datapoint-Limit", "X-Datapoint-Remaining")
        print(f"  HTTP {f.status}  " + "  ".join(f"{k}={f.headers.get(k)}" for k in keep if f.headers.get(k)))
        if f.error:
            print(f"  error: {f.error}")
        if isinstance(f.body, dict):
            print(f"  top-level keys: {sorted(f.body)}")
            print("  raw (ตัด 1200 ตัวอักษร): " + json.dumps(f.body, ensure_ascii=False)[:1200])
    print("  parsed: " + json.dumps(parsed, ensure_ascii=False))


def probe(codes):
    token = cfg("TMD_TOKEN")
    print(f"TMD_TOKEN: {'set' if token else 'NOT SET'}")       # บอกแค่มี/ไม่มี — ห้ามพิมพ์ค่า
    home, err, f = fetch_home(token)
    _probe_report("home (TMD place)", f, {"error": err, **home})
    airports = load_airports()
    tomorrow = time.time() + 86400
    for code in codes:
        dest, err, f = fetch_dest(code.upper(), tomorrow, airports, token)
        _probe_report(f"dest {code.upper()}", f, {"error": err, **dest})
    if not home.get("days"):
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        probe(sys.argv[2:] or ["CNX", "TPE"])
    else:
        main()
