#!/bin/bash
# enable-hw-watchdog.sh — เปิด hardware watchdog ของ Pi 5 (bcm2712/bcm2835) ผ่าน systemd.
#
# ทำไม: fr24-watchdog เป็น software watchdog ที่รันบน Pi ตัวเดียวกัน — ถ้า "ทั้ง Pi แฮงค์"
# (kernel lockup / SD I/O stall / soft hang) มันตายไปด้วย กู้ dongle ไม่ได้เลย.
# hardware watchdog เป็นตัวจับเวลาในชิป: systemd ต้อง "ป้อน" มันเรื่อยๆ — ถ้า systemd/kernel
# แฮงค์เกิน RUNTIME วินาที ฮาร์ดแวร์จะ reset Pi เอง → self-heal จาก full hang (roadmap #6).
# (ไม่ช่วยกรณีไฟดับสนิท — นั่นต้องแก้ที่ power supply.)
#
# รันครั้งเดียว:  sudo bash deploy/enable-hw-watchdog.sh   (idempotent — รันซ้ำได้)
set -euo pipefail

CONF=/etc/systemd/system.conf
RUNTIME=15          # systemd ป้อน watchdog ทุก RUNTIME/2 วิ; แฮงค์เกิน RUNTIME → reset (bcm สูงสุด ~15s)
REBOOT="2min"       # ถ้า reboot/shutdown ค้างเกินนี้ → บังคับ reset

[ -e /dev/watchdog ] || {
    echo "ไม่พบ /dev/watchdog — Pi 5 ควรมี bcm watchdog ในตัว (เช็ค: dmesg | grep -i watchdog)"; exit 1; }

# uncomment + ตั้งค่า (ค่า default ในไฟล์เป็น '#RuntimeWatchdogSec=off')
sed -i \
    -e "s/^#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=${RUNTIME}/" \
    -e "s/^#\?RebootWatchdogSec=.*/RebootWatchdogSec=${REBOOT}/" \
    "$CONF"
# กันกรณีไม่มีบรรทัดเลย → เพิ่มใต้ [Manager]
grep -q '^RuntimeWatchdogSec=' "$CONF" || sed -i "/^\[Manager\]/a RuntimeWatchdogSec=${RUNTIME}" "$CONF"
grep -q '^RebootWatchdogSec='  "$CONF" || sed -i "/^\[Manager\]/a RebootWatchdogSec=${REBOOT}" "$CONF"

systemctl daemon-reexec        # โหลด system.conf ใหม่ (daemon-reload ไม่พอ — ต้อง re-exec PID 1)

echo "--- ตรวจสอบ (RuntimeWatchdogUSec ควร > 0) ---"
systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec
journalctl -b 0 --no-pager | grep -i 'hardware watchdog' | tail -3 || true
echo "เสร็จ: Pi จะ reset เองถ้า kernel/systemd แฮงค์เกิน ${RUNTIME}s (ทดสอบจริงห้ามใช้ในเวลาทำงาน:"
echo "  echo c | sudo tee /proc/sysrq-trigger  → kernel panic → ควร reboot เองใน ~${RUNTIME}s)"
