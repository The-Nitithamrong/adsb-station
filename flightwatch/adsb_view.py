#!/usr/bin/env python3
"""adsb_view.py — ตารางสดของเครื่องบินที่ dongle รับได้ตอนนี้
อ่านจาก SBS/BaseStation stream (dump1090 port 30003) ไม่ต้องลง dependency
รัน: python3 adsb_view.py   (Ctrl+C เพื่อออก)
"""
import socket, time, sys

HOST, PORT = "127.0.0.1", 30003
STALE = 60          # ลบเครื่องที่หายเกินกี่วินาที
FIELDS = {"flight": 10, "alt": 11, "gs": 12, "trk": 13,
          "lat": 14, "lon": 15, "sq": 17}   # index ใน SBS CSV (0-based)

planes = {}  # hex -> {field: value, ts: ...}


def parse(line):
    f = line.split(",")
    if len(f) < 22 or f[0] != "MSG":
        return
    hexid = f[4].strip()
    if not hexid:
        return
    p = planes.setdefault(hexid, {k: "" for k in FIELDS})
    for k, i in FIELDS.items():
        if f[i].strip():
            p[k] = f[i].strip()
    p["ts"] = time.time()


def render():
    now = time.time()
    for h in [h for h, p in planes.items() if now - p.get("ts", 0) > STALE]:
        del planes[h]
    rows = sorted(planes.items(), key=lambda kv: kv[1].get("flight") or "zzz")
    sys.stdout.write("\033[2J\033[H")   # clear screen
    print(f"  \u2708  {len(planes)} ลำในระยะ  |  {time.strftime('%H:%M:%S')}\n")
    print(f"{'HEX':7} {'FLIGHT':8} {'ALT':>6} {'GS':>4} {'TRK':>4} "
          f"{'LAT':>9} {'LON':>9} {'SQ':>5}  age")
    print("-" * 68)
    for h, p in rows:
        age = int(now - p.get("ts", 0))
        print(f"{h:7} {p['flight']:8} {p['alt']:>6} {p['gs']:>4} {p['trk']:>4} "
              f"{p['lat']:>9} {p['lon']:>9} {p['sq']:>5}  {age}s")


def main():
    s = socket.create_connection((HOST, PORT))
    s.settimeout(1)
    buf, last = "", 0.0
    while True:
        try:
            data = s.recv(4096).decode(errors="ignore")
            if not data:
                time.sleep(0.5)
            buf += data
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                parse(line.strip())
        except socket.timeout:
            pass
        if time.time() - last >= 1:
            render()
            last = time.time()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nออกแล้ว")
