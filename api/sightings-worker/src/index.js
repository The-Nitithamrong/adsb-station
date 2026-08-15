/**
 * adsb-sightings-api — อ่านแคตตาล็อกเที่ยวบินที่สถานีจับได้ (D1 table `sightings`)
 *
 * 1 แถว = (วัน UTC, เที่ยวบิน, ลำ) เห็นครั้งแรกเมื่อไร — Pi เขียนลง SQLite แล้ว outbox ส่งขึ้น D1
 * (ทางเดียว: Worker นี้อ่านอย่างเดียว ไม่รับเขียน — ฝั่งเขียนใช้ D1 REST API + token ของ outbox อยู่แล้ว)
 *
 *   GET /sightings?date=2026-08-10&flight=THA476&reg=HS-TKF&hex=8801f2&station=Arin
 *                 &from=2026-08-01&to=2026-08-10&limit=200&offset=0&order=asc
 *   GET /health
 *
 * auth: ตั้ง secret API_KEY แล้วต้องส่ง `Authorization: Bearer <key>`; ไม่ตั้ง = เปิดอ่านสาธารณะ
 */

const MAX_LIMIT = 1000;
const DEFAULT_LIMIT = 200;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

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

/** ตัวเลขที่รับจาก query — ไม่ใช่ตัวเลข/ติดลบ = ใช้ค่า default (ไม่ throw ให้ผู้เรียกงง) */
function intParam(v, dflt, max) {
  const n = parseInt(v ?? "", 10);
  if (!Number.isFinite(n) || n < 0) return dflt;
  return max !== undefined ? Math.min(n, max) : n;
}

/**
 * แปลง query string → WHERE + ค่า bind
 * ทุกค่าที่ผู้ใช้ส่งมาเข้าเป็น bound parameter เสมอ (?) — ไม่ต่อ string ลง SQL = ไม่มีทาง inject
 */
function buildFilter(q) {
  const where = [];
  const binds = [];
  const eq = (col, val, tf = (s) => s) => {
    if (val) {
      where.push(`${col} = ?`);
      binds.push(tf(val));
    }
  };

  eq("day", DATE_RE.test(q.get("date") ?? "") ? q.get("date") : null);
  eq("flight", q.get("flight"), (s) => s.trim().toUpperCase());
  eq("reg", q.get("reg"), (s) => s.trim().toUpperCase());
  eq("hex", q.get("hex"), (s) => s.trim().toLowerCase());
  eq("station", q.get("station"));

  // ช่วงวัน (ใช้ร่วมกับ date ได้ แต่ปกติเลือกอย่างใดอย่างหนึ่ง)
  const from = q.get("from");
  const to = q.get("to");
  if (DATE_RE.test(from ?? "")) { where.push("day >= ?"); binds.push(from); }
  if (DATE_RE.test(to ?? "")) { where.push("day <= ?"); binds.push(to); }

  return { sql: where.length ? ` WHERE ${where.join(" AND ")}` : "", binds };
}

async function sightings(env, url) {
  const q = url.searchParams;
  const { sql: whereSql, binds } = buildFilter(q);
  const limit = intParam(q.get("limit"), DEFAULT_LIMIT, MAX_LIMIT);
  const offset = intParam(q.get("offset"), 0);
  const order = q.get("order") === "asc" ? "ASC" : "DESC";

  const rows = await env.DB.prepare(
    `SELECT day, flight, reg, hex, first_seen_utc, first_seen_ts, station
       FROM sightings${whereSql}
      ORDER BY first_seen_ts ${order}
      LIMIT ? OFFSET ?`,
  ).bind(...binds, limit, offset).all();

  const results = (rows.results ?? []).map((r) => ({
    flight_number: r.flight,
    registration: r.reg,          // null = ไม่มีในฐานทะเบียน (ADS-B ส่งมาแค่ hex)
    first_seen_utc: r.first_seen_utc,
    first_seen_ts: r.first_seen_ts,
    day: r.day,
    hex: r.hex,
    station: r.station,
  }));

  // has_more จากจำนวนที่ได้จริง — ไม่ต้องยิง COUNT(*) อีกรอบ (D1 คิดเงินตามแถวที่อ่าน)
  return json({ count: results.length, limit, offset, has_more: results.length === limit, results });
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
    try {
      if (path === "/health") return json({ ok: true });
      if (path === "/sightings" || path === "/") return await sightings(env, url);
      return json({ error: "not found", endpoints: ["/sightings", "/health"] }, 404);
    } catch (e) {
      return json({ error: "query failed", detail: String(e) }, 500);
    }
  },
};
