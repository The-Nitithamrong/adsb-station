#!/bin/bash
# adsb-autoupdate.sh — ให้ Pi ดึง origin/main เองอัตโนมัติ (systemd timer, root)
# แล้ว sync ไฟล์ที่ git pull ไม่อัปเดตให้ (watchdog → /usr/local/bin, unit → /etc/systemd/system)
# + restart เฉพาะ service ที่ไฟล์เปลี่ยนจริง.
#
# ปลอดภัย: ใช้ `merge --ff-only` — ถ้า repo มี local changes/diverged จะข้าม (ไม่ทับของในเครื่อง)
# แล้ว log เตือนไว้ใน journal (journalctl -u adsb-autoupdate).
# git ทำในนามเจ้าของ repo (arin) ผ่าน runuser; ส่วน cp/systemctl ทำเป็น root.
set -uo pipefail

REPO="${ADSB_REPO:-/home/arin/adsb-station}"
RUN_AS="${ADSB_RUN_AS-runuser -u arin --}"   # override เป็น "" ตอนทดสอบ (รัน git ตรงๆ)
gitc() { $RUN_AS git -C "$REPO" "$@"; }
log()  { echo "$(date '+%F %T') $*"; }

gitc fetch --quiet origin main || { log "fetch failed (network?) — ข้ามรอบนี้"; exit 0; }
BEFORE="$(gitc rev-parse HEAD)"
REMOTE="$(gitc rev-parse origin/main)"
[ "$BEFORE" = "$REMOTE" ] && exit 0                  # ไม่มีอะไรใหม่ = เงียบ

if ! gitc merge --ff-only --quiet origin/main; then
    log "WARN: ff-only ไม่ได้ (repo มี local changes/diverged) — ข้าม auto-update"
    log "      เคลียร์: sudo -u arin git -C $REPO status  (แล้ว stash/checkout ให้สะอาด)"
    exit 0
fi
AFTER="$(gitc rev-parse HEAD)"
log "updated ${BEFORE:0:7} -> ${AFTER:0:7}"
CHANGED="$(gitc diff --name-only "$BEFORE" "$AFTER")"

# ---- sync ไฟล์ที่รันนอก repo ----
if grep -q '^watchdog/fr24-watchdog.sh$' <<<"$CHANGED"; then
    install -m 755 "$REPO/watchdog/fr24-watchdog.sh" /usr/local/bin/fr24-watchdog.sh && log "synced watchdog script"
    systemctl start fr24-watchdog.service && log "ran fr24-watchdog (new script)"
fi
if grep -q '^deploy/adsb-autoupdate.sh$' <<<"$CHANGED"; then
    install -m 755 "$REPO/deploy/adsb-autoupdate.sh" /usr/local/bin/adsb-autoupdate.sh && log "self-updated (ใช้รอบหน้า)"
fi
if grep -q '^systemd/' <<<"$CHANGED"; then
    cp "$REPO"/systemd/*.service "$REPO"/systemd/*.timer /etc/systemd/system/ && systemctl daemon-reload && log "synced unit files + daemon-reload"
    for t in fr24-watchdog.timer adsb-autoupdate.timer adsb-outbox.timer adsb-ha-mqtt.timer; do
        systemctl is-enabled "$t" >/dev/null 2>&1 && systemctl restart "$t"
    done
fi

# ---- restart service ที่รันจาก repo ----
grep -q '^flightwatch/' <<<"$CHANGED" && systemctl restart flight-watcher && log "restarted flight-watcher"
grep -q '^pixoo/'       <<<"$CHANGED" && systemctl restart pixoo          && log "restarted pixoo"
exit 0
