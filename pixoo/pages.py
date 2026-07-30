"""pages.py — registry ของหน้าที่หมุนได้ในโซนล่าง
เพิ่มหน้าใหม่ = เขียน 1 ฟังก์ชัน แล้วต่อท้าย PAGES
แต่ละ page รับ (d, data) แล้ววาดในโซน y=28..61 (ในกรอบ)
"""
import renderer as R


#   layout ทุกหน้า (โซนในกรอบ y28..61):
#     แถวบน y28  : ป้าย (ซ้าย) · ข้อมูลย่อย (ขวา)
#     กลาง   y38  : เลขพระเอก ตัวใหญ่ กลางจอ (สีสื่อความหมาย)
#     แถวล่าง y54 : หน่วย/label (ซ้าย) · สถานะ/ค่า (ขวา)
def feeder_status(d, data):
    health = data.get("health", "stale")
    hc = R.HEALTH.get(health, R.HEALTH["stale"])   # สีค่า/สุขภาพ
    rate = data.get("msg_per_s", 0)
    ac = data.get("aircraft", 0)

    # แถวบน: ป้าย FR24 (amber) ซ้าย · ✈ + จำนวนเครื่องบิน (น้ำเงิน) ขวา
    R.text(d, (4, 28), "FR24", "small", R.PALETTE["title"], anchor="la")
    R.draw_plane(d, 33, 28, R.PALETTE["aircraft"])
    R.text(d, (60, 28), str(ac), "small", R.PALETTE["aircraft"], anchor="ra")

    # กลาง: msg/s = เลขพระเอก (สีสุขภาพ)
    R.text(d, (32, 38), str(rate), "big", hc, anchor="ma")

    # แถวล่าง: หน่วย msg/s (ซ้าย) · สถานะ live/rcvr/DOWN (สีสุขภาพ ขวา)
    R.text(d, (4, 54), "msg/s", "small", R.PALETTE["label"], anchor="la")
    R.text(d, (60, 54), R.HEALTH_WORD.get(health, "?"), "small", hc, anchor="ra")


def tha_inbound(d, data):
    """หน้า THA inbound VTBS — ETA (นาที) เป็นเลขพระเอก สีตามความใกล้
    data["tha"] = {flight, eta_min, dist_nm, alt, gs} หรือ None"""
    tha = data.get("tha")

    R.text(d, (4, 28), "THA", "small", R.PALETTE["title"], anchor="la")

    if not tha:   # ไม่มี inbound → ไอคอนเทา + ข้อความ
        R.draw_plane(d, 6, 42, R.PALETTE["label"])
        R.text(d, (17, 42), "no inbd", "small", R.PALETTE["label"], anchor="la")
        return

    # แถวบนขวา: callsign (น้ำเงิน)
    R.text(d, (60, 28), tha["flight"], "small", R.PALETTE["aircraft"], anchor="ra")

    # กลาง: ETA พระเอก — <=15 นาที = เหลือง (ใกล้) มิฉะนั้นเขียว
    eta = int(round(tha.get("eta_min") or 0))
    ecol = R.HEALTH["recovering"] if eta <= 15 else R.HEALTH["ok"]
    R.text(d, (32, 38), str(eta), "big", ecol, anchor="ma")

    # แถวล่าง: หน่วย min (ซ้าย) · ระยะ nm (ขวา)
    dist = int(round(tha.get("dist_nm") or 0))
    R.text(d, (4, 54), "min", "small", R.PALETTE["label"], anchor="la")
    R.text(d, (60, 54), f"{dist}nm", "small", R.PALETTE["label"], anchor="ra")


def flights_list(d, data):
    """หน้า list เครื่องที่รับได้ตอนนี้ (font 3x5) — callsign · ระยะ nm · FL, เรียงใกล้→ไกล"""
    lst = data.get("flights") or []
    nrx = data.get("nrx", len(lst))

    # หัว: AIR + จำนวนเครื่องที่รับได้ทั้งหมด (amber)
    R.text(d, (3, 28), f"AIR {nrx}", "tiny", R.PALETTE["title"], anchor="la")
    if not lst:
        R.text(d, (3, 40), "NO AIRCRAFT", "tiny", R.PALETTE["label"], anchor="la")
        return

    # แถวละเครื่อง (สูงสุด 5) — callsign ซ้าย, ระยะ กลาง, FL ขวา
    for i, fl in enumerate(lst[:5]):
        y = 34 + i * 6
        R.text(d, (3, y),  fl["cs"],               "tiny", R.PALETTE["aircraft"], anchor="la")
        R.text(d, (34, y), str(fl.get("dist", 0)), "tiny", R.PALETTE["label"], anchor="la")
        R.text(d, (48, y), f"{fl.get('fl', 0):03d}", "tiny", R.PALETTE["label"], anchor="la")


