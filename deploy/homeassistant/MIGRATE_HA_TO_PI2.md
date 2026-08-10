# ย้าย Home Assistant Pi#1 → Pi#2 (แล้วเปิด power-cycle automation)

**ทำไม:** automation ตัดไฟ Pi#1 — ถ้า HA อยู่บน Pi#1 เอง พอตัดไฟ HA ตายตาม สั่งเปิดกลับไม่ได้ →
Pi ค้างดับถาวร. HA ต้องอยู่ Pi#2 (คนละแหล่งไฟกับ Pi#1) ก่อนถึงจะเปิด automation ได้ปลอดภัย.

**ปลายทาง:** HA (Docker) บน Pi#2 + ใช้ **native mosquitto บน Pi#2** (fleet broker เดิม) เป็น MQTT ตัวเดียว
ของทั้งระบบ (fleet topics + HA discovery). Pi#1 เลิกรัน HA/mosquitto, ชี้ `mqtt_publish` มาที่ Pi#2.

---

## Phase 1 — เพิ่ม user `adsb` ใน broker Pi#2 (สำหรับ HA discovery + mqtt_publish)
fleet broker มีแค่ user `fleet`. HA + `mqtt_publish.py` ใช้ user `adsb` → เพิ่มเข้าไฟล์เดียวกัน. บน **Pi#2**:
```bash
sudo mosquitto_passwd -b /etc/mosquitto/fleet.passwd adsb 'ADSB_MQTT_PASS'   # ตั้งรหัส user adsb
sudo chown root:mosquitto /etc/mosquitto/fleet.passwd && sudo chmod 640 /etc/mosquitto/fleet.passwd
sudo systemctl restart mosquitto
mosquitto_sub -h 127.0.0.1 -u adsb -P 'ADSB_MQTT_PASS' -t 'test/#' -W 2; echo "exit=$?"   # 27=ok
```

## Phase 2 — ย้าย HA config Pi#1 → Pi#2
บน **Pi#1** — หยุด stack เดิม + แพ็ค config:
```bash
cd ~/adsb-station/deploy/homeassistant
docker compose down                       # หยุด HA + mosquitto Docker บน Pi#1
sudo tar czf ~/ha-config.tgz -C "$PWD" config     # แพ็ค ./config ทั้งก้อน
```
คัดลอกไป **Pi#2** (จากเครื่องคุณ หรือ scp ตรง):
```bash
scp arin@192.168.41.241:~/ha-config.tgz arin@192.168.41.207:~/
```
บน **Pi#2** — กาง config + start HA:
```bash
cd ~/adsb-station/deploy/homeassistant
git pull                                  # ให้มี docker-compose.yml เวอร์ชัน Pi#2 (HA อย่างเดียว)
tar xzf ~/ha-config.tgz -C "$PWD"         # ได้ ./config
# ชี้ MQTT ใน HA config ให้เป็น native broker: 127.0.0.1:1883 user adsb (แก้ใน HA UI ทีหลังก็ได้)
docker compose up -d                      # HA :8123 (ไม่มี mosquitto Docker — ใช้ native)
docker logs -f homeassistant              # รอ HA ขึ้น
```
> Tuya integration อาจต้อง **re-auth** หลังย้าย (token ผูกกับ cloud/instance). HA → Settings →
> Devices & Services → Tuya → reconfigure ถ้าขึ้น error. ถ้า config ย้ายมาไม่สมบูรณ์ ทางเลือกสำรอง:
> ตั้ง HA ใหม่บน Pi#2 แล้ว add integration **MQTT** (127.0.0.1:1883 user adsb) + **Tuya** ใหม่.

