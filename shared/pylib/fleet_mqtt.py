"""fleet_mqtt.py — single source of truth: fleet MQTT topics + watchdog timings.

ทั้ง Pi#1 (health-agent) และ Pi#2 (peer-watchdog) import จากไฟล์นี้ → topic/timing ไม่ drift.
(`shared/contracts/mqtt.yaml` เป็น mirror อ่านง่าย — แก้ที่นี่แล้ว sync yaml ให้ตรง). stdlib ล้วน.
"""

# ---- topics ----
STATUS      = "fleet/pi-adsb/status"        # retained, LWT = "online"/"offline"
HEALTH      = "fleet/pi-adsb/health"        # NOT retained, ทุก HEARTBEAT_INTERVAL_S
MAINTENANCE = "fleet/pi-adsb/maintenance"   # retained, JSON {until, reason}
CMD         = "fleet/pi-adsb/cmd"           # peer-watchdog -> Pi#1 (restart-services)
HA_STATUS   = "fleet/pi-ha/status"
DEPLOY_LOG  = "fleet/deploy/log"
INCIDENT    = "fleet/incident"              # ทุก action / refusal ของ watchdog → เห็นได้

# ---- timings (วินาที) — วัดจาก "อายุ heartbeat ล่าสุด" (stale) ----
HEARTBEAT_INTERVAL_S  = 30
HEALTH_STALE_S        = 120     # L0: log
SOFT_RESTART_S        = 180     # L1: MQTT cmd restart-services
SSH_ACTION_S          = 300     # L2: ssh restart → reboot
POWER_CYCLE_S         = 600     # L3: Tuya cut — เฉพาะเมื่อ ping+ssh ตายทั้งคู่
POST_POWER_WAIT_S     = 240
COOLDOWN_AFTER_FAIL_S = 21600   # 6 ชม. แล้ว alert-only

# ---- limits ----
MAX_REBOOTS_PER_24H              = 4
MAX_POWER_CYCLES_PER_24H         = 2
MIN_SECONDS_BETWEEN_POWER_CYCLES = 900   # 15 นาที
