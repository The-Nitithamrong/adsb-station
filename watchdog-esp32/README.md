# ESP32 external watchdog — power-cycle the Pi via a Tuya plug

ชั้นป้องกันสุดท้าย (layer 4) ของสถานี: ESP32 เฝ้า Pi ผ่าน LAN — ถ้า **Pi หายเกิน 15 นาที** (ค้างทั้ง
kernel / WiFi ตายจนเข้าไม่ได้) → สั่ง **ตัด-เปิดไฟปลั๊ก Tuya ที่จ่ายไฟ Pi** (hard reset) ให้ Pi boot ใหม่.

**ทำไมต้องมี**: watchdog ในเครื่อง (systemd `RuntimeWatchdogSec`) กู้ full hang ไม่ได้ — เครื่องแข็งตายไปด้วย.
ตัวนอกที่ตัดไฟได้เอง = ทางเดียวที่กู้ "ไฟเขียวค้าง" โดยไม่ต้องเดินไปถอดปลั๊กเอง. เช็ค LAN ตรงๆ (ไม่พึ่ง cloud).

```
ESP32  ── ทุก 60 วิ: TCP connect Pi:22 ──►  ต่อได้ = Pi ok (รีเซ็ตตัวนับ)
   │                                         ต่อไม่ได้ติดกัน >15 นาที
   └── สั่ง Tuya (local v3.3) OFF → 10s → ON ──►  Pi hard-reboot
```

## ⚠️ ก่อนอื่น — ความปลอดภัย
- **ESP32 ต้องเสียบไฟคนละแหล่งกับ Pi** — ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด (ไม่งั้นตัดไฟตัวเองตาย).
- **ตั้ง power-on state ของปลั๊ก = ON** (แอป Smart Life → ปลั๊ก → Settings → Relay Status / Power-on = ON)
  → Pi ได้ไฟกลับเสมอ แม้ ESP32 รีบูตหรือไฟดับ.
- power-cycle = hard reset (เสี่ยงไฟล์พังเล็กน้อย) → เป็น **ทางสุดท้าย** เท่านั้น. แก้ต้นเหตุ (ปิด router
  scheduled-reboot) + เดินสาย LAN ก่อน; ตัวนี้ไว้กันเคสที่หลุดรอดทุกชั้น.

## สิ่งที่ต้องมี
- ESP32 (WiFi ในตัว) — flash **MicroPython** (esp32 build, มี `cryptolib` + `ntptime`).
- **Pi เสียบปลั๊ก Tuya ที่เป็น v3.3** (v3.4/3.5 ตัวนี้ไม่รองรับ — ใช้ `tinytuya scan` ดู version).
- `device_id` + `local_key` + `ip` ของปลั๊กนั้น (ดูด้านล่าง).

## 1. ดึง local_key ของปลั๊ก (บน PC)
ตั้ง Tuya IoT Cloud project + link บัญชีแอป (ดู README หลักหัวข้อ heartbeat/Outbox สำหรับขั้น IoT project) แล้ว:
```bash
pip install tinytuya
python -m tinytuya wizard      # ได้ devices.json ที่มี id / key / ip / version ครบ
python -m tinytuya scan        # ยืนยัน ip + version บน LAN
```
เอาปลั๊กที่ **version = 3.3** มาใช้ (สลับ Pi ไปเสียบตัวนั้นถ้าจำเป็น).

## 2. ⭐ ทดสอบ local control บน PC ก่อน (de-risk)
พิสูจน์ว่า id/key/ip/DP ถูก **ก่อน** ไปเชื่อ ESP32 — tinytuya เสถียรกว่า พอร์ตนี้ต้อง match มัน:
```python
import tinytuya, time
d = tinytuya.OutletDevice("DEVICE_ID", "192.168.41.177", "LOCAL_KEY")
d.set_version(3.3)
print(d.status())               # เห็น {'dps': {'1': True/False, ...}} → เลข DP ของสวิตช์คือ key ตัวนั้น
d.turn_off(); time.sleep(3); d.turn_on()   # ปลั๊กต้องดับ 3 วิ แล้วติด
```
- ปลั๊ก **ดับ→ติด** = ผ่าน → เอา DP ที่เห็น (มัก `"1"`) ไปใส่ `TUYA_DP`.
- ไม่ขยับ → เช็ค id/key/ip/version ให้ตรงก่อน (อย่าเพิ่งไป ESP32).

## 3. ตั้งค่า + อัปโหลดขึ้น ESP32
```bash
cp config.example.py config.py     # แล้วแก้ config.py: WIFI_*, PI_IP, TUYA_ID/KEY/IP/DP
```
อัปโหลด 3 ไฟล์ขึ้น ESP32 (ใช้ `mpremote` / Thonny / ampy):
```bash
mpremote connect /dev/ttyUSB0 fs cp tuya33.py config.py main.py :
mpremote connect /dev/ttyUSB0 reset
```
`main.py` รันเอง (bootup) — ดู serial log:
```bash
mpremote connect /dev/ttyUSB0 repl     # เห็น "watchdog started — เฝ้า 192.168.41.241"
```

## 4. ทดสอบ end-to-end
- **ทดสอบ trigger ไว**: ลด `DOWN_S = 120` (2 นาที) ชั่วคราวใน config → ทำให้ Pi เข้าไม่ถึง (ถอดสาย/ปิด WiFi
  Pi ชั่วคราว) → รอ ~2 นาที → ESP32 ควร log `power-cycle` แล้วปลั๊กดับ-ติด Pi boot ใหม่.
- ผ่านแล้ว **คืนค่า `DOWN_S = 900`**.

## จูน (config.py)
| ค่า | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `CHECK_S` | 60 | เช็ค Pi ทุกกี่วินาที |
| `DOWN_S` | 900 | เงียบเกินกี่วินาที → power-cycle (15 นาที — เผื่อ router reboot ผ่านไปก่อน) |
| `OFF_SECONDS` | 10 | ตัดไฟค้างกี่วินาที |
| `COOLDOWN_S` | 1500 | หลัง cycle เว้นกี่วินาทีก่อน cycle ใหม่ (กัน loop — ให้ Pi boot+พิสูจน์ตัวก่อน) |
| `PI_PORT` | 22 | พอร์ตที่เช็ค (22=SSH สะท้อน network stack ของ Pi) |

## Troubleshooting
- **ปลั๊กไม่ขยับจาก ESP32 แต่ tinytuya บน PC ได้** → เช็ค `TUYA_DP` (ลองค่าที่เห็นจาก `d.status()`),
  และ version ต้องเป็น 3.3 จริง.
- **`OSError` ตอน connect** → IP ปลั๊กเปลี่ยน (ตั้ง DHCP reservation) หรือปลั๊กหลุด WiFi.
- **ESP32 หลุด WiFi บ่อย** → เช็คสัญญาณ/แหล่งไฟ ESP32; โค้ด reconnect เอง แต่ต้องนิ่งพอ.
- **`ImportError: cryptolib`** → ใช้ MicroPython build ที่มี cryptolib (esp32 official build มีให้).

## ลำดับป้องกันของสถานี (ตัวนี้คือชั้น 4)
1. ปิด root cause (router scheduled-reboot 03:00)
2. เดินสาย LAN เข้า Pi (ตัดปัญหา WiFi re-associate)
3. internal HW watchdog (`RuntimeWatchdogSec`) — จับ CPU lockup เร็ว
4. **ESP32 + Tuya power-cycle (ตัวนี้)** — backstop สุดท้าย จับ full hang / ทุกอย่างที่ชั้น 1-3 พลาด
