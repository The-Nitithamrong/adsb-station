#!/usr/bin/env python3
"""metar_leadup.py — ตัวแปรใน METAR เปลี่ยนยังไงใน 12 ชั่วโมงก่อนฝนตก (VTBS).

คำถาม: temp / dew point / cloud / QNH ก่อนฝนตก มีรูปแบบที่ใช้เตือนล่วงหน้าได้มั้ย

⚠️ จุดตายของการวิเคราะห์นี้คือ **กลุ่มควบคุม** — ไม่มีมันแล้วผลลัพธ์จะหลอกตัวเอง
   "อุณหภูมิลดลงก่อนฝนตก" เป็นจริงเสมอ เพราะฝนกรุงเทพตกบ่าย-เย็น ซึ่งเป็นช่วงที่อุณหภูมิ
   ลดลงอยู่แล้วทุกวันไม่ว่าฝนจะตกหรือไม่. ถ้าเทียบกับ "ค่าเฉลี่ยรวม" จะเห็นสัญญาณปลอมทันที.
   → จับคู่ทุก onset กับหน้าต่างควบคุมที่ **ชั่วโมงเดียวกันของวัน** บนวันที่ไม่มีฝน
     ส่วนต่างที่เหลือจึงเป็นของฝนจริง ไม่ใช่วงจรกลางวัน-กลางคืน

แหล่งข้อมูล: Iowa State IEM ASOS archive (ฟรี ไม่ต้องใช้ key) — ให้ METAR ที่ parse มาแล้ว
เป็น CSV จึงไม่ต้องเขียน parser METAR เอง. รันบน Pi (sandbox ของ Claude บล็อกโดเมนนี้).

ใช้: python3 weather/metar_leadup.py [จำนวนวันย้อนหลัง]     (ค่าเริ่มต้น 90)
stdlib ล้วน ตามกติกาของ repo.
"""
import csv
import io
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

STATION = "VTBS"
TZ_BKK = timezone(timedelta(hours=7))
LEAD_H = 12                 # ย้อนดูกี่ชั่วโมงก่อนฝนเริ่ม
DRY_BEFORE_H = 3            # ต้องแห้งต่อเนื่องกี่ชั่วโมงก่อนถึงนับเป็น "ฝนเริ่มตก" (กันนับซ้ำในพายุลูกเดียว)
CTRL_CLEAR_H = 3            # หน้าต่างควบคุมต้องไม่มีฝนอีกกี่ชั่วโมงหลังจุดอ้างอิง
HTTP_TIMEOUT = 120
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
RAIN_CODES = ("RA", "DZ", "GR", "GS", "TS")   # TSRA/SHRA/-RA/+RA ถูกจับด้วย substring
CLOUD_OKTA = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0,
              "FEW": 1.5, "SCT": 3.5, "BKN": 6.0, "OVC": 8.0, "VV": 8.0}


