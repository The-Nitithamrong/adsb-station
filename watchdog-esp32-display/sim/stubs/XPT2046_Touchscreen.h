#pragma once
#include "Arduino.h"
#include "SPI.h"
// เหมือน library จริง: touched() = อ่านแรงกดสด 1 sample ทุกครั้งที่เรียก (ไม่เฉลี่ย).
//   sim::dropSamples  = อีกกี่ sample ถัดไปที่จะอ่านได้ "ไม่แตะ" ทั้งที่นิ้วยังอยู่ (แรงกดบนจอ resistive แกว่ง)
//   sim::ghostSamples = อีกกี่ sample ถัดไปที่จะอ่านได้ "แตะ" ทั้งที่ไม่มีนิ้ว (สัญญาณรบกวนครั้งเดียว)
namespace sim { extern bool touchDown; extern int touchRawX, touchRawY, dropSamples, ghostSamples; }
struct TS_Point { int16_t x, y, z; };
class XPT2046_Touchscreen {
  bool sample() {
    if (sim::touchDown) { if (sim::dropSamples > 0) { sim::dropSamples--; return false; } return true; }
    if (sim::ghostSamples > 0) { sim::ghostSamples--; return true; }
    return false;
  }
 public:
  XPT2046_Touchscreen(int, int) {}
  void begin(SPIClass&) {}
  void setRotation(int) {}
  bool tirqTouched() { return sim::touchDown || sim::ghostSamples > 0; }
  bool touched() { return sample(); }
  TS_Point getPoint() { return {(int16_t)sim::touchRawX, (int16_t)sim::touchRawY, 1000}; }
};
