// main.cpp — panel เปิด/ปิดไฟในห้อง + Pi power-cycle watchdog (เบื้องหลัง)
// บอร์ด CYD2USB (Sunton ESP32-2432S028Rv3: ST7789 240x320 + XPT2046)
//
// จอมี 3 หน้า:
//   PANEL — ปุ่มไฟ 1–6 ปุ่ม (LIGHTS_INIT ใน config.h) สั่ง + อ่านสถานะจริงผ่าน HA REST API (ha_switch.h)
//   SAVER — screensaver: นาฬิกา + พยากรณ์อากาศชุดเดียวกับหน้า WX ของ Pixoo (Pi → :8099/weather).
//           ขึ้นเองเมื่อไม่แตะจอ IDLE_MS. แตะ 1 ครั้ง = กลับ PANEL เฉย ๆ (ยังไม่สั่งไฟ — กันแตะปลุกจอแล้วไฟดับ)
//   PI    — หน้า watchdog เดิม: Pi OK/DOWN, uptime, ปุ่ม RESET PI (ยืนยัน 2 ครั้ง), ปุ่ม COFFEE.
//           เข้าจากมุมขวาบนของ PANEL
//
// watchdog ทำงานตลอด ไม่ขึ้นกับหน้าที่โชว์ (เหมือนเดิมทุกอย่าง — แค่ไม่มีหน้าจอของตัวเองเป็นหน้าหลักแล้ว):
//   เฝ้า Pi (TCP :22) ทุก CHECK_MS. เงียบเกิน DOWN_MS (20 นาที) → ยิง 1 webhook → HA off→delay→on เอง.
//   ปุ่มไฟใช้ token, การตัดไฟ Pi ใช้ webhook — แยกกันโดยตั้งใจ: token หาย/หมดอายุ ปุ่มไฟใช้ไม่ได้
//   แต่ backstop ยังยิงได้.
//
// วาดด้วย TFT_eSPI ตรงๆ (ไม่ใช้ LVGL — เบา RAM, ไม่มี framebuffer, block ตอน countdown ได้).
// จอ = ST7789 บน HSPI (ตั้งใน platformio.ini) · touch = XPT2046 บน VSPI (bus แยก).
//
// ⚠️ HA ต้องอยู่คนละเครื่องกับ Pi ที่จะตัด — ไม่งั้นสั่ง OFF แล้ว HA ตายตาม สั่ง ON ไม่ได้ → Pi ค้างดับถาวร.
//    + ESP32 เสียบไฟคนละแหล่งกับ Pi.
//
// ตำแหน่งใน pi-fleet (3 ชั้นกู้ Pi#1 ไม่ทับกัน — ดู shared/pylib/fleet_mqtt.py):
//   1) fr24-watchdog (บน Pi#1)       — กู้ dongle ระดับแอปตอน Pi#1 ยังปกติ
//   2) peer-watchdog (บน Pi#2, MQTT) — L3 ตัดไฟที่ 600s + รอ 240s → เสร็จ ~14 นาที + boot ~16 นาที
//   3) ESP32 (ตัวนี้, ไม่พึ่ง MQTT)  — backstop สุดท้าย: ยิงที่ DOWN_MS=20 นาที = "หลัง" ชั้น 2 ลองครบ
//      แล้วเอาไม่อยู่ — หรือกรณีที่ peer-watchdog/broker ตาย (ชั้น 2 ทำอะไรไม่ได้เลย) ESP32 เป็นทางเดียวที่กู้ได้.
//      (สิ่งเดียวที่ ESP32 ต้องพึ่งคือ HA ยังรับ webhook ได้ — HA อยู่บน Pi#2 ดังนั้นถ้า Pi#2 ดับทั้งเครื่อง
//      พร้อม Pi#1 แฮงก์ ไม่มีชั้นไหนตัดไฟได้; ทางออกของกรณีนั้นคือ tuya.h สั่งปลั๊กตรง ไม่ผ่าน HA)
//      ∴ DOWN_MS ต้อง > POWER_CYCLE_S(600)+POST_POWER_WAIT_S(240)+boot ไม่งั้นยิงซ้อนตอน Pi กำลัง boot.
//   ESP32 คง "ไม่พึ่ง MQTT" ตั้งใจ (จะได้กู้ได้แม้ทั้ง fleet ล่ม). ให้ action โผล่ใน fleet/incident โดย
//   ฝั่ง HA (automation ที่รับ webhook) publish incident แทน — ไม่ต้องแตะโค้ด ESP32 (ดู README).

#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>
#include <time.h>
#include <ctype.h>
#ifndef PANEL_SIM            // ตัวจำลองบน PC (sim/) ใส่ config ของมันเอง — ต้องไม่หยิบ config.h จริงที่มี token มาใช้
#include "config.h"
#endif
#include "panel_logic.h"
#include "ha_switch.h"

// config.h อยู่นอก git → เครื่องที่ pull โค้ดใหม่ยังถือ config.h รุ่น watchdog ล้วนอยู่. ให้พังตอน build
// พร้อมบอกว่าขาดอะไร ดีกว่าได้ error ยาวเหยียดจากบรรทัดที่ใช้ค่าพวกนี้.
#if !defined(HA_BASE) || !defined(HA_TOKEN) || !defined(LIGHTS_INIT)
#error "src/config.h เป็นรุ่นเก่า: เพิ่ม HA_BASE, HA_TOKEN, LIGHTS_INIT ตาม src/config.h.example"
#endif

