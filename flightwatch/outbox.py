#!/usr/bin/env python3
"""outbox.py — ส่ง events + tracks จาก SQLite ขึ้น cloud sink (ตอนนี้: Cloudflare D1)

ทนเน็ตหลุด (outbox pattern): mark คอลัมน์ `sent` ต่อแถว. แถวที่ยังไม่ส่ง = คิวไว้ retry รอบหน้า.
idempotent: D1 มี uid เป็น PRIMARY KEY + INSERT OR IGNORE → ส่งซ้ำ (เช่น crash หลัง POST) ไม่เกิด dup.
pluggable: เพิ่ม sink ใหม่ = เขียน send(table, cols, rows)->จำนวนที่สำเร็จ แล้วใส่ใน SINKS.

secrets/config ใน /etc/fr24-watchdog.env:
  D1_ACCOUNT_ID, D1_DATABASE_ID, D1_API_TOKEN   (ต้องมีเมื่อ sink=d1)
  STATION_ID   (optional; default = hostname)   OUTBOX_SINK (optional; default d1)
รันโดย systemd timer (User=arin). stdlib ล้วน.
"""
import sqlite3, sys, os, json, time, socket, urllib.request, urllib.error

DB = "/home/arin/flightwatch.db"
ENV_FILE = "/etc/fr24-watchdog.env"
BATCH = 200          # แถวสูงสุด/table/รอบ (กัน payload ใหญ่ตอน backlog หลังเน็ตหลุดนาน)
D1_MAX_PARAMS = 90   # D1 จำกัด bound params ~100/query → เผื่อไว้


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


ENV = load_env(ENV_FILE)


def cfg(key, default=None):
    """env ของ process (systemd EnvironmentFile) ก่อน แล้วค่อยอ่านไฟล์เอง.
    ต้องดู os.environ ด้วย เพราะ unit รันเป็น User=arin แต่ /etc/fr24-watchdog.env เก็บความลับ —
    ถ้าไฟล์เป็น root-only, load_env() จะเงียบ ๆ คืน {} แล้วฟ้อง 'ตั้ง D1_... ก่อน' ทั้งที่ตั้งไว้แล้ว
    (อาการเดียวกับ 'ยังไม่ได้ตั้ง' เป๊ะ — แยกไม่ออก). ให้ systemd อ่านไฟล์ในฐานะ root ส่งเข้ามาแทน."""
    return os.environ.get(key) or ENV.get(key) or default


STATION = cfg("STATION_ID") or socket.gethostname()


def log(*a):
    print(time.strftime("%F %T"), "outbox:", *a)


# ---- config ต่อ table: คอลัมน์ (ตามลำดับใน SQLite) + วิธีสร้าง uid ----
TABLES = {
    "events": {
        "cols": ["ts", "flight", "hex", "eta_min", "dist_nm", "gs", "alt"],
        "uid": lambda d: f"{STATION}:{d['ts']}:{d['hex']}",
    },
    "tracks": {
        "cols": ["hex", "flight", "watched", "first_ts", "last_ts", "samples",
                 "min_dist_nm", "alt_at_min", "min_alt", "last_dist_nm", "last_alt",
                 "max_dist_nm", "alert_ts", "alert_eta", "star_fix", "star_alt", "star_ts"],
        "uid": lambda d: f"{STATION}:{d['hex']}:{d['last_ts']}",
    },
    # แคตตาล็อกเที่ยวบิน 1 แถว/(วัน UTC, เที่ยวบิน, ลำ) — ป้อน API ภายนอก
    # where: ข้ามแถวที่ reg_lookup ยังไม่แตะ (reg_state=0) — ส่งขึ้นไปครั้งเดียวตอนข้อมูลนิ่งแล้ว
    # (D1 ใช้ INSERT OR IGNORE = ส่งซ้ำไม่อัปเดตของเดิม ถ้าส่งตอน reg ยังว่างทะเบียนจะไม่มีวันตามไปเติม)
    "sightings": {
        "cols": ["day", "flight", "hex", "first_seen_ts", "first_seen_utc", "reg"],
        "uid": lambda d: f"{STATION}:{d['day']}:{d['flight']}:{d['hex']}",
        "where": "reg_state != 0",
    },
}


