# ESP32 display watchdog — touchscreen Pi reset (CYD2USB / ESP32-2432S028Rv3)

Watchdog แบบมีจอ+ทัช บนบอร์ด **Sunton ESP32-2432S028Rv3 ("CYD2USB", 2 USB ports)** — จอ 2.8" **ST7789**
240×320 + touch XPT2046. Config นี้ **verified บนฮาร์ดแวร์จริง** (ขอบคุณ know-how ของเจ้าของบอร์ด).

**2 โหมดในเครื่องเดียว:**
- **NORMAL (อัตโนมัติ)** — เฝ้า Pi (TCP `:22`) ทุก 30 วิ. เงียบเกิน **15 นาที** → ยิง webhook power-cycle เอง
- **BACKUP (มือกด)** — แตะปุ่ม **RESET PI** บนจอ (กดยืนยัน 2 ครั้ง) → ยิง **1 webhook** → HA ตัด→รอ 60 วิ→เปิดเอง

> เวอร์ชันไม่มีจอ (MicroPython) อยู่ที่ `../watchdog-esp32/` สำหรับ ESP32 เปล่า

## ⚠️ อ่านก่อน — ฮาร์ดแวร์เฉพาะบอร์ดนี้
- **Rv3 = ST7789 ไม่ใช่ ILI9341** (แยกจาก Rv1/Rv2). ตั้งไว้ใน `platformio.ini` แล้ว
- **USB-C ของบอร์ดไม่มี CC resistor** → สาย C-to-C **ไม่จ่ายไฟเลย** (เหมือนบอร์ดตาย) → **ใช้ micro-USB** หรือ USB-A→C. อย่าเสียบ 2 USB พร้อมกัน
- จอ = HSPI · touch = **VSPI (bus แยก)** — ตั้งในโค้ด/flags แล้ว
- **ESP32 ต้องเสียบไฟคนละแหล่งกับ Pi** (ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด) + ปลั๊กตั้ง **power-on state = ON**
- ใช้ปลั๊ก **Tuya v3.3** (v3.4/3.5 tuya.h นี้ไม่รองรับ)

## คุมปลั๊กยังไง: HA Webhook เดียว (default)
โปรเจกต์นี้สั่ง power-cycle ผ่าน **Home Assistant Webhook เดียว** (`src/ha_switch.h` → `haWebhook`) — **ไม่ต้องมี token/auth**
(secret อยู่ใน URL เอง). ESP32 → HTTP POST webhook **1 ครั้ง** → HA automation เดียวทำ **turn_off → delay 60 วิ → turn_on** ครบวงจร.
**HA เป็นคนจับเวลา** ไม่ใช่ ESP32 → ถ้า ESP32 หลุด WiFi หลังยิงไปแล้ว ปลั๊กก็เปิดกลับเอง (ทนกว่าแบบ 2 webhook).

⚠️ **กฎเหล็ก: HA ต้องอยู่คนละเครื่องกับ Pi ที่จะตัด** — ถ้า HA รันบน Pi ตัวที่ถูกตัด: สั่ง OFF → Pi ดับ →
HA ตายตาม → สั่ง ON ไม่ได้ → **Pi ค้างดับถาวร**. ดังนั้น:
- **ตอนนี้ (HA ยังบน ADS-B Pi)** → automation ให้ตัด **ปลั๊ก/switch ตัวอื่น** เท่านั้น (เช่น พัดลม/spare)
- **watchdog จริง (ตัดปลั๊ก ADS-B Pi)** → ทำได้เมื่อ **ย้าย HA ไป Pi อีกตัว** (HA อยู่คนละเครื่อง = ตัดปลอดภัย)

*(ทางเลือก: `haSwitch` (token) + `src/tuya.h` (direct Tuya v3.3 local) ยังมีให้ ถ้าอยากใช้แทน webhook)*

## Build — PlatformIO (ไม่ใช่ Arduino IDE)
โครง PlatformIO: `platformio.ini` + `src/main.cpp` + `src/tuya.h`. TFT_eSPI/LVGL ตั้งผ่าน **build flags**
(ไม่มี `User_Setup.h`, ไม่มี `lv_conf.h` — โปรเจกต์นี้**ไม่ใช้ LVGL** วาดด้วย TFT_eSPI ตรงๆ เบา RAM).

1. เปิดโฟลเดอร์ `watchdog-esp32-display/` ใน VS Code + PlatformIO (ต้องเปิดโฟลเดอร์ที่มี `platformio.ini`)
2. `lib_deps` ลงเอง: **TFT_eSPI 2.5.43** + **XPT2046_Touchscreen v1.4**
3. **pin `upload_port`/`monitor_port` ให้ตรงพอร์ต CH340 ของบอร์ด** (uncomment ใน `[env:cyd2usb]`) —
   auto-detect อาจเลือกผิดอุปกรณ์ (เช่นชน CP210x ตัวอื่น). ยืนยัน log ว่าพอร์ตถูกก่อน flash
4. `upload_speed = 115200` (อย่าขึ้น 921600 — CH340 นี้ไม่นิ่ง)

