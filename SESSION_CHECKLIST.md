# Session start checklist — adsb-station

เช็คลิสต์ตอนเริ่มเข้ามาทำงานกับสถานี (VTBD feeder, Pi 5 `Arin`, user `arin`).
รันบน Pi ผ่าน SSH. ทำจากบนลงล่าง — เจอ ❌ ตรงไหน แก้ก่อนค่อยไปต่อ.

## 0. Sync โค้ด (deploy model = Pi รัน `git pull` เท่านั้น)
```bash
cd ~/adsb-station && git pull
# ห้าม hand-edit ไฟล์บน Pi — แก้ใน repo แล้ว pull ลงมา
```

## 1. Health ตัวจริง = ข้อมูลไหลจริง (ไม่ใช่ service "up")
```bash
# นับ ^MSG บน 30003 ภายใน 10 วิ — > 0 = ดี, 0 = ดongle ค้าง (21-hour silent-failure bug)
timeout 10 nc localhost 30003 | grep -c '^MSG'
```
> `fr24feed-status` ขึ้น "up" ได้ทั้งที่ดongle ค้าง — อย่าเชื่อ. นับ MSG เท่านั้น.

## 2. Service ทั้ง 3 ตัว
```bash
systemctl status fr24-watchdog.timer flight-watcher pixoo --no-pager
journalctl -u flight-watcher -n 20 --no-pager   # ดู THA inbound ล่าสุด
journalctl -u fr24-watchdog -n 20 --no-pager    # ดูว่ามี escalation (L1/L2) ไหม
```

## 3. USB dongle (RTL2832U — USB ตัวเดียวในระบบ)
```bash
lsusb | grep RTL2832U     # ต้องเจอ
uhubctl -l 3 -p 2         # port ที่ watchdog L2 จะ power-cycle
```
> ถ้าไม่เจอ dongle: watchdog ควรจัดการเอง (L2 `uhubctl -l 3 -p 2 -a cycle`).
> SSH/network รอดเพราะ Pi 5 network ไม่ได้อยู่บน USB.

## 4. Ports (localhost)
| Port | อะไร |
|---|---|
| 30003 | SBS text (health + flight_watcher อ่านตรงนี้) |
| 30005 | Beast |
| 8754  | fr24 status |

## 5. Test manual ก่อน deploy โค้ดใหม่เสมอ
```bash
python3 flightwatch/adsb_view.py       # ตารางเครื่องบินสด
python3 flightwatch/flight_watcher.py  # THA inbound VTBS + ETA<=30m
```

## 6. หลังแก้โค้ด → restart service ที่เกี่ยว
```bash
cd ~/adsb-station && git pull
sudo systemctl restart flight-watcher   # หรือ service ที่แก้
```

## Guardrails (อย่าลืม)
- **Secrets** อยู่ที่ `/etc/fr24-watchdog.env` เท่านั้น (`TG_API`, `TG_CHAT`, `HC_URL`) — ห้าม commit `.env`/`*.db`.
- Edge scripts ใช้ Python stdlib อย่างเดียว (ไม่มี pip dep).
- **GOTCHA:** `nc | awk` เสียข้อมูลเพราะ buffering — อ่าน socket ทีละบรรทัดในโค้ด (adsb_view.py / flight_watcher.py ทำแบบนี้อยู่แล้ว อย่า "ทำให้ง่าย" กลับไปเป็น pipe).
- Watchdog escalation: L1 restart → L2 `uhubctl` power-cycle → L3 Telegram alert.

## จบ session
```bash
git status              # อย่าลืม commit + push งานที่แก้ (container/Pi ไม่ sync ให้อัตโนมัติ)
```
