// sim_main.cpp — ตัวจำลองบน PC: รัน firmware "ตัวจริง" (src/main.cpp) กับฮาร์ดแวร์ปลอม (stubs/) ด้วยนาฬิกาจำลอง,
// นิ้วแตะตามสคริปต์ และ LAN ปลอม (Home Assistant + endpoint :8099 ของ Pi + webhook ตัดไฟ).
//
// ทำไมต้องมี: firmware นี้ "ตัดไฟ Pi ได้" และ "สั่งไฟในห้อง" — พฤติกรรมอย่าง "Pi เงียบครบ 20 นาทีต้องยิง webhook
// 1 ครั้งพอดี แม้จอกำลังเป็น screensaver" หรือ "แตะปลุกจอต้องไม่ไปสั่งไฟ" ทดสอบบนบอร์ดจริงต้องนั่งรอเป็นชั่วโมง.
// ที่นี่ 8 ชั่วโมงจำลองจบในไม่ถึงวินาที และไม่ต้องมีบอร์ด.
//
//   make test          → unit test + ทุก scenario (exit != 0 ถ้ามีข้อไหนไม่ผ่าน)
//   make shots         → ภาพหน้าจอ PNG ทุกหน้า ลง build/shots/ (ต้องมี python3 + Pillow)
#include <map>
#include <string>
#include <vector>
#include "Arduino.h"
#include "WiFi.h"
#include "HTTPClient.h"

namespace sim {
unsigned long nowMs = 0;
long long epochBase = 0;
bool ntpOk = true;
bool wifiOk = true, piAlive = true, piInfoUp = true, haUp = true, haTokenOk = true;
bool touchDown = false; int touchRawX = 0, touchRawY = 0;
FILE* dl = nullptr;
int coffee = 0;
std::string weather = "wx1 0";
long lagMs = 1200;                     // อุปกรณ์ cloud: สถานะที่ HA รายงาน ตามหลังคำสั่งเท่านี้
struct Ent { std::string state, pending; unsigned long pendingAt = 0; };
std::map<std::string, Ent> ents;
struct Req { unsigned long ms; std::string method, url, body; int code; };
std::vector<Req> reqs;
int webhooks = 0;

static int handle(const std::string& method, const std::string& url, const std::map<std::string, std::string>& hdr,
                  const std::string& body, std::string* resp, int ct) {
  // ปลายทางไม่ตอบ: รอจนครบ connect timeout ที่ firmware ตั้ง. ไม่ได้ตั้ง (-1) = ค้างตาม TCP stack — ในนี้สมมติ 18 วิ
  // (ตัวเลขจริงขึ้นกับ lwIP; ประเด็นคือ "นานจนจอทัชนิ่ง") เพื่อให้ request ที่ลืมตั้ง timeout โผล่ในผลทดสอบ
  auto dead = [&]() { nowMs += (ct > 0 ? ct : 18000); return -1; };
  resp->clear();
  const std::string HA = "http://192.168.41.207:8123", PI = "http://192.168.41.241:8099";
  if (!wifiOk) return dead();
  if (url.compare(0, PI.size(), PI) == 0) {
    if (!piAlive || !piInfoUp) return dead();
    nowMs += 30;
    std::string p = url.substr(PI.size());
    if (p == "/") { *resp = "123456\n"; return 200; }
    if (p == "/coffee") { *resp = coffee ? "1\n" : "0\n"; return 200; }
    if (p == "/coffee/toggle") { coffee = !coffee; *resp = coffee ? "1\n" : "0\n"; return 200; }
    if (p == "/weather") { *resp = weather + "\n"; return 200; }
    return 404;
  }
  if (url.compare(0, HA.size(), HA) == 0) {
    if (!haUp) return dead();
    nowMs += 40;
    std::string p = url.substr(HA.size());
    if (p.compare(0, 13, "/api/webhook/") == 0) { if (method == "POST") webhooks++; return 200; }   // webhook ไม่ใช้ token
    auto a = hdr.find("Authorization");
    if (!haTokenOk || a == hdr.end() || a->second != "Bearer SIMTOKEN") { *resp = "401: Unauthorized"; return 401; }
    if (method == "GET" && p.compare(0, 12, "/api/states/") == 0) {
      auto e = ents.find(p.substr(12));
      if (e == ents.end()) { *resp = "{\"message\":\"Entity not found.\"}"; return 404; }
      if (e->second.pendingAt && nowMs >= e->second.pendingAt) { e->second.state = e->second.pending; e->second.pendingAt = 0; }
      *resp = "{\"entity_id\":\"" + e->first + "\",\"state\":\"" + e->second.state +
              "\",\"attributes\":{\"friendly_name\":\"x\",\"state\":\"zzz\"},\"last_changed\":\"2026-11-24T12:00:00+00:00\"}";
      return 200;
    }
    if (method == "POST" && p.compare(0, 28, "/api/services/homeassistant/") == 0) {
      std::string svc = p.substr(28);
      size_t q = body.find("\"entity_id\":\"");
      if (q == std::string::npos) return 400;
      std::string id = body.substr(q + 13, body.find('"', q + 13) - q - 13);
      auto e = ents.find(id);
      nowMs += 150;
      if (e != ents.end() && e->second.state != "unavailable") {
        std::string cur = e->second.pendingAt ? e->second.pending : e->second.state;
        std::string want = svc == "turn_on" ? "on" : svc == "turn_off" ? "off" : (cur == "on" ? "off" : "on");
        e->second.pending = want; e->second.pendingAt = nowMs + lagMs;
      }
      *resp = "[]";
      return 200;
    }
    return 404;
  }
  return dead();
}
int http(const std::string& method, const std::string& url, const std::map<std::string, std::string>& hdr,
         const std::string& body, std::string* resp, int ct, int) {
  unsigned long t0 = nowMs;
  int code = handle(method, url, hdr, body, resp, ct);
  reqs.push_back({t0, method, url, body, code});
  return code;
}
}  // namespace sim

