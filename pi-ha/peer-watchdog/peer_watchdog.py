#!/usr/bin/env python3
"""peer_watchdog.py — Pi#2 เฝ้า Pi#1 ผ่าน MQTT + escalation ladder (รันบน Pi#2 / HA box).

ทำไมต้องมี ทั้งๆ ที่ Pi#1 มี fr24-watchdog ของตัวเองแล้ว:
  fr24-watchdog เป็น software watchdog **บน Pi#1 เอง** — ถ้า Pi#1 hang ทั้งเครื่อง
  (kernel lockup / green-LED-stuck) มันตายไปด้วย กู้ dongle ไม่ได้. peer-watchdog อยู่คนละ
  เครื่อง → เห็นความเงียบและตัดไฟข้ามเครื่องได้. (ESP32 watchdog = backstop ฮาร์ดแวร์อีกชั้น
  ที่ไม่พึ่ง MQTT เลย — เก็บไว้ทั้งคู่.)

หลักการ: **วัดจากความเงียบ** (อายุ heartbeat ล่าสุดบน fleet/pi-adsb/health) ไม่ใช่ health flag —
  เพราะ health flag ที่ยัง "ok" ตอน dongle hang คือบั๊ก 21 ชม.เดิม. เงียบนานขึ้น → ยกระดับ action:
    HEALTHY  <  HEALTH_STALE_S      ปกติ
    L0 log   >= HEALTH_STALE_S      (120s)  บันทึกเฉยๆ
    L1 soft  >= SOFT_RESTART_S      (180s)  MQTT cmd restart-services
    L2 ssh   >= SSH_ACTION_S        (300s)  ssh restart → (ยังเงียบ) ssh reboot
    L3 power >= POWER_CYCLE_S       (600s)  ตัดไฟ — เฉพาะเมื่อ ping+ssh ตายทั้งคู่ (guard)
  ทุก action/ปฏิเสธ publish ไป fleet/incident + Telegram, และนับ rate-limit ที่ persist ลง
  state.json (watchdog restart ไม่ล้างโควตา). heartbeat กลับมา → รีเซ็ต ladder เป็น HEALTHY.

โครงสร้าง: thread นึง sub MQTT (อัปเดต last_health) — main loop tick ทุก TICK_S เช็ค "อายุ"
  heartbeat. **ต้องมี tick แยก** เพราะความเงียบไม่สร้าง event ให้ปลุก (ไม่มีข้อความ = ไม่ตื่น).

stdlib + mosquitto_sub/pub (apt mosquitto-clients) — pattern เดียวกับ ha/mqtt_publish.py.
DRY_RUN=1 (ดีฟอลต์) = ไม่ทำ action จริง (ดู escalation.py).
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared", "pylib"))

import escalation  # noqa: E402
from fleet_mqtt import (COOLDOWN_AFTER_FAIL_S, HEALTH, HEALTH_STALE_S,  # noqa: E402
                        INCIDENT, MAINTENANCE, POWER_CYCLE_S, SOFT_RESTART_S,
                        SSH_ACTION_S, STATUS, CMD, MAX_POWER_CYCLES_PER_24H,
                        MAX_REBOOTS_PER_24H)
import notify  # noqa: E402

BROKER_HOST = os.environ.get("BROKER_HOST", "127.0.0.1")
BROKER_PORT = os.environ.get("BROKER_PORT", "1883")
BROKER_USER = os.environ.get("BROKER_USER", "")
BROKER_PASS = os.environ.get("BROKER_PASS", "")
STATE_PATH  = os.environ.get("STATE_PATH", "/var/lib/fleet/state.json")
STATION_ID  = os.environ.get("STATION_ID", "pi-adsb")
TICK_S      = int(os.environ.get("TICK_S", "15"))   # เช็คความเงียบทุกกี่วินาที

DAY = 86400


# ---------------- mosquitto helpers ----------------
def _auth_args():
    a = ["-h", BROKER_HOST, "-p", str(BROKER_PORT)]
    if BROKER_USER:
        a += ["-u", BROKER_USER, "-P", BROKER_PASS]
    return a


def mqtt_pub(topic, payload, retain=False):
    argv = ["mosquitto_pub"] + _auth_args() + ["-t", topic, "-m", payload]
    if retain:
        argv.append("-r")
    try:
        subprocess.run(argv, check=False, timeout=10)
    except Exception as e:
        print("mqtt_pub error:", e)


def publish_cmd(cmd):
    """peer-watchdog → Pi#1 บน fleet/pi-adsb/cmd (Pi#1 health-agent subscribe แล้วทำตาม)."""
    mqtt_pub(CMD, json.dumps({"cmd": cmd, "from": "peer-watchdog"}))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def incident(text, level="info"):
    """publish ไป fleet/incident + Telegram — ทุก action/ปฏิเสธเห็นได้ที่เดียว."""
    mqtt_pub(INCIDENT, json.dumps({"ts": _now_iso(), "level": level,
                                   "station": STATION_ID, "msg": text}))
    notify.telegram(f"[peer-watchdog:{level}] {text}")


