#pragma once
#include "Arduino.h"
#include <map>
namespace sim { int http(const std::string& method, const std::string& url, const std::map<std::string, std::string>& hdr, const std::string& body, std::string* resp, int connectTimeout, int readTimeout); }
class HTTPClient {
  std::string url_, resp_;
  std::map<std::string, std::string> hdr_;
  int ct_ = -1, rt_ = 5000;
 public:
  bool begin(const char* u) { url_ = u; hdr_.clear(); return true; }
  bool begin(const String& u) { return begin(u.c_str()); }
  void setTimeout(uint16_t ms) { rt_ = ms; }
  void setConnectTimeout(int32_t ms) { ct_ = ms; }
  void addHeader(const String& k, const String& v) { hdr_[k.s] = v.s; }
  int GET() { return sim::http("GET", url_, hdr_, "", &resp_, ct_, rt_); }
  int POST(const String& b) { return sim::http("POST", url_, hdr_, b.s, &resp_, ct_, rt_); }
  int POST(uint8_t*, size_t) { return sim::http("POST", url_, hdr_, "", &resp_, ct_, rt_); }
  String getString() { return String(resp_); }
  void end() {}
};
