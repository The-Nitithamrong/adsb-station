# ESP32 room panel — ปุ่มไฟในห้อง + screensaver อากาศ + Pi watchdog (CYD2USB / ESP32-2432S028Rv3)

จอทัชบนบอร์ด **Sunton ESP32-2432S028Rv3 ("CYD2USB", 2 USB ports)** — จอ 2.8" **ST7789** 240×320 +
touch XPT2046. เดิมเป็น watchdog อย่างเดียว ตอนนี้หน้าจอเป็น **panel เปิด/ปิดไฟในห้อง** ส่วน
**watchdog ยังทำงานเบื้องหลังเหมือนเดิมทุกอย่าง** (ชื่อโฟลเดอร์จึงยังเป็น `watchdog-esp32-display`).

| หน้า | โชว์อะไร | เข้ายังไง |
|---|---|---|
| **PANEL** | ปุ่มไฟ 1–6 ปุ่ม สีเหลือง = เปิด · เข้ม = ปิด (มีคำ `ON`/`OFF` กำกับเสมอ) | หน้าหลัก |
| **SAVER** | นาฬิกา + พยากรณ์อากาศ (วันนี้ที่บ้าน · 2 วันถัดไป · ปลายทางของเที่ยวบินถัดไป) | ไม่แตะจอ 45 วิ (`IDLE_MS`) |
| **PI** | Pi OK/DOWN, uptime, ปุ่ม **RESET PI** (ยืนยัน 2 ครั้ง), ปุ่ม **COFFEE** | แตะป้าย `Pi OK >` มุมขวาบนของ PANEL |

- **แตะจอตอนเป็น screensaver = กลับหน้า PANEL อย่างเดียว** ยังไม่สั่งไฟ (กันแตะปลุกจอแล้วไฟดับ).
  ปุ่มเริ่มรับแตะหลัง **ยกนิ้วออกแล้ว ~0.4 วิ** — double-tap ปลุกจอจึงไม่ไปโดนปุ่มที่อยู่ใต้นิ้ว
- **กดค้าง = 1 คำสั่ง** ไม่ยิงซ้ำตามนิ้ว (และ sample ทัชที่หลุด/หลอนตัวเดียวไม่นับเป็นยกนิ้ว/แตะ)
- ปุ่มรู้ **สถานะจริง** ของไฟ: กดจากสวิตช์ผนังหรือแอป จอก็ตามเองภายใน ~4 วิ (`LIGHT_REFRESH_MS`).
  ปลุกจอมาใหม่ ๆ ปุ่มขึ้น `?` แวบหนึ่งจนกว่าจะอ่านค่าจริงเสร็จ — ไม่โชว์ค่าก่อนพักจอเป็นค่าปัจจุบัน
- watchdog: เฝ้า Pi (TCP `:22`) ทุก 30 วิ **ไม่ว่าจออยู่หน้าไหน** — เงียบเกิน **20 นาที** → ยิง webhook power-cycle เอง

> เวอร์ชันไม่มีจอ (MicroPython) อยู่ที่ `../watchdog-esp32/` สำหรับ ESP32 เปล่า

## ⚠️ อ่านก่อน — ฮาร์ดแวร์เฉพาะบอร์ดนี้
- **Rv3 = ST7789 ไม่ใช่ ILI9341** (แยกจาก Rv1/Rv2). ตั้งไว้ใน `platformio.ini` แล้ว
- **USB-C ของบอร์ดไม่มี CC resistor** → สาย C-to-C **ไม่จ่ายไฟเลย** (เหมือนบอร์ดตาย) → **ใช้ micro-USB** หรือ USB-A→C. อย่าเสียบ 2 USB พร้อมกัน
- จอ = HSPI · touch = **VSPI (bus แยก)** — ตั้งในโค้ด/flags แล้ว
- **ESP32 ต้องเสียบไฟคนละแหล่งกับ Pi** (ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด) + ปลั๊กของ Pi ตั้ง **power-on state = ON**

