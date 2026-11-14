import zipfile
from datetime import date
from decimal import Decimal

from app import nhso18


def test_txt_layout_matches_old_export():
    rows = [{"HN": "000001", "BILLMAUD": "12", "TYPE": "17", "CODE": "55021", "QTY": 1, "RATE": Decimal("100"), "SEQ": "691107010119",
             "TOTCOPAY": 0, "TOTAL": Decimal("100"), "QTYDAY": None, "DATEOPD": date(2026, 11, 7), "LMP": None, "DCIP": None}]
    data = nhso18.to_txt("ADP", rows).decode("cp874")
    header, line, end = data.split("\r\n")
    assert end == "" and "DCIP/E_Screen" in header and header.startswith("HN|AN|DATEOPD|BILLMAUD")
    assert line == "000001||20261107|12|17|55021|1|100.00|691107010119|||||0.00||100.00|0||||||||"


def test_field_formats():
    assert nhso18.fmt("2056-12-31", "D", 8) == "20561231"
    assert nhso18.fmt("2599-12-31", "D", 8) == "20561231"  # Buddhist year
    assert nhso18.fmt(None, "N1", 3) == "0.0"
    assert nhso18.fmt(11.0, "C", 14) == "11.00000"  # numbers put in character fields, like DRUGPRIC
    assert nhso18.fmt(3, "C", 2) == "3"
    assert nhso18.fmt("ชื่อยาที่ยาวมาก", "C", 5) == "ชื่อย"
    assert nhso18.fmt("X-RAY\r", "C", 20) == "X-RAY\r"  # stray CR kept as in the old file
    assert nhso18.fmt("a|b", "C", 5) == "a b"


def test_replace_keeps_first_position_and_last_values():
    rows = [{"SEQ": "1", "OPER": "9920", "DROPID": "a"}, {"SEQ": "2", "OPER": "1", "DROPID": "b"},
            {"SEQ": "1", "OPER": "9920", "DROPID": "c"}]
    assert nhso18.replace_rows(rows, ("SEQ", "OPER")) == [{"SEQ": "1", "OPER": "9920", "DROPID": "c"}, rows[1]]


def test_ipd_rows_have_no_seq():
    rows = nhso18.finish("CHT", [{"SEQ": "690000001", "AN": "690000001", "HN": "1"}, {"SEQ": "6911", "AN": "", "HN": "2"}])
    assert [r["SEQ"] for r in rows] == ["", "6911"]


def test_title_rules():
    assert nhso18.patient_title({"pname": "นางสาว"}) == "น.ส."
    assert nhso18.patient_title({"pname": "Mr.", "sex": "1", "birthday": date(1990, 1, 1)}) == "นาย"
    assert nhso18.patient_title({"pname": "", "sex": "2", "birthday": date.today()}) == "ด.ญ."
    assert nhso18.patient_title({"pname": "", "sex": "2", "birthday": date(1980, 1, 1), "marrystatus": "1"}) == "น.ส."


def test_adp_code_rules():
    base = {"seq": "1", "maininscl": "UCS", "paidst": "02", "qty": 2, "unitprice": 50, "sum_price": 100,
            "dateopd": date(2026, 11, 7), "type": "2"}
    assert nhso18.adp_item({**base, "billmaud": 12, "adp_code": None}, set(), set())["code"] == "55999"
    assert nhso18.adp_item({**base, "billmaud": 9, "adp_code": None, "maininscl": "OFC"}, set(), set())["code"] is None
    assert nhso18.adp_item({**base, "billmaud": 9, "adp_code": None}, set(), set())["code"] == "51999"
    assert nhso18.adp_item({**base, "billmaud": 7, "adp_code": "31001"}, set(), set())["code"] == "31001"
    paid = nhso18.adp_item({**base, "billmaud": 7, "adp_code": "31001", "paidst": "03"}, set(), set())
    assert paid["qty"] == 0 and paid["sum_price"] == 100


def test_zip_has_all_files(tmp_path):
    result = nhso18.build_zip({"INS": [{"HN": "1", "SEQ": "2"}]}, "10866", tmp_path)
    with zipfile.ZipFile(result.zip_path) as z:
        assert z.namelist() == [f"{n}.TXT" for n in nhso18.FILES]
        assert z.read("LVD.TXT").decode("cp874") == "SEQLVD|AN|DATEOUT|TIMEOUT|DATEIN|TIMEIN|QTYDAY\r\n"
    assert result.counts["INS"] == 1


def test_local_exclusions_in_query():
    b = nhso18.Builder(lambda sql: [], nhso18.Config(hcode="1"), "rcm", "eclaim", excluded={("691", "1500"), ("691", "15'0")})
    clause = b.noexport("oo", "vn", False)
    assert "opitemrece_noexport" in clause and "'691|1500'" in clause and "'691|15\\'0'" in clause


