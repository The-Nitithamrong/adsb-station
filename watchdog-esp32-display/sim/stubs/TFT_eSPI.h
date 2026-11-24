// stub ของ TFT_eSPI: ทุกคำสั่งวาดถูกจดเป็นข้อความ 1 บรรทัด (display list) ให้ render.py วาดซ้ำเป็น PNG.
// ขนาดตัวอักษรใช้ของ font GLCD จริง (font 1): ช่องละ 6x8 px คูณ setTextSize.
#pragma once
#include "Arduino.h"
#define TFT_BLACK 0x0000
#define TFT_WHITE 0xFFFF
#define TFT_RED 0xF800
#define TFT_GREEN 0x07E0
#define TFT_CYAN 0x07FF
#define TFT_YELLOW 0xFFE0
#define TFT_ORANGE 0xFDA0
#define TFT_DARKGREEN 0x03E0
#define TFT_DARKGREY 0x7BEF
#define TL_DATUM 0
#define TC_DATUM 1
#define TR_DATUM 2
#define ML_DATUM 3
#define MC_DATUM 4
#define MR_DATUM 5
#define TFT_BL 21
namespace sim { extern FILE* dl; }
class TFT_eSPI {
  int datum_ = 0, size_ = 1; unsigned fg_ = 0xFFFF, bg_ = 0;
 public:
  void init() {}
  void setRotation(int) {}
  void fillScreen(uint16_t c) { fprintf(sim::dl, "F 0 0 320 240 %u\n", c); }
  void fillRect(int x, int y, int w, int h, uint16_t c) { fprintf(sim::dl, "F %d %d %d %d %u\n", x, y, w, h, c); }
  void fillRoundRect(int x, int y, int w, int h, int r, uint16_t c) { fprintf(sim::dl, "R %d %d %d %d %d %u\n", x, y, w, h, r, c); }
  void drawRoundRect(int x, int y, int w, int h, int r, uint16_t c) { fprintf(sim::dl, "O %d %d %d %d %d %u\n", x, y, w, h, r, c); }
  void fillCircle(int x, int y, int r, uint16_t c) { fprintf(sim::dl, "C %d %d %d %u\n", x, y, r, c); }
  void drawFastHLine(int x, int y, int w, uint16_t c) { fprintf(sim::dl, "F %d %d %d 1 %u\n", x, y, w, c); }
  void setTextDatum(int d) { datum_ = d; }
  void setTextSize(int s) { size_ = s; }
  void setTextColor(uint16_t fg, uint16_t bg) { fg_ = fg; bg_ = bg; }
  void drawString(const char* s, int x, int y) { fprintf(sim::dl, "T %d %d %d %d %u %u %s\n", x, y, datum_, size_, fg_, bg_, s); }
};
