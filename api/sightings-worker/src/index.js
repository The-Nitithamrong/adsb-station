/**
 * adsb-sightings-api — อ่านแคตตาล็อกเที่ยวบินที่สถานีจับได้ (D1 table `sightings`)
 *
 * 1 แถว = (เที่ยวบิน, ลำ) ที่เห็นในวันนั้น — เห็นครั้งแรกกี่โมง อยู่ที่ไหน สูงเท่าไร
 * Pi เขียนลง SQLite แล้ว outbox ส่งขึ้น D1 (Worker นี้อ่านอย่างเดียว ไม่รับเขียน)
 *
 *   GET /flights?date=2026-08-15&tz=+07:00[&airline=THA]   ← "วันนั้นมีเที่ยวบินอะไรบ้าง"
 *   GET /aircraft?date=...[&transit=1]                     ← 1 แถว/ลำ + เที่ยวที่มันบิน (ดู transit)
 *   GET /days?from=2026-08-01&to=2026-08-15                 ← นับต่อวัน (ทำตัวเลือกวัน)
 *   GET /sightings?date=...                                 ← แถวดิบ มีพิกัด/ความสูงครบ
 *   GET /health
 *
 * เรื่อง "วัน" ที่สำคัญ: คอลัมน์ `day` ใน DB เป็นวัน **UTC** — ถ้า match ตรง ๆ ผู้ใช้ไทยที่ถามว่า
 * "วันที่ 15" จะได้ช่วง 07:00 ของวันที่ 15 ถึง 07:00 ของวันที่ 16 ตามเวลาไทย ซึ่งไม่ใช่สิ่งที่ถาม
 * (เที่ยวบินตี 1-7 โมงเช้าจะไปโผล่ในวันก่อนหน้า). ทุก endpoint จึงแปลง date+tz เป็นช่วง epoch
 * แล้วค้นด้วย `first_seen_ts` (มี index) แทน และ **ตอบ `window` กลับไปด้วยเสมอ** เพื่อให้ผู้เรียก
 * เห็นกับตาว่าได้ช่วงไหนมา ไม่ต้องเดาเรื่อง timezone
 *
 * auth: ตั้ง secret API_KEY แล้วต้องส่ง `Authorization: Bearer <key>`; ไม่ตั้ง = เปิดอ่านสาธารณะ
 */

const MAX_LIMIT = 5000;          // 1 วันมีราว 1,200 แถว → ขอทั้งวันได้ในรอบเดียว ไม่ต้องไล่ paginate
const DEFAULT_LIMIT = 1000;
const DEFAULT_TZ = "+07:00";     // สถานีอยู่กรุงเทพ ผู้ใช้คิดเป็นเวลาไทย — override ด้วย ?tz= ได้
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_S = 86400;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });
}

/** วินาที → รูปแบบมาตรฐาน "+07:00" / "-05:30" / "Z"
 *  ต้องสร้างจาก offset ที่ตีความได้แล้ว ไม่ใช่สะท้อนสตริงดิบกลับไป: `?tz=+07:00` ที่ไม่ได้ encode
 *  จะถูกถอด `+` เป็นช่องว่างตามมาตรฐาน URL → ค่าที่ผู้เรียกส่งมาถึงเราเป็น " 07:00" (คำนวณถูก
 *  แต่ echo ออกไปหน้าตาเพี้ยน) */
function formatTz(offs) {
  if (offs === 0) return "Z";
  const sign = offs < 0 ? "-" : "+";
  const a = Math.abs(offs);
  const hh = String(Math.floor(a / 3600)).padStart(2, "0");
  const mm = String(Math.floor((a % 3600) / 60)).padStart(2, "0");
  return `${sign}${hh}:${mm}`;
}

