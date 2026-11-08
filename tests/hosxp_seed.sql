-- Synthetic HOSxP subset for local testing. All people below are made up.
-- Column names come from the queries in ssop.txt; a real HOSxP database has many more.
SET NAMES tis620;
DROP DATABASE IF EXISTS hos_test; DROP DATABASE IF EXISTS rcm_test;
CREATE DATABASE hos_test DEFAULT CHARACTER SET tis620; CREATE DATABASE rcm_test DEFAULT CHARACTER SET tis620;
USE hos_test;
CREATE TABLE ovst (vn VARCHAR(12) PRIMARY KEY, hn VARCHAR(9), an VARCHAR(9), pttype VARCHAR(2), spclty VARCHAR(2), vstdate DATE, vsttime TIME, cur_dep_time TIME, ovstist VARCHAR(2), ovstost VARCHAR(2), doctor VARCHAR(7));
CREATE TABLE vn_stat (vn VARCHAR(12) PRIMARY KEY, pdx VARCHAR(7), hospmain VARCHAR(5), pttype_begin DATE, pttype_expire DATE, income DOUBLE(15,2), rcpt_money DOUBLE(15,2), debt_id_list VARCHAR(50), rcpno_list VARCHAR(50));
CREATE TABLE patient (hn VARCHAR(9) PRIMARY KEY, cid VARCHAR(13), pname VARCHAR(25), fname VARCHAR(50), lname VARCHAR(50), nationality INT, birthday DATE);
CREATE TABLE ptcardno (hn VARCHAR(9), cardtype VARCHAR(2), cardno VARCHAR(20));
CREATE TABLE pttype (pttype VARCHAR(2) PRIMARY KEY, name VARCHAR(100), isuse CHAR(1), hipdata_code VARCHAR(5), pcode VARCHAR(2), pttype_eclaim_id VARCHAR(5), pttype_upp_type_id INT);
CREATE TABLE pttype_upp_type (pttype_upp_type_id INT PRIMARY KEY, pttype_upp_type_code VARCHAR(5));
CREATE TABLE spclty (spclty VARCHAR(2) PRIMARY KEY, name VARCHAR(60), nhso_code VARCHAR(2));
CREATE TABLE opitemrece (vn VARCHAR(12), hn VARCHAR(9), icode VARCHAR(7), income VARCHAR(2), qty INT, unitprice DOUBLE(15,3), sum_price DOUBLE(15,3), paidst VARCHAR(2), over_debt DOUBLE(15,2), rxdate DATE, rxtime TIME, drugusage VARCHAR(7), sp_use VARCHAR(7));
CREATE TABLE s_drugitems (icode VARCHAR(7) PRIMARY KEY, name VARCHAR(100), strength VARCHAR(50), nhso_adp_code VARCHAR(15), nhso_adp_type_id INT, sks_drug_code VARCHAR(25), tpu_code_list VARCHAR(25), sks_product_category_id VARCHAR(2), drugcategory VARCHAR(50), sks_dfs_code VARCHAR(10), units VARCHAR(20), icd9cm VARCHAR(10), drugaccount VARCHAR(5));
CREATE TABLE nondrugitems (icode VARCHAR(7) PRIMARY KEY, unit VARCHAR(20));
CREATE TABLE income (income VARCHAR(2) PRIMARY KEY, group2 INT, drg_chrgitem_id INT);
CREATE TABLE income_report2 (group_id INT PRIMARY KEY, cscd_group VARCHAR(2));
CREATE TABLE ovstist (ovstist VARCHAR(2) PRIMARY KEY, name VARCHAR(60), export_code VARCHAR(5));
CREATE TABLE ovstost (ovstost VARCHAR(2) PRIMARY KEY, name VARCHAR(60), export_code VARCHAR(5));
CREATE TABLE drugusage (drugusage VARCHAR(7) PRIMARY KEY, name1 VARCHAR(60), name2 VARCHAR(60), name3 VARCHAR(60));
CREATE TABLE sp_use (sp_use VARCHAR(7) PRIMARY KEY, name1 VARCHAR(60), name2 VARCHAR(60), name3 VARCHAR(60));
CREATE TABLE ovstdiag (ovst_diag_id INT PRIMARY KEY AUTO_INCREMENT, vn VARCHAR(12), icd10 VARCHAR(7), diagtype VARCHAR(1));
CREATE TABLE icd101 (code VARCHAR(7) PRIMARY KEY, name VARCHAR(200), codeset VARCHAR(5));
CREATE TABLE icd10_nhso (code VARCHAR(7) PRIMARY KEY);
CREATE TABLE icd10_ssop_z (code VARCHAR(9) PRIMARY KEY, description VARCHAR(250));
CREATE TABLE doctor (code VARCHAR(7) PRIMARY KEY, licenseno VARCHAR(20));
CREATE TABLE ptdepart (vn VARCHAR(12), outdate DATE, outtime TIME);
CREATE TABLE oapp (vn VARCHAR(12), nextdate DATE, oapp_status_id INT);
CREATE TABLE ovst_presc_ned (vn VARCHAR(12), icode VARCHAR(7), presc_reason VARCHAR(10));
CREATE TABLE opitemrece_noexport (seq VARCHAR(50), icode VARCHAR(15), type VARCHAR(1) NOT NULL DEFAULT '', PRIMARY KEY (seq,icode,type));
CREATE TABLE rcm_test.chi (type VARCHAR(20), vn VARCHAR(12) PRIMARY KEY, status VARCHAR(1));
CREATE TABLE rcm_test.repeclaim (vn VARCHAR(12), rep VARCHAR(20), errorcode VARCHAR(20));
CREATE TABLE rcm_test.labitem (icode VARCHAR(7), tmlt_code VARCHAR(25), chi VARCHAR(1));
CREATE TABLE rcm_test.claimcat (icode VARCHAR(7), maininscl VARCHAR(5), start_date DATE, finish_date DATE, remark VARCHAR(50));

