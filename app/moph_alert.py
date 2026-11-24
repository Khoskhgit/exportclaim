"""Send a text message to a LINE group through MOPH Alert (MOPH Notify, กระทรวงสาธารณสุข).

The client key and secret key belong to one LINE group (the group the MOPH Alert bot was added to and registered
for), so there is no recipient to choose. The request body is LINE-style messages.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .line_push import LineError, chunks

URL = "https://morpromt2f.moph.go.th/api/notify/send?messages=yes"


class MophAlertError(LineError):
    pass


def send(client_key: str, secret_key: str, message: str, url: str = URL, opener=urllib.request.urlopen,
         timeout: int = 20) -> None:
    if not (client_key and secret_key):
        raise MophAlertError("ยังไม่ได้ตั้งค่า MOPH_ALERT_CLIENT_KEY / MOPH_ALERT_SECRET_KEY")
    body = {"messages": [{"type": "text", "text": t} for t in chunks(message)]}
    request = urllib.request.Request(url or URL, data=json.dumps(body).encode(), method="POST", headers={
        "client-key": client_key, "secret-key": secret_key, "Content-Type": "application/json"})
    try:
        with opener(request, timeout=timeout) as reply:
            raw = reply.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise MophAlertError(f"MOPH Alert ตอบ HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise MophAlertError(f"ส่ง MOPH Alert ไม่ได้: {exc}") from None
    try:
        answer = json.loads(raw)
    except ValueError:
        return
    status = answer.get("status") if isinstance(answer, dict) else None
    if status not in (None, 200, "200", "success", "ok"):
        raise MophAlertError(f"MOPH Alert ไม่รับข้อความ: {raw[:300]}")
