import io
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import repstm, repstm_store
from app.main import create_app
from app.store import Store
from test_api import OK, SENT, FakeHosxp, by_vn
from factories import settings

PID = "1" * 13
BIL = f"""เอกสารตอบรับ ข้อมูลเบิกค่ารักษาพยาบาลผู้ป่วยนอกสิทธิประกันสังคม
รหัส ร.พ.\t\t\t= 10866
งวดส่งของ ร.พ.\t\t= 0017_09_20261109-214410
วันที่ออกเลขตอบรับ \t\t= 09/11/2569   เวลา: 21:44
เลขที่ตอบรับ\t\t= 9170006
สถานี: 09
รายการที่ส่งไป\t= 2
รายการที่ตรวจผ่าน\t= 1
รายการที่ตรวจไม่ผ่าน\t= 1
*| A 09, 1, 10866, 10699, , 16/10/2569 19:02, {SENT}, {PID}, S, 795.00, 795.00 | W07,
*| C 09, 2, 10866, 10699, , 15/10/2569 20:56, {OK}, {PID}, S, 443.00, 443.00 | R61,T61,W05,
รหัส
R61 : เป็นรายการลบหรือแก้ไขแต่ไม่พบเลขที่ใบสั่งยาเดิม
T61 : เป็นรายการลบหรือแก้ไขที่ไม่พบ Inv No. เดิม
W05 : ราคายาไม่ตรงกับ Drugcatalog
""".replace("\n", "\r\n").encode("tis-620")
DIS = f"""รายละเอียดเพิ่มเติมการตอบรับ ข้อมูลยา (BILLDISP)
เลขที่ตอบรับ \t= D9170006
*|2|R61,---|{OK}|{OK}|{PID}|1|42.00|42.00|0.00|0.00
=|W05,---|1500016|1|LIDOCAINE 2 %|1|42.00|42.00|42.00|565335|OD
""".encode("tis-620")
STM = f"""<?xml version = "1.0" encoding="UTF-8" standalone="yes"?>
<STMSTMP><stmAccountID>SOGN</stmAccountID><hcode id = "EA0010866">10866</hcode><hname>x</hname>
<AccPeriod desc = "ตุลาคม 2569">202610</AccPeriod><STMdoc desc = "">10866_SOGNSTMP_202610</STMdoc>
<dateStart>1  ตุลาคม 2569</dateStart><dateEnd>31  ตุลาคม 2569</dateEnd><dateDue>19  พฤศจิกายน  2569</dateDue>
<dateIssue>8  พฤศจิกายน  2569</dateIssue><acount>1</acount><amount>795.0000</amount><gst>1</gst >
<TBills code ='OP'><ST name = "01"><HG hc = "10866"><TBill><sys id = "1">OP</sys><station>09</station><hmain>10699</hmain>
<hproc>10866</hproc><hcare>10866</hcare><hn>000000001</hn><pid>{PID}</pid><name>ผู้ป่วย ทดสอบ</name><invno>{SENT}</invno>
<bf>Y</bf><pcode>0</pcode><care>M</care><payplan>80</payplan><bp>S</bp><dttran>2026-10-16T19:02:12</dttran><copay/><cfh/>
<total>795.0000</total><ExtP/><rid>9170006</rid><cstat/></TBill></HG></ST></TBills></STMSTMP>""".encode("utf-8")


