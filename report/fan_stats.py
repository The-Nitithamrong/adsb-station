#!/usr/bin/env python3
"""fan_stats.py — สรุปความถี่ + เวลารวมการเปิดพัดลมต่อวัน จาก fan_events.jsonl.

log เขียนโดย mqtt_publish (ทุกครั้งที่ switch on↔off, ความละเอียด ~1 นาทีตาม timer).
รัน:  python3 fan_stats.py [วันย้อนหลัง=7]      · stdlib ล้วน.
`last24h_stats()` ถูก import โดย daily_status.py ใส่บรรทัดพัดลมในข้อความ 09:00 (ช่วง 24 ชม.ล่าสุด).
"""
import datetime
import json
import sys
import time

LOG = "/home/arin/fan_events.jsonl"


def read_events(path=None):
    path = path or LOG          # อ่าน global ตอนเรียก (ไม่ผูกค่าตอน def)
    evs = []
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                    evs.append((int(d["ts"]), bool(d["on"])))
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    evs.sort()
    return evs


def intervals(evs, now):
    """ช่วงที่พัดลมเปิด [(start,end), ...] — จับคู่ on→off, ถ้ายังเปิดอยู่ปิดที่ now."""
    out, on_since = [], None
    for ts, on in evs:
        if on and on_since is None:
            on_since = ts
        elif not on and on_since is not None:
            out.append((on_since, ts))
            on_since = None
    if on_since is not None:
        out.append((on_since, now))
    return out


def _day_start(ts):
    d = datetime.datetime.fromtimestamp(ts).date()
    return datetime.datetime(d.year, d.month, d.day).timestamp()   # local midnight


def summary(evs, day_start, day_end, now):
    """(จำนวนช่วงที่เปิด, นาทีรวมที่เปิด) ในช่วง [day_start, day_end).
    นับ "ครั้ง" = จำนวน interval ที่คาบเกี่ยววันนี้ (เปิดค้างข้ามคืน = 1 ครั้ง ไม่ใช่ 0 —
    เดิมนับ on-event ที่ ts เป็นวันนี้ → พลาดช่วงที่เปิดมาตั้งแต่ก่อนเที่ยงคืน)."""
    end = min(day_end, now)
    on_count = 0
    on_secs = 0
    for s, e in intervals(evs, now):
        lo, hi = max(s, day_start), min(e, end)
        if hi > lo:
            on_count += 1
            on_secs += hi - lo
    return on_count, int(on_secs // 60)


def last24h_stats(now=None):
    """พัดลมในช่วง 24 ชม.ล่าสุด (now-24ชม. → now). digest 09:00 = 09:00 เมื่อวาน → 09:00 วันนี้
    (เห็นการใช้ทั้งวัน+คืนที่ผ่านมา แทน 'ตั้งแต่เที่ยงคืน' ที่ 09:00 ครอบแค่ 9 ชม.)."""
    now = int(now or time.time())
    return summary(read_events(), now - 86400, now, now)


def main():
    now = int(time.time())
    evs = read_events()
    if not evs:
        print("ยังไม่มี log (fan_events.jsonl ว่าง — รอ mqtt_publish บันทึก on/off ครั้งแรก)")
        return
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(f"fan events: {len(evs)} รายการ  (ย้อนหลัง {days} วัน)")
    print(f"{'วันที่':>12} | {'เปิด(ครั้ง)':>10} | {'รวมเวลา':>9}")
    today0 = _day_start(now)
    for i in range(days):
        ds = today0 - i * 86400
        c, m = summary(evs, ds, ds + 86400, now)
        if c == 0 and m == 0:
            continue
        h, mm = divmod(m, 60)
        dur = f"{h}ชม {mm}น" if h else f"{mm}น"
        print(f"{datetime.date.fromtimestamp(ds).isoformat():>12} | {c:>10} | {dur:>9}")


if __name__ == "__main__":
    main()
