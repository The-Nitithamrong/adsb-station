#!/bin/bash
# adsb-system-update.sh — อัปเดตแพ็กเกจ OS รายสัปดาห์แบบ "เบา + ปลอดภัย" + สรุปเข้า Telegram.
#
# ทำไมมีไฟล์นี้ (บันทึกจากเหตุจริง 2026-08-09):
#   เดิม auto-update.sh (hand-install บน Pi) รัน `apt full-upgrade` ทุกอาทิตย์ตี 4 โดยไม่มีใครเฝ้า.
#   heartbeat "กล่องดำ" จับได้ว่า Pi#1 รีบูตช่วง 04:07-04:10 — 13 วิ หลัง auto-update.service เริ่ม
#   (04:06:44) ทั้งที่ temp เย็น/load ว่าง/ไฟปกติ → พีค I/O+CPU ตอน apt ไปสะกิด hang/brownout
#   ในหน้าต่างเงียบ (journal boot -1 หายเกลี้ยง = หยุดแบบไม่ clean). HW watchdog (1 นาที) รีเซ็ตให้ฟื้น.
#
# เวอร์ชันนี้ลดความเสี่ยง 4 ชั้น:
#   1) เวลากลางวัน (timer = Sun 14:00) ที่คนตื่นเฝ้าได้ + HW/peer-watchdog มีตาดู ไม่ใช่ตี 4 เงียบ ๆ
#   2) priority ต่ำสุด: nice + ionice idle (+ Nice/IOSchedulingClass/CPUWeight ใน .service) → พีคแบนลง กัน brownout
#   3) `upgrade` (ไม่ใช่ `full-upgrade`) — ไม่ปรับโครงสร้าง dependency/ลบแพ็กเกจเอง; งานเบากว่า
#      (อยากได้ full-upgrade กลับ เปลี่ยน UPGRADE_CMD ด้านล่าง). ไม่ auto-reboot — แค่แจ้งถ้ามี kernel/libc.
#   4) flock กันรันซ้อน, Acquire::Retries กันเน็ตกระตุก, log มีเพดานขนาด, TimeoutStartSec กัน apt ค้างยาว.
#
# deploy ผ่าน git: adsb-autoupdate sync ไฟล์นี้ → /usr/local/bin, unit → /etc/systemd/system, enable timer เอง.
# secrets (TG_API/TG_CHAT) ใน /etc/fr24-watchdog.env. รันเป็น root (apt ต้อง root) โดย systemd timer.
#
# แทนที่ตัวเดิมบน Pi (ทำครั้งเดียวหลัง deploy):
#   sudo systemctl disable --now auto-update.timer
#   sudo rm -f /etc/systemd/system/auto-update.{timer,service} /usr/local/bin/auto-update.sh
#   sudo systemctl daemon-reload
set -uo pipefail

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive
# shellcheck source=/dev/null
[ -f /etc/fr24-watchdog.env ] && . /etc/fr24-watchdog.env

HOST="$(hostname)"
LOG=/var/log/adsb-system-update.log
LOCK=/run/adsb-system-update.lock
UPGRADE_CMD=upgrade                       # เปลี่ยนเป็น full-upgrade ถ้าต้องการ dist-upgrade เต็ม
# priority ต่ำสุดทั้ง CPU (nice) และ I/O (ionice idle) → พีคแบน กัน brownout ตอน apt โหลด/เขียนดิสก์
APT=(nice -n 19 ionice -c3 apt-get -o Acquire::Retries=3)

notify() {
    [ -n "${TG_API:-}" ] || return 0
    curl -s -m 15 "$TG_API" -d chat_id="${TG_CHAT:-}" --data-urlencode text="$1" >/dev/null 2>&1
}

# กันรันซ้อน (timer เผลอยิงซ้ำ / รันมือทับ) — จับ lock ไม่ได้ = มีตัวรันอยู่แล้ว → ออกเงียบ
exec 9>"$LOCK"
flock -n 9 || { echo "$(date '+%F %T') มีตัวอื่นรันอยู่ — ข้าม"; exit 0; }

# เพดาน log 1MB (เดิมโตไม่จำกัด) — เกินก็เก็บครึ่งท้ายพอ
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -c 524288 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

"${APT[@]}" update >>"$LOG" 2>&1

# แพ็กเกจที่จะอัปเกรด (simulate) — ใช้ตัดสินใจ reboot + สรุปข้อความ
pkgs="$("${APT[@]}" -s "$UPGRADE_CMD" 2>/dev/null | awk '/^Inst/{print $2}' | tr '\n' ' ')"
read -ra pkg_arr <<<"$pkgs"
count=${#pkg_arr[@]}

{
    echo "===== $(date '+%F %T') start ($UPGRADE_CMD, upgradable: $count) ====="
    "${APT[@]}" -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" "$UPGRADE_CMD"
    rc=$?
    "${APT[@]}" -y autoremove
    "${APT[@]}" clean
    echo "===== end (rc=$rc) ====="
} >>"$LOG" 2>&1

# ต้อง reboot ไหม (kernel/bootloader/libc เปลี่ยน หรือมี /run/reboot-required)
reboot_needed=no
if [ -f /run/reboot-required ] || grep -qiE 'linux-image|raspberrypi-kernel|raspberrypi-bootloader|linux-headers|libc6' <<<"$pkgs"; then
    reboot_needed=yes
fi

# ตัดรายการไม่ให้ข้อความยาวเกิน
shown="$pkgs"
if [ "$count" -gt 25 ]; then
    shown="$(tr ' ' '\n' <<<"$pkgs" | head -25 | tr '\n' ' ')... (+$((count - 25)) อื่น ๆ)"
fi

if [ "$count" -eq 0 ]; then
    msg="🟢 System-update ($HOST) $(date '+%F %T')
ไม่มีอะไรต้องอัปเดต ระบบล่าสุดแล้ว"
else
    msg="🔄 System-update ($HOST) $(date '+%F %T')
อัปเดต $count แพ็กเกจ ($UPGRADE_CMD):
$shown"
fi
if [ "$reboot_needed" = yes ]; then
    msg="$msg

⚠️ มีอัปเดตที่ควร reboot (kernel/libc) — เครื่องยังไม่ reboot ให้ สั่ง 'sudo reboot' เองตอนสะดวก"
fi

notify "$msg"
logger -t adsb-system-update "done: $count pkg(s), cmd=$UPGRADE_CMD, reboot_needed=$reboot_needed"
