# Pi#2 (ArinII) provisioning — reflash / ย้าย storage (USB → SD)

Checklist ตั้งค่า Pi#2 ใหม่ทั้งเครื่องหลัง flash SD (ไม่ใช่ clone — ต้อง reprovision).
Pi#2 = `arin@ArinII` @ **192.168.41.207** รัน 4 อย่าง: **fleet Mosquitto · Home Assistant (Docker) ·
peer-watchdog · timetable kiosk** + GitOps (`pi-ha-autoupdate`).

git มีแค่ **โค้ด** — secrets / HA config / mosquitto password **ไม่อยู่ใน git**. ต้อง back up เอง.

---

## 0. ⚠️ ก่อนถอด USB drive — BACK UP state ที่ไม่อยู่ใน git (ทำก่อน! หายแล้วสร้างใหม่ยาก)

รันบน Pi#2 (ตอนยังบูตจาก USB อยู่) → tar เดียวเก็บทุกอย่าง (ไฟล์ไหนไม่มีก็ข้าม, `|| true` กันพัง):
```bash
sudo tar czf /home/arin/pi2-backup-$(date +%F).tgz \
  /home/arin/adsb-station/deploy/homeassistant/config \
  /etc/mosquitto/fleet.passwd \
  /etc/fleet-peer-watchdog.env \
  /etc/adsb-timetable.env \
  /home/arin/.ssh/fleet_id /home/arin/.ssh/fleet_id.pub \
  /etc/fr24-watchdog.env 2>/dev/null || true
# เช็คได้ครบ แล้วก๊อปออกไปเครื่องอื่นก่อนถอดไดรฟ์:
tar tzf /home/arin/pi2-backup-*.tgz
# scp /home/arin/pi2-backup-*.tgz you@laptop:~/   (หรือก๊อปใส่ USB อีกอัน)
```
(คำอธิบายว่าแต่ละไฟล์คืออะไร ดูตารางล่าง)
> คืนทีหลังด้วย `sudo tar xzf pi2-backup-*.tgz -C /` (path เต็มในไฟล์ → กลับที่เดิมเป๊ะ)

**สิ่งที่ต้อง back up (สรุป):**
| อะไร | ที่อยู่ | ทำไม |
|---|---|---|
| HA config | `deploy/homeassistant/config/` | automations (power-cycle + fan), MQTT integration, Tuya, ปลั๊ก `switch.pi_socket_1`, entity registry |
| mosquitto passwd | `/etc/mosquitto/fleet.passwd` | user `fleet`+`adsb` — ไม่งั้น Pi#1/peer-watchdog ต่อ broker ไม่ได้ |
| env files | `/etc/{fleet-peer-watchdog,adsb-timetable,fr24-watchdog}.env` | secrets ทั้งหมด |
| SSH key | `/home/arin/.ssh/fleet_id`(+`.pub`) | L2 SSH เข้า Pi#1 |

---

