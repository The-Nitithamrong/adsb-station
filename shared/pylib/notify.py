"""notify.py — Telegram + healthchecks.io (stdlib urllib). reuse env เดิม TG_API/TG_CHAT/HC_URL."""
import os
import urllib.parse
import urllib.request


def telegram(text):
    api, chat = os.environ.get("TG_API"), os.environ.get("TG_CHAT")
    print(">>>", text.replace("\n", " | "))
    if not api:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(api, data=data, timeout=15)
    except Exception as e:
        print("telegram error:", e)


def hc_fail(msg=""):
    """ส่ง fail signal ไป healthchecks.io (HC_URL) — ให้ dead-man switch แจ้งถ้า watchdog เองก็ตาย."""
    url = os.environ.get("HC_URL")
    if not url:
        return
    try:
        urllib.request.urlopen(url.rstrip("/") + "/fail", data=msg.encode()[:900], timeout=10)
    except Exception as e:
        print("healthchecks error:", e)
