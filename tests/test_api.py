import re
import io
import zipfile
from dataclasses import replace

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.hosxp import HosxpError
from app.main import create_app, mask_id, mask_host
from app.store import Store
from factories import drug, item, settings, visit

OK, ZED, BAD, SLEEP, SENT = (f"69100100000{n}" for n in range(1, 6))


class FakeHosxp:
    def __init__(self):
        self.visits = [
            visit(vn=OK), visit(vn=ZED, pdx="Z999"), visit(vn=BAD, pdx=""), visit(vn=SLEEP), visit(vn=SENT, chi_status="A"),
        ]
        self.items = [item(vn=v["vn"]) for v in self.visits if v["vn"] != SLEEP]
        self.items.append(item(vn=SLEEP, nhso_adp_code="51120"))

    def result_code_names(self):
        return {}

    def preflight(self):
        return {"ok": True, "missing_tables": [], "missing_columns": [], "optional_missing": [], "functions": [], "user_can_write": False}

    def pttypes(self):
        return [{"pttype": "S1", "name": "ประกันสังคม"}]

    def z_codes(self):
        return {"Z000"}

    def search_visits(self, *args):
        return self.visits

    def visits_by_vn(self, vns):
        return [v for v in self.visits if v["vn"] in vns]

    def fetch_items(self, vns):
        return [dict(i) for i in self.items if i["vn"] in vns]

    def fetch_dx(self, vns):
        return [{"vn": vn, "code": "J00", "name": "A", "diagtype": "1", "codeset": "IT", "tm_code": None} for vn in vns]


@pytest.fixture
def client(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "exports")
    return TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))


def by_vn(client):
    return {v["vn"]: v for v in client.post("/api/search", json={"start": "2026-10-01", "end": "2026-10-31", "pttypes": ["S1"]}).json()["visits"]}


def export(client, vns, **kw):
    return client.post("/api/export", json={"vns": vns, "session": "1", "tflag": "A", "file_date": "2026-10-31", **kw})


def test_page_loads(client):
    assert "ส่งออก SSOP" in client.get("/").text


def test_search_classifies_and_masks_id(client):
    v = by_vn(client)
    assert v[OK]["default_selected"] and v[OK]["cid"] == "x" * 9 + "0011"
    assert v[ZED]["selectable"] and not v[ZED]["default_selected"]
    assert not v[BAD]["selectable"] and not v[SLEEP]["selectable"]
    assert v[SLEEP]["special"] == "Sleep Test"
    assert v[SENT]["already_sent"]


def test_export_and_download(client):
    response = export(client, [OK])
    assert response.status_code == 200 and response.json()["bill_count"] == 1
    download = client.get(f"/api/exports/{response.json()['id']}/download")
    assert download.status_code == 200
    assert len(zipfile.ZipFile(io.BytesIO(download.content)).namelist()) == 3
    assert client.get("/api/exports").json()[0]["bill_count"] == 1


@pytest.mark.parametrize("vn", [BAD, SLEEP])
def test_server_blocks_unsendable_even_if_page_sends_it(client, vn):
    response = export(client, [OK, vn])
    assert response.status_code == 409 and response.json()["blocked"][0]["vn"] == vn
    assert client.get("/api/exports").json() == []


def test_resending_needs_edit_flag_and_confirmation(client):
    assert export(client, [SENT]).status_code == 409
    second = export(client, [SENT], tflag="E")
    assert second.status_code == 409 and second.json()["needs_confirm"]
    assert export(client, [SENT], tflag="E", confirm_resend=True).status_code == 200


def test_local_export_marks_visit_as_sent_until_cancelled(client):
    assert export(client, [OK]).status_code == 200
    assert by_vn(client)[OK]["status"] == "ส่งออกแล้ว"