def fetch(days):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    q = [("station", STATION), ("tz", "UTC"), ("format", "onlycomma"),
         ("missing", "M"), ("trace", "0.0001"), ("latlon", "no"), ("elev", "no"),
         ("year1", start.year), ("month1", start.month), ("day1", start.day),
         ("year2", end.year), ("month2", end.month), ("day2", end.day)]
    # alti (inHg) มีครบกว่า mslp ในหลายสนามนอกสหรัฐ → ขอทั้งคู่แล้วเลือกอันที่มีค่า
    for d in ("tmpc", "dwpc", "mslp", "alti", "skyc1", "skyl1", "p01i", "wxcodes"):
        q.append(("data", d))
    url = f"{IEM}?{urllib.parse.urlencode(q)}"
    print(f"ดึง METAR {STATION} ย้อนหลัง {days} วัน ...")
    req = urllib.request.Request(url, headers={"User-Agent": "adsb-station metar_leadup"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode(errors="ignore")


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def parse(text):
    """CSV → obs รายชั่วโมง (เอา ob ที่ใกล้ต้นชั่วโมงที่สุดของแต่ละชั่วโมง)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {}
    by_hour = {}
    for r in rows:
        try:
            t = datetime.strptime(r["valid"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        hkey = t.replace(minute=0, second=0, microsecond=0)
        # ระยะห่างจากต้นชั่วโมง — ob ที่ :00 ชนะ ob ที่ :30
        gap = abs((t - hkey).total_seconds())
        if hkey in by_hour and by_hour[hkey][0] <= gap:
            continue
        qnh = num(r.get("mslp"))
        if qnh is None:
            alti = num(r.get("alti"))
            qnh = alti * 33.8639 if alti is not None else None   # inHg → hPa
        tmp, dwp = num(r.get("tmpc")), num(r.get("dwpc"))
        wx = (r.get("wxcodes") or "").upper()
        p01 = num(r.get("p01i")) or 0.0
        by_hour[hkey] = (gap, {
            "t": tmp, "td": dwp,
            "sp": (tmp - dwp) if (tmp is not None and dwp is not None) else None,
            "qnh": qnh,
            "cld": CLOUD_OKTA.get((r.get("skyc1") or "").strip().upper()),
            "base": num(r.get("skyl1")),
            "rain": any(c in wx for c in RAIN_CODES) or p01 > 0.02,
        })
    return {k: v[1] for k, v in by_hour.items()}


def onsets(obs):
    """ชั่วโมงที่ฝน 'เริ่ม' = มีฝน โดยก่อนหน้า DRY_BEFORE_H ชั่วโมงแห้งครบทุกชั่วโมง."""
    out = []
    for h, o in sorted(obs.items()):
        if not o["rain"]:
            continue
        prev = [obs.get(h - timedelta(hours=i)) for i in range(1, DRY_BEFORE_H + 1)]
        if any(p is None for p in prev) or any(p["rain"] for p in prev):
            continue
        if all(obs.get(h - timedelta(hours=i)) is not None for i in range(1, LEAD_H + 1)):
            out.append(h)
    return out


def controls(obs):
    """จุดอ้างอิงบนวันที่ไม่มีฝน: ไม่มีฝนตั้งแต่ -LEAD_H จนถึง +CTRL_CLEAR_H และมีข้อมูลครบ."""
    out = []
    for h in sorted(obs):
        span = [obs.get(h + timedelta(hours=i)) for i in range(-LEAD_H, CTRL_CLEAR_H + 1)]
        if any(s is None for s in span) or any(s["rain"] for s in span):
            continue
        out.append(h)
    return out


def series(obs, anchors, key):
    """ค่าเฉลี่ยของตัวแปร key ที่ lead -LEAD_H..0 เทียบกับจุดอ้างอิง."""
    out = {}
    for lead in range(-LEAD_H, 1):
        vals = []
        for a in anchors:
            o = obs.get(a + timedelta(hours=lead))
            if o and o.get(key) is not None:
                vals.append(o[key])
        out[lead] = vals
    return out


def cohen_d(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    sa, sb = st.pstdev(a), st.pstdev(b)
    pooled = (((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2) / (len(a) + len(b) - 2)) ** 0.5
    return (st.mean(a) - st.mean(b)) / pooled if pooled else None


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    obs = parse(fetch(days))
    if not obs:
        print("ไม่ได้ข้อมูล — IEM อาจไม่มีสถานีนี้ หรือ endpoint เปลี่ยน ลอง ogimet.com แทน")
        return
    lo, hi = min(obs), max(obs)
    print(f"obs รายชั่วโมง {len(obs)} ชั่วโมง  {lo.astimezone(TZ_BKK):%Y-%m-%d} "
          f"→ {hi.astimezone(TZ_BKK):%Y-%m-%d} (เวลาไทย)")
    rainy = sum(1 for o in obs.values() if o["rain"])
    print(f"ชั่วโมงที่มีฝน {rainy} ({rainy/len(obs)*100:.1f}%)")

    ons = onsets(obs)
    ctl_all = controls(obs)
    print(f"ฝนเริ่มตก (แห้งมาก่อน {DRY_BEFORE_H} ชม. + ข้อมูลครบ {LEAD_H} ชม.): {len(ons)} ครั้ง")
    if len(ons) < 10:
        print("น้อยเกินไป — เพิ่มจำนวนวันย้อนหลัง"); return

    # จับคู่ควบคุมตาม "ชั่วโมงเดียวกันของวัน" (เวลาไทย) — หัวใจของการวิเคราะห์นี้
    by_h = {}
    for c in ctl_all:
        by_h.setdefault(c.astimezone(TZ_BKK).hour, []).append(c)
    ctl = []
    for o in ons:
        ctl.extend(by_h.get(o.astimezone(TZ_BKK).hour, []))
    ctl = sorted(set(ctl))
    print(f"หน้าต่างควบคุม (ชั่วโมงเดียวกันของวัน ไม่มีฝน): {len(ctl)} ครั้ง")

    dist = {}
    for o in ons:
        dist[o.astimezone(TZ_BKK).hour] = dist.get(o.astimezone(TZ_BKK).hour, 0) + 1
    print("ฝนเริ่มตกตามชั่วโมง (ไทย):",
          " ".join(f"{h:02d}:{n}" for h, n in sorted(dist.items()) if n))

    VARS = [("t", "อุณหภูมิ °C"), ("td", "จุดน้ำค้าง °C"), ("sp", "T−Td °C"),
            ("qnh", "QNH hPa"), ("cld", "เมฆ okta"), ("base", "ฐานเมฆ ft")]
    print("\n" + "=" * 96)
    print("ค่าเฉลี่ยก่อนฝนเริ่มตก เทียบกับกลุ่มควบคุมชั่วโมงเดียวกัน (diff = ฝน − ควบคุม)")
    print("=" * 96)
    for key, label in VARS:
        R, C = series(obs, ons, key), series(obs, ctl, key)
        print(f"\n--- {label}")
        print("  lead   ฝน      ควบคุม   diff     d")
        for lead in range(-LEAD_H, 1):
            r, c = R[lead], C[lead]
            if len(r) < 3 or len(c) < 3:
                continue
            mr, mc = st.mean(r), st.mean(c)
            d = cohen_d(r, c)
            flag = ""
            if d is not None:
                flag = " ***" if abs(d) >= 0.8 else " **" if abs(d) >= 0.5 else " *" if abs(d) >= 0.2 else ""
            print(f"  {lead:+3d}h  {mr:7.2f} {mc:8.2f} {mr-mc:+7.2f}  "
                  f"{d if d is not None else float('nan'):+5.2f}{flag}")

    # แนวโน้ม 3 ชั่วโมง (ตัวที่ METAR ไทยไม่มีให้ ต้องคำนวณเอง)
    print("\n--- แนวโน้ม QNH 3 ชม. (ค่า ณ lead − ค่าเมื่อ 3 ชม. ก่อนหน้านั้น)")
    print("  lead   ฝน      ควบคุม   diff     d")
    for lead in range(-LEAD_H + 3, 1):
        def trend(anchors):
            v = []
            for a in anchors:
                a1, a0 = obs.get(a + timedelta(hours=lead)), obs.get(a + timedelta(hours=lead - 3))
                if a1 and a0 and a1["qnh"] is not None and a0["qnh"] is not None:
                    v.append(a1["qnh"] - a0["qnh"])
            return v
        r, c = trend(ons), trend(ctl)
        if len(r) < 3 or len(c) < 3:
            continue
        d = cohen_d(r, c)
        flag = " ***" if d and abs(d) >= 0.8 else " **" if d and abs(d) >= 0.5 else " *" if d and abs(d) >= 0.2 else ""
        print(f"  {lead:+3d}h  {st.mean(r):7.2f} {st.mean(c):8.2f} {st.mean(r)-st.mean(c):+7.2f}  "
              f"{d if d is not None else float('nan'):+5.2f}{flag}")

    print("\nd = Cohen's d (ส่วนต่างเป็นกี่เท่าของความผันผวน): * ≥0.2 เล็ก · ** ≥0.5 กลาง · *** ≥0.8 ใหญ่")
    print("ตัวแปรที่ d ยังใหญ่ตั้งแต่ lead ติดลบมาก ๆ = เตือนล่วงหน้าได้จริง")
    print("ถ้า d ใหญ่เฉพาะ -1h/-2h = รู้ตอนฝนจะตกอยู่แล้ว ไม่ได้ช่วยพยากรณ์")


if __name__ == "__main__":
    main()