// --- จูน panel (ตั้งทับใน config.h ได้) ---
#ifndef IDLE_MS
#define IDLE_MS            45000UL   // ไม่แตะจอนานเท่านี้ → screensaver
#endif
#ifndef LIGHT_REFRESH_MS
#define LIGHT_REFRESH_MS   4000UL    // อ่านสถานะไฟ "ครบทุกดวง" ทุกกี่ ms ขณะอยู่หน้า PANEL (ถามทีละดวง เวียนไป)
#endif
#ifndef LIGHT_SETTLE_MS
#define LIGHT_SETTLE_MS    2500UL    // หลังสั่ง: เชื่อค่าที่เพิ่งสั่งไปก่อนนานเท่านี้ (อุปกรณ์ cloud รายงานกลับช้า)
#endif
#ifndef HA_TIMEOUT_MS
#define HA_TIMEOUT_MS      2500      // อ่านสถานะ 1 ครั้ง
#endif
#ifndef HA_CMD_TIMEOUT_MS
#define HA_CMD_TIMEOUT_MS  6000      // สั่งเปิด/ปิด 1 ครั้ง (HA รออุปกรณ์ตอบก่อนคืนผล — นานกว่าอ่าน)
#endif
#ifndef HA_BACKOFF_MS
#define HA_BACKOFF_MS      15000UL   // HA ไม่ตอบ → พักถามนานเท่านี้ (ทุกครั้งที่ถามแล้วไม่ตอบ จอทัชนิ่งไป HA_TIMEOUT_MS)
#endif
#ifndef HA_AUTH_BACKOFF_MS
#define HA_AUTH_BACKOFF_MS 60000UL   // token ผิด (401/403) → ถามซ้ำช้า ๆ: ทุกครั้งที่ผิด HA จด "login failed" ไว้
#endif
#ifndef WX_FETCH_MS
#define WX_FETCH_MS        600000UL  // ดึงพยากรณ์จาก Pi ทุก 10 นาที (Pi เองอัปเดตชั่วโมงละครั้ง)
#endif
#ifndef WX_STALE_S
#define WX_STALE_S         21600L    // พยากรณ์เก่ากว่า 6 ชม. = ไม่โชว์ (ค่าเดียวกับ WX_STALE_SEC ของ Pixoo)
#endif

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

#define TAP_GAP_MS  250          // เว้นขั้นต่ำระหว่าง 2 แตะ (debounce เดิม)
#define RELEASE_MS  80           // ต้องยกนิ้วนานเท่านี้ถึงนับเป็นแตะครั้งใหม่

static const int16_t SCREEN_W = 320;   // landscape (rotation 1)
static const int16_t SCREEN_H = 240;

#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

// --- หน้า PI: ปุ่ม RESET ---
#define BTN_X 40
#define BTN_Y 165
#define BTN_W 240
#define BTN_H 60

// --- หน้า PI: ปุ่ม COFFEE (สลับ coffee break ของ Pixoo ผ่าน endpoint บน Pi :PI_INFO_PORT) ---
#define CBTN_X 176
#define CBTN_Y 26
#define CBTN_W 138
#define CBTN_H 28

// --- หน้า PI: ปุ่มกลับหน้าไฟ ---
static const PRect BACK_BTN = {6, 26, 104, 28};

// --- หน้า PANEL: ป้ายสถานะ Pi มุมขวาบน (แตะ = ไปหน้า PI). เขตแตะกว้างกว่าป้ายที่วาด — นิ้วใหญ่กว่า 22 px ---
static const PRect PI_CHIP     = {202, 3, 88, 22};
static const PRect PI_CHIP_HIT = {186, 0, 134, GRID_Y};

// --- สีปุ่มไฟ: สถานะบอกด้วย "สี + คำ" เสมอ (ON/OFF) — ไม่พึ่งสีอย่างเดียว ---
static const uint16_t C_ON_BG   = RGB565(255, 196, 40);
static const uint16_t C_OFF_BG  = RGB565(38, 46, 58);
static const uint16_t C_UNK_BG  = RGB565(22, 24, 28);
static const uint16_t C_PEND_BG = RGB565(70, 80, 96);
static const uint16_t C_EDGE    = RGB565(90, 100, 116);
static const uint16_t C_PI_OK   = RGB565(0, 80, 36);
// --- สีพยากรณ์: ชุดเดียวกับ pixoo/pages.py + pixoo/renderer.py (สองจอต้องอ่านเป็นระบบเดียวกัน) ---
static const uint16_t C_WARM = RGB565(240, 150, 70);    // อุณหภูมิสูงสุด
static const uint16_t C_COOL = RGB565(127, 216, 232);   // อุณหภูมิต่ำสุด
static const uint16_t C_RAIN = RGB565(90, 159, 224);
static const uint16_t C_GOLD = RGB565(240, 200, 90);
static const uint16_t C_LINE = RGB565(40, 44, 52);

enum { PAGE_PANEL = 0, PAGE_SAVER = 1, PAGE_PI = 2 };
enum { VIS_NONE = -9, VIS_UNKNOWN = -1, VIS_OFF = 0, VIS_ON = 1, VIS_PENDING = 2, VIS_ERROR = 3 };

struct LightDef { const char* label; const char* entity; };
static const LightDef LIGHTS[] = LIGHTS_INIT;
static const int N_LIGHTS = sizeof(LIGHTS) / sizeof(LIGHTS[0]);
static_assert(sizeof(LIGHTS) / sizeof(LIGHTS[0]) >= 1 && sizeof(LIGHTS) / sizeof(LIGHTS[0]) <= 6,
              "LIGHTS_INIT ใน config.h ต้องมี 1 ถึง 6 ปุ่ม");

TFT_eSPI tft = TFT_eSPI();
SPIClass touchSpi(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS, XPT2046_IRQ);

bool wifiUp = false, piUp = true;
unsigned long lastPiOk = 0, lastCheck = 0, lastCycle = 0, lastDraw = 0, confirmUntil = 0;

// สถานะที่วาดล่าสุด (กัน flicker: วาดใหม่เฉพาะส่วนที่เปลี่ยน — เลขเปลี่ยนทุกวิ, "Pi OK" นิ่ง)
bool forceUI = true, dWifi = false, dPiUp = false;
char dLine[64] = {0}, dLine2[64] = {0};   // หน้า PI: dLine = บรรทัด Pi up, dLine2 = บรรทัด last check