SerialT Serial;
WiFiT WiFi;
static bool verbose = false;
void simLog(const char* s) { if (verbose) printf("  [serial %lu] %s", sim::nowMs, s); }
bool WiFiClient::connect(const char*, int, int timeoutMs) {
  if (sim::wifiOk && sim::piAlive) { sim::nowMs += 5; return true; }
  sim::nowMs += timeoutMs; return false;
}

#include "main.cpp"          // firmware ตัวจริง: setup(), loop() และ global ทั้งหมดของมัน

// ---------- ตัวช่วยเขียน scenario ----------
static int fails = 0;
#define EXPECT(c) do { if (!(c)) { printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)
static void runFor(unsigned long ms) { unsigned long end = sim::nowMs + ms; while (sim::nowMs < end) loop(); }
static void press(int x, int y) {
  sim::touchRawX = TOUCH_X_MIN + (long)x * (TOUCH_X_MAX - TOUCH_X_MIN) / (SCREEN_W - 1);
  sim::touchRawY = TOUCH_Y_MIN + (long)y * (TOUCH_Y_MAX - TOUCH_Y_MIN) / (SCREEN_H - 1);
  sim::touchDown = true;
}
static void tap(int x, int y, unsigned long hold = 120) { press(x, y); runFor(hold); sim::touchDown = false; runFor(300); }
static void snap(const char* name) { fprintf(sim::dl, "S %s\n", name); }
static int count(const char* method, const char* urlPart, unsigned long since = 0) {
  int n = 0;
  for (auto& r : sim::reqs) if (r.ms >= since && r.method == method && r.url.find(urlPart) != std::string::npos) n++;
  return n;
}
static std::string clockStr() { time_t e = simTime(nullptr); struct tm t; localtime_r(&e, &t); char b[32]; strftime(b, sizeof b, "%a %H:%M:%S", &t); return b; }
static void say(const char* what) { printf("[%8.1fs %s] %s\n", sim::nowMs / 1000.0, clockStr().c_str(), what); }
static void setWx(const char* fmt, long long ts) { char b[512]; snprintf(b, sizeof b, fmt, ts); sim::weather = b; }
static long long nowEpoch() { return sim::epochBase + sim::nowMs / 1000; }
static PRect cell(int i) { return gridCell(N_LIGHTS, i); }
static void tapLightBtn(int i, unsigned long hold = 120) { PRect r = cell(i); tap(r.x + r.w / 2, r.y + r.h / 2, hold); }
static void tapHeader() { tap(100, 16); }          // แถบหัวหน้า PANEL: ไม่ใช่ปุ่ม — ใช้กันจอเข้า screensaver ระหว่างทดสอบ