## คุยกับ Home Assistant 2 ทาง — แยกกันโดยตั้งใจ
| งาน | ทาง | ทำไม |
|---|---|---|
| ปุ่มไฟ | **REST API + long-lived token** (`haSwitch` / `haToggle` / `haState` ใน `src/ha_switch.h`) | webhook อ่านสถานะกลับไม่ได้ — ปุ่มที่ไม่รู้ว่าไฟเปิดหรือปิดอยู่ใช้งานไม่ได้ |
| ตัดไฟ Pi | **Webhook เดียว ไม่มี token** (`haWebhook`) | token หมดอายุ/ถูกลบ ต้องเสียแค่ปุ่มไฟ ไม่ใช่ backstop |

เพราะรู้สถานะ ปุ่มจึงสั่งแบบ **ระบุปลายทาง** (`homeassistant.turn_on` / `turn_off` ตามที่จอโชว์) ไม่ใช่ toggle:
ถ้าจอยังโชว์ค่าเก่าเพราะมีคนเพิ่งกดสวิตช์ผนัง toggle จะได้ผลตรงข้ามกับที่ตั้งใจ ส่วนคำสั่งระบุปลายทางสั่งซ้ำได้ไม่มีผลข้างเคียง.
ใช้ toggle เฉพาะตอนสถานะเป็น `?` (ไม่รู้).

