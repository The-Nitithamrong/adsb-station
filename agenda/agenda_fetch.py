#!/usr/bin/env python3
"""agenda_fetch.py — ดึง "งานถัดไป" (เที่ยวบินถัดไป) จาก Google Calendar มาโชว์บน Pixoo.

ทำไมใช้ ICS ไม่ใช่ API: Pixoo/Pi อ่านได้แค่ไฟล์ local — ต่อ Google Calendar (OAuth) ตรงๆ ไม่ได้.
วิธีที่เบาสุด + stdlib ล้วน + ไม่ต้อง OAuth = ดึง "secret address in iCal format" (private ICS URL)
ของปฏิทินผ่าน HTTPS แล้ว parse เอาอีเวนต์ถัดไป เขียนลง /run/agenda/next.json ให้ pixoo/main.py อ่าน.
(มิเรอร์วิธีที่ flight_watcher.py เขียน inbound.json — สัญญา JSON ใน /run, world-readable, ไม่ลับ.)

รันโดย systemd timer (User=arin) ทุก ~15 นาที. secret URL อยู่ใน /etc/fr24-watchdog.env:
  GCAL_ICS_URL="https://calendar.google.com/calendar/ical/<...>/basic.ics"
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ENV_FILE = "/etc/fr24-watchdog.env"
OUT_DIR = "/run/agenda"
OUT_F = os.path.join(OUT_DIR, "next.json")
HORIZON_DAYS = 14           # มองไปข้างหน้าไกลสุดกี่วัน (กันไปหยิบอีเวนต์ปีหน้า)
TIMEOUT = 20

try:
    from zoneinfo import ZoneInfo
except ImportError:         # Python < 3.9 (ไม่น่าเจอบน Pi) — fallback เป็น local time
    ZoneInfo = None

# code เที่ยวบิน เช่น TG632 / SQ711 · route เช่น BKK->TPE / BKK-TPE / BKK/TPE
_CODE_RE = re.compile(r"\b([A-Z]{2}[A-Z]?\d{2,4})\b")
_ROUTE_RE = re.compile(r"\b([A-Z]{3})\s*(?:->|-->|→|-|/|to)\s*([A-Z]{3})\b", re.I)


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


def unfold(text):
    # RFC 5545: บรรทัดที่ขึ้นต้นด้วย space/tab = ต่อจากบรรทัดก่อน
    out = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def parse_dt(val, params):
    """แปลงค่า DTSTART/DTEND เป็น (datetime aware, all_day:bool). None ถ้า parse ไม่ได้."""
    tzid = None
    for p in params:
        if p.upper().startswith("TZID="):
            tzid = p[5:]
    val = val.strip()
    # date-only (all-day): VALUE=DATE หรือความยาว 8
    if any(p.upper() == "VALUE=DATE" for p in params) or (len(val) == 8 and "T" not in val):
        try:
            d = dt.datetime.strptime(val[:8], "%Y%m%d")
            return d.replace(tzinfo=_local_tz()), True
        except ValueError:
            return None, False
    try:
        if val.endswith("Z"):
            d = dt.datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        else:
            d = dt.datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
            d = d.replace(tzinfo=_tz(tzid))
        return d, False
    except ValueError:
        return None, False


def _tz(tzid):
    if tzid and ZoneInfo:
        try:
            return ZoneInfo(tzid)
        except Exception:
            pass
    return _local_tz()


def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def clean(s):
    # de-escape ICS + ตัดอักขระที่ Pixoo วาดไม่ได้ (emoji ฯลฯ) ออก เหลือ ASCII printable
    s = s.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")
    s = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def parse_events(lines):
    events, cur = [], None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            name, val = ln.split(":", 1)
            parts = name.split(";")
            key, params = parts[0].upper(), parts[1:]
            if key == "SUMMARY":
                cur["summary"] = clean(val)
                cur["summary_raw"] = val    # เก็บดิบไว้ดึง route (ลูกศร →/-> ยังอยู่ก่อนตัด non-ASCII)
            elif key == "DTSTART":
                cur["start"], cur["all_day"] = parse_dt(val, params)
            elif key == "DTEND":
                cur["end"], _ = parse_dt(val, params)
            elif key == "RRULE":
                cur["rrule"] = True            # ข้ามอีเวนต์ซ้ำ (roster เป็น one-off)
    return events


def pick_next(events, now):
    horizon = now + dt.timedelta(days=HORIZON_DAYS)
    best = None
    for e in events:
        st = e.get("start")
        if not st or e.get("rrule"):
            continue
        end = e.get("end") or st
        # เอาอีเวนต์ที่ยังไม่จบ (กำลังเกิด หรือจะเกิด) ภายใน horizon — ตัวที่เริ่มเร็วสุด
        if end >= now and st <= horizon:
            if best is None or st < best["start"]:
                best = e
    return best


def build_payload(ev, now):
    ts = int(now.timestamp())
    if not ev:
        return {"ts": ts, "summary": None}
    summ = ev.get("summary", "")
    code = None
    m = _CODE_RE.search(summ.upper())
    if m:
        code = m.group(1)
    route = None
    r = _ROUTE_RE.search(ev.get("summary_raw", summ))   # ดิบก่อน (ลูกศร → ยังไม่ถูกตัด)
    if r:
        route = f"{r.group(1).upper()}-{r.group(2).upper()}"
    st = ev["start"]
    return {
        "ts": ts,
        "summary": summ,
        "code": code,
        "route": route,
        "start_ts": int(st.timestamp()),
        "start_str": st.astimezone(_local_tz()).strftime("%d/%m %H:%M"),
        "in_min": int((st.timestamp() - now.timestamp()) / 60),
        "all_day": bool(ev.get("all_day")),
    }


def write_out(payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = OUT_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, OUT_F)
    try:
        os.chmod(OUT_F, 0o644)
    except OSError:
        pass


def main():
    env = load_env(ENV_FILE)
    url = env.get("GCAL_ICS_URL")
    if not url:
        print(f"agenda_fetch: ยังไม่ตั้ง GCAL_ICS_URL ใน {ENV_FILE} — ข้าม")
        return
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"agenda_fetch: ดึง ICS ไม่สำเร็จ (network/URL?) — {e}")
        sys.exit(1)
    now = dt.datetime.now(dt.timezone.utc)
    ev = pick_next(parse_events(unfold(raw)), now)
    payload = build_payload(ev, now)
    write_out(payload)
    if ev:
        print(f"agenda_fetch: next = {payload.get('code') or payload['summary']} @ {payload['start_str']} ({payload['in_min']}m)")
    else:
        print("agenda_fetch: ไม่มีอีเวนต์ใน horizon")


if __name__ == "__main__":
    main()