static const char* WX_FULL = "wx1 %lld\nH TMD BKK 2026-11-24 partly 33 24 0.0 -\nH TMD BKK 2026-11-25 rain 31 24 5.2 -\n"
                             "H TMD BKK 2026-11-26 storm 30 25 22.4 -\nD OM TPE 2026-11-27 heavy 22 18 30.2 80";

// ===== watchdog: Pi เงียบ 75 นาที → ตัดไฟที่ DOWN_MS แล้วเว้น COOLDOWN_MS · Pi กลับมา/WiFi ตัวเองหลุด = ไม่ตัด =====
// เลขที่คาด (20.06 / 46.16 / 72.26 นาที) วัดจาก firmware รุ่น watchdog ล้วนก่อนเพิ่ม panel ด้วยสคริปต์เดียวกันนี้
// — รุ่นนี้ต้องได้เท่าเดิม: การเพิ่มหน้าจอต้องไม่ขยับเวลาตัดไฟแม้แต่รอบเดียว.
static int scenarioWatchdog() {
  setup();
  runFor(10 * 60000UL);
  unsigned long t0 = sim::nowMs; sim::piAlive = false;
  runFor(75 * 60000UL);
  std::vector<double> at;
  for (auto& r : sim::reqs) if (r.url.find("/api/webhook/") != std::string::npos) at.push_back((r.ms - t0) / 60000.0);
  for (double m : at) printf("  webhook sent %.2f min after Pi went silent\n", m);
  const double want[3] = {20.06, 46.16, 72.26};
  EXPECT(at.size() == 3);
  for (size_t i = 0; i < at.size() && i < 3; i++) EXPECT(at[i] > want[i] - 0.05 && at[i] < want[i] + 0.05);
  sim::piAlive = true; int before = sim::webhooks; runFor(60 * 60000UL);
  printf("  Pi back for 60 min: %d extra webhooks\n", sim::webhooks - before);
  EXPECT(sim::webhooks == before && piUp);
  sim::wifiOk = false; sim::piAlive = false; before = sim::webhooks; runFor(45 * 60000UL);   // WiFi ของ ESP32 เองหลุด → ห้ามโทษ Pi
  printf("  ESP32's own WiFi down 45 min (Pi silent too): %d webhooks\n", sim::webhooks - before);
  EXPECT(sim::webhooks == before);
  return fails;
}

// ===== layout: ภาพหน้า PANEL ของจำนวนปุ่มนี้ (-DSIM_NLIGHTS) =====
static int scenarioLayout() {
  setup(); runFor(1500);
  char n[32]; snprintf(n, sizeof n, "panel_%d_lights", N_LIGHTS); snap(n);
  for (int i = 0; i < N_LIGHTS; i++) EXPECT(lightDrawn[i] == lightState[i]);
  return fails;
}