def _countdown(mins):
    """นาที → ข้อความนับถอยหลังสั้น: 'NOW' / 'IN 45M' / 'IN 22H' / 'IN 3D'"""
    if mins is None:
        return ""
    if mins <= 0:
        return "NOW"
    if mins < 60:
        return f"IN {mins}M"
    if mins < 1440:
        return f"IN {mins // 60}H"
    return f"IN {mins // 1440}D"


def next_flight(d, data):
    """หน้า NEXT — งาน/เที่ยวบินถัดไปจาก Google Calendar (agenda_fetch.py → /run/agenda/next.json)
    data["agenda"] = {summary, code, route, start_str, in_min, ...} หรือ None"""
    ag = data.get("agenda")

    R.text(d, (4, 28), "NEXT", "small", R.PALETTE["title"], anchor="la")

    if not ag:   # ไม่มีนัด → ไอคอนเทา + ข้อความ
        R.draw_plane(d, 6, 42, R.PALETTE["label"])
        R.text(d, (17, 42), "no flt", "small", R.PALETTE["label"], anchor="la")
        return

    # แถวบนขวา: วันเริ่ม (เช่น 28/07) — gold
    start = ag.get("start_str") or ""
    day = start.split(" ")[0] if start else ""
    if day:
        R.text(d, (60, 28), day, "small", R.PALETTE["date"], anchor="ra")

    # hero: code เที่ยวบิน (เช่น TG632) — น้ำเงิน; ถ้าไม่มี code ใช้คำแรกของ summary
    hero = ag.get("code") or (ag.get("summary") or "").split(" ")[0] or "?"
    R.text(d, (32, 37), hero[:6], "big", R.PALETTE["aircraft"], anchor="ma")

    # แถวล่าง (tiny): route (ซ้าย) · นับถอยหลัง (ขวา สีเหลืองถ้าใกล้ <=24h)
    route = ag.get("route") or ""
    R.text(d, (4, 55), route, "tiny", R.PALETTE["label"], anchor="la")
    mins = ag.get("in_min")
    ccol = R.HEALTH["recovering"] if (mins is not None and mins <= 1440) else R.PALETTE["label"]
    R.text(d, (60, 55), _countdown(mins), "tiny", ccol, anchor="ra")


# vcgencmd get_throttled — บิตปัจจุบัน (0-3) เรียงตามความสำคัญ · บิตประวัติ (+16 = เคยเกิด)
_THR_NOW = [(0x1, "UV"), (0x4, "THR"), (0x2, "CAP"), (0x8, "TMP")]
_THR_EVER = [(0x10000, "UV"), (0x40000, "THR"), (0x20000, "CAP"), (0x80000, "TMP")]


def _throttle(raw):
    """คืน (word, sev): 'bad'=มีปัญหาตอนนี้(แดง) · 'warn'=เคยเกิด(เหลือง) · 'ok'(เขียว) · (None,None)=ไม่รู้"""
    if raw is None:
        return (None, None)
    for b, w in _THR_NOW:
        if raw & b:
            return (w, "bad")
    for b, w in _THR_EVER:
        if raw & b:
            return (w.lower(), "warn")
    return ("OK", "ok")


def _fmt2(s):
    """uptime เป็น 2 หน่วยบนสุด เช่น '3D 14H' / '14H 22M' / '22M'"""
    d, r = divmod(int(s), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}D {h}H"
    if h:
        return f"{h}H {m}M"
    return f"{m}M"


