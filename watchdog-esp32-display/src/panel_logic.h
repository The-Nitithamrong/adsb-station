// panel_logic.h — ตรรกะล้วน ๆ ของ panel (ไม่แตะ Arduino/จอ/network เลย): อ่านสถานะจาก JSON ของ HA,
// แกะข้อความพยากรณ์จาก Pi, คำนวณตารางปุ่ม. แยกไฟล์ไว้เพื่อให้ compile + ทดสอบบน PC ได้ด้วย g++ ธรรมดา
// โดยไม่ต้องมีบอร์ด — ของที่พังเงียบ ๆ ได้ง่ายที่สุด (parser) ควรตรวจได้ก่อน flash.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---------- Home Assistant ----------
// body = JSON ที่ GET /api/states/<entity_id> ตอบ → 1 = on · 0 = off · -1 = อย่างอื่นทั้งหมด
// (unavailable / unknown / แกะไม่ได้). หา key "state" ตัวแรก: HA เรียง entity_id → state → attributes
// เสมอ จึงเป็นตัวบนสุด ไม่ใช่ "state" ที่อาจซ้อนอยู่ใน attributes. ไม่ใช้ JSON library — ต้องการค่าเดียว.
static inline int parseHaState(const char* body) {
  if (!body) return -1;
  const char* p = strstr(body, "\"state\"");
  if (!p) return -1;
  p += 7;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
  if (*p != ':') return -1;
  p++;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
  if (*p != '"') return -1;
  p++;
  if (!strncmp(p, "on\"", 3)) return 1;
  if (!strncmp(p, "off\"", 4)) return 0;
  return -1;
}

// ---------- พยากรณ์อากาศ (จาก http://<pi>:8099/weather — รูปแบบดู weather_text() ใน deploy/uptime_server.py) ----------
#define WX_MAX_HOME 4

struct WxDay {
  bool    used;                 // มีบรรทัดนี้ส่งมา
  char    date[11];             // "2026-11-24" · "" = ไม่มี
  char    kind[8];              // clear/partly/cloudy/rain/heavy/storm/fog/snow/cool/hot · "" = ไม่รู้
  bool    hasT;   int   tmax, tmin;
  bool    hasMm;  float rainMm;
  bool    hasPct; int   rainPct;
};

struct WxData {
  long  ts;                     // เวลาที่ Pi เขียน forecast.json (epoch) · 0 = Pi บอกว่าไม่มีข้อมูล
  char  homeSrc[5], homeLabel[7];
  WxDay home[WX_MAX_HOME];
  int   nHome;
  bool  hasDest;                // มีเที่ยวบินถัดไป (มีบรรทัด D)
  char  destSrc[5], destLabel[7];
  WxDay dest;
};

static inline bool wxIsNum(const char* s) {            // "-" (ไม่มีค่า) และของแปลก ๆ → false
  if (*s == '-' || *s == '+') s++;
  if (!*s) return false;
  bool dot = false;
  for (; *s; s++) {
    if (*s == '.' && !dot) { dot = true; continue; }
    if (*s < '0' || *s > '9') return false;
  }
  return true;
}

static inline void wxCopy(char* dst, size_t n, const char* tok) {   // "-" → ""
  if (!strcmp(tok, "-")) { dst[0] = 0; return; }
  strncpy(dst, tok, n - 1);
  dst[n - 1] = 0;
}

// ตัดบรรทัดเดียว (จบที่ \n หรือ \0) เป็น token คั่นด้วยช่องว่าง ลง buf. คืนจำนวน token, -1 = บรรทัดยาวเกิน buf.
// ไม่ใช้ sscanf("%4s ...") เพราะ token ที่ยาวเกินช่องจะ "ล้นไปเป็น field ถัดไป" เงียบ ๆ — ได้ 9 field ครบ
// แต่ค่าเลื่อนช่องกันหมด. แบบนี้นับ token จริง + เช็คความยาวทีละตัว: ผิดรูปแบบ = ทิ้งบรรทัดนั้น.
static inline int wxSplit(const char* line, char* buf, size_t bufN, char** tok, int maxTok) {
  size_t len = 0;
  while (line[len] && line[len] != '\n' && line[len] != '\r') len++;
  if (len >= bufN) return -1;
  memcpy(buf, line, len);
  buf[len] = 0;
  int n = 0;
  char* p = buf;
  while (*p) {
    while (*p == ' ') p++;
    if (!*p) break;
    if (n == maxTok) return maxTok + 1;          // token เกิน → ให้ผู้เรียกเห็นว่าไม่เท่ากับที่คาด
    tok[n++] = p;
    while (*p && *p != ' ') p++;
    if (*p) *p++ = 0;
  }
  return n;
}