def zipped(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


REP_ZIP = zipped({"10866_SOCDBIL_9170006.BIL": BIL, "10866_SOCDDIS_9170006.BIL": DIS})
STM_ZIP = zipped({"10866_SOGNSTMP_202610.xml": STM, "10866_SOGNSUMP_202610.xml": b"<STMSUMP/>",
                  "10866_SOGNSTMP_202610.htm": b"x", "SOGNSTMP67.xsl": b"x"})


def test_rep_zip_is_read():
    parsed = repstm.parse_upload("10866_SOCDBIL_9170006.ZIP", REP_ZIP)
    rep = parsed.reps["9170006"]
    assert (rep.session, rep.station, rep.issued_at, rep.sent, rep.passed, rep.failed) == (
        "0017_09_20261109-214410", "09", "2026-11-09T21:44", 2, 1, 1)
    failed = rep.bills[1]
    assert (failed.stat, failed.invno, failed.dttran, failed.claimamt, failed.codes) == (
        "C", OK, "2026-10-15T20:56", "443.00", "R61,T61,W05")
    assert rep.disps[0].codes == "R61" and rep.disps[0].drugs[0].drgid == "565335"
    assert rep.code_names["T61"].startswith("เป็นรายการลบ")


def test_stm_zip_is_read_and_copies_are_ignored():
    parsed = repstm.parse_upload("10866_SOGNSTM_202610.ZIP", STM_ZIP)
    stm = parsed.stms["10866_SOGNSTMP_202610"]
    assert (stm.period, stm.date_issue, stm.date_due, stm.count, stm.amount) == ("202610", "2026-11-08", "2026-11-19", 1, "795.00")
    assert stm.bills[0].invno == SENT and stm.bills[0].rid == "9170006" and parsed.skipped == []
    assert not hasattr(stm.bills[0], "pid") and not hasattr(stm.bills[0], "name")


def test_unknown_file_is_refused():
    with pytest.raises(repstm.RepStmError):
        repstm.parse_upload("other.zip", zipped({"readme.txt": b"x"}))


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(replace(settings(), export_dir=tmp_path / "exports"), FakeHosxp(), Store(tmp_path / "s.sqlite3")))


def upload(client, name, data):
    return client.post(f"/api/repstm/import?name={name}", content=data)


def test_import_drives_ssop_status(client, tmp_path):
    assert upload(client, "10866_SOCDBIL_9170006.ZIP", REP_ZIP).status_code == 200
    rows = by_vn(client)
    assert rows[OK]["status"] == "ติด C" and rows[OK]["resend_flag"] == "A"
    assert rows[OK]["rep_file"] == "10866_SOCDBIL_9170006" and rows[OK]["stm_paid"] == ""
    assert [c["code"] for c in rows[OK]["result_codes"]][:2] == ["R61", "T61"]
    assert rows[OK]["result_codes"][0]["name"].startswith("เป็นรายการลบ")

    assert upload(client, "10866_SOGNSTM_202610.ZIP", STM_ZIP).status_code == 200
    rows = by_vn(client)
    assert rows[SENT]["status"] == "ชดเชยแล้ว" and rows[SENT]["stm_paid"] == "795.00"
    assert rows[SENT]["stm_no"] == "10866_SOGNSTMP_202610" and rows[SENT]["rep_file"] == "10866_SOCDBIL_9170006"

    again = upload(client, "10866_SOCDBIL_9170006.ZIP", REP_ZIP).json()
    assert again["again"]
    listing = client.get("/api/repstm/list").json()
    assert len(listing["reps"]) == 1 and listing["reps"][0]["failed"] == 1 and len(listing["uploads"]) == 3
    detail = client.post("/api/repstm/rep", json={"rep_no": "9170006"}).json()
    assert len(detail["bills"]) == 2 and detail["disps"][0]["drugs"][0]["name"] == "LIDOCAINE 2 %"
    history = client.post("/api/repstm/vn", json={"vn": SENT}).json()
    assert history["reps"][0]["stat"] == "A" and history["stms"][0]["total"] == "795.00"
    assert list((tmp_path / "rep_stm").glob("*_10866_SOCDBIL_9170006.ZIP"))


def test_bad_upload_is_refused(client):
    response = upload(client, "x.zip", b"not a zip")
    assert response.status_code == 400 and "ZIP" in response.json()["detail"]


def bil(rep_no, issued, stat, codes):
    text = f"""รหัส ร.พ.\t= 10866
งวดส่งของ ร.พ.\t= 0001_09_20261109-100000
วันที่ออกเลขตอบรับ \t= {issued}   เวลา: 10:00
สถานี: 09
*| {stat} 09, 1, 10866, 10699, , 15/10/2569 20:56, {OK}, {PID}, S, 443.00, 443.00 | {codes}
"""
    return zipped({f"10866_SOCDBIL_{rep_no}.BIL": text.encode("tis-620")})


