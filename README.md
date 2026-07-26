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

## ทดสอบก่อนรันเป็น service

```bash
python3 flightwatch/adsb_view.py        # ดูข้อมูลสด
python3 flightwatch/flight_watcher.py   # ดู THA inbound + ETA
```

## Config ที่ปรับได้

- `flight_watcher.py`: `DEST_LAT/LON` (VTBS → OPC), `ETA_ALERT_MIN`, `WATCH_PREFIX`
- `pixoo/main.py`: `PIXOO_IP`, `STATUS_F`
