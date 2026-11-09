import hashlib
import hmac
from datetime import date

import pytest

from app import fdh


def test_layout_follows_old_fdh_columns():
    assert [c for c, _, _ in fdh.layout("ORF")][-1] == "REFERDATE"
    adp = [c for c, _, _ in fdh.layout("ADP")]
    assert "BILLMAUD" not in adp and adp[:5] == ["HN", "AN", "DATEOPD", "TYPE", "CODE"] and adp[-1] == "SP_ITEM"
    assert [c for c, _, _ in fdh.layout("PHDB")] == ["HN", "CODE", "QTY", "PRICE", "AN", "SEQ", "INSCL"]
    assert [c for c, _, _ in fdh.layout("DRU")][-3:] == ["SIGCODE", "SIGTEXT", "PROVIDER"]


def test_fix_rows_permitno_referdate_and_blank_hn():
    rows = {
        "INS": [{"HN": "1", "SEQ": "691101", "PERMITNO": "PG0012345"}, {"HN": "2", "SEQ": "691102", "PERMITNO": "EP123"},
                {"HN": "", "SEQ": "691103"}],
        "ORF": [{"HN": "1", "SEQ": "691101", "DATEOPD": date(2026, 11, 1)}],
        "IRF": [{"AN": "", "REFER": "1"}],
    }
    asked = []

    def select(sql):
        asked.append(sql)
        return [{"vn": "691101", "auth_code": "AUTH9"}]

    out = fdh.fix_rows(rows, select)
    assert [r["PERMITNO"] for r in out["INS"]] == ["AUTH9", "EP123"]
    assert out["ORF"][0]["REFERDATE"] == date(2026, 11, 1)
    assert out["IRF"] == [] and len(asked) == 1 and "'691101'" in asked[0] and "'691102'" not in asked[0]
    assert rows["INS"][0]["PERMITNO"] == "PG0012345"  # input rows are not changed


def test_txt_is_utf8_with_header():
    data = fdh.to_txt("PAT", [{"HN": "000001", "FNAME": "สมชาย", "DOB": date(1990, 1, 2), "IDTYPE": 1}]).decode("utf-8")
    header, line, end = data.split("\r\n")
    assert header.startswith("HCODE|HN|") and end == ""
    assert "|19900102|" in line and "สมชาย" in line


def test_files_and_multipart():
    parts = fdh.files({}, with_phdb=False)
    assert list(parts)[0] == "ins.txt" and "phdb.txt" not in parts and len(parts) == 16
    assert "phdb.txt" in fdh.files({}, with_phdb=True)
    body, ctype = fdh.multipart({"ins.txt": b"A|B\r\n"}, boundary="XX")
    assert ctype == "multipart/form-data; boundary=XX"
    assert body == (b'--XX\r\nContent-Disposition: form-data; name="type"\r\n\r\ntxt\r\n'
                    b'--XX\r\nContent-Disposition: form-data; name="file"; filename="ins.txt"\r\nContent-Type: text/plain\r\n\r\n'
                    b"A|B\r\n\r\n--XX--\r\n")


def test_password_hash():
    expected = hmac.new(b"$jwt@moph#", b"secret", hashlib.sha256).hexdigest().upper()
    assert fdh.password_hash("secret", "$jwt@moph#") == expected


class FakePost:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append((url, data, headers))
        return self.replies.pop(0)


def make(post):
    return fdh.Client("user1", "pw", "10866", "https://fdh.moph.go.th", "$jwt@moph#", post=post)


def test_send_uses_token_and_reads_thai_message():
    post = FakePost((200, "aaa.bbb.ccc"), (200, '{"status": 200, "message": "OK", "message_th": "นำเข้าสำเร็จ"}'))
    reply = make(post).send("uat", {"ins.txt": b"x"})
    assert reply.ok and reply.message == "นำเข้าสำเร็จ"
    token_url, _, _ = post.calls[0]
    assert token_url.startswith("https://fdh.moph.go.th/token?Action=get_moph_access_token&user=user1&password_hash=")
    assert "hospital_code=10866" in token_url and "pw" not in token_url.split("password_hash=")[0]
    url, body, headers = post.calls[1]
    assert url == "https://uat-fdh.inet.co.th/api/v2/data_hub/16_files" and headers["Authorization"] == "Bearer aaa.bbb.ccc"
    assert b'filename="ins.txt"' in body


def test_prescreen_url_and_failure_message():
    post = FakePost((200, "aaa.bbb.ccc"), (400, '{"Status": 400, "Message": "Invalid file"}'))
    reply = make(post).send("prod", {}, prescreen=True)
    assert not reply.ok and reply.message == "Invalid file" and reply.http_status == 400
    assert post.calls[1][0] == "https://fdh.moph.go.th/api/v1/auth/open_api/fda/file"


def test_test_accepts_400():
    post = FakePost((200, "aaa.bbb.ccc"), (400, '{"status": 400, "message": "file is required"}'))
    assert make(post).test("uat").ok


def test_token_errors():
    with pytest.raises(fdh.FdhError, match="User or password"):
        make(FakePost((200, '{"MessageCode": 500, "Message": "User or password incorrect"}'))).token()
    with pytest.raises(fdh.FdhError, match="HTTP 502"):
        make(FakePost((502, "<html>bad gateway</html>"))).token()
    with pytest.raises(fdh.FdhError, match="FDH_USER"):
        fdh.Client("", "", "10866", "https://x", "k").token()


def test_too_big(monkeypatch):
    monkeypatch.setattr(fdh, "MAX_BYTES", 10)
    with pytest.raises(fdh.FdhError, match="50 MB"):
        make(FakePost((200, "aaa.bbb.ccc"))).send("uat", {"ins.txt": b"x" * 100})
