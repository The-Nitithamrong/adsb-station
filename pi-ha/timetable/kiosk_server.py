#!/usr/bin/env python3
"""kiosk_server.py — จอ "ตารางเรียน" บน Pi#2: week view 7 วัน × แกนเวลา 08:00–20:00 จาก Google Calendar (ICS).

ดึง ICS (private secret address) → parse VEVENT (+ ขยาย RRULE weekly/daily ของสัปดาห์นี้) → วาง event
เป็นบล็อกตามเวลาจริง (ไม่ใช่คาบตายตัว เพราะเวลาเรียน vary). ไฮไลต์วันนี้ + เส้นเวลาปัจจุบัน + นาฬิกาสด.
สีต่อวิชา = hash ชื่อ event (สม่ำเสมอ). emoji ในชื่อ event โชว์ตามจริง.

stdlib ล้วน (urllib) — ไม่มี pip. ดึง ICS ทุก REFRESH_MIN (cache), render ต่อ request จาก cache
(วัน/สัปดาห์/now อัปเดตเสมอ). เสิร์ฟ 127.0.0.1 เท่านั้น. Chromium kiosk ชี้มา http://localhost:PORT.

config (env): TIMETABLE_ICS_URL (หรือ GCAL_ICS_URL), TIMETABLE_PORT(8080),
              TIMETABLE_DAY_START(8), TIMETABLE_DAY_END(20), TIMETABLE_REFRESH_MIN(15)
"""
import datetime as dt
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICS_URL = os.environ.get("TIMETABLE_ICS_URL") or os.environ.get("GCAL_ICS_URL", "")
PORT = int(os.environ.get("TIMETABLE_PORT", "8080"))
DAY_START = int(os.environ.get("TIMETABLE_DAY_START", "8"))     # ชั่วโมงเริ่มแกน
DAY_END = int(os.environ.get("TIMETABLE_DAY_END", "20"))         # ชั่วโมงจบแกน
REFRESH_MIN = int(os.environ.get("TIMETABLE_REFRESH_MIN", "15"))
TZ_OFFSET_H = 7                                                  # ไทย = UTC+7 (แปลงเวลา Z)

TH_DAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
          "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
PALETTE = ["#3b82f6", "#ef4444", "#14b8a6", "#a855f7", "#f97316", "#22c55e",
           "#eab308", "#ec4899", "#0ea5e9", "#8b5cf6", "#f43f5e", "#10b981"]
WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

_EVENTS = []            # cache ของ VEVENT ที่ parse แล้ว
_LOCK = threading.Lock()


