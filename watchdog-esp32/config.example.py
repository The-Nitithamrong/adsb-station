# config.example.py — คัดลอกเป็น config.py แล้วเติมค่า
# (config.py ไม่ขึ้น repo — อยู่ใน .gitignore เพราะมี wifi pass + tuya local_key)

# --- WiFi (วงเดียวกับ Pi + ปลั๊ก Tuya) ---
WIFI_SSID = "Arin II"
WIFI_PASS = "<wifi password>"

# --- Pi ที่จะเฝ้า ---
PI_IP   = "192.168.41.241"
PI_PORT = 22            # พอร์ตที่เช็ค (22=SSH — เปิดเสมอถ้า network stack ของ Pi ยังมีชีวิต)

# --- ปลั๊ก Tuya ที่จ่ายไฟ Pi (v3.3, จาก tinytuya scan/wizard) ---
# ตั้ง DHCP Reservation ให้ IP นิ่ง + ตั้ง power-on state = ON ในแอป Smart Life
TUYA_IP  = "192.168.41.177"
TUYA_ID  = "eb101d70fa0bb4f8cfojwd"           # device_id (จาก scan)
TUYA_KEY = "<local_key 16 ตัว จาก `tinytuya wizard`>"
TUYA_DP  = "1"          # DP ของสวิตช์หลัก — ปลั๊กส่วนใหญ่ = "1" (เช็คด้วย tinytuya ถ้าไม่แน่ใจ)

# --- จูน (วินาที) ---
CHECK_S     = 60        # เช็ค Pi ทุกกี่วินาที
TCP_TIMEOUT = 5         # timeout ต่อการเช็ค 1 ครั้ง
DOWN_S      = 900       # Pi เงียบเกินกี่วินาที → power-cycle (900 = 15 นาที)
OFF_SECONDS = 10        # ตัดไฟค้างกี่วินาทีก่อนเปิดใหม่
COOLDOWN_S  = 1500      # หลัง cycle เว้นกี่วินาทีก่อนยอม cycle ใหม่ (1500 = 25 นาที — ให้ Pi boot+ส่ง heartbeat ทัน)
