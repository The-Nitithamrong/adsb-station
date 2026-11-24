#pragma once
#include "Arduino.h"
#include "WiFi.h"
#include <map>
namespace sim { int http(const std::string& method, const std::string& url, const std::map<std::string, std::string>& hdr, const std::string& body, std::string* resp, int connectTimeout, int readTimeout); }
class HTTPClient {
  std::string url_;
  std::map<std::string, std::string> hdr_;
  WiFiClient cli_;
  int ct_ = -1, rt_ = 5000, size_ = -1;
  int done(int code, const std::string& resp) {
    cli_.body = resp; cli_.open = code > 0; size_ = code > 0 ? (int)resp.size() : -1;
    return code;
  }
 public:
  bool begin(const char* u) { url_ = u; hdr_.clear(); return true; }
  bool begin(const String& u) { return begin(u.c_str()); }
  void setTimeout(uint16_t ms) { rt_ = ms; }
  void setConnectTimeout(int32_t ms) { ct_ = ms; }
  void addHeader(const String& k, const String& v) { hdr_[k.s] = v.s; }
  int GET() { std::string r; return done(sim::http("GET", url_, hdr_, "", &r, ct_, rt_), r); }
  int POST(const String& b) { std::string r; return done(sim::http("POST", url_, hdr_, b.s, &r, ct_, rt_), r); }
  int POST(uint8_t*, size_t) { std::string r; return done(sim::http("POST", url_, hdr_, "", &r, ct_, rt_), r); }
  int getSize() { return size_; }                 // Content-Length
  WiFiClient* getStreamPtr() { return &cli_; }
  // ไม่มี getString() โดยตั้งใจ: firmware ต้องอ่าน body ผ่าน httpReadBody (มีเส้นตาย) เท่านั้น — เผลอกลับไปใช้ = compile ไม่ผ่าน
  void end() {}
};
