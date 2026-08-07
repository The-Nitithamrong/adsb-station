#!/usr/bin/env python3
"""kiosk_server.py — student "Timetable" display on Pi#2: week view, 7 days x 08:00-20:00, from ICS.

Pulls the Google Calendar private ICS -> parses VEVENTs (expands weekly/daily RRULE for the current
week) -> lays events out by real time (class times vary, so no fixed periods). Tap an event -> popup
with its details. English UI, soft pastel colours for kids, live clock + now-line, highlights today.

stdlib only (urllib). Fetches the ICS every REFRESH_MIN (cache); renders per request so day/week/now
stay current. Serves 127.0.0.1 only. Chromium kiosk points at http://localhost:PORT.

config (env): TIMETABLE_ICS_URL (or GCAL_ICS_URL), TIMETABLE_PORT(8080),
              TIMETABLE_DAY_START(8), TIMETABLE_DAY_END(20), TIMETABLE_REFRESH_MIN(15)
"""
import datetime as dt
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICS_URL = os.environ.get("TIMETABLE_ICS_URL") or os.environ.get("GCAL_ICS_URL", "")
PORT = int(os.environ.get("TIMETABLE_PORT", "8080"))
DAY_START = int(os.environ.get("TIMETABLE_DAY_START", "8"))
DAY_END = int(os.environ.get("TIMETABLE_DAY_END", "20"))
REFRESH_MIN = int(os.environ.get("TIMETABLE_REFRESH_MIN", "15"))
TZ_OFFSET_H = 7                                          # Thailand = UTC+7 (for Z times)

EN_DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EN_DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EN_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# soft pastels — light backgrounds, dark text on top
PALETTE = ["#bfdbfe", "#fecaca", "#bbf7d0", "#ddd6fe", "#fed7aa", "#bae6fd",
           "#fef08a", "#fbcfe8", "#a7f3d0", "#e9d5ff", "#fda4af", "#c7f9cc"]
WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

_EVENTS = []
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


