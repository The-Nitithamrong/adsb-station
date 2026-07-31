# ESP32 display watchdog — touchscreen Pi reset (CYD / ESP32-2432S028R)

Watchdog แบบมีจอ+ทัช บนบอร์ด **CYD (Cheap Yellow Display, ESP32-2432S028R)** — จอ 2.8" ILI9341 240×320 + touch XPT2046.

**2 โหมดในเครื่องเดียว:**
- **NORMAL (อัตโนมัติ)** — เฝ้า Pi (TCP `:22`) ทุก 30 วิ. เงียบเกิน **15 นาที** → power-cycle ปลั๊ก Tuya เอง
- **BACKUP (มือกด)** — แตะปุ่ม **RESET PI** บนจอ (กดยืนยัน 2 ครั้ง) → Tuya OFF → **นับถอยหลัง 60 วิ บนจอ** → Tuya ON

> ตัว MicroPython แบบไม่มีจอ อยู่ที่ `../watchdog-esp32/` (สำหรับ ESP32 เปล่า) — โฟลเดอร์นี้คือเวอร์ชันจอ+ทัชสำหรับบอร์ด CYD

## ⚠️ ความปลอดภัย (อ่านก่อน)
- **ESP32 ต้องเสียบไฟคนละแหล่งกับ Pi** — ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด (ไม่งั้นตัดไฟตัวเองตาย)
- **ตั้ง power-on state ของปลั๊ก = ON** (แอป Smart Life) → Pi ได้ไฟกลับเสมอ
- ปลั๊กต้องเป็น **Tuya v3.3** (v3.4/3.5 tuya.h นี้ไม่รองรับ)

## 1. Arduino IDE — บอร์ด + library
- Board: **ESP32 Dev Module** (Tools → Board → ESP32 Arduino) · Flash 4MB · PSRAM disabled
- ลง library (Library Manager):
  - **TFT_eSPI** (Bodmer)
  - **XPT2046_Touchscreen** (Paul Stoffregen)

## 2. ⭐ ตั้ง TFT_eSPI ให้ตรงบอร์ด CYD (จุดพลาดบ่อยสุด)
แก้ไฟล์ `User_Setup.h` ใน library TFT_eSPI (`Arduino/libraries/TFT_eSPI/User_Setup.h`) ให้เป็น:
```cpp
#define ILI9341_2_DRIVER          // CYD บางล็อตสีเพี้ยน → ลองสลับ ILI9341_DRIVER + #define TFT_INVERSION_ON
#define TFT_WIDTH  240
#define TFT_HEIGHT 320
#define TFT_MISO 12
#define TFT_MOSI 13
#define TFT_SCLK 14
#define TFT_CS   15
#define TFT_DC    2
#define TFT_RST  -1
#define TFT_BL   21
#define TFT_BACKLIGHT_ON HIGH
#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_FONT6
#define LOAD_FONT7
#define LOAD_FONT8
#define LOAD_GFXFF
#define SMOOTH_FONT
#define SPI_FREQUENCY       55000000
#define SPI_READ_FREQUENCY  20000000
#define SPI_TOUCH_FREQUENCY  2500000
```
(touch XPT2046 ตั้ง pin ในสเก็ตช์แล้ว — `T_CLK/CS/MOSI/MISO/IRQ = 25/33/32/39/36` — ไม่ต้องแตะ User_Setup)

## 3. local_key ของปลั๊ก
ดึงด้วย `tinytuya wizard` แล้ว **ทดสอบ control บน PC ก่อน** (ดู `../watchdog-esp32/README.md` ข้อ 1-2). ใช้ปลั๊ก **v3.3**.

## 4. ตั้งค่า + flash
```bash
cp config.h.example config.h     # แก้ WIFI_*, PI_IP, TUYA_ID/KEY/IP/DP
```
เปิด `watchdog-esp32-display.ino` ใน Arduino IDE (config.h + tuya.h อยู่โฟลเดอร์เดียวกัน โผล่เป็นแท็บเอง) → Upload → เปิด Serial Monitor 115200

## 5. Calibrate touch (ถ้าปุ่มกดไม่ตรง)
แตะจอแล้วดู Serial: `touch raw=(x,y) -> screen=(x,y)`
- ถ้า screen coord ไม่ตรงจุดที่แตะ → ปรับ `RAW_XMIN/XMAX/YMIN/YMAX` ในสเก็ตช์ (หรือสลับแกน x↔y ถ้าหมุนผิด)
- ปุ่มใหญ่ (240×60) เลยพอเผื่อ error ได้เยอะ

## 6. ทดสอบ
- **manual**: แตะ RESET PI → กลายเป็น TAP AGAIN (ส้ม) → แตะซ้ำใน 3 วิ → Tuya OFF + นับถอยหลัง 60 → ON
- **auto**: ลด `DOWN_MS` เป็น `120000UL` (2 นาที) ชั่วคราว → ทำ Pi เข้าไม่ถึง (ปิด WiFi Pi) → รอ 2 นาที → จอควรขึ้น power-cycle เอง → คืนค่า `900000UL`

## Troubleshooting
- **สีเพี้ยน (แดง↔น้ำเงินสลับ)** → ใน User_Setup สลับเป็น `ILI9341_DRIVER` + เพิ่ม `#define TFT_INVERSION_ON` (หรือ `TFT_RGB_ORDER TFT_BGR`)
- **จอขาว/ดำ ไม่ขึ้น** → เช็ค TFT_BL (21) + driver ให้ตรง
- **ทัชไม่ตอบ** → เช็คว่า `ts.begin(touchSPI)` ใช้ HSPI + pin 25/33/32/39/36 ถูก
- **ปลั๊กไม่ขยับ** → ทดสอบ local control ด้วย tinytuya บน PC ก่อน (ยืนยัน id/key/DP/version=3.3), เช็ค `TUYA_DP`
- **`mbedtls/aes.h` not found** → ใช้ ESP32 core (มากับ mbedtls) ไม่ใช่ AVR

## ชั้นป้องกันของสถานี (ตัวนี้คือชั้น 4 + มี manual)
1. ปิด router scheduled-reboot 03:00 · 2. เดินสาย LAN เข้า Pi · 3. internal HW watchdog (CPU lockup)
4. **ESP32 display watchdog (ตัวนี้)** — auto จับ full hang + ปุ่มกด reset มือได้ทุกเมื่อ
