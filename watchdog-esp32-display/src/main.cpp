// main.cpp — Pi power-cycle watchdog บนบอร์ด CYD2USB (Sunton ESP32-2432S028Rv3: ST7789 240x320 + XPT2046)
//
// 2 โหมดในเครื่องเดียว:
//   NORMAL — เฝ้า Pi (TCP :22) ทุก CHECK_MS. เงียบเกิน DOWN_MS (15 นาที) → auto power-cycle
//   BACKUP — แตะปุ่ม "RESET PI" บนจอ (กดยืนยัน 2 ครั้ง) → ยิง 1 webhook → HA off→delay→on เอง
//
// จอสลับ 2 หน้าทุก PAGE_SWITCH_MS (5 นาที): นาฬิกา ↔ Pi status. แตะหน้านาฬิกา = ไปหน้า status ทันที
// (ปุ่ม RESET พร้อมใช้เสมอ). การเฝ้า/auto-reset ทำงานตลอดไม่ขึ้นกับหน้าที่โชว์.
// วาดด้วย TFT_eSPI ตรงๆ (ไม่ใช้ LVGL — เบา RAM, ไม่มี framebuffer, block ตอน countdown ได้).
// จอ = ST7789 บน HSPI (ตั้งใน platformio.ini) · touch = XPT2046 บน VSPI (bus แยก).
// คุมปลั๊กผ่าน HA Webhook เดียว (ha_switch.h → haWebhook) — HA automation จับเวลา off→delay→on เอง.
//
// ⚠️ HA ต้องอยู่คนละเครื่องกับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้ → Pi ค้างดับถาวร.
//    ตอน HA ยังบน ADS-B Pi ให้ทดสอบกับปลั๊ก "ตัวอื่น" เท่านั้น. + ESP32 เสียบไฟคนละแหล่งกับ Pi.

#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>
#include <time.h>
#include "config.h"
#include "ha_switch.h"

// --- touch XPT2046 บน VSPI (bus แยกจากจอที่อยู่ HSPI) — pin ของ CYD2USB ---
#define XPT2046_IRQ  36
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
#define XPT2046_CLK  25
#define XPT2046_CS   33

// --- touch calibration: วัดจริงบนบอร์ดนี้ที่ rotation 1 (ไม่ swap/invert) ---
// เปลี่ยน setRotation เมื่อไหร่ = ค่าพวกนี้ใช้ไม่ได้ ต้องวัดใหม่
#define TOUCH_X_MIN 338
#define TOUCH_X_MAX 3676
#define TOUCH_Y_MIN 505
#define TOUCH_Y_MAX 3465

static const int16_t SCREEN_W = 320;   // landscape (rotation 1)
static const int16_t SCREEN_H = 240;

#define PAGE_SWITCH_MS 300000UL         // สลับหน้า นาฬิกา ↔ Pi status ทุกกี่ ms (300000 = 5 นาที)

// --- ปุ่ม RESET ---
#define BTN_X 40
#define BTN_Y 165
#define BTN_W 240
#define BTN_H 60

// --- ปุ่ม COFFEE (สลับ coffee break ของ Pixoo ผ่าน endpoint บน Pi :PI_INFO_PORT) ---
#define CBTN_X 176
#define CBTN_Y 26
#define CBTN_W 138
#define CBTN_H 28

TFT_eSPI tft = TFT_eSPI();
SPIClass touchSpi(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS, XPT2046_IRQ);

bool wifiUp = false, piUp = true;
unsigned long lastPiOk = 0, lastCheck = 0, lastCycle = 0, lastDraw = 0, confirmUntil = 0;

// สถานะที่วาดล่าสุด (กัน flicker: วาดใหม่เฉพาะส่วนที่เปลี่ยน — เลขเปลี่ยนทุกวิ, "Pi OK" นิ่ง)
bool forceUI = true, dWifi = false, dPiUp = false;
char dLine[64] = {0}, dLine2[64] = {0};   // dLine = บรรทัด Pi up, dLine2 = บรรทัด last check

// หน้าจอ 2 หน้า สลับทุก PAGE_SWITCH_MS: 0 = Pi status (มีปุ่ม RESET) · 1 = นาฬิกา
int page = 0;
unsigned long lastPageSwitch = 0;
char dClk[8] = {0}, dDate[24] = {0};      // cache หน้านาฬิกา (กัน redraw ตัวใหญ่ทุกวิ)

