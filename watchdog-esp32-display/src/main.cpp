// main.cpp — Pi power-cycle watchdog บนบอร์ด CYD2USB (Sunton ESP32-2432S028Rv3: ST7789 240x320 + XPT2046)
//
// 2 โหมดในเครื่องเดียว:
//   NORMAL — เฝ้า Pi (TCP :22) ทุก CHECK_MS. เงียบเกิน DOWN_MS (15 นาที) → auto power-cycle
//   BACKUP — แตะปุ่ม "RESET PI" บนจอ (กดยืนยัน 2 ครั้ง) → OFF → นับถอยหลัง OFF_SECONDS บนจอ → ON
//
// วาดด้วย TFT_eSPI ตรงๆ (ไม่ใช้ LVGL — เบา RAM, ไม่มี framebuffer, block ตอน countdown ได้).
// จอ = ST7789 บน HSPI (ตั้งใน platformio.ini) · touch = XPT2046 บน VSPI (bus แยก).
// คุมปลั๊กผ่าน HA Webhook (ha_switch.h → haWebhook) — ไม่ต้องมี token/auth (secret อยู่ใน URL).
//
// ⚠️ HA ต้องอยู่คนละเครื่องกับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้ → Pi ค้างดับถาวร.
//    ตอน HA ยังบน ADS-B Pi ให้ทดสอบกับปลั๊ก "ตัวอื่น" เท่านั้น. + ESP32 เสียบไฟคนละแหล่งกับ Pi.

#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
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

// --- ปุ่ม RESET ---
#define BTN_X 40
#define BTN_Y 165
#define BTN_W 240
#define BTN_H 60

TFT_eSPI tft = TFT_eSPI();
SPIClass touchSpi(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS, XPT2046_IRQ);

bool wifiUp = false, piUp = true;
unsigned long lastPiOk = 0, lastCheck = 0, lastCycle = 0, lastDraw = 0, confirmUntil = 0;

// สถานะที่วาดล่าสุด (กัน flicker: วาดใหม่เฉพาะส่วนที่เปลี่ยน — เลขเปลี่ยนทุกวิ, "Pi OK" นิ่ง)
bool forceUI = true, dWifi = false, dPiUp = false;
char dLine[64] = {0};

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

void drawStatic() {   // header + ปุ่ม (วาดครั้งเดียว/เมื่อเปลี่ยนโหมด)
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.drawString("Pi WATCHDOG", 6, 6);
  drawButton(confirmUntil && millis() < confirmUntil);
  forceUI = true;     // หลังล้างจอ → drawDynamic วาดทุกส่วนใหม่ 1 รอบ
}

void drawDynamic() {  // วาดใหม่เฉพาะส่วนที่เปลี่ยน → ไม่ flicker (เลขเปลี่ยนทุกวิ, "Pi OK" นิ่ง)
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
  char line[64];                                             // บรรทัดรายละเอียด: วาดเฉพาะเมื่อข้อความเปลี่ยน
  unsigned long ago = (millis() - lastPiOk) / 1000;
  if (piUp) snprintf(line, sizeof(line), "last ok %lus ago", ago);
  else      snprintf(line, sizeof(line), "down %lum%02lus  (reset at %lum)", ago / 60, ago % 60, DOWN_MS / 60000);
  if (forceUI || strcmp(line, dLine) != 0) {
    tft.fillRect(0, 113, SCREEN_W, 15, TFT_BLACK);           // ล้างเฉพาะแถบบางๆ ของบรรทัดนี้
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(line, 160, 120);
    strncpy(dLine, line, sizeof(dLine) - 1);
  }
  forceUI = false;
}

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

  // DRY_RUN=1 → ทดสอบ UI ไม่ยิง HA จริง — โชว์ countdown ครบแต่ไม่ตัดไฟ
  bool offOk;
  if (DRY_RUN) {
    offOk = true;
  } else {
    offOk = false;
    for (int i = 0; i < 3 && !offOk; i++) {          // retry เผื่อ request แรกพลาด
      offOk = haWebhook(HA_WEBHOOK_OFF);   // ยิง webhook → HA turn_off ปลั๊ก
      if (!offOk) delay(1500);
    }
  }
  const char* offMsg = DRY_RUN ? "DRY RUN - no real cut"
                               : (offOk ? "Pi OFF - power back in..." : "HA OFF FAILED - check token/entity");
  for (int s = OFF_SECONDS; s > 0; s--) {            // นับถอยหลังบนจอ (block ได้ — ไม่มี LVGL ต้อง service)
    tft.fillRect(0, 90, SCREEN_W, 110, TFT_BLACK);
    tft.setTextColor(offOk ? TFT_YELLOW : TFT_RED, TFT_BLACK);
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
  tft.drawString("POWER ON", 160, 130);
  if (!DRY_RUN) {
    for (int i = 0; i < 3; i++) {
      if (haWebhook(HA_WEBHOOK_ON)) break;   // ยิง webhook → HA turn_on ปลั๊ก
      delay(1500);
    }
  }
  delay(2000);
  unsigned long now = millis();                      // รีเซ็ตตัวนับ — ให้ Pi boot ก่อน
  lastPiOk = now; lastCycle = now; lastCheck = now; piUp = true;
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
  configTime(7 * 3600, 0, "pool.ntp.org");           // BKK UTC+7 (field 't' ของ Tuya)

  unsigned long now = millis();
  lastPiOk = now; lastCheck = 0; lastDraw = 0;
  lastCycle = now - COOLDOWN_MS;                      // ยอม auto-cycle ครั้งแรกได้เมื่อครบ DOWN_MS
  piUp = true;
  drawStatic();
}

void loop() {
  // --- touch (ปุ่ม RESET + ยืนยัน 2 ครั้ง) ---
  int16_t tx, ty;
  if (readTouch(tx, ty)) {
    if (tx >= BTN_X && tx <= BTN_X + BTN_W && ty >= BTN_Y && ty <= BTN_Y + BTN_H) {
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
    drawButton(false);
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
      piUp = piAlive();
      if (piUp) {
        lastPiOk = millis();
      } else if (millis() - lastPiOk >= DOWN_MS && millis() - lastCycle >= COOLDOWN_MS) {
        powerCycle("no signal 15m");
        drawStatic();
      }
    }
  }

  // --- รีเฟรช UI ~1 วิ ---
  if (millis() - lastDraw >= 1000) {
    lastDraw = millis();
    drawDynamic();
  }
  delay(20);
}
