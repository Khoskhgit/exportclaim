import hashlib
import re
import zipfile
from dataclasses import replace
from datetime import date, datetime

import pytest

from app import export as e
from factories import drug, item, settings, visit

NOW = datetime(2026, 11, 6, 8, 30, 15)
V = "691001000001"


def build(tmp_path, visits=None, items=None, dx=None, **kw):
    visits = [visit()] if visits is None else visits
    items = {V: [item(), drug()]} if items is None else items
    dx = {V: [{"code": "J00", "name": "Acute", "diagtype": "1", "codeset": "IT", "tm_code": None}]} if dx is None else dx
    config = kw.pop("config", settings())
    return e.build_export(visits, items, dx, config, kw.pop("session", "12"), kw.pop("flag", "A"),
                          date(2026, 10, 31), tmp_path, now=NOW, **kw)


def read(result, name):
    return (result.folder / name).read_bytes()


def rows(raw, section):
    text = raw.decode("cp874")
    return re.search(rf"<{section}>\r\n(.*?)</{section}>", text, re.S).group(1).splitlines()


def test_zip_name_and_contents(tmp_path):
    result = build(tmp_path)
    assert result.zip_path.name == "11111_SSOPBIL_0012_01_20261106-083015.ZIP"
    with zipfile.ZipFile(result.zip_path) as archive:
        assert sorted(archive.namelist()) == ["BillDisp20261031.TXT", "BillTran20261031.TXT", "OPServices20261031.TXT"]


def test_header_encoding_and_line_endings(tmp_path):
    raw = read(build(tmp_path), "BillTran20261031.TXT")
    assert raw.startswith(b'<?xml version="1.0" encoding="windows-874"?>\r\n')
    text = raw.decode("cp874")
    assert 'PayPlan="SS" Version="0.93" Prgs="RCM" TFlag="A"' in text
    assert "<HNAME>รพ.ทดสอบ</HNAME>" in text
    assert "<DATETIME>2026-11-06T08:30:15</DATETIME>" in text
    assert "<SESSNO>0012</SESSNO>" in text and "<RECCOUNT>1</RECCOUNT>" in text
    assert "\n" not in text.replace("\r\n", "")


@pytest.mark.parametrize("name", ["BillTran20261031.TXT", "BillDisp20261031.TXT", "OPServices20261031.TXT"])
def test_checksum_covers_everything_before_endnote(tmp_path, name):
    raw = read(build(tmp_path), name)
    body, note = raw.split(b'<?EndNote Checksum="')
    assert body.endswith(b"</ClaimRec>\r\n")
    assert note == hashlib.md5(body).hexdigest().encode() + b'"?>'


def test_bill_amounts_and_field_order(tmp_path):
    bill = rows(read(build(tmp_path), "BillTran20261031.TXT"), "BILLTRAN")[0].split("|")
    assert bill == [
        "01", "", "2026-10-01T09:00:00", "11111", V, "R/1", "HN1", "", "150.00", "0.00", "", "A",
        "1100000000011", "นายทดสอบ ผ่าน", "11111", "80", "150.00", "", "0.00",
    ]


def test_patient_paid_lowers_claim_and_zeroes_item_claim(tmp_path):
    items = {V: [item(paidst="01"), drug()]}
    result = build(tmp_path, items=items)
    raw = read(result, "BillTran20261031.TXT")
    bill = rows(raw, "BILLTRAN")[0].split("|")
    assert bill[8:10] == ["150.00", "100.00"] and bill[16] == "50.00"
    assert result.claim_total == 50
    lines = [r.split("|") for r in rows(raw, "BillItems")]
    paid_line = next(r for r in lines if r[3] == "3000001")
    assert paid_line[9:11] == ["0.00", "0.00"]


def test_bill_item_columns_and_order(tmp_path):
    lines = [r.split("|") for r in rows(read(build(tmp_path), "BillTran20261031.TXT"), "BillItems")]
    assert [r[2] for r in lines] == ["3", "C"]
    assert lines[0] == [V, "2026-10-01", "3", "1000001", "100000000001", "พาราเซตามอล 500 mg", "10", "5.00", "50.00", "5.00", "50.00", V, "OP1"]