int page = PAGE_PANEL;
unsigned long lastActivity = 0;           // แตะจอครั้งล่าสุด → ครบ IDLE_MS ไป screensaver
bool touchHeld = false;                   // นิ้วยังค้างอยู่จากแตะที่นับไปแล้ว (1 แตะ = 1 คำสั่ง ไม่ว่ากดค้างนานแค่ไหน)
unsigned long lastTouchSeen = 0, lastTapMs = 0;
int16_t rawX = 0, rawY = 0;               // ค่าดิบของแตะล่าสุด (พิมพ์ลง Serial ไว้ใช้ calibrate)

// เวลาจริง (epoch) ของการเช็ค Pi รอบล่าสุด — โชว์ "last check HH:MM:SS" อัปเดตทุก CHECK_MS (30 วิ)
time_t lastCheckEpoch = 0;
// uptime จริงของ Pi (วินาที) ดึงจาก endpoint บน Pi ทุกรอบเช็ค — -1 = ยังไม่ได้/ดึงไม่ได้
long piUpSecs = -1;
unsigned long piUpAtMillis = 0;   // millis ตอนได้ค่า piUpSecs (ไว้ interpolate ระหว่างรอบเช็ค)
int coffeeOn = -1;                // coffee break ของ Pixoo: 1=on / 0=off / -1=ยังไม่รู้ (ดึงจาก /coffee)

// --- ไฟ ---
int8_t lightState[6];             // 1 on · 0 off · -1 ไม่รู้
int8_t lightDrawn[6];             // VIS_* ที่วาดอยู่บนจอ
int    lightCode[6];              // HTTP code ล่าสุดของดวงนั้น (404 = entity_id ผิด)
bool   lightCmdPending[6];        // เพิ่งสั่งไป → ยังไม่ให้ค่าที่อ่านได้มาทับ จนครบ LIGHT_SETTLE_MS
unsigned long lightCmdAt[6];
int pollIdx = 0, pollBurst = 0;   // pollBurst > 0 = ถามติดกันทุก loop (ตอนเพิ่งเข้าหน้า PANEL) ไม่รอรอบ
unsigned long lastLightPoll = 0;
// ปัญหาระดับ HA ทั้งตัว (ไม่ใช่รายดวง): 0 = ปกติ · 1 = ต่อไม่ได้ · 2 = token ไม่ผ่าน
int haFault = 0, dHaFault = -1;
bool haBackoff = false;
unsigned long haFailAt = 0, haBackoffMs = 0;

// --- พยากรณ์ ---
WxData wx;                        // ชุดล่าสุดที่ดึงได้ (ts = 0 → ไม่มี)
bool wxFetched = false;
unsigned long lastWxFetch = 0;
char dClk[8] = {0}, dDate[24] = {0}, dWxSig[40] = {0}, dSaverLine[48] = {0};   // cache หน้า SAVER

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

// timeout ของทุก request ไป endpoint :PI_INFO_PORT บน Pi. ตั้ง "connect" ด้วย ไม่ใช่แค่ตอนรออ่าน: ถ้า Pi ดับ
// ระหว่างทาง การ "ต่อ" ที่ไม่มี timeout จะค้างตามค่า default ของ TCP stack และปุ่มไฟจะกดไม่ติดไปทั้งช่วงนั้น
// (ตอนเป็น watchdog ล้วนไม่มีใครสังเกต เพราะไม่มีอะไรให้กดนอกจาก RESET).
void piInfoTimeouts(HTTPClient& http) {
  http.setConnectTimeout(TCP_TIMEOUT_MS);
  http.setTimeout(TCP_TIMEOUT_MS);
}

// uptime จริงของ Pi (วินาที) จาก endpoint adsb-uptime.service — คืน -1 ถ้าดึงไม่ได้ (service ยังไม่ลง/ล่ม)
// display-only + best-effort: ไม่ใช่ตัวตัดสิน reset (liveness ยังใช้ piAlive/TCP:22 เพราะ server อาจล่มขณะ Pi ปกติ)
long piUptimeS() {
  HTTPClient http;
  char url[48];
  snprintf(url, sizeof(url), "http://%s:%d/", PI_IP, PI_INFO_PORT);
  if (!http.begin(url)) return -1;
  piInfoTimeouts(http);
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
  piInfoTimeouts(http);
  int r = (http.GET() == 200) ? http.getString().toInt() : -1;
  http.end();
  return r;
}

// พยากรณ์ บ้าน+ปลายทาง จาก Pi (/weather). ดึงไม่ได้/รูปแบบผิด → เก็บชุดเดิมไว้ (จะหมดอายุเองตาม ts ของมัน)
// display-only เหมือน uptime: เรียกเฉพาะรอบที่เพิ่งเช็คแล้วว่า Pi ยังอยู่ จึงไม่มีทางไปหน่วงการเฝ้า Pi.
bool fetchWeather() {
  HTTPClient http;
  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/weather", PI_IP, PI_INFO_PORT);
  if (!http.begin(url)) return false;
  piInfoTimeouts(http);
  bool ok = false;
  if (http.GET() == 200) {
    WxData w;
    if (wxParse(http.getString().c_str(), &w)) { wx = w; ok = true; }
  }
  http.end();
  return ok;
}

// ---------- touch ----------
bool readTouch(int16_t &sx, int16_t &sy) {
  if (!ts.tirqTouched() || !ts.touched()) return false;
  TS_Point p = ts.getPoint();
  rawX = p.x; rawY = p.y;
  sx = constrain((int16_t)map(p.x, TOUCH_X_MIN, TOUCH_X_MAX, 0, SCREEN_W - 1), 0, SCREEN_W - 1);
  sy = constrain((int16_t)map(p.y, TOUCH_Y_MIN, TOUCH_Y_MAX, 0, SCREEN_H - 1), 0, SCREEN_H - 1);
  return true;
}

// ---------- เวลา ----------
bool clockReady(time_t e) { return e > 1600000000; }   // NTP มาแล้ว (ไม่ใช่ปี 1970)

