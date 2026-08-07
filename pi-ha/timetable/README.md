# ตารางเรียน kiosk (Pi#2)

จอนักเรียนบน Pi#2: **week view 7 วัน × แกนเวลา 08:00–20:00** ดึงจาก **Google Calendar (ICS)** →
วาง event ตามเวลาจริง (ไม่ใช่คาบตายตัว เพราะเวลาเรียน vary). Chromium kiosk เปิดเต็มจอ.

- `kiosk_server.py` — stdlib ล้วน: ดึง ICS (private secret address) ทุก ~15 นาที → parse VEVENT
  (+ ขยาย RRULE weekly/daily ของสัปดาห์นี้) → render week view. ไฮไลต์วันนี้ + เส้นเวลาปัจจุบัน +
  นาฬิกาสด. สีต่อวิชา = hash ชื่อ event. emoji ในชื่อ event โชว์ตามจริง. reload เองทุก 1 นาที.

## ICS URL — ใส่ที่ไหน
service อ่าน `TIMETABLE_ICS_URL` (หรือ `GCAL_ICS_URL`) จาก env. unit โหลด `-/etc/adsb-timetable.env`
และ `-/etc/fr24-watchdog.env` (optional ทั้งคู่). วิธีง่ายสุด:
```bash
echo 'TIMETABLE_ICS_URL=https://calendar.google.com/calendar/ical/.../basic.ics' | \
  sudo tee /etc/adsb-timetable.env
sudo chmod 600 /etc/adsb-timetable.env         # เป็น secret (ลิงก์นี้ = อ่านปฏิทินได้)
```
> ICS secret address หาได้ที่: Google Calendar → Settings ของปฏิทินนั้น → **Secret address in iCal format**

## ติดตั้ง (Pi#2)
```bash
cd /home/arin/adsb-station && git pull
sudo cp pi-ha/systemd/adsb-timetable.service /etc/systemd/system/
sudo apt install -y fonts-noto-color-emoji     # emoji (ไทยมีอยู่แล้วใน Desktop)
sudo systemctl daemon-reload && sudo systemctl enable --now adsb-timetable
journalctl -u adsb-timetable -n 5 --no-pager   # ต้องเห็น "ดึง ICS สำเร็จ — N events"
```

## ชี้ Chromium kiosk มาที่ตารางเรียน
แก้ `~/.config/labwc/autostart` ให้ URL เป็น `http://localhost:8080`:
```bash
mkdir -p ~/.config/labwc
tee ~/.config/labwc/autostart >/dev/null <<'EOF'
#!/bin/sh
chromium --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 http://localhost:8080 &
EOF
chmod +x ~/.config/labwc/autostart
sudo reboot
```
ต่อจอ → boot ปุ๊บขึ้นตารางเรียนเต็มจอ.

## แก้ตาราง = แก้ที่ Google Calendar
เพิ่ม/ลบ/ย้าย event ในปฏิทิน (แอป/เว็บ) → จอ sync เองภายใน ~15 นาที (หรือ `systemctl restart
adsb-timetable` ให้ดึงทันที). ทำ event เรียนซ้ำ = ใช้ **recurring weekly** (BYDAY) — ตัว server ขยายให้เอง.
ใส่ emoji ในชื่อ event ได้ (เช่น "🔢 คณิตศาสตร์") จะโชว์บนจอ.

## จูน (optional, ใน env / unit)
`TIMETABLE_PORT`(8080) · `TIMETABLE_DAY_START`(8) · `TIMETABLE_DAY_END`(20) · `TIMETABLE_REFRESH_MIN`(15)

## รองรับ / ข้อจำกัด
- **รองรับ:** event เดี่ยว, RRULE `FREQ=WEEKLY`/`DAILY` (+ `BYDAY`,`INTERVAL`,`UNTIL`,`EXDATE`),
  all-day, เวลา TZID/floating/UTC(Z→+7). แสดงสัปดาห์ปัจจุบัน (จันทร์–อาทิตย์).
- **ยังไม่รองรับ:** `FREQ=MONTHLY/YEARLY`, `COUNT` (ใช้ `UNTIL` แทน). เวลาแปลงเป็นไทย (UTC+7) ตายตัว.
- เสิร์ฟ 127.0.0.1 เท่านั้น (จอบนเครื่อง ไม่เปิด network).
