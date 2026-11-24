// ha_switch.h — คุยกับ Home Assistant (ไม่ต้องมี Tuya local_key)
//
// ใช้ HA ที่คุมอุปกรณ์อยู่แล้วเป็นตัวกลาง: ESP32 → HTTP → HA → อุปกรณ์. มี 2 ทางในไฟล์นี้ ใช้คนละงาน:
//   - Webhook (haWebhook, ไม่ต้อง token)      → ตัดไฟ Pi (watchdog). ไม่พึ่ง token = ยิงได้แม้ token หมดอายุ/ถูกลบ
//   - REST API + long-lived token (haSwitch / haToggle / haState) → ปุ่มไฟในห้อง. ต้องใช้ token เพราะ
//     webhook "อ่านสถานะกลับ" ไม่ได้ — ปุ่มที่ไม่รู้ว่าไฟเปิดหรือปิดอยู่จริงใช้งานไม่ได้
//     (doc: https://developers.home-assistant.io/docs/api/rest)
//
// ⚠️ สำหรับ watchdog: HA ต้องอยู่ "คนละเครื่อง" กับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้
//    → Pi ค้างดับถาวร. (ตอน HA ยังอยู่บน ADS-B Pi ให้ทดสอบกับปลั๊ก "ตัวอื่น" เท่านั้น)
#pragma once
#include <WiFi.h>
#include <HTTPClient.h>
#include "panel_logic.h"

// ยิง HA webhook (POST) — ไม่ต้องมี token/auth (secret อยู่ใน URL). คืน true ถ้า HTTP 2xx.
// HA รับ webhook แล้ว trigger automation เดียวที่ทำ turn_off → delay → turn_on ครบวงจรเอง.
static bool haWebhook(const char* url, uint16_t timeoutMs = 5000) {
  HTTPClient http;
  if (!http.begin(url)) return false;
  http.setTimeout(timeoutMs);
  int code = http.POST((uint8_t*)nullptr, 0);   // POST body ว่าง
  http.end();
  return code >= 200 && code < 300;
}

// timeout + token ชุดเดียวกันทุก request ของ REST API (เรียกหลัง http.begin เสมอ).
// ต้องตั้ง "connect" timeout แยกด้วย: setTimeout คุมแค่ตอนรออ่านคำตอบ — ถ้า HA ดับทั้งเครื่อง การ "ต่อ" จะค้าง
// ตามค่า default ของ TCP stack (หลายวินาที) และจอทัชจะนิ่งไปทั้งช่วงนั้น เพราะ loop มี thread เดียว.
// LAN: ต่อได้ก็ภายในไม่กี่ ms ไม่ได้ก็คือไม่ได้ → connect สั้นเสมอ (HA_CONNECT_MS) แม้คำสั่งจะยอมรอคำตอบนานกว่านั้น.
#ifndef HA_CONNECT_MS
#define HA_CONNECT_MS 2500
#endif
static void haPrep(HTTPClient& http, const char* token, uint16_t timeoutMs) {
  http.setConnectTimeout(timeoutMs < HA_CONNECT_MS ? timeoutMs : HA_CONNECT_MS);
  http.setTimeout(timeoutMs);
  http.addHeader("Authorization", String("Bearer ") + token);
}

// อ่าน body ไม่เกิน n-1 ไบต์ ภายใน timeoutMs แล้วปิดท้ายด้วย \0 (เรียกหลัง GET/POST คืน 200). คืนจำนวนไบต์ที่ได้.
// ใช้แทน http.getString() ทุกที่: getString วนอ่านจน "ครบ Content-Length" ตราบใดที่ connection ยังเปิด — ปลายทางที่
// ส่ง header แล้วค้าง (Pi แฮงก์กลางคำตอบ คืออาการที่ watchdog ตัวนี้มีไว้กู้พอดี) อาจทำให้ loop() ไม่กลับมาอีกเลย
// และ backstop ตายเงียบ ๆ ไปด้วย — แบบเดียวกับ PUSH HANG ของ Pixoo. ตรงนี้มีเส้นตายของตัวเอง: ครบเวลา = เลิก.
// ขนาดจำกัดก็พอ: ทุกคำตอบที่เราอ่าน ค่าที่ต้องการอยู่ในร้อยไบต์แรก.
static size_t httpReadBody(HTTPClient& http, char* buf, size_t n, uint16_t timeoutMs) {
  size_t got = 0, cap = n - 1;
  int len = http.getSize();                          // Content-Length · -1 = server ไม่บอก
  if (len >= 0 && (size_t)len < cap) cap = (size_t)len;
  WiFiClient* c = http.getStreamPtr();
  unsigned long t0 = millis();
  while (c && got < cap && millis() - t0 < timeoutMs) {
    int avail = c->available();
    if (avail > 0) {
      size_t want = (size_t)avail < cap - got ? (size_t)avail : cap - got;
      int r = c->read((uint8_t*)buf + got, want);
      if (r <= 0) break;
      got += (size_t)r;
    } else if (!c->connected()) {
      break;                                         // ปลายทางปิดแล้วและไม่มีอะไรค้างให้อ่าน
    } else {
      delay(2);
    }
  }
  buf[got] = 0;
  return got;
}

