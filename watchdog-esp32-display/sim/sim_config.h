// sim_config.h — config ของตัวจำลอง (แทน src/config.h จริง ซึ่งมี token และไม่อยู่ใน git).
// ค่า watchdog ตรงกับ src/config.h.example · -DSIM_NLIGHTS=n เลือกจำนวนปุ่ม (1/2/3/4/6)
#pragma once
#define WIFI_SSID "sim"
#define WIFI_PASS "sim"
#define HA_BASE "http://192.168.41.207:8123"
#define HA_TOKEN "SIMTOKEN"
#ifndef SIM_NLIGHTS
#define SIM_NLIGHTS 4
#endif
#if SIM_NLIGHTS == 1
#define LIGHTS_INIT { {"BEDROOM", "light.l1"}, }
#elif SIM_NLIGHTS == 2
#define LIGHTS_INIT { {"CEILING", "light.l1"}, {"LAMP", "switch.l2"}, }
#elif SIM_NLIGHTS == 3
#define LIGHTS_INIT { {"CEILING", "light.l1"}, {"LAMP", "switch.l2"}, {"DESK", "light.l3"}, }
#elif SIM_NLIGHTS == 4
#define LIGHTS_INIT { {"CEILING", "light.l1"}, {"LAMP", "switch.l2"}, {"DESK", "light.l3"}, {"BALCONY", "switch.l4"}, }
#else
#define LIGHTS_INIT { {"CEILING", "light.l1"}, {"LAMP", "switch.l2"}, {"DESK", "light.l3"}, {"BALCONY", "switch.l4"}, {"CLOSET", "light.l5"}, {"BATHROOM LONG", "light.l6"}, }
#endif
#define PI_IP "192.168.41.241"
#define PI_PORT 22
#define PI_INFO_PORT 8099
#define HA_WEBHOOK_CYCLE "http://192.168.41.207:8123/api/webhook/pi1_power_cycle"
#define CHECK_MS 30000UL
#define DOWN_MS 1200000UL
#define OFF_SECONDS 60
#define COOLDOWN_MS 1500000UL
#define TCP_TIMEOUT_MS 4000
#define IDLE_MS 45000UL
#define DRY_RUN 0
