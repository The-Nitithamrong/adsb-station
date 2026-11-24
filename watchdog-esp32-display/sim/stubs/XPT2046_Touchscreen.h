#pragma once
#include "Arduino.h"
#include "SPI.h"
namespace sim { extern bool touchDown; extern int touchRawX, touchRawY; }
struct TS_Point { int16_t x, y, z; };
class XPT2046_Touchscreen {
 public:
  XPT2046_Touchscreen(int, int) {}
  void begin(SPIClass&) {}
  void setRotation(int) {}
  bool tirqTouched() { return sim::touchDown; }
  bool touched() { return sim::touchDown; }
  TS_Point getPoint() { return {(int16_t)sim::touchRawX, (int16_t)sim::touchRawY, 1000}; }
};
