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
