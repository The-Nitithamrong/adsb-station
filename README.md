# adsb-station

Raspberry Pi 5 ADS-B station (VTBD feeder) + reliability watchdog, THA inbound
flight watcher, and Pixoo 64 status display.

ทุก secret อยู่ใน `/etc/fr24-watchdog.env` บน Pi (ไม่อยู่ใน repo) → repo public/pull ได้ปลอดภัย.

## โครงสร้าง

| โฟลเดอร์ | ทำอะไร |
|---|---|
| `watchdog/` | `fr24-watchdog.sh` — เช็ค data-flow จริง (port 30003), restart → uhubctl USB power-cycle → Telegram + healthchecks.io |
| `flightwatch/` | `flight_watcher.py` — THA inbound VTBS, ETA<=30m → Telegram + SQLite · `adsb_view.py` — ตารางเครื่องบินสด |
| `pixoo/` | `renderer.py` / `pages.py` / `main.py` — จอสถานะ Pixoo 64 (ต้องมี PixelOperator*.ttf) |
| `systemd/` | unit files ของทุก service |

## Deploy (บน Pi — ครั้งเดียว)

```bash
cd /home/arin
git clone https://github.com/iamkkn/adsb-station.git

# --- secret: สร้างครั้งเดียว ไม่ขึ้น repo ---
sudo tee /etc/fr24-watchdog.env >/dev/null << 'ENV'
TG_API="https://api.telegram.org/bot<TOKEN>/sendMessage"
TG_CHAT="<CHAT_ID>"
HC_URL="https://hc-ping.com/<uuid>"
ENV
sudo chmod 600 /etc/fr24-watchdog.env

# --- watchdog ---
sudo cp adsb-station/watchdog/fr24-watchdog.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/fr24-watchdog.sh
sudo cp adsb-station/systemd/fr24-watchdog.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fr24-watchdog.timer

# --- flight watcher ---
sudo cp adsb-station/systemd/flight-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now flight-watcher

# --- pixoo (ต้องมี font ก่อน) ---
# วาง PixelOperator-Bold.ttf + PixelOperator8.ttf ใน adsb-station/pixoo/
sudo cp adsb-station/systemd/pixoo.service /etc/systemd/system/
sudo systemctl enable --now pixoo
```

## อัปเดตหลังจากนั้น

```bash
cd ~/adsb-station && git pull
sudo systemctl restart flight-watcher   # (หรือ service ที่แก้; pixoo รันจาก repo โดยตรง)
```

⚠️ **watchdog รันจาก `/usr/local/bin/` ไม่ใช่ repo** — `git pull` ไม่อัปเดตให้.
ถ้าแก้ `fr24-watchdog.sh` ต้อง copy ทับ + รันรอบใหม่:
```bash
sudo cp ~/adsb-station/watchdog/fr24-watchdog.sh /usr/local/bin/fr24-watchdog.sh
sudo systemctl start fr24-watchdog
```
(unit files ใน `systemd/` ก็เหมือนกัน — แก้แล้วต้อง `cp` เข้า `/etc/systemd/system/` + `daemon-reload`.)

## Auto-update (ให้ Pi ดึง origin/main เอง — ติดตั้งครั้งเดียว)

หลังจากนี้ merge เข้า `main` แล้ว Pi จะ pull + sync + restart เองทุก ~10 นาที ไม่ต้องสั่ง.

```bash
sudo cp ~/adsb-station/deploy/adsb-autoupdate.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/adsb-autoupdate.sh
sudo cp ~/adsb-station/systemd/adsb-autoupdate.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsb-autoupdate.timer
```

- ดึง `origin/main` แบบ `merge --ff-only` (ปลอดภัย — ถ้า repo มี local edit ที่ชนกัน จะ**ข้าม**ไม่ทับ แล้ว log เตือน).
- sync `watchdog` → `/usr/local/bin`, unit files → `/etc/systemd/system` (+ `daemon-reload`) ให้เอง.
- restart เฉพาะ service ที่ไฟล์เปลี่ยนจริง (`flight-watcher` / `pixoo` / `fr24-watchdog`).
- ดู log: `journalctl -u adsb-autoupdate -f` · หยุดชั่วคราว: `sudo systemctl disable --now adsb-autoupdate.timer`
- สคริปต์ self-update ตัวเองด้วย (แก้ `deploy/adsb-autoupdate.sh` แล้ว merge → รอบถัดไปใช้ตัวใหม่).

## Outbox → Cloudflare D1 (optional — ส่ง events+tracks ขึ้น cloud, ทนเน็ตหลุด)

`flightwatch/outbox.py` ส่งแถวใหม่ของ `events` + `tracks` ขึ้น D1 ทุก ~10 นาที (systemd timer),
mark `sent` ต่อแถว → เน็ตหลุดก็คิวไว้ retry, ส่งซ้ำไม่ dup (uid = PK + `INSERT OR IGNORE`).
pluggable — เพิ่ม sink อื่น (เช่น Dataverse) ทีหลังได้.

