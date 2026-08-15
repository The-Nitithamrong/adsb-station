# adsb-sightings-api

API อ่าน **แคตตาล็อกเที่ยวบินที่สถานีจับได้** — 1 แถวต่อ (วัน UTC, เที่ยวบิน, ลำ) พร้อมทะเบียนและเวลาที่เห็นครั้งแรก

```
Pi: flight_watcher ──เขียน──> SQLite sightings (reg=NULL, reg_state=0)
                                    │
    reg_lookup (timer 10 นาที) ─────┤ เติมทะเบียนจาก hex → reg_state=1/2
                                    │
    outbox (timer 10 นาที) ─────────┴─> Cloudflare D1 `sightings`
                                                  │
                                        Worker ตัวนี้ ──> GET /sightings
```

**ทำไมทะเบียนถึงไม่ได้มากับ ADS-B**: บนอากาศมีแค่ callsign (`THA476`) กับ ICAO 24-bit hex
(`8801f2`) เท่านั้น ทะเบียน (`HS-TKF`) ต้อง lookup จาก hex อีกที (`flightwatch/reg_lookup.py`)
→ `registration` เป็น `null` ได้ แปลว่าหาไม่เจอในฐานทะเบียน ไม่ใช่ว่ายังไม่ได้หา
(แถวที่ยังหาไม่เสร็จจะไม่ถูกส่งขึ้น D1 เลย)

## Endpoints

### `GET /sightings`

| param | ตัวอย่าง | ความหมาย |
|---|---|---|
| `date` | `2026-08-10` | วัน UTC (ตรงตัว) |
| `from` / `to` | `2026-08-01` | ช่วงวัน UTC |
| `flight` | `THA476` | callsign (case-insensitive) |
| `reg` | `HS-TKF` | ทะเบียน (case-insensitive) |
| `hex` | `8801f2` | ICAO 24-bit |
| `station` | `Arin` | สถานี (เผื่ออนาคตมีหลายตัว) |
| `limit` | `200` | default 200, สูงสุด 1000 |
| `offset` | `0` | สำหรับหน้าถัดไป |
| `order` | `asc` | เรียงตามเวลา, default `desc` |

```bash
curl "https://adsb-sightings-api.<subdomain>.workers.dev/sightings?date=2026-08-10&limit=3"
```

```json
{
  "count": 3, "limit": 3, "offset": 0, "has_more": true,
  "results": [
    { "flight_number": "THA476",
      "registration": "HS-TKF",
      "first_seen_utc": "2026-08-10T13:07:42Z",
      "first_seen_ts": 1786000062,
      "day": "2026-08-10",
      "hex": "8801f2",
      "station": "Arin" }
  ]
}
```

`has_more` มาจาก "ได้แถวเต็ม limit พอดี" ไม่ได้ยิง `COUNT(*)` ซ้ำ (D1 คิดเงินตามแถวที่อ่าน)
— หน้าถัดไปใช้ `offset` เพิ่มไปทีละ `limit`

### `GET /health`
`{"ok": true}` — ไม่แตะ D1

## Deploy

```bash
cd api/sightings-worker

# 1. ชี้ไป D1 ตัวเดิมที่ outbox ส่งขึ้นไป (id เดียวกับ D1_DATABASE_ID ใน /etc/fr24-watchdog.env)
npx wrangler d1 list                       # เอา database_id มาใส่ใน wrangler.toml
npx wrangler d1 execute adsb --remote --file=schema.sql     # สร้างตาราง (ครั้งเดียว)

# 2. (optional) ล็อกด้วย bearer token — ไม่ตั้ง = ใครก็อ่านได้
npx wrangler secret put API_KEY

# 3. deploy
npx wrangler deploy
```

**ต้องสร้างตารางใน D1 ก่อน** ถึงจะให้ Pi เริ่มส่ง ไม่งั้น `outbox` จะ log ว่าส่ง `sightings` ไม่สำเร็จทุก
10 นาที (ไม่กระทบ `events`/`tracks` — outbox กันไว้ทีละ table แล้ว) แถวที่ส่งไม่ผ่านจะคิวไว้และตามไปเอง
เมื่อสร้างตารางเสร็จ ไม่มีข้อมูลหาย

## หมายเหตุ

- Worker นี้ **อ่านอย่างเดียว** ฝั่งเขียนคือ `outbox.py` ที่ยิง D1 REST API ด้วย `D1_API_TOKEN` ตรง ๆ
  (แยกสิทธิ์กัน: token เขียนไม่เคยออกจาก Pi)
- ค่าที่ผู้เรียกส่งมาเข้าเป็น bound parameter ทั้งหมด ไม่มีการต่อ string ลง SQL
- เปิด CORS `*` ไว้ให้เว็บแอปเรียกจากเบราว์เซอร์ได้ — ถ้าตั้ง `API_KEY` แล้วอย่าฝัง key ในหน้าเว็บ
  ให้ proxy ผ่าน backend แทน
