// stub ของ Arduino core บน PC — พอให้ src/main.cpp ตัวจริง compile + รันได้ด้วยนาฬิกาจำลอง (millis/delay/time)
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <cctype>
#include <ctime>
#include <string>

namespace sim {
extern unsigned long nowMs;       // virtual clock
extern long long epochBase;       // wall clock at nowMs = 0 (0 → NTP not yet synced)
extern bool ntpOk;
}
inline unsigned long millis() { return sim::nowMs; }
inline void delay(unsigned long ms) { sim::nowMs += ms; }
inline time_t simTime(time_t* out) { time_t t = sim::ntpOk ? (time_t)(sim::epochBase + sim::nowMs / 1000) : (time_t)(sim::nowMs / 1000); if (out) *out = t; return t; }
#define time(x) simTime(x)
inline void configTime(long, int, const char*) {}

#define HIGH 1
#define LOW 0
#define OUTPUT 1
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
template <class T, class A, class B> T constrain(T v, A lo, B hi) { return v < (T)lo ? (T)lo : (v > (T)hi ? (T)hi : v); }
inline long map(long x, long in_min, long in_max, long out_min, long out_max) { return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min; }

class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const std::string& c) : s(c) {}
  String operator+(const String& o) const { return String(s + o.s); }
  String operator+(const char* o) const { return String(s + o); }
  const char* c_str() const { return s.c_str(); }
  long toInt() const { return atol(s.c_str()); }
  size_t length() const { return s.size(); }
};
inline String operator+(const char* a, const String& b) { return String(std::string(a) + b.s); }

struct SerialT {
  void begin(long) {}
  void printf(const char* f, ...) { va_list a; va_start(a, f); char b[256]; vsnprintf(b, sizeof b, f, a); va_end(a); extern void simLog(const char*); simLog(b); }
};
extern SerialT Serial;
