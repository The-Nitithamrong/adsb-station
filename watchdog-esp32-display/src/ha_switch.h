// ha_switch.h — คุมปลั๊กผ่าน Home Assistant (ไม่ต้องมี Tuya local_key)
//
// ใช้ HA ที่คุม Tuya อยู่แล้วเป็นตัวกลาง: ESP32 → HTTP POST → HA automation → สั่ง Tuya → ปลั๊กดับ/ติด
// default = Webhook (haWebhook, ไม่ต้อง token). ทางเลือก = service API + token (haSwitch).
//
// ⚠️ สำหรับ watchdog: HA ต้องอยู่ "คนละเครื่อง" กับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้
//    → Pi ค้างดับถาวร. (ตอน HA ยังอยู่บน ADS-B Pi ให้ทดสอบกับปลั๊ก "ตัวอื่น" เท่านั้น)
#pragma once
#include <WiFi.h>
#include <HTTPClient.h>

// ยิง HA webhook (POST) — ไม่ต้องมี token/auth (secret อยู่ใน URL). คืน true ถ้า HTTP 2xx.
// HA รับ webhook แล้ว trigger automation (turn_off/on ปลั๊ก) แบบ async.
static bool haWebhook(const char* url, uint16_t timeoutMs = 5000) {
  HTTPClient http;
  if (!http.begin(url)) return false;
  http.setTimeout(timeoutMs);
  int code = http.POST((uint8_t*)nullptr, 0);   // POST body ว่าง
  http.end();
  return code >= 200 && code < 300;
}

// (ทางเลือก) สั่งผ่าน service API + long-lived token — ใช้ถ้ามี token
static bool haSwitch(const char* haBase, const char* token, const char* entity,
                     bool on, uint16_t timeoutMs = 5000) {
  HTTPClient http;
  String url = String(haBase) + "/api/services/homeassistant/" + (on ? "turn_on" : "turn_off");
  if (!http.begin(url)) return false;
  http.setTimeout(timeoutMs);
  http.addHeader("Authorization", String("Bearer ") + token);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(String("{\"entity_id\":\"") + entity + "\"}");
  http.end();
  return code >= 200 && code < 300;
}
