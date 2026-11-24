import json
import time
import urllib.error
from dataclasses import replace
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import autopull, line_push
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp
from test_seamless import FakeSeamless


def test_fiscal_year_and_schedule():
    assert autopull.fiscal_year_be(date(2026, 9, 30)) == 2569 and autopull.fiscal_year_be(date(2026, 10, 1)) == 2570
    assert autopull.parse_time("06:00") == (6, 0) and autopull.parse_time("6.30") == (6, 30)
    assert autopull.parse_time("25:00") is None and autopull.parse_time("") is None
    now = datetime(2026, 11, 24, 5, 59, tzinfo=autopull.TZ)
    assert autopull.next_run(now, (6, 0)) == datetime(2026, 11, 24, 6, 0, tzinfo=autopull.TZ)
    assert autopull.next_run(now.replace(hour=6), (6, 0)) == datetime(2026, 11, 25, 6, 0, tzinfo=autopull.TZ)


def test_line_chunks_and_requests():
    assert line_push.chunks("a\nb") == ["a\nb"]
    long = "\n".join("x" * 3000 for _ in range(3))
    assert [len(c) for c in line_push.chunks(long)] == [3000, 3000, 3000]
    assert len(line_push.chunks("y" * 40000)) == 5
    sent = []

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def opener(request, timeout):
        sent.append((request.full_url, request.headers["Authorization"], json.loads(request.data)))
        return Reply()

    line_push.send("tok", "U123", "hi", opener=opener)
    line_push.send("tok", "", "hi", opener=opener)
    assert sent[0][0].endswith("/push") and sent[0][1] == "Bearer tok" and sent[0][2]["to"] == "U123"
    assert sent[1][0].endswith("/broadcast") and "to" not in sent[1][2]
    with pytest.raises(line_push.LineError, match="LINE_CHANNEL_TOKEN"):
        line_push.send("", "U1", "hi", opener=opener)

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "x", {}, __import__("io").BytesIO(b'{"message":"bad token"}'))
    with pytest.raises(line_push.LineError, match="401.*bad token"):
        line_push.send("t", "U1", "hi", opener=refuse)


class FakeEclaim:
    def list_reps(self, fund, month, year):
        return []


def test_run_once_pulls_both_and_sends_a_summary(tmp_path):
    messages = []
    config = replace(settings(), export_dir=tmp_path / "e", eclaim_user="u", eclaim_password="p", eclaim_totp_secret="GEZDGNBV")
    fake = FakeSeamless()
    app = create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), eclaim=FakeEclaim(), seamless=fake,
                     line_send=messages.append)
    client = TestClient(app)
    assert client.get("/api/autopull").json()["time"] == ""
    assert client.post("/api/autopull/run").json() == {"started": True}
    for _ in range(300):
        status = client.get("/api/autopull").json()
        if status["last"] and not status["running"]:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("auto pull did not finish")
    text = messages[0]
    year = autopull.fiscal_year_be(date.today())
    assert "[e-Claim REP UCS, OFC, LGO ย้อนหลัง 2 เดือน]" in text and f"[Seamless for DMIS ปีงบ {year}]" in text
    assert "ดาวน์โหลดแล้ว 3" in text and "- R1 NAP" in text and "อื่น: Seamless ตอบ HTTP 500" in text
    assert status["last"]["line"] == "ส่ง LINE แล้ว"
    steps = {st["key"]: st for st in status["steps"]}
    assert [st["key"] for st in status["steps"]] == ["eclaim", "seamless", "aipn", "mms", "line"]
    assert steps["eclaim"]["state"] == "done" and steps["aipn"]["state"] == "skip" and steps["mms"]["state"] == "skip" and steps["line"]["state"] == "done"
    assert steps["seamless"]["state"] == "error" and steps["seamless"]["imported"] == 3  # project 28 answered HTTP 500
    assert ("1", year - 543) in fake.searches
    assert client.post("/api/autopull/line-test").json() == {"sent": True} and "ทดสอบส่ง LINE" in messages[-1]


