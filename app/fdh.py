"""Send the 16/18 files to Financial Data Hub (FDH), ported from api_fdh.txt (the old "API ส่ง 16 แฟ้ม เข้า FDH").

Old flow: GetToken -> ExportData (copy the 16-file TXT into FDH's column layout, UTF-8) -> ReadDatas (multipart) -> send.
  token     POST {token_url}/token?Action=get_moph_access_token&user=..&password_hash=HMAC-SHA256&hospital_code=..
  send      POST {zone}/api/v2/data_hub/16_files          (Bearer, multipart: type=txt + one "file" part per file)
  prescreen POST {zone}/api/v1/auth/open_api/fda/file     (same files without PHDB)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .nhso18 import CRLF, SCHEMA, fmt, lit_list
from .validate import text

ZONES = {"uat": "https://uat-fdh.inet.co.th", "prod": "https://fdh.moph.go.th"}
SEND_PATH = "/api/v2/data_hub/16_files"
PRESCREEN_PATH = "/api/v1/auth/open_api/fda/file"
MAX_BYTES = 50 * 1024 * 1024
FILES = ["INS", "PAT", "OPD", "ORF", "ODX", "OOP", "IPD", "IRF", "IDX", "IOP", "CHT", "CHA", "AER", "ADP", "LVD", "DRU"]

# Columns of the old program's CreateCursor (the layout FDH reads). Columns the 18-file export has but FDH does not
# (ADP.BILLMAUD, PHDB.NAME, LAB) are dropped; FDH-only columns not filled by the old program stay empty.
_EXTRA = {"REFERDATE": ("D", 8), "SERVPRICE": ("N2", 15), "OPD_MEMO": ("C", 200), "INVOICE_NO": ("C", 50),
          "INVOICE_LT": ("C", 50), "CLINIC": ("C", 5), "ITEMSRC": ("C", 1), "PROVIDER": ("C", 25), "SIGCODE": ("C", 25),
          "SIGTEXT": ("C", 200)}
_COLUMNS = {
    "ORF": "HN DATEOPD CLINIC REFER REFERTYPE SEQ REFERDATE",
    "OOP": "HN DATEOPD CLINIC OPER DROPID PERSON_ID SEQ SERVPRICE",
    "CHT": "HN AN DATE TOTAL PAID PTTYPE PERSON_ID SEQ OPD_MEMO INVOICE_NO INVOICE_LT",
    "ADP": "HN AN DATEOPD TYPE CODE QTY RATE SEQ CAGCODE DOSE CA_TYPE SERIALNO TOTCOPAY USE_STATUS TOTAL QTYDAY TMLTCODE "
           "STATUS1 BI CLINIC ITEMSRC PROVIDER GRAVIDA GA_WEEK DCIP LMP SP_ITEM",
    "DRU": " ".join(c for c, _, _ in SCHEMA["DRU"]) + " SIGCODE SIGTEXT PROVIDER",
    "PHDB": "HN CODE QTY PRICE AN SEQ INSCL",
}


def layout(name: str) -> list[tuple[str, str, int]]:
    ours = {c: (k, s) for c, k, s in SCHEMA[name]}
    names = _COLUMNS.get(name, " ".join(ours)).split()
    return [(c, *(ours.get(c) or _EXTRA[c])) for c in names]


class FdhError(RuntimeError):
    """FDH refused the request or could not be reached."""


@dataclass
class Reply:
    ok: bool
    http_status: int
    status: int | None
    message: str


# ---------------------------------------------------------------- files
def fix_rows(rows: dict[str, list[dict]], select: Callable[[str], list[dict]]) -> dict[str, list[dict]]:
    """ExportData: drop rows without HN/AN, PERMITNO 'PG..' -> visit_pttype.auth_code, empty ORF.REFERDATE -> DATEOPD."""
    out = {}
    for name in FILES + ["PHDB"]:
        cols = {c for c, _, _ in SCHEMA[name]}
        key = "HN" if "HN" in cols else "AN" if "AN" in cols else ""
        out[name] = [dict(r) for r in rows.get(name, []) if not key or text(r.get(key))]
    pg = [r for r in out["INS"] if text(r.get("SEQ")) and text(r.get("PERMITNO")).startswith("PG")]
    if pg:
        found = {text(r["vn"]): text(r["auth_code"]) for r in select(
            f"SELECT vn, MAX(auth_code) auth_code FROM visit_pttype WHERE vn IN ({lit_list(text(r['SEQ']) for r in pg)}) "
            "AND IFNULL(auth_code,'')<>'' GROUP BY vn")}
        for r in pg:
            r["PERMITNO"] = found.get(text(r["SEQ"]), "")
    for r in out["ORF"]:
        if not r.get("REFERDATE"):
            r["REFERDATE"] = r.get("DATEOPD")
    return out


def to_txt(name: str, rows: list[dict]) -> bytes:
    cols = layout(name)
    lines = ["|".join(c for c, _, _ in cols)] + ["|".join(fmt(r.get(c), k, s) for c, k, s in cols) for r in rows]
    return (CRLF.join(lines) + CRLF).encode("utf-8")


def files(rows: dict[str, list[dict]], with_phdb: bool) -> dict[str, bytes]:
    names = FILES + (["PHDB"] if with_phdb else [])
    return {f"{n.lower()}.txt": to_txt(n, rows.get(n, [])) for n in names}


def multipart(parts: dict[str, bytes], boundary: str | None = None) -> tuple[bytes, str]:
    boundary = boundary or f"----FDH{secrets.token_hex(12)}"
    body = [f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ntxt\r\n'.encode()]
    for filename, data in parts.items():
        body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: text/plain\r\n\r\n".encode() + data + b"\r\n")
    body.append(f"--{boundary}--\r\n".encode())
    return b"".join(body), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------- HTTP
def password_hash(password: str, key: str) -> str:
    return hmac.new(key.encode(), password.encode(), hashlib.sha256).hexdigest().upper()


def _post(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URLs
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FdhError(f"เชื่อมต่อ FDH ไม่ได้ ({urllib.parse.urlsplit(url).hostname}): {reason}") from None


def _json(body: str) -> dict | None:
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _field(data: dict, *names: str):
    lower = {k.lower(): v for k, v in data.items()}
    for n in names:
        if lower.get(n.lower()) not in (None, ""):
            return lower[n.lower()]
    return None


class Client:
    def __init__(self, user: str, password: str, hcode: str, token_url: str, hash_key: str,
                 user_agent: str = "EMR", timeout: float = 120, post: Callable = _post):
        self.user, self.password, self.hcode = user, password, hcode
        self.token_url, self.hash_key, self.user_agent, self.timeout = token_url.rstrip("/"), hash_key, user_agent, timeout
        self._post = post

    def token(self) -> str:
        """The old GetToken: a JWT on success, or JSON with a non-zero MessageCode."""
        if not self.user or not self.password:
            raise FdhError("ยังไม่ได้ตั้ง FDH_USER / FDH_PASSWORD ใน .env")
        query = urllib.parse.urlencode({"Action": "get_moph_access_token", "user": self.user,
                                        "password_hash": password_hash(self.password, self.hash_key), "hospital_code": self.hcode})
        status, body = self._post(f"{self.token_url}/token?{query}", b"", {"User-Agent": self.user_agent}, 30)
        data = _json(body)
        if data is not None:
            code = _field(data, "MessageCode")
            token = _field(data, "token", "access_token", "jwt")
            if token and str(code or 0) == "0":
                return str(token)
            raise FdhError(f"ขอ Token FDH ไม่ได้: {_field(data, 'Message', 'message') or body[:200]}")
        token = body.strip().strip('"')
        if status != 200 or not re.fullmatch(r"[\w-]+\.[\w-]+\.[\w-]+", token):
            raise FdhError(f"ขอ Token FDH ไม่ได้ (HTTP {status})")
        return token

    def upload(self, url: str, parts: dict[str, bytes], accept_400: bool = False) -> Reply:
        body, content_type = multipart(parts)
        if len(body) > MAX_BYTES:
            raise FdhError(f"ข้อมูลมีขนาด {len(body) / 1048576:.1f} MB เกิน 50 MB กรุณาแบ่งส่ง")
        headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": content_type,
                   "Accept": "application/json", "User-Agent": self.user_agent}
        http, raw = self._post(url, body, headers, self.timeout)
        data = _json(raw) or {}
        status = _field(data, "Status", "status", "code")
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        message = text(_field(data, "Message_TH", "message_th", "Message", "message")) or (
            "" if data else re.sub(r"<[^>]+>|\s+", " ", raw)[:300].strip())
        ok_codes = {200, 400} if accept_400 else {200}
        ok = (status or http) in ok_codes and (http < 500)
        return Reply(ok, http, status, message or ("สำเร็จ" if ok else f"HTTP {http}"))

    def test(self, zone: str) -> Reply:
        """cmdTest: a form with only type=txt; HTTP 200 or 400 means the token and URL work."""
        return self.upload(ZONES[zone] + SEND_PATH, {}, accept_400=True)

    def send(self, zone: str, parts: dict[str, bytes], prescreen: bool = False) -> Reply:
        return self.upload(ZONES[zone] + (PRESCREEN_PATH if prescreen else SEND_PATH), parts)