def test_cancel_export_clears_local_sent_state(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.record_export("0001", "A", tmp_path / "a.zip", [OK], 1)
    store.record_export("0002", "D", tmp_path / "b.zip", [OK], 1)
    assert store.sent_vns([OK]) == set()


def test_export_with_nothing_selected(client):
    assert export(client, []).status_code == 400


def test_unknown_vn_is_rejected(client):
    assert export(client, ["999"]).status_code == 400


def test_exclusion_changes_detail_and_export(client):
    assert client.post("/api/exclusion", json={"vn": OK, "icode": "3000001", "excluded": True}).status_code == 200
    detail = client.post("/api/detail", json={"vn": OK}).json()
    assert detail["items"][0]["excluded"]
    assert export(client, [OK]).json()["claim_total"] == "0"


def test_z_code_upload_changes_the_pdx_check(client):
    book = openpyxl.Workbook()
    book.active.append(["1", "Z999", "Allowed now"])
    book.active.append(["2", "A00", "Not a Z code"])
    data = io.BytesIO()
    book.save(data)
    assert client.post("/api/zcodes", content=data.getvalue()).json()["count"] == 1
    assert by_vn(client)[ZED]["default_selected"]


def test_bad_z_file_is_rejected(client):
    assert client.post("/api/zcodes", content=b"not excel").status_code == 400


def test_other_hosts_are_refused(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


def test_mask_id_short_values():
    assert mask_id("123") == "123" and mask_id(None) == ""


def test_item_excluded_in_hosxp_can_be_sent_and_excluded_again(tmp_path):
    db = FakeHosxp()
    db.items = [item(vn=OK, hosxp_noexport=1)] + [i for i in db.items if i["vn"] != OK]
    client = TestClient(create_app(replace(settings(), export_dir=tmp_path / "exports"), db, Store(tmp_path / "s.sqlite3")))
    detail = lambda: client.post("/api/detail", json={"vn": OK}).json()["items"][0]
    assert detail()["excluded"] and detail()["hosxp_excluded"]

    client.post("/api/exclusion", json={"vn": OK, "icode": "3000001", "excluded": False})
    assert not detail()["excluded"] and detail()["hosxp_excluded"]
    assert export(client, [OK]).json()["claim_total"] == "100"

    client.post("/api/exclusion", json={"vn": OK, "icode": "3000001", "excluded": True})
    assert detail()["excluded"]
    assert db.items[0]["hosxp_noexport"] == 1


def test_rejected_visit_must_be_resent_as_a_correction(tmp_path):
    db = FakeHosxp()
    db.visits = [visit(vn=OK, chi_status="C", chi_codes="R01,S01,T02,W07")]
    client = TestClient(create_app(replace(settings(), export_dir=tmp_path / "exports"), db, Store(tmp_path / "s.sqlite3")))

    refused = export(client, [OK])
    assert refused.status_code == 409 and refused.json()["resend"] == [OK]
    unconfirmed = export(client, [OK], tflag="E")
    assert unconfirmed.status_code == 409 and unconfirmed.json()["needs_confirm"]
    assert export(client, [OK], tflag="E", confirm_resend=True).status_code == 200

    found = client.post("/api/search", json={"start": "2026-10-01", "end": "2026-10-31", "pttypes": []}).json()["visits"][0]
    assert found["needs_correction"] and "E: แก้ไข" in found["result_codes"][0]["hint"]


@pytest.mark.parametrize("host,shown", [("192.168.1.4", "xxx.xxx.1.4"), ("127.0.0.1", "xxx.xxx.0.1"), ("db.example.org", "xxx")])
def test_database_host_is_masked(host, shown):
    assert mask_host(host) == shown


def test_status_shows_only_the_masked_host(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e", db_host="192.168.1.4")
    status = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"))).get("/api/status")
    assert status.json()["db_host"] == "xxx.xxx.1.4" and "192.168" not in status.text


def test_status_reports_failed_connection_with_masked_host(tmp_path):
    db = FakeHosxp()
    def refuse():
        raise HosxpError("เชื่อมต่อฐาน HOSxP ไม่ได้")
    db.preflight = refuse
    config = replace(settings(), export_dir=tmp_path / "e", db_host="192.168.1.4")
    body = TestClient(create_app(config, db, Store(tmp_path / "s.sqlite3"))).get("/api/status").json()
    assert body["database"] is None and body["db_host"] == "xxx.xxx.1.4" and body["db_error"]


def test_station_entered_on_screen_is_used(client):
    assert 'id="station"' in client.get("/").text
    response = export(client, [OK], station="9")
    assert response.status_code == 200
    name = response.json()["file"] if "file" in response.json() else client.get("/api/exports").json()[0]["file"]
    assert "_SSOPBIL_0001_09_" in name
    content = zipfile.ZipFile(io.BytesIO(client.get(f"/api/exports/{response.json()['id']}/download").content))
    bill = content.read(next(n for n in content.namelist() if n.startswith("BillTran"))).decode("cp874")
    assert "<BILLTRAN>\r\n09|" in bill


@pytest.mark.parametrize("station", ["00", "abc", "123"])
def test_bad_station_is_rejected(client, station):
    response = export(client, [OK], station=station)
    assert response.status_code == 400 and "Station" in response.json()["detail"]


def test_allowed_hosts_follow_settings(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e", host="0.0.0.0", extra_hosts=("192.168.80.88",),
                     moph_redirect_uri="https://exportclaim.local:5180/auth/healthid/callback")
    assert config.allowed_hosts == ["127.0.0.1", "localhost", "192.168.80.88", "exportclaim.local"]
    client = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")), base_url="http://192.168.80.88")
    assert client.get("/").status_code == 200
    other = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s2.sqlite3")), base_url="http://evil.example")
    assert other.get("/").status_code == 400


def test_static_links_carry_a_version(client):
    page = client.get("/").text
    assert re.search(r'/static/app\.css\?v=[0-9a-f]{10}"', page) and re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', page)
