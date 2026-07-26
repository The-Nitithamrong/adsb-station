#!/usr/bin/env python3
"""track_stats.py — สรุปสถิติจากตาราง tracks (flight_watcher บันทึกตอนเครื่องหายไป)

ตอบ 2 คำถาม:
  1) ใกล้ VTBS สุด รับสัญญาณได้ที่ความสูงเท่าไหร่ (coverage floor)
  2) เวลาจริง (alert → สัญญาณหลุด) เทียบ ETA เส้นตรงที่คำนวณ — ไว้ calibrate
     (straight-line ETA เชื่อไม่ได้: STAR ไม่บินตรงเข้า + gs ลดตอน descend)

รัน:  python3 track_stats.py           # ทุกเที่ยว
      python3 track_stats.py THA       # เฉพาะ callsign ขึ้นต้น THA
stdlib ล้วน (sqlite3) — ไม่มี pip dep.
"""
import sqlite3, sys, statistics

DB = "/home/arin/flightwatch.db"
COLS = ["hex", "flight", "watched", "first_ts", "last_ts", "samples",
        "min_dist", "alt_at_min", "min_alt", "last_dist", "last_alt",
        "max_dist", "alert_ts", "alert_eta", "star_fix", "star_alt", "star_ts"]


def main():
    pref = sys.argv[1].upper() if len(sys.argv) > 1 else None
    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT hex, flight, watched, first_ts, last_ts, samples, "
            "min_dist_nm, alt_at_min, min_alt, last_dist_nm, last_alt, "
            "max_dist_nm, alert_ts, alert_eta, star_fix, star_alt, star_ts "
            "FROM tracks").fetchall()
    except sqlite3.OperationalError:
        print("ยังไม่มีตาราง tracks — flight_watcher เวอร์ชันใหม่ยังไม่ได้รัน/บันทึก")
        return
    data = [dict(zip(COLS, r, strict=True)) for r in rows]
    if pref:
        data = [d for d in data if (d["flight"] or "").startswith(pref)]
    if not data:
        print("ยังไม่มีข้อมูล tracks (รอ flight_watcher บันทึกหลังมีเครื่องผ่านเข้าใกล้สนามแล้วหายไป ~5 นาที)")
        return

    print(f"tracks: {len(data)} เที่ยว" + (f"  (filter {pref})" if pref else ""))

    # 1) COVERAGE FLOOR — แบ่งตามแถบระยะใกล้สนาม
    havealt = [d for d in data if d["alt_at_min"] is not None]
    print("\n=== Coverage floor: เข้าใกล้ VTBS แค่ไหน รับได้ต่ำสุดเท่าไหร่ (alt ที่จุดใกล้สุด) ===")
    print(f"{'แถบระยะ':>10} | {'n':>4} | {'ต่ำสุด':>9} | {'median':>9}")
    for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 40), (40, 60)]:
        band = [d for d in havealt if lo <= d["min_dist"] < hi]
        if not band:
            continue
        alts = sorted(d["alt_at_min"] for d in band)
        print(f"{lo:>3}-{hi:<3}nm | {len(band):>4} | {min(alts):>6} ft | {int(statistics.median(alts)):>6} ft")

    if havealt:
        closest = min(havealt, key=lambda d: d["min_dist"])
        print(f"\nเข้าใกล้สุด : {closest['flight'] or closest['hex']} — {closest['min_dist']} nm @ {closest['alt_at_min']} ft"
              f"  (สัญญาณหลุดที่ {closest['last_dist']} nm / {closest['last_alt']} ft)")
        low = min(havealt, key=lambda d: d["alt_at_min"])
        print(f"alt ต่ำสุดที่เคยรับใกล้สนาม: {low['alt_at_min']} ft @ {low['min_dist']} nm ({low['flight'] or low['hex']})")

    # 2) STAR gate — เวลาจริงจากจุดเข้า STAR ถึงสัญญาณหลุด (anchor ต่อ gate)
    gated = [d for d in data if d["star_ts"] and d["last_ts"] and d["last_ts"] > d["star_ts"]]
    print("\n=== STAR gate → สัญญาณหลุด: เวลาจริง (นาที) + alt ตอนผ่าน gate ===")
    if gated:
        print(f"{'gate':>6} | {'n':>3} | {'เวลา median':>11} | {'ช่วง':>9} | {'alt@gate med':>12}")
        by_gate = {}
        for d in gated:
            by_gate.setdefault(d["star_fix"], []).append(d)
        for gate, ds in sorted(by_gate.items(), key=lambda kv: -len(kv[1])):
            mins = sorted((d["last_ts"] - d["star_ts"]) / 60.0 for d in ds)
            alts = [d["star_alt"] for d in ds if d["star_alt"] is not None]
            amed = f"{int(statistics.median(alts))} ft" if alts else "-"
            print(f"{gate:>6} | {len(ds):>3} | {statistics.median(mins):>9.1f}m | "
                  f"{mins[0]:>3.0f}-{mins[-1]:<3.0f}m | {amed:>12}")
        print("หมายเหตุ: เวลานี้ยังไม่รวม final หลังสัญญาณหลุด — เอาไว้เทียบ/จูน ETA_DESCENT_FPM ต่อ gate")
    else:
        print("(ยังไม่มีเที่ยวที่จับจุดเข้า STAR ได้ — รอเครื่องผ่านใกล้ WILLA/NORTA/EASTE/TUMGA/LEBIM)")

    # 3) ETA จริง vs คำนวณ (เฉพาะเที่ยวที่ alert แล้ว)
    alerted = [d for d in data if d["alert_ts"] and d["alert_eta"] and d["last_ts"]]
    print("\n=== เวลาจริง (alert → สัญญาณหลุด) vs ETA เส้นตรง ===")
    if not alerted:
        print("(ยังไม่มีเที่ยวที่ alert แล้วถูกบันทึก)")
        return
    print(f"{'flight':>8} | {'ETAคำนวณ':>8} | {'จริง≥':>6} | {'หลุดที่':>16}")
    diffs = []
    for d in sorted(alerted, key=lambda x: x["alert_ts"])[-20:]:
        actual = (d["last_ts"] - d["alert_ts"]) / 60.0
        diffs.append(actual - d["alert_eta"])
        print(f"{(d['flight'] or d['hex']):>8} | {d['alert_eta']:>6.0f}m | {actual:>4.0f}m | "
              f"{(d['last_dist'] or 0):>4.0f}nm/{(d['last_alt'] or 0):>5}ft")
    print("\nหมายเหตุ: 'จริง' = เวลาถึงตอนสัญญาณหลุด (ยังไม่ถึงพื้น — บวก final อีกนิด)")
    print(f"เฉลี่ย (จริง − คำนวณ) = {statistics.mean(diffs):+.1f} นาที  (บวก = บินนานกว่าที่คำนวณ)")


if __name__ == "__main__":
    main()