def test_duplicate_refusal_after_acceptance_keeps_a(client):
    # Imported newest first, to show the order of import does not matter.
    upload(client, "10866_SOCDBIL_9170006.ZIP", bil("9170006", "09/11/2569", "C", "R01,S01,T02,W05"))
    upload(client, "10866_SOCDBIL_9170005.ZIP", bil("9170005", "08/11/2569", "A", "W05"))
    upload(client, "10866_SOCDBIL_9170004.ZIP", bil("9170004", "07/11/2569", "C", "R61,T61"))
    row = by_vn(client)[OK]
    assert row["status"] == "ส่งแล้ว" and row["rep_file"] == "10866_SOCDBIL_9170005"
    history = client.post("/api/repstm/vn", json={"vn": OK}).json()
    assert history["status_stat"] == "A" and [r["duplicate"] for r in history["reps"]] == [False, False, True]


def test_rejection_without_earlier_acceptance_stays_c(client):
    upload(client, "10866_SOCDBIL_9170006.ZIP", bil("9170006", "09/11/2569", "C", "R01,S01,T02"))
    assert by_vn(client)[OK]["status"] == "ติด C"


def test_seq_restarts_for_each_station(tmp_path):
    """The October 2026 STM numbers bills 1, 2, 3 … again under each station; the old key refused it."""
    import sqlite3
    path = tmp_path / "s.sqlite3"
    with sqlite3.connect(path) as db:  # a store made before the fix
        db.execute(repstm_store.SCHEMA.split("CREATE TABLE IF NOT EXISTS stm_bills")[1].split(";")[0]
                   .replace("PRIMARY KEY (stm_no, station, seq)", "PRIMARY KEY (stm_no, seq)")
                   .join(["CREATE TABLE stm_bills", ""]))
        db.execute("INSERT INTO stm_bills VALUES ('old', 1, '01', '', '', '', '', 'v', '', '', '', '', '', '', '', '', '1', '', '', '')")
    bill = STM.split(b"<TBill>")[1].split(b"</TBill>")[0]
    second = bill.replace(b"<station>09</station>", b"<station>01</station>").replace(SENT.encode(), b"690000000002")
    data = STM.replace(b"</TBill></HG>", b"</TBill><TBill>" + second + b"</TBill></HG>")
    client = TestClient(create_app(replace(settings(), export_dir=tmp_path / "exports"), FakeHosxp(), Store(path)))
    assert upload(client, "10866_SOGNSTMP_202610.xml", data).status_code == 200
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT stm_no, station, seq FROM stm_bills ORDER BY stm_no, station").fetchall()
    assert rows == [("10866_SOGNSTMP_202610", "01", 1), ("10866_SOGNSTMP_202610", "09", 1), ("old", "01", 1)]


def ofc_xls(rows):
    """A CSMBS STM .xls laid out like the NHSO one: titles, a block per REP NO with headers repeated."""
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("พึงรับ")
    lines = [["ออกรายงานวันที่ 08/11/2569 เวลา 14:57"], [], ["โรงพยาบาล 10866 รพ.คลองหาด"], [],
             ["เลขที่เอกสาร 10866_OP202610_02"]]
    for rep in dict.fromkeys(r[0] for r in rows):
        lines += [[], [f"REP NO : {rep}"], [],
                  ["REP", "ลำดับที่", "HN", "AN", "PID", "ชื่อ - สกุล", "วันเข้ารักษา", "วันจำหน่าย", "PROJCODE", "AdjRW",
                   "เรียกเก็บ", "พรบ.", "พึงรับ"] + [""] * 6 + ["พึงรับทั้งหมด", "SEQ NO"]]
        lines += [[float(rep), str(seq), hn, "-", PID, "ชื่อ", when, "", "", "0.0000", amount, 0.0] + [0.0] * 7
                  + [amount, vn] for of, seq, hn, when, amount, vn in rows if of == rep]
    lines += [[], ["", "หมายเหตุ", "PROJCODE : "]]
    for r, line in enumerate(lines):
        for c, value in enumerate(line):
            sheet.write(r, c, value)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_ofc_stm_xls_is_read():
    data = ofc_xls([("691000047", 1, "000004747", "17/10 /2026 15:23:00", 181.5, "691017152316"),
                    ("691000047", 2, "000057092", "16/10 /2026 11:30:00", 187.0, "691016113002"),
                    ("691000048", 3, "000010091", "10/10 /2026 23:50:49", 140.0, "691010235049")])
    parsed = repstm.parse_upload("STM_10866_OP202610_02 - Copy (2).xls", data)
    stm = parsed.stms["10866_OP202610_02"]
    assert (stm.account, stm.period, stm.date_issue, stm.count, stm.amount) == ("OFC", "202610", "2026-11-08", 3, "508.50")
    assert (stm.date_start, stm.date_end) == ("2026-10-10", "2026-10-17")
    first = stm.bills[0]
    assert (first.invno, first.hn, first.rid, first.total, first.dttran) == (
        "691017152316", "000004747", "691000047", "181.50", "2026-10-17T15:23:00")
    assert stm.bills[2].rid == "691000048" and PID not in str(stm)