# ---------------- persistent state (rate limits) ----------------
class State:
    """นับ reboot/power-cycle + maintenance flag, persist ลง state.json.
    watchdog restart → โหลดกลับ → โควตา 24 ชม. ไม่รีเซ็ต (กัน loop ตัดไฟรัวๆ)."""

    def __init__(self, path=STATE_PATH):
        self.path = path
        self.reboots = []            # list[epoch] ของ ssh reboot
        self.power_cycles = []       # list[epoch] ของ L3 ตัดไฟ
        self.last_power_cycle = 0.0
        self.cooldown_until = 0.0
        self._maint_until = 0.0      # epoch; > now = maintenance active
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.reboots = d.get("reboots", [])
            self.power_cycles = d.get("power_cycles", [])
            self.last_power_cycle = d.get("last_power_cycle", 0.0)
            self.cooldown_until = d.get("cooldown_until", 0.0)
        except FileNotFoundError:
            pass
        except Exception as e:
            print("state load error:", e)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({
                    "reboots": self.reboots,
                    "power_cycles": self.power_cycles,
                    "last_power_cycle": self.last_power_cycle,
                    "cooldown_until": self.cooldown_until,
                }, f)
            os.replace(tmp, self.path)
        except Exception as e:
            print("state save error:", e)

    def _prune(self, lst, now):
        return [t for t in lst if now - t < DAY]

    def reboots_last_24h(self, now):
        self.reboots = self._prune(self.reboots, now)
        return len(self.reboots)

    def power_cycles_last_24h(self, now):
        self.power_cycles = self._prune(self.power_cycles, now)
        return len(self.power_cycles)

    def record_reboot(self, now):
        self.reboots.append(now)
        self.save()

    def record_power_cycle(self, now):
        self.power_cycles.append(now)
        self.last_power_cycle = now
        self.save()

    def enter_cooldown(self, now):
        self.cooldown_until = now + COOLDOWN_AFTER_FAIL_S
        self.save()

    # ---- maintenance flag (จาก retained MQTT MAINTENANCE) ----
    def set_maintenance(self, until_epoch):
        self._maint_until = until_epoch

    def maintenance_active(self, now):
        return now < self._maint_until


# ---------------- MQTT subscriber thread ----------------
class Subscriber(threading.Thread):
    """sub HEALTH/MAINTENANCE/STATUS ตลอดเวลา, อัปเดต shared state (last_health + maintenance).
    read บรรทัดต่อบรรทัด (บั๊ก nc|awk buffering เดิม — อย่า simplify กลับเป็น pipe)."""

    daemon = True

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.last_health = 0.0     # epoch heartbeat ล่าสุด (0 = ยังไม่เคยได้)
        self._lock = threading.Lock()

    def get_last_health(self):
        with self._lock:
            return self.last_health

    def run(self):
        argv = ["mosquitto_sub"] + _auth_args() + [
            "-v", "-t", HEALTH, "-t", MAINTENANCE, "-t", STATUS,
        ]
        while True:
            try:
                p = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1)
                for line in p.stdout:
                    line = line.rstrip("\n")
                    if " " not in line:
                        continue
                    topic, _, payload = line.partition(" ")
                    self._handle(topic, payload)
            except Exception as e:
                print("subscriber error, retry ใน 5s:", e)
            time.sleep(5)   # sub ตาย (broker restart) → เชื่อมใหม่

    def _handle(self, topic, payload):
        if topic == HEALTH:
            with self._lock:
                self.last_health = time.time()
        elif topic == MAINTENANCE:
            self._apply_maintenance(payload)
        elif topic == STATUS and payload.strip().strip('"').lower() == "offline":
            incident("LWT: fleet/pi-adsb/status = offline", "warn")

    def _apply_maintenance(self, payload):
        """retained payload: {"until":"ISO8601Z","reason":"..."} — self-expiry."""
        try:
            if not payload.strip():
                self.state.set_maintenance(0)
                return
            d = json.loads(payload)
            until = d.get("until", "")
            if until:
                # ISO8601 Z (UTC) → epoch
                epoch = calendar_timegm(time.strptime(until, "%Y-%m-%dT%H:%M:%SZ"))
            else:
                epoch = 0
            self.state.set_maintenance(epoch)
            if epoch > time.time():
                incident(f"maintenance flag ON ถึง {until} ({d.get('reason','')}) — watchdog พัก", "info")
        except Exception as e:
            print("maintenance parse error:", e)