// ===== ui: ใช้งานปกติ → ความผิดพลาดแต่ละแบบ → หน้า Pi → screensaver → reset มือ → Pi ดับใต้ screensaver =====
static int scenarioUi() {
  setWx(WX_FULL, sim::epochBase - 600);
  setup();
  EXPECT(page == PAGE_PANEL);
  runFor(1500);
  EXPECT(lightState[0] == 1 && lightState[1] == 0 && lightState[2] == 0 && lightState[3] == -1);
  snap("01_panel_boot"); say("boot: panel shows real states (BALCONY is 'unavailable' in HA)");

  // แตะ LAMP (ปิด → เปิด): สั่ง turn_on ครั้งเดียว, โชว์ทันที, ไม่กระพริบกลับระหว่างที่อุปกรณ์ยังรายงานค่าเก่า
  unsigned long t = sim::nowMs;
  press(cell(1).x + 20, cell(1).y + 20); loop();
  EXPECT(lightState[1] == 1 && lightCmdPending[1]);
  sim::touchDown = false; runFor(300);
  snap("02_after_tap_lamp");
  EXPECT(count("POST", "/homeassistant/turn_on", t) == 1 && count("POST", "/homeassistant/", t) == 1);
  runFor(900);  EXPECT(lightState[1] == 1 && sim::ents["switch.l2"].state == "off");   // HA ยังตอบ "off" อยู่ — ยังไม่เชื่อ
  runFor(6000); EXPECT(lightState[1] == 1 && sim::ents["switch.l2"].state == "on" && !lightCmdPending[1]);
  say("tap LAMP: 1 turn_on, shown at once, held through the device's slow report, then confirmed");

  // มีคนกดสวิตช์ผนัง: CEILING เปิด → ปิด — จอตามเองโดยไม่มีใครแตะ
  sim::ents["light.l1"].state = "off"; runFor(4500);
  EXPECT(lightState[0] == 0 && lightDrawn[0] == VIS_OFF);
  say("wall switch turned CEILING off -> panel followed");

  // กดค้าง 3 วิ = 1 คำสั่ง · แตะ 2 ครั้ง = 2 คำสั่ง
  t = sim::nowMs; tapLightBtn(2, 3000);
  EXPECT(count("POST", "/homeassistant/", t) == 1 && lightState[2] == 1);
  runFor(3000); t = sim::nowMs; tapLightBtn(2); tapLightBtn(2);
  EXPECT(count("POST", "/homeassistant/turn_off", t) == 1 && count("POST", "/homeassistant/turn_on", t) == 1);
  runFor(4000); EXPECT(lightState[2] == 1);
  say("finger held 3 s = 1 command; two taps = 2 commands");

  // จอยังโชว์ CEILING "ปิด" แต่เพิ่งถูกเปิดจากที่อื่น → แตะแล้วต้องไม่ไปปิดมัน (สั่งแบบระบุปลายทาง ไม่ใช่ toggle)
  runFor(4000 - (sim::nowMs % 1000));
  EXPECT(lightState[0] == 0);
  sim::ents["light.l1"].state = "on"; t = sim::nowMs;
  press(cell(0).x + 20, cell(0).y + 20); loop(); sim::touchDown = false;
  EXPECT(count("POST", "/homeassistant/turn_on", t) == 1 && count("POST", "/homeassistant/toggle", t) == 0);
  runFor(4000); EXPECT(lightState[0] == 1 && sim::ents["light.l1"].state == "on");
  say("stale display + tap = explicit turn_on (light stays on), not a blind toggle");

  // ไม่รู้สถานะ → toggle เป็นทางเดียว
  t = sim::nowMs; tapLightBtn(3);
  EXPECT(count("POST", "/homeassistant/toggle", t) == 1);
  runFor(3000);

  // entity_id ผิด → ปุ่มบอกเอง
  sim::ents.erase("light.l3"); runFor(5000);
  EXPECT(lightState[2] == -1 && lightCode[2] == 404 && haFault == 0);
  snap("03_panel_wrong_entity"); say("entity missing -> that button says NO ENTITY, the others are unaffected");
  sim::ents["light.l3"].state = "off"; runFor(5000); EXPECT(lightState[2] == 0);

  // HA ดับ: บอกสาเหตุ, ทุกดวงเป็น "?", ถามซ้ำห่าง ๆ, คำสั่งที่ไม่สำเร็จไม่ถูกโชว์ว่าสำเร็จ, กลับมาแล้วฟื้นเอง
  sim::haUp = false; runFor(8000); tapHeader();
  EXPECT(page == PAGE_PANEL && haFault == 1);
  for (int i = 0; i < N_LIGHTS; i++) EXPECT(lightState[i] == -1);
  snap("04_panel_ha_offline");
  { unsigned long a = sim::nowMs; for (int k = 0; k < 4; k++) { runFor(10000); tapHeader(); }
    int n = count("GET", "/api/states/", a); printf("  HA down: %d state requests in 40 s\n", n); EXPECT(n >= 2 && n <= 4); }
  t = sim::nowMs; tapLightBtn(0);
  EXPECT(count("POST", "/homeassistant/", t) == 1 && lightState[0] == -1 && lightDrawn[0] != VIS_ON);
  sim::haUp = true; runFor(20000); tapHeader(); runFor(3000);
  EXPECT(haFault == 0 && lightState[0] == 1);
  say("HA down -> 'HA OFFLINE', all '?', polite retry, failed command shown as failed; HA back -> recovers");

  // token ผิด/ถูกเพิกถอน: ถามซ้ำช้า ๆ (ทุกครั้งที่ผิด HA จด login failed)
  sim::haTokenOk = false; runFor(8000); tapHeader();
  EXPECT(haFault == 2);
  snap("05_panel_bad_token");
  { unsigned long a = sim::nowMs; for (int k = 0; k < 12; k++) { runFor(10000); tapHeader(); }
    int n = count("GET", "/api/states/", a); printf("  bad token: %d state requests in 2 min\n", n); EXPECT(n <= 3); }
  sim::haTokenOk = true; runFor(61000); tapHeader(); runFor(3000);
  EXPECT(haFault == 0 && lightState[1] == 1);
  say("401 -> 'HA TOKEN?', slow retry");

  // หน้า Pi: coffee, RESET ต้องแตะ 2 ครั้ง, แตะครั้งเดียวหมดเวลาเอง, ออกจากหน้า = ยกเลิก
  tap(250, 12); EXPECT(page == PAGE_PI);
  runFor(1200); snap("06_pi_page");
  int c0 = sim::coffee; tap(CBTN_X + 60, CBTN_Y + 14); EXPECT(sim::coffee != c0 && coffeeOn == sim::coffee);
  tap(160, 195); EXPECT(confirmUntil != 0 && sim::webhooks == 0);
  snap("07_pi_confirm");
  runFor(4000); EXPECT(confirmUntil == 0 && sim::webhooks == 0);
  tap(160, 195); tap(BACK_BTN.x + 20, BACK_BTN.y + 10); EXPECT(page == PAGE_PANEL && confirmUntil == 0);
  runFor(1000);
  say("Pi page: coffee toggled, single RESET tap expires, leaving the page cancels it");

  // ไม่แตะ 45 วิ → screensaver; อยู่ตรงนั้น 10 นาที = ไม่ถาม HA เลย
  runFor(46000); EXPECT(page == PAGE_SAVER);
  runFor(1500); snap("08_saver");
  t = sim::nowMs; runFor(10 * 60000UL);
  EXPECT(count("GET", "/api/states/", t) == 0 && count("POST", "/homeassistant/", t) == 0);
  EXPECT(count("GET", "/weather", t) == 1);
  say("idle 45 s -> screensaver; 10 min there = 0 requests to HA, weather refetched once");

  // แตะปลุกตรงตำแหน่งปุ่มไฟพอดี → ปลุกอย่างเดียว ไม่สั่งไฟ
  t = sim::nowMs; tapLightBtn(0);
  EXPECT(page == PAGE_PANEL && count("POST", "/homeassistant/", t) == 0);
  runFor(1500); EXPECT(lightState[0] == 1); snap("09_panel_after_wake");
  say("wake tap landing on a button -> panel only, no command sent");

  // screensaver แบบต่าง ๆ
  runFor(46000);
  setWx("wx1 %lld\nH OM BKK 2026-11-24 heavy 29 23 120.4 95\nH OM BKK 2026-11-25 fog 30 24 0.0 10\nD OM HEL 2026-12-02 snow -3 -12 4.0 70", nowEpoch());
  wxFetched = false; runFor(31000); snap("10_saver_cold_dest");
  setWx("wx1 %lld\nH TMD BKK 2026-11-24 hot 39 29 0.0 -\nH TMD BKK 2026-11-25 cool 22 15 0.0 -\nH TMD BKK 2026-11-26 clear 33 24 - -\nD OM LHR 2026-12-20 - - - - -", nowEpoch());
  wxFetched = false; runFor(31000); snap("11_saver_dest_beyond");
  setWx("wx1 %lld\nH TMD BKK 2026-11-24 cloudy 32 25 1.5 -\nH TMD BKK 2026-11-25 clear 33 24 0.0 -\nH TMD BKK 2026-11-26 partly 33 24 0.0 -", nowEpoch());
  wxFetched = false; runFor(31000); snap("12_saver_no_flight");
  sim::piInfoUp = false; runFor(7 * 3600000UL);     // endpoint หาย 7 ชม. → ข้อมูลเก่าเกิน 6 ชม. = ไม่โชว์ (และข้ามเที่ยงคืนไปด้วย)
  snap("13_saver_stale"); EXPECT(page == PAGE_SAVER);
  sim::piInfoUp = true; setWx(WX_FULL, nowEpoch()); runFor(31000);
  snap("14_saver_next_day");                         // ตอนนี้พุธ 25: "วันนี้" ต้องเป็นแถวของวันที่ 25 (เลือกด้วยนาฬิกาของ ESP32 เอง)
  { char today[11]; dateStr(today, sizeof today, 0); EXPECT(!strcmp(today, "2026-11-25"));
    const WxDay* d = wxHomeDay(wx, today); EXPECT(d && d->tmax == 31); }
  say("screensaver variants rendered; past midnight it picks the next day's row");

  // reset มือจากหน้า Pi
  tap(10, 10); tap(250, 12); EXPECT(page == PAGE_PI);
  tap(160, 195); press(160, 195); loop();           // แตะครั้งที่ 2 ภายใน 3 วิ → เริ่มตัดไฟ (block ~62 วิ)
  sim::touchDown = false;
  EXPECT(sim::webhooks == 1 && page == PAGE_PI);
  runFor(1500); snap("15_pi_after_manual_reset");
  say("manual RESET: two taps -> exactly 1 webhook, back on the Pi page");

  // Pi ดับขณะจอเป็น screensaver → บรรทัดล่างบอก และยังตัดไฟที่ 20 นาทีเหมือนเดิม
  runFor(46000); EXPECT(page == PAGE_SAVER);
  runFor(26 * 60000UL);                              // ให้ cooldown 25 นาทีจาก reset มือผ่านไปก่อน
  sim::piAlive = false; unsigned long down = sim::nowMs; int wh = sim::webhooks;
  runFor(12 * 60000UL + 31000); snap("16_saver_pi_down"); EXPECT(!piUp && sim::webhooks == wh);
  // ระหว่าง Pi ดับ ปุ่มไฟต้องยังใช้ได้ และ 1 loop ต้องไม่ค้างเกิน timeout ที่ตั้งไว้ (เช็ค Pi 4 วิ)
  tap(10, 10); t = sim::nowMs; tapLightBtn(1); EXPECT(count("POST", "/homeassistant/turn_off", t) == 1);
  { unsigned long worst = 0; for (int k = 0; k < 4000; k++) { unsigned long a = sim::nowMs; loop(); if (sim::nowMs - a > worst) worst = sim::nowMs - a; }
    printf("  Pi down: longest single loop() = %lu ms\n", worst); EXPECT(worst <= TCP_TIMEOUT_MS + 500); }
  while (sim::webhooks == wh && sim::nowMs - down < 25 * 60000UL) runFor(100);
  unsigned long fired = 0; for (auto& r : sim::reqs) if (r.url.find("/api/webhook/") != std::string::npos) fired = r.ms;
  printf("  auto power-cycle webhook sent %.2f min after Pi went silent\n", (fired - down) / 60000.0);
  EXPECT(sim::webhooks == wh + 1 && fired - down >= 19.5 * 60000 && fired - down <= 20.6 * 60000);
  EXPECT(page == PAGE_PI);
  sim::piAlive = true; runFor(32000); EXPECT(piUp && page == PAGE_PI);
  say("Pi silent under the screensaver -> 'Pi DOWN' line, lights still work, power-cycle at 20 min");
  return fails;
}

int main(int argc, char** argv) {
  setenv("TZ", "<+07>-7", 1); tzset();
  const char* scenario = argc > 1 ? argv[1] : "ui";
  sim::dl = fopen(argc > 2 ? argv[2] : "/dev/null", "w");
  if (!sim::dl) { perror("display list"); return 2; }
  verbose = argc > 3;
  sim::epochBase = 1795523400;                      // อังคาร 2026-11-24 19:30:00 +07
  sim::ents["light.l1"].state = "on";
  sim::ents["switch.l2"].state = "off";
  sim::ents["light.l3"].state = "off";
  sim::ents["switch.l4"].state = "unavailable";
  sim::ents["light.l5"].state = "on";
  sim::ents["light.l6"].state = "off";

  int rc = !strcmp(scenario, "watchdog") ? scenarioWatchdog() : !strcmp(scenario, "layout") ? scenarioLayout() : scenarioUi();
  fclose(sim::dl);
  printf("%s (%d lights): %s\n", scenario, N_LIGHTS, rc ? "FAILED" : "ok");
  return rc ? 1 : 0;
}
