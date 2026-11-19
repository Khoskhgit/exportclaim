from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.ppfs_instrument import CsvError, WebStore, adp_codes, check, instrument_icodes, parse_csv
from app.store import Store
from factories import settings
from test_api import FakeHosxp

CID = "1" * 13
HEAD = ("Zone,ZoneName,ProvinceCode,ProvinceName,HospCode,HospName,VisitNo,VstDate,CID,Fname,Lname,HN,AN,Target,"
        "DisabillityType,ADL,MainInscl,SubInscl,ServiceType,Code,Name,BrandCode,BrandName,ICD10,ICD9,Medium_Price,"
        "Service_Charge,Real_Price,Diff,Amount,Request_Price,Income,RegDate,Staff,SerialNo,Status")


def line(code="8902", day="05/03/2569", cid=CID, hn="000001", income="4000", hosp="11111"):
    return (f"06,เขต 6,2700,สระแก้ว,{hosp},รพ.ทดสอบ,1,{day},{cid},ชื่อ,สกุล,{hn},,คนพิการ,ทางกาย,,UCS,74,OP,{code},"
            f"ขาเทียม,,,Z89,,4400,0,4400,0,1,4400,{income},06/03/2569,เจ้าหน้าที่,SN1,อนุมัติ")


def item(vn="v1", day="2026-03-05", code="8902", qty=1, amount=4400, pttype_name="บัตรทอง", authen="EP1", paidst="02"):
    return {"vn": vn, "hn": "000001", "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": None, "pttype": "72",
            "pttype_name": pttype_name, "cid": CID, "ptname": "ผู้ป่วย ทดสอบ", "authen": authen,
            "icode": "3000001", "paidst": paidst, "code": code, "item_name": "ขาเทียม", "qty": qty, "amount": amount}


def adp(code, type_id=2, ucs=4400, start=None, expire="0000-00-00", active=None):
    return {"code": code, "type_id": type_id, "name": f"(2-Instrument)-{code}", "ucs": ucs, "start_date": start,
            "expire_date": expire, "active_status": active}


ADP = {c: adp(c, ucs=u) for c, u in (("8902", 4400), ("8705", 250), ("8101", 0))}


def data(items=(), web=(), web_visits=(), equipment=None, eclaim=None, adp_rows=None):
    return {"items": list(items), "web": list(web), "web_visits": list(web_visits),
            "equipment": equipment or {}, "eclaim": eclaim or {}, "adp": ADP if adp_rows is None else adp_rows}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def web_row(code="8902", income="4000.00"):
    return {"cid": CID, "hn": "", "vstdate": "2026-03-05", "code": code, "name": "ขาเทียม", "amount": "1.00",
            "request_price": "4400.00", "income": income, "reg_date": "2026-03-06", "status": "อนุมัติ", "serial_no": "SN1"}


def test_every_adp_type_2_item_is_an_instrument():
    seen = []
    q = lambda sql: seen.append(sql) or [{"icode": "3003848"}]
    assert instrument_icodes(q, ["3003848", "1500087", ""]) == {"3003848"}
    assert "nhso_adp_type_id=2" in seen[0] and "REGEXP" not in seen[0]
    assert instrument_icodes(q, []) == set() and len(seen) == 1


def test_a_registered_device_in_the_list():
    (row,) = check(data([item()]))
    assert (row.key, row.count, row.amount, row.extra["adp_found"], row.extra["adp_ucs"]) == (
        "v1|8902", 1, Decimal("4400.00"), True, "4400.00")
    assert codes(row) == ([], [])


def test_warnings_do_not_block():
    (row,) = check(data([item(code="8705", amount=250, authen="")], eclaim={"v1": {"rep": "OPUCS1", "paid": "250"}}))
    assert codes(row) == ([], ["NO_AUTHEN", "ECLAIM"])


def test_the_code_is_checked_against_nhso_adp_code():
    (row,) = check(data([item()], adp_rows={}))
    assert codes(row)[0] == ["NOT_IN_ADP"] and not row.extra["adp_found"]
    (row,) = check(data([item()], adp_rows={"8902": adp("8902", type_id=19)}))
    assert codes(row)[0] == ["ADP_TYPE"]
    (row,) = check(data([item()], adp_rows={"8902": adp("8902", active="N")}))
    assert codes(row)[0] == ["ADP_CLOSED"]
    (row,) = check(data([item()], adp_rows={"8902": adp("8902", expire=date(2026, 3, 1))}))
    assert codes(row)[0] == ["ADP_DATE"]
    (row,) = check(data([item()], adp_rows={"8902": adp("8902", start="2026-04-01")}))
    assert codes(row)[0] == ["ADP_DATE"]


def test_a_unit_price_over_the_ucs_price_is_a_warning():
    (row,) = check(data([item(qty=2, amount=9000)]))
    assert codes(row) == ([], ["OVER_UCS"])
    (row,) = check(data([item(qty=2, amount=8800)]))
    assert codes(row) == ([], [])
    (row,) = check(data([item(code="8101", amount=99999)]))  # no UCS price in the table: nothing to compare
    assert codes(row) == ([], [])