// แกะข้อความทั้งก้อน. คืน false = ไม่ใช่รูปแบบ wx1 (อย่าเอาไปทับของเดิม). บรรทัดที่ผิดรูปแบบถูกข้าม
// ทีละบรรทัด ไม่ทิ้งทั้งก้อน — บรรทัดเสีย 1 บรรทัดไม่ควรทำให้วันอื่นหายไปด้วย.
static inline bool wxParse(const char* text, WxData* out) {
  if (!text || !out) return false;
  WxData w;
  memset(&w, 0, sizeof(w));
  char buf[96];
  char* t[9];
  if (wxSplit(text, buf, sizeof(buf), t, 9) != 2 || strcmp(t[0], "wx1") || !wxIsNum(t[1]) || t[1][0] == '-')
    return false;
  w.ts = atol(t[1]);
  for (const char* p = strchr(text, '\n'); p && *++p; p = strchr(p, '\n')) {
    // tag src label date kind tmax tmin rain_mm rain_pct
    if (wxSplit(p, buf, sizeof(buf), t, 9) != 9) continue;
    static const size_t maxLen[9] = {1, 4, 6, 10, 7, 7, 7, 7, 7};
    bool fits = true;
    for (int i = 0; i < 9; i++) fits = fits && strlen(t[i]) <= maxLen[i];
    if (!fits) continue;
    WxDay d;
    memset(&d, 0, sizeof(d));
    d.used = true;
    wxCopy(d.date, sizeof(d.date), t[3]);
    wxCopy(d.kind, sizeof(d.kind), t[4]);
    if (wxIsNum(t[5]) && wxIsNum(t[6])) { d.hasT = true; d.tmax = atoi(t[5]); d.tmin = atoi(t[6]); }
    if (wxIsNum(t[7])) { d.hasMm = true;  d.rainMm = (float)atof(t[7]); }
    if (wxIsNum(t[8])) { d.hasPct = true; d.rainPct = atoi(t[8]); }
    if (t[0][0] == 'H' && w.nHome < WX_MAX_HOME) {
      if (w.nHome == 0) { wxCopy(w.homeSrc, sizeof(w.homeSrc), t[1]); wxCopy(w.homeLabel, sizeof(w.homeLabel), t[2]); }
      w.home[w.nHome++] = d;
    } else if (t[0][0] == 'D' && !w.hasDest) {
      w.hasDest = true;
      wxCopy(w.destSrc, sizeof(w.destSrc), t[1]);
      wxCopy(w.destLabel, sizeof(w.destLabel), t[2]);
      w.dest = d;
    }
  }
  *out = w;
  return true;
}

// วันของ "บ้าน" ที่ตรงกับ date ("YYYY-MM-DD") — ไม่มี = nullptr (จอโชว์ "--" ไม่หยิบวันอื่นมาแทน)
static inline const WxDay* wxHomeDay(const WxData& w, const char* date) {
  for (int i = 0; i < w.nHome; i++)
    if (!strcmp(w.home[i].date, date)) return &w.home[i];
  return nullptr;
}

// ---------- ตารางปุ่มไฟ ----------
// จำนวนปุ่ม → ตาราง: 1 = เต็มจอ · 2 = 2x1 · 3–4 = 2x2 · 5–6 = 3x2 (ปุ่มใหญ่สุดเท่าที่จำนวนยอม)
struct PRect { int16_t x, y, w, h; };

#define GRID_X   6
#define GRID_Y   32
#define GRID_W   308
#define GRID_H   202
#define GRID_GAP 6

static inline void gridDims(int n, int* cols, int* rows) {
  *cols = n <= 1 ? 1 : (n <= 4 ? 2 : 3);
  *rows = n <= 2 ? 1 : 2;
}

static inline PRect gridCell(int n, int i) {
  int cols, rows;
  gridDims(n, &cols, &rows);
  int16_t cw = (GRID_W - GRID_GAP * (cols - 1)) / cols;
  int16_t ch = (GRID_H - GRID_GAP * (rows - 1)) / rows;
  PRect r;
  r.x = GRID_X + (i % cols) * (cw + GRID_GAP);
  r.y = GRID_Y + (i / cols) * (ch + GRID_GAP);
  r.w = cw;
  r.h = ch;
  return r;
}

static inline bool inRect(int16_t px, int16_t py, const PRect& r) {
  return px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h;
}

// ---------- uptime ----------
// รูปแบบเดียวกับ Pixoo (_fmt2): 2 หน่วยบนสุด ไม่มีวินาที เช่น "3D 14H" / "14H 22M" / "22M"
static inline void fmtUp(char* b, size_t n, unsigned long s) {
  unsigned long d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60;
  if (d)      snprintf(b, n, "%luD %luH", d, h);
  else if (h) snprintf(b, n, "%luH %luM", h, m);
  else        snprintf(b, n, "%luM", m);
}
