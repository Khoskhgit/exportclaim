import base64
import json

import pytest

from app import eclaim_api, nhso18
from app.store import Store


class FakePost:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append((url, data, headers))
        return self.replies.pop(0)


def make(post):
    return eclaim_api.Client("user1", "pw", post=post)


def test_files_are_the_zip_txt_without_phdb_and_lab():
    rows = {"PAT": [{"HN": "1", "FNAME": "สมชาย"}]}
    parts = eclaim_api.files(rows)
    assert list(parts) == eclaim_api.FILES and "PHDB" not in parts and "LAB" not in parts
    assert parts["PAT"] == nhso18.to_txt("PAT", rows["PAT"])


def test_payload_matches_old_readdata():
    data = "HN|X\r\n".encode("cp874")
    p = eclaim_api.payload({"INS": data}, "UCS", True)
    assert p["fileType"] == "txt" and p["maininscl"] == "UCS" and p["importDup"] is True
    assert p["assignToMe"] is False and p["dataTypes"] == ["IP", "OP"] and p["opRefer"] is False
    assert p["file"]["ins"] == {"blobName": "INS.txt", "blobType": "text/plain", "blob": base64.b64encode(data).decode(),
                                "size": len(data), "encoding": "MS874"}
    assert p["file"]["lab"] is None
    assert eclaim_api.payload({}, "", False)["maininscl"] is None


def test_send_gets_token_then_posts_json():
    post = FakePost((200, '{"token": "tok123"}'), (200, '{"status": 200, "message": "นำเข้าสำเร็จ"}'))
    reply = make(post).send({"INS": b"a"}, "", False)
    assert reply.ok and reply.message == "นำเข้าสำเร็จ"
    url, body, headers = post.calls[0]
    assert url == "https://nhsoapi.nhso.go.th/FMU/ecimp/v1/auth" and json.loads(body) == {"username": "user1", "password": "pw"}
    url, body, headers = post.calls[1]
    assert url == "https://nhsoapi.nhso.go.th/FMU/ecimp/v1/send" and headers["Authorization"] == "Bearer tok123"
    assert json.loads(body)["file"]["ins"]["blob"] == "YQ=="


def test_send_failure_and_token_errors():
    post = FakePost((200, '{"token": "t"}'), (200, '{"status": 400, "message": "ไฟล์ไม่ถูกต้อง"}'))
    reply = make(post).send({}, "", False)
    assert not reply.ok and reply.message == "ไฟล์ไม่ถูกต้อง"
    with pytest.raises(eclaim_api.EclaimError, match="รหัสผ่านผิด"):
        make(FakePost((401, '{"message": "รหัสผ่านผิด"}'))).token()
    with pytest.raises(eclaim_api.EclaimError, match="NHSO_USER"):
        eclaim_api.Client("", "").token()
    with pytest.raises(eclaim_api.EclaimError, match="สิทธิ"):
        make(FakePost()).send({}, "XXX")


def test_eclaim_sent_counts_only_success(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.record_eclaim_send(["A"], "", False, False, 400, "bad")
    store.record_eclaim_send(["B", "B"], "UCS", True, True, 200, "ok")
    assert store.eclaim_sent(["A", "B"]) == {"B"}
    assert store.eclaim_history()[0]["maininscl"] == "UCS"
