from datetime import date
from decimal import Decimal

from app.ppfs_cervix import (HPV_1618, HPV_LAB, HPV_NEG, HPV_OTHER, PAP_LAB, PAP_NEG, PAP_POS, check, hpv_code, ktb_csv,
                             pap_code, rewrite_adp)

HPV_ICODE, PAP_ICODE = "3000851", "3000740"


def item(vn="v1", day="2026-03-05", hn="1", icode=HPV_ICODE, age=40, sex="2", nationality="99", qty=1, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": None, "pttype": "72",
            "pttype_name": "บัตรทอง", "cid": "1" * 13, "sex": sex, "nationality": nationality, "ptname": "ผู้ป่วย ทดสอบ",
            "age_y": age, "icode": icode, "paidst": paidst, "item_name": "HPV", "adp": "38608" if icode == HPV_ICODE else "38302",
            "qty": qty, "amount": 700 * qty}


def data(items, results=None, history=None, dx=None):
    labs = {HPV_ICODE: HPV_LAB, PAP_ICODE: PAP_LAB}
    if results is None:
        results = {(i["vn"], labs[i["icode"]]): "Negative" for i in items}
    if history is None:
        history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items]
    return {"items": items, "results": results, "history": history, "icodes": labs,
            "dx": {i["vn"] for i in items} if dx is None else dx}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_hpv_results_pick_the_ktb_code():
    assert hpv_code("Negative") == HPV_NEG
    assert hpv_code("HPV Type 16") == hpv_code("HPV Type 16,68,52") == hpv_code("HPV Type 18 ,39,35,53") == HPV_1618
    assert hpv_code("HPV Type non 16,18") == HPV_OTHER and hpv_code("HPV Type 52") == HPV_OTHER
    assert hpv_code("") is None and hpv_code(None) is None


def test_pap_results():
    assert pap_code("NILM") == PAP_NEG and pap_code("LSIL") == PAP_POS
    assert pap_code("Image") is None and pap_code("Out Lab") is None


def test_a_clean_negative_hpv():
    (row,) = check(data([item()]))
    assert (row.key, row.code, row.amount, row.extra["adp_type"], row.extra["hosxp_adp"]) == ("v1|1B0046_0", HPV_NEG, Decimal("700.00"), "4", "38608")
    assert codes(row) == ([], [])


def test_no_result_cannot_be_sent():
    (row,) = check(data([item(icode=PAP_ICODE)], results={("v1", PAP_LAB): "Image"}))
    assert row.key == "v1|Pap" and "NO_RESULT" in codes(row)[0]


def test_once_every_five_years():
    first, again, later = item("v0", "2022-06-01"), item("v1", "2026-03-05"), item("v2", "2032-01-01")
    rows = {r.vn: r for r in check(data([again, later], history=[{"vn": x["vn"], "hn": "1", "vstdate": x["vstdate"]} for x in (first, again, later)]))}
    assert "WITHIN_5Y" in codes(rows["v1"])[0] and rows["v1"].extra["last_vn"] == "v0"
    assert "WITHIN_5Y" not in codes(rows["v2"])[0]


def test_age_and_people():
    assert "AGE" in codes(check(data([item(age=60)]))[0])[0]
    assert "AGE" in codes(check(data([item(age=14)]))[0])[0]
    assert codes(check(data([item(age=25)]))[0]) == ([], ["RISK_AGE"])
    assert "NOT_FEMALE" in codes(check(data([item(sex="1")]))[0])[0]
    assert "NOT_THAI" in codes(check(data([item(nationality="48")]))[0])[1]
    assert "NO_DX" in codes(check(data([item()], dx=set()))[0])[1]
    assert "SELF_PAID" in codes(check(data([item(paidst="03")]))[0])[0]


def test_positive_results_ask_for_follow_up():
    assert "REFER_COLPO" in codes(check(data([item()], results={("v1", HPV_LAB): "HPV Type 16"}))[0])[1]
    assert "REFER_LBC" in codes(check(data([item()], results={("v1", HPV_LAB): "HPV Type non 16,18"}))[0])[1]


def test_rewrite_sends_the_ktb_code_and_type():
    adp = {"ADP": [{"SEQ": "v1", "CODE": "38608", "TYPE": "15"}, {"SEQ": "v1", "CODE": "10000", "TYPE": "4"},
                   {"SEQ": "v2", "CODE": "38302", "TYPE": "15"}]}
    rewrite_adp(adp, {"v1": {HPV_1618}, "v2": {PAP_NEG}}, {"v1|1B0046_1": "38608", "v2|1B004N": "38302"})
    assert [(r["CODE"], r["TYPE"]) for r in adp["ADP"]] == [(HPV_1618, "4"), ("10000", "4"), (PAP_NEG, "3")]


def test_ktb_csv_follows_the_health_platform_format():
    people = [{"hn": "1", "cid": "1101700000011", "fname": "สมหญิง.", "lname": " ใจดี ", "birthday": date(1985, 4, 29), "nationality": "99"},
              {"hn": "2", "cid": "0000000000001", "fname": "Mary", "lname": "Doe", "birthday": date(1990, 1, 1), "nationality": "48"}]
    content, written, skipped = ktb_csv(lambda sql: people, "10866", ["1", "2"])
    lines = content.decode("utf-8").split("\r\n")
    assert lines[0] == "HCODE,CID,NAME,MIDDLENAME,LASTNAME,BIRTHDAYDATE,NATION,WORKPERMIT,PASSPORTNUMBER"
    assert lines[1] == "10866,1101700000011,สมหญิง,,ใจดี,19850429,THA,,"
    assert (written, skipped) == (1, 1) and not content.startswith(b"\xef\xbb\xbf")


def test_ktb_zip_names_files_as_ktb_checks_them(tmp_path):
    import zipfile
    from datetime import datetime

    from app.ppfs_cervix import ktb_yymm, ktb_zip
    rows = {"INS": [{"HN": "1", "SEQ": "691105"}], "PAT": [{"HN": "1", "PERSON_ID": "1101700000011"}],
            "ADP": [{"HN": "1", "SEQ": "691105", "DATEOPD": "20261105", "TYPE": "4", "CODE": "1B0046_0"},
                    {"HN": "2", "SEQ": "691001", "DATEOPD": "20261001", "TYPE": "4", "CODE": "1B0046_1"}],
            "DRU": [], "LAB": [{"HN": "1"}], "PHDB": [{"HN": "1"}]}
    assert ktb_yymm(rows, date(2026, 12, 1)) == "6911"  # newest service month, Buddhist year
    assert ktb_yymm({}, date(2027, 1, 5)) == "7001"
    result = ktb_zip(rows, "10866", tmp_path, datetime(2026, 11, 21, 22, 0, 0))
    with zipfile.ZipFile(result.zip_path) as z:
        names = z.namelist()
        ins = z.read("INS6911.txt").decode("cp874")
    assert names == ["INS6911.txt", "PAT6911.txt", "ADP6911.txt"]  # no empty DRU, no LAB/PHDB
    assert all(len(n.removesuffix(".txt")) <= 20 for n in names)
    assert ins.startswith("HN|INSCL|") and "|691105|" in ins
    assert result.counts == {"INS": 1, "PAT": 1, "ADP": 2}