# ---------------------------------------------------------------- UCS walk-in
from datetime import timedelta  # noqa: E402


def visit(vn, hn="1", day=date(2026, 11, 3), time=timedelta(hours=9), pttype="W1", inscl="UCS", an=None):
    return {"vn": vn, "hn": hn, "vstdate": day, "vsttime": time, "pttype": pttype, "maininscl": inscl, "an": an}


def test_same_day_walkin_groups_by_hn_and_day():
    rows = [visit("B", time=timedelta(hours=14)), visit("A", time=timedelta(hours=8, minutes=5)), visit("C", time="9:00:00"),
            visit("D", day=date(2026, 11, 4)),            # another day
            visit("E", hn="2"),                            # another patient, alone
            visit("F", pttype="X"), visit("G", inscl="OFC"), visit("H", an="690000001")]
    assert nhso18.same_day_walkin(rows, ["W1"]) == {"C": "A", "B": "A"}
    assert nhso18.same_day_walkin(rows, []) == {}
    # search rows use date/time/has_an
    search = [{"vn": "1", "hn": "9", "date": date(2026, 11, 3), "time": timedelta(hours=10), "pttype": "W1", "maininscl": "UCS"},
              {"vn": "2", "hn": "9", "date": date(2026, 11, 3), "time": timedelta(hours=9), "pttype": "W1", "maininscl": "UCS"}]
    assert nhso18.same_day_walkin(search, ["W1"]) == {"1": "2"}


def test_walkin_siblings_query():
    asked = []

    def select(sql):
        asked.append(sql)
        return [visit("A"), visit("B", time=timedelta(hours=10))]

    assert nhso18.walkin_siblings(select, ["B"], ["W1"]) == {"B": "A"}
    assert "vn IN ('B')" in asked[0] and "pttype IN ('W1')" in asked[0]
    assert nhso18.walkin_siblings(select, ["B"], []) == {} and len(asked) == 1


def test_merge_walkin_files():
    d = date(2026, 11, 3)
    data = {
        "INS": [{"HN": "1", "SEQ": "A"}, {"HN": "1", "SEQ": "B"}],
        "PAT": [{"HN": "1"}],
        "OPD": [{"HN": "1", "SEQ": "A", "TIMEOPD": "0800", "UUC": "2"}, {"HN": "1", "SEQ": "B", "TIMEOPD": "1400", "UUC": "1"}],
        "ODX": [{"SEQ": "A", "DIAG": "J00", "DXTYPE": "1"}, {"SEQ": "B", "DIAG": "J00", "DXTYPE": "1"},
                {"SEQ": "B", "DIAG": "K30", "DXTYPE": "1"}, {"SEQ": "B", "DIAG": "Z71", "DXTYPE": "2"}],
        "OOP": [{"SEQ": "A", "OPER": "9920"}, {"SEQ": "B", "OPER": "9920"}, {"SEQ": "B", "OPER": "9390"}],
        "CHT": [{"SEQ": "A", "TOTAL": Decimal("100.00"), "PAID": Decimal("0.00")},
                {"SEQ": "B", "TOTAL": Decimal("50.50"), "PAID": Decimal("10.00")}],
        "CHA": [{"SEQ": "A", "CHRGITEM": "31", "AMOUNT": Decimal("60")}, {"SEQ": "B", "CHRGITEM": "31", "AMOUNT": Decimal("20")},
                {"SEQ": "B", "CHRGITEM": "41", "AMOUNT": Decimal("30.50")}],
        "ADP": [{"SEQ": "A", "TYPE": "5", "CODE": "WALKIN", "RATE": Decimal("0"), "SP_ITEM": None, "QTY": 1, "TOTAL": None},
                {"SEQ": "B", "TYPE": "5", "CODE": "WALKIN", "RATE": Decimal("0"), "SP_ITEM": None, "QTY": 1, "TOTAL": None},
                {"SEQ": "A", "TYPE": "3", "CODE": "55021", "RATE": Decimal("100"), "SP_ITEM": None, "QTY": 1,
                 "TOTCOPAY": Decimal("0"), "TOTAL": Decimal("100")},
                {"SEQ": "B", "TYPE": "3", "CODE": "55021", "RATE": Decimal("100"), "SP_ITEM": None, "QTY": 2,
                 "TOTCOPAY": Decimal("0"), "TOTAL": Decimal("200")}],
        "DRU": [{"SEQ": "A", "DID": "D1", "DATE_SERV": d, "USE_STATUS": "1", "AMOUNT": 10, "TOTAL": Decimal("20"), "TOTCOPAY": 0},
                {"SEQ": "B", "DID": "D1", "DATE_SERV": d, "USE_STATUS": "1", "AMOUNT": 5, "TOTAL": Decimal("10"), "TOTCOPAY": 0},
                {"SEQ": "B", "DID": "D2", "DATE_SERV": d, "USE_STATUS": "1", "AMOUNT": 1, "TOTAL": Decimal("3"), "TOTCOPAY": 0}],
        "AER": [{"SEQ": "B", "UCAE": ""}],
        "IPD": [{"AN": "690000001"}],
        "ORF": [],
    }
    out = nhso18.merge_walkin(data, {"B": "A"})
    assert [r["SEQ"] for r in out["INS"]] == ["A"] and out["PAT"] is data["PAT"]
    assert out["OPD"] == [{"HN": "1", "SEQ": "A", "TIMEOPD": "0800", "UUC": "1"}]
    assert [(r["DIAG"], r["DXTYPE"]) for r in out["ODX"]] == [("J00", "1"), ("K30", "4"), ("Z71", "2")]
    assert {r["SEQ"] for r in out["ODX"]} == {"A"}
    assert [r["OPER"] for r in out["OOP"]] == ["9920", "9390"]
    assert out["CHT"] == [{"SEQ": "A", "TOTAL": Decimal("150.50"), "PAID": Decimal("10.00")}]
    assert [(r["CHRGITEM"], r["AMOUNT"]) for r in out["CHA"]] == [("31", Decimal("80")), ("41", Decimal("30.50"))]
    adp = {r["CODE"]: r for r in out["ADP"]}
    assert len(out["ADP"]) == 2 and adp["WALKIN"]["QTY"] == 1
    assert adp["55021"]["QTY"] == 3 and adp["55021"]["TOTAL"] == Decimal("300")
    assert [(r["DID"], r["AMOUNT"], r["TOTAL"]) for r in out["DRU"]] == [("D1", 15, Decimal("30")), ("D2", 1, Decimal("3"))]
    assert out["AER"] == [{"SEQ": "A", "UCAE": ""}]  # only the later visit had one: it moves to the first VN
    assert out["IPD"] is data["IPD"]
    assert data["CHT"][0]["TOTAL"] == Decimal("100.00") and data["ODX"][1]["SEQ"] == "B"  # input not changed
    assert nhso18.merge_walkin(data, {}) is data