def test_ofc_stm_import_shows_in_vn_history(client):
    data = ofc_xls([("691000048", 1, "000010091", "10/10 /2026 23:50:49", 140.0, "691010235049")])
    response = client.post("/api/repstm/import?name=STM_10866_OP202610_02.xls", content=data)
    assert response.status_code == 200, response.text
    history = client.post("/api/repstm/vn", json={"vn": "691010235049"}).json()
    assert history["stms"][0]["stm_no"] == "10866_OP202610_02" and history["stms"][0]["total"] == "140.00"


def ofc_rep_xls(rows):
    """A CSMBS e-Claim REP .xls: a Detail sheet with titles, two header lines and one row per visit."""
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("Detail")
    sheet.write(0, 0, "ออกรายงานวันที่ 11/11/2569 เวลา 09:48")
    sheet.write(1, 15, "เลขที่เอกสาร eclaim_10866_OPCS_25691010_111902014")
    sheet.write(3, 15, "โรงพยาบาล 10866 รพ.คลองหาด")
    head = {0: "REP", 1: "ลำดับที่", 2: "TRAN_ID", 3: "HN", 4: "AN", 5: "PID", 8: "วันเข้ารักษา", 10: "ชดเชยสุทธิ",
            12: "Error Code", 29: "เรียกเก็บ", 56: "SEQ NO"}
    for col, label in head.items():
        sheet.write(5, col, label)
    sheet.write(6, 10, "ค่ารักษา")
    for r, (rep, line, when, paid, code, claimed, vn, *an) in enumerate(rows, start=7):
        for col, value in {0: rep, 1: float(line), 2: "829091203", 3: "000098590", 4: an[0] if an else "", 5: PID,
                           8: when, 10: paid, 12: code, 29: claimed, 56: vn}.items():
            sheet.write(r, col, value)
    book.add_sheet("Summary")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_ofc_rep_xls_is_read_and_listed(client):
    data = ofc_rep_xls([("691000025", 1, "08/10/2026 12:02:00", 65.0, "-", 65.0, "691008120216"),
                        ("691000025", 14, "07/10/2026 07:55:00", 0.0, "565", 7057.75, "691007075513")])
    rep = repstm.parse_upload("eclaim_10866_OPCS_25691010_111902014.xls", data).reps["691000025"]
    assert (rep.account, rep.hcode, rep.issued_at, rep.sent, rep.passed, rep.failed) == ("OFC", "10866", "2026-11-11T09:48", 2, 1, 1)
    assert rep.session == "eclaim_10866_OPCS_25691010_111902014" and PID not in str(rep)
    failed = rep.bills[1]
    assert (failed.stat, failed.codes, failed.invno, failed.claimamt, failed.dttran) == (
        "C", "565", "691007075513", "7057.75", "2026-10-07T07:55")
    assert rep.bills[0].stat == "A" and rep.bills[0].codes == "" and rep.bills[0].paid == "65.00"

    response = client.post("/api/repstm/import?name=eclaim_10866_OPCS_25691010_111902014.xls", content=data)
    assert response.status_code == 200, response.text
    listed = client.get("/api/repstm/list").json()["reps"]
    assert listed[0]["rep_no"] == "691000025" and listed[0]["failed"] == 1
    history = client.post("/api/repstm/vn", json={"vn": "691007075513"}).json()
    assert history["status_stat"] == "C" and history["reps"][0]["codes"] == "565"


