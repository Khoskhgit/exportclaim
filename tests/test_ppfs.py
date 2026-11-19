from datetime import date

from app import ppfs
from app.ppfs import CHECK, DRUG, postnatal, window


def item(vn, day, code, hn="1", amount=None, nationality="99", an=None):
    rate = 150 if code == CHECK else 135
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": nationality,
            "code": code, "icode": "3004011", "item_name": "ตรวจหลังคลอด", "qty": 1,
            "unitprice": rate, "amount": rate if amount is None else amount}


def care(vn, day, number, birth="2026-10-01", hn="1"):
    return {"vn": vn, "care_date": date.fromisoformat(day), "care_no": number, "person_anc_id": 7,
            "labor_date": date.fromisoformat(birth), "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00",
            "an": None, "pttype": "20", "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13,
            "nationality": "99"}


def data(items, cares=(), births=(("1", "2026-10-01"),), iron=("v1",)):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"], "code": i["code"]} for i in items]
    return {"items": list(items), "cares": list(cares), "history": history, "iron": {v: 90 for v in iron},
            "births": [{"hn": hn, "person_anc_id": 7, "labor_date": date.fromisoformat(d)} for hn, d in births]}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_windows_are_counted_from_the_birth():
    assert [window(d) for d in (0, 7, 8, 15, 16, 42, 43, -1, None)] == [1, 1, 2, 2, 3, 3, None, None, None]


def test_a_clean_postnatal_visit():
    rows = postnatal(data([item("v1", "2026-10-05", CHECK), item("v1", "2026-10-05", DRUG)], [care("v1", "2026-10-05", 1)]))
    check, drug = rows
    assert (check.key, check.days, check.window, check.count, str(check.amount)) == ("v1|30015", 4, 1, 1, "150.00")
    assert codes(check) == ([], []) and codes(drug) == ([], [])


def test_limits_per_birth():
    items = [item("v1", "2026-10-03", CHECK), item("v2", "2026-10-06", CHECK), item("v3", "2026-10-10", CHECK),
             item("v4", "2026-10-20", CHECK), item("v5", "2026-11-20", CHECK),
             item("v1", "2026-10-03", DRUG), item("v3", "2026-10-10", DRUG), item("v4", "2026-10-20", DRUG)]
    rows = {r.key: r for r in postnatal(data(items, iron=("v1", "v3", "v4")))}
    assert "SAME_WINDOW" in codes(rows["v2|30015"])[0]  # day 5 again in the 1-7 window
    assert codes(rows["v3|30015"])[0] == [] and codes(rows["v4|30015"])[0] == ["OVER_COUNT"]  # 4th check
    assert "OVER_42" in codes(rows["v5|30015"])[0]
    assert codes(rows["v4|30016"])[0] == ["OVER_COUNT"] and codes(rows["v3|30016"])[0] == []  # 3rd drug


def test_errors_and_warnings():
    rows = {r.key: r for r in postnatal(data(
        [item("v1", "2026-10-05", CHECK, amount=100), item("v2", "2026-10-05", CHECK, hn="2"),
         item("v3", "2026-10-06", CHECK, hn="3", nationality="57"), item("v4", "2026-10-06", DRUG, an="690000001")],
        [care("v1", "2026-10-05", 3), care("v9", "2026-10-07", 1, hn="9")], iron=(),
        births=(("1", "2026-10-01"), ("3", "2026-10-01"), ("9", "2026-10-01"))))}
    assert codes(rows["v1|30015"]) == ([], ["CARE_NO", "PRICE"])
    assert codes(rows["v2|30015"])[0] == ["NO_BIRTH"] and "NO_CARE" in codes(rows["v2|30015"])[1]
    assert codes(rows["v3|30015"])[0] == ["NOT_THAI"]
    assert codes(rows["v4|30016"]) == (["IPD"], ["NO_IRON"])
    assert codes(rows["v9|30015"])[0] == ["NO_ITEM"]  # recorded but not billed: key the item in HOSxP


def test_sent_and_the_zip_keeps_only_the_claimed_items():
    rows = postnatal(data([item("v1", "2026-10-05", CHECK)]), {("v1", CHECK): "2026-11-01T10:00:00"})
    assert rows[0].sent == "2026-11-01T10:00:00"
    built = {"ADP": [{"SEQ": "v1", "CODE": CHECK}, {"SEQ": "v1", "CODE": "30011"}, {"SEQ": "v2", "CODE": CHECK}],
             "DRU": [{"SEQ": "v1"}], "LAB": [{"SEQ": "v1"}], "OPD": [{"SEQ": "v1"}]}
    out = ppfs.only_claimed(built, {"v1": {CHECK}})
    assert out["ADP"] == [{"SEQ": "v1", "CODE": CHECK}] and out["LAB"] == [] and out["OPD"] == [{"SEQ": "v1"}]
    assert out["DRU"] == [{"SEQ": "v1"}]  # drugs left in the build were ticked by the user


def test_the_zip_keeps_the_refer_in_aer_but_not_the_accident():
    refer = {"SEQ": "v1", "REFER_NO": "Out:228/70", "REFMAINO": "10699", "OREFTYPE": "1100", "UCAE": "E", "AUTHAE": "X1",
             "AEDATE": "20261021", "AETYPE": "V", "EMTYPE": "1"}
    accident = {"SEQ": "v2", "UCAE": "A", "AUTHAE": "X2", "AEDATE": "20261021"}
    out = ppfs.only_claimed({"ADP": [], "AER": [refer, accident]}, {"v1": set()})
    (row,) = out["AER"]
    assert (row["REFMAINO"], row["REFER_NO"], row["OREFTYPE"]) == ("10699", "Out:228/70", "1100")
    assert (row["UCAE"], row["AUTHAE"], row["AEDATE"], row["AETYPE"], row["EMTYPE"]) == ("", "", "", "", "")
    assert ppfs.only_claimed({"ADP": [], "AER": [{**refer, "UCAE": "R"}]}, {})["AER"][0]["UCAE"] == "R"
    assert ppfs.only_claimed({"ADP": [], "AER": [accident]}, {}, keep_lab=True)["AER"] == [accident]


def test_fdh_needs_an_account_and_a_known_zone(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.store import Store
    from factories import settings
    from test_api import FakeHosxp

    config = replace(settings(), export_dir=tmp_path / "e", fdh_user="", fdh_password="")
    client = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    body = {"service": "pregtest", "keys": ["v1|30014"], "dates": ["2026-03-01"]}
    assert client.post("/api/ppfs/fdh/send", json={**body, "zone": "x"}).status_code == 400
    response = client.post("/api/ppfs/fdh/send", json={**body, "zone": "uat"})
    assert response.status_code == 400 and "บัญชี FDH" in response.json()["detail"]