def test_status_uses_imported_rep_and_stm():
    from app import nhso18_service as svc
    assert svc.status({}, False, "", {"rep_stat": "A", "rep_no": "691100023"}) == "REP ผ่าน (A)"
    assert svc.status({}, False, "", {"rep_stat": "C"}) == "REP ไม่ผ่าน (C)"
    assert svc.status({}, False, "", {"rep_stat": "A", "stm_no": "STM1"}) == "ชดเชยแล้ว"
    assert svc.status({"rep_rep": "x"}, False, "", {"rep_stat": "C"}) == "ส่งแล้ว"
    assert svc.status({}, True, "", None) == "ส่งออกแล้ว"


def test_set_uuc_uses_the_hand_choice():
    from app.nhso18 import Visit, set_uuc
    visits = [Visit(vn="A", uuc="2"), Visit(vn="B", uuc="1"), Visit(vn="C", uuc="2")]
    set_uuc(visits, {"A": "1", "B": "2", "C": ""})
    assert [v.uuc for v in visits] == ["1", "2", "2"]


def test_opd_uuc_is_1_unless_hosxp_marks_not_claimed():
    from datetime import date
    from app.nhso18 import PttypeGroups, opd_contexts
    base = {"hn": "1", "vstdate": date(2026, 11, 2), "an": None, "maininscl": "UCS", "pttype_eclaim": "01", "hospmain": "10866"}
    rows = [{**base, "vn": "A", "uuc_flag": ""}, {**base, "vn": "B", "uuc_flag": "2"}]
    select = lambda sql: rows if "uuc_flag" in sql else []
    assert [v.uuc for v in opd_contexts(select, ["A", "B"], PttypeGroups(), "09")] == ["1", "2"]


def test_not_claimed_reads_request_funds_n():
    from app.nhso18 import not_claimed
    seen = []
    select = lambda sql: seen.append(sql) or [{"vn": "B"}]
    assert not_claimed(select, ["A", "B"]) == {"B"} and "request_funds='N'" in seen[0]
    assert not_claimed(select, []) == set() and len(seen) == 1


def test_paid_amount_prefers_stm_then_fdh():
    from app import nhso18_service as svc
    assert svc.paid_amount({"fdh_amount": 900}, "ชดเชยแล้ว", {"stm_paid": "1200.5"}) == "1200.50"
    assert svc.paid_amount({"fdh_amount": 900}, "ชดเชยแล้ว", {}) == "900.00"
    assert svc.paid_amount({}, "ชดเชยแล้ว", None) == ""
    assert svc.paid_amount({"fdh_amount": 900}, "ส่งแล้ว", {"stm_paid": "1200"}) == ""