อ้างอิง: [HA REST API](https://developers.home-assistant.io/docs/api/rest) (`GET /api/states/<entity_id>`,
`POST /api/services/<domain>/<service>`) · [homeassistant.turn_on](https://www.home-assistant.io/actions/homeassistant.turn_on/)
(ใช้ได้ทุก domain — `light.*` และ `switch.*` ใช้ปุ่มแบบเดียวกันได้)

## ตั้งค่าปุ่มไฟ
1. **สร้าง token** — HA → รูปโปรไฟล์ (มุมซ้ายล่าง) → **Security** → Long-lived access tokens → Create token
   (โชว์ครั้งเดียว, อายุ 10 ปี — [Authentication API](https://developers.home-assistant.io/docs/auth_api/)).
   แนะนำให้สร้างจาก **user แยกที่ไม่ใช่ admin** (Settings → People → Add person → Allow login, ปิด Administrator
   → login เป็น user นั้นแล้วค่อยสร้าง token): token จะแตะการตั้งค่าของ HA ไม่ได้ และยกเลิกได้ด้วยการลบ user เดียว.
   ⚠️ **แต่ยังสั่ง entity ได้ทุกตัวเท่ากับ admin** รวมถึงปลั๊กของ Pi — ใน HA สิทธิ์ admin คุมแค่ "การตั้งค่า"
   ไม่ได้จำกัดว่าสั่งอุปกรณ์ตัวไหนได้ ([People and user configuration](https://www.home-assistant.io/docs/configuration/user-configuration/),
   [Permissions](https://developers.home-assistant.io/docs/auth_permissions/)). ความปลอดภัยจริงจึงมาจาก token อยู่ใน
   `src/config.h` ซึ่ง **ไม่อยู่ใน git** และ HA รับเฉพาะใน LAN.
2. **หา entity_id จริง** — HA → Developer Tools → States → ก๊อป `light.xxx` / `switch.xxx` (ห้ามเดา)
3. ใส่ใน `src/config.h`:
   ```c
   #define HA_BASE   "http://192.168.41.207:8123"     // เครื่องที่รัน HA (Pi#2)
   #define HA_TOKEN  "eyJ..."
   #define LIGHTS_INIT { \
     {"CEILING", "light.bedroom_ceiling"}, \
     {"LAMP",    "switch.desk_lamp"}, \
   }
   ```
   1–6 ปุ่ม จอจัดตารางเอง (1 = เต็มจอ · 2 = 2×1 · 3–4 = 2×2 · 5–6 = 3×2). ชื่อปุ่มเป็น **อังกฤษ/ตัวเลขเท่านั้น**
   (font บนจอไม่มีภาษาไทย); ≤ 7 ตัวอักษรได้ตัวใหญ่ในตาราง 3×2 ยาวกว่านั้นตัวเล็กลงเอง.

⚠️ **ห้ามใส่ปลั๊กของ Pi หรือปลั๊กของเครื่องที่รัน HA ใน `LIGHTS_INIT`** — ปุ่มไฟกดทีเดียวมีผลทันที ไม่มีขั้นยืนยัน
และไม่มีใครเปิดกลับให้ (ต่างจาก RESET PI ที่ HA จับเวลาเปิดคืนเอง).

### จอบอกอะไรเมื่อมีปัญหา
| บนจอ | แปลว่า | แก้ที่ |
|---|---|---|
| ปุ่มขึ้น `NO ENTITY` | HA ตอบ 404 — ไม่มี entity_id นี้ | `LIGHTS_INIT` (ก๊อป id ใหม่จาก Developer Tools → States) |
| ปุ่มขึ้น `?` ดวงเดียว | HA ตอบว่า `unavailable`/`unknown` | ตัวอุปกรณ์/integration ใน HA (เช่น Tuya หลุด) |
| `HA TOKEN?` + ทุกปุ่ม `?` | HA ตอบ 401/403 | `HA_TOKEN` (ถูกลบ/ก๊อปไม่ครบ) → แก้แล้ว flash ใหม่. ค้างอยู่ทั้งที่ token ถูก → **ถอดปลั๊ก ESP32 เสียบใหม่** (ดูใต้ตาราง) |
| `HA OFFLINE` + ทุกปุ่ม `?` | ต่อ HA ไม่ได้เลย | `HA_BASE` / เครื่อง HA ดับ / IP เปลี่ยน — ลองใหม่เองทุก 15 วิ |
| ปุ่มแดง `ERROR` ~1 วิ | สั่งแล้ว HA ไม่รับ | เหมือนสองแถวบน — จอไม่ทำเป็นว่าเปลี่ยนสำเร็จ |
| จุดมุมขวาบนเป็นสีแดง | WiFi ของ ESP32 เองหลุด | `WIFI_*` / router |

**ทำไม `HA TOKEN?` ไม่หายเอง:** token ไม่ผ่าน 2 ครั้งติด (ห่างกัน 1 นาที) จอจะ **เลิกใช้ token จนกว่าจะเปิดเครื่องใหม่**
และไม่ส่งคำสั่งไฟระหว่างนั้น. token ฝังมากับ firmware ถามซ้ำก็ไม่หาย แต่ทุกครั้งที่ผิด HA นับเป็น login ล้มเหลวจาก IP นี้ —
ถ้า HA เปิด [IP banning](https://www.home-assistant.io/integrations/http/) ไว้ IP ของ ESP32 จะโดนแบน **แบบไม่หมดอายุ**
และ webhook ตัดไฟ Pi (ซึ่งไม่ใช้ token) จะโดนไปด้วย. ปุ่มไฟเสียได้ backstop ต้องไม่เสียตาม.

## Screensaver — พยากรณ์อากาศ
ใช้พยากรณ์ **ชุดเดียวกับหน้า WX ของ Pixoo**: `weather/weather_fetch.py` บน Pi เขียน `/run/weather/forecast.json`
(ในไทย = กรมอุตุฯ TMD · ต่างประเทศ = Open-Meteo) แล้ว `adsb-uptime.service` เสิร์ฟต่อที่
`http://<pi>:8099/weather` เป็นข้อความบรรทัดละวัน (ดู `weather_text()` ใน `deploy/uptime_server.py`).
ESP32 ไม่ถือ token ของ TMD และไม่รู้พิกัดบ้าน — Pi เป็นตัวเดียวที่คุยกับแหล่งข้อมูล.

- แถวกลาง = **วันนี้ที่บ้าน** (ESP32 เลือกวันจากนาฬิกาของมันเอง → ข้ามเที่ยงคืนเปลี่ยนวันเอง)
- แถวล่าง = **2 วันถัดไป** + **ปลายทางของเที่ยวบินถัดไป** (`TPE 27/11` = พยากรณ์ของ *วันที่บินถึง* ไม่ใช่วันนี้)
- ไอคอน/สี สูงสุด-ต่ำสุด เป็นชุดเดียวกับ Pixoo (ขยายจาก 7×7 px) · มุมขวาบอกที่มา `TMD` / `OM`
- `--` = ไม่มีพยากรณ์ของวันนั้น (ไม่เดา) · `NO FLIGHT` = ไม่มีเที่ยวบินถัดไปในปฏิทิน
- `WEATHER: NO DATA` = ข้อมูลเก่ากว่า 6 ชม. (เกณฑ์เดียวกับ Pixoo) / ยังดึงไม่ได้ / นาฬิกายังไม่ sync NTP
- บรรทัดล่างสุดยังบอก `Pi OK` / `Pi DOWN 12m (reset at 20m)` / `WiFi DOWN` เสมอ

**ลำดับ deploy:** merge ขึ้น `main` ก่อน → Pi (autoupdate) ได้ route `/weather` เอง → แล้วค่อย flash ESP32.
flash ก่อนก็ไม่พัง — screensaver แค่ขึ้น `WEATHER: NO DATA` จนกว่า Pi จะอัปเดต. เช็คด้วย
`curl -s http://192.168.41.241:8099/weather` (บรรทัดแรกต้องขึ้นต้น `wx1 <ตัวเลขไม่ใช่ 0>`).

## Watchdog — ตัดไฟ Pi ผ่าน HA Webhook เดียว
ESP32 → HTTP POST webhook **1 ครั้ง** → HA automation เดียวทำ **turn_off → delay 60 วิ → turn_on** ครบวงจร.
**HA เป็นคนจับเวลา** ไม่ใช่ ESP32 → ถ้า ESP32 หลุด WiFi หลังยิงไปแล้ว ปลั๊กก็เปิดกลับเอง.

⚠️ **กฎเหล็ก: HA ต้องอยู่คนละเครื่องกับ Pi ที่จะตัด** — ถ้า HA รันบน Pi ตัวที่ถูกตัด: สั่ง OFF → Pi ดับ →
HA ตายตาม → สั่ง ON ไม่ได้ → **Pi ค้างดับถาวร** (ดู `../deploy/homeassistant/MIGRATE_HA_TO_PI2.md`).

automation สำเร็จรูป: `../deploy/homeassistant/automations/pi1-power-cycle.yaml` (วางใน HA → แก้ `webhook_id` +
`entity_id`) แล้วเอา URL `http://192.168.41.207:8123/api/webhook/<webhook_id>` ใส่ `HA_WEBHOOK_CYCLE`.
ทดสอบจาก PC: `curl -X POST http://192.168.41.207:8123/api/webhook/<id>` → ปลั๊กควรดับแล้วติดกลับใน 60 วิ.

**ปุ่ม COFFEE** (หน้า PI): เปิด/ปิด coffee break ของ Pixoo — ยิง `http://<pi>:8099/coffee/toggle`.

## Build — PlatformIO (ไม่ใช่ Arduino IDE)
โครง PlatformIO: `platformio.ini` + `src/`. TFT_eSPI ตั้งผ่าน **build flags** (ไม่มี `User_Setup.h`,
ไม่ใช้ LVGL — วาดด้วย TFT_eSPI ตรงๆ เบา RAM).

1. เปิดโฟลเดอร์ `watchdog-esp32-display/` ใน VS Code + PlatformIO (ต้องเปิดโฟลเดอร์ที่มี `platformio.ini`)
2. `lib_deps` ลงเอง: **TFT_eSPI 2.5.43** + **XPT2046_Touchscreen v1.4**
3. **pin `upload_port`/`monitor_port` ให้ตรงพอร์ต CH340 ของบอร์ด** (uncomment ใน `[env:cyd2usb]`) —
   auto-detect อาจเลือกผิดอุปกรณ์ (เช่นชน CP210x ตัวอื่น). ยืนยัน log ว่าพอร์ตถูกก่อน flash
4. `upload_speed = 115200` (อย่าขึ้น 921600 — CH340 นี้ไม่นิ่ง)

```bash
cp src/config.h.example src/config.h     # ครั้งแรก — แล้วแก้ WIFI_*, HA_*, LIGHTS_INIT, HA_WEBHOOK_CYCLE
```
**มี `config.h` จากรุ่น watchdog อยู่แล้ว:** ไม่ต้องเริ่มใหม่ — เพิ่ม 3 ค่า `HA_BASE`, `HA_TOKEN`, `LIGHTS_INIT`
(ก๊อปบล็อกจาก `config.h.example`) ค่าเดิมใช้ต่อได้หมด. ถ้าลืม build จะหยุดพร้อมบอกว่าขาดตัวไหน.

Build + Upload → Serial Monitor 115200 → จอขึ้น `LIGHTS` + ปุ่ม.

## ทดสอบหลัง flash (ไล่ตามลำดับ)
1. **ปุ่มไฟ**: ปุ่มขึ้น `ON`/`OFF` ตรงกับไฟจริง → แตะ → ไฟเปลี่ยน + ปุ่มเปลี่ยนสี → กดสวิตช์ผนัง → ปุ่มตามเองใน ~4 วิ
2. **Screensaver**: ไม่แตะ 45 วิ → นาฬิกา + อากาศ → แตะ 1 ครั้ง → กลับหน้าปุ่ม โดย **ไฟไม่เปลี่ยน**
   — ลองทั้งแตะสั้น, **กดค้าง 2–3 วิ ตรงตำแหน่งปุ่มไฟ**, และ double-tap: ไฟต้องไม่เปลี่ยนทั้ง 3 แบบ
3. **กดค้าง**: วางนิ้ว "เบา ๆ" ค้างบนปุ่มไฟ 3 วิ (ลองปุ่มมุมจอด้วย) → ไฟเปลี่ยน **ครั้งเดียว**. ถ้ากระพริบกลับ = แรงกดบนจอ
   แกว่งเกินที่กันไว้ → เพิ่ม `RELEASE_SAMPLES` ใน `main.cpp`
4. **ทัชหลอน**: เปิด Serial Monitor ทิ้งไว้หน้า screensaver สักชั่วโมงโดยไม่แตะ → ต้อง **ไม่มี** บรรทัด `touch raw=` โผล่เอง
   (ขา touch IRQ/MISO ของบอร์ดนี้คือ GPIO36/39 ซึ่งมี erratum เรื่อง glitch ตอน WiFi ทำงาน — โค้ดกันไว้ด้วยการต้องเห็น
   2 sample ติดกัน แต่ยังไม่เคยวัดบนบอร์ดจริง)
5. **หน้า PI** (`DRY_RUN 1` ไว้ก่อน): แตะ `Pi OK >` → RESET PI → TAP AGAIN (ส้ม) → แตะซ้ำใน 3 วิ → countdown
   ขึ้น "DRY RUN - no real cut" → ค่อยตั้ง `DRY_RUN 0`
6. **auto** (ถ้าอยากเห็นกับตา): ลด `DOWN_MS` เป็น `120000UL` ชั่วคราว → ทำ Pi เข้าไม่ถึง → รอ 2 นาที → power-cycle
   เอง → **คืน `1200000UL`**

## ทดสอบโดยไม่ต้องมีบอร์ด — `sim/`
`sim/` รัน `src/main.cpp` **ตัวจริง** บน PC กับฮาร์ดแวร์ปลอม (จอ/ทัช/WiFi/HTTP) ด้วยนาฬิกาจำลอง:
8 ชั่วโมงจำลองจบในไม่ถึงวินาที. ต้องมีแค่ `g++`.
```bash
make -C sim test      # unit test + scenario ทั้งหมด (CI รันชุดเดียวกันนี้)
make -C sim shots     # + ภาพหน้าจอทุกหน้าเป็น PNG ใน sim/build/shots/ (ต้องมี python3 + Pillow)
```
สิ่งที่มันยืนยันทุกครั้งที่โค้ดเปลี่ยน:
- **watchdog**: Pi เงียบ → webhook ที่ 20.06 / 46.16 / 72.26 นาที (เท่ากับ firmware รุ่น watchdog ล้วน), Pi กลับมาหรือ
  WiFi ของ ESP32 เองหลุด = ไม่ตัด
- **ui**: ใช้งานปกติ, HA ดับ/401/entity ผิด, หน้า PI, screensaver ทุกแบบ, อยู่หน้าพัก = ไม่ถาม HA เลย, Pi ดับแล้วปุ่มไฟยังกดติด
- **touch** (HA ตอบช้า 40–300 ms × sample ทัชหลุด/หลอน ทุกจังหวะ): แตะปลุกจอค้าง/สั่น/double-tap = 0 คำสั่ง,
  กดค้าง = 1 คำสั่ง, ปุ่มกลับของหน้า PI ไม่ทะลุไปโดนปุ่มไฟ, ปลุกแล้วแตะ = สั่งถูกทิศตามค่าที่อ่านใหม่
- **badtoken**: token ผิด ใช้งาน 1 ชม. = login ล้มเหลว 2 ครั้งพอดี แล้ว backstop ยังยิงได้
- **stall**: ปลายทางส่ง header แล้วค้าง → `loop()` ยังกลับมาเสมอ และ watchdog ยังเดิน

stub ตั้งใจให้ "ไม่นิ่ง" เหมือนของจริง — รุ่นแรกนิ่งเกินไป และ bug แตะปลุกจอ → สั่งไฟ ผ่านไปได้จนมีคนอ่านโค้ดเจอ.
ภาพใน `shots/` ใช้ขนาดตัวอักษรจริงของ font บนจอ (ตรวจการล้น/ทับได้) แต่รูปร่างตัวอักษรเป็นของ font บน PC.

`.github/workflows/esp32-build.yml` รัน sim + **build ด้วย PlatformIO จริง** ทุกครั้งที่โฟลเดอร์นี้เปลี่ยน —
firmware นี้ flash ด้วยมือ ไม่มี autoupdate จึงเป็นด่านเดียวก่อนถึงบอร์ด.

## Touch calibration
ค่าในโค้ด (`TOUCH_X_MIN/MAX`, `TOUCH_Y_MIN/MAX` = 338/3676/505/3465) **วัดจริงที่ rotation 1** ของบอร์ดนี้
(ไม่ swap/invert). ถ้าปุ่มกดไม่ตรง แตะจอดู Serial `touch raw=...` แล้วปรับ 4 ค่านั้น.
**เปลี่ยน `setRotation` = ค่าพวกนี้ใช้ไม่ได้ ต้องวัดใหม่** (calibration ผูกกับ rotation).

## Troubleshooting (จาก field notes)
- **สีเพี้ยน** → ตรวจ `-DST7789_DRIVER -DTFT_RGB_ORDER=TFT_BGR -DTFT_INVERSION_OFF` ใน platformio.ini
- **จอไม่ขึ้น** → เช็ค `TFT_BL=21` (active HIGH) + ST7789 driver + สาย micro-USB (ไม่ใช่ C-to-C)
- **ทัชไม่ตอบ/ไม่ตรง** → touch ต้องอยู่ **VSPI** (pin 25/33/32/39/36) + rotation ตรงกับ calibration
- **ปุ่มไฟ** → ดูตาราง "จอบอกอะไรเมื่อมีปัญหา" ข้างบน. ทดสอบ token จาก PC:
  `curl -H "Authorization: Bearer <token>" http://192.168.41.207:8123/api/states/<entity_id>`
- **ปลั๊ก Pi ไม่ขยับตอน RESET** → curl ทดสอบ webhook, เช็ค automation ใน HA ว่า target ปลั๊กถูก + enabled
- **`Pi up -- (info svc?)`** → `adsb-uptime.service` บน Pi ไม่ตอบ (`curl -s http://192.168.41.241:8099/`)
- **flash ผิดอุปกรณ์** → pin `upload_port` เสมอ, ยืนยันพอร์ตก่อน flash

## ตำแหน่งใน pi-fleet — ESP32 คือ backstop สุดท้าย
กู้ Pi#1 มี 3 ชั้นไม่ทับกัน (timing มาจาก `shared/pylib/fleet_mqtt.py` — SSOT เดียว):

| ชั้น | ตัว | อยู่ที่ | ทำเมื่อ | ต้องพึ่ง |
|---|---|---|---|---|
| 1 | `fr24-watchdog` | Pi#1 | กู้ dongle ระดับแอป (L1 restart → L2 uhubctl) | Pi#1 ยังปกติ |
| 2 | `peer-watchdog` | Pi#2 | เงียบ → L1 MQTT → L2 ssh → **L3 ตัดไฟ 600s** + รอ 240s | Pi#2 + broker + MQTT healthy |
| 3 | **ESP32 (ตัวนี้)** | แยกเครื่อง | TCP:22 เงียบ **20 นาที** → ตัดไฟ + ปุ่มมือ | HA (webhook) เท่านั้น — ไม่พึ่ง MQTT/token |

**ทำไม 20 นาที ไม่ใช่ 15:** peer-watchdog (ชั้น 2) ตัดไฟที่ 600s + รอ 240s + Pi boot ~120s ≈ **16 นาที**.
ESP32 ที่ 15 นาทีจะยิง**ซ้อน**ตอน Pi กำลัง boot จากการตัดของชั้น 2. เลื่อนเป็น 20 นาที → ESP32 ยิงเฉพาะเมื่อ
ชั้น 2 ลองครบแล้วเอาไม่อยู่ **หรือ** peer-watchdog/broker เองก็ตาย.
ESP32 **ตั้งใจไม่พึ่ง MQTT** — จะได้กู้ได้แม้ทั้ง fleet ล่ม.

**ให้การตัดไฟของ ESP32 โผล่ใน `fleet/incident`** (โดยไม่ต้องแตะโค้ด ESP32): ใน HA automation ที่รับ
webhook power-cycle เพิ่ม action `mqtt.publish` topic `fleet/incident` ก่อนตัดไฟ (`pi1-power-cycle.yaml` มีให้แล้ว).
แยกว่าใครสั่ง: ถ้า peer-watchdog เป็นคนตัด มันจะ log incident ของตัวเอง**ก่อน**เรียก webhook อยู่แล้ว →
เห็น incident ของ peer-watchdog คู่กับของ HA = ชั้น 2 สั่ง; เห็นแต่ของ HA เดี่ยวๆ = **ESP32 สั่ง**.

### รวมชั้นป้องกันทั้งหมดของสถานี
1. ปิด router scheduled-reboot 03:00 · 2. เดินสาย LAN เข้า Pi · 3. internal HW watchdog (CPU lockup)
4. fr24-watchdog (แอป) · 5. peer-watchdog (Pi#2) · 6. **ESP32 (ตัวนี้)** — backstop + ปุ่มมือทุกเมื่อ