INSERT INTO pttype VALUES ('S1','ประกันสังคม รพ.ทดสอบ','Y','SS','A7','10',NULL);
INSERT INTO spclty VALUES ('01','อายุรกรรม','01');
INSERT INTO ovstist VALUES ('01','มาเอง','1');
INSERT INTO ovstost VALUES ('01','กลับบ้าน','1'),('02','Admit','8');
INSERT INTO doctor VALUES ('D1','ว12345');
INSERT INTO income VALUES ('03',3,3),('12',12,12),('07',7,7);
INSERT INTO income_report2 VALUES (3,'3'),(12,'C'),(7,'7');
INSERT INTO drugusage VALUES ('0101','รับประทาน','ครั้งละ 1 เม็ด','หลังอาหาร');
INSERT INTO s_drugitems VALUES
 ('1000001','พาราเซตามอล','500 mg',NULL,NULL,'100000000001',NULL,NULL,NULL,'TAB','เม็ด',NULL,'-'),
 ('3000001','ค่าบริการผู้ป่วยนอก',NULL,'55020',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
 ('3000002','Sleep Test',NULL,'51120',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
 ('3000003','ยาเคมีบำบัด',NULL,'C01X',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
 ('3000004','ทำแผล',NULL,'55021',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'86599',NULL);
INSERT INTO nondrugitems VALUES ('3000001','ครั้ง'),('3000004','ครั้ง');
INSERT INTO icd101 VALUES ('J00','Acute nasopharyngitis','IT'),('Z000','General examination','IT'),('Z999','Not claimable Z','IT'),('V01','Pedestrian injured','IT'),('C50','Breast cancer','IT'),('I10','Hypertension','IT');
INSERT INTO icd10_nhso VALUES ('J00'),('Z000'),('Z999'),('V01'),('C50'),('I10');
INSERT INTO icd10_ssop_z VALUES ('Z000','General examination');

-- Make one visit: helper data is listed per person. The dates are all in 2026-10.
INSERT INTO patient VALUES
 ('HN0001','1100000000011','นาย','ทดสอบ','ผ่านครบ',99,'1990-01-01'),
 ('HN0002','1100000000022','นาง','ทดสอบ','รหัสแซด',99,'1985-01-01'),
 ('HN0003','1100000000033','นาย','ทดสอบ','โรคภายนอก',99,'1980-01-01'),
 ('HN0004','1100000000044','นาง','ทดสอบ','มะเร็ง',99,'1975-01-01'),
 ('HN0005','1100000000055','นาย','ทดสอบ','นอนกรน',99,'1970-01-01'),
 ('HN0006','1100000000066','นาง','ทดสอบ','ส่งแล้ว',99,'1995-01-01'),
 ('HN0007','1100000000077','นาย','ทดสอบ','ยอดไม่ตรง',99,'1992-01-01'),
 ('HN0008','1100000000088','นาง','ทดสอบ','คำเตือน',99,'1991-01-01');
INSERT INTO ovst VALUES
 ('691001000001','HN0001',NULL,'S1','01','2026-10-01','09:00:00','10:30:00','01','01','D1'),
 ('691001000002','HN0002',NULL,'S1','01','2026-10-01','09:10:00','09:40:00','01','01','D1'),
 ('691001000003','HN0003',NULL,'S1','01','2026-10-01','09:20:00','09:50:00','01','01','D1'),
 ('691001000004','HN0004',NULL,'S1','01','2026-10-02','09:00:00','09:30:00','01','01','D1'),
 ('691001000005','HN0005',NULL,'S1','01','2026-10-02','09:10:00','09:40:00','01','01','D1'),
 ('691001000006','HN0006',NULL,'S1','01','2026-10-02','09:20:00','09:50:00','01','01','D1'),
 ('691001000007','HN0007',NULL,'S1','01','2026-10-03','09:00:00','09:30:00','01','01','D1'),
 ('691001000008','HN0008',NULL,'S1','01','2026-10-03','09:10:00','09:30:00','01','01','D9');
INSERT INTO doctor VALUES ('D9','สมชาย');
INSERT INTO vn_stat VALUES
 ('691001000001','J00','11111','2026-01-01','2026-12-31',150,0,'DEBT1','RC1'),
 ('691001000002','Z999','11111','2026-01-01','2026-12-31',100,0,'DEBT2','RC2'),
 ('691001000003','V01','11111','2026-01-01','2026-12-31',100,0,'DEBT3','RC3'),
 ('691001000004','C50','11111','2026-01-01','2026-12-31',1000,0,'DEBT4','RC4'),
 ('691001000005','I10','11111','2026-01-01','2026-12-31',2000,0,'DEBT5','RC5'),
 ('691001000006','J00','11111','2026-01-01','2026-12-31',100,0,'DEBT6','RC6'),
 ('691001000007','J00','11111','2026-01-01','2026-12-31',999,0,'DEBT7','RC7'),
 ('691001000008','Z000','11111','2026-01-01','2026-12-31',100,0,'DEBT8','RC8');
INSERT INTO opitemrece VALUES
 ('691001000001','HN0001','1000001','03',10,5,50,'02',0,'2026-10-01','09:30:00','0101',NULL),
 ('691001000001','HN0001','3000001','12',1,50,50,'02',0,'2026-10-01','09:05:00',NULL,NULL),
 ('691001000001','HN0001','3000004','12',1,50,50,'02',0,'2026-10-01','09:15:00',NULL,NULL),
 ('691001000002','HN0002','3000001','12',1,100,100,'02',0,'2026-10-01','09:15:00',NULL,NULL),
 ('691001000003','HN0003','3000001','12',1,100,100,'02',0,'2026-10-01','09:25:00',NULL,NULL),
 ('691001000004','HN0004','3000003','12',1,1000,1000,'02',0,'2026-10-02','09:10:00',NULL,NULL),
 ('691001000005','HN0005','3000002','12',1,2000,2000,'02',0,'2026-10-02','09:15:00',NULL,NULL),
 ('691001000006','HN0006','3000001','12',1,100,100,'02',0,'2026-10-02','09:25:00',NULL,NULL),
 ('691001000007','HN0007','3000001','12',1,100,100,'02',0,'2026-10-03','09:05:00',NULL,NULL),
 ('691001000008','HN0008','3000001','12',1,100,100,'02',0,'2026-10-03','09:15:00',NULL,NULL);
INSERT INTO ovstdiag (vn,icd10,diagtype) VALUES
 ('691001000001','J00','1'),('691001000001','I10','2'),('691001000002','Z999','1'),('691001000003','V01','1'),
 ('691001000004','C50','1'),('691001000005','I10','1'),('691001000006','J00','1'),('691001000007','J00','1'),('691001000008','Z000','1');
INSERT INTO rcm_test.chi VALUES ('SSOP','691001000006','A');
CREATE USER IF NOT EXISTS 'ssop_ro'@'localhost' IDENTIFIED BY 'local-test-only';
GRANT SELECT ON hos_test.* TO 'ssop_ro'@'localhost';
GRANT SELECT ON rcm_test.* TO 'ssop_ro'@'localhost';
