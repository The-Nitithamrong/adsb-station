# health_agent (Pi#1)

Pi#1 (สถานี ADS-B) ส่ง **ชีพจร** เข้า fleet MQTT ให้ Pi#2 `peer-watchdog` เฝ้า. เป็นคู่หูของ
`pi-ha/peer-watchdog` — topic/timing มาจาก `shared/pylib/fleet_mqtt.py` ตัวเดียวกัน (ไม่ drift).

## ทำอะไร
1. **heartbeat** ทุก 30s → `fleet/pi-adsb/health` (ไม่ retained). payload = สุขภาพจริง:
   - `fr24_feed_ok` — จาก `/run/fr24-watchdog/status.json` (`health=="ok"` + ไม่ค้างเก่า) = **data-flow
     จริงบน 30003** ไม่ใช่แค่ process alive (flag ที่โกหกตอน dongle แฮงก์คือบั๊ก 21 ชม.เดิม)
   - `pixoo_ok`, `flight_watcher_ok` — `systemctl is-active`
   - `uptime_s` — แยก hang จริง (uptime รีเซ็ต) ออกจากเน็ตหลุด (uptime เดินต่อ)
   - `commit` — git short SHA ที่ deploy อยู่
2. **LWT** — ถือ connection ค้างด้วย `mosquitto_sub` ที่ตั้ง will=`offline`(retained) บน
   `fleet/pi-adsb/status`. connection ตาย (Pi แฮงก์/agent ถูกฆ่า) → broker ยิง `offline` ให้เอง.
   ตอน start ยิง `online`(retained). ปิดสุภาพ (SIGTERM) → ยิง `offline` เอง.
3. **รับ cmd** — subscribe `fleet/pi-adsb/cmd`; ได้ `restart-services` (จาก peer-watchdog L1) →
   เรียก `fleet-cmd restart-services`.

> ต่างจาก `mqtt_publish.py`: ตัวนั้นคุย broker ของ **Home Assistant** (discovery/state). ตัวนี้อยู่
> **fleet plane** คุย broker บน **Pi#2** (`BROKER_HOST`). คนละหน้าที่ อยู่ด้วยกันได้.

## fleet-cmd — คำสั่งที่ peer-watchdog สั่ง Pi#1 ได้ (จำกัด 3 อย่าง)
`deploy/fleet-cmd` เป็นจุดเดียวที่นิยาม `restart-services` / `reboot` / `status`. ใช้ทั้ง
MQTT L1 (health_agent เรียก) และ SSH L2 (forced-command). restart/reboot ผ่าน `sudo` (NOPASSWD
เฉพาะ 2 บรรทัด — `deploy/fleet-cmd.sudoers`).

## ติดตั้ง (Pi#1)
health-agent deploy ผ่าน git เหมือน service อื่นของ Pi#1 — `adsb-autoupdate` จะ:
- auto-enable `adsb-health-agent.service` (daemon ใหม่) ให้เอง
- sync `fleet-cmd` → `/usr/local/bin/`
- restart health-agent เมื่อ `ha/health_agent.py` หรือ `shared/` เปลี่ยน

เหลือ **2 อย่างที่ต้องทำมือ** (secret + sudoers — git ไม่แตะ):

```bash
cd /home/arin/adsb-station          # (git pull ให้มีไฟล์ก่อน ถ้ายังไม่มี)

# 1) sudoers ให้ arin restart/reboot ได้แบบ NOPASSWD
sudo install -m 440 deploy/fleet-cmd.sudoers /etc/sudoers.d/fleet-cmd
sudo visudo -cf /etc/sudoers.d/fleet-cmd     # ต้องขึ้น "parsed OK"

# 2) ชี้ broker Pi#2 ใน /etc/fr24-watchdog.env (เพิ่มบรรทัด — IP ของ ArinII)
sudo tee -a /etc/fr24-watchdog.env >/dev/null <<'ENV'
BROKER_HOST=192.168.41.XXX   # IP ของ Pi#2 (ArinII)
BROKER_PORT=1883
BROKER_USER=fleet
BROKER_PASS=<รหัสที่ตั้งด้วย mosquitto_passwd บน Pi#2>
ENV

sudo systemctl restart adsb-health-agent     # (ถ้า autoupdate ยังไม่ enable ให้: enable --now)
journalctl -u adsb-health-agent -f
```

## เปิด broker Pi#2 ให้ Pi#1 ต่อได้ (ทำครั้งเดียวบน Pi#2)
mosquitto default ฟังแค่ localhost → Pi#1 ต่อข้ามเครื่องไม่ได้. บน **Pi#2**:

```bash
cd /home/arin/adsb-station
sudo cp pi-ha/mosquitto/fleet.conf /etc/mosquitto/conf.d/fleet.conf
sudo mosquitto_passwd -c /etc/mosquitto/fleet.passwd fleet     # ตั้งรหัส user "fleet"
sudo systemctl restart mosquitto
```

แล้วใส่ `BROKER_USER=fleet` + `BROKER_PASS=...` ตัวเดียวกันใน env ของ **ทั้ง** Pi#1 health-agent และ
Pi#2 peer-watchdog (`/etc/fleet-peer-watchdog.env`).

## ตรวจว่าเดินครบวง
บน Pi#2:
```bash
mosquitto_sub -h 127.0.0.1 -u fleet -P <pass> -t 'fleet/#' -v
# ควรเห็น fleet/pi-adsb/status = online, แล้ว fleet/pi-adsb/health {...} ทุก 30s
```
peer-watchdog จะเลิก "รอ heartbeat แรก" แล้วขึ้น HEALTHY. ลองหยุด health-agent บน Pi#1
(`systemctl stop adsb-health-agent`) → status = offline + heartbeat หยุด → (DRY-RUN) peer-watchdog
ไต่ L0→L3 ใน log โดยไม่ทำจริง.
