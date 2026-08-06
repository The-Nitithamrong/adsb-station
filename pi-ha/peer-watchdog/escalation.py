"""escalation.py — actions + hard guards ของ peer-watchdog (รันบน Pi#2).

ปรัชญา: **ยิ่ง action แรงยิ่งต้องมั่นใจ**. ก่อน power-cycle (L3, ตัดไฟจริง) ต้องผ่าน guard ครบ —
ถ้า Pi#1 ยัง ping ได้ หรือ ssh เข้าได้ แปลว่าปัญหาเป็น "software" ตัดไฟมีแต่ทำให้แย่ลง
(กำลัง write ค้าง → SD/FS พัง). ตัดไฟเฉพาะตอน "เงียบสนิททุกช่องทาง" เท่านั้น.

DRY_RUN=1 (ดีฟอลต์) = log เจตนาอย่างเดียว ไม่ทำจริง — ตัวที่ห้ามข้ามตอนติดตั้งครั้งแรก.
stdlib ล้วน (subprocess/socket/urllib) — รันได้ทุกที่.
"""
import os
import socket
import subprocess

import tuya_power

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

PI1_IP   = os.environ.get("PI1_IP", "192.168.41.241")
SSH_USER = os.environ.get("SSH_USER", "arin")
SSH_KEY  = os.environ.get("SSH_KEY", "/home/arin/.ssh/fleet_id")
SSH_PORT = int(os.environ.get("SSH_PORT", "22"))


def _dry(tag, msg):
    print(f"[{'DRY-RUN' if DRY_RUN else 'LIVE'}] {tag}: {msg}")


# ---------- probes (อ่านสถานะ Pi#1 แบบ out-of-band, ไม่พึ่ง MQTT) ----------
def ping_ok(host=PI1_IP, timeout=2):
    """Pi#1 ตอบ network ไหม. True = ยังมีชีวิตระดับ IP → ปัญหาน่าจะ software."""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 2,
        )
        return r.returncode == 0
    except Exception:
        return False


def tcp_open(host=PI1_IP, port=SSH_PORT, timeout=3):
    """port 22 เปิดไหม (ssh daemon ยังรับ). True = kernel/userspace ยังตอบ."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ssh(cmd, timeout=25):
    """ยิง forced-command ผ่าน ssh key. Pi#1 authorized_keys ผูก command="fleet-cmd"
    รับแค่ restart-services / reboot / status → key รั่วก็สั่งอย่างอื่นไม่ได้."""
    argv = [
        "ssh", "-i", SSH_KEY, "-p", str(SSH_PORT),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={timeout}",
        f"{SSH_USER}@{PI1_IP}", cmd,
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 5)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ---------- L3 guard: อนุญาต power-cycle ไหม ----------
def may_power_cycle(state, now):
    """คืน (ok: bool, reason: str). ต้องผ่านทุกข้อถึงตัดไฟได้:
      1) ไม่อยู่ใน maintenance
      2) ไม่ติด cooldown (เพิ่งล้มเหลว → alert-only 6 ชม.)
      3) ping ตาย  (ถ้ายัง ping ได้ = software, ห้ามตัด)
      4) ssh/tcp:22 ตาย (ถ้ายังเข้าได้ = ควรใช้ ssh reboot ไม่ใช่ตัดไฟ)
      5) ยังไม่เกินโควตา 24 ชม. + เว้นระยะขั้นต่ำจากครั้งก่อน
    """
    from fleet_mqtt import (MAX_POWER_CYCLES_PER_24H,
                            MIN_SECONDS_BETWEEN_POWER_CYCLES)

    if state.maintenance_active(now):
        return False, "maintenance flag ยัง active (self-expiry ยังไม่ถึงเวลา)"
    if now < state.cooldown_until:
        return False, f"อยู่ใน cooldown ถึง {int(state.cooldown_until - now)}s ข้างหน้า (alert-only)"
    if ping_ok():
        return False, "Pi#1 ยัง ping ได้ → ปัญหาเป็น software, ตัดไฟมีแต่ทำให้แย่ลง"
    if tcp_open():
        return False, "ssh:22 ยังเปิด → ใช้ ssh reboot แทน, ไม่ตัดไฟ"
    n = state.power_cycles_last_24h(now)
    if n >= MAX_POWER_CYCLES_PER_24H:
        return False, f"ตัดไฟครบโควตาแล้ว {n}/{MAX_POWER_CYCLES_PER_24H} ใน 24 ชม."
    if state.last_power_cycle and (now - state.last_power_cycle) < MIN_SECONDS_BETWEEN_POWER_CYCLES:
        return False, "เพิ่งตัดไฟไปยังไม่ถึงระยะเว้นขั้นต่ำ"
    return True, "ping+ssh ตายทั้งคู่, ผ่าน guard ครบ"


# ---------- actions ----------
def soft_restart(publish_cmd):
    """L1: ขอ Pi#1 restart services ตัวเองผ่าน MQTT cmd (เบาสุด, ไม่แตะ OS)."""
    if DRY_RUN:
        _dry("L1 soft_restart", "จะ publish cmd=restart-services ไป fleet/pi-adsb/cmd")
        return True
    publish_cmd("restart-services")
    return True


def ssh_recover(reboot=False):
    """L2: ssh เข้า Pi#1 สั่ง restart-services ก่อน; ถ้า reboot=True สั่ง reboot (แรงกว่า).
    คืน (ok, output)."""
    action = "reboot" if reboot else "restart-services"
    if DRY_RUN:
        _dry("L2 ssh_recover", f"จะ ssh สั่ง '{action}' ที่ {SSH_USER}@{PI1_IP}")
        return True, "(dry-run)"
    try:
        rc, out = _ssh(action)
        return rc == 0, out.strip()
    except Exception as e:
        return False, f"ssh error: {e}"


def power_cycle():
    """L3: ตัดไฟ Pi#1 ผ่าน HA webhook (HA ทำ off→delay→on). คืน True ถ้ายิงสำเร็จ.
    ⚠️ ต้องผ่าน may_power_cycle() มาก่อนเท่านั้น."""
    if DRY_RUN:
        _dry("L3 power_cycle", "จะสั่ง tuya_power.cycle() (HA webhook off→delay→on)")
        return True
    return tuya_power.cycle()
