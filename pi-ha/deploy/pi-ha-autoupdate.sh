#!/bin/bash
# pi-ha-autoupdate.sh — GitOps ฝั่ง Pi#2 (กล่อง HA). ฝาแฝดของ deploy/adsb-autoupdate.sh (Pi#1)
# แต่ดูแลเฉพาะของ Pi#2: pi-ha/ + shared/. ให้ Pi#2 ดึง origin/main เองอัตโนมัติ (systemd timer, root)
# แล้ว sync unit ที่ git pull ไม่อัปเดตให้ (pi-ha/systemd/*.service → /etc/systemd/system)
# + restart peer-watchdog เฉพาะเมื่อโค้ดที่มันใช้ (pi-ha/ หรือ shared/) เปลี่ยนจริง.
#
# ทำไมไม่ใช้ตัวเดียวกับ Pi#1: adsb-autoupdate.sh ผูก user arin/path /home/arin + restart pixoo/
# flight-watcher ที่ Pi#2 ไม่มี. แยกไฟล์ = ไม่เสี่ยงพัง Pi#1 และแต่ละ role อ่านง่าย.
#
# ปลอดภัย: `merge --ff-only` — repo มี local changes/diverged จะข้าม (ไม่ทับของในเครื่อง) + log เตือน.
# git ทำในนามเจ้าของ repo (pi) ผ่าน runuser; ส่วน cp/systemctl ทำเป็น root.
set -uo pipefail

REPO="${PIHA_REPO:-/home/pi/adsb-station}"
RUN_AS="${PIHA_RUN_AS-runuser -u pi --}"   # override เป็น "" ตอนทดสอบ (รัน git ตรงๆ)
gitc() { $RUN_AS git -C "$REPO" "$@"; }
log()  { echo "$(date '+%F %T') $*"; }

gitc fetch --quiet origin main || { log "fetch failed (network?) — ข้ามรอบนี้"; exit 0; }

# ---- reconcile daemon services ทุกรอบ (ก่อนเช็คว่ามี commit ใหม่) ----
# service ใน pi-ha/systemd ที่เป็น daemon จริง (Type=simple, มี [Install]) → ยัง disabled ให้ enable --now.
# ทำทุกรอบเพราะสคริปต์นี้ self-update "รอบหน้า". idempotent. ปิดถาวรใช้ `systemctl mask <s>`.
for f in "$REPO"/pi-ha/systemd/*.service; do
    [ -e "$f" ] || continue
    s="$(basename "$f")"
    grep -q '^\[Install\]' "$f" || continue
    [ "$(systemctl is-enabled "$s" 2>/dev/null || true)" = "disabled" ] &&
        systemctl enable --now "$s" && log "auto-enabled new service $s"
done

BEFORE="$(gitc rev-parse HEAD)"
REMOTE="$(gitc rev-parse origin/main)"
[ "$BEFORE" = "$REMOTE" ] && exit 0                  # ไม่มีอะไรใหม่ = เงียบ

if ! gitc merge --ff-only --quiet origin/main; then
    log "WARN: ff-only ไม่ได้ (repo มี local changes/diverged) — ข้าม auto-update"
    log "      เคลียร์: sudo -u pi git -C $REPO status  (แล้ว stash/checkout ให้สะอาด)"
    exit 0
fi
AFTER="$(gitc rev-parse HEAD)"
log "updated ${BEFORE:0:7} -> ${AFTER:0:7}"
CHANGED="$(gitc diff --name-only "$BEFORE" "$AFTER")"

# ---- self-update สคริปต์นี้ (รันจาก /usr/local/bin) ----
if grep -q '^pi-ha/deploy/pi-ha-autoupdate.sh$' <<<"$CHANGED"; then
    install -m 755 "$REPO/pi-ha/deploy/pi-ha-autoupdate.sh" /usr/local/bin/pi-ha-autoupdate.sh && log "self-updated (ใช้รอบหน้า)"
fi

# ---- sync unit files (รันนอก repo) ----
if grep -q '^pi-ha/systemd/' <<<"$CHANGED"; then
    cp "$REPO"/pi-ha/systemd/*.service /etc/systemd/system/ && systemctl daemon-reload && log "synced pi-ha unit files + daemon-reload"
    # timer (ถ้ามีในอนาคต): enabled → restart · disabled → enable --now (hands-free)
    for f in "$REPO"/pi-ha/systemd/*.timer; do
        [ -e "$f" ] || continue
        t="$(basename "$f")"
        case "$(systemctl is-enabled "$t" 2>/dev/null || true)" in
            enabled)  systemctl restart "$t" ;;
            disabled) systemctl enable --now "$t" && log "auto-enabled new timer $t" ;;
            *)        : ;;
        esac
    done
fi

# ---- restart peer-watchdog เมื่อโค้ดที่มันรัน (จาก repo) เปลี่ยน ----
# peer_watchdog.py import shared/pylib → shared/ เปลี่ยนก็ต้อง restart ด้วย.
if grep -qE '^(pi-ha/peer-watchdog/|shared/)' <<<"$CHANGED"; then
    systemctl restart peer-watchdog && log "restarted peer-watchdog"
fi
exit 0
