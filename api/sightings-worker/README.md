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

## สถานะ: deploy แล้ว

```
https://adsb-sightings-api.happytohelp.workers.dev
```

ตาราง `sightings` ใน D1 `adsb` สร้างแล้ว และ `API_KEY` ตั้งเป็น secret แล้ว → **ต้องส่ง bearer เสมอ**
(key อยู่นอก repo — ถ้าหายให้ตั้งใหม่ด้วยขั้นตอน "หมุน key" ข้างล่าง)

```bash
curl -H "Authorization: Bearer $ADSB_API_KEY" \
     "https://adsb-sightings-api.happytohelp.workers.dev/sightings?limit=5"
```

## Deploy / แก้แล้ว deploy ใหม่

`database_id` **ไม่ได้ commit** เพราะ repo นี้ public — `wrangler.toml` ที่อยู่ใน repo เป็น placeholder
ให้ทำ config ตัวจริงไว้ในเครื่อง (gitignore ครอบ `api/*/wrangler.local.toml` ไว้แล้ว):

```bash
cd api/sightings-worker
npx wrangler d1 list                                    # เอา uuid ของ database ชื่อ adsb
sed 's/PUT-D1-DATABASE-ID-HERE/<uuid>/' wrangler.toml > wrangler.local.toml

npx wrangler d1 execute adsb --remote --file=schema.sql  # ครั้งแรกครั้งเดียว (idempotent)
npx wrangler deploy --config wrangler.local.toml
```

หมุน key: `npx wrangler secret put API_KEY --config wrangler.local.toml`
(ลบ secret ทิ้ง = กลับไปเปิดอ่านสาธารณะ — โค้ดเช็ค `env.API_KEY` ว่ามีค่าไหมเท่านั้น)

<details>
<summary>ไม่มี wrangler / npm ใช้ไม่ได้ — deploy ผ่าน REST API ตรง ๆ ก็ได้</summary>

```bash
ACC=<account_id>; TOK=<api_token>; D1=<database_uuid>
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACC/workers/scripts/adsb-sightings-api" \
  -H "Authorization: Bearer $TOK" \
  -F "metadata={\"main_module\":\"index.js\",\"compatibility_date\":\"2025-01-01\",\"bindings\":[{\"type\":\"d1\",\"name\":\"DB\",\"id\":\"$D1\"}]};type=application/json" \
  -F "index.js=@src/index.js;type=application/javascript+module"

curl -X PUT ".../workers/scripts/adsb-sightings-api/secrets" -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"name":"API_KEY","text":"<key>","type":"secret_text"}'
curl -X POST ".../workers/scripts/adsb-sightings-api/subdomain" -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{"enabled":true}'
```
หมายเหตุ: อัปโหลดสคริปต์ทับ **ไม่ลบ secret** (secret เป็น setting ของ script ไม่ใช่ของ bundle)
</details>

**ต้องมีตารางใน D1 ก่อน** ถึงจะให้ Pi เริ่มส่ง ไม่งั้น `outbox` จะ log ว่าส่ง `sightings` ไม่สำเร็จทุก
10 นาที (ไม่กระทบ `events`/`tracks` — outbox กันไว้ทีละ table แล้ว) แถวที่ส่งไม่ผ่านจะคิวไว้และตามไปเอง
เมื่อสร้างตารางเสร็จ ไม่มีข้อมูลหาย

## หมายเหตุ

- Worker นี้ **อ่านอย่างเดียว** ฝั่งเขียนคือ `outbox.py` ที่ยิง D1 REST API ด้วย `D1_API_TOKEN` ตรง ๆ
  (แยกสิทธิ์กัน: token เขียนไม่เคยออกจาก Pi)
- ค่าที่ผู้เรียกส่งมาเข้าเป็น bound parameter ทั้งหมด ไม่มีการต่อ string ลง SQL
- เปิด CORS `*` ไว้ให้เว็บแอปเรียกจากเบราว์เซอร์ได้ — ถ้าตั้ง `API_KEY` แล้วอย่าฝัง key ในหน้าเว็บ
  ให้ proxy ผ่าน backend แทน
