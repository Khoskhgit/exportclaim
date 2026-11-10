import io
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import nhso18
from app.approve import ApproveError, assign, cancelled, iso_date, parse_txt, parse_upload
from app.approve_routes import Approvals
from app.approve_store import ApproveStore
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

A, B, C = "1" * 13, "2" * 13, "3" * 13


def line(cid, txn_date, txn_time, amount, kind, appr, ref, channel="TMS", hcode="10866", reason=""):
    f = [""] * 43
    f[0], f[1], f[2], f[3], f[4], f[5], f[6] = f"EA00{hcode}", f"HCG{hcode}", "รพ.", "0040000209", "รพ.", "B", "0040000967"
    f[7], f[8], f[9], f[10], f[11], f[12], f[13] = txn_date, txn_time, "08/11/2026", "10:00:00", cid, "ชื่อ", "สกุล"
    f[20], f[22], f[25], f[26], f[27], f[28], f[30], f[35] = "01/01/1970", amount, kind, appr, ref, "EDC", reason, channel
    return "|".join(f)


def text_file(*lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


SAMPLE = text_file(
    line(A, "16/10/2026", "10:00:00", "1176.00", "Payment", "369484621", "369484621", "CB-IBANK", reason="เครื่อง EDC ไม่มีสัญญาณ"),
    line(B, "08/11/2026", "11:08:09", "772.00", "Payment", "369490258", "303842887"),
    line(C, "17/10/2026", "20:25:41", "-162.00", "Refund", "369485959", "301292314"),
)
NAME = "HCG10866_0000010866_TXT001_20261108.txt"


def test_txt_is_read_without_names():
    parsed = parse_txt(SAMPLE, NAME)
    assert parsed.hcode == "10866" and parsed.file_date == "2026-11-08" and parsed.total == "1786.00"
    first, _, refund = parsed.rows
    assert (first.appr_code, first.cid, first.txn_date, first.txn_time, first.amount, first.channel) == \
        ("369484621", A, "2026-10-16", "10:00:00", "1176.00", "CB-IBANK")
    assert first.reason == "เครื่อง EDC ไม่มีสัญญาณ"
    assert (refund.kind, refund.amount, refund.trans_ref) == ("REFUND", "-162.00", "301292314")
    assert not any("ชื่อ" in str(v) or "สกุล" in str(v) for r in parsed.rows for v in vars(r).values())


def test_bad_files_are_refused():
    with pytest.raises(ApproveError):
        parse_txt(b"not|a|bank|file\n")
    with pytest.raises(ApproveError):
        parse_upload("x.pdf", b"%PDF")
    assert iso_date("08/11/2569") == "2026-11-08" and iso_date("31/02/2026") == ""


def test_zip_of_the_download_folder():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"DWN_HOSPITAL/{NAME}", SAMPLE)
        z.writestr("DWN_HOSPITAL/HCG10866_0000010866_HCD001_20261108.pdf", b"%PDF")
    files, skipped = parse_upload("DWN.zip", buffer.getvalue())
    assert [n for n, _ in files] == [NAME] and skipped == ["HCG10866_0000010866_HCD001_20261108.pdf"]


def row(appr, cid, day, time, amount, kind="PAYMENT", ref="", old=""):
    return {"appr_code": appr, "cid": cid, "txn_date": day, "txn_time": time, "amount": amount, "kind": kind,
            "trans_ref": ref or appr, "appr_old": old}


def test_refund_takes_back_the_payment():
    pay = row("1", C, "2026-10-17", "19:00:00", "162.00", ref="301292314")
    other = row("2", C, "2026-10-17", "19:30:00", "162.00")
    by_ref = row("9", C, "2026-10-17", "20:25:41", "-162.00", "REFUND", ref="301292314")
    assert cancelled([pay, other, by_ref]) == {"1": "9"}
    by_amount = row("9", C, "2026-10-17", "20:25:41", "-162.00", "REFUND", ref="555")
    assert cancelled([pay, other, by_amount]) == {"2": "9"}  # the latest earlier payment of the same amount
    assert cancelled([by_amount]) == {}


def test_payment_goes_to_the_right_visit():
    visits = [
        {"cid": B, "vn": "v1", "date": "2026-11-08", "time": "08:00:00", "income": 772.0, "pttype": "10"},
        {"cid": B, "vn": "v2", "date": "2026-11-08", "time": "10:41:35", "income": 500.0, "pttype": "21"},
        {"cid": B, "vn": "v3", "date": "2026-11-08", "time": "13:00:00", "income": 772.0, "pttype": "21"},
    ]
    pay = row("369490258", B, "2026-11-08", "11:08:09", "772.00")
    assert assign([pay], visits, ("21",))["369490258"]["vn"] == "v3"  # right first, then the amount, then the time
    second = row("369490259", B, "2026-11-08", "11:09:00", "500.00")
    found = assign([pay, second], visits, ("21",))
    assert found["369490258"]["vn"] == "v3" and found["369490259"]["vn"] == "v2"
    assert assign([row("x", B, "2026-11-09", "09:00", "1.00")], visits, ("21",)) == {}


