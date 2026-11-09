"""Send the 16 files straight into New e-Claim, ported from api_eclaim_new.txt (the old "API ส่ง 16 แฟ้ม เข้า New-EClaim").

  token  POST {base}/ecimp/v1/auth   JSON {"username","password"} -> {"token": ...}
  send   POST {base}/ecimp/v1/send   Bearer, JSON with each file as base64 of the MS874 (windows-874) TXT
The old program only used production (its TestZone box was disabled).
"""
from __future__ import annotations

import base64
import json
from typing import Callable

from . import nhso18
from .fdh import FdhError, Reply, _field, _json, _post
from .validate import text

BASE = "https://nhsoapi.nhso.go.th/FMU"
FILES = ["INS", "PAT", "OPD", "ORF", "ODX", "OOP", "IPD", "IRF", "IDX", "IOP", "CHT", "CHA", "AER", "ADP", "LVD", "DRU"]
MAININSCL = ("UCS", "OFC", "LGO", "SSS", "NHS")


class EclaimError(FdhError):
    """New e-Claim refused the request or could not be reached."""


def files(rows: dict[str, list[dict]]) -> dict[str, bytes]:
    """The same TXT bytes as in the 18-file ZIP (PHDB is not sent, LAB is null, as in the old program)."""
    return {name: nhso18.to_txt(name, rows.get(name, [])) for name in FILES}


def payload(parts: dict[str, bytes], maininscl: str = "", import_dup: bool = False) -> dict:
    file = {name.lower(): {"blobName": f"{name}.txt", "blobType": "text/plain",
                           "blob": base64.b64encode(data).decode("ascii"), "size": len(data), "encoding": "MS874"}
            for name, data in parts.items()}
    file["lab"] = None
    return {"fileType": "txt", "maininscl": maininscl or None, "importDup": bool(import_dup), "assignToMe": False,
            "dataTypes": ["IP", "OP"], "opRefer": False, "file": file}


class Client:
    def __init__(self, user: str, password: str, user_agent: str = "EMR", base: str = BASE, timeout: float = 180,
                 post: Callable = _post):
        self.user, self.password, self.user_agent, self.base, self.timeout = user, password, user_agent, base.rstrip("/"), timeout
        self._post = post

    def _headers(self, token: str = "") -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": self.user_agent}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def token(self) -> str:
        if not self.user or not self.password:
            raise EclaimError("ยังไม่ได้ตั้ง NHSO_USER / NHSO_PASSWORD ใน .env")
        body = json.dumps({"username": self.user, "password": self.password}).encode()
        try:
            status, raw = self._post(f"{self.base}/ecimp/v1/auth", body, self._headers(), 30)
        except FdhError as exc:
            raise EclaimError(str(exc).replace("FDH", "New e-Claim")) from None
        data = _json(raw) or {}
        token = _field(data, "token", "access_token")
        if not token:
            message = text(_field(data, "message", "Message")) or f"HTTP {status}"
            raise EclaimError(f"ขอ Token New e-Claim ไม่ได้: {message}")
        return str(token)

    def send(self, parts: dict[str, bytes], maininscl: str = "", import_dup: bool = False) -> Reply:
        if maininscl and maininscl not in MAININSCL:
            raise EclaimError("สิทธิที่เลือกไม่ถูกต้อง")
        body = json.dumps(payload(parts, maininscl, import_dup)).encode()
        token = self.token()
        try:
            http, raw = self._post(f"{self.base}/ecimp/v1/send", body, self._headers(token), self.timeout)
        except FdhError as exc:
            raise EclaimError(str(exc).replace("FDH", "New e-Claim")) from None
        data = _json(raw) or {}
        status = _field(data, "status", "Status")
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        message = text(_field(data, "message_th", "message", "Message")) or ("" if data else raw[:300].strip())
        ok = (status or http) == 200 and http < 400
        return Reply(ok, http, status, message or ("สำเร็จ" if ok else f"HTTP {http}"))