// คืนรหัส HTTP (<= 0 = ต่อไม่ได้)
static int haService(const char* haBase, const char* token, const char* service,
                     const char* entity, uint16_t timeoutMs) {
  HTTPClient http;
  if (!http.begin(String(haBase) + "/api/services/homeassistant/" + service)) return 0;
  haPrep(http, token, timeoutMs);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(String("{\"entity_id\":\"") + entity + "\"}");
  http.end();
  return code;
}

// สั่งเปิด/ปิดแบบระบุปลายทาง — homeassistant.turn_on/turn_off ใช้ได้ทั้ง light.* และ switch.*
// (HA ส่งต่อให้ domain ของ entity เอง: https://www.home-assistant.io/actions/homeassistant.turn_on/)
// *httpCode (ถ้าให้มา) = รหัส HTTP — ผู้เรียกใช้แยก "token ไม่ผ่าน" ออกจาก "สั่งไม่สำเร็จ" เฉย ๆ
static bool haSwitch(const char* haBase, const char* token, const char* entity,
                     bool on, uint16_t timeoutMs = 5000, int* httpCode = nullptr) {
  int code = haService(haBase, token, on ? "turn_on" : "turn_off", entity, timeoutMs);
  if (httpCode) *httpCode = code;
  return code >= 200 && code < 300;
}

// สลับ — ใช้เฉพาะตอน "ไม่รู้สถานะปัจจุบัน". รู้สถานะแล้วให้ใช้ haSwitch: คำสั่งแบบระบุปลายทางสั่งซ้ำได้
// ไม่มีผลข้างเคียง ส่วน toggle ที่ยิงตอนจอโชว์ค่าเก่า (มีคนกดสวิตช์ผนังไปก่อน) จะได้ผลตรงข้ามกับที่ตั้งใจ.
static bool haToggle(const char* haBase, const char* token, const char* entity,
                     uint16_t timeoutMs = 5000, int* httpCode = nullptr) {
  int code = haService(haBase, token, "toggle", entity, timeoutMs);
  if (httpCode) *httpCode = code;
  return code >= 200 && code < 300;
}

// อ่านสถานะ entity → 1 = on · 0 = off · -1 = ไม่รู้ (unavailable/unknown/อ่านไม่ได้).
// *httpCode = รหัส HTTP ที่ได้ (<= 0 = ต่อ HA ไม่ได้เลย · 401 = token ผิด · 404 = ไม่มี entity นี้)
// — ผู้เรียกใช้แยกสาเหตุบนจอ: "ไม่รู้" เฉย ๆ บอกไม่ได้ว่าต้องไปแก้ที่ไหน.
static int haState(const char* haBase, const char* token, const char* entity,
                   uint16_t timeoutMs, int* httpCode) {
  HTTPClient http;
  *httpCode = 0;
  if (!http.begin(String(haBase) + "/api/states/" + entity)) return -1;
  haPrep(http, token, timeoutMs);
  int code = http.GET();
  *httpCode = code;
  int st = -1;
  if (code == 200) {
    char body[256];                                  // "state" เป็น key ที่ 2 ของ JSON — อยู่ในร้อยไบต์แรกเสมอ
    if (httpReadBody(http, body, sizeof(body), timeoutMs) == 0)
      *httpCode = 0;                                 // ตอบ 200 แต่ไม่ส่ง body มาเลย = HA ไม่ได้ตอบจริง → ให้ผู้เรียกพักถาม
    else
      st = parseHaState(body);                       // body โดนตัดกลางค่า → แกะไม่ได้ → -1 (ไม่รู้) ไม่ใช่ค่าผิด
  }
  http.end();
  return st;
}