// เวลาจริง (epoch) ของการเช็ค Pi รอบล่าสุด — โชว์ "last check HH:MM:SS" อัปเดตทุก CHECK_MS (30 วิ)
time_t lastCheckEpoch = 0;
// uptime จริงของ Pi (วินาที) ดึงจาก endpoint บน Pi ทุกรอบเช็ค — -1 = ยังไม่ได้/ดึงไม่ได้
long piUpSecs = -1;
unsigned long piUpAtMillis = 0;   // millis ตอนได้ค่า piUpSecs (ไว้ interpolate ระหว่างรอบเช็ค)
int coffeeOn = -1;                // coffee break ของ Pixoo: 1=on / 0=off / -1=ยังไม่รู้ (ดึงจาก /coffee)

// รูปแบบเดียวกับ Pixoo (_fmt2): 2 หน่วยบนสุด ไม่มีวินาที เช่น "3D 14H" / "14H 22M" / "22M"
void fmtUp(char* b, size_t n, unsigned long s) {
  unsigned long d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60;
  if (d)      snprintf(b, n, "%luD %luH", d, h);
  else if (h) snprintf(b, n, "%luH %luM", h, m);
  else        snprintf(b, n, "%luM", m);
}

// ---------- WiFi ----------
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
  wifiUp = (WiFi.status() == WL_CONNECTED);
}

// ---------- Pi health ----------
bool piAlive() {
  WiFiClient c;
  bool ok = c.connect(PI_IP, PI_PORT, TCP_TIMEOUT_MS);   // ต่อ TCP ได้ = Pi มีชีวิตระดับ network
  c.stop();
  return ok;
}

// uptime จริงของ Pi (วินาที) จาก endpoint adsb-uptime.service — คืน -1 ถ้าดึงไม่ได้ (service ยังไม่ลง/ล่ม)
// display-only + best-effort: ไม่ใช่ตัวตัดสิน reset (liveness ยังใช้ piAlive/TCP:22 เพราะ server อาจล่มขณะ Pi ปกติ)
long piUptimeS() {
  HTTPClient http;
  char url[48];
  snprintf(url, sizeof(url), "http://%s:%d/", PI_IP, PI_INFO_PORT);
  if (!http.begin(url)) return -1;
  http.setTimeout(TCP_TIMEOUT_MS);
  long s = (http.GET() == 200) ? http.getString().toInt() : -1;
  http.end();
  return s;
}

// GET coffee endpoint (/coffee = อ่าน state, /coffee/toggle = สลับ) → 1/0, -1 ถ้าดึงไม่ได้
int coffeeGet(const char* path) {
  HTTPClient http;
  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d%s", PI_IP, PI_INFO_PORT, path);
  if (!http.begin(url)) return -1;
  http.setTimeout(TCP_TIMEOUT_MS);
  int r = (http.GET() == 200) ? http.getString().toInt() : -1;
  http.end();
  return r;
}

// ---------- touch ----------
bool readTouch(int16_t &sx, int16_t &sy) {
  if (!ts.tirqTouched() || !ts.touched()) return false;
  TS_Point p = ts.getPoint();
  sx = constrain((int16_t)map(p.x, TOUCH_X_MIN, TOUCH_X_MAX, 0, SCREEN_W - 1), 0, SCREEN_W - 1);
  sy = constrain((int16_t)map(p.y, TOUCH_Y_MIN, TOUCH_Y_MAX, 0, SCREEN_H - 1), 0, SCREEN_H - 1);
  Serial.printf("touch raw=(%d,%d) -> screen=(%d,%d)\n", p.x, p.y, sx, sy);
  return true;
}

// ---------- UI ----------
void drawButton(bool confirm) {
  uint16_t col = confirm ? TFT_ORANGE : TFT_RED;
  tft.fillRoundRect(BTN_X, BTN_Y, BTN_W, BTN_H, 8, col);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, col);
  tft.setTextSize(2);
  tft.drawString(confirm ? "TAP AGAIN" : "RESET PI", BTN_X + BTN_W / 2, BTN_Y + BTN_H / 2);
}

void drawCoffeeBtn(int st) {   // ปุ่มสลับ coffee break: 1=ON(เขียว) / 0=OFF(เทา) / -1=?(เข้ม)
  uint16_t col = st == 1 ? TFT_DARKGREEN : (st == 0 ? 0x4208 : 0x2104);
  tft.fillRoundRect(CBTN_X, CBTN_Y, CBTN_W, CBTN_H, 6, col);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, col);
  tft.setTextSize(1);
  tft.drawString(st == 1 ? "COFFEE: ON" : (st == 0 ? "COFFEE: OFF" : "COFFEE: ?"),
                 CBTN_X + CBTN_W / 2, CBTN_Y + CBTN_H / 2);
}

