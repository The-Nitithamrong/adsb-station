#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WIFI_STA 1
namespace sim { extern bool wifiOk, piAlive, stallBody; }
struct WiFiT { void mode(int) {} void begin(const char*, const char*) {} int status() { return sim::wifiOk ? WL_CONNECTED : 6; } };
extern WiFiT WiFi;
// ใช้ 2 แบบ: (1) piAlive() ต่อ TCP:22 ตรง ๆ  (2) เป็น stream ของ body ที่ HTTPClient ถืออยู่ (getStreamPtr)
class WiFiClient {
 public:
  std::string body;            // body ที่ยังไม่ถูกอ่าน
  bool open = false;           // server ยังไม่ปิด connection
  bool connect(const char* host, int port, int timeoutMs);
  void stop() {}
  // sim::stallBody = ปลายทางส่ง header (บอก Content-Length) แล้วค้าง: ไม่มี byte มา และ connection ไม่ปิด
  int available() { return sim::stallBody ? 0 : (int)body.size(); }
  bool connected() { return open; }
  int read(uint8_t* b, size_t n) {
    if (sim::stallBody) return 0;
    if (n > body.size()) n = body.size();
    memcpy(b, body.data(), n); body.erase(0, n);
    return (int)n;
  }
};
