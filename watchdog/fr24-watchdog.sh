#!/bin/bash
# fr24-watchdog v2  —  ADS-B feeder health watchdog
# -----------------------------------------------------------------------------
# WHAT CHANGED FROM v1 (and why v1 stayed silent for ~21h):
#   1. Health = REAL data flow on port 30003 (นับ MSG จริง)
#      NOT `fr24feed-status`. ตอน dongle ค้าง USB, daemon ยัง online →
#      fr24feed-status ผ่าน → v1 ไม่เคยเข้า branch alert = เงียบสนิท.
#   2. Escalation ladder ที่ "ตัดไฟ dongle" ได้จริง (uhubctl) ไม่ใช่แค่ restart
#      process — เพราะ restart service ไม่ reset USB (ตรงกับที่ต้องถอดไฟถึงหาย).
#   3. External heartbeat (healthchecks.io) — ดักได้แม้ Pi ดับทั้งเครื่อง,
#      เพราะ watchdog บนเครื่องเดียวกับที่เฝ้า รายงานตัวเองตายไม่ได้.
#   4. notify() ส่งเสียงดังเสมอ (log ผลทุกครั้ง) ไม่ fail เงียบ.
#
# Ladder ต่อ 1 episode ที่ป่วย:
#   L1  restart decoder + feeder
#   L2  uhubctl power-cycle dongle (แก้เคสค้างระดับ USB) + restart
#   L3  hard alert "ต้องเข้าไปเช็กเอง" (rate-limited รายชั่วโมง)
# -----------------------------------------------------------------------------

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077

# ---- config (override ได้ใน /etc/fr24-watchdog.env) ----
# shellcheck source=/dev/null  # runtime secrets file, not in repo (TG_API/TG_CHAT/HC_URL)
[ -f /etc/fr24-watchdog.env ] && . /etc/fr24-watchdog.env

SBS_HOST="${SBS_HOST:-127.0.0.1}"
SBS_PORT="${SBS_PORT:-30003}"        # dump1090 SBS/BaseStation output
SAMPLE_SECS="${SAMPLE_SECS:-20}"     # ฟังนานเท่าไรต่อ 1 check
MIN_MSGS="${MIN_MSGS:-1}"            # >=1 MSG ในหน้าต่าง = สุขภาพดี (บาร์ต่ำสุด = ดักเฉพาะ 0 จริง)
THRESHOLD="${THRESHOLD:-2}"          # ป่วยติดกันกี่ครั้งถึงลงมือ (2 = ~10 นาที data เป็น 0)
DECODER_SVC="${DECODER_SVC:-dump1090-mutability}"
FEEDER_SVC="${FEEDER_SVC:-fr24feed}"
UHUBCTL_LOC="${UHUBCTL_LOC:-3}"      # จาก `sudo uhubctl`: dongle อยู่ hub 3 port 2
UHUBCTL_PORT="${UHUBCTL_PORT:-2}"
UHUBCTL_DELAY="${UHUBCTL_DELAY:-5}"  # ตัดไฟค้างกี่วินาที
SETTLE_SECS="${SETTLE_SECS:-60}"     # รอหลัง action ก่อนเช็คซ้ำ
HARDALERT_COOLDOWN="${HARDALERT_COOLDOWN:-3600}"

STATE_DIR=/run/fr24-watchdog
mkdir -p "$STATE_DIR"
chmod 755 "$STATE_DIR" 2>/dev/null || true   # dir traversable → pixoo (user arin) อ่าน status.json ได้ (watchdog รัน root+umask077)
FAILS_F="$STATE_DIR/fails"
LASTHARD_F="$STATE_DIR/last_hardalert"
STATUS_F="$STATE_DIR/status.json"    # อ่านโดย pixoo/main.py (feeder page)
HOST="$(hostname)"

# ล่าสุดจาก sample รอบนี้ (count_msgs ตั้งค่าให้) → write_status ใช้ต่อ
LAST_MSGS=0
LAST_AIRCRAFT=0

log() { echo "$(date '+%T') $*"; }   # stdout → journald เห็นใต้ `journalctl -u` เสมอ (logger บางที attribute ไม่ติด unit)

notify() {  # Telegram — ดังเสมอ, log ผลทุกครั้ง
    if [ -n "${TG_API:-}" ]; then
        local resp
        resp="$(curl -s -m 15 "$TG_API" -d chat_id="$TG_CHAT" --data-urlencode text="$1" 2>&1)"
        case "$resp" in
            *'"ok":true'*) log "telegram OK" ;;
            *)             log "telegram FAILED: $resp" ;;
        esac
    else
        log "telegram SKIPPED (TG_API not set)"
    fi
}

hc_ping() {  # $1 = "" success | "/fail"  (healthchecks.io external heartbeat)
    [ -n "${HC_URL:-}" ] && curl -fsS -m 10 --retry 2 "${HC_URL}${1:-}" -o /dev/null 2>/dev/null
}

