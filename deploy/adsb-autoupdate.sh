#!/bin/bash
# adsb-autoupdate.sh — ให้ Pi ดึง origin/main เองอัตโนมัติ (systemd timer, root)
# แล้ว sync ไฟล์ที่ git pull ไม่อัปเดตให้ (watchdog → /usr/local/bin, unit → /etc/systemd/system)
# + restart เฉพาะ service ที่ไฟล์เปลี่ยนจริง.
# + auto-enable timer ใหม่ (disabled → enable) และ daemon service ใหม่ (Type=simple ไม่มี timer คู่)
#   → deploy hands-free ไม่ต้อง SSH ไป enable เอง. ปิดถาวรใช้ `systemctl mask <unit>`.
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

# ---- reconcile daemon services ทุกรอบ (ก่อนเช็คว่ามี commit ใหม่) ----
# service ที่เป็น daemon จริง (Type=simple, ไม่มี .timer คู่, มี [Install]) → ถ้ายัง disabled ให้ enable --now.
# ทำทุกรอบ (ไม่ผูกกับ systemd/ diff) เพราะสคริปต์นี้ self-update "รอบหน้า" — รอบที่ logic นี้เริ่มมีผล
# อาจไม่มี diff แล้ว. idempotent (enabled อยู่แล้ว = ข้าม). อยากปิดตัวไหนถาวรใช้ `systemctl mask <s>`.
for f in "$REPO"/systemd/*.service; do
    s="$(basename "$f")"; b="${s%.service}"
    [ -f "$REPO/systemd/$b.timer" ] && continue          # มี timer คู่ = oneshot ที่ timer สั่ง → ไม่ enable ตรง
    grep -q '^\[Install\]' "$f" || continue              # ไม่มี [Install] = enable ไม่ได้
    [ "$(systemctl is-enabled "$s" 2>/dev/null || true)" = "disabled" ] &&
        systemctl enable --now "$s" && log "auto-enabled new service $s"
done

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
if grep -q '^deploy/fleet-cmd$' <<<"$CHANGED"; then
    install -m 755 "$REPO/deploy/fleet-cmd" /usr/local/bin/fleet-cmd && log "synced fleet-cmd"
fi
if grep -q '^deploy/adsb-system-update.sh$' <<<"$CHANGED"; then
    install -m 755 "$REPO/deploy/adsb-system-update.sh" /usr/local/bin/adsb-system-update.sh && log "synced adsb-system-update"
fi
if grep -q '^systemd/' <<<"$CHANGED"; then
    cp "$REPO"/systemd/*.service "$REPO"/systemd/*.timer /etc/systemd/system/ && systemctl daemon-reload && log "synced unit files + daemon-reload"
    # ทุก timer ใน repo: enabled → restart รับ unit ใหม่ · disabled (รวม timer ใหม่) → enable --now ให้เอง
    # (hands-free deploy — ไม่ต้อง SSH ไปเปิด timer ใหม่). อยากปิดตัวไหนถาวรใช้ `systemctl mask <t>` → ข้าม.
    for f in "$REPO"/systemd/*.timer; do
        t="$(basename "$f")"
        case "$(systemctl is-enabled "$t" 2>/dev/null || true)" in
            enabled)  systemctl restart "$t" ;;
            disabled) systemctl enable --now "$t" && log "auto-enabled new timer $t" ;;
            *)        : ;;   # masked/static/unknown → ข้าม
        esac
    done
fi

# ---- restart service ที่รันจาก repo ----
grep -q '^flightwatch/'             <<<"$CHANGED" && systemctl restart flight-watcher && log "restarted flight-watcher"
grep -q '^pixoo/'                   <<<"$CHANGED" && systemctl restart pixoo          && log "restarted pixoo"
grep -q '^deploy/uptime_server.py$' <<<"$CHANGED" && systemctl restart adsb-uptime    && log "restarted adsb-uptime"
grep -q '^report/inbound_push.py$'  <<<"$CHANGED" && systemctl restart adsb-inbound-push && log "restarted adsb-inbound-push"
grep -q '^report/eta_push.py$'      <<<"$CHANGED" && systemctl restart adsb-eta-push     && log "restarted adsb-eta-push"
# health-agent รันจาก repo (ha/health_agent.py) + import shared/pylib → เปลี่ยนอย่างใดต้อง restart
grep -qE '^(ha/health_agent\.py|shared/)' <<<"$CHANGED" && systemctl restart adsb-health-agent && log "restarted adsb-health-agent"
exit 0
