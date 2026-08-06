"""tuya_power.py — power-cycle ปลั๊กของ Pi#1 (L3).

ตอนนี้สั่งผ่าน **HA webhook** (HA automation ทำ off → delay → on) เหมือน ESP32 watchdog —
เพราะ Tuya local key ยังติด (รอ pi ใหม่/cloudcutter). พอได้ local key ค่อยสลับมา tinytuya
(local, ไม่พึ่ง cloud/internet) โดยไม่ต้องแก้ที่อื่น. stdlib urllib ล้วน.

⚠️ Pi#2 (ตัวรัน watchdog นี้) ต้องเสียบไฟ **คนละปลั๊กกับ Pi#1** — ไม่งั้นตัดแล้วตัวเองตายด้วย.
"""
import os
import urllib.request


def cycle():
    """สั่ง power-cycle. คืน True ถ้ายิงคำสั่งสำเร็จ (HTTP 2xx). การจับเวลา off→on อยู่ฝั่ง HA."""
    url = os.environ.get("HA_WEBHOOK_CYCLE", "")
    if not url:
        print("tuya_power: ไม่ได้ตั้ง HA_WEBHOOK_CYCLE")
        return False
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print("tuya_power webhook error:", e)
        return False
