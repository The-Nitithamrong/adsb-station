// tuya.h — Tuya v3.3 local control (ESP32 Arduino) — สั่ง on/off ปลั๊กในบ้านตรงๆ ไม่พึ่ง cloud.
// โปรโตคอล 55AA frame + AES-128-ECB(local_key) ผ่าน mbedtls (มากับ ESP32 core). CONTROL (0x07) พอ.
// รองรับ v3.3 เท่านั้น (AES-ECB). ต้องรู้: device_id, local_key (16 ตัว), ip, dp (ปลั๊ก = "1").
#pragma once
#include <WiFi.h>
#include <time.h>
#include "mbedtls/aes.h"

// CRC32 (poly 0xEDB88320) — ตรงกับ tinytuya
static uint32_t tuyaCrc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int j = 0; j < 8; j++) crc = (crc >> 1) ^ (0xEDB88320UL & (0 - (crc & 1)));
  }
  return ~crc;
}

// สั่งปลั๊ก dp = value (true/false). คืน true ถ้าต่อ+ส่งสำเร็จ.
static bool tuyaSet(const char* ip, const char* devId, const char* localKey,
                    bool value, const char* dp = "1", uint16_t timeoutMs = 5000) {
  // 1) payload JSON
  char json[256];
  int jlen = snprintf(json, sizeof(json),
      "{\"devId\":\"%s\",\"uid\":\"%s\",\"t\":\"%lu\",\"dps\":{\"%s\":%s}}",
      devId, devId, (unsigned long)time(nullptr), dp, value ? "true" : "false");
  if (jlen <= 0 || jlen >= (int)sizeof(json)) return false;

  // 2) PKCS7 pad ให้หาร 16 ลงตัว
  size_t pad = 16 - (jlen % 16);
  size_t plen = jlen + pad;
  uint8_t plain[288];
  if (plen > sizeof(plain)) return false;
  memcpy(plain, json, jlen);
  for (size_t i = jlen; i < plen; i++) plain[i] = (uint8_t)pad;

  // 3) AES-128-ECB(local_key) ทีละบล็อก 16 ไบต์
  uint8_t enc[288];
  mbedtls_aes_context ctx;
  mbedtls_aes_init(&ctx);
  mbedtls_aes_setkey_enc(&ctx, (const uint8_t*)localKey, 128);
  for (size_t i = 0; i < plen; i += 16)
    mbedtls_aes_crypt_ecb(&ctx, MBEDTLS_AES_ENCRYPT, plain + i, enc + i);
  mbedtls_aes_free(&ctx);

  // 4) data = "3.3" + 12x00 header (15 ไบต์) + ciphertext
  uint8_t data[320];
  size_t dlen = 0;
  memcpy(data, "3.3", 3); dlen = 3;
  memset(data + dlen, 0, 12); dlen += 12;
  memcpy(data + dlen, enc, plen); dlen += plen;

  // 5) frame = PREFIX SEQ CMD LEN [data] CRC32 SUFFIX (big-endian)
  uint8_t frame[360];
  size_t f = 0;
  auto put32 = [&](uint32_t v) {
    frame[f++] = v >> 24; frame[f++] = v >> 16; frame[f++] = v >> 8; frame[f++] = v;
  };
  put32(0x000055AA); put32(1); put32(7); put32(dlen + 8);   // CONTROL=7, LEN=data+crc+suffix
  memcpy(frame + f, data, dlen); f += dlen;
  put32(tuyaCrc32(frame, f)); put32(0x0000AA55);

  // 6) ส่งผ่าน TCP 6668
  WiFiClient c;
  if (!c.connect(ip, 6668, timeoutMs)) return false;
  c.write(frame, f);
  unsigned long t0 = millis();
  while (c.connected() && !c.available() && millis() - t0 < timeoutMs) delay(10);  // best-effort อ่านตอบ
  c.stop();
  return true;
}
