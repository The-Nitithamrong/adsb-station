// test_logic.cpp — unit test ของ src/panel_logic.h (parser สถานะ HA, parser พยากรณ์, ตารางปุ่ม) บน PC.
// ตัวอย่างข้อความพยากรณ์ก๊อปมาจากผลจริงของ weather_text() ใน deploy/uptime_server.py — สองฝั่งต้องตรงกัน.
#include "panel_logic.h"
#include <assert.h>
#include <math.h>
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int fails = 0;
int main() {
  // --- parseHaState: รูปจริงที่ HA ตอบ (orjson, ไม่มีช่องว่าง) ---
  CHECK(parseHaState("{\"entity_id\":\"light.bedroom\",\"state\":\"on\",\"attributes\":{\"brightness\":255,\"friendly_name\":\"Bed\"},\"last_changed\":\"x\"}") == 1);
  CHECK(parseHaState("{\"entity_id\":\"switch.lamp\",\"state\":\"off\",\"attributes\":{}}") == 0);
  CHECK(parseHaState("{\"entity_id\":\"light.x\",\"state\":\"unavailable\",\"attributes\":{}}") == -1);
  CHECK(parseHaState("{\"entity_id\":\"light.x\",\"state\":\"unknown\"}") == -1);
  CHECK(parseHaState("{\"entity_id\": \"light.x\", \"state\" : \"on\"}") == 1);          // pretty-printed
  CHECK(parseHaState("{\"entity_id\":\"light.x\",\"state\":\"off\",\"attributes\":{\"state\":\"on\"}}") == 0);  // ตัวบนสุดชนะ
  CHECK(parseHaState("{\"entity_id\":\"light.x\",\"state\":\"online\"}") == -1);           // ไม่ใช่ prefix match
  CHECK(parseHaState("{\"entity_id\":\"light.x\",\"state\":\"offline\"}") == -1);
  CHECK(parseHaState("{\"message\":\"Entity not found.\"}") == -1);                         // 404 body
  CHECK(parseHaState("401: Unauthorized") == -1);
  CHECK(parseHaState("") == -1);
  CHECK(parseHaState(nullptr) == -1);
  CHECK(parseHaState("{\"state\"") == -1);
  CHECK(parseHaState("{\"state\":") == -1);
  CHECK(parseHaState("{\"state\":\"o") == -1);

  // --- wxParse: ข้อความที่ uptime_server.weather_text() ผลิตจริง (ก๊อปจากผล test ฝั่ง Python) ---
  WxData w;
  const char* full = "wx1 1795500000\nH TMD BKK 2026-11-24 clear 33 24 0.0 -\nH TMD BKK 2026-11-25 rain 32 24 5.2 -\n"
                     "H TMD BKK 2026-11-26 partly 32 25 - -\nD OM TPE 2026-11-27 heavy 22 18 30.2 80\n";
  CHECK(wxParse(full, &w));
  CHECK(w.ts == 1795500000L && w.nHome == 3 && w.hasDest);
  CHECK(!strcmp(w.homeSrc, "TMD") && !strcmp(w.homeLabel, "BKK"));
  CHECK(!strcmp(w.home[0].date, "2026-11-24") && !strcmp(w.home[0].kind, "clear") && w.home[0].hasT && w.home[0].tmax == 33 && w.home[0].tmin == 24);
  CHECK(w.home[0].hasMm && fabs(w.home[0].rainMm) < 1e-6 && !w.home[0].hasPct);
  CHECK(w.home[1].hasMm && fabs(w.home[1].rainMm - 5.2f) < 1e-4);
  CHECK(!w.home[2].hasMm && w.home[2].hasT);
  CHECK(!strcmp(w.destSrc, "OM") && !strcmp(w.destLabel, "TPE") && w.dest.hasT && w.dest.tmax == 22 && w.dest.tmin == 18 && w.dest.hasPct && w.dest.rainPct == 80);
  CHECK(wxHomeDay(w, "2026-11-25") == &w.home[1]);
  CHECK(wxHomeDay(w, "2026-11-23") == nullptr && wxHomeDay(w, "") == nullptr);
  // ปลายทางไกลเกินพยากรณ์: มีชื่อ+วันที่ ไม่มีค่า
  CHECK(wxParse("wx1 5\nD OM LHR 2026-12-20 - - - - -", &w) && w.hasDest && !w.dest.hasT && !w.dest.kind[0] && !strcmp(w.dest.date, "2026-12-20") && w.nHome == 0);
  // อุณหภูมิติดลบ (ปลายทางเมืองหนาว)
  CHECK(wxParse("wx1 5\nD OM HEL 2026-12-20 snow -3 -12 0.4 60", &w) && w.dest.hasT && w.dest.tmax == -3 && w.dest.tmin == -12);
  // Pi บอกว่าไม่มีข้อมูล
  CHECK(wxParse("wx1 0", &w) && w.ts == 0 && w.nHome == 0 && !w.hasDest);
  CHECK(wxParse("wx1 0\n", &w) && w.ts == 0);
  // CRLF
  CHECK(wxParse("wx1 9\r\nH TMD BKK 2026-11-24 clear 33 24 0.0 -\r\n", &w) && w.nHome == 1 && w.home[0].tmin == 24 && !w.home[0].hasPct);
  // ไม่ใช่ wx1 → false และห้ามแตะ out
  w.ts = 777;
  CHECK(!wxParse("<html>404</html>", &w) && w.ts == 777);
  CHECK(!wxParse("", &w) && !wxParse(nullptr, &w) && !wxParse("wx2 5", &w) && !wxParse("wx1", &w) && !wxParse("wx1 -5", &w) && !wxParse("wx1 abc", &w) && !wxParse("12345", &w));
  CHECK(w.ts == 777);
  // บรรทัดเสียถูกข้ามทีละบรรทัด: field ขาด / เกิน / token ยาวเกินช่อง (ต้องไม่ล้นไปเลื่อน field)
  const char* bad = "wx1 9\nH TMD BKK 2026-11-24 clear 33 24\nH TMD BKK 2026-11-25 rain 32 24 5.2 - extra\n"
                    "H TMD TOOLONGLABEL 2026-11-26 rain 32 24 5.2 -\nH TMD BKK 2026-11-27 rain 99999999 24 5.2 -\n"
                    "\n   \nX TMD BKK 2026-11-28 rain 32 24 5.2 -\nH TMD BKK 2026-11-29 fog 30 22 1.0 10\n";
  CHECK(wxParse(bad, &w) && w.nHome == 1 && !strcmp(w.home[0].date, "2026-11-29") && w.home[0].tmax == 30 && w.home[0].rainPct == 10);
  // เกิน WX_MAX_HOME → ตัดที่ 4 ไม่ล้น array · D ซ้ำ → เอาตัวแรก
  const char* many = "wx1 9\nH a b 2026-01-01 - 1 1 - -\nH a b 2026-01-02 - 2 2 - -\nH a b 2026-01-03 - 3 3 - -\nH a b 2026-01-04 - 4 4 - -\n"
                     "H a b 2026-01-05 - 5 5 - -\nD s AAA 2026-01-01 - 1 1 - -\nD s BBB 2026-01-01 - 2 2 - -\n";
  CHECK(wxParse(many, &w) && w.nHome == 4 && w.home[3].tmax == 4 && !strcmp(w.destLabel, "AAA"));
  // บรรทัดยาวผิดปกติ (> buf) → ข้าม ไม่ overflow
  char longl[400]; strcpy(longl, "wx1 9\nH TMD BKK 2026-11-24 clear 33 24 0.0 -"); for (int i = 0; i < 200; i++) strcat(longl, " "); strcat(longl, "x\nH TMD BKK 2026-11-25 clear 1 2 - -\n");
  CHECK(wxParse(longl, &w) && w.nHome == 1 && w.home[0].tmax == 1);
  // tmax มี tmin ไม่มี → ไม่โชว์ครึ่งเดียว
  CHECK(wxParse("wx1 9\nH TMD BKK 2026-11-24 clear 33 - 0.0 -", &w) && !w.home[0].hasT && w.home[0].hasMm);

  // --- ตารางปุ่ม: ทุกจำนวน 1..6 ปุ่มต้องอยู่ในจอ ไม่ทับกัน ไม่ทับ header ---
  for (int n = 1; n <= 6; n++) {
    for (int i = 0; i < n; i++) {
      PRect a = gridCell(n, i);
      CHECK(a.x >= 0 && a.y >= GRID_Y && a.x + a.w <= 320 && a.y + a.h <= 240 && a.w >= 90 && a.h >= 90);
      CHECK(inRect(a.x, a.y, a) && inRect(a.x + a.w - 1, a.y + a.h - 1, a) && !inRect(a.x + a.w, a.y, a) && !inRect(a.x, a.y - 1, a));
      for (int j = i + 1; j < n; j++) {
        PRect b = gridCell(n, j);
        bool overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
        CHECK(!overlap);
      }
    }
    int c, r; gridDims(n, &c, &r); CHECK(c * r >= n);
    PRect f = gridCell(n, 0); printf("n=%d grid %dx%d cell %dx%d\n", n, c, r, f.w, f.h);
  }
  // --- fmtUp (ย้ายมาจาก main.cpp — ต้องให้ผลเดิม) ---
  char b[16];
  fmtUp(b, sizeof b, 59);       CHECK(!strcmp(b, "0M"));
  fmtUp(b, sizeof b, 22 * 60);  CHECK(!strcmp(b, "22M"));
  fmtUp(b, sizeof b, 14 * 3600 + 22 * 60 + 5); CHECK(!strcmp(b, "14H 22M"));
  fmtUp(b, sizeof b, 3 * 86400 + 14 * 3600 + 59 * 60); CHECK(!strcmp(b, "3D 14H"));
  printf(fails ? "\n%d FAILED\n" : "\nALL OK\n", fails);
  return fails ? 1 : 0;
}