## 1. Flash + first boot
- **Raspberry Pi Imager** → RPi OS 64-bit → ตั้งใน Imager: hostname `ArinII`, user `arin`, เปิด SSH, WiFi, **timezone Asia/Bangkok**.
- ⚠️ ใช้ **SD การ์ดแท้** (รอบก่อนเจอการ์ดปลอม "asdfg" 512MB) — เช็ค `sudo dmesg | grep mmc` ว่าความจุจริงตรงฉลาก; **อย่า** แตะ `kernel=` ใน config.txt (เคยทำ Pi#2 บูตไม่ขึ้น).
- **IP ต้องเป็น 192.168.41.207** เหมือนเดิม (จอง DHCP ตาม MAC ที่ router หรือตั้ง static) —
  Pi#1 `MQTT_HOST`, peer-watchdog, ESP32 webhook ทั้งหมดชี้มาที่ IP นี้.
- ถอด USB drive ออก / ตั้ง boot order ให้บูตจาก SD.

## 2. Base packages
```bash
sudo apt update
sudo apt install -y git mosquitto mosquitto-clients
curl -fsSL https://get.docker.com | sh          # docker + compose plugin (หรือ apt install docker.io docker-compose-plugin)
sudo usermod -aG docker arin ; sudo systemctl enable --now docker
# chromium สำหรับ kiosk (ถ้า RPi OS Desktop ยังไม่มี): sudo apt install -y chromium-browser
```

## 3. Clone repo (GitOps)
```bash
git clone https://github.com/iamkkn/adsb-station.git /home/arin/adsb-station
```

## 4. คืน secrets/env จาก backup (ข้อ 0)
```bash
sudo tar xzf pi2-backup-*.tgz -C /            # คืน HA config + /etc env + mosquitto passwd + ssh key กลับที่เดิม
sudo chown -R arin:arin /home/arin/adsb-station/deploy/homeassistant/config /home/arin/.ssh
chmod 700 /home/arin/.ssh ; chmod 600 /home/arin/.ssh/fleet_id
```
> ถ้า backup ไม่มี/ไม่ครบ: สร้างใหม่ตามข้อ 5-8 (mosquitto passwd, env จาก `*.example`, HA import automation).

## 5. Fleet Mosquitto broker
```bash
sudo cp /home/arin/adsb-station/pi-ha/mosquitto/fleet.conf /etc/mosquitto/conf.d/fleet.conf
# ถ้าไม่ได้คืน passwd จาก backup — สร้าง user fleet + adsb ใหม่ (รหัสต้องตรงกับ env ทุกฝั่ง):
#   sudo mosquitto_passwd -c /etc/mosquitto/fleet.passwd fleet
#   sudo mosquitto_passwd -b /etc/mosquitto/fleet.passwd adsb <PASS>
sudo systemctl enable --now mosquitto ; sudo systemctl restart mosquitto
# เช็ค: ฟัง 0.0.0.0:1883
ss -tlnp | grep 1883
```

## 6. Home Assistant (Docker)
```bash
cd /home/arin/adsb-station/deploy/homeassistant
# config/ คืนจาก backup แล้ว (ข้อ 4) — มี automations + integration ครบ
docker compose up -d
docker ps | grep homeassistant           # ต้อง Up
```
- เปิด `http://192.168.41.207:8123` → MQTT integration ต้องต่อ `127.0.0.1:1883` user `adsb` ได้.
- เช็ค automations: **power-cycle** + **fan-control** + **fan-state** อยู่ครบ (ถ้า config ใหม่/ไม่ได้คืน →
  import จาก `deploy/homeassistant/automations/*.yaml` ตาม `MIGRATE_HA_TO_PI2.md`).

## 7. peer-watchdog
```bash
sudo cp /home/arin/adsb-station/pi-ha/systemd/peer-watchdog.service /etc/systemd/system/
# /etc/fleet-peer-watchdog.env คืนจาก backup แล้ว (DRY_RUN=0=ของจริง, BROKER_USER/PASS, HA_WEBHOOK_CYCLE, SSH_KEY)
#   ไม่มี backup → cp pi-ha/peer-watchdog/config.env.example /etc/fleet-peer-watchdog.env แล้วเติมค่า
sudo systemctl daemon-reload ; sudo systemctl enable --now peer-watchdog
journalctl -u peer-watchdog -n 15 --no-pager    # ต้องเห็นรับ heartbeat จาก Pi#1 → HEALTHY
```

## 8. Timetable kiosk
```bash
sudo cp /home/arin/adsb-station/pi-ha/systemd/adsb-timetable.service /etc/systemd/system/
# /etc/adsb-timetable.env (TIMETABLE_ICS_URL) คืนจาก backup — ไม่มีก็ตั้งใหม่ (ดู pi-ha/timetable/README.md)
sudo systemctl daemon-reload ; sudo systemctl enable --now adsb-timetable
# Chromium kiosk autostart → localhost:8080
mkdir -p ~/.config/labwc
tee ~/.config/labwc/autostart >/dev/null <<'EOF'
chromium --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 http://localhost:8080 &
EOF
chmod +x ~/.config/labwc/autostart
```

## 9. pi-ha-autoupdate (GitOps — Pi#2 pull เอง)
```bash
sudo install -m 755 /home/arin/adsb-station/pi-ha/deploy/pi-ha-autoupdate.sh /usr/local/bin/
sudo cp /home/arin/adsb-station/pi-ha/systemd/pi-ha-autoupdate.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload ; sudo systemctl enable --now pi-ha-autoupdate.timer
```

## 10. Verify end-to-end
- `ss -tlnp | grep -E '1883|8123|8080'` → mosquitto + HA + kiosk ฟังครบ
- peer-watchdog log = รับ heartbeat Pi#1 → `HEALTHY` (ไม่ปีน ladder)
- บน **Pi#1**: `journalctl -u adsb-health-agent -n 5` = ส่ง heartbeat ไป broker Pi#2 สำเร็จ (ไม่ "not authorised")
- HA → พัดลม/temp sensor ของ Pi#1 โผล่ (discovery ทำงาน)
- จอ kiosk โชว์ตารางเรียน

## 11. ⚠️ IP ต้องคงที่ 192.168.41.207
ของที่ผูกกับ IP นี้ (แก้ถ้า IP เปลี่ยน):
- **Pi#1** `/etc/fr24-watchdog.env` → `MQTT_HOST=192.168.41.207` → `systemctl restart adsb-ha-mqtt adsb-health-agent`
- **peer-watchdog** `HA_WEBHOOK_CYCLE=http://192.168.41.207:8123/...`
- **ESP32** `src/config.h` HA webhook

## ลำดับสำคัญ
Mosquitto (ข้อ 5) ต้องขึ้น **ก่อน** peer-watchdog + HA — ทั้งคู่ต่อ broker นี้. ถ้า Pi#1 health-agent
ต่อไม่ได้ (auth) = passwd ไม่ตรง → กลับไปข้อ 5 ตั้ง user `fleet`/`adsb` ให้รหัสตรงกับ env ทุกฝั่ง.