## Phase 3 — ชี้ mqtt_publish (Pi#1) มา broker Pi#2
บน **Pi#1** แก้ `/etc/fr24-watchdog.env` (host เดิม 127.0.0.1 = mosquitto ที่เพิ่งปิด):
```
MQTT_HOST=192.168.41.207     # Pi#2 (ArinII)
MQTT_PORT=1883
MQTT_USER=adsb
MQTT_PASS=ADSB_MQTT_PASS     # เดียวกับ Phase 1
```
แล้ว `sudo systemctl restart adsb-ha-mqtt` (ถ้าเป็น timer รอรอบถัดไปก็ได้). เช็คใน HA ว่า sensor
`sensor.ads_b_arin_*` กลับมา available.

## Phase 4 — ใส่ automation ตัดไฟ
HA → Settings → Automations → Create → ⋮ → **Edit in YAML** → วางเนื้อจาก
`deploy/homeassistant/automations/pi1-power-cycle.yaml` → แก้ 2 ค่า:
- `webhook_id` → ตั้งชื่อ (เช่น `pi1_power_cycle`) แล้วจำไว้
- `entity_id` → switch ปลั๊ก Pi#1 จริง (Developer Tools → States หา `switch.*`)
Save.

## Phase 4b — ใส่ fan automations (กันพัดลมค้าง on)
เดิม automation คุมพัดลมอยู่บน HA Pi#1 — ย้ายแล้วต้องสร้างใหม่ ไม่งั้นพัดลมไม่ถูกสั่งปิด (ค้าง on 24ชม)
และ digest/Pixoo โชว์สถานะพัดลมค้าง. วาง 2 ไฟล์ (Create → ⋮ → Edit in YAML):
- `automations/pi1-fan-control.yaml` — เปิด/ปิดพัดลมตาม CPU temp (hysteresis + time_pattern กันค้าง)
- `automations/pi1-fan-state.yaml` — republish สถานะ switch → `adsb/<sid>/fan` (retained) ให้ Pixoo/digest
แก้ทั้งคู่: `switch.YOUR_FAN_SWITCH` = switch Tuya พัดลมจริง; entity temp + topic `adsb/<sid>/fan` ให้ตรง sid.
หา sid/entity จริงจาก Pi#1:
```
python3 -c "import re,socket; s=open('/etc/fr24-watchdog.env').read(); m=re.search(r'STATION_ID=(\S+)',s); st=(m.group(1).strip('\"\\'') if m else socket.gethostname()); sid=''.join(c if c.isalnum() else '_' for c in st).lower(); print('topic=adsb/'+sid+'/fan  entity=sensor.ads_b_'+sid+'_cpu_temperature')"
```

## Phase 5 — ต่อ webhook URL เข้ากับ watchdog ทั้งสอง
URL = `http://192.168.41.207:8123/api/webhook/pi1_power_cycle` (ตาม webhook_id ที่ตั้ง).
- **peer-watchdog (Pi#2)** `/etc/fleet-peer-watchdog.env` → `HA_WEBHOOK_CYCLE=<URL>` → `sudo systemctl restart peer-watchdog`
- **ESP32** `src/config.h` → `#define HA_WEBHOOK_CYCLE "<URL>"` → reflash

## Phase 6 — ทดสอบ + go-live
```bash
# ยิง webhook ด้วยมือ (DRY_RUN ฝั่ง watchdog ไม่เกี่ยว — นี่เทสต์ HA ตรงๆ)
curl -X POST http://192.168.41.207:8123/api/webhook/pi1_power_cycle
```
→ ปลั๊ก Pi#1 ต้องดับ ~60 วิ แล้วติดกลับ + เห็น `fleet/incident` เด้ง (sub 'fleet/#' ดู).
เมื่อชัวร์แล้ว → `DRY_RUN=0` ใน peer-watchdog env → restart → **peer-watchdog ตัดไฟจริงได้แล้ว**.

⚠️ ตอนทดสอบ curl ครั้งแรก **ยืนยันว่า `entity_id` เป็นปลั๊ก Pi#1 จริง** ไม่ใช่ปลั๊กอื่น (โดยเฉพาะไม่ใช่
ปลั๊ก Pi#2 ที่รัน HA เอง). ทดสอบครั้งแรกควรนั่งดูหน้าเครื่องด้วย.