**1. สร้าง D1 database + ตาราง** (Cloudflare dashboard หรือ `wrangler`):
```sql
CREATE TABLE IF NOT EXISTS events (
  uid TEXT PRIMARY KEY, station TEXT,
  ts INTEGER, flight TEXT, hex TEXT, eta_min REAL, dist_nm REAL, gs INTEGER, alt INTEGER);
CREATE TABLE IF NOT EXISTS tracks (
  uid TEXT PRIMARY KEY, station TEXT,
  hex TEXT, flight TEXT, watched INTEGER, first_ts INTEGER, last_ts INTEGER, samples INTEGER,
  min_dist_nm REAL, alt_at_min INTEGER, min_alt INTEGER, last_dist_nm REAL, last_alt INTEGER,
  max_dist_nm REAL, alert_ts INTEGER, alert_eta REAL, star_fix TEXT, star_alt INTEGER, star_ts INTEGER);
```

**2. API token**: Cloudflare → My Profile → API Tokens → สิทธิ์ `Account : D1 : Edit`.

**3. เพิ่มลง `/etc/fr24-watchdog.env`** (ไม่ขึ้น repo):
```bash
D1_ACCOUNT_ID="..."      # Cloudflare account id
D1_DATABASE_ID="..."     # จาก wrangler d1 create / dashboard
D1_API_TOKEN="..."       # token ข้อ 2
STATION_ID="T-VTBD178"   # optional (default = hostname) — แยกสถานีตอนมีหลายตัว (OPC ในอนาคต)
```

**4. ติดตั้ง service + timer**:
```bash
sudo cp ~/adsb-station/systemd/adsb-outbox.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsb-outbox.timer
sudo systemctl start adsb-outbox     # ส่งรอบแรกเลย · ดู log: journalctl -u adsb-outbox
```
(อยู่ในโฟลเดอร์ `systemd/` → auto-update จัดการ restart timer ให้เองเมื่อแก้ในอนาคต)

## Home Assistant + MQTT (optional — ข้อมูล ADS-B เป็น sensor ใน HA)

รัน HA + Mosquitto เป็น Docker container บน Pi (อยู่ร่วมกับ ADS-B) แล้วสถานี publish สถานะเข้า MQTT
→ HA auto-discovery สร้าง sensor ให้เอง (feeder health/rate/aircraft, THA inbound flight/ETA/dist, จำนวนที่รับได้).

**1. Docker** (ถ้ายังไม่มี): `curl -fsSL https://get.docker.com | sh`

**2. ตั้งรหัส MQTT + start stack**:
```bash
cd ~/adsb-station/deploy/homeassistant
# สร้าง user 'adsb' ให้ broker (ตั้งรหัสเอง)
docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /mosquitto/config/passwd adsb 'YOUR_MQTT_PASS'
docker compose up -d          # HA :8123, Mosquitto :1883
```

**3. ตั้ง HA**: เปิด `http://<pi>:8123` → onboarding → Settings → Devices → **Add Integration → MQTT**
→ broker `127.0.0.1`, port `1883`, user `adsb` + รหัสข้อ 2. sensor จะโผล่เองใต้ device "ADS-B ...".

**4. ให้สถานี publish** — เพิ่มลง `/etc/fr24-watchdog.env`:
```bash
MQTT_HOST="127.0.0.1"
MQTT_PORT="1883"
MQTT_USER="adsb"
MQTT_PASS="YOUR_MQTT_PASS"    # เดียวกับข้อ 2
```
```bash
sudo apt install -y mosquitto-clients     # publisher ใช้ mosquitto_pub บน host
sudo cp ~/adsb-station/systemd/adsb-ha-mqtt.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now adsb-ha-mqtt.timer
sudo systemctl start adsb-ha-mqtt         # ยิงรอบแรก · ดู log: journalctl -u adsb-ha-mqtt
```
(publisher รันจาก repo → auto-update ดูแลให้; unit อยู่ใน `systemd/` → timer restart อัตโนมัติ)

**power/throttle sensor** (`vcgencmd get_throttled` — จับ undervoltage / thermal throttle ของ Pi,
โผล่ทั้ง HA sensor "Power/throttle" และหน้า UP บน Pixoo) ต้องให้ user `arin` อยู่กลุ่ม `video`:
```bash
sudo usermod -aG video arin      # แล้ว logout/login หรือ restart service (ไม่งั้นอ่านได้ "unknown")
```

## Next flight บน Pixoo (optional — เที่ยวบินถัดไปจาก Google Calendar)

หน้า `NEXT` บน Pixoo โชว์เที่ยวบิน/นัดถัดไป (code + route + นับถอยหลัง) ดึงจาก Google Calendar.
ใช้ **private iCal (ICS) URL** ของปฏิทิน (ไม่ต้อง OAuth, stdlib ล้วน) — `agenda_fetch.py` ดึง ICS
ทุก ~15 นาที, หาอีเวนต์ถัดไป, เขียน `/run/agenda/next.json` ให้ `pixoo/main.py` อ่าน.