void drawStatusStatic() {   // header + ปุ่ม (วาดครั้งเดียว/เมื่อเปลี่ยนหน้า)
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.drawString("Pi WATCHDOG", 6, 6);
  drawButton(confirmUntil && millis() < confirmUntil);
  drawCoffeeBtn(coffeeOn);
  forceUI = true;     // หลังล้างจอ → drawDynamic วาดทุกส่วนใหม่ 1 รอบ
}

void drawStatusDynamic() {  // วาดใหม่เฉพาะส่วนที่เปลี่ยน → ไม่ flicker (เลขเปลี่ยนทุกวิ, "Pi OK" นิ่ง)
  if (forceUI || dWifi != wifiUp) {                          // WiFi dot: เปลี่ยนเฉพาะตอนสถานะเปลี่ยน
    tft.fillCircle(305, 14, 6, wifiUp ? TFT_GREEN : TFT_RED);
    dWifi = wifiUp;
  }
  if (forceUI || dPiUp != piUp) {                            // "Pi OK/DOWN" ตัวใหญ่: เปลี่ยนเฉพาะตอนสถานะพลิก
    tft.fillRect(0, 55, SCREEN_W, 42, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(4);
    tft.setTextColor(piUp ? TFT_GREEN : TFT_RED, TFT_BLACK);
    tft.drawString(piUp ? "Pi OK" : "Pi DOWN", 160, 75);
    dPiUp = piUp;
  }
  // บรรทัด 1 — Pi uptime จริง (จาก endpoint บน Pi) ฟอร์แมตแบบ Pixoo. ดึงไม่ได้ → "-- (info svc?)".
  // Pi ดับ → นับถอยหลังถึงเวลา auto-reset แทน
  char line[64], num[16];
  if (piUp) {
    if (piUpSecs >= 0) {
      unsigned long s = (unsigned long)piUpSecs + (millis() - piUpAtMillis) / 1000;  // interpolate ระหว่างรอบเช็ค
      fmtUp(num, sizeof(num), s);
      snprintf(line, sizeof(line), "Pi up %s", num);
    } else {
      snprintf(line, sizeof(line), "Pi up -- (info svc?)");
    }
  } else {
    unsigned long ago = (millis() - lastPiOk) / 1000;
    snprintf(line, sizeof(line), "down %lum%02lus  (reset at %lum)", ago / 60, ago % 60, DOWN_MS / 60000);
  }
  if (forceUI || strcmp(line, dLine) != 0) {
    tft.fillRect(0, 110, SCREEN_W, 15, TFT_BLACK);           // ล้างเฉพาะแถบบางๆ ของบรรทัดนี้
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(line, 160, 117);
    strncpy(dLine, line, sizeof(dLine) - 1);
  }

  // บรรทัด 2 — check: เวลาจริง (HH:MM:SS) ที่ ping Pi รอบล่าสุด — เปลี่ยนทุก 30 วิ (ไม่กระพริบ)
  char line2[64];
  bool haveClock = lastCheckEpoch > 1600000000;   // นาฬิกา NTP มาแล้ว (ไม่ใช่ปี 1970)
  if (haveClock) { struct tm t; localtime_r(&lastCheckEpoch, &t);
                   char ts[16]; strftime(ts, sizeof(ts), "%H:%M:%S", &t);
                   snprintf(line2, sizeof(line2), "last check %s", ts); }
  else           snprintf(line2, sizeof(line2), "check: waiting NTP...");
  if (forceUI || strcmp(line2, dLine2) != 0) {
    tft.fillRect(0, 132, SCREEN_W, 15, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(haveClock ? TFT_DARKGREY : TFT_RED, TFT_BLACK);
    tft.drawString(line2, 160, 139);
    strncpy(dLine2, line2, sizeof(dLine2) - 1);
  }
  forceUI = false;
}

// ---------- หน้านาฬิกา ----------
void drawClockStatic() {   // ล้างจอ + label เล็กบนหัว (วาดครั้งเดียวตอนสลับมาหน้านี้)
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
  tft.drawString("tap = Pi status", 6, 6);
  forceUI = true;
  dClk[0] = dDate[0] = 0;   // บังคับวาดเวลา/วันที่ใหม่
}

void drawClockDynamic() {  // เวลาตัวใหญ่ (โคลอนกระพริบ 1 Hz) + วันที่ + สถานะ Pi เล็กๆ ล่างสุด
  time_t e = time(nullptr);
  bool haveClock = e > 1600000000;
  struct tm t; localtime_r(&e, &t);

  char clk[8];
  if (haveClock) { strftime(clk, sizeof(clk), "%H:%M", &t); if (t.tm_sec % 2) clk[2] = ' '; }  // ' ' กว้างเท่า ':'
  else           strcpy(clk, "--:--");
  if (forceUI || strcmp(clk, dClk) != 0) {                   // เวลาตัวใหญ่: opaque bg → overwrite ไม่ flicker
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(7);
    tft.setTextColor(TFT_CYAN, TFT_BLACK);
    tft.drawString(clk, 160, 95);
    strncpy(dClk, clk, sizeof(dClk) - 1);
  }

  char date[24];
  if (haveClock) strftime(date, sizeof(date), "%a %d %b", &t);
  else           strcpy(date, "waiting NTP...");
  if (forceUI || strcmp(date, dDate) != 0) {
    tft.fillRect(0, 150, SCREEN_W, 24, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(2);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(date, 160, 162);
    strncpy(dDate, date, sizeof(dDate) - 1);
  }

  // สถานะ Pi เล็กๆ ล่างสุด (ยังเห็นได้ระหว่างอยู่หน้านาฬิกา) — วาด opaque ไม่ต้อง cache
  tft.setTextDatum(MC_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(piUp ? TFT_GREEN : TFT_RED, TFT_BLACK);
  tft.drawString(piUp ? "Pi OK   " : "Pi DOWN ", 160, 210);
  forceUI = false;
}

// ---------- router: เลือกวาดตามหน้า ----------
void drawStatic()  { if (page == 1) drawClockStatic();  else drawStatusStatic(); }
void drawDynamic() { if (page == 1) drawClockDynamic(); else drawStatusDynamic(); }

// ---------- power-cycle (ใช้ร่วมทั้ง auto + manual) ----------
void powerCycle(const char* reason) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_RED, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawString("POWER OFF", 160, 30);
  tft.setTextSize(1);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(reason, 160, 58);

  // ยิง webhook เดียว → HA automation คุมทั้ง cycle เอง: turn_off → delay OFF_SECONDS → turn_on.
  // แบบนี้ทน ESP32 หลุด WiFi หลังยิง: ปลั๊กเปิดกลับเองเพราะ HA เป็นคนจับเวลา ไม่ใช่ ESP32.
  // countdown ล่างเป็นแค่ตัวโชว์บนจอ (ต้องตั้ง OFF_SECONDS ให้ตรงกับ delay ใน automation).
  bool cycOk;
  if (DRY_RUN) {
    cycOk = true;
  } else {
    cycOk = false;
    for (int i = 0; i < 3 && !cycOk; i++) {          // retry เผื่อ request แรกพลาด
      cycOk = haWebhook(HA_WEBHOOK_CYCLE);   // ยิง webhook → HA off→delay→on
      if (!cycOk) delay(1500);
    }
  }
  const char* offMsg = DRY_RUN ? "DRY RUN - no real cut"
                               : (cycOk ? "Pi OFF - HA power back in..." : "HA WEBHOOK FAILED - check URL");
  for (int s = OFF_SECONDS; s > 0; s--) {            // นับถอยหลังบนจอ (block ได้ — ไม่มี LVGL ต้อง service)
    tft.fillRect(0, 90, SCREEN_W, 110, TFT_BLACK);
    tft.setTextColor(cycOk ? TFT_YELLOW : TFT_RED, TFT_BLACK);
    tft.setTextSize(6);
    char buf[8]; snprintf(buf, sizeof(buf), "%d", s);
    tft.drawString(buf, 160, 130);
    tft.setTextSize(1);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(offMsg, 160, 190);
    delay(1000);
  }
  tft.fillRect(0, 90, SCREEN_W, 130, TFT_BLACK);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);
  tft.setTextSize(3);
  tft.drawString("POWER ON", 160, 130);              // HA เปิดกลับเองแล้ว (ไม่ต้องยิง ON ซ้ำ)
  delay(2000);
  unsigned long now = millis();                      // รีเซ็ตตัวนับ — ให้ Pi boot ก่อน
  lastPiOk = now; lastCycle = now; lastCheck = now; piUp = true;
  piUpSecs = -1;                                     // Pi เพิ่ง reboot → รอเช็ครอบหน้าดึง uptime ใหม่
  page = 0; lastPageSwitch = now;                    // หลัง reset → กลับหน้า status (คนดูจะได้เห็นสถานะ)
}

// ---------- setup / loop ----------
void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  tft.init();
  tft.setRotation(1);                                // landscape 320x240 (ต้องตรงกับตอน calibrate)
  tft.fillScreen(TFT_BLACK);
  touchSpi.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
  ts.begin(touchSpi);
  ts.setRotation(1);                                 // ต้องตรงกับ tft rotation

  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawString("Connecting WiFi...", 160, 120);
  connectWiFi();
  configTime(7 * 3600, 0, "pool.ntp.org");           // BKK UTC+7 (นาฬิกาสำหรับ "last check")

  unsigned long now = millis();
  lastPiOk = now; lastCheck = 0; lastDraw = 0;
  lastCycle = now - COOLDOWN_MS;                      // ยอม auto-cycle ครั้งแรกได้เมื่อครบ DOWN_MS
  lastPageSwitch = now;                              // เริ่มที่หน้า status → สลับหน้าแรกเมื่อครบ PAGE_SWITCH_MS
  piUp = true;
  drawStatic();
}

void loop() {
  // --- touch (หน้านาฬิกา: แตะที่ไหนก็ไปหน้า status · หน้า status: ปุ่ม RESET + ยืนยัน 2 ครั้ง) ---
  int16_t tx, ty;
  if (readTouch(tx, ty)) {
    if (page == 1) {                                 // อยู่หน้านาฬิกา → แตะ = ไปหน้า status ทันที (ปุ่ม RESET พร้อมใช้)
      page = 0; lastPageSwitch = millis();
      drawStatic();
    } else if (tx >= CBTN_X && tx <= CBTN_X + CBTN_W && ty >= CBTN_Y && ty <= CBTN_Y + CBTN_H) {
      int r = coffeeGet("/coffee/toggle");           // แตะปุ่ม COFFEE → สลับ coffee break ของ Pixoo
      if (r >= 0) { coffeeOn = r; drawCoffeeBtn(coffeeOn); }
    } else if (tx >= BTN_X && tx <= BTN_X + BTN_W && ty >= BTN_Y && ty <= BTN_Y + BTN_H) {
      if (confirmUntil && millis() < confirmUntil) { // แตะครั้งที่ 2 = ยืนยัน
        confirmUntil = 0;
        powerCycle("manual reset");
        drawStatic();
      } else {                                       // แตะครั้งแรก = ขอยืนยัน 3 วิ
        confirmUntil = millis() + 3000;
        drawButton(true);
      }
    }
    delay(250);                                      // debounce
  }
  if (confirmUntil && millis() > confirmUntil) {     // หมดเวลายืนยัน → กลับปุ่มแดง
    confirmUntil = 0;
    if (page == 0) drawButton(false);
  }

  // --- สลับหน้า นาฬิกา ↔ status ทุก PAGE_SWITCH_MS ---
  if (millis() - lastPageSwitch >= PAGE_SWITCH_MS) {
    lastPageSwitch = millis();
    page ^= 1;
    drawStatic();
  }

  // --- WiFi ของตัวเอง (reconnect + อย่าโทษ Pi ตอนที่เราหลุด) ---
  if (WiFi.status() != WL_CONNECTED) {
    wifiUp = false;
    connectWiFi();
    lastPiOk = millis();
  } else {
    wifiUp = true;
  }

  // --- เช็ค Pi ตามรอบ ---
  if (millis() - lastCheck >= CHECK_MS) {
    lastCheck = millis();
    if (wifiUp) {
      lastCheckEpoch = time(nullptr);                // เวลาจริงของรอบเช็คนี้ → บรรทัด "last check"
      piUp = piAlive();
      if (piUp) {
        lastPiOk = millis();
        piUpSecs = piUptimeS(); piUpAtMillis = millis();   // ดึง uptime จริงของ Pi มาโชว์
      } else {
        piUpSecs = -1;                               // Pi ดับ → uptime ไม่รู้
        if (millis() - lastPiOk >= DOWN_MS && millis() - lastCycle >= COOLDOWN_MS) {
          powerCycle("no signal 15m");
          drawStatic();
        }
      }
      int c = coffeeGet("/coffee");                  // sync สถานะ coffee break (เผื่อสลับจากที่อื่น) + redraw ถ้าเปลี่ยน
      if (c != coffeeOn) { coffeeOn = c; if (page == 0) drawCoffeeBtn(coffeeOn); }
    }
  }

  // --- รีเฟรช UI ~1 วิ ---
  if (millis() - lastDraw >= 1000) {
    lastDraw = millis();
    drawDynamic();
  }
  delay(20);
}
