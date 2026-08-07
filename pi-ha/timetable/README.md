# ตารางเรียน kiosk (Pi#2)

จอนักเรียนบน Pi#2: Python เสิร์ฟหน้าตารางเรียน (วัน × คาบ) → Chromium kiosk เปิดเต็มจอ.
**ไม่ต้อง login, ไม่พึ่ง cloud/Google** — แก้ตารางที่ไฟล์ JSON ในเครื่อง.

- `schedule.json` — ตาราง: `days`, `periods` (คาบ+เวลา), `subjects` (ชื่อ/emoji/สี), `grid` (วิชาต่อคาบต่อวัน)
- `kiosk_server.py` — เสิร์ฟ HTML (stdlib) อ่าน schedule.json สดทุก request: emoji + สีต่อวิชา, นาฬิกาเดินสด,
  **ไฮไลต์วันนี้ + คาบตอนนี้** (คำนวณฝั่ง JS จากเวลาเครื่อง), reload เองทุก 1 นาที (แก้ JSON แล้วเห็นเลย)

## ติดตั้ง (Pi#2)
```bash
cd /home/arin/adsb-station && git pull
sudo cp pi-ha/systemd/adsb-timetable.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now adsb-timetable
curl -s localhost:8080 | head -c 200          # เช็คว่าเสิร์ฟได้ (เห็น <!doctype html>)
```
(หรือรอ pi-ha-autoupdate auto-enable เอง — เป็น daemon ใน pi-ha/systemd)

## ชี้ Chromium kiosk มาที่ตารางเรียน (แทน kid-timetable.pages.dev)
แก้ `~/.config/labwc/autostart` ให้ URL เป็น `http://localhost:8080`:
```bash
mkdir -p ~/.config/labwc
tee ~/.config/labwc/autostart >/dev/null <<'EOF'
#!/bin/sh
chromium --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 http://localhost:8080 &
EOF
chmod +x ~/.config/labwc/autostart
```
แล้ว `sudo reboot` (หรือออก-เข้า session ใหม่) → boot ปุ๊บขึ้นตารางเรียนเต็มจอ.

## แก้ตาราง
แก้ `schedule.json` แล้วเซฟ — จอ reload เองใน ≤1 นาที (หรือรีเฟรช):
- **เปลี่ยนวิชาในช่อง:** แก้ `grid` (แถว = คาบตามลำดับ `periods`, คอลัมน์ = วันตามลำดับ `days`) ใส่ key ของวิชา
- **เพิ่ม/แก้วิชา:** เพิ่มใน `subjects` — `"key": {"name":"...","emoji":"…","color":"#rrggbb"}`
- **เพิ่มวัน (เสาร์/อาทิตย์):** เพิ่มใน `days` + ต่อคอลัมน์ในทุกแถวของ `grid`
- **เปลี่ยนเวลา/คาบ:** แก้ `periods` (`break:true` = คาบพัก โชว์แบบจาง)
- **ชื่อนักเรียน:** ใส่ `student` (โชว์ข้างหัวข้อ)

JSON ผิด → จอแสดง error พร้อมบรรทัดที่พัง (แก้แล้วรีเฟรช).

## หมายเหตุ
- ต้องมีฟอนต์ไทย + emoji บน Pi#2 (RPi OS Desktop มี Noto Sans Thai; emoji ติดตั้งเพิ่มถ้าจำเป็น:
  `sudo apt install -y fonts-noto-color-emoji`)
- เสิร์ฟเฉพาะ localhost (127.0.0.1) — จอบนเครื่องเท่านั้น ไม่เปิดออก network
