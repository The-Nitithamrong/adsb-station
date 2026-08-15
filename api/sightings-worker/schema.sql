-- ตาราง D1 ปลายทางของ sightings (สร้างครั้งเดียว):
--   npx wrangler d1 execute adsb --remote --file=schema.sql
-- ต้องตรงกับที่ flightwatch/outbox.py ส่งมา: uid, station, day, flight, hex, first_seen_ts,
-- first_seen_utc, reg  (uid = PRIMARY KEY → INSERT OR IGNORE ส่งซ้ำไม่เกิดแถวซ้ำ)
CREATE TABLE IF NOT EXISTS sightings (
  uid            TEXT PRIMARY KEY,   -- "<station>:<day>:<flight>:<hex>"
  station        TEXT,
  day            TEXT,               -- วัน UTC ที่เห็นครั้งแรก "YYYY-MM-DD"
  flight         TEXT,               -- callsign จาก ADS-B เช่น "THA476"
  hex            TEXT,               -- ICAO 24-bit address (ID จริงของลำ)
  first_seen_ts  INTEGER,            -- epoch วินาที
  first_seen_utc TEXT,               -- "YYYY-MM-DDTHH:MM:SSZ"
  reg            TEXT,               -- ทะเบียน เช่น "HS-TKF"; NULL = หาไม่เจอในฐานทะเบียน
  -- สภาพ ณ ตอนเห็นครั้งแรก (ไม่ใช่ค่าล่าสุด) — NULL = ลำนั้นไม่เคยส่งค่านั้นตอนอยู่ในระยะ
  lat            REAL,
  lon            REAL,
  alt_ft         INTEGER,
  gs_kt          INTEGER
);
-- ตารางที่สร้างไว้ก่อนมี 4 คอลัมน์นี้: รันทีละบรรทัด ข้าม error "duplicate column" ได้
-- ALTER TABLE sightings ADD COLUMN lat REAL;
-- ALTER TABLE sightings ADD COLUMN lon REAL;
-- ALTER TABLE sightings ADD COLUMN alt_ft INTEGER;
-- ALTER TABLE sightings ADD COLUMN gs_kt INTEGER;

-- คิวรีที่ API ใช้จริง: กรองตามวัน / เที่ยวบิน / ทะเบียน แล้วเรียงตามเวลา
CREATE INDEX IF NOT EXISTS sightings_day    ON sightings(day);
CREATE INDEX IF NOT EXISTS sightings_flight ON sightings(flight);
CREATE INDEX IF NOT EXISTS sightings_reg    ON sightings(reg);
CREATE INDEX IF NOT EXISTS sightings_ts     ON sightings(first_seen_ts);
