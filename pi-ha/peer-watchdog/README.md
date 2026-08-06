# peer-watchdog (Pi#2)

Pi#2 (กล่อง Home Assistant) เฝ้า **Pi#1** (สถานี ADS-B) ผ่าน MQTT แล้วกู้ให้เมื่อเงียบ —
โดยไต่ระดับ action จากเบาไปหนัก (soft → ssh → ตัดไฟ).

## ทำไมต้องมี ในเมื่อ Pi#1 มี `fr24-watchdog` อยู่แล้ว
`fr24-watchdog` เป็น **software watchdog บน Pi#1 เอง** — ถ้า Pi#1 แฮงก์ทั้งเครื่อง
(kernel lockup / green-LED-stuck) มันตายไปพร้อมกัน กู้ dongle ไม่ได้. peer-watchdog อยู่
**คนละเครื่อง** → เห็นความเงียบและสั่ง reboot/ตัดไฟข้ามเครื่องได้. ทำงานคู่กับ:

- **fr24-watchdog (Pi#1)** — กู้ dongle ระดับแอป (L1 restart → L2 uhubctl) เมื่อ Pi#1 ยังปกติ.
- **ESP32 watchdog** — backstop **ฮาร์ดแวร์** ที่ไม่พึ่ง MQTT เลย (ping TCP:22 → ตัดไฟ). เก็บไว้
  เป็นชั้นสุดท้าย เผื่อ Pi#2/MQTT เองก็ล่ม.

สามชั้นนี้ไม่ทับซ้อนกัน — คนละจุดล้ม.

## หลักการ: วัดจาก "ความเงียบ" ไม่ใช่ health flag
Pi#1 (health-agent) ส่ง heartbeat ทุก 30s ไป `fleet/pi-adsb/health`. peer-watchdog ดู **อายุ
heartbeat ล่าสุด** (stale) ไม่ใช่ค่า flag — เพราะ flag ที่ยัง "ok" ตอน dongle แฮงก์คือบั๊ก
21 ชม.เดิม. เงียบนานขึ้น = ยกระดับ:

| stage | เงียบตั้งแต่ | action |
|---|---|---|
| HEALTHY | < 120s | ปกติ |
| L0 log | ≥ 120s (`HEALTH_STALE_S`) | บันทึกเฉยๆ |
| L1 soft | ≥ 180s (`SOFT_RESTART_S`) | ส่ง MQTT `cmd=restart-services` ให้ Pi#1 restart เอง |
| L2 ssh | ≥ 300s (`SSH_ACTION_S`) | ssh restart-services → (ยังเงียบ) ssh reboot |
| L3 power | ≥ 600s (`POWER_CYCLE_S`) | **ตัดไฟ** ผ่าน HA webhook — *เฉพาะเมื่อผ่าน guard* |

heartbeat กลับมา → รีเซ็ตเป็น HEALTHY อัตโนมัติ. แต่ละ stage ยิง action **ครั้งเดียว**ต่อการ
เงียบหนึ่งรอบ (ยกระดับใหม่เท่านั้น ไม่รัวทุก tick).

## Guard ก่อนตัดไฟ (L3) — ข้อสำคัญที่สุด
> ถ้า Pi#1 ยัง **ping ได้** หรือ **ssh:22 ยังเปิด** = ปัญหาเป็น *software* → ตัดไฟมีแต่ทำให้แย่ลง
> (write ค้าง → SD/FS พัง). ตัดไฟเฉพาะตอน **เงียบสนิททุกช่องทาง**.

`may_power_cycle()` ต้องผ่านครบ: ไม่อยู่ maintenance · ไม่ติด cooldown · **ping ตาย** ·
**ssh/tcp:22 ตาย** · ยังไม่เกินโควตา 2 ครั้ง/24 ชม. · เว้นขั้นต่ำ 15 นาทีจากครั้งก่อน.
ตัดไม่สำเร็จ → เข้า cooldown 6 ชม. (alert-only).

โควตา (reboot 4/วัน, power-cycle 2/วัน) เก็บใน `/var/lib/fleet/state.json` — restart watchdog
ไม่ล้างโควตา (กัน loop ตัดไฟรัวๆ).

## Maintenance flag (กัน watchdog ยิงตอน deploy)
ตอน deploy/รีสตาร์ตบริการเอง ให้ publish retained ไป `fleet/pi-adsb/maintenance`:

```
{"until":"2026-08-06T12:34:00Z","reason":"deploy a1b2c3d"}
```

watchdog พักจนถึงเวลา `until` แล้ว **หมดอายุเอง** (ไม่ค้างปิด watchdog ถาวร). เคลียร์ด้วยการ
publish payload ว่างทับ topic เดิม.

## ตัวแปร / topic
ค่าคงที่ (topic + timing + limit) เป็น single source of truth ใน
[`shared/pylib/fleet_mqtt.py`](../../shared/pylib/fleet_mqtt.py) — Pi#1 และ Pi#2 import ตัวเดียวกัน
จึงไม่ drift. มี mirror อ่านง่ายที่ [`shared/contracts/mqtt.yaml`](../../shared/contracts/mqtt.yaml).

## ติดตั้ง (Pi#2) — bootstrap ครั้งเดียว
```bash
sudo apt install -y git mosquitto-clients      # mosquitto_pub/sub
git clone https://github.com/iamkkn/adsb-station /home/pi/adsb-station   # user pi

cd /home/pi/adsb-station
sudo cp pi-ha/peer-watchdog/config.env.example /etc/fleet-peer-watchdog.env
sudo nano /etc/fleet-peer-watchdog.env         # เติม broker/ssh/HA webhook/TG — DRY_RUN=1 ไว้ก่อน

sudo cp pi-ha/systemd/peer-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now peer-watchdog
journalctl -u peer-watchdog -f                 # ดู ladder เดินใน DRY-RUN
```

## ต่อ git (GitOps auto-deploy) — ทำครั้งเดียว แล้ว pull เองตลอด
เหมือน Pi#1: ติดตั้ง `pi-ha-autoupdate` (systemd timer) ให้ Pi#2 ดึง `origin/main` เองทุก ~10 นาที
แล้ว sync unit + restart `peer-watchdog` เมื่อ `pi-ha/` หรือ `shared/` เปลี่ยน. หลังจากนี้แก้โค้ด →
merge เข้า main → Pi#2 อัปเดตเอง **ไม่ต้อง SSH**:

```bash
cd /home/pi/adsb-station
sudo install -m 755 pi-ha/deploy/pi-ha-autoupdate.sh /usr/local/bin/pi-ha-autoupdate.sh
sudo cp pi-ha/systemd/pi-ha-autoupdate.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-ha-autoupdate.timer
journalctl -u pi-ha-autoupdate -f              # ดูรอบ pull
```

หมายเหตุ:
- `peer_watchdog.py` รันจาก repo (import `shared/pylib` ผ่าน sys.path) → `git pull` อัปเดตโค้ดให้
  ตรงๆ, autoupdate แค่ `systemctl restart peer-watchdog` ให้.
- unit file (`.service`/`.timer`) `git pull` **ไม่**อัปเดต `/etc/systemd/system/` ให้ — autoupdate
  `cp` + `daemon-reload` ให้เมื่อ `pi-ha/systemd/` เปลี่ยน (เหมือน GOTCHA ของ Pi#1).
- service/timer ใหม่ใน `pi-ha/systemd/` ที่ยัง `disabled` → autoupdate `enable --now` ให้เอง.
  อยากปิดตัวไหนถาวรใช้ `sudo systemctl mask <unit>`.
- repo ต้องสะอาด (ไม่มี local edit) ไม่งั้น `merge --ff-only` ข้าม — อย่าแก้ไฟล์บน Pi#2 ตรงๆ.

### SSH forced-command ฝั่ง Pi#1 (สำหรับ L2)
สร้าง key คู่นึงบน Pi#2 (`ssh-keygen -f ~/.ssh/fleet_id`) แล้วใส่ public key ใน Pi#1
`~arin/.ssh/authorized_keys` โดยผูก forced-command ให้รับแค่คำสั่งที่อนุญาต — key รั่วก็สั่งอื่นไม่ได้:

```
command="/usr/local/bin/fleet-cmd",no-port-forwarding,no-x11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... peer-watchdog
```

`fleet-cmd` (บน Pi#1) อ่าน `$SSH_ORIGINAL_COMMAND` แล้วยอมเฉพาะ `restart-services` /
`reboot` / `status` เท่านั้น. (สคริปต์ฝั่ง Pi#1 อยู่ในงาน Pi#1 — ยังไม่รวมในโฟลเดอร์นี้.)

## เปิดของจริง
เมื่อพิสูจน์แล้วว่า L0→L3 เดินถูกใน DRY-RUN (ดู Telegram + `journalctl`): ตั้ง `DRY_RUN=0` ใน
`/etc/fleet-peer-watchdog.env` แล้ว `systemctl restart peer-watchdog`.

## ⚠️ ข้อควรระวัง
- **Pi#2 ต้องเสียบไฟคนละปลั๊กกับ Pi#1** — ปลั๊กที่ L3 ตัดต้องเป็นของ Pi#1 เท่านั้น ไม่งั้นตัดแล้ว
  Pi#2 (ตัว watchdog เอง) ตายด้วย.
- Mosquitto ควรอยู่บน **Pi#2** (ไม่ใช่ Pi#1) — ไม่งั้น Pi#1 ล่มแล้ว broker ก็ล่ม มองไม่เห็นความเงียบ.
- L3 ตอนนี้ยิงผ่าน **HA webhook** (HA ทำ off→delay→on) เพราะ Tuya local key ยังติด. ได้ local key
  เมื่อไหร่ค่อยสลับ `tuya_power.cycle()` เป็น tinytuya (local, ไม่พึ่ง cloud) — ไม่ต้องแก้ที่อื่น.
