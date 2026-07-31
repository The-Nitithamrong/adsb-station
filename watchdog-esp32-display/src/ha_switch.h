// ha_switch.h — คุมปลั๊กผ่าน Home Assistant REST API (ไม่ต้องมี Tuya local_key)
//
// ใช้ HA ที่คุม Tuya อยู่แล้วเป็นตัวกลาง: ESP32 → HTTP POST → HA → สั่ง Tuya → ปลั๊กดับ/ติด
// service = homeassistant/turn_on|turn_off (ใช้ได้ทุก domain: switch/light/...).
//
// ⚠️ สำหรับ watchdog: HA ต้องอยู่ "คนละเครื่อง" กับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้
//    → Pi ค้างดับถาวร. (ตอน HA ยังอยู่บน ADS-B Pi ให้ทดสอบกับปลั๊ก "ตัวอื่น" เท่านั้น)
#pragma once
#include <WiFi.h>
#include <HTTPClient.h>

// สั่ง HA เปิด/ปิด entity. คืน true ถ้า HTTP 2xx. (Tuya local_key ตัวเดิมอยู่ใน tuya.h ถ้าจะกลับไปใช้ local)
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