/** "+07:00" | "+0700" | "7" | "-05:30" | "Z" | " 07:00" (จาก + ที่ถูกถอด) → วินาที (null = ผิดรูป) */
function tzSeconds(tz) {
  const t = (tz ?? DEFAULT_TZ).trim().toUpperCase();
  if (t === "Z" || t === "UTC") return 0;
  const m = /^([+-]?)(\d{1,2})(?::?(\d{2}))?$/.exec(t);
  if (!m) return null;
  const h = parseInt(m[2], 10);
  const mi = m[3] ? parseInt(m[3], 10) : 0;
  if (h > 14 || mi > 59) return null;
  return (m[1] === "-" ? -1 : 1) * (h * 3600 + mi * 60);
}

/** เที่ยงคืนของวันนั้น "ตามเวลาท้องถิ่น" เป็น epoch (null = วันที่ไม่ถูกต้อง) */
function localMidnight(dateStr, offs) {
  if (!DATE_RE.test(dateStr ?? "")) return null;
  const [y, m, d] = dateStr.split("-").map(Number);
  const utc = Date.UTC(y, m - 1, d) / 1000;
  // Date.UTC ปัดวันที่เกินจริงให้เอง (2026-02-31 → 3 มี.ค.) → เช็คย้อนว่ายังเป็นวันเดิม
  if (new Date(utc * 1000).toISOString().slice(0, 10) !== dateStr) return null;
  return utc - offs;
}