def uptime(d, data):
    """หน้า UP — system uptime (hero) + service uptime (FDR) + อุณหภูมิ CPU + เวลา boot ล่าสุด"""
    up = data.get("uptime_s", 0)
    days, rem = divmod(up, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60

    R.text(d, (4, 28), "UP", "small", R.PALETTE["title"], anchor="la")

    # ไอคอนพัดลมระบายความร้อน (กลางแถวบน) — เขียว+หมุนจริงตอนเปิด · หรี่เทานิ่ง=ปิด · ไม่วาด=ไม่รู้
    fan = data.get("fan")
    if fan is not None:
        frame = (data.get("anim", 0) // 2) if fan else 0   # เปิด=สลับเฟรม(หมุน) · ปิด=นิ่ง
        R.draw_fan(d, 26, 28, R.HEALTH["ok"] if fan else R.PALETTE["divider"], frame=frame)

    # อุณหภูมิ CPU มุมขวาบน — สีตามความร้อน (<65 เขียว · <75 เหลือง · ร้อนกว่านั้น แดง)
    t = data.get("temp_c")
    if t is not None:
        tcol = R.HEALTH["ok"] if t < 65 else (R.HEALTH["recovering"] if t < 75 else R.HEALTH["dead"])
        R.text(d, (60, 28), f"{int(round(t))}C", "small", tcol, anchor="ra")

    # hero: system uptime หน่วยใหญ่สุด — cyan
    hero = f"{days}D" if days else (f"{hours}H" if hours else f"{mins}M")
    R.text(d, (32, 36), hero, "big", R.PALETTE["time"], anchor="ma")

    # แถวล่าง (tiny): service uptime (FDR) ซ้าย · สถานะ power/throttle ขวา · เวลา boot ล่างสุด
    su = data.get("svc_uptime_s")
    svc = f"{data.get('svc_name', 'SVC')} " + (_fmt2(su) if su is not None else "--")
    R.text(d, (3, 53), svc, "tiny", R.PALETTE["label"], anchor="la")
    tw, tsev = _throttle(data.get("throttled"))
    if tw:
        tcol = {"bad": R.HEALTH["dead"], "warn": R.HEALTH["recovering"], "ok": R.HEALTH["ok"]}[tsev]
        R.text(d, (61, 53), tw, "tiny", tcol, anchor="ra")
    R.text(d, (32, 59), data.get("boot_str", "?"), "tiny", R.PALETTE["label"], anchor="mm")


def coffee_break(d, data):
    """หน้าเตือนพักกาแฟ (โผล่ตามเวลา ไม่อยู่ในรอบหมุนปกติ) — ถ้วยกาแฟ + ไอลอย (ขยับตาม anim)"""
    cup = (168, 120, 74)          # น้ำตาลกาแฟ
    R.text(d, (4, 28), "BREAK", "small", R.PALETTE["title"], anchor="la")

    # ไอกาแฟลอยขึ้น 3 เส้น — หยักซ้าย-ขวาตาม anim = เคลื่อนไหว
    a = data.get("anim", 0)
    for i, cx in enumerate((27, 32, 37)):
        for j in range(3):
            x = cx + (1 if (a + i + j) % 2 else -1)
            d.point((x, 33 + j * 2), fill=(120, 140, 160))

    # ถ้วย (สี่เหลี่ยม + ขอบปาก) + หูจับ + กาแฟ + จานรอง
    d.rectangle([(23, 41), (39, 53)], outline=cup)
    d.line([(23, 41), (39, 41)], fill=(210, 160, 96))
    d.rectangle([(39, 44), (43, 50)], outline=cup)         # หูจับ
    d.rectangle([(25, 43), (37, 45)], fill=(96, 62, 38))   # ผิวกาแฟ
    d.line([(20, 55), (42, 55)], fill=cup)                 # จานรอง

    R.text(d, (32, 59), "COFFEE TIME", "tiny", R.PALETTE["label"], anchor="mm")


def knock_off(d, data):
    """หน้าเตือนเข้านอน 22:00 (โผล่ครั้งเดียว/วัน ไม่อยู่ในรอบหมุน) — พระจันทร์เสี้ยว + ดาวกระพริบ"""
    moon = (235, 225, 150)
    R.text(d, (4, 28), "BED", "small", R.PALETTE["title"], anchor="la")

    # พระจันทร์เสี้ยว: วาดวงเต็มแล้วเจาะด้วยสี bg เยื้องขึ้น-ขวา → เหลือเสี้ยวซ้ายล่าง
    d.ellipse([(25, 38), (37, 50)], fill=moon)
    d.ellipse([(28, 36), (41, 49)], fill=R.PALETTE["bg"])

    # ดาวกระพริบรอบๆ — สว่าง=กากบาทเล็ก · หรี่=จุดเดียว (สลับตาม anim)
    a = data.get("anim", 0)
    for i, (sx, sy) in enumerate(((14, 34), (49, 36), (45, 51), (17, 53), (52, 45))):
        if (a + i) % 2:
            col = (245, 240, 200)
            for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
                d.point((sx + dx, sy + dy), fill=col)
        else:
            d.point((sx, sy), fill=(90, 92, 110))

    R.text(d, (32, 59), "TIME TO BED", "tiny", R.PALETTE["label"], anchor="mm")


# registry — หน้าจะหมุนตาม PAGE_HOLD; เพิ่มได้เรื่อยๆ ต่อท้าย
# (tha_inbound / flights_list เก็บฟังก์ชันไว้ ใส่กลับใน list ได้ทุกเมื่อ)
PAGES = [feeder_status, uptime, next_flight]