def test_excluded_items_leave_the_totals(tmp_path):
    raw = read(build(tmp_path, excluded={(V, "1000001")}), "BillTran20261031.TXT")
    assert rows(raw, "BILLTRAN")[0].split("|")[8] == "100.00"
    assert [r.split("|")[3] for r in rows(raw, "BillItems")] == ["3000001"]


def test_hosxp_noexport_flag_is_respected(tmp_path):
    raw = read(build(tmp_path, items={V: [item(), drug(hosxp_noexport=1)]}), "BillTran20261031.TXT")
    assert rows(raw, "BILLTRAN")[0].split("|")[8] == "100.00"


def test_zero_price_item_stays_out_of_bill_items(tmp_path):
    raw = read(build(tmp_path, items={V: [item(), drug(sum_price=0, qty=0)]}), "BillTran20261031.TXT")
    assert len(rows(raw, "BillItems")) == 1


def test_disp_files(tmp_path):
    raw = read(build(tmp_path), "BillDisp20261031.TXT")
    head = rows(raw, "Dispensing")[0].split("|")
    assert head[0:3] == ["11111", V, V] and head[5] == "2026-10-01 09:05:00"
    assert head[7:13] == ["ว12345", "1", "50.00", "50.00", "0.00", "0.00"]
    assert head[13:] == ["HP", "SS", "1", V, ""]
    line = rows(raw, "DispensedItems")[0].split("|")
    assert line[:4] == [V, "1", "1000001", "100000000001"]
    assert line[6:10] == ["เม็ด", "0101", "รับประทาน", "10"]
    assert line[15:17] == ["EC", "OP1"]  # drugaccount '-' with no NED reason (ssop.txt:2364-2367)
    assert b"<RECCOUNT>1</RECCOUNT>" in raw


def test_disp_defaults_usage_when_missing(tmp_path):
    raw = read(build(tmp_path, items={V: [drug(usage_code=None, usage_text="")]}), "BillDisp20261031.TXT")
    assert rows(raw, "DispensedItems")[0].split("|")[7:9] == ["0201", "วิธีใช้ตามแพทย์สั่ง"]


def test_no_drugs_means_empty_disp_with_zero_count(tmp_path):
    raw = read(build(tmp_path, items={V: [item()]}), "BillDisp20261031.TXT")
    assert b"<RECCOUNT>0</RECCOUNT>" in raw and rows(raw, "Dispensing") == []


def test_service_rows_and_procedures(tmp_path):
    items = {V: [item(), item(icode="3000004", icd9cm="86.59"), item(icode="3000005", icd9cm="8659012")]}
    result = build(tmp_path, items=items)
    raw = read(result, "OPServices20261031.TXT")
    lines = [r.split("|") for r in rows(raw, "OPServices")]
    assert result.service_count == 3 and b"<RECCOUNT>3</RECCOUNT>" in raw
    assert lines[0][:3] == [V, V + "0001", "EC"]
    assert lines[0][6:13] == ["1", "01", "1", "1", "", "ว12345", "01"]
    assert lines[0][13:15] == ["2026-10-01T09:00:00", "2026-10-01T10:00:00"]
    assert lines[1][1:3] == [V + "0002", "OP"] and lines[1][15:18] == ["3000004", "IN", "8659"]
    assert lines[2][1] == V + "0003" and lines[2][16] == "TT"


def test_diagnosis_rows_all_and_pdx_only(tmp_path):
    dx = {V: [
        {"code": "J00", "name": "A", "diagtype": "1", "codeset": None, "tm_code": None},
        {"code": "I10", "name": "B", "diagtype": "2", "codeset": "IT", "tm_code": "I10"},
    ]}
    raw = read(build(tmp_path / "a", dx=dx), "OPServices20261031.TXT")
    assert [r.split("|") for r in rows(raw, "OPDx")] == [
        ["EC", V + "0001", "1", "IT", "J00", "A"], ["EC", V + "0001", "2", "TT", "I10", "B"],
    ]
    only = replace(settings(), pdx_only=True)
    raw = read(build(tmp_path / "b", dx=dx, config=only), "OPServices20261031.TXT")
    assert [r.split("|")[4] for r in rows(raw, "OPDx")] == ["J00"]