def test_uc_rep_payout_adds_the_nhso_and_agency_columns(tmp_path):
    xlrd = pytest.importorskip("xlrd")
    data = ofc_rep_xls([("691100045", 1, "02/11/2026 07:03:00", 350.0, "DENY", 825.0, "691102070338")])
    book = xlrd.open_workbook(file_contents=data, formatting_info=False)
    sheet = book.sheet_by_name("Detail")
    rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    rows[6][11], rows[7][11] = "ต้นสังกัด", 5.0  # ชดเชยสุทธิ: สปสช. 350 + ต้นสังกัด 5
    xlwt = pytest.importorskip("xlwt")
    out = xlwt.Workbook(encoding="utf-8")
    detail = out.add_sheet("Detail")
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            if value != "":
                detail.write(r, c, value)
    out.add_sheet("Summary")
    buffer = io.BytesIO()
    out.save(buffer)
    rep = repstm.parse_upload("eclaim_10866_OP_25691116_153533322.xls", buffer.getvalue()).reps["691100045"]
    assert (rep.bills[0].stat, rep.bills[0].codes, rep.bills[0].paid) == ("C", "DENY", "355.00")
    store = repstm_store.RepStmStore(Store(tmp_path / "s.sqlite3"))
    store.save(repstm.parse_upload("eclaim_10866_OP_25691116_153533322.xls", buffer.getvalue()), "x.xls", "h", "", "")
    found = store.latest(["691102070338"])["691102070338"]
    assert (found["rep_paid"], found["rep_claim"], found["rep_codes"]) == ("355.00", "825.00", "DENY")


def test_old_rep_bills_table_gets_the_paid_column(tmp_path):
    import sqlite3
    path = tmp_path / "s.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE rep_bills (rep_no TEXT NOT NULL, line INTEGER NOT NULL, stat TEXT NOT NULL, station TEXT NOT NULL,
          hcode TEXT NOT NULL, hmain TEXT NOT NULL, authcode TEXT NOT NULL, dttran TEXT NOT NULL, invno TEXT NOT NULL, bp TEXT NOT NULL,
          amount TEXT NOT NULL, claimamt TEXT NOT NULL, codes TEXT NOT NULL, PRIMARY KEY (rep_no, line))""")
        db.execute("INSERT INTO rep_bills VALUES ('1',1,'A','','','','','','v1','','10','10','')")
    store = repstm_store.RepStmStore(Store(path))
    assert store.rep_detail("1") is None  # no rep_files row, but the table was migrated without losing rows
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT paid FROM rep_bills WHERE invno='v1'").fetchone() == ("",)


def test_ofc_ipcs_rep_uses_the_an():
    data = ofc_rep_xls([("690900008", 1, "14/07/2026 14:29:00", 3980.47, "-", 5118.0, "", "690001401")])
    rep = repstm.parse_upload("eclaim_10866_IPCS_25690904_084523325.xls", data).reps["690900008"]
    assert [(b.invno, b.stat, b.claimamt) for b in rep.bills] == [("690001401", "A", "5118.00")]


def test_eclaim_rep_account_from_the_document_name():
    from app.repstm import rep_account
    assert rep_account("eclaim_10866_OPCS_25691101_1") == "OFC" and rep_account("eclaim_10866_IPCS_25691101_1") == "OFC"
    assert rep_account("eclaim_10866_OP_25691116_153533322") == "UCS" and rep_account("eclaim_10866_IP_25691116_1") == "UCS"
    assert rep_account("eclaim_10866_OPLGO_25691116_1") == "LGO" and rep_account("") == "OFC"


AIPN_REP = """\t\t          เอกสารตอบรับข้อมูลผู้ป่วยใน ประกันสังคม
รหัส ร.พ.    = 11111
เลขตอบรับที่   = 10117
วันที่ออกเลขตอบรับ\t=  9/10/2569     เวลา : 13:43:18
งวดที่ส่งไป\t=  1\t\tเลขงวดที่ส่ง : 10124
รายการที่ส่งไป\t= 3 ราย
รายการที่ตรวจผ่าน\t= 1 ราย
รายการที่ตรวจไม่ผ่าน\t= 2 ราย
รูปแบบรายการ *| pcode tcode iptype CareAs, SS, HMain, HCare, AN, DRG, rw, adjrw, ST, SST, PT, Amt, name [:err[:dat]]
*| 0 A S B Y, 10699, 11111, 690001807, 04550,  0.6005,   0.6005, IPR,,,, นาย ทดสอบ: 837
*| - C - B -, 10699, 11111, 690001434,,,,,,,, นาย สอง, สาม: 644,666
*| - C - M -, 11111, 11111, 690001339,,,,,,,, นาง สี่: 432:11111,644
--432: รหัสสถานพยาบาลหลัก(Hmain) ไม่มี/ไม่ถูกต้อง/ไม่อยู่ในทะเบียน
--644: ในข้อมูล BillItems: เบิกค่าใช้จ่ายหมวด 03,04,06,07 แต่ไม่แจ้ง STDCode
"""
AIPN_SUP = """*| AN , HN , Name (PID),  chkstat ([checkcode[,checkcode[,...]]])---
        =|[checkcode[,checkcode[,...]]]--- | sequence,billGrCs,LCCode ,CSCode , STDCode,Descript  , QTY, UnitPrice ,ChargeAmt , ClaimUP , ClaimAmount
