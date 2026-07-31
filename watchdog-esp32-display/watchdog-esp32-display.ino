// watchdog-esp32-display.ino — Pi power-cycle watchdog บนบอร์ด CYD (ESP32-2432S028R, จอ 2.8" + touch)
//
// 2 โหมดในเครื่องเดียว:
//   NORMAL — เฝ้า Pi (TCP :22) ทุก CHECK_MS. เงียบเกิน DOWN_MS (15 นาที) → auto power-cycle ปลั๊ก Tuya
//   BACKUP — แตะปุ่ม "RESET PI" บนจอ (กดยืนยัน 2 ครั้ง) → Tuya OFF → นับถอยหลัง OFF_SECONDS บนจอ → Tuya ON
//
// ⚠️ ESP32 ต้องเสียบไฟ "คนละแหล่ง" กับ Pi (ห้ามเสียบปลั๊ก Tuya ตัวที่มันจะตัด!) + ตั้ง power-on state ปลั๊ก = ON
// จอ: TFT_eSPI (ตั้ง User_Setup สำหรับ CYD — ดู README) · ทัช: XPT2046 · Tuya v3.3 local: tuya.h (mbedtls)

#include <SPI.h>
#include <WiFi.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>
#include <time.h>
#include "config.h"
#include "tuya.h"

// --- CYD (ESP32-2432S028R) touch pins (XPT2046 บน bus แยก) ---
#define T_CS 33
#define T_IRQ 36
#define T_CLK 25
#define T_MOSI 32
#define T_MISO 39

// --- touch calibration (ค่า raw ~) — ปรับตามที่ Serial print ถ้าปุ่มกดไม่ตรง ---
#define RAW_XMIN 200
#define RAW_XMAX 3700
#define RAW_YMIN 240
#define RAW_YMAX 3800

// --- ปุ่ม RESET (landscape 320x240) ---
#define BTN_X 40
#define BTN_Y 165
#define BTN_W 240
#define BTN_H 60

TFT_eSPI tft = TFT_eSPI();
SPIClass touchSPI(HSPI);
XPT2046_Touchscreen ts(T_CS, T_IRQ);

bool wifiUp = false, piUp = true;
unsigned long lastPiOk = 0, lastCheck = 0, lastCycle = 0, lastDraw = 0, confirmUntil = 0;

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
  sx = constrain(map(p.x, RAW_XMIN, RAW_XMAX, 0, 320), 0, 319);
  sy = constrain(map(p.y, RAW_YMIN, RAW_YMAX, 0, 240), 0, 239);
  Serial.printf("touch raw=(%d,%d) -> screen=(%d,%d)\n", p.x, p.y, sx, sy);  // ใช้ปรับ calibration
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
}

void drawDynamic() {  // สถานะ + รายละเอียด (รีเฟรช ~1 วิ, เคลียร์เฉพาะโซนกัน flicker)
  tft.fillCircle(305, 14, 6, wifiUp ? TFT_GREEN : TFT_RED);   // WiFi dot
  tft.fillRect(0, 45, 320, 110, TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextSize(4);
  tft.setTextColor(piUp ? TFT_GREEN : TFT_RED, TFT_BLACK);
  tft.drawString(piUp ? "Pi OK" : "Pi DOWN", 160, 75);
  tft.setTextSize(1);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  char line[64];
  unsigned long ago = (millis() - lastPiOk) / 1000;
  if (piUp) snprintf(line, sizeof(line), "last ok %lus ago", ago);
  else      snprintf(line, sizeof(line), "down %lum%02lus  (reset at %lum)", ago / 60, ago % 60, DOWN_MS / 60000);
  tft.drawString(line, 160, 120);
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

  bool offOk = false;
  for (int i = 0; i < 3 && !offOk; i++) {            // retry เผื่อ frame แรกหาย
    offOk = tuyaSet(TUYA_IP, TUYA_ID, TUYA_KEY, false, TUYA_DP);
    if (!offOk) delay(1500);
  }
  for (int s = OFF_SECONDS; s > 0; s--) {            // นับถอยหลังบนจอ
    tft.fillRect(0, 90, 320, 110, TFT_BLACK);
    tft.setTextColor(offOk ? TFT_YELLOW : TFT_RED, TFT_BLACK);
    tft.setTextSize(6);
    char buf[8]; snprintf(buf, sizeof(buf), "%d", s);
    tft.drawString(buf, 160, 130);
    tft.setTextSize(1);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(offOk ? "Pi OFF - power back in..." : "TUYA OFF FAILED - check plug", 160, 190);
    delay(1000);
  }
  tft.fillRect(0, 90, 320, 130, TFT_BLACK);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);
  tft.setTextSize(3);
  tft.drawString("POWER ON", 160, 130);
  for (int i = 0; i < 3; i++) {
    if (tuyaSet(TUYA_IP, TUYA_ID, TUYA_KEY, true, TUYA_DP)) break;
    delay(1500);
  }
  delay(2000);
  unsigned long now = millis();                      // รีเซ็ตตัวนับ — ให้ Pi boot ก่อน
  lastPiOk = now; lastCycle = now; lastCheck = now; piUp = true;
}

// ---------- setup / loop ----------
void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(1);                                // landscape 320x240
  tft.fillScreen(TFT_BLACK);
  touchSPI.begin(T_CLK, T_MISO, T_MOSI, T_CS);
  ts.begin(touchSPI);
  ts.setRotation(1);

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
