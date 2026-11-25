-- visit ผู้ป่วยนอกสิทธิประกันสังคม เดือน พ.ย. 2569 ที่ยอด vn_stat.income ไม่ตรงกับผลรวมรายการ (opitemrece.sum_price, qty=0 คิด 0)
-- อ่านอย่างเดียว · เปลี่ยนช่วงวันที่ 2 จุด (ในวงเล็บย่อยและ WHERE หลัก)
-- n_qty0_with_price = รายการที่ qty 0 แต่ยังมี sum_price ค้าง (สาเหตุยอดไม่ตรงที่พบบ่อย)
-- n_income_empty = รายการที่หมวดค่าใช้จ่ายว่าง

SET STATEMENT max_statement_time = 60 FOR
SELECT o.vn, o.hn, o.vstdate, o.pttype, ptt.name AS pttype_name,
  CONCAT(IFNULL(pt.pname,''), IFNULL(pt.fname,''), ' ', IFNULL(pt.lname,'')) AS ptname,
  v.income                         AS visit_income,
  IFNULL(it.item_total, 0)         AS item_total,
  v.income - IFNULL(it.item_total, 0) AS diff,
  IFNULL(it.n_items, 0)            AS n_items,
  IFNULL(it.n_qty0, 0)             AS n_qty0_with_price,
  IFNULL(it.n_income_empty, 0)     AS n_income_empty
FROM ovst o
JOIN vn_stat v       ON v.vn = o.vn
JOIN pttype ptt      ON ptt.pttype = o.pttype
LEFT JOIN patient pt ON pt.hn = o.hn
LEFT JOIN (
  SELECT oo.vn,
    SUM(IF(IFNULL(oo.qty,0)=0, 0, IFNULL(oo.sum_price,0)))              AS item_total,
    COUNT(*)                                                            AS n_items,
    SUM(IFNULL(oo.qty,0)=0 AND IFNULL(oo.sum_price,0)<>0)               AS n_qty0,
    SUM(IFNULL(oo.income,'')='')                                        AS n_income_empty
  FROM opitemrece oo
  WHERE oo.vstdate BETWEEN '2026-11-01' AND '2026-11-30'
    AND oo.vn IS NOT NULL AND oo.vn <> ''
  GROUP BY oo.vn
) it ON it.vn = o.vn
WHERE o.vstdate BETWEEN '2026-11-01' AND '2026-11-30'
  AND IFNULL(o.an,'') = ''
  AND (ptt.hipdata_code REGEXP 'SS' OR ptt.pcode = 'A7')
  AND ABS(v.income - IFNULL(it.item_total, 0)) > 0.01
ORDER BY ABS(v.income - IFNULL(it.item_total, 0)) DESC, o.vstdate, o.vn;