// วันที่ไทย "YYYY-MM-DD" ของวันนี้ + addDays (ไทยไม่มี DST → บวกทีละ 86400 ได้ตรง ๆ)
void dateStr(char* b, size_t n, int addDays) {
  time_t e = time(nullptr) + (time_t)addDays * 86400;
  struct tm t; localtime_r(&e, &t);
  strftime(b, n, "%Y-%m-%d", &t);
}

// =====================================================================
// หน้า PANEL — ปุ่มไฟ
// =====================================================================
void drawLightBtn(int i, int8_t vis) {
  PRect r = gridCell(N_LIGHTS, i);
  uint16_t bg, fg;
  const char* st;
  switch (vis) {
    case VIS_ON:      bg = C_ON_BG;   fg = TFT_BLACK;    st = "ON";    break;
    case VIS_OFF:     bg = C_OFF_BG;  fg = TFT_WHITE;    st = "OFF";   break;
    case VIS_PENDING: bg = C_PEND_BG; fg = TFT_WHITE;    st = "...";   break;
    case VIS_ERROR:   bg = TFT_RED;   fg = TFT_WHITE;    st = "ERROR"; break;
    default:          bg = C_UNK_BG;  fg = TFT_DARKGREY; st = (lightCode[i] == 404) ? "NO ENTITY" : "?"; break;
  }
  tft.fillRoundRect(r.x, r.y, r.w, r.h, 8, bg);
  if (vis == VIS_OFF || vis == VIS_UNKNOWN) tft.drawRoundRect(r.x, r.y, r.w, r.h, 8, C_EDGE);

  // ตัวอักษรใหญ่สุดที่ชื่อยังไม่ล้นปุ่ม (font GLCD: 6 px ต่อตัว ต่อ 1 size)
  int16_t fit = r.w - 10;
  int len = strlen(LIGHTS[i].label);
  uint8_t sz = (r.h >= 150 && len * 18 <= fit) ? 3 : (len * 12 <= fit ? 2 : 1);
  uint8_t stSz = ((int)strlen(st) * 12 <= fit) ? 2 : 1;
  int16_t cx = r.x + r.w / 2, cy = r.y + r.h / 2;
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(fg, bg);
  tft.setTextSize(sz);
  tft.drawString(LIGHTS[i].label, cx, cy - 4 - 4 * sz);
  tft.setTextSize(stSz);
  tft.drawString(st, cx, cy + 6 + 8 * stSz);
  lightDrawn[i] = vis;
}

