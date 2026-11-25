-- ผูกรหัส H (บริการฟื้นฟูสมรรถภาพด้านการแพทย์อื่นๆ 9 รายการ, ส่งผ่าน Disability Portal) ให้รายการค่าบริการใน HOSxP
-- ร่างจากข้อมูลเครื่องทดสอบ (22/11/2569) · icode ของเครื่องจริงอาจต่าง ให้รันส่วนที่ 1 ตรวจก่อนทุกครั้ง
--
-- วิธีใช้ (ให้ผู้ดูแล HOSxP รันเอง ด้วยบัญชีที่แก้ข้อมูลได้ แอป KHH Claim Sure ไม่เขียน HOSxP):
--   1. รันส่วนที่ 1 ดูรายการปัจจุบัน
--   2. รันส่วนที่ 2 สำรองค่าเดิม (ต้องทำก่อนแก้)
--   3. รันส่วนที่ 3 (แก้ที่แน่นอน)
--   4. ส่วนที่ 4 เป็นทางเลือก อ่านคำเตือนก่อน แล้วค่อยเอา -- ออกเฉพาะบรรทัดที่ต้องการ
--   5. รันส่วนที่ 5 ตรวจผล · ถ้าต้องคืนค่า ใช้ส่วนที่ 6
--
-- nhso_adp_code อยู่ทั้งใน nondrugitems และ s_drugitems (ตารางจริงทั้งคู่) จึงแก้ทั้งสองที่
-- ข้อควรรู้: รหัส ADP ของรายการผูกกับรายการ ไม่ได้แยกตามสิทธิ ถ้าเปลี่ยน 51900 / 57999 เป็นรหัส H
-- visit สิทธิอื่น (OFC/SSS/LGO) และแฟ้ม ADP ที่ส่ง e-Claim/FDH จะได้รหัส H ไปด้วย

-- ===================================================================== 1. ดูรายการที่เกี่ยวข้อง
SELECT n.icode, n.name, n.nhso_adp_code AS adp_now, s.nhso_adp_code AS s_adp_now, n.istatus, n.price
FROM nondrugitems n LEFT JOIN s_drugitems s ON s.icode = n.icode
WHERE n.icode IN ('3004133','3004134','3004098','3003150','3003156','3002984','3002983','3002985','3002987','3001135')
ORDER BY n.icode;

-- ===================================================================== 2. สำรองค่าเดิม (รันครั้งเดียว)
CREATE TABLE IF NOT EXISTS zz_backup_rehab_hcode_20261122 AS
SELECT n.icode, n.nhso_adp_code AS nondrug_adp, s.nhso_adp_code AS s_adp, NOW() AS saved_at
FROM nondrugitems n LEFT JOIN s_drugitems s ON s.icode = n.icode
WHERE n.icode IN ('3004133','3004134','3004098','3003150','3003156','3002984','3002983','3002985','3002987','3001135');

-- ===================================================================== 3. แก้ที่แน่นอน
-- Early Intervention (รายบุคคล) ผูกผิดเป็น H9383.1 (= กิจกรรมบำบัด) ต้องเป็น H9438.1
UPDATE nondrugitems SET nhso_adp_code = 'H9438.1' WHERE icode = '3004133' AND nhso_adp_code = 'H9383.1';
UPDATE s_drugitems  SET nhso_adp_code = 'H9438.1' WHERE icode = '3004133' AND nhso_adp_code = 'H9383.1';
-- Early Intervention (รายกลุ่ม) 3004134 = H9438.2 ถูกแล้ว ไม่ต้องแก้

