"""tuya33.py — Tuya v3.3 local control (MicroPython) — สั่ง on/off ปลั๊กในบ้านตรงๆ ไม่พึ่ง cloud.

โปรโตคอล 55AA frame + AES-128-ECB(local_key). ใช้เฉพาะคำสั่ง CONTROL (0x07) พอสำหรับ watchdog.
รองรับ v3.3 เท่านั้น (AES-ECB — MicroPython มี cryptolib ในตัว); v3.4/3.5 ใช้ handshake+GCM คนละเรื่อง.

ต้องรู้: device_id, local_key (16 ตัว, จาก `tinytuya wizard`), ip (LAN), dp (สวิตช์หลัก — ปลั๊กส่วนใหญ่ = "1").
frame = PREFIX(4) SEQ(4) CMD(4) LEN(4) [ "3.3"+12x00 header + AES-ECB(json) ] CRC32(4) SUFFIX(4).
"""
import socket, struct, time, json
try:
    import cryptolib
except ImportError:               # MicroPython รุ่นเก่าใช้ชื่อ ucryptolib
    import ucryptolib as cryptolib

_PREFIX  = 0x000055AA
_SUFFIX  = 0x0000AA55
_CONTROL = 7
_HEADER33 = b"3.3" + b"\x00" * 12   # version header (15 ไบต์) — prepend สำหรับ CONTROL ใน v3.3


def _crc32(data):
    # CRC32 (poly 0xEDB88320) — เขียนเองกัน MicroPython บางบิลด์ไม่มี binascii.crc32
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1))
    return crc ^ 0xFFFFFFFF


def _pad(b):                        # PKCS7 → ยาวหาร 16 ลงตัว (AES block size)
    p = 16 - (len(b) % 16)
    return b + bytes([p]) * p


def _frame(dev_id, key, dp, value):
    payload = json.dumps({
        "devId": dev_id, "uid": dev_id,
        "t": str(int(time.time())),
        "dps": {str(dp): value},
    }).encode()
    enc = cryptolib.aes(key, 1).encrypt(_pad(payload))   # mode 1 = ECB, key ใหม่ทุกครั้ง (one-shot)
    data = _HEADER33 + enc
    body = struct.pack(">IIII", _PREFIX, 1, _CONTROL, len(data) + 8) + data
    return body + struct.pack(">II", _crc32(body), _SUFFIX)


def set_switch(ip, dev_id, local_key, value, dp="1", timeout=5):
    """สั่งปลั๊ก dp = value (True/False). คืน True ถ้าต่อ+ส่งสำเร็จ."""
    key = local_key.encode() if isinstance(local_key, str) else local_key
    s = socket.socket()
    try:
        s.settimeout(timeout)
        s.connect((ip, 6668))                  # Tuya local = TCP 6668
        s.send(_frame(dev_id, key, dp, value))
        try:
            s.recv(1024)                       # อุปกรณ์ตอบ = รับคำสั่งแล้ว (best-effort ไม่บังคับ)
        except OSError:
            pass
        return True
    except OSError:
        return False
    finally:
        s.close()