*|690001434     ,000009408,     นาย สอง  (1111111111111 ) , C (644,666,)
          =| 666, |15 , 04 ,1690021         ,         ,           ,gabapentin 300 mg.   ,   30.00 ,    3.00 ,   90.00 ,    3.00 ,   90.00
          #| 251, |1 ,ICD9CM     ,9396         ,Respiratory therapy,Other
"""
AIPN_STM = """<?xml version="1.0" encoding="UTF-8"?><STMLIST><stmAccountID>SIGN</stmAccountID><stmdat><hcode>11111</hcode>
<period>6910</period><stmno>11111ST6910</stmno><dateDue>2  พฤศจิกายน  2569</dateDue><cases>1</cases></stmdat>
<Bills><Bill><hcode>11111</hcode><hmain>10699</hmain><hproc>11111</hproc><hn>000018066</hn><an>690001807</an>
<pid>1111111111111</pid><name>นาย ทดสอบ</name><dateadm>2026-09-09</dateadm><datedsc>2026-09-11</datedsc><ft>S</ft><bf>Y</bf>
<drg>04550</drg><rw>0.6005</rw><adjrw>0.6005</adjrw><ptype>2</ptype><rid>10117</rid><careas>B</careas>
<Reimb>4649.50</Reimb><Nreimb>0.00</Nreimb><Copay>0.00</Copay><PP>Q</PP></Bill></Bills></STMLIST>"""


def aipn_zips():
    rep, stm = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(rep, "w") as z:
        z.writestr("11111_SIGNREP_10117.REP", AIPN_REP.encode("cp874"))
        z.writestr("11111_SIGNSUP_10117.REP", AIPN_SUP.encode("cp874"))
    with zipfile.ZipFile(stm, "w") as z:
        z.writestr("11111_SIGNSTMS_6910.xml", AIPN_STM.encode())
        z.writestr("11111_SIGNSUMS_6910.xml", b"<STMSUMS/>")
        z.writestr("ssSTMS2025.xsl", b"")
    return rep.getvalue(), stm.getvalue()


def test_aipn_rep_and_sup_are_read_by_an_without_names():
    parsed = repstm.parse_upload("11111_SIGNREP_10117.ZIP", aipn_zips()[0])
    rep = parsed.reps["10117"]
    assert (rep.account, rep.session, rep.issued_at, rep.sent, rep.passed, rep.failed) == ("SIGN", "10124", "2026-10-09T13:43", 3, 1, 2)
    assert [(b.invno, b.stat, b.authcode, b.amount, b.bp, b.codes) for b in rep.bills] == [
        ("690001807", "A", "04550", "0.6005", "B", "837"), ("690001434", "C", "", "", "B", "644,666"),
        ("690001339", "C", "", "", "M", "432,644")]
    (disp,) = rep.disps
    assert (disp.invno, disp.codes, disp.charge) == ("690001434", "644,666", "90.00")
    assert [(g.prdcat, g.hospdrgid, g.codes) for g in disp.drugs] == [("04", "1690021", "666"), ("IPOp ICD9CM", "9396", "251")]
    assert rep.code_names["432"].startswith("รหัสสถานพยาบาลหลัก")
    assert "ทดสอบ" not in repr(rep) and "สอง" not in repr(rep) and "1111111111111" not in repr(rep)


def test_aipn_stm_is_read_and_shows_in_the_an_history(client):
    rep_zip, stm_zip = aipn_zips()
    stm = repstm.parse_upload("11111_SIGNSTM_6910.ZIP", stm_zip).stms["11111ST6910"]
    assert (stm.account, stm.period, stm.date_due, stm.count, stm.amount) == ("SIGN", "6910", "2026-11-02", 1, "4649.50")
    (bill,) = stm.bills
    assert (bill.invno, bill.payplan, bill.cfh, bill.total, bill.rid) == ("690001807", "04550", "0.6005", "4649.50", "10117")
    assert "ทดสอบ" not in repr(stm)
    for name, data in (("11111_SIGNREP_10117.ZIP", rep_zip), ("11111_SIGNSTM_6910.ZIP", stm_zip)):
        assert client.post(f"/api/repstm/import?name={name}", content=data).status_code == 200
    h = client.post("/api/repstm/vn", json={"vn": "690001807"}).json()
    assert h["status_stat"] == "A" and [s["total"] for s in h["stms"]] == ["4649.50"] and h["stms"][0]["account"] == "SIGN"
    failed = client.post("/api/repstm/vn", json={"vn": "690001434"}).json()
    assert failed["status_stat"] == "C" and failed["disps"][0]["account"] == "SIGN"


def test_aipn_rows_take_the_imported_result():
    from app.aipn_routes import with_result
    base = lambda: {"status": "ส่งออกแล้ว", "selectable": True, "already_sent": True, "default_selected": False}
    assert with_result(base(), None)["status"] == "ส่งออกแล้ว"
    paid = with_result(base(), {"rep_stat": "A", "stm_no": "11111ST6910", "stm_paid": "4649.50"})
    assert (paid["status"], paid["stm_paid"], paid["already_sent"]) == ("ชดเชยแล้ว", "4649.50", True)
    failed = with_result(base(), {"rep_stat": "C", "rep_codes": "644,666", "rep_no": "10117"})
    assert (failed["status"], failed["rep_codes"], failed["already_sent"], failed["default_selected"]) == ("ติด C", "644,666", False, False)
    assert with_result(base(), {"rep_stat": "A"})["status"] == "ผ่าน (A)"


def test_eclaim_rep_codes_come_with_the_nhso_detail_and_fix(client):
    data = ofc_rep_xls([("691000025", 14, "07/10/2026 07:55:00", 0.0, "565", 7057.75, "691007075513")])
    assert client.post("/api/repstm/import?name=eclaim_10866_OPCS_25691010_111902014.xls", content=data).status_code == 200
    history = client.post("/api/repstm/vn", json={"vn": "691007075513"}).json()
    assert history["c_codes"]["565"]["found"] and history["c_codes"]["565"]["fix"]
    detail = client.post("/api/repstm/rep", json={"rep_no": "691000025"}).json()
    assert set(detail["c_codes"]) == {"565"}
    found = client.get("/api/repstm/c-codes", params={"q": "565"}).json()
    assert found["rows"][0]["code"] == "565" and found["total"] == 578


def test_aipn_codes_are_not_explained_by_the_eclaim_document(client):
    rep_zip, _ = aipn_zips()
    assert client.post("/api/repstm/import?name=11111_SIGNREP_10117.ZIP", content=rep_zip).status_code == 200
    failed = client.post("/api/repstm/vn", json={"vn": "690001434"}).json()
    assert failed["c_codes"] == {}
