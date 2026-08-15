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

## Phase 3b — ต่อ MQTT integration ใน HA (ขั้นที่พลาดง่ายที่สุด)
Phase 3 ทำให้ Pi#1 **ส่ง** ขึ้น broker ได้ แต่ HA จะ **อ่าน** ได้ต่อเมื่อมี MQTT integration ของตัวเอง
— ไม่ใช่ของที่ติดมากับ config ที่ย้ายมา ต้องเพิ่มเอง:

HA → Settings → Devices & Services → **+ ADD INTEGRATION** → `MQTT` → **Manual**
(อย่าเลือกโหมด add-on — HA ตัวนี้รันใน Docker ธรรมดา ไม่ใช่ HAOS จึงไม่มี add-on)

| ช่อง | ค่า |
|---|---|
| Broker | `127.0.0.1` |
| Port | `1883` |
| Username | `adsb` |
| Password | เดียวกับ `MQTT_PASS` ของ Pi#1 |

ใช้ `127.0.0.1` ไม่ใช่ IP ของ wlan0 — Pi#2 มีสองขา (eth0/wlan0) และ IP เป็น DHCP ทั้งคู่
ใส่ IP ไว้เท่ากับผูกให้พังตอน DHCP เปลี่ยนเลข

**ยืนยันว่าติดจริง** (ห้ามดูแค่ว่ามี entity — ดูเหตุผลใน "กับดัก" ข้างล่าง):
```bash
sudo grep '127.0.0.1' /var/log/mosquitto/mosquitto.log | grep "u'adsb'" | tail -3
```

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

ทั้งสองไฟล์ใส่ค่าของสถานีปัจจุบันไว้แล้ว (`switch.pi_fan_socket_1`, `sensor.ads_b_arin_cpu_temperature`,
`adsb/arin/fan`) — วางได้เลยถ้า STATION ยังเป็น `Arin`. เปลี่ยน STATION_ID เมื่อไรค่อยแก้ตาม:

**หลังวาง `pi1-fan-state.yaml` ต้องกระตุ้นให้มันยิงหนึ่งครั้ง** — trigger ของมันคือ "switch เปลี่ยนสถานะ"
กับ "HA start" เท่านั้น ถ้าไม่ทำอะไรเลย topic จะว่างตลอดและ `/run/adsb-ha/fan.json` ค้างที่ `"on": null`
(อาการนี้หน้าตาเหมือน MQTT พังทุกประการ). กด ⋮ → **Run actions** หรือสลับสวิตช์พัดลมหนึ่งครั้งก็ได้

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

## กับดักตอนไล่หาปัญหา MQTT (เจอมาแล้วทั้งหมด เสียเวลาไปหลายรอบ)

**1. entity มีอยู่ ไม่ได้แปลว่า MQTT ต่ออยู่**
`core.entity_registry` เก็บ entity ที่เคยถูก discovery ไว้ถาวร — ย้ายเครื่องมาแล้ว `sensor.ads_b_*`
ยังโผล่ครบทั้ง 9 ตัวทั้งที่ integration ไม่ได้ต่อ broker เลยก็ได้ ต้องดูที่ log ของ broker เท่านั้น

**2. log การเชื่อมต่อของ mosquitto ไม่ได้อยู่ใน journald**
`mosquitto.conf` ตั้ง `log_dest file /var/log/mosquitto/mosquitto.log` → `journalctl -u mosquitto`
เห็นแค่ start/reload ของ service ไม่เห็น client เลย ต้องอ่านไฟล์

**3. อย่าใช้ `tail` หา connection ของ HA**
HA ต่อ MQTT **ครั้งเดียวแล้วค้างไว้ตลอด** บรรทัด connect จึงอยู่ตอน HA บูต (อาจหลายวันก่อน) ส่วน
Pi#1 ต่อ-ตัดใหม่ทุกครั้งที่ `mosquitto_pub` ทำงาน (ทุกนาที × หลาย topic) จึงท่วมท้ายไฟล์หมด
→ ใช้ `grep "127.0.0.1"` ทั้งไฟล์ ไม่ใช่ `tail`

**4. `mosquitto_sub -W` ที่ไม่มี `-C` อาจกลืน output ทิ้ง**
เวลาเรียกผ่าน pipe/`capture_output` stdout เป็น block-buffered — พอ `-W` หมดเวลาแล้วจบ ข้อความที่
รับมาแล้วอาจไม่ถูก flush → เห็นเป็น "Timed out" เปล่า ๆ ทั้งที่ retained มีอยู่จริง
→ ทดสอบด้วย `-C 1` (จบทันทีที่ได้ข้อความ) หรือรันตรงในเทอร์มินัลไม่ผ่าน pipe

**5. `sub_retained()` ใน mqtt_publish.py คืน `None` ทั้งกรณี "ต่อไม่ได้" และ "ไม่มีข้อความ"**
`fan.json` เป็น `"on": null` จึงบอกได้แค่ว่า "ไม่รู้" แยกสองสาเหตุไม่ได้ ต้องยิง `mosquitto_sub` มือ
พร้อม `-d` ดู CONNACK/SUBACK เอง

**6. ถ้า publish สำเร็จแต่ subscribe ไม่เจอ อย่าเพิ่งโทษ ACL**
`fleet.conf` ไม่มี `acl_file` → ทุก user ที่ auth ผ่านทำได้หมดทั้งอ่านและเขียน ตรวจ round-trip
(`mosquitto_pub -r` แล้ว `mosquitto_sub -C 1`) ก่อนเสมอ จะตัดฝั่ง broker ออกได้ในคำสั่งเดียว