function iso(ts) {
  return new Date(ts * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** เวลาท้องถิ่นแบบอ่านง่าย "YYYY-MM-DD HH:MM" */
function localStr(ts, offs) {
  return new Date((ts + offs) * 1000).toISOString().slice(0, 16).replace("T", " ");
}

function intParam(v, dflt, max) {
  const n = parseInt(v ?? "", 10);
  if (!Number.isFinite(n) || n < 0) return dflt;
  return max !== undefined ? Math.min(n, max) : n;
}

/**
 * query string → ช่วงเวลา + WHERE + ค่า bind
 * ทุกค่าที่ผู้ใช้ส่งมาเป็น bound parameter เสมอ — ไม่มีการต่อ string ลง SQL
 * คืน {error} เมื่อรูปแบบผิด เพื่อให้ตอบ 400 แทนที่จะเงียบ ๆ ให้ผลลัพธ์ผิดวัน
 */
function buildQuery(q) {
  const offs = tzSeconds(q.get("tz"));
  if (offs === null) return { error: "tz ไม่ถูกต้อง — ใช้รูปแบบ +07:00, -05:30, 7 หรือ Z" };

  let from = null;
  let to = null;
  if (q.get("date")) {
    from = localMidnight(q.get("date"), offs);
    if (from === null) return { error: "date ไม่ถูกต้อง — ใช้รูปแบบ YYYY-MM-DD" };
    to = from + DAY_S;
  } else if (q.get("from") || q.get("to")) {
    if (q.get("from")) {
      from = localMidnight(q.get("from"), offs);
      if (from === null) return { error: "from ไม่ถูกต้อง — ใช้รูปแบบ YYYY-MM-DD" };
    }
    if (q.get("to")) {
      const t = localMidnight(q.get("to"), offs);
      if (t === null) return { error: "to ไม่ถูกต้อง — ใช้รูปแบบ YYYY-MM-DD" };
      to = t + DAY_S;                     // to เป็นวันที่ "รวมวันนั้นด้วย" (inclusive)
    }
  }

  const where = [];
  const binds = [];
  if (from !== null) { where.push("first_seen_ts >= ?"); binds.push(from); }
  if (to !== null) { where.push("first_seen_ts < ?"); binds.push(to); }

  const eq = (col, val, tf = (s) => s) => {
    if (val) { where.push(`${col} = ?`); binds.push(tf(val)); }
  };
  eq("flight", q.get("flight"), (s) => s.trim().toUpperCase());
  eq("reg", q.get("reg"), (s) => s.trim().toUpperCase());
  eq("hex", q.get("hex"), (s) => s.trim().toLowerCase());
  eq("station", q.get("station"));
  if (q.get("airline")) {                 // prefix ของ callsign: THA, AIQ, ... (คำถามมักเจาะจงสายการบิน)
    where.push("flight LIKE ?");
    binds.push(`${q.get("airline").trim().toUpperCase()}%`);
  }

  return {
    offs,
    sql: where.length ? ` WHERE ${where.join(" AND ")}` : "",
    binds,
    window: from !== null || to !== null
      ? { from: from === null ? null : iso(from), to: to === null ? null : iso(to) }
      : null,
  };
}

/** ส่วนหัวที่ทุก endpoint ตอบเหมือนกัน — ผู้เรียกตรวจได้ว่าตัวเองได้ช่วงไหนมา */
function envelope(f, q, extra) {
  return {
    tz: formatTz(f.offs),          // รูปแบบมาตรฐานเสมอ ไม่ใช่สตริงดิบที่ผู้เรียกส่งมา
    tz_offset_min: f.offs / 60,    // ตัวเลขล้วน ไว้ให้โค้ดฝั่งผู้เรียกใช้ต่อโดยไม่ต้อง parse
    date: q.get("date") ?? null,
    window_utc: f.window,
    ...extra,
  };
}

/** แถวดิบ 1 แถว/(เที่ยวบิน, ลำ) พร้อมพิกัดตอนเห็นครั้งแรก */
async function sightings(env, q, f) {
  const limit = intParam(q.get("limit"), DEFAULT_LIMIT, MAX_LIMIT);
  const offset = intParam(q.get("offset"), 0);
  const order = q.get("order") === "desc" ? "DESC" : "ASC";   // ไล่ตามเวลาเป็นค่าตั้งต้นสำหรับ "ทั้งวัน"
  const rows = await env.DB.prepare(
    `SELECT day, flight, reg, hex, first_seen_utc, first_seen_ts, station,
            lat, lon, alt_ft, gs_kt
       FROM sightings${f.sql}
      ORDER BY first_seen_ts ${order}
      LIMIT ? OFFSET ?`,
  ).bind(...f.binds, limit, offset).all();

  const results = (rows.results ?? []).map((r) => ({
    flight_number: r.flight,
    registration: r.reg,          // null = ไม่มีในฐานทะเบียน (ADS-B ส่งมาแค่ hex)
    first_seen_utc: r.first_seen_utc,
    first_seen_local: localStr(r.first_seen_ts, f.offs),
    first_seen_ts: r.first_seen_ts,
    day: r.day,                   // วัน UTC ที่ Pi บันทึก (คนละอย่างกับ window ข้างบน)
    hex: r.hex,
    station: r.station,
    lat: r.lat,
    lon: r.lon,
    altitude_ft: r.alt_ft,
    ground_speed_kt: r.gs_kt,
  }));
  return json(envelope(f, q, {
    count: results.length, limit, offset, has_more: results.length === limit, results,
  }));
}

/** 1 แถว/เที่ยวบิน — ตอบคำถาม "วันนั้นมีเที่ยวบินอะไรบ้าง" ตรง ๆ */
async function flights(env, q, f) {
  const limit = intParam(q.get("limit"), DEFAULT_LIMIT, MAX_LIMIT);
  const rows = await env.DB.prepare(
    `SELECT flight,
            MIN(first_seen_ts)         AS first_ts,
            COUNT(*)                   AS sightings,
            COUNT(DISTINCT hex)        AS aircraft,
            GROUP_CONCAT(DISTINCT reg) AS regs
       FROM sightings${f.sql}
      GROUP BY flight
      ORDER BY first_ts
      LIMIT ?`,
  ).bind(...f.binds, limit).all();

  const results = (rows.results ?? []).map((r) => ({
    flight_number: r.flight,
    // ปกติ 1 เที่ยว = 1 ลำ; มากกว่านั้นคือเปลี่ยนเครื่อง (หรือเที่ยวบินเดียวกันคนละวันที่คาบช่วง)
    registration: r.regs ? r.regs.split(",")[0] : null,
    registrations: r.regs ? r.regs.split(",") : [],
    aircraft: r.aircraft,
    first_seen_utc: iso(r.first_ts),
    first_seen_local: localStr(r.first_ts, f.offs),
    first_seen_ts: r.first_ts,
    sightings: r.sightings,
  }));
  return json(envelope(f, q, { count: results.length, limit, flights: results }));
}

/** 1 แถว/ลำ + เที่ยวที่มันบินในช่วงนั้น — ?transit=1 = เอาเฉพาะลำที่บินมากกว่า 1 เที่ยว
 *  (เครื่องที่ลงกรุงเทพแล้วบินออกเป็นอีกเที่ยว จะโผล่ที่นี่พร้อมทั้งสอง callsign) */
async function aircraft(env, q, f) {
  const limit = intParam(q.get("limit"), DEFAULT_LIMIT, MAX_LIMIT);
  const transitOnly = ["1", "true", "yes"].includes((q.get("transit") ?? "").toLowerCase());
  const rows = await env.DB.prepare(
    `SELECT hex,
            MAX(reg)                      AS reg,
            COUNT(DISTINCT flight)        AS n_flights,
            GROUP_CONCAT(DISTINCT flight) AS flights,
            MIN(first_seen_ts)            AS first_ts,
            MAX(first_seen_ts)            AS last_ts
       FROM sightings${f.sql}
      GROUP BY hex
      ${transitOnly ? "HAVING n_flights > 1" : ""}
      ORDER BY first_ts
      LIMIT ?`,
  ).bind(...f.binds, limit).all();

  const results = (rows.results ?? []).map((r) => ({
    hex: r.hex,
    registration: r.reg,
    flight_numbers: r.flights ? r.flights.split(",") : [],
    flights: r.n_flights,
    first_seen_utc: iso(r.first_ts),
    first_seen_local: localStr(r.first_ts, f.offs),
    last_seen_utc: iso(r.last_ts),
    // ช่องว่างระหว่างเที่ยวแรกกับเที่ยวสุดท้าย — เวลา turnaround คร่าว ๆ ของลำที่มา transit
    span_min: Math.round((r.last_ts - r.first_ts) / 60),
  }));
  return json(envelope(f, q, {
    count: results.length, limit, transit_only: transitOnly, aircraft: results,
  }));
}

/** นับต่อวัน (ตัดวันตาม tz) — ใช้ทำตัวเลือกวันโดยไม่ต้องดึงข้อมูลทั้งวันมานับเอง */
async function days(env, q, f) {
  const limit = intParam(q.get("limit"), 400, 1000);
  const rows = await env.DB.prepare(
    `SELECT strftime('%Y-%m-%d', first_seen_ts + ?, 'unixepoch') AS d,
            COUNT(*)                AS sightings,
            COUNT(DISTINCT flight)  AS flights,
            COUNT(DISTINCT hex)     AS aircraft
       FROM sightings${f.sql}
      GROUP BY d
      ORDER BY d DESC
      LIMIT ?`,
  ).bind(f.offs, ...f.binds, limit).all();

  const results = (rows.results ?? []).map((r) => ({
    date: r.d, sightings: r.sightings, flights: r.flights, aircraft: r.aircraft,
  }));
  return json(envelope(f, q, { count: results.length, days: results }));
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    if (request.method !== "GET") return json({ error: "method not allowed" }, 405);

    // ตั้ง secret API_KEY เมื่อไร = บังคับ bearer เมื่อนั้น (ไม่ตั้ง = อ่านได้สาธารณะ)
    if (env.API_KEY && request.headers.get("Authorization") !== `Bearer ${env.API_KEY}`) {
      return json({ error: "unauthorized" }, 401);
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path === "/health") return json({ ok: true });

    const handler = { "/sightings": sightings, "/flights": flights,
                      "/aircraft": aircraft, "/days": days, "/": flights }[path];
    if (!handler) {
      return json({ error: "not found",
                    endpoints: ["/flights", "/aircraft", "/days", "/sightings", "/health"] }, 404);
    }

    const q = url.searchParams;
    const f = buildQuery(q);
    if (f.error) return json({ error: f.error }, 400);
    try {
      return await handler(env, q, f);
    } catch (e) {
      return json({ error: "query failed", detail: String(e) }, 500);
    }
  },
};