def test_summary_when_a_pull_could_not_start():
    text = autopull.summary(None, None, datetime(2026, 11, 25, 6, 0), 2570)
    assert text.startswith("สรุปดึง REP อัตโนมัติ 25/11/2569 06:00") and "[e-Claim REP] ไม่ได้ดึง" in text


def test_loop_runs_at_the_set_time():
    from types import SimpleNamespace
    times = iter([datetime(2026, 11, 25, 5, 59, 59, tzinfo=autopull.TZ)] + [datetime(2026, 11, 25, 6, 0, 1, tzinfo=autopull.TZ)] * 5)
    auto = autopull.AutoPull(SimpleNamespace(state=None), replace(settings(), auto_pull_time="06:00"), send=lambda t: None,
                             clock=lambda: next(times))
    ran = []
    auto._stop.wait = lambda seconds: None
    auto.run_once = lambda: (ran.append(auto.next_at), auto.stop())
    auto.loop()
    assert ran == [datetime(2026, 11, 25, 6, 0, tzinfo=autopull.TZ)]


def test_moph_alert_request_and_errors():
    import json
    from app import moph_alert
    sent = []

    class Reply:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def opener(request, timeout):
        sent.append(request)
        return Reply(b'{"status":200,"message":"Succesfully"}')

    moph_alert.send("ck", "sk", "hi", opener=opener)
    request = sent[0]
    assert request.full_url == moph_alert.URL and request.get_header("Client-key") == "ck"
    assert request.get_header("Secret-key") == "sk"
    assert json.loads(request.data) == {"messages": [{"type": "text", "text": "hi"}]}
    with pytest.raises(moph_alert.MophAlertError, match="MOPH_ALERT_CLIENT_KEY"):
        moph_alert.send("", "sk", "hi", opener=opener)
    with pytest.raises(line_push.LineError, match="ไม่รับข้อความ"):
        moph_alert.send("ck", "sk", "hi", opener=lambda r, timeout: Reply(b'{"status":401,"message":"bad key"}'))


def test_autopull_uses_moph_alert_when_its_keys_are_set(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e", moph_alert_client_key="ck", moph_alert_secret_key="sk")
    app = create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"))
    status = TestClient(app).get("/api/autopull").json()
    assert status["line_ready"] and status["line_to"] == "กลุ่ม LINE ของ MOPH Alert"


def test_flex_summary_card():
    import json
    eclaim = {"funds": ["ucs"], "months": 2, "found": 3, "total": 2, "imported": 2, "errors": 1, "lines": [
        {"kind": "rep", "ok": True, "rep_no": "691000043", "fund": "ucs", "sent": 10, "failed": 4},
        {"kind": "rep", "ok": False, "rep_no": "691000044", "text": "หมดเวลา"}]}
    seamless = {"found": 1, "total": 1, "imported": 1, "errors": 0, "lines": [
        {"kind": "seamless", "ok": True, "rep_no": "R1", "project": "NAP", "net": "1200.5", "denied": "0"}]}
    card = autopull.flex(eclaim, seamless, datetime(2026, 11, 25, 6, 0), 2570)
    dumped = json.dumps(card, ensure_ascii=False)
    assert card["type"] == "bubble" and "25/11/2569 06:00" in dumped
    assert "691000043 UCS" in dumped and "ผ่าน 6 · ไม่ผ่าน 4" in dumped and "1,200.50 บาท" in dumped
    assert "691000044: หมดเวลา" in dumped and card["header"]["backgroundColor"] == "#E65100"
    texts = [n for n in json.loads(dumped, object_hook=lambda d: d).get("body", {}).get("contents", [])]
    assert texts and len(dumped) < 30000
    message = line_push.messages("สรุป\nบรรทัด", card)
    assert message == [{"type": "flex", "altText": "สรุป\nบรรทัด", "contents": card}]


def test_deliver_falls_back_to_text_when_flex_is_refused(tmp_path):
    from fastapi import FastAPI
    auto = autopull.AutoPull(FastAPI(), settings())
    sent = []

    def picky(text, flex=None):
        if flex:
            raise line_push.LineError("flex not allowed")
        sent.append(text)
    auto.send = picky
    auto.deliver("ข้อความ", {"type": "bubble"})
    assert sent == ["ข้อความ"]