def unescape(v):
    return (v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def parse_dt(val, params):
    """(local Thai datetime, is_all_day). Handles DATE / floating / TZID / Z(UTC->+7)."""
    if params.get("VALUE") == "DATE" or (len(val) == 8 and val.isdigit()):
        return dt.datetime.strptime(val[:8], "%Y%m%d"), True
    z = val.endswith("Z")
    d = dt.datetime.strptime(val.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if z:
        d += dt.timedelta(hours=TZ_OFFSET_H)
    return d, False


def parse_events(text):
    events, cur = [], None
    for ln in unfold(text):
        if ln == "BEGIN:VEVENT":
            cur = {"exdate": set(), "desc": "", "loc": ""}
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
                cur["summary"] = unescape(val)
            elif name == "DESCRIPTION":
                cur["desc"] = unescape(val)
            elif name == "LOCATION":
                cur["loc"] = unescape(val)
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


# ---------------- expand to occurrences for the week ----------------
def occurrences(events, week_start):
    week_days = [week_start + dt.timedelta(days=i) for i in range(7)]
    out = []
    for e in events:
        ds = e["dtstart"]
        de = e.get("dtend") or (ds + dt.timedelta(minutes=50))
        dur = de - ds
        meta = (e.get("summary", "(no title)"), e.get("desc", ""), e.get("loc", ""), e.get("allday"))
        rr = e.get("rrule")
        if not rr:
            if week_start <= ds.date() <= week_days[-1]:
                _emit(out, week_days, ds, ds + dur, meta)
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
                if ((_monday(d) - _monday(ds.date())).days // 7) % interval != 0:
                    continue
            elif freq == "DAILY":
                if (d - ds.date()).days % interval != 0:
                    continue
            else:
                continue
            st = dt.datetime.combine(d, ds.time())
            _emit(out, week_days, st, st + dur, meta)
    return out


def _monday(d):
    return d - dt.timedelta(days=d.weekday())


def _emit(out, week_days, start, end, meta):
    di = (start.date() - week_days[0]).days
    if not (0 <= di <= 6):
        return
    summ, desc, loc, allday = meta
    o = {"day": di, "summary": summ, "desc": desc, "loc": loc, "allday": bool(allday),
         "date": start.date()}
    if allday:
        o["start_min"], o["end_min"] = DAY_START * 60, DAY_END * 60
    else:
        o["start_min"] = start.hour * 60 + start.minute
        o["end_min"] = end.hour * 60 + end.minute
    out.append(o)


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
    for o in day_occs:
        o["lanes"] = len(ends) or 1
    return day_occs


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def render(occs, week_start, now):
    span = (DAY_END - DAY_START) * 60
    nh = DAY_END - DAY_START
    today_i = (now.date() - week_start).days if 0 <= (now.date() - week_start).days <= 6 else -1

    heads = []
    for i in range(7):
        d = week_start + dt.timedelta(days=i)
        cls = "today" if i == today_i else ""
        heads.append(f'<div class="dcol {cls}"><div class="dn">{EN_DAY[i]}</div>'
                     f'<div class="dd">{d.day} {EN_MON[d.month - 1]}</div></div>')

    labels = "".join(
        f'<div class="hl" style="top:{(h - DAY_START) / nh * 100:.3f}%">{h:02d}:00</div>'
        for h in range(DAY_START, DAY_END + 1))
    grad = (f"repeating-linear-gradient(to bottom,rgba(100,116,139,.18) 0 1px,"
            f"transparent 1px calc(100%/{nh}))")

    daycols = []
    for i in range(7):
        dl = _lanes([o for o in occs if o["day"] == i])
        blocks = []
        for o in dl:
            top = max(0, (o["start_min"] - DAY_START * 60) / span * 100)
            bot = min(100, (o["end_min"] - DAY_START * 60) / span * 100)
            h = max(bot - top, 2.4)
            w = 100 / o["lanes"]
            col = color_for(o["summary"])
            tm = "All day" if o["allday"] else f'{_hhmm(o["start_min"])}–{_hhmm(o["end_min"])}'
            when = f'{EN_DAY_FULL[i]} {o["date"].day} {EN_MON[o["date"].month - 1]} {o["date"].year} · {tm}'
            blocks.append(
                f'<div class="ev" tabindex="0" style="top:{top:.2f}%;height:{h:.2f}%;'
                f'left:{o["lane"] * w:.2f}%;width:{w - 1.5:.2f}%;background:{col}" '
                f'data-title="{_esc(o["summary"])}" data-when="{_esc(when)}" '
                f'data-loc="{_esc(o["loc"])}" data-desc="{_esc(o["desc"])}">'
                f'<div class="es">{_esc(o["summary"])}</div><div class="et">{tm}</div></div>')
        cls = "today" if i == today_i else ""
        daycols.append(f'<div class="col {cls}">{"".join(blocks)}</div>')

    warn = "" if _EVENTS else ('<div class="warn">No calendar yet — set TIMETABLE_ICS_URL '
                               'then restart the service.</div>')
    return _PAGE.replace("__HEADS__", "".join(heads)).replace("__LABELS__", labels)\
        .replace("__GRAD__", grad).replace("__COLS__", "".join(daycols))\
        .replace("__DAYSTART__", str(DAY_START)).replace("__DAYEND__", str(DAY_END))\
        .replace("__TODAY__", str(today_i)).replace("__WARN__", warn)


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Timetable</title><style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f4f6fb;--card:#ffffff;--ink:#334155;--ink2:#1f2937;--muted:#8a97a8;--line:#e2e8f0}
html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:'Nunito','Baloo 2','Noto Sans Thai',
  system-ui,sans-serif;padding:1.6vmin;display:flex;flex-direction:column;gap:1vmin;overflow:hidden}
header{display:flex;align-items:baseline;justify-content:space-between}
header .t{font-size:3.6vmin;font-weight:800;color:var(--ink2)}
header .clock{font-size:4vmin;font-weight:800;font-variant-numeric:tabular-nums;color:var(--ink2)}
header .date{color:var(--muted);font-size:2vmin;text-align:right;font-weight:700}
.warn{background:#fee2e2;color:#b91c1c;padding:1.2vmin;border-radius:1.2vmin;font-size:2vmin;font-weight:700}
.grid{flex:1;display:flex;flex-direction:column;min-height:0}
.head{display:grid;grid-template-columns:6.5vmin repeat(7,1fr);gap:.6vmin;margin-bottom:.6vmin}
.dcol{background:var(--card);border-radius:1.4vmin;text-align:center;padding:.8vmin;
  box-shadow:0 1px 3px rgba(15,23,42,.06)}
.dcol .dn{font-weight:800;font-size:2.1vmin;color:var(--ink2)}
.dcol .dd{color:var(--muted);font-size:1.5vmin;font-weight:700}
.dcol.today{background:#93c5fd}.dcol.today .dn,.dcol.today .dd{color:#0b2e63}
.body{flex:1;display:grid;grid-template-columns:6.5vmin 1fr;gap:.6vmin;min-height:0}
.axis{position:relative}
.hl{position:absolute;right:.3vmin;transform:translateY(-50%);font-size:1.5vmin;color:var(--muted);
  font-variant-numeric:tabular-nums;font-weight:700}
.cols{position:relative;display:grid;grid-template-columns:repeat(7,1fr);gap:.6vmin;
  border-radius:1.4vmin;overflow:hidden}
.col{position:relative;background:var(--card);box-shadow:inset 0 0 0 1px var(--line)}
.col.today{background:#eff6ff}
.ev{position:absolute;border-radius:1vmin;padding:.6vmin .8vmin;overflow:hidden;cursor:pointer;
  color:var(--ink2);box-shadow:0 1px 4px rgba(15,23,42,.12);transition:transform .08s}
.ev:hover,.ev:focus{transform:scale(1.02);outline:none;z-index:6}
.ev .es{font-size:1.85vmin;font-weight:800;line-height:1.15}
.ev .et{font-size:1.45vmin;font-weight:700;opacity:.7;font-variant-numeric:tabular-nums;margin-top:.2vmin}
#nowline{position:absolute;left:0;right:0;height:0;border-top:.35vmin solid #fb923c;z-index:5;display:none}
#nowline::before{content:'';position:absolute;left:-.7vmin;top:-.7vmin;width:1.4vmin;height:1.4vmin;
  background:#fb923c;border-radius:50%}
#modal{position:fixed;inset:0;background:rgba(15,23,42,.45);display:none;align-items:center;
  justify-content:center;z-index:20}
#modal .card{background:var(--card);border-radius:2vmin;padding:3vmin;max-width:70vmin;min-width:40vmin;
  box-shadow:0 10px 40px rgba(15,23,42,.3);position:relative}
#modal .x{position:absolute;top:1.4vmin;right:1.8vmin;border:0;background:none;font-size:4vmin;
  line-height:1;color:var(--muted);cursor:pointer;font-weight:800}
#modal .mt{font-size:3.4vmin;font-weight:800;color:var(--ink2);padding-right:4vmin}
#modal .mw{color:#2563eb;font-weight:800;font-size:2.2vmin;margin-top:1vmin}
#modal .ml{color:var(--ink);font-size:2vmin;margin-top:1.4vmin;font-weight:700}
#modal .md{color:var(--ink);font-size:2vmin;margin-top:1.4vmin;white-space:pre-wrap;line-height:1.5}
</style></head><body>
<header><div class="t">Timetable</div>
  <div style="text-align:right"><div class="clock" id="clock">--:--:--</div><div class="date" id="date"></div></div>
</header>
__WARN__
<div class="grid">
  <div class="head"><div></div>__HEADS__</div>
  <div class="body">
    <div class="axis">__LABELS__</div>
    <div class="cols" id="cols" style="background:__GRAD__">__COLS__<div id="nowline"></div></div>
  </div>
</div>
<div id="modal" onclick="if(event.target===this)closeM()">
  <div class="card"><button class="x" onclick="closeM()">&times;</button>
    <div class="mt" id="mTitle"></div><div class="mw" id="mWhen"></div>
    <div class="ml" id="mLoc"></div><div class="md" id="mDesc"></div></div>
</div>
<script>
const DS=__DAYSTART__, DE=__DAYEND__, TODAY=__TODAY__;
const D=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function tick(){
  const n=new Date(), p=x=>String(x).padStart(2,'0');
  clock.textContent=p(n.getHours())+':'+p(n.getMinutes())+':'+p(n.getSeconds());
  date.textContent=D[n.getDay()]+' '+n.getDate()+' '+M[n.getMonth()]+' '+n.getFullYear();
  const cur=n.getHours()*60+n.getMinutes(), span=(DE-DS)*60, nl=document.getElementById('nowline');
  if(TODAY>=0 && cur>=DS*60 && cur<=DE*60){
    const col=document.getElementById('cols').children[TODAY];
    nl.style.display='block'; nl.style.top=((cur-DS*60)/span*100)+'%';
    nl.style.left=col.offsetLeft+'px'; nl.style.width=col.offsetWidth+'px'; nl.style.right='auto';
  } else nl.style.display='none';
}
tick(); setInterval(tick,1000);
function openM(el){
  mTitle.textContent=el.dataset.title; mWhen.textContent=el.dataset.when;
  mLoc.textContent=el.dataset.loc?('\\uD83D\\uDCCD '+el.dataset.loc):''; mLoc.style.display=el.dataset.loc?'block':'none';
  mDesc.textContent=el.dataset.desc||''; mDesc.style.display=el.dataset.desc?'block':'none';
  document.getElementById('modal').style.display='flex';
}
function closeM(){document.getElementById('modal').style.display='none';}
document.getElementById('cols').addEventListener('click',e=>{const ev=e.target.closest('.ev'); if(ev)openM(ev);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeM();});
setTimeout(()=>{ if(document.getElementById('modal').style.display!=='flex') location.reload(); }, 60000);
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
                print(f"timetable: fetched ICS ok — {len(evs)} events")
            except Exception as e:
                print("timetable: ICS fetch failed:", e)
        else:
            print("timetable: TIMETABLE_ICS_URL/GCAL_ICS_URL not set")
        time.sleep(REFRESH_MIN * 60)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        now = dt.datetime.now()
        with _LOCK:
            evs = list(_EVENTS)
        body = render(occurrences(evs, _monday(now.date())), _monday(now.date()), now).encode("utf-8")
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
