# ESP32 display watchdog — touchscreen Pi reset (CYD2USB / ESP32-2432S028Rv3)

Watchdog แบบมีจอ+ทัช บนบอร์ด **Sunton ESP32-2432S028Rv3 ("CYD2USB", 2 USB ports)** — จอ 2.8" **ST7789**
240×320 + touch XPT2046. Config นี้ **verified บนฮาร์ดแวร์จริง** (ขอบคุณ know-how ของเจ้าของบอร์ด).

**2 โหมดในเครื่องเดียว:**
- **NORMAL (อัตโนมัติ)** — เฝ้า Pi (TCP `:22`) ทุก 30 วิ. เงียบเกิน **15 นาที** → power-cycle ปลั๊ก Tuya เอง
- **BACKUP (มือกด)** — แตะปุ่ม **RESET PI** บนจอ (กดยืนยัน 2 ครั้ง) → Tuya OFF → **นับถอยหลัง 60 วิ** → Tuya ON

> เวอร์ชันไม่มีจอ (MicroPython) อยู่ที่ `../watchdog-esp32/` สำหรับ ESP32 เปล่า

## ⚠️ อ่านก่อน — ฮาร์ดแวร์เฉพาะบอร์ดนี้
- **Rv3 = ST7789 ไม่ใช่ ILI9341** (แยกจาก Rv1/Rv2). ตั้งไว้ใน `platformio.ini` แล้ว
- **USB-C ของบอร์ดไม่มี CC resistor** → สาย C-to-C **ไม่จ่ายไฟเลย** (เหมือนบอร์ดตาย) → **ใช้ micro-USB** หรือ USB-A→C. อย่าเสียบ 2 USB พร้อมกัน
- จอ = HSPI · touch = **VSPI (bus แยก)** — ตั้งในโค้ด/flags แล้ว
- **ESP32 ต้องเสียบไฟคนละแหล่งกับ Pi** (ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด) + ปลั๊กตั้ง **power-on state = ON**
- ใช้ปลั๊ก **Tuya v3.3** (v3.4/3.5 tuya.h นี้ไม่รองรับ)

## ทำไม direct Tuya local (ไม่ใช่ HA REST API) สำหรับ "reset Pi"
**HA รันบน Pi** → ตอน Pi แฮงก์ (จังหวะที่ watchdog ต้องทำงาน) HA ตายด้วย → คุม Tuya ผ่าน HA ไม่ได้.
watchdog reset จึงต้อง **direct Tuya local** = อิสระจาก Pi/HA. (งานอื่นเช่นโชว์ sensor ค่อยใช้ HA REST API แยก
ตอน Pi ยังอยู่ก็ได้ — เพิ่มทีหลัง.)

## Build — PlatformIO (ไม่ใช่ Arduino IDE)
โครง PlatformIO: `platformio.ini` + `src/main.cpp` + `src/tuya.h`. TFT_eSPI/LVGL ตั้งผ่าน **build flags**
(ไม่มี `User_Setup.h`, ไม่มี `lv_conf.h` — โปรเจกต์นี้**ไม่ใช้ LVGL** วาดด้วย TFT_eSPI ตรงๆ เบา RAM).

1. เปิดโฟลเดอร์ `watchdog-esp32-display/` ใน VS Code + PlatformIO (ต้องเปิดโฟลเดอร์ที่มี `platformio.ini`)
2. `lib_deps` ลงเอง: **TFT_eSPI 2.5.43** + **XPT2046_Touchscreen v1.4**
3. **pin `upload_port`/`monitor_port` ให้ตรงพอร์ต CH340 ของบอร์ด** (uncomment ใน `[env:cyd2usb]`) —
   auto-detect อาจเลือกผิดอุปกรณ์ (เช่นชน CP210x ตัวอื่น). ยืนยัน log ว่าพอร์ตถูกก่อน flash
4. `upload_speed = 115200` (อย่าขึ้น 921600 — CH340 นี้ไม่นิ่ง)

## local_key ของปลั๊ก
`tinytuya wizard` → ได้ local_key + **ทดสอบ control บน PC ก่อน** (ดู `../watchdog-esp32/README.md`). ใช้ปลั๊ก **v3.3**.

## ตั้งค่า + flash
```bash
cp src/config.h.example src/config.h     # แก้ WIFI_*, PI_IP, TUYA_ID/KEY/IP/DP
```
Build + Upload (PlatformIO) → เปิด Serial Monitor 115200 → ควรเห็นจอขึ้น "Pi WATCHDOG" + สถานะ

## Touch calibration
ค่าในโค้ด (`TOUCH_X_MIN/MAX`, `TOUCH_Y_MIN/MAX` = 338/3676/505/3465) **วัดจริงที่ rotation 1** ของบอร์ดนี้
(ไม่ swap/invert). ถ้าปุ่มกดไม่ตรง แตะจอดู Serial `touch raw=...` แล้วปรับ 4 ค่านั้น.
**เปลี่ยน `setRotation` = ค่าพวกนี้ใช้ไม่ได้ ต้องวัดใหม่** (calibration ผูกกับ rotation).

## ทดสอบ
- **manual**: แตะ RESET PI → TAP AGAIN (ส้ม) → แตะซ้ำใน 3 วิ → Tuya OFF + นับถอยหลัง 60 → ON
- **auto**: ลด `DOWN_MS` เป็น `120000UL` (2 นาที) ชั่วคราว → ทำ Pi เข้าไม่ถึง → รอ 2 นาที → power-cycle เอง → คืน `900000UL`

## Troubleshooting (จาก field notes)
- **สีเพี้ยน** → ตรวจ `-DST7789_DRIVER -DTFT_RGB_ORDER=TFT_BGR -DTFT_INVERSION_OFF` ใน platformio.ini
- **จอไม่ขึ้น** → เช็ค `TFT_BL=21` (active HIGH) + ST7789 driver + สาย micro-USB (ไม่ใช่ C-to-C)
- **ทัชไม่ตอบ/ไม่ตรง** → touch ต้องอยู่ **VSPI** (pin 25/33/32/39/36) + rotation ตรงกับ calibration
- **ปลั๊กไม่ขยับ** → ทดสอบ tinytuya บน PC ก่อน (ยืนยัน id/key/DP/version=3.3), เช็ค `TUYA_DP`
- **flash ผิดอุปกรณ์** → pin `upload_port` เสมอ, ยืนยันพอร์ตก่อน flash

## ชั้นป้องกันของสถานี (ตัวนี้คือชั้น 4 + มี manual)
1. ปิด router scheduled-reboot 03:00 · 2. เดินสาย LAN เข้า Pi · 3. internal HW watchdog (CPU lockup)
4. **ESP32 display watchdog (ตัวนี้)** — auto จับ full hang + ปุ่มกด reset มือได้ทุกเมื่อ
