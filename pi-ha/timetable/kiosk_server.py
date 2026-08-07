#!/usr/bin/env python3
"""kiosk_server.py — เสิร์ฟหน้า "ตารางเรียน" ให้ Chromium kiosk บน Pi#2 (จอนักเรียน).

อ่าน schedule.json (โฟลเดอร์เดียวกัน) → render HTML ตารางสัปดาห์ (วัน × คาบ) สวยๆ: emoji + สีต่อวิชา,
นาฬิกาเดินสด, ไฮไลต์ "วันนี้ + คาบตอนนี้" (คำนวณฝั่ง JS จากเวลาเครื่อง), รีเฟรชเองทุกนาที (แก้ JSON แล้ว
เห็นเลยตอน reload). stdlib http.server ล้วน — ไม่มี pip, ไม่ต้อง login, ไม่พึ่ง cloud.

แก้ตาราง: แก้ schedule.json (subjects / periods / grid) แล้ว refresh. รันเป็น daemon (systemd, User=arin).
Chromium kiosk ชี้มา http://localhost:PORT (ดู README). PORT ปรับผ่าน env TIMETABLE_PORT (default 8080).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_F = os.path.join(HERE, "schedule.json")
PORT = int(os.environ.get("TIMETABLE_PORT", "8080"))

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f172a;--surface:#1e293b;--line:#334155;--ink:#f1f5f9;--muted:#94a3b8}
html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:'Noto Sans Thai','Sarabun',sans-serif;
  padding:2vmin;display:flex;flex-direction:column;gap:1.5vmin;overflow:hidden}
header{display:flex;align-items:baseline;justify-content:space-between;gap:2vmin}
header .title{font-size:4.2vmin;font-weight:800}
header .who{color:var(--muted);font-size:2.4vmin;font-weight:600}
header .clock{font-size:4.6vmin;font-weight:800;font-variant-numeric:tabular-nums}
header .date{color:var(--muted);font-size:2.2vmin;text-align:right}
table{width:100%;height:100%;border-collapse:separate;border-spacing:0.7vmin;table-layout:fixed}
th,td{border-radius:1.4vmin;padding:1vmin;text-align:center;vertical-align:middle}
thead th{background:var(--surface);color:var(--muted);font-size:2.6vmin;font-weight:800;padding:1.4vmin}
thead th.today{background:#2563eb;color:#fff}
.pcol{background:var(--surface);width:13%;font-size:2vmin;color:var(--muted);font-weight:700;line-height:1.35}
.pcol .lbl{color:var(--ink);font-size:2.3vmin}
.pcol .tm{font-variant-numeric:tabular-nums}
td.cell{font-weight:800;position:relative;border:0.35vmin solid transparent}
td.cell .emj{font-size:4.4vmin;line-height:1;display:block}
td.cell .nm{font-size:2.4vmin;margin-top:0.6vmin;display:block}
tr.brk td{font-size:2.4vmin;color:var(--muted);font-weight:700}
tr.brk .pcol{background:#0b1220}
td.now{border-color:#fbbf24 !important;box-shadow:0 0 0 0.35vmin #fbbf24, 0 0 3vmin rgba(251,191,36,.5)}
th.today, td.todaycol{outline:0.25vmin solid rgba(37,99,235,.5)}
"""

JS = """
const PERIODS = __PERIODS__;   // [{start,end,break}] minutes
const NDAYS = __NDAYS__;       // days count (Mon-based)
function mins(hhmm){const [h,m]=hhmm.split(':').map(Number);return h*60+m;}
function tick(){
  const now=new Date();
  const hh=String(now.getHours()).padStart(2,'0'), mm=String(now.getMinutes()).padStart(2,'0'),
        ss=String(now.getSeconds()).padStart(2,'0');
  document.getElementById('clock').textContent=hh+':'+mm+':'+ss;
  const thDays=['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์'];
  const thMon=['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
  document.getElementById('date').textContent=
    'วัน'+thDays[now.getDay()]+' '+now.getDate()+' '+thMon[now.getMonth()]+' '+(now.getFullYear()+543);
  // today column index (Mon=0..): getDay() 1=Mon..5=Fri -> 0..4 ; weekend -> -1
  const col = (now.getDay()>=1 && now.getDay()<=NDAYS) ? now.getDay()-1 : -1;
  const cur = now.getHours()*60+now.getMinutes();
  let prow = -1;
  PERIODS.forEach((p,i)=>{ if(cur>=mins(p.start) && cur<mins(p.end)) prow=i; });
  document.querySelectorAll('.now,.today,.todaycol').forEach(e=>e.classList.remove('now','today','todaycol'));
  if(col>=0){
    const th=document.querySelector('thead th[data-col="'+col+'"]'); if(th) th.classList.add('today');
    document.querySelectorAll('td[data-col="'+col+'"]').forEach(e=>e.classList.add('todaycol'));
    if(prow>=0){ const c=document.querySelector('td[data-row="'+prow+'"][data-col="'+col+'"]'); if(c) c.classList.add('now'); }
  }
}
tick(); setInterval(tick,1000);
setTimeout(()=>location.reload(), 60000);   // reload ทุก 1 นาที (รับ schedule.json ที่แก้ + กัน drift)
"""


def render(sch):
    days = sch.get("days", [])
    periods = sch.get("periods", [])
    subjects = sch.get("subjects", {})
    grid = sch.get("grid", [])
    title = sch.get("title", "ตารางเรียน")
    who = sch.get("student", "")

    head = "".join(
        f'<th data-col="{c}">{d}</th>' for c, d in enumerate(days))
    rows = []
    for r, per in enumerate(periods):
        brk = per.get("break")
        pcell = (f'<td class="pcol"><div class="lbl">{per.get("label","")}</div>'
                 f'<div class="tm">{per.get("start","")}–{per.get("end","")}</div></td>')
        cells = []
        row_keys = grid[r] if r < len(grid) else []
        for c in range(len(days)):
            key = row_keys[c] if c < len(row_keys) else None
            s = subjects.get(key or "", {})
            color = s.get("color", "#334155")
            emj = s.get("emoji", "")
            nm = s.get("name", "")
            bg = f"background:{color}22;" if key else "background:#1e293b;"
            cells.append(f'<td class="cell" data-row="{r}" data-col="{c}" '
                         f'style="{bg}color:{color}">'
                         f'<span class="emj">{emj}</span><span class="nm" style="color:var(--ink)">{nm}</span></td>')
        rows.append(f'<tr class="{"brk" if brk else ""}">{pcell}{"".join(cells)}</tr>')

    per_js = json.dumps([{"start": p.get("start", "00:00"), "end": p.get("end", "00:00")}
                         for p in periods], ensure_ascii=False)
    js = JS.replace("__PERIODS__", per_js).replace("__NDAYS__", str(len(days)))
    whohtml = f'<span class="who">{who}</span>' if who else ""
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<header>
  <div><span class="title">{title}</span> {whohtml}</div>
  <div style="text-align:right"><div class="clock" id="clock">--:--:--</div><div class="date" id="date"></div></div>
</header>
<table><thead><tr><th class="pcol"></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<script>{js}</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            with open(SCHEDULE_F, encoding="utf-8") as f:
                sch = json.load(f)
            body = render(sch).encode("utf-8")
        except (OSError, ValueError) as e:
            body = f"<h1>schedule.json ผิดพลาด</h1><pre>{e}</pre>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass   # เงียบ (ไม่รก journal)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"timetable kiosk: http://127.0.0.1:{PORT}  (schedule={SCHEDULE_F})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
