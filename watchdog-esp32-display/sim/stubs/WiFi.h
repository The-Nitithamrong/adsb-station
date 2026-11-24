#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WIFI_STA 1
namespace sim { extern bool wifiOk, piAlive; }
struct WiFiT { void mode(int) {} void begin(const char*, const char*) {} int status() { return sim::wifiOk ? WL_CONNECTED : 6; } };
extern WiFiT WiFi;
class WiFiClient {
 public:
  bool connect(const char* host, int port, int timeoutMs);
  void stop() {}
};