**1. เอา secret ICS URL**: Google Calendar → ⚙ Settings → เลือกปฏิทิน → **Integrate calendar**
→ คัดลอก **"Secret address in iCal format"** (ลงท้าย `/basic.ics`). ⚠️ ลับ — ใครมีลิงก์อ่านปฏิทินได้.

**2. เพิ่มลง `/etc/fr24-watchdog.env`** (ไม่ขึ้น repo):
```bash
GCAL_ICS_URL="https://calendar.google.com/calendar/ical/<...>/basic.ics"
```

**3. ติดตั้ง service + timer**:
```bash
sudo cp ~/adsb-station/systemd/adsb-agenda.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsb-agenda.timer
sudo systemctl start adsb-agenda      # ดึงรอบแรกเลย · ดู log: journalctl -u adsb-agenda
```
(รันจาก repo → auto-update ดูแลให้; unit อยู่ใน `systemd/` → timer restart อัตโนมัติ. หน้า `NEXT`
โผล่ใน rotation ของ Pixoo เอง; ถ้าไม่มีนัดจะโชว์ "no flt". ทดสอบ: `python3 agenda/agenda_fetch.py`)

## พัดลมระบายความร้อน + สถานะบน Pixoo (optional — HA/Tuya)

Pixoo หน้า UP โชว์ไอคอนพัดลม (เขียว=หมุน / หรี่=ปิด) จากสถานะ switch จริงของปลั๊ก Tuya.
ทิศทางข้อมูลกลับด้าน: HA รู้สถานะ Tuya → republish เข้า MQTT → Pi อ่านมาเขียน `/run/adsb-ha/fan.json`.

**1. Automation ใน HA — publish สถานะ switch เข้า MQTT** (Settings → Automations → Create → Edit in YAML).
แก้ `switch.pi_fan_socket_1` เป็น entity ปลั๊กจริง และ `adsb/arin/fan` ให้ `arin` = STATION_ID (ตัวเล็ก):
```yaml
alias: Publish fan state to MQTT
triggers:
  - trigger: state
    entity_id: switch.pi_fan_socket_1
  - trigger: homeassistant
    event: start
actions:
  - action: mqtt.publish
    data:
      topic: adsb/arin/fan
      payload: "{{ states('switch.pi_fan_socket_1') }}"
      retain: true
mode: single
```

**2. ให้ mqtt_publish อ่านกลับ** — ไม่ต้องตั้งอะไรเพิ่ม: `mqtt_publish.py` subscribe `adsb/<sid>/fan`
(retained) ทุกรอบ timer แล้วเขียน `/run/adsb-ha/fan.json` ให้ Pixoo เอง (RuntimeDirectory `adsb-ha`).
หลัง `git pull` sync unit ใหม่ + restart:
```bash
sudo cp ~/adsb-station/systemd/adsb-ha-mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl start adsb-ha-mqtt
cat /run/adsb-ha/fan.json      # {"ts": ..., "on": true/false}
```

**3. Automation เปิด/ปิดพัดลมตามอุณหภูมิ** (ตัวที่สั่งปลั๊กจริง) — trigger จาก sensor CPU temp,
above 50 → `switch.turn_on`, below 45 → `switch.turn_off` (hysteresis กันกระพริบ) + `time_pattern: /2`
เช็คซ้ำทุก 2 นาที (numeric_state ยิงแค่ตอน "ข้าม" threshold — พลาด edge = พัดลมค้าง).

> ⚠️ **GOTCHA ชื่อ entity ใน HA**: HA แปลงชื่อ device `ADS-B <host>` เป็น entity_id โดย `-`/`/` → `_`
> → entity จริงคือ **`sensor.ads_b_<host>_*`** (เช่น `sensor.ads_b_arin_cpu_temperature`,
> `sensor.ads_b_arin_power_throttle`) **ไม่ใช่** `adsb_...` ที่เดา. ถ้าใส่ชื่อผิด numeric_state condition
> จะเป็น False เงียบๆ (พัดลมไม่ทำงานทั้ง on/off). **คัดลอกชื่อจริงจาก Developer Tools → States เสมอ**.
> HA unit system ต้องเป็น **Metric** ด้วย ไม่งั้น temp เป็น °F แล้ว threshold เพี้ยน (Settings → System → General).

## ทดสอบก่อนรันเป็น service

```bash
python3 flightwatch/adsb_view.py        # ดูข้อมูลสด
python3 flightwatch/flight_watcher.py   # ดู THA inbound + ETA
```

## Config ที่ปรับได้

- `flight_watcher.py`: `DEST_LAT/LON` (VTBS → OPC), `ETA_ALERT_MIN`, `WATCH_PREFIX`
- `pixoo/main.py`: `PIXOO_IP`, `STATUS_F`
