#!/usr/bin/env python3
"""track_stats.py — สรุปสถิติจากตาราง tracks (flight_watcher บันทึกตอนเครื่องหายไป)

ตอบ:
  1) coverage floor — ใกล้ VTBS แค่ไหน รับได้ต่ำสุดเท่าไหร่ + จุดสัญญาณหลุดทั่วไป
     (obs จริง: หลุด ~10nm / 2-3000ft ≈ 3 นาทีก่อนแตะพื้น)
  2) STAR gate → touchdown: เวลาจริงต่อ gate (บวก final ที่มองไม่เห็น ~last_alt/900)
  3) arrivals ตามชั่วโมงของวัน (BKK): จำนวนเที่ยว + เวลา gate→พื้น ต่อ ชม. (ดู traffic ตามช่วงเวลา)
  4) ETA ที่คำนวณตอน alert vs เวลาจริงถึงพื้น — ไว้จูน ETA_DESCENT_FPM

รัน:  python3 track_stats.py           # ทุกเที่ยว
      python3 track_stats.py THA       # เฉพาะ callsign ขึ้นต้น THA
stdlib ล้วน (sqlite3) — ไม่มี pip dep.
"""
import sqlite3, sys, statistics, time

DB = "/home/arin/flightwatch.db"
DESCENT_FPM = 900   # ต้องตรงกับ ETA_DESCENT_FPM ใน flight_watcher — ใช้ประมาณ "final ที่มองไม่เห็น"
#   obs จริง: สัญญาณหลุด ~10nm / 2-3000ft ≈ 3 นาทีก่อนแตะพื้น (2500/900≈2.8m ✓) → บวกกลับให้เป็นเวลาถึงพื้น
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

    lost = [d for d in data if d["last_dist"] is not None and d["last_alt"] is not None]
    if lost:
        md = statistics.median(d["last_dist"] for d in lost)
        ma = statistics.median(d["last_alt"] for d in lost)
        print(f"จุดสัญญาณหลุดทั่วไป (median): {md:.0f} nm / {int(ma)} ft"
              f"  → เหลือ ~{ma / DESCENT_FPM:.1f} นาทีถึงพื้น (final ที่มองไม่เห็น)")

    # 2) STAR gate — เวลาจริงจากจุดเข้า STAR ถึงพื้น (= ถึงสัญญาณหลุด + final ~last_alt/750)
    gated = [d for d in data if d["star_ts"] and d["last_ts"] and d["last_ts"] > d["star_ts"]]
    print("\n=== STAR gate → touchdown: เวลาจริง (บวก final ที่มองไม่เห็น) + alt ตอนผ่าน gate ===")
    if gated:
        print(f"{'gate':>6} | {'n':>3} | {'→หลุด':>7} | {'→พื้น≈':>7} | {'ช่วง→พื้น':>11} | {'alt@gate':>9}")
        by_gate = {}
        for d in gated:
            by_gate.setdefault(d["star_fix"], []).append(d)
        for gate, ds in sorted(by_gate.items(), key=lambda kv: -len(kv[1])):
            loss = [(d["last_ts"] - d["star_ts"]) / 60.0 for d in ds]
            td = sorted(m + (d["last_alt"] or 0) / DESCENT_FPM for m, d in zip(loss, ds, strict=True))
            alts = [d["star_alt"] for d in ds if d["star_alt"] is not None]
            amed = f"{int(statistics.median(alts))}ft" if alts else "-"
            print(f"{gate:>6} | {len(ds):>3} | {statistics.median(loss):>5.1f}m | "
                  f"{statistics.median(td):>5.1f}m | {td[0]:>3.0f}-{td[-1]:<3.0f}m | {amed:>9}")
        print("→พื้น≈ = เวลาถึง touchdown (เทียบ anchor ~20-25 นาที/gate ได้ตรง). obs: หลุด ~10nm/2-3000ft")
    else:
        print("(ยังไม่มีเที่ยวที่จับจุดเข้า STAR ได้ — รอเครื่องผ่านใกล้ WILLA/NORTA/EASTE/TUMGA/LEBIM)")

    # 3) ARRIVALS ตามชั่วโมงของวัน (BKK) — จำนวนเที่ยว + เวลา gate→พื้น ต่อชั่วโมง
    #    นับจากเวลาแตะพื้นโดยประมาณ (last_ts + final ที่มองไม่เห็น ~last_alt/FPM), เฉพาะที่เข้า ≤10nm = ลงจริง
    arr = [d for d in data if d["last_ts"] and d["min_dist"] is not None and d["min_dist"] < 10]
    print("\n=== Arrivals ตามชั่วโมงของวัน (BKK) — เวลาแตะพื้นโดยประมาณ ===")
    if arr:
        by_hr = {}
        for d in arr:
            td = d["last_ts"] + (d["last_alt"] or 0) / DESCENT_FPM * 60      # touchdown ≈ last_ts + final
            by_hr.setdefault(time.gmtime(td + 7 * 3600).tm_hour, []).append(d)   # +7 ชม. = BKK
        mx = max(len(v) for v in by_hr.values())
        print(f"{'ชม.':>5} | {'n':>4} | {'gate→พื้น':>9} | บาร์ (เทียบ ชม.พีค)")
        for h in range(24):
            ds = by_hr.get(h)
            if not ds:
                continue
            gd = [x for x in ds if x["star_ts"] and x["last_ts"] > x["star_ts"]]
            tds = [(x["last_ts"] - x["star_ts"]) / 60.0 + (x["last_alt"] or 0) / DESCENT_FPM for x in gd]
            avg = f"{statistics.median(tds):.0f}m" if tds else "-"
            print(f"{h:02d}:00 | {len(ds):>4} | {avg:>9} | {'#' * round(len(ds) / mx * 24)}")
        peak = max(by_hr.items(), key=lambda kv: len(kv[1]))
        print(f"ชั่วโมงพีค: {peak[0]:02d}:00 ({len(peak[1])} เที่ยว) · รวม {len(arr)} เที่ยวที่เข้า ≤10nm")
    else:
        print("(ยังไม่มีเที่ยวที่เข้าใกล้ ≤10nm)")

    # 4) ETA คำนวณตอน alert vs เวลาจริงถึงพื้น (= alert→หลุด + final ~last_alt/900)
    alerted = [d for d in data if d["alert_ts"] and d["alert_eta"] and d["last_ts"]]
    print("\n=== ETA ที่คำนวณตอน alert vs เวลาจริงถึงพื้น ===")
    if not alerted:
        print("(ยังไม่มีเที่ยวที่ alert แล้วถูกบันทึก)")
        return
    print(f"{'flight':>8} | {'ETAคำนวณ':>8} | {'จริง≈':>6} | {'หลุดที่':>16}")
    diffs = []
    for d in sorted(alerted, key=lambda x: x["alert_ts"])[-20:]:
        actual = (d["last_ts"] - d["alert_ts"]) / 60.0 + (d["last_alt"] or 0) / DESCENT_FPM
        diffs.append(actual - d["alert_eta"])
        print(f"{(d['flight'] or d['hex']):>8} | {d['alert_eta']:>6.0f}m | {actual:>4.0f}m | "
              f"{(d['last_dist'] or 0):>4.0f}nm/{(d['last_alt'] or 0):>5}ft")
    print("\n'จริง≈' = alert→สัญญาณหลุด + final ที่มองไม่เห็น (~last_alt/750)")
    print(f"เฉลี่ย (จริง − คำนวณ) = {statistics.mean(diffs):+.1f} นาที  "
          f"(บวก = บินจริงนานกว่าที่คำนวณ → ลด ETA_DESCENT_FPM)")


if __name__ == "__main__":
    main()