## ตั้ง HA — สร้าง 1 webhook automation (off → delay → on)
Settings → Automations → **Create automation** → (ข้าม / Start with empty) → **Edit in YAML** วางแบบนี้
(แก้ `webhook_id` กับ `entity_id` เป็นของคุณ):
```yaml
alias: Pi power-cycle
trigger:
  - platform: webhook
    webhook_id: xxxxxxxx        # ได้จาก HA — จะไปอยู่ท้าย URL
    allowed_methods: [POST]
    local_only: true           # รับเฉพาะใน LAN (ปลอดภัยขึ้น)
action:
  - service: homeassistant.turn_off
    target: { entity_id: switch.ปลั๊กทดสอบ }
  - delay: { seconds: 60 }     # ตั้งให้ตรงกับ OFF_SECONDS ในโค้ด
  - service: homeassistant.turn_on
    target: { entity_id: switch.ปลั๊กทดสอบ }
mode: single                   # กันยิงซ้ำระหว่างกำลัง cycle
```
เอา URL (`http://192.168.41.241:8123/api/webhook/<webhook_id>`) ไปใส่ `HA_WEBHOOK_CYCLE`
- ⚠️ `entity_id` = **ปลั๊กตัวอื่น** ไม่ใช่ปลั๊กของ Pi ที่รัน HA
- ทดสอบจาก PC: `curl -X POST http://192.168.41.241:8123/api/webhook/<id>` → ปลั๊กควรดับแล้วติดกลับใน 60 วิ (webhook คืน 200 เสมอ)

## Pi uptime จริงบนจอ (ออปชัน) — endpoint บน Pi
บรรทัด **"Pi up"** บนจอดึง uptime จริงของ Pi จาก `adsb-uptime.service` (HTTP `:8099`, stdlib).
ถ้ายังไม่ขึ้นจะโชว์ `Pi up -- (info svc?)`.

**ถ้ารัน `adsb-autoupdate` อยู่แล้ว → ไม่ต้องทำอะไร** — autoupdate จะ `cp` unit + `enable --now adsb-uptime`
ให้เองภายใน ~2 รอบ (มันรู้จัก daemon service ใหม่แล้ว). เช็คด้วย `curl -s http://192.168.41.241:8099/`

ถ้าไม่ได้รัน autoupdate → ติดตั้งเองครั้งเดียว:
```bash
cd ~/adsb-station && git pull
sudo cp systemd/adsb-uptime.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now adsb-uptime
curl -s http://192.168.41.241:8099/          # ควรได้ตัวเลขวินาที uptime
```

## ตั้งค่า + flash
```bash
cp src/config.h.example src/config.h     # แก้ WIFI_*, PI_IP, HA_WEBHOOK_CYCLE
```
Build + Upload (PlatformIO) → เปิด Serial Monitor 115200 → ควรเห็นจอขึ้น "Pi WATCHDOG" + สถานะ

## Touch calibration
ค่าในโค้ด (`TOUCH_X_MIN/MAX`, `TOUCH_Y_MIN/MAX` = 338/3676/505/3465) **วัดจริงที่ rotation 1** ของบอร์ดนี้
(ไม่ swap/invert). ถ้าปุ่มกดไม่ตรง แตะจอดู Serial `touch raw=...` แล้วปรับ 4 ค่านั้น.
**เปลี่ยน `setRotation` = ค่าพวกนี้ใช้ไม่ได้ ต้องวัดใหม่** (calibration ผูกกับ rotation).

## ทดสอบ
- **ยังไม่มี key ก็ทดสอบได้**: ตั้ง `DRY_RUN 1` (ค่าเริ่มต้นใน config) → จอ/ทัช/WiFi/เฝ้า Pi + ปุ่ม→ยืนยัน→
  countdown ทำงานครบ **โดยไม่ยิง Tuya จริง** (โชว์ "DRY RUN - no real cut"). พอได้ `TUYA_KEY` แล้วตั้ง `DRY_RUN 0`
- **manual**: แตะ RESET PI → TAP AGAIN (ส้ม) → แตะซ้ำใน 3 วิ → (DRY_RUN=0) Tuya OFF + นับถอยหลัง 60 → ON
- **auto**: ลด `DOWN_MS` เป็น `120000UL` (2 นาที) ชั่วคราว → ทำ Pi เข้าไม่ถึง → รอ 2 นาที → power-cycle เอง → คืน `900000UL`

## Troubleshooting (จาก field notes)
- **สีเพี้ยน** → ตรวจ `-DST7789_DRIVER -DTFT_RGB_ORDER=TFT_BGR -DTFT_INVERSION_OFF` ใน platformio.ini
- **จอไม่ขึ้น** → เช็ค `TFT_BL=21` (active HIGH) + ST7789 driver + สาย micro-USB (ไม่ใช่ C-to-C)
- **ทัชไม่ตอบ/ไม่ตรง** → touch ต้องอยู่ **VSPI** (pin 25/33/32/39/36) + rotation ตรงกับ calibration
- **ปลั๊กไม่ขยับ** → curl ทดสอบ webhook (`curl -X POST .../api/webhook/<id>`), เช็ค automation ใน HA ว่า target ปลั๊กถูก + enabled
- **flash ผิดอุปกรณ์** → pin `upload_port` เสมอ, ยืนยันพอร์ตก่อน flash

## ชั้นป้องกันของสถานี (ตัวนี้คือชั้น 4 + มี manual)
1. ปิด router scheduled-reboot 03:00 · 2. เดินสาย LAN เข้า Pi · 3. internal HW watchdog (CPU lockup)
4. **ESP32 display watchdog (ตัวนี้)** — auto จับ full hang + ปุ่มกด reset มือได้ทุกเมื่อ