# ---------------- ICS fetch + parse ----------------
def fetch_ics(url):
    req = urllib.request.Request(url, headers={"User-Agent": "adsb-timetable/1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def unfold(text):
    out = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def parse_dt(val, params):
    """คืน (datetime local ไทย, is_all_day). รองรับ DATE / floating / TZID / Z(UTC→+7)."""
    if params.get("VALUE") == "DATE" or (len(val) == 8 and val.isdigit()):
        d = dt.datetime.strptime(val[:8], "%Y%m%d")
        return d, True
    z = val.endswith("Z")
    core = val.rstrip("Z")
    d = dt.datetime.strptime(core[:15], "%Y%m%dT%H%M%S")
    if z:                                   # UTC → เวลาไทย
        d += dt.timedelta(hours=TZ_OFFSET_H)
    return d, False


def parse_events(text):
    events, cur = [], None
    for ln in unfold(text):
        if ln == "BEGIN:VEVENT":
            cur = {"exdate": set()}
        elif ln == "END:VEVENT":
            if cur and cur.get("dtstart"):
                events.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            head, _, val = ln.partition(":")
            name, *plist = head.split(";")
            params = dict(p.split("=", 1) for p in plist if "=" in p)
            name = name.upper()
            if name == "SUMMARY":
                cur["summary"] = val
            elif name == "DTSTART":
                cur["dtstart"], cur["allday"] = parse_dt(val, params)
            elif name == "DTEND":
                cur["dtend"], _ = parse_dt(val, params)
            elif name == "RRULE":
                cur["rrule"] = dict(kv.split("=", 1) for kv in val.split(";") if "=" in kv)
            elif name == "EXDATE":
                for part in val.split(","):
                    try:
                        cur["exdate"].add(parse_dt(part, params)[0].date())
                    except ValueError:
                        pass
    return events


# ---------------- expand ให้เป็น occurrence ของสัปดาห์ ----------------
def occurrences(events, week_start):
    """คืน [{day(0-6), start_min, end_min, summary}] ในสัปดาห์ [week_start .. +6 วัน]."""
    week_days = [week_start + dt.timedelta(days=i) for i in range(7)]
    out = []
    for e in events:
        ds = e["dtstart"]
        de = e.get("dtend") or (ds + dt.timedelta(minutes=50))
        dur = de - ds
        summ = e.get("summary", "(ไม่มีชื่อ)")
        rr = e.get("rrule")
        if not rr:
            if week_start <= ds.date() <= week_days[-1]:
                _emit(out, week_days, ds, ds + dur, summ, e.get("allday"))
            continue
        freq = rr.get("FREQ", "")
        interval = int(rr.get("INTERVAL", "1") or "1")
        until = None
        if rr.get("UNTIL"):
            try:
                until = parse_dt(rr["UNTIL"], {})[0].date()
            except ValueError:
                until = None
        bydays = {WD[x] for x in rr.get("BYDAY", "").split(",") if x in WD} or {ds.weekday()}
        for d in week_days:
            if d < ds.date() or (until and d > until) or d in e["exdate"]:
                continue
            if freq == "WEEKLY":
                if d.weekday() not in bydays:
                    continue
                wk = (_monday(d) - _monday(ds.date())).days // 7
                if wk % interval != 0:
                    continue
            elif freq == "DAILY":
                if (d - ds.date()).days % interval != 0:
                    continue
            else:
                continue
            st = dt.datetime.combine(d, ds.time())
            _emit(out, week_days, st, st + dur, summ, e.get("allday"))
    return out


def _monday(d):
    return d - dt.timedelta(days=d.weekday())


def _emit(out, week_days, start, end, summ, allday):
    di = (start.date() - week_days[0]).days
    if not (0 <= di <= 6):
        return
    if allday:
        out.append({"day": di, "start_min": DAY_START * 60, "end_min": DAY_END * 60,
                    "summary": summ, "allday": True})
    else:
        out.append({"day": di, "start_min": start.hour * 60 + start.minute,
                    "end_min": end.hour * 60 + end.minute, "summary": summ, "allday": False})


# ---------------- render ----------------
def color_for(summ):
    return PALETTE[sum(map(ord, summ)) % len(PALETTE)]


def _lanes(day_occs):
    day_occs.sort(key=lambda o: (o["start_min"], o["end_min"]))
    ends = []
    for o in day_occs:
        for i, en in enumerate(ends):
            if o["start_min"] >= en:
                ends[i] = o["end_min"]
                o["lane"] = i
                break
        else:
            o["lane"] = len(ends)
            ends.append(o["end_min"])
    n = len(ends) or 1
    for o in day_occs:
        o["lanes"] = n
    return day_occs


def render(occs, week_start, now):
    span = (DAY_END - DAY_START) * 60
    hours = list(range(DAY_START, DAY_END + 1))
    today_i = (now.date() - week_start).days if 0 <= (now.date() - week_start).days <= 6 else -1

    # หัวคอลัมน์วัน
    heads = []
    for i in range(7):
        d = week_start + dt.timedelta(days=i)
        cls = "today" if i == today_i else ""
        heads.append(f'<div class="dcol {cls}"><div class="dn">{TH_DAYS[i]}</div>'
                     f'<div class="dd">{d.day} {TH_MON[d.month - 1]}</div></div>')

    # ป้ายชั่วโมงในราง (ซ้าย) + gradient เส้นชั่วโมงพาดทุกคอลัมน์
    nh = DAY_END - DAY_START
    labels = "".join(
        f'<div class="hl" style="top:{(h - DAY_START) / nh * 100:.3f}%">{h:02d}:00</div>'
        for h in hours)
    grad = (f"repeating-linear-gradient(to bottom,rgba(148,163,184,.22) 0 1px,"
            f"transparent 1px calc(100%/{nh}))")

    # บล็อก event ต่อวัน
    daycols = []
    for i in range(7):
        dl = _lanes([o for o in occs if o["day"] == i])
        blocks = []
        for o in dl:
            top = max(0, (o["start_min"] - DAY_START * 60) / span * 100)
            bot = min(100, (o["end_min"] - DAY_START * 60) / span * 100)
            h = max(bot - top, 2.2)
            w = 100 / o["lanes"]
            left = o["lane"] * w
            col = color_for(o["summary"])
            tm = "" if o.get("allday") else (
                f'{o["start_min"] // 60:02d}:{o["start_min"] % 60:02d}–'
                f'{o["end_min"] // 60:02d}:{o["end_min"] % 60:02d}')
            blocks.append(
                f'<div class="ev" style="top:{top:.2f}%;height:{h:.2f}%;'
                f'left:{left:.2f}%;width:{w - 1.5:.2f}%;background:{col}26;border-color:{col}">'
                f'<div class="es" style="color:{col}">{_esc(o["summary"])}</div>'
                f'<div class="et">{tm}</div></div>')
        cls = "today" if i == today_i else ""
        daycols.append(f'<div class="col {cls}" data-day="{i}">{"".join(blocks)}</div>')

    stale = "" if _EVENTS else '<div class="warn">ยังไม่ได้ดึงปฏิทิน — ตั้ง TIMETABLE_ICS_URL แล้ว restart</div>'
    return _PAGE.replace("__HEADS__", "".join(heads)).replace("__LABELS__", labels)\
        .replace("__GRAD__", grad).replace("__COLS__", "".join(daycols))\
        .replace("__DAYSTART__", str(DAY_START)).replace("__DAYEND__", str(DAY_END))\
        .replace("__TODAY__", str(today_i)).replace("__WARN__", stale)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ตารางเรียน</title><style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f172a;--surface:#1e293b;--line:#334155;--ink:#f1f5f9;--muted:#94a3b8}
html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:'Noto Sans Thai','Sarabun',sans-serif;
  padding:1.4vmin;display:flex;flex-direction:column;gap:1vmin;overflow:hidden}
header{display:flex;align-items:baseline;justify-content:space-between}
header .t{font-size:3.6vmin;font-weight:800}
header .clock{font-size:4vmin;font-weight:800;font-variant-numeric:tabular-nums}
header .date{color:var(--muted);font-size:2vmin;text-align:right}
.warn{background:#7f1d1d;color:#fecaca;padding:1vmin;border-radius:1vmin;font-size:2vmin}
.grid{flex:1;display:flex;flex-direction:column;min-height:0}
.head{display:grid;grid-template-columns:7vmin repeat(7,1fr);gap:.5vmin;margin-bottom:.5vmin}
.head .sp{}
.dcol{background:var(--surface);border-radius:1vmin;text-align:center;padding:.7vmin}
.dcol .dn{font-weight:800;font-size:2.1vmin}.dcol .dd{color:var(--muted);font-size:1.5vmin}
.dcol.today{background:#2563eb;color:#fff}.dcol.today .dd{color:#dbeafe}
.body{flex:1;display:grid;grid-template-columns:6.5vmin 1fr;gap:.6vmin;min-height:0}
.axis{position:relative}
.hl{position:absolute;right:.3vmin;transform:translateY(-50%);font-size:1.5vmin;color:var(--muted);
  font-variant-numeric:tabular-nums}
.cols{position:relative;display:grid;grid-template-columns:repeat(7,1fr);gap:.5vmin;
  border-radius:1vmin;overflow:hidden}
.col{position:relative;background:rgba(255,255,255,.025)}
.col.today{background:rgba(37,99,235,.16)}
.ev{position:absolute;border-left:.5vmin solid;border-radius:.7vmin;padding:.5vmin .7vmin;
  overflow:hidden;font-weight:700}
.ev .es{font-size:1.75vmin;line-height:1.15}
.ev .et{font-size:1.4vmin;color:var(--muted);font-variant-numeric:tabular-nums;margin-top:.2vmin}
#nowline{position:absolute;left:0;right:0;height:0;border-top:.3vmin solid #fbbf24;z-index:5;display:none}
#nowline::before{content:'';position:absolute;left:-.6vmin;top:-.6vmin;width:1.2vmin;height:1.2vmin;
  background:#fbbf24;border-radius:50%}
</style></head><body>
<header><div class="t">ตารางเรียน</div>
  <div style="text-align:right"><div class="clock" id="clock">--:--:--</div><div class="date" id="date"></div></div>
</header>
__WARN__
<div class="grid">
  <div class="head"><div class="sp"></div>__HEADS__</div>
  <div class="body">
    <div class="axis">__LABELS__</div>
    <div class="cols" id="cols" style="background:__GRAD__">__COLS__<div id="nowline"></div></div>
  </div>
</div>
<script>
const DS=__DAYSTART__, DE=__DAYEND__, TODAY=__TODAY__;
const thD=['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์'];
const thM=['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
function tick(){
  const n=new Date(), p=x=>String(x).padStart(2,'0');
  clock.textContent=p(n.getHours())+':'+p(n.getMinutes())+':'+p(n.getSeconds());
  date.textContent='วัน'+thD[n.getDay()]+' '+n.getDate()+' '+thM[n.getMonth()]+' '+(n.getFullYear()+543);
  const cur=n.getHours()*60+n.getMinutes(), span=(DE-DS)*60;
  const nl=document.getElementById('nowline');
  if(TODAY>=0 && cur>=DS*60 && cur<=DE*60){
    const cols=document.getElementById('cols'), col=cols.children[TODAY];
    nl.style.display='block';
    nl.style.top=((cur-DS*60)/span*100)+'%';
    nl.style.left=(col.offsetLeft)+'px'; nl.style.width=(col.offsetWidth)+'px'; nl.style.right='auto';
  } else nl.style.display='none';
}
tick(); setInterval(tick,1000);
setTimeout(()=>location.reload(), 60000);
</script></body></html>"""


# ---------------- refresh thread + server ----------------
def refresh_loop():
    global _EVENTS
    import time
    while True:
        if ICS_URL:
            try:
                evs = parse_events(fetch_ics(ICS_URL))
                with _LOCK:
                    _EVENTS = evs
                print(f"timetable: ดึง ICS สำเร็จ — {len(evs)} events")
            except Exception as e:
                print("timetable: ดึง ICS ไม่สำเร็จ:", e)
        else:
            print("timetable: ยังไม่ได้ตั้ง TIMETABLE_ICS_URL/GCAL_ICS_URL")
        time.sleep(REFRESH_MIN * 60)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        now = dt.datetime.now()
        with _LOCK:
            evs = list(_EVENTS)
        occs = occurrences(evs, _monday(now.date()))
        body = render(occs, _monday(now.date()), now).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    threading.Thread(target=refresh_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"timetable kiosk: http://127.0.0.1:{PORT}  (ICS={'set' if ICS_URL else 'MISSING'})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