# ---- Cloudflare D1 sink ----
def d1_query(sql, params):
    acc, dbid, tok = cfg("D1_ACCOUNT_ID"), cfg("D1_DATABASE_ID"), cfg("D1_API_TOKEN")
    if not (acc and dbid and tok):
        raise RuntimeError(f"ตั้ง D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN ใน {ENV_FILE} ก่อน")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{dbid}/query"
    req = urllib.request.Request(
        url, data=json.dumps({"sql": sql, "params": params}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        raise RuntimeError(f"D1 error: {resp.get('errors')}")
    return resp


def d1_send(table, cols, rows):
    """INSERT OR IGNORE เป็น chunk (คุม params ไม่ให้เกินลิมิต D1). คืนจำนวนแถวที่สำเร็จ (prefix)."""
    per = max(1, D1_MAX_PARAMS // len(cols))
    done = 0
    for i in range(0, len(rows), per):
        chunk = rows[i:i + per]
        ph = ",".join("(" + ",".join(["?"] * len(cols)) + ")" for _ in chunk)
        sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES {ph}"
        params = [v for row in chunk for v in row]
        try:
            d1_query(sql, params)
        except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
            log(f"{table}: ส่งไม่สำเร็จที่แถว {done} — {e} (คิวที่เหลือไว้รอบหน้า)")
            return done
        done += len(chunk)
    return done


SINKS = {"d1": d1_send}


def forward(db, table, tcfg, sink):
    cols = tcfg["cols"]
    extra = f" AND ({tcfg['where']})" if tcfg.get("where") else ""  # เงื่อนไข "พร้อมส่ง" เฉพาะ table
    try:
        rows = db.execute(
            f"SELECT rowid,{','.join(cols)} FROM {table} WHERE sent=0{extra} "
            f"ORDER BY rowid LIMIT {BATCH}"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0                                    # ตารางยังไม่มี/ยังไม่มีคอลัมน์ sent
    if not rows:
        return 0
    rowids = [r[0] for r in rows]
    send_cols = ["uid", "station"] + cols
    send_rows = [(tcfg["uid"](dict(zip(cols, r[1:], strict=True))), STATION, *r[1:]) for r in rows]
    n = sink(table, send_cols, send_rows)
    if n:
        done = rowids[:n]
        db.execute(f"UPDATE {table} SET sent=1 WHERE rowid IN ({','.join(['?'] * len(done))})", done)
        db.commit()
        more = " (ยังมี backlog)" if len(rows) == BATCH else ""
        log(f"{table}: ส่ง {n}/{len(rows)} แถว{more}")
    return n


def main():
    if not os.path.exists(DB):
        return                                       # flight_watcher ยังไม่สร้าง DB
    sink_name = cfg("OUTBOX_SINK", "d1")
    sink = SINKS.get(sink_name)
    if not sink:
        log(f"ไม่รู้จัก sink '{sink_name}' (มี: {','.join(SINKS)})")
        sys.exit(1)

    db = sqlite3.connect(DB, timeout=30)             # รอ lock ได้ (flight_watcher เขียนพร้อมกัน)
    for t in TABLES:                                 # เพิ่มคอลัมน์ sent — idempotent, ไม่แตะ flight_watcher
        try:
            db.execute(f"ALTER TABLE {t} ADD COLUMN sent INTEGER DEFAULT 0")
            db.commit()
        except sqlite3.OperationalError:
            pass                                     # มีแล้ว หรือตารางยังไม่มี

    total = 0
    for t, tcfg in TABLES.items():        # ห้ามตั้งชื่อ cfg — จะบัง cfg() ทำให้เป็น local ทั้งฟังก์ชัน
        try:
            total += forward(db, t, tcfg, sink)
        except Exception as e:                       # กัน table เดียวล้มทั้ง run
            log(f"{t}: ผิดพลาด — {e}")
    if total:
        log(f"รวมส่ง {total} แถว (sink={sink_name}, station={STATION})")


if __name__ == "__main__":
    main()