void drawPiChip() {
  uint16_t bg = piUp ? C_PI_OK : TFT_RED;
  tft.fillRoundRect(PI_CHIP.x, PI_CHIP.y, PI_CHIP.w, PI_CHIP.h, 6, bg);
  tft.setTextDatum(MC_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(TFT_WHITE, bg);
  tft.drawString(piUp ? "Pi OK >" : "Pi DOWN >", PI_CHIP.x + PI_CHIP.w / 2, PI_CHIP.y + PI_CHIP.h / 2);
}

void drawPanelStatic() {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.drawString("LIGHTS", 6, 6);
  for (int i = 0; i < N_LIGHTS; i++) lightDrawn[i] = VIS_NONE;
  dHaFault = -1;
  forceUI = true;
}

void drawPanelDynamic() {  // วาดเฉพาะส่วนที่เปลี่ยน
  if (forceUI || dWifi != wifiUp) {
    tft.fillCircle(305, 14, 6, wifiUp ? TFT_GREEN : TFT_RED);
    dWifi = wifiUp;
  }
  if (forceUI || dPiUp != piUp) {
    drawPiChip();
    dPiUp = piUp;
  }
  if (forceUI || dHaFault != haFault) {   // ปัญหาของ HA ทั้งตัว — บอกสาเหตุ ไม่ใช่แค่ปุ่มขึ้น "?" ทุกดวง
    tft.fillRect(84, 6, 100, 16, TFT_BLACK);
    if (haFault) {
      tft.setTextDatum(TL_DATUM);
      tft.setTextSize(1);
      tft.setTextColor(TFT_RED, TFT_BLACK);
      tft.drawString(haFault == 2 ? "HA TOKEN?" : "HA OFFLINE", 86, 10);
    }
    dHaFault = haFault;
  }
  for (int i = 0; i < N_LIGHTS; i++)
    if (lightDrawn[i] != lightState[i]) drawLightBtn(i, lightState[i]);
  forceUI = false;
}

// HA ไม่ตอบ / token ไม่ผ่าน → ทุกดวงกลายเป็น "ไม่รู้" (ไม่โชว์ค่าเก่าเป็นค่าจริง) + พักถาม
void haFailed(int fault) {
  haFault = fault;
  haBackoff = true;
  haFailAt = millis();
  haBackoffMs = (fault == 2) ? HA_AUTH_BACKOFF_MS : HA_BACKOFF_MS;
  for (int i = 0; i < N_LIGHTS; i++) { lightState[i] = -1; lightCode[i] = 0; lightCmdPending[i] = false; }
}

// อ่านสถานะไฟ "ทีละดวง" ต่อ 1 loop (เวียนไป) — แต่ละครั้ง block สั้น ๆ จอทัชจึงยังตอบสนองระหว่างรอบ
void pollLights() {
  unsigned long now = millis();
  if (haBackoff) {
    if (now - haFailAt < haBackoffMs) return;
    haBackoff = false;
    pollBurst = N_LIGHTS;                 // กลับมาถามใหม่ครบทุกดวงทันที
  }
  unsigned long step = LIGHT_REFRESH_MS / N_LIGHTS;
  if (!pollBurst && now - lastLightPoll < step) return;
  lastLightPoll = now;
  if (pollBurst) pollBurst--;

  int i = pollIdx;
  pollIdx = (pollIdx + 1) % N_LIGHTS;
  if (lightCmdPending[i]) {
    if (now - lightCmdAt[i] < LIGHT_SETTLE_MS) return;   // เพิ่งสั่ง — รออุปกรณ์รายงานกลับก่อน
    lightCmdPending[i] = false;
  }
  int code = 0;
  int s = haState(HA_BASE, HA_TOKEN, LIGHTS[i].entity, HA_TIMEOUT_MS, &code);
  if (code <= 0)                      haFailed(1);
  else if (code == 401 || code == 403) haFailed(2);
  else {
    haFault = 0;
    if (lightCode[i] != code) lightDrawn[i] = VIS_NONE;   // "?" ↔ "NO ENTITY" ใช้ VIS เดียวกัน → บังคับวาดใหม่
    lightCode[i] = code;
    lightState[i] = s;
  }
  drawPanelDynamic();
}

void tapLight(int i) {
  int8_t cur = lightState[i];
  drawLightBtn(i, VIS_PENDING);
  // รู้สถานะ → สั่งแบบระบุปลายทาง (เปิด/ปิด) ตามที่จอโชว์ · ไม่รู้ → toggle (ดูเหตุผลที่ haToggle)
  bool ok = (cur == 1) ? haSwitch(HA_BASE, HA_TOKEN, LIGHTS[i].entity, false, HA_CMD_TIMEOUT_MS)
          : (cur == 0) ? haSwitch(HA_BASE, HA_TOKEN, LIGHTS[i].entity, true,  HA_CMD_TIMEOUT_MS)
                       : haToggle(HA_BASE, HA_TOKEN, LIGHTS[i].entity, HA_CMD_TIMEOUT_MS);
  if (ok) {
    if (cur >= 0) lightState[i] = cur ? 0 : 1;   // โชว์ค่าที่สั่งไปก่อน แล้วให้รอบอ่านถัดไปยืนยัน/แก้
    lightCmdPending[i] = true;
    lightCmdAt[i] = millis();
    haFault = 0;
    haBackoff = false;
  } else {
    drawLightBtn(i, VIS_ERROR);                  // สั่งไม่สำเร็จ → บอกตรง ๆ ไม่ทำเป็นว่าเปลี่ยนแล้ว
    delay(900);
    pollBurst = N_LIGHTS;                        // แล้วอ่านใหม่ทั้งชุดว่าตอนนี้จริง ๆ เป็นยังไง
  }
  if (page == PAGE_PANEL) drawPanelDynamic();
}

// =====================================================================
// หน้า SAVER — นาฬิกา + พยากรณ์อากาศ
// =====================================================================
// ไอคอน 7x7 ชุดเดียวกับ Pixoo (pixoo/renderer.py _WX_ICONS) ขยายเป็นบล็อก — ตัวอักษร = สี:
// S แดด · C เมฆ · R ฝน · B ฟ้าผ่า · W หิมะ · F หมอก
static const char* const WX_SUN[7]    = {"...S...", ".S...S.", "..SSS..", "S.SSS.S", "..SSS..", ".S...S.", "...S..."};
static const char* const WX_PARTLY[7] = {".S.S...", "..SSS..", ".SSCC..", "..CCCC.", ".CCCCCC", "CCCCCCC", "......."};
static const char* const WX_CLOUDY[7] = {".......", ".......", "..CC...", ".CCCCC.", "CCCCCCC", "CCCCCCC", "......."};
static const char* const WX_RAIN[7]   = {"..CC...", ".CCCCC.", "CCCCCCC", "CCCCCCC", ".......", ".R.R.R.", "R.R.R.."};
static const char* const WX_HEAVY[7]  = {"..CC...", ".CCCCC.", "CCCCCCC", "CCCCCCC", "R.R.R.R", ".R.R.R.", "R.R.R.R"};
static const char* const WX_STORM[7]  = {"..CC...", ".CCCCC.", "CCCCCCC", "CCCCCCC", "...BB..", "..BB...", "...B..."};
static const char* const WX_SNOW[7]   = {"..CC...", ".CCCCC.", "CCCCCCC", "CCCCCCC", ".......", ".W.W.W.", "W.W.W.."};
static const char* const WX_FOG[7]    = {".......", "FFFFFF.", ".......", ".FFFFFF", ".......", "FFFFFF.", "......."};

// kind ไม่รู้จัก = ไม่วาด (ไม่เดา) — เหมือน draw_wx ของ Pixoo
void drawWx(int16_t x, int16_t y, uint8_t s, const char* kind) {
  const char* const* rows = nullptr;
  uint16_t sun = RGB565(245, 200, 60);
  if      (!strcmp(kind, "clear"))  rows = WX_SUN;
  else if (!strcmp(kind, "cool"))   { rows = WX_SUN; sun = C_COOL; }             // หนาว/เย็น = แดดสีฟ้า (ไม่ใช่หิมะ)
  else if (!strcmp(kind, "hot"))    { rows = WX_SUN; sun = RGB565(240, 100, 50); }
  else if (!strcmp(kind, "partly")) rows = WX_PARTLY;
  else if (!strcmp(kind, "cloudy")) rows = WX_CLOUDY;
  else if (!strcmp(kind, "rain"))   rows = WX_RAIN;
  else if (!strcmp(kind, "heavy"))  rows = WX_HEAVY;
  else if (!strcmp(kind, "storm"))  rows = WX_STORM;
  else if (!strcmp(kind, "snow"))   rows = WX_SNOW;
  else if (!strcmp(kind, "fog"))    rows = WX_FOG;
  if (!rows) return;
  for (int ry = 0; ry < 7; ry++)
    for (int rx = 0; rx < 7; rx++) {
      uint16_t c;
      switch (rows[ry][rx]) {
        case 'S': c = sun; break;
        case 'C': c = RGB565(170, 180, 195); break;
        case 'R': c = C_RAIN; break;
        case 'B': c = RGB565(245, 220, 70); break;
        case 'W': c = RGB565(235, 240, 250); break;
        case 'F': c = RGB565(130, 140, 155); break;
        default:  continue;
      }
      tft.fillRect(x + rx * s, y + ry * s, s, s, c);
    }
}

// "สูงสุด/ต่ำสุด" สองสี ชิดซ้ายที่ (x,y). ยาวเกิน 5 ตัว (เช่น -12/-20) ลดขนาดลง 1 ขั้น ไม่ให้ล้นช่อง
void drawHiLo(int16_t x, int16_t y, uint8_t sz, int hi, int lo) {
  char a[8], b[8];
  snprintf(a, sizeof(a), "%d", hi);
  snprintf(b, sizeof(b), "%d", lo);
  if (strlen(a) + strlen(b) + 1 > 5 && sz > 1) sz--;
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(sz);
  tft.setTextColor(C_WARM, TFT_BLACK);     tft.drawString(a, x, y);   x += strlen(a) * 6 * sz;
  tft.setTextColor(TFT_DARKGREY, TFT_BLACK); tft.drawString("/", x, y); x += 6 * sz;
  tft.setTextColor(C_COOL, TFT_BLACK);     tft.drawString(b, x, y);
}

void fmtRain(char* b, size_t n, const WxDay& d) {     // ปริมาณฝน: TMD มีแต่ มม. · Open-Meteo มี % ด้วย
  if (!d.hasMm)            b[0] = 0;
  else if (d.rainMm < 100) snprintf(b, n, "%.1fmm", d.rainMm);
  else                     snprintf(b, n, "%.0fmm", d.rainMm);   // 3 หลักขึ้นไป: ตัดทศนิยม ไม่ให้ล้นช่อง
}

// ช่องเล็กแถวล่าง: ป้าย (วัน / เมือง+วันที่) · ไอคอน · สูง/ต่ำ · ฝน. day = nullptr หรือไม่มีอุณหภูมิ → "--"
void drawWxCell(int16_t x0, const char* label, uint16_t labelCol, const WxDay* d) {
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(labelCol, TFT_BLACK);
  tft.drawString(label, x0, 164);
  if (!d || !d->hasT) {
    tft.setTextSize(2);
    tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
    tft.drawString("--", x0 + 27, 178);
    return;
  }
  drawWx(x0, 176, 3, d->kind);
  drawHiLo(x0 + 27, 178, 2, d->tmax, d->tmin);
  char r[16];
  if (d->hasPct) snprintf(r, sizeof(r), "%d%%", d->rainPct);
  else           fmtRain(r, sizeof(r), *d);
  tft.setTextSize(1);
  tft.setTextColor(C_RAIN, TFT_BLACK);
  tft.drawString(r, x0 + 27, 198);
}

// แถวกลาง (วันนี้ที่บ้าน) + แถวล่าง (2 วันถัดไป + ปลายทางของเที่ยวบินถัดไป)
void drawWxBlock(bool valid, const char* today) {
  tft.fillRect(0, 85, SCREEN_W, 137, TFT_BLACK);
  tft.drawFastHLine(6, 85, SCREEN_W - 12, C_LINE);
  if (!valid) {                                   // ยังไม่มี / เก่าเกิน / ยังไม่มีนาฬิกา → ไม่เดา
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(2);
    tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
    tft.drawString("WEATHER: NO DATA", 160, 150);
    return;
  }
  // --- วันนี้ ---
  const WxDay* d = wxHomeDay(wx, today);
  char buf[24];
  snprintf(buf, sizeof(buf), "%s TODAY", wx.homeLabel[0] ? wx.homeLabel : "HOME");
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
  tft.drawString(buf, 80, 92);
  tft.setTextDatum(TR_DATUM);
  tft.drawString(wx.homeSrc, 314, 92);            // ที่มาของข้อมูล (TMD / OM) มุมขวา
  if (d && d->hasT) {
    drawWx(12, 94, 8, d->kind);
    drawHiLo(80, 108, 4, d->tmax, d->tmin);
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
    tft.drawString("RAIN", 214, 108);
    fmtRain(buf, sizeof(buf), *d);
    tft.setTextSize(2);
    tft.setTextColor(C_RAIN, TFT_BLACK);
    tft.drawString(buf[0] ? buf : "--", 214, 120);
    if (d->hasPct) { snprintf(buf, sizeof(buf), "%d%%", d->rainPct); tft.drawString(buf, 214, 138); }
  } else {
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(4);
    tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
    tft.drawString("--", 80, 108);
  }
  // --- 2 วันถัดไป + ปลายทาง ---
  tft.drawFastHLine(6, 157, SCREEN_W - 12, C_LINE);
  for (int k = 1; k <= 2; k++) {
    char date[11], wd[8];
    dateStr(date, sizeof(date), k);
    time_t e = time(nullptr) + (time_t)k * 86400;
    struct tm t; localtime_r(&e, &t);
    strftime(wd, sizeof(wd), "%a", &t);
    for (char* p = wd; *p; p++) *p = toupper(*p);
    drawWxCell(6 + (k - 1) * 106, wd, TFT_DARKGREY, wxHomeDay(wx, date));
  }
  if (!wx.hasDest) {
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
    tft.drawString("NO FLIGHT", 218, 164);
  } else {                                        // "TPE 27/11" = พยากรณ์ของวันที่บินถึง ไม่ใช่วันนี้
    if (strlen(wx.dest.date) == 10) snprintf(buf, sizeof(buf), "%s %.2s/%.2s", wx.destLabel, wx.dest.date + 8, wx.dest.date + 5);
    else                            snprintf(buf, sizeof(buf), "%s", wx.destLabel);
    drawWxCell(218, buf, C_GOLD, &wx.dest);
  }
}

void drawSaverStatic() {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TR_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
  tft.drawString("tap = lights", 314, 228);
  forceUI = true;
  dClk[0] = dDate[0] = dWxSig[0] = dSaverLine[0] = 0;   // บังคับวาดทุกส่วนใหม่
}

void drawSaverDynamic() {
  time_t e = time(nullptr);
  bool haveClock = clockReady(e);
  struct tm t; localtime_r(&e, &t);

  char clk[8];
  if (haveClock) { strftime(clk, sizeof(clk), "%H:%M", &t); if (t.tm_sec % 2) clk[2] = ' '; }  // ' ' กว้างเท่า ':'
  else           strcpy(clk, "--:--");
  if (forceUI || strcmp(clk, dClk) != 0) {                   // opaque bg → overwrite ไม่ flicker
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(6);
    tft.setTextColor(TFT_CYAN, TFT_BLACK);
    tft.drawString(clk, 160, 30);
    strncpy(dClk, clk, sizeof(dClk) - 1);
  }

  char date[24];
  if (haveClock) strftime(date, sizeof(date), "%a %d %b", &t);
  else           strcpy(date, "waiting NTP...");
  if (forceUI || strcmp(date, dDate) != 0) {
    tft.fillRect(0, 60, SCREEN_W, 20, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(2);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(date, 160, 70);
    strncpy(dDate, date, sizeof(dDate) - 1);
  }

  // พยากรณ์: วาดใหม่เฉพาะเมื่อ ชุดข้อมูลเปลี่ยน / ข้ามวัน / เพิ่งหมดอายุ (ไม่ใช่ทุกวินาที)
  char today[11] = "";
  if (haveClock) strftime(today, sizeof(today), "%Y-%m-%d", &t);
  bool valid = haveClock && wx.ts > 0 && (long)e - wx.ts <= WX_STALE_S;
  char sig[40];
  snprintf(sig, sizeof(sig), "%ld|%s|%d", wx.ts, today, valid ? 1 : 0);
  if (forceUI || strcmp(sig, dWxSig) != 0) {
    drawWxBlock(valid, today);
    strncpy(dWxSig, sig, sizeof(dWxSig) - 1);
  }

  // บรรทัดล่าง: สถานะที่ต้องรู้แม้อยู่หน้าพัก — WiFi หลุด / Pi ดับ (พร้อมเวลาที่เหลือก่อน auto-reset)
  char line[48];
  uint16_t col = TFT_GREEN;
  if (!wifiUp)   { snprintf(line, sizeof(line), "WiFi DOWN"); col = TFT_RED; }
  else if (piUp) { snprintf(line, sizeof(line), "Pi OK"); }
  else {
    unsigned long ago = (millis() - lastPiOk) / 60000;
    snprintf(line, sizeof(line), "Pi DOWN %lum (reset at %lum)", ago, DOWN_MS / 60000);
    col = TFT_RED;
  }
  if (forceUI || strcmp(line, dSaverLine) != 0) {
    tft.fillRect(0, 226, 200, 12, TFT_BLACK);
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(col, TFT_BLACK);
    tft.drawString(line, 6, 228);
    strncpy(dSaverLine, line, sizeof(dSaverLine) - 1);
  }
  forceUI = false;
}

// =====================================================================
// หน้า PI — watchdog (เดิม)
// =====================================================================
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

void drawBackBtn() {
  tft.fillRoundRect(BACK_BTN.x, BACK_BTN.y, BACK_BTN.w, BACK_BTN.h, 6, C_OFF_BG);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, C_OFF_BG);
  tft.setTextSize(1);
  tft.drawString("< LIGHTS", BACK_BTN.x + BACK_BTN.w / 2, BACK_BTN.y + BACK_BTN.h / 2);
}

void drawStatusStatic() {   // header + ปุ่ม (วาดครั้งเดียว/เมื่อเปลี่ยนหน้า)
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.drawString("Pi WATCHDOG", 6, 6);
  drawButton(confirmUntil && millis() < confirmUntil);
  drawCoffeeBtn(coffeeOn);
  drawBackBtn();
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
    tft.drawString(piUp ? "Pi OK" : "Pi DOWN", 160, 76);
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
  bool haveClock = clockReady(lastCheckEpoch);
  if (haveClock) { struct tm t; localtime_r(&lastCheckEpoch, &t);
                   char hms[16]; strftime(hms, sizeof(hms), "%H:%M:%S", &t);
                   snprintf(line2, sizeof(line2), "last check %s", hms); }
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

// ---------- router: เลือกวาดตามหน้า ----------
void drawStatic() {
  if (page == PAGE_SAVER)   drawSaverStatic();
  else if (page == PAGE_PI) drawStatusStatic();
  else                      drawPanelStatic();
}
void drawDynamic() {
  if (page == PAGE_SAVER)   drawSaverDynamic();
  else if (page == PAGE_PI) drawStatusDynamic();
  else                      drawPanelDynamic();
}

void gotoPage(int p) {
  page = p;
  if (p == PAGE_PANEL) {                 // เข้าหน้าไฟ → อ่านสถานะจริงใหม่ทุกดวงทันที (ระหว่างพักจอไม่ได้ถาม HA เลย)
    pollIdx = 0;
    pollBurst = N_LIGHTS;
    if (haBackoff && haFault == 1) haBackoff = false;   // คนเพิ่งแตะจอ → ลอง HA ใหม่เลย ไม่รอครบ backoff
  }
  drawStatic();
  drawDynamic();
  lastDraw = millis();
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
  lastActivity = now;                                // หลัง reset → โชว์หน้า PI ไว้ IDLE_MS (คนดูจะได้เห็นสถานะ)
  gotoPage(PAGE_PI);
}

// ---------- แตะ 1 ครั้ง ----------
void handleTap(int16_t tx, int16_t ty) {
  if (page == PAGE_SAVER) {                          // แตะปลุก: กลับหน้าไฟอย่างเดียว ไม่ส่งต่อเป็นการกดปุ่ม
    gotoPage(PAGE_PANEL);
    return;
  }
  if (page == PAGE_PANEL) {
    if (inRect(tx, ty, PI_CHIP_HIT)) { gotoPage(PAGE_PI); return; }
    for (int i = 0; i < N_LIGHTS; i++)
      if (inRect(tx, ty, gridCell(N_LIGHTS, i))) { tapLight(i); return; }
    return;
  }
  // --- หน้า PI ---
  if (inRect(tx, ty, BACK_BTN)) {
    confirmUntil = 0;                                // ออกจากหน้านี้ = ยกเลิกการยืนยัน RESET ที่ค้างอยู่
    gotoPage(PAGE_PANEL);
  } else if (tx >= CBTN_X && tx <= CBTN_X + CBTN_W && ty >= CBTN_Y && ty <= CBTN_Y + CBTN_H) {
    int r = coffeeGet("/coffee/toggle");             // แตะปุ่ม COFFEE → สลับ coffee break ของ Pixoo
    if (r >= 0) { coffeeOn = r; drawCoffeeBtn(coffeeOn); }
  } else if (tx >= BTN_X && tx <= BTN_X + BTN_W && ty >= BTN_Y && ty <= BTN_Y + BTN_H) {
    if (confirmUntil && millis() < confirmUntil) {   // แตะครั้งที่ 2 = ยืนยัน
      confirmUntil = 0;
      powerCycle("manual reset");
    } else {                                         // แตะครั้งแรก = ขอยืนยัน 3 วิ
      confirmUntil = millis() + 3000;
      drawButton(true);
    }
  }
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
  configTime(7 * 3600, 0, "pool.ntp.org");           // BKK UTC+7 (นาฬิกา + เลือก "วันนี้" ของพยากรณ์)

  for (int i = 0; i < 6; i++) { lightState[i] = -1; lightDrawn[i] = VIS_NONE; lightCode[i] = 0; lightCmdPending[i] = false; lightCmdAt[i] = 0; }
  memset(&wx, 0, sizeof(wx));

  unsigned long now = millis();
  lastPiOk = now; lastCheck = 0; lastDraw = 0;
  lastCycle = now - COOLDOWN_MS;                      // ยอม auto-cycle ครั้งแรกได้เมื่อครบ DOWN_MS
  lastActivity = now;
  piUp = true;
  gotoPage(PAGE_PANEL);
}

void loop() {
  // --- touch: นับ "จังหวะแตะลง" เท่านั้น — กดค้างไม่ยิงซ้ำ (ไฟจะกระพริบติด-ดับรัว ๆ ตามนิ้วที่ค้าง) ---
  int16_t tx, ty;
  if (readTouch(tx, ty)) {
    if (!touchHeld && millis() - lastTapMs >= TAP_GAP_MS) {
      touchHeld = true;
      lastTapMs = lastActivity = millis();
      Serial.printf("touch raw=(%d,%d) -> screen=(%d,%d) page=%d\n", rawX, rawY, tx, ty, page);
      handleTap(tx, ty);
    }
    lastTouchSeen = millis();                        // (หลัง handleTap: คำสั่งที่ block นานไม่นับเป็น "ยกนิ้วแล้ว")
  } else if (touchHeld && millis() - lastTouchSeen >= RELEASE_MS) {
    touchHeld = false;
  }
  if (confirmUntil && millis() > confirmUntil) {     // หมดเวลายืนยัน → กลับปุ่มแดง
    confirmUntil = 0;
    if (page == PAGE_PI) drawButton(false);
  }

  // --- ไม่แตะจอครบ IDLE_MS → screensaver ---
  if (page != PAGE_SAVER && !confirmUntil && millis() - lastActivity >= IDLE_MS) gotoPage(PAGE_SAVER);

  // --- WiFi ของตัวเอง (reconnect + อย่าโทษ Pi ตอนที่เราหลุด) ---
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiUp)                                      // เพิ่งหลุด → สถานะไฟที่ถืออยู่ยืนยันไม่ได้แล้ว อย่าโชว์เป็นค่าจริง
      for (int i = 0; i < N_LIGHTS; i++) { lightState[i] = -1; lightCmdPending[i] = false; }
    wifiUp = false;
    connectWiFi();
    lastPiOk = millis();
    if (wifiUp) pollBurst = N_LIGHTS;                // กลับมาแล้ว → อ่านใหม่ทุกดวง
  } else {
    wifiUp = true;
  }

  // --- เช็ค Pi ตามรอบ (ทำงานทุกหน้า — นี่คือ watchdog) ---
  if (millis() - lastCheck >= CHECK_MS) {
    lastCheck = millis();
    if (wifiUp) {
      lastCheckEpoch = time(nullptr);                // เวลาจริงของรอบเช็คนี้ → บรรทัด "last check"
      piUp = piAlive();
      if (piUp) {
        lastPiOk = millis();
        piUpSecs = piUptimeS(); piUpAtMillis = millis();   // ดึง uptime จริงของ Pi มาโชว์
        if (!wxFetched || millis() - lastWxFetch >= WX_FETCH_MS) {   // พยากรณ์ของ screensaver (ถ้าพลาด ลองใหม่รอบเช็คหน้า)
          if (fetchWeather()) { wxFetched = true; lastWxFetch = millis(); }
        }
      } else {
        piUpSecs = -1;                               // Pi ดับ → uptime ไม่รู้
        if (millis() - lastPiOk >= DOWN_MS && millis() - lastCycle >= COOLDOWN_MS) {
          powerCycle("no signal (backstop)");
        }
      }
      // sync สถานะ coffee break (เผื่อสลับจากที่อื่น) + redraw ถ้าเปลี่ยน. Pi ดับ = ไม่รู้ (-1) โดยไม่ต้องถาม:
      // ผลเท่ากับถามแล้วไม่ตอบ แต่ไม่เสียเวลารอ — ระหว่าง Pi ดับ ปุ่มไฟต้องยังกดติด
      int c = piUp ? coffeeGet("/coffee") : -1;
      if (c != coffeeOn) { coffeeOn = c; if (page == PAGE_PI) drawCoffeeBtn(coffeeOn); }
    }
  }

  // --- อ่านสถานะไฟ (เฉพาะตอนมีคนดูหน้าไฟอยู่ — หน้าพักไม่ถาม HA ให้เปลือง) ---
  if (page == PAGE_PANEL && wifiUp) pollLights();

  // --- รีเฟรช UI ~1 วิ ---
  if (millis() - lastDraw >= 1000) {
    lastDraw = millis();
    drawDynamic();
  }
  delay(20);
}
