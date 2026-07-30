#!/usr/bin/env python3
"""heartbeat.py — "กล่องดำ" (black box): ยิง snapshot สุขภาพ Pi ขึ้น Cloudflare D1 ทุก ~10 นาที.

ทำไม: Pi เคยค้างแบบเงียบ (kernel freeze, ไฟเขียวค้าง) แล้ว journal ในเครื่องหาย → ไม่รู้สาเหตุ.
เก็บ snapshot ออก cloud ทุก 10 นาที → "row สุดท้ายก่อนเงียบ" = สภาพตอนใกล้ค้าง.
พอค้าง เปิด D1 ดูไม่กี่แถวท้ายก่อน gap: temp พุ่ง? throttle ติด? volts ตก? load พุ่ง (cron.daily)?
  - throttled = raw hex (รวม sticky "occurred" bits 0x1_0000+) → จับ undervoltage ชั่ววูบระหว่างรอบได้
  - uptime_s ลดลง = เพิ่งรีบูต → ช่วง gap ก่อนหน้าคือตอนค้าง (แยก hang ออกจากเน็ตหลุด: เน็ตหลุด uptime ยังเดินต่อ)
  - ถ้าแถวท้ายทุกค่าปกติแล้วเงียบดื้อๆ = hard brownout (ไฟตกกะทันหัน ไม่ทิ้งร่องรอย)

NVMe/SSD ก็เคยค้าง → ตัด storage/USB ออก เหลือ ไฟ/kernel/ความร้อน — ฟิลด์พวกนี้ชี้ตัวได้.

idempotent: uid = station:ts + INSERT OR IGNORE (ส่งซ้ำไม่ dup). stdlib ล้วน (reuse D1 pattern จาก outbox.py).
config ใน /etc/fr24-watchdog.env: D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN, STATION_ID (opt).
สร้างตาราง D1 ครั้งเดียวก่อนใช้ (ดู HEARTBEAT_SCHEMA ท้ายไฟล์ / README). รันโดย systemd timer (User=arin) ทุก 10 นาที.
"""
import json, os, socket, subprocess, time, urllib.request, urllib.error

ENV_FILE = "/etc/fr24-watchdog.env"
STATUS_F = "/run/fr24-watchdog/status.json"
LOCAL_LOG = "/home/arin/heartbeat.jsonl"     # backup ในเครื่อง (cloud = ตัวหลักที่รอด hang)
STALE_SEC = 20 * 60                           # status.json เก่ากว่านี้ = watchdog ไม่เดิน → stale

# ลำดับคอลัมน์ = ลำดับใน INSERT (ต้องตรงกับ schema ใน D1)
COLS = ["uid", "station", "ts", "uptime_s", "temp_c", "throttled", "volts_core",
        "freq_arm_mhz", "load1", "load5", "load15", "mem_avail_mb", "disk_used_pct",
        "msg_per_s", "aircraft", "health"]


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
STATION = ENV.get("STATION_ID") or socket.gethostname()


def log(*a):
    print(time.strftime("%F %T"), "heartbeat:", *a)


def _vcgencmd(*args):
    try:
        return subprocess.run(["vcgencmd", *args], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def read_throttled():
    out = _vcgencmd("get_throttled")             # "throttled=0x0"
    try:
        return int(out.split("=")[1], 16)
    except (IndexError, ValueError):
        return None


def read_volts():
    out = _vcgencmd("measure_volts", "core")     # "volt=0.7360V"
    try:
        return round(float(out.split("=")[1].rstrip("V")), 4)
    except (IndexError, ValueError):
        return None


def read_arm_mhz():
    out = _vcgencmd("measure_clock", "arm")      # "frequency(48)=2400000000"
    try:
        return int(out.split("=")[1]) // 1_000_000
    except (IndexError, ValueError):
        return None


def read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def read_uptime():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


def read_loads():
    try:
        with open("/proc/loadavg") as f:
            a = f.read().split()
        return float(a[0]), float(a[1]), float(a[2])
    except (OSError, ValueError, IndexError):
        return None, None, None


def read_mem_avail_mb():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def read_disk_used_pct():
    try:
        st = os.statvfs("/")
        return round((st.f_blocks - st.f_bfree) / st.f_blocks * 100)
    except (OSError, ZeroDivisionError):
        return None


def read_feeder():
    try:
        with open(STATUS_F) as f:
            d = json.load(f)
        health = d.get("health", "stale")
        if time.time() - d.get("ts", 0) > STALE_SEC:
            health = "stale"
        return d.get("msg_per_s", 0), d.get("aircraft", 0), health
    except (OSError, ValueError):
        return None, None, "stale"


def snapshot():
    ts = int(time.time())
    l1, l5, l15 = read_loads()
    mps, ac, health = read_feeder()
    return {
        "uid": f"{STATION}:{ts}",
        "station": STATION,
        "ts": ts,
        "uptime_s": read_uptime(),
        "temp_c": read_temp(),
        "throttled": read_throttled(),
        "volts_core": read_volts(),
        "freq_arm_mhz": read_arm_mhz(),
        "load1": l1, "load5": l5, "load15": l15,
        "mem_avail_mb": read_mem_avail_mb(),
        "disk_used_pct": read_disk_used_pct(),
        "msg_per_s": mps, "aircraft": ac, "health": health,
    }


def d1_insert(row):
    acc, dbid, tok = ENV.get("D1_ACCOUNT_ID"), ENV.get("D1_DATABASE_ID"), ENV.get("D1_API_TOKEN")
    if not (acc and dbid and tok):
        raise RuntimeError(f"ตั้ง D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN ใน {ENV_FILE} ก่อน")
    sql = (f"INSERT OR IGNORE INTO heartbeat ({','.join(COLS)}) "
           f"VALUES ({','.join(['?'] * len(COLS))})")
    params = [row.get(c) for c in COLS]
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{dbid}/query"
    req = urllib.request.Request(
        url, data=json.dumps({"sql": sql, "params": params}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        raise RuntimeError(f"D1 error: {resp.get('errors')}")


def local_append(row):
    try:
        with open(LOCAL_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log("local log ไม่สำเร็จ:", e)


def main():
    row = snapshot()
    local_append(row)                            # backup ในเครื่องก่อนเสมอ (เผื่อ D1 ล่ม)
    print(json.dumps(row, ensure_ascii=False))
    try:
        d1_insert(row)
    except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
        log("ส่ง D1 ไม่สำเร็จ (เน็ตหลุด?) — ข้ามรอบนี้:", e)   # ไม่ crash, รอบหน้าค่อยส่งใหม่


# สร้างตารางใน D1 ครั้งเดียวก่อนใช้ (wrangler d1 execute / dashboard console):
HEARTBEAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeat (
  uid TEXT PRIMARY KEY, station TEXT, ts INTEGER, uptime_s INTEGER,
  temp_c REAL, throttled INTEGER, volts_core REAL, freq_arm_mhz INTEGER,
  load1 REAL, load5 REAL, load15 REAL, mem_avail_mb INTEGER, disk_used_pct INTEGER,
  msg_per_s REAL, aircraft INTEGER, health TEXT
);
CREATE INDEX IF NOT EXISTS idx_hb_station_ts ON heartbeat(station, ts);
"""

if __name__ == "__main__":
    main()