def test_debt_number_is_used_when_vn_is_not_invoice(tmp_path):
    config = replace(settings(), use_vn_as_invno=False)
    raw = read(build(tmp_path, config=config), "BillTran20261031.TXT")
    assert rows(raw, "BILLTRAN")[0].split("|")[4] == "DEBT"


def test_delimiters_in_data_cannot_break_columns(tmp_path):
    the_visit = visit(ptname='นาย<ทด|สอบ>&"x"')
    bill = rows(read(build(tmp_path, visits=[the_visit]), "BillTran20261031.TXT"), "BILLTRAN")[0].split("|")
    assert len(bill) == 19 and bill[13] == "นายน้อยกว่าทด สอบมากกว่า and x"


@pytest.mark.parametrize("value,expected", [
    ("ว12345", "ว12345"), ("-12345", "-12345"), ("สมชาย", "-99999"), ("", "ว99999"), (None, "ว99999"),
])
def test_doctor_license(value, expected):
    assert e.doctor_license(value, "ว99999") == expected


@pytest.mark.parametrize("code,expected", [("1", "1"), ("5", "4"), ("6", "4"), ("8", "5"), ("7", "9"), ("", "9")])
def test_type_out(code, expected):
    assert e.type_out(code) == expected


@pytest.mark.parametrize("session", ["", "0", "0000", "12345", "ab"])
def test_bad_session_is_rejected(tmp_path, session):
    with pytest.raises(e.ExportError):
        build(tmp_path, session=session)


def test_bad_flag_and_empty_selection_are_rejected(tmp_path):
    with pytest.raises(e.ExportError):
        build(tmp_path, flag="X")
    with pytest.raises(e.ExportError):
        build(tmp_path, visits=[])


def test_missing_settings_stop_the_export(tmp_path):
    with pytest.raises(e.ExportError, match="HCODE"):
        build(tmp_path, config=replace(settings(), hcode=""))


def test_text_outside_windows_874_is_rejected(tmp_path):
    with pytest.raises(e.ExportError):
        build(tmp_path, visits=[visit(ptname="名前")])


def test_same_second_export_reports_an_error_not_a_crash(tmp_path):
    build(tmp_path)
    with pytest.raises(e.ExportError):
        build(tmp_path)


@pytest.mark.parametrize("changes,expected", [
    ({"drugaccount": "ก"}, "OD"), ({"drugaccount": "-", "ned": "EA"}, "EA"), ({"cscd_group": "5"}, "OD"),
])
def test_claim_cont(changes, expected):
    assert e.claim_cont(drug(**changes)) == expected


def test_disp_head_uses_earliest_rx_time(tmp_path):
    from datetime import timedelta
    late, early = drug(icode="1000001", rxtime=timedelta(hours=11)), drug(icode="1000002", rxtime=timedelta(hours=8))
    raw = read(build(tmp_path, items={V: [late, early]}), "BillDisp20261031.TXT")
    head = rows(raw, "Dispensing")[0].split("|")
    assert head[5] == head[6] == "2026-10-01 08:00:00"


def test_excluded_item_leaves_every_file(tmp_path):
    kept = item(icode="3000001", sum_price=100)
    gone = item(icode="3001309", sum_price=250, unitprice=250, cscd_group="B", icd9cm="9354", hosxp_noexport=1)
    result = build(tmp_path, items={V: [kept, gone]})
    for name in ("BillTran20261031.TXT", "BillDisp20261031.TXT", "OPServices20261031.TXT"):
        assert b"3001309" not in read(result, name)
    assert result.claim_total == 100
    assert e.send_amounts([kept, gone], set()) == {"send_amount": "100.00", "claim_amount": "100.00", "excluded_count": 1}
