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

ทุก endpoint ตัด "วัน" ตาม **เวลาท้องถิ่น** ไม่ใช่ UTC — `tz` default `+07:00`

> **ทำไมต้องมี `tz`**: คอลัมน์ `day` ใน DB เป็นวัน UTC ถ้า match ตรง ๆ คนไทยที่ถามว่า "วันที่ 15"
> จะได้ช่วง 07:00 ของวันที่ 15 ถึง 07:00 ของวันที่ 16 ตามเวลาไทย (เที่ยวบินตี 1–7 โมงเช้าหายไปอยู่วันก่อนหน้า)
> ทุก endpoint จึงแปลง `date`+`tz` เป็นช่วง epoch แล้วค้นด้วย `first_seen_ts` และ
> **ตอบ `window_utc` กลับมาเสมอ** ให้ผู้เรียกตรวจได้ว่าได้ช่วงไหนมาจริง ๆ

พารามิเตอร์ร่วม: `date` · `from`/`to` (ช่วงวัน, รวมวันสุดท้ายด้วย) · `tz` · `airline` (prefix ของ
callsign) · `flight` · `reg` · `hex` · `station` · `limit` (default 1000, สูงสุด 5000)

### `GET /flights` — "วันนั้นมีเที่ยวบินอะไรบ้าง"
1 แถวต่อ 1 เที่ยวบิน (เป็น default ของ `/` ด้วย)
```bash
curl -H "Authorization: Bearer $ADSB_API_KEY" \
  "https://adsb-sightings-api.happytohelp.workers.dev/flights?date=2026-08-15&airline=THA"
```
```json
{
  "tz": "+07:00", "date": "2026-08-15",
  "window_utc": { "from": "2026-08-14T17:00:00Z", "to": "2026-08-15T17:00:00Z" },
  "count": 135,
  "flights": [
    { "flight_number": "THA476", "registration": "HS-TKF", "registrations": ["HS-TKF"],
      "aircraft": 1, "first_seen_utc": "2026-08-15T03:38:44Z",
      "first_seen_local": "2026-08-15 10:38", "first_seen_ts": 1786765124, "sightings": 1 }
  ]
}
```
~1,200 เที่ยว/วัน อยู่ใน `limit` default → ขอทั้งวันได้ในรอบเดียว ไม่ต้องไล่ paginate

### `GET /aircraft` — 1 แถวต่อ 1 ลำ + เที่ยวที่มันบิน
`?transit=1` = เอาเฉพาะลำที่บินมากกว่า 1 เที่ยวในช่วงนั้น (ลงกรุงเทพแล้วบินออกเป็นอีกเที่ยว)
```json
{ "hex": "880456", "registration": "HS-ABV",
  "flight_numbers": ["AIQ3511", "AIQ3225"], "flights": 2,
  "first_seen_utc": "2026-08-15T03:38:40Z", "last_seen_utc": "2026-08-15T04:38:16Z",
  "span_min": 59 }
```
**อ่าน `span_min` ด้วยเสมอ**: turnaround จริงกินเวลาเป็นสิบนาทีขึ้นไป (ตัวอย่างนี้ 59 นาที)
ส่วน `span_min` ใกล้ 0 = ลำเดียวรายงานสอง callsign ในวินาทีเดียวกัน ซึ่งเป็นไปไม่ได้ →
เป็นสัญญาณรบกวน (ADS-B bit error ทำให้ข้อความของอีกลำหลุดมาเป็น hex นี้) ไม่ใช่ transit

### `GET /days` — นับต่อวัน
ไว้ทำตัวเลือกวันโดยไม่ต้องดึงข้อมูลทั้งวันมานับเอง
```json
{ "days": [ { "date": "2026-08-15", "sightings": 148, "flights": 135, "aircraft": 132 } ] }
```

### `GET /sightings` — แถวดิบ
เหมือน `/flights` แต่ไม่ยุบ 1 แถว/เที่ยว และมีพิกัด/ความสูง/ความเร็ว ณ ตอนเห็นครั้งแรกครบ
รองรับ `offset` + `has_more` สำหรับดึงทีละหน้า

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