class ApproveHosxp(FakeHosxp):
    def ofc_visits(self, start, end, cids=(), pttypes=()):
        return [
            {"cid": A, "hn": "000001", "ptname": "ผู้ป่วย หนึ่ง", "vn": "691016082737", "vstdate": "2026-10-16",
             "vsttime": "08:27:37", "pttype": "21", "pttype_name": "เบิกจ่ายตรง", "income": 1176.0, "claim_code": "PP1"},
            {"cid": B, "hn": "000002", "ptname": "ผู้ป่วย สอง", "vn": "691108104135", "vstdate": "2026-11-08",
             "vsttime": "10:41:35", "pttype": "21", "pttype_name": "เบิกจ่ายตรง", "income": 770.0, "claim_code": "EP2"},
            {"cid": "9" * 13, "hn": "000009", "ptname": "ผู้ป่วย เก้า", "vn": "691020090000", "vstdate": "2026-10-20",
             "vsttime": "09:00:00", "pttype": "21", "pttype_name": "เบิกจ่ายตรง", "income": 300.0, "claim_code": "EP9"},
        ]


@pytest.fixture
def client(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "exports", hcode="10866")
    return TestClient(create_app(config, ApproveHosxp(), Store(tmp_path / "s.sqlite3")))


def test_import_and_list(client, tmp_path):
    response = client.post(f"/api/approve/import?name={NAME}", content=SAMPLE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files"][0]["payments"] == 2 and body["files"][0]["refunds"] == 1 and body["files"][0]["added"] == 3
    assert (tmp_path / "approve").is_dir()
    again = client.post(f"/api/approve/import?name={NAME}", content=SAMPLE).json()
    assert again["files"][0]["updated"] == 3 and again["again"]

    rows = {r["appr_code"] or r["vn"]: r for r in client.post("/api/approve/list", json={}).json()["rows"]}
    assert rows["369484621"]["state"] == "matched" and rows["369484621"]["vn"] == "691016082737"
    assert rows["369484621"]["claim_code"] == "PP1" and rows["369484621"]["cid"].endswith(A[-4:])
    assert A not in str(rows)
    assert rows["369490258"]["state"] == "matched" and rows["369490258"]["income"] == "770.00"
    assert rows["369485959"]["state"] == "refund" and rows["369485959"]["refund_of"] == ""
    assert "691020090000" not in rows  # 20/10 has no bank file yet
    client.post("/api/approve/import?name=HCG10866_0000010866_TXT001_20261020.txt",
                content=text_file(line(C, "20/10/2026", "09:00:00", "5.00", "Payment", "7", "7")))
    rows = {r["appr_code"] or r["vn"]: r for r in client.post("/api/approve/list", json={}).json()["rows"]}
    assert rows["691020090000"]["state"] == "missing" and rows["7"]["state"] == "no_visit"


def test_file_of_another_hospital_is_refused(client):
    data = text_file(line(A, "16/10/2026", "10:00:00", "1.00", "Payment", "1", "1", hcode="11111"))
    response = client.post(f"/api/approve/import?name={NAME}", content=data)
    assert response.status_code == 400 and "11111" in response.json()["detail"]


def test_export_uses_the_approve_code(tmp_path):
    store = ApproveStore(Store(tmp_path / "s.sqlite3"))
    store.save(parse_txt(SAMPLE, NAME), NAME, "x", "x")
    approvals = Approvals(store, ApproveHosxp(), ("21",))
    found = approvals.for_visits([{"cid": B, "vn": "691108104135", "date": "2026-11-08", "pttype": "21"},
                                  {"cid": C, "vn": "691017193542", "date": "2026-10-17", "pttype": "21"},
                                  {"cid": A, "vn": "691016082737", "date": "2026-10-16", "pttype": "10"}])
    assert {vn: p["appr_code"] for vn, p in found.items()} == {"691108104135": "369490258"}
    data = {"INS": [{"SEQ": "691108104135", "PERMITNO": "EP2"}, {"SEQ": "690000000001", "PERMITNO": "PP"}]}
    nhso18.set_permitno(data, {"691108104135": "369490258"})
    assert [r["PERMITNO"] for r in data["INS"]] == ["369490258", "PP"]