def calendar_timegm(t):
    """timegm แบบ stdlib (แปลง struct_time UTC → epoch) — เลี่ยง import calendar เกินจำเป็น."""
    import calendar
    return calendar.timegm(t)


# ---------------- escalation ladder ----------------
class Ladder:
    """ตัดสินใจ action จาก 'ความเงียบ' (stale) — action ละครั้งต่อการเงียบหนึ่งรอบ (edge)."""

    def __init__(self, state):
        self.state = state
        self.stage = "HEALTHY"

    def _reset(self):
        if self.stage != "HEALTHY":
            incident("Pi#1 heartbeat กลับมาแล้ว → รีเซ็ต ladder เป็น HEALTHY", "ok")
        self.stage = "HEALTHY"

    def _advance(self, stage):
        """ทำ action ของ stage เฉพาะเมื่อ 'ยกระดับใหม่' (กันยิงซ้ำทุก tick)."""
        if self.stage == stage:
            return
        self.stage = stage
        getattr(self, "_do_" + stage.lower())()

    def evaluate(self, stale, now):
        if stale < HEALTH_STALE_S:
            self._reset()
        elif stale < SOFT_RESTART_S:
            self._advance("l0_log")
        elif stale < SSH_ACTION_S:
            self._advance("l1_soft")
        elif stale < POWER_CYCLE_S:
            self._advance("l2_ssh")
        else:
            self._advance("l3_power")

    # ---- per-stage actions ----
    def _do_l0_log(self):
        incident(f"Pi#1 เงียบ ≥ {HEALTH_STALE_S}s — จับตา (ยังไม่ทำอะไร)", "warn")

    def _do_l1_soft(self):
        incident(f"Pi#1 เงียบ ≥ {SOFT_RESTART_S}s — L1 ขอ restart-services ผ่าน MQTT", "warn")
        escalation.soft_restart(publish_cmd)

    def _do_l2_ssh(self):
        now = time.time()
        n = self.state.reboots_last_24h(now)
        ok, out = escalation.ssh_recover(reboot=False)     # ลอง restart-services ก่อน
        if ok:
            incident(f"Pi#1 เงียบ ≥ {SSH_ACTION_S}s — L2 ssh restart-services สำเร็จ", "warn")
            return
        if n >= MAX_REBOOTS_PER_24H:
            incident(f"L2 ssh restart ล้มเหลว + reboot ครบโควตา ({n}/{MAX_REBOOTS_PER_24H}) — ข้าม reboot", "error")
            return
        rebooted, out2 = escalation.ssh_recover(reboot=True)
        if rebooted:
            self.state.record_reboot(now)
            incident(f"L2 ssh reboot สั่งแล้ว ({n + 1}/{MAX_REBOOTS_PER_24H} ใน 24 ชม.)", "warn")
        else:
            incident(f"L2 ssh ล้มเหลวทั้ง restart+reboot: {out2[:120]}", "error")

    def _do_l3_power(self):
        now = time.time()
        ok, reason = escalation.may_power_cycle(self.state, now)
        if not ok:
            incident(f"L3 งดตัดไฟ: {reason}", "warn")
            return
        fired = escalation.power_cycle()
        if fired:
            self.state.record_power_cycle(now)
            n = self.state.power_cycles_last_24h(now)
            incident(f"L3 ตัดไฟ Pi#1 แล้ว (HA webhook) — {n}/{MAX_POWER_CYCLES_PER_24H} ใน 24 ชม. — {reason}", "error")
        else:
            self.state.enter_cooldown(now)
            incident(f"L3 สั่งตัดไฟล้มเหลว → cooldown {COOLDOWN_AFTER_FAIL_S // 3600} ชม. (alert-only)", "error")


# ---------------- main loop ----------------
def main():
    state = State()
    sub = Subscriber(state)
    sub.start()
    ladder = Ladder(state)
    mode = "DRY-RUN" if escalation.DRY_RUN else "LIVE"
    incident(f"peer-watchdog เริ่มทำงาน (โหมด {mode}) — เฝ้า {STATION_ID} ทุก {TICK_S}s", "info")

    while True:
        time.sleep(TICK_S)
        now = time.time()
        lh = sub.get_last_health()
        if not lh:
            continue   # ยังไม่เคยได้ heartbeat แรก — อย่าเพิ่งตัดสิน (กันหลัง boot)
        ladder.evaluate(now - lh, now)


if __name__ == "__main__":
    main()
