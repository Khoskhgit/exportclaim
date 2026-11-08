from datetime import date, time, timedelta
from pathlib import Path

from app.config import Settings


def settings(**overrides):
    values = dict(
        db_user="u", db_name="hos", hcode="11111", hname="รพ.ทดสอบ", hosp_main=("11111",),
        default_doctor="ว99999", default_usage_code="0201", default_usage_name="วิธีใช้ตามแพทย์สั่ง",
        export_dir=Path("/nonexistent"), login_required=False,
    )
    values.update(overrides)
    return Settings(**values)


def visit(**overrides):
    values = dict(
        vn="691001000001", hn="HN1", an=None, cid="1100000000011", ptname="นายทดสอบ ผ่าน",
        vstdate=date(2026, 10, 1), vsttime=timedelta(hours=9), cur_dep_time=timedelta(hours=10),
        depart_out=None, clinic="01", pdx="J00", nhso_code="J00", typeout_name="กลับบ้าน",
        typein_code="1", typeout_code="1", hospmain="11111", hipdata_code="SS", pttype_eclaim_id="10",
        upp_code=None, datein=date(2026, 1, 1), dateexp=date(2026, 12, 31), doctor="ว12345",
        income=100, rcpt_money=0, debt_id="DEBT#1", rcpno="R:1", appoint_date=None,
        chi_status=None, rep=None, hi_registered=0,
    )
    values.update(overrides)
    return values


def item(**overrides):
    values = dict(
        vn="691001000001", hn="HN1", icode="3000001", qty=1, unitprice=100, sum_price=100,
        paidst="02", over_debt=0, rxdate=date(2026, 10, 1), rxtime=timedelta(hours=9, minutes=5),
        usage_code=None, sp_use_code=None, name="ค่าบริการ", strength=None, nhso_adp_code="55020",
        nhso_adp_type_id=None, sks_drug_code=None, tpu_code_list=None, sks_product_category_id=None,
        drugcategory=None, sks_dfs_code=None, units=None, nd_unit="ครั้ง", icd9cm=None, drugaccount=None,
        cscd_group="C", drg_chrgitem_id=12, usage_text="", sp_use_text="", ned=None, tmlt_code=None,
        lab_chi=None, ssocac_item=0, hosxp_noexport=0,
    )
    values.update(overrides)
    return values


def drug(**overrides):
    values = dict(
        icode="1000001", qty=10, unitprice=5, sum_price=50, usage_code="0101", name="พาราเซตามอล",
        strength="500 mg", nhso_adp_code=None, sks_drug_code="100000000001", sks_dfs_code="TAB",
        units="เม็ด", cscd_group="3", drg_chrgitem_id=3, usage_text="รับประทาน", drugaccount="-",
    )
    values.update(overrides)
    return item(**values)


__all__ = ["settings", "visit", "item", "drug", "time"]