def test_project_code_is_saved_per_visit(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    assert "options" in client.get("/api/ppfs/instrument/project").json()
    assert client.post("/api/ppfs/instrument/project", json={"vn": "v1", "code": " DISABILITY "}).json()["code"] == "DISABILITY"
    assert client.post("/api/ppfs/instrument/project", json={"vn": "v1", "code": "ผิด;"}).status_code == 400
    assert client.post("/api/ppfs/instrument/project", json={"code": "X"}).status_code == 400


def test_project_store(tmp_path):
    from app.ppfs_routes import PpfsStore
    store = PpfsStore(Store(tmp_path / "s.sqlite3"))
    store.set_project("instrument", "v1", "DISABILITY")
    store.set_project("instrument", "v2", "OTHER")
    store.set_project("instrument", "v2", "")
    assert store.projects("instrument", ["v1", "v2"]) == {"v1": "DISABILITY"}
    assert store.projects("cervix", ["v1"]) == {}


def test_disability_right_and_registry_are_not_used():
    (row,) = check(data([item()]))
    assert codes(row) == ([], []) and "deformed" not in row.extra and "disabled_right" not in row.extra
    (row,) = check(data([item(pttype_name="ผู้พิการ ในเขต")]))
    assert codes(row) == ([], [])


def test_web_record_and_income():
    (row,) = check(data([item(amount=4400)], web=[web_row(income="4000.00")]))
    assert row.extra["web"] and row.extra["income"] == "4000.00"
    assert {"IN_WEB", "OVER_INCOME"} <= set(codes(row)[1])


def test_equipment_service_counts_as_web():
    (row,) = check(data([item()], equipment={("v1", "8902"): {"regdate": "2026-03-06", "status": "A"}}))
    assert row.extra["web"] and "IN_WEB" in codes(row)[1] and "OVER_INCOME" not in codes(row)[1]


def test_web_only_row_needs_the_item_in_hosxp():
    visit = {k: v for k, v in item().items() if k not in ("icode", "code", "qty", "amount", "paidst", "item_name")}
    (row,) = check(data(web=[web_row()], web_visits=[visit]))
    assert row.key == "v1|8902" and "NO_ITEM" in codes(row)[0]


def test_self_paid_is_an_error():
    (row,) = check(data([item(paidst="03")]))
    assert "SELF_PAID" in codes(row)[0]


def test_csv_with_header_in_tis620():
    rows = parse_csv(f"{HEAD}\r\n{line()}\r\n".encode("cp874"))
    (r,) = rows
    assert (r.cid, r.vstdate, r.code, r.income, r.request_price, r.reg_date, r.serial_no) == (
        CID, "2026-03-05", "8902", Decimal("4000.00"), Decimal("4400.00"), "2026-03-06", "SN1")
    assert not hasattr(r, "fname") and "ชื่อ" not in repr(r)


def test_csv_without_header_uses_the_old_column_order():
    (r,) = parse_csv(line(code="8101").encode("utf-8-sig"))
    assert (r.code, r.vstdate) == ("8101", "2026-03-05")


def test_csv_that_is_not_the_export_is_refused():
    for data_ in (b"", "a,b\r\n1,2\r\n".encode()):
        try:
            parse_csv(data_)
        except CsvError:
            continue
        raise AssertionError("should be refused")


def test_web_store_replaces_on_reimport(tmp_path):
    store = WebStore(Store(tmp_path / "s.sqlite3"))
    rows = parse_csv(f"{HEAD}\n{line()}\n".encode())
    assert store.save(rows, "a.csv") == {"rows": 1, "added": 1, "updated": 0}
    assert store.save(parse_csv(f"{HEAD}\n{line(income='3500')}\n".encode()), "b.csv")["updated"] == 1
    (saved,) = store.between(date(2026, 3, 1), date(2026, 3, 31))
    assert saved["income"] == "3500.00" and saved["file"] == "b.csv"
    assert store.summary()["n"] == 1


def test_import_route_checks_the_hospital(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    ok = client.post("/api/ppfs/instrument/web?name=Export-Equipment.CSV", content=f"{HEAD}\n{line()}\n".encode())
    assert ok.status_code == 200 and ok.json()["summary"]["n"] == 1
    other = client.post("/api/ppfs/instrument/web?name=x.csv", content=f"{HEAD}\n{line(hosp='99999')}\n".encode())
    assert other.status_code == 400
    assert client.get("/api/ppfs/instrument/web").json()["n"] == 1
    assert any(s["key"] == "instrument" for s in client.get("/api/ppfs/services").json())


def test_a_code_under_several_types_uses_its_type_2_row():
    rows = [adp("3001", type_id=2, ucs=130), adp("3001", type_id=19, ucs=130), adp("4511", type_id=2, ucs=2850)]
    for order in (rows, rows[::-1]):
        found = adp_codes(lambda sql: order, ["3001", "4511"])
        assert (found["3001"]["type_id"], found["4511"]["type_id"]) == (2, 2)
