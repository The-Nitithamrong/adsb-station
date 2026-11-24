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

static bool haService(const char* haBase, const char* token, const char* service,
                      const char* entity, uint16_t timeoutMs) {
  HTTPClient http;
  if (!http.begin(String(haBase) + "/api/services/homeassistant/" + service)) return false;
  haPrep(http, token, timeoutMs);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(String("{\"entity_id\":\"") + entity + "\"}");
  http.end();
  return code >= 200 && code < 300;
}

// สั่งเปิด/ปิดแบบระบุปลายทาง — homeassistant.turn_on/turn_off ใช้ได้ทั้ง light.* และ switch.*
// (HA ส่งต่อให้ domain ของ entity เอง: https://www.home-assistant.io/actions/homeassistant.turn_on/)
static bool haSwitch(const char* haBase, const char* token, const char* entity,
                     bool on, uint16_t timeoutMs = 5000) {
  return haService(haBase, token, on ? "turn_on" : "turn_off", entity, timeoutMs);
}

// สลับ — ใช้เฉพาะตอน "ไม่รู้สถานะปัจจุบัน". รู้สถานะแล้วให้ใช้ haSwitch: คำสั่งแบบระบุปลายทางสั่งซ้ำได้
// ไม่มีผลข้างเคียง ส่วน toggle ที่ยิงตอนจอโชว์ค่าเก่า (มีคนกดสวิตช์ผนังไปก่อน) จะได้ผลตรงข้ามกับที่ตั้งใจ.
static bool haToggle(const char* haBase, const char* token, const char* entity, uint16_t timeoutMs = 5000) {
  return haService(haBase, token, "toggle", entity, timeoutMs);
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
  int st = (code == 200) ? parseHaState(http.getString().c_str()) : -1;
  http.end();
  return st;
}
