"""Send a text message through the LINE Messaging API (LINE Notify closed on 31 Mar 2025).

LINE_CHANNEL_TOKEN is the channel access token (long-lived) of a LINE Official Account. A LINE ID such as
"somsak-t" can't be addressed through the API: the message goes to LINE_TO, a user ID ("U" + 32 hex, shown as
"Your user ID" in the LINE Developers console of the person who made the channel) or LINE_GROUP_ID, a group
("C" + 32 hex) the account has been invited into, or, with both empty, to every friend of the account (broadcast).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.line.me/v2/bot/message/"
MAX_TEXT = 5000  # LINE's limit for one text message


class LineError(Exception):
    pass


def chunks(message: str, size: int = MAX_TEXT) -> list[str]:
    """Split a long message on line breaks into at most 5 messages (one request carries up to 5)."""
    parts, current = [], ""
    for line in message.split("\n"):
        while len(line) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:size])
            line = line[size:]
        if current and len(current) + 1 + len(line) > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    if len(parts) > 5:
        parts = parts[:5]
        parts[-1] = parts[-1][: size - 20] + "\n… (ตัดข้อความ)"
    return parts


def messages(message: str, flex: dict | None = None) -> list[dict]:
    """A flex bubble (with the first lines of the text as its notification text), or the text in chunks."""
    if flex:
        return [{"type": "flex", "altText": message[:400] or "สรุป", "contents": flex}]
    return [{"type": "text", "text": t} for t in chunks(message)]


def send(token: str, to: str, message: str, opener=urllib.request.urlopen, timeout: int = 20,
         flex: dict | None = None) -> None:
    if not token:
        raise LineError("ยังไม่ได้ตั้งค่า LINE_CHANNEL_TOKEN")
    body: dict = {"messages": messages(message, flex)}
    if to:
        body["to"] = to
    request = urllib.request.Request(API + ("push" if to else "broadcast"), data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener(request, timeout=timeout) as reply:
            reply.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LineError(f"LINE ตอบ HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise LineError(f"ส่ง LINE ไม่ได้: {exc}") from None