count_msgs() {  # 1 sample → set LAST_MSGS/LAST_AIRCRAFT, echo จำนวน MSG (contract เดิม)
    # capture ทั้งหน้าต่างก่อน (timeout ปิด nc แล้วค่อย process) — ไม่โดน buffering-loss
    # แบบ live pipe เพราะ command substitution รอ output ครบก่อน
    local sample
    sample="$(timeout "$SAMPLE_SECS" nc "$SBS_HOST" "$SBS_PORT" 2>/dev/null)"
    LAST_MSGS="$(printf '%s\n' "$sample" | grep -c '^MSG' || true)"
    # distinct aircraft = ICAO hex ไม่ซ้ำ (SBS field 5 = f[4] ตาม flight_watcher.py)
    LAST_AIRCRAFT="$(printf '%s\n' "$sample" | awk -F, '$1=="MSG" && $5!="" {print $5}' | sort -u | grep -c . || true)"
    echo "$LAST_MSGS"
}

write_status() {  # $1 = health (ok|recovering|dead) — atomic write ให้ pixoo อ่าน
    local rate=$(( LAST_MSGS / SAMPLE_SECS ))
    printf '{"ts":%s,"health":"%s","msg_per_s":%s,"aircraft":%s}\n' \
        "$(date +%s)" "$1" "$rate" "${LAST_AIRCRAFT:-0}" \
        > "$STATUS_F.tmp" && mv -f "$STATUS_F.tmp" "$STATUS_F"
    chmod 644 "$STATUS_F" 2>/dev/null || true   # world-readable (ไม่ใช่ความลับ) → pixoo อ่านได้
}

is_healthy() {
    local n; n="$(count_msgs)"; n="${n:-0}"
    log "data-flow: $n msgs/${SAMPLE_SECS}s (need >=$MIN_MSGS)"
    [ "$n" -ge "$MIN_MSGS" ]
}

restart_stack() {
    log "restart $DECODER_SVC + $FEEDER_SVC"
    systemctl restart "$DECODER_SVC" 2>/dev/null || log "WARN: restart $DECODER_SVC failed"
    sleep 3
    systemctl restart "$FEEDER_SVC"  2>/dev/null || log "WARN: restart $FEEDER_SVC failed"
}

usb_powercycle() {
    log "uhubctl cycle dongle (loc $UHUBCTL_LOC port $UHUBCTL_PORT, ${UHUBCTL_DELAY}s)"
    uhubctl -l "$UHUBCTL_LOC" -p "$UHUBCTL_PORT" -a cycle -d "$UHUBCTL_DELAY" 2>&1
    sleep 5   # ให้ USB re-enumerate
}

# ============================ main ============================
if is_healthy; then
    echo 0 > "$FAILS_F"
    write_status "ok"
    hc_ping ""                # heartbeat: ยังมีชีวิต + feed อยู่
    exit 0
fi

fails=$(( $(cat "$FAILS_F" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$FAILS_F"
log "UNHEALTHY #$fails / threshold $THRESHOLD"
write_status "recovering"     # feed ตก แต่ยังไม่ถึงเกณฑ์ลงมือ → เหลือง
hc_ping "/fail"

[ "$fails" -lt "$THRESHOLD" ] && exit 0   # รอยืนยันก่อนลงมือ (กัน false positive ตอนฟ้าว่าง)

# ---- L1: restart software ----
notify "⚠️ FR24 watchdog ($HOST)
ไม่มีข้อมูลเข้า ${SAMPLE_SECS}s ต่อเนื่อง — กู้อัตโนมัติ L1 (restart)
$(date '+%F %T')"
restart_stack
sleep "$SETTLE_SECS"
if is_healthy; then
    echo 0 > "$FAILS_F"
    write_status "ok"
    notify "✅ FR24 watchdog ($HOST) — L1 restart สำเร็จ feed กลับมาแล้ว
$(date '+%F %T')"
    hc_ping ""; exit 0
fi

# ---- L2: USB power-cycle (แก้เคสค้างระดับ USB) ----
notify "⚠️ FR24 watchdog ($HOST)
L1 ไม่หาย — power-cycle dongle ผ่าน USB (L2)
$(date '+%F %T')"
usb_powercycle
restart_stack
sleep "$SETTLE_SECS"
if is_healthy; then
    echo 0 > "$FAILS_F"
    write_status "ok"
    notify "✅ FR24 watchdog ($HOST) — L2 USB power-cycle สำเร็จ feed กลับมาแล้ว
$(date '+%F %T')"
    hc_ping ""; exit 0
fi

# ---- L3: ยังตาย — hard alert (rate-limited) ----
write_status "dead"           # L1+L2 กู้ไม่ขึ้น → แดง
now=$(date +%s)
last=$(cat "$LASTHARD_F" 2>/dev/null || echo 0)
if [ $(( now - last )) -ge "$HARDALERT_COOLDOWN" ]; then
    echo "$now" > "$LASTHARD_F"
    notify "❌ FR24 watchdog ($HOST)
L1+L2 กู้ไม่สำเร็จ — ต้องเข้าไปเช็กเอง (dongle/สาย/ฮาร์ดแวร์)
$(date '+%F %T')"
fi
hc_ping "/fail"
exit 1