-- ===================================================================== 4. ทางเลือก (ยังปิดอยู่)
-- คำเตือน: รายการเหล่านี้ตอนนี้ใช้ 51900 / 57999 และสิทธิอื่นก็ใช้ (ตั้งแต่ ต.ค. 68 มี OFC 55, SSS 35, LGO 7 ครั้ง)
-- ทางที่ปลอดภัยกว่า: สร้างรายการใหม่หนึ่งรายการต่อรหัส H ใน HOSxP (หน้าจอตั้งค่ารายการค่าบริการ) แล้วให้นักบำบัด
-- คีย์รายการใหม่วันละครั้งเฉพาะผู้มีสิทธิ วิธีนี้ไม่กระทบรายการเดิม
--
-- 4.1 กายภาพบำบัด H9339 · Magnetic Stimulation (PMS) (9339) ใช้ 152 ครั้ง
--     หมายเหตุ: H9339 คิดเป็นครั้งต่อวัน แต่คลินิกกายภาพคีย์เป็นหัตถการ 56xxx หลายรายการต่อ visit (UC 1,244 visit)
--     ถ้าจะเบิก H9339 ทุก visit กายภาพ ควรสร้างรายการใหม่ "ฟื้นฟูกายภาพบำบัด H9339" แทนการแก้ PMS
-- UPDATE nondrugitems SET nhso_adp_code = 'H9339' WHERE icode = '3004098';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9339' WHERE icode = '3004098';
--
-- 4.2 กิจกรรมบำบัด รายกลุ่ม H9383.2 · การทำกลุ่มกิจกรรมบำบัด ใช้ 74 ครั้ง
-- UPDATE nondrugitems SET nhso_adp_code = 'H9383.2' WHERE icode = '3002985';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9383.2' WHERE icode = '3002985';
--
-- 4.3 การแก้ไขการพูด รายบุคคล H9375.1 · Speech therapy (ยังไม่เคยใช้)
-- UPDATE nondrugitems SET nhso_adp_code = 'H9375.1' WHERE icode = '3001135';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9375.1' WHERE icode = '3001135';
--
-- 4.4 จิตบำบัด รายบุคคล H9449.1 · Individual Psychotherapy ใช้ 3,035 ครั้ง (ส่วนใหญ่คลินิกจิตเวช ไม่ใช่งานฟื้นฟู)
--     แนะนำสร้างรายการใหม่แทน เพราะรายการนี้ใช้มากและเกือบทั้งหมดไม่ใช่กลุ่มเป้าหมายฟื้นฟู
-- UPDATE nondrugitems SET nhso_adp_code = 'H9449.1' WHERE icode = '3003156';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9449.1' WHERE icode = '3003156';
--
-- 4.5 จิตบำบัด รายกลุ่ม H9449.2 · การทำกลุ่มจิตบำบัด ต่อคน ใช้ 431 ครั้ง
-- UPDATE nondrugitems SET nhso_adp_code = 'H9449.2' WHERE icode = '3002984';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9449.2' WHERE icode = '3002984';
--
-- 4.6 พฤติกรรมบำบัด รายบุคคล H9433.1 · พฤติกรรมบำบัด ใช้ 2 ครั้ง
-- UPDATE nondrugitems SET nhso_adp_code = 'H9433.1' WHERE icode = '3002987';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9433.1' WHERE icode = '3002987';
--
-- 4.7 พฤติกรรมบำบัด รายกลุ่ม H9433.2 · กลุ่มจิตบำบัด/พฤติกรรมบำบัด/ฟื้นฟูสภาพ ใช้ 1 ครั้ง
-- UPDATE nondrugitems SET nhso_adp_code = 'H9433.2' WHERE icode = '3002983';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9433.2' WHERE icode = '3002983';
--
-- 4.8 Early Intervention รายบุคคล H9438.1 · กระตุ้นพัฒนาการเด็ก (ยังไม่มีรหัส) ใช้ 390 ครั้ง
--     ใช้ได้เฉพาะเด็กกลุ่มเป้าหมายฟื้นฟู ถ้าเป็นการกระตุ้นพัฒนาการทั่วไป ให้ใช้รายการ 3004133 แทน
-- UPDATE nondrugitems SET nhso_adp_code = 'H9438.1' WHERE icode = '3003150' AND IFNULL(nhso_adp_code,'') = '';
-- UPDATE s_drugitems  SET nhso_adp_code = 'H9438.1' WHERE icode = '3003150' AND IFNULL(nhso_adp_code,'') = '';
--
-- ยังไม่มีรายการใน HOSxP (ต้องสร้างใหม่ถ้าให้บริการ):
--   H9375.2 แก้ไขการพูด รายกลุ่ม · H9549 ฟื้นฟูการได้ยิน · H9378.2 / H9378.3 ฟื้นฟูการเห็น · H0489 Phenol block

-- ===================================================================== 5. ตรวจผล
SELECT n.icode, n.name, n.nhso_adp_code AS nondrug_adp, s.nhso_adp_code AS s_adp,
       (n.nhso_adp_code <=> s.nhso_adp_code) AS same_in_both
FROM nondrugitems n LEFT JOIN s_drugitems s ON s.icode = n.icode
WHERE n.nhso_adp_code IN ('H9339','H9383.1','H9383.2','H9375.1','H9375.2','H9449.1','H9449.2','H9433.1','H9433.2',
                          'H9549','H9378.2','H9378.3','H9438.1','H9438.2','H0489')
ORDER BY n.nhso_adp_code;

-- ===================================================================== 6. คืนค่าเดิม (ถ้าต้องการ)
-- UPDATE nondrugitems n JOIN zz_backup_rehab_hcode_20261122 b ON b.icode = n.icode SET n.nhso_adp_code = b.nondrug_adp;
-- UPDATE s_drugitems  s JOIN zz_backup_rehab_hcode_20261122 b ON b.icode = s.icode SET s.nhso_adp_code = b.s_adp;
